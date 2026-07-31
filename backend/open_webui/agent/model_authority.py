from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse, Response, StreamingResponse

from open_webui.agent.canonical import canonical_sha256
from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL
from open_webui.models.agent_runs import AgentRunOperationConflict
from open_webui.models.users import Users
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.models import check_model_access, get_all_models

AGENT_MODEL_STREAM_HEARTBEAT_SECONDS = 10.0


async def _stream_with_control_heartbeat(
    source: AsyncIterator[bytes],
    *,
    heartbeat_seconds: float,
) -> AsyncIterator[bytes]:
    """Keep an SSE transport live without creating transcript-visible chunks."""
    iterator = source.__aiter__()
    pending: asyncio.Task | None = None
    exhausted = False

    yield b': openwebui-stream-start\n\n'
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(iterator))

            done, _ = await asyncio.wait(
                {pending},
                timeout=max(0.001, heartbeat_seconds),
            )
            if not done:
                yield b': openwebui-keep-alive\n\n'
                continue

            try:
                chunk = pending.result()
            except StopAsyncIteration:
                exhausted = True
                return
            finally:
                pending = None
            yield chunk
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        if not exhausted:
            aclose = getattr(iterator, 'aclose', None)
            if aclose is not None:
                await aclose()


class ModelAuthorityError(ValueError):
    code = 'model_authority_error'


class ModelGuardRejected(ModelAuthorityError):
    code = 'agent_internal_model_call_required'


class ModelNotAllowed(ModelAuthorityError):
    code = 'model_not_allowed'


class ModelRunRejected(ModelAuthorityError):
    code = 'model_run_rejected'

    def __init__(self, message: str, *, current_state: str | None = None) -> None:
        super().__init__(message)
        self.current_state = current_state


class ModelOperationInProgress(ModelAuthorityError):
    code = 'operation_in_progress'


class ModelStreamNotReplayable(ModelAuthorityError):
    code = 'model_stream_not_replayable'

    def __init__(self, operation_status: str) -> None:
        super().__init__(
            'Streaming model operation already '
            f'{operation_status}; streamed responses cannot be replayed'
        )
        self.operation_status = operation_status


class ModelCallRequest(BaseModel):
    model_config = ConfigDict(extra='allow')

    run_id: str
    participant_id: str
    model_call_id: str
    model: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


CompletionHandler = Callable[[Any, dict[str, Any], Any], Awaitable[Any]]
UserLoader = Callable[[str], Awaitable[Any]]
ModelAccessChecker = Callable[[Any, dict[str, Any]], Awaitable[None]]
ModelLoader = Callable[[Any, Any], Awaitable[Any]]


@dataclass(frozen=True)
class PreparedStreamModelCall:
    user: Any
    model: dict[str, Any]
    operation_id: str


@dataclass
class ModelStreamOutcome:
    error: dict[str, str] | None = None
    finalized: bool = False


async def _await_cancellation_safe(awaitable: Awaitable[Any]) -> Any:
    """Let a terminal DB write finish even when its request task is cancelled."""
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(asyncio.CancelledError, Exception):
            await task
        raise


class AgentModelStreamingResponse(StreamingResponse):
    """Own cleanup for claims that never reach the lazy body iterator."""

    def __init__(
        self,
        content,
        *,
        on_close: Callable[[], Awaitable[None]],
        **kwargs,
    ) -> None:
        super().__init__(content, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await _await_cancellation_safe(self._on_close())


class _ModelStreamErrorDetector:
    """Observe raw SSE bytes without changing the provider wire payload."""

    def __init__(self) -> None:
        self._line_buffer = bytearray()
        self._data_lines: list[bytes] = []
        self.terminal_seen = False

    def feed(self, chunk: bytes) -> dict[str, str] | None:
        self._line_buffer.extend(chunk)
        while True:
            newline_index = self._line_buffer.find(b'\n')
            if newline_index < 0:
                return None
            line = bytes(self._line_buffer[:newline_index])
            del self._line_buffer[: newline_index + 1]
            error = self._process_line(line.removesuffix(b'\r'))
            if error is not None:
                return error
            if self.terminal_seen:
                return None

    def finish(self) -> dict[str, str] | None:
        if self._line_buffer:
            line = bytes(self._line_buffer).removesuffix(b'\r')
            self._line_buffer.clear()
            error = self._process_line(line, allow_terminal=False)
            if error is not None:
                return error
        return self._dispatch_event(allow_terminal=False)

    def _process_line(
        self,
        line: bytes,
        *,
        allow_terminal: bool = True,
    ) -> dict[str, str] | None:
        if not line:
            return self._dispatch_event(allow_terminal=allow_terminal)
        if line.startswith(b'data:'):
            data = line[len(b'data:') :]
            if data.startswith(b' '):
                data = data[1:]
            self._data_lines.append(data)
        return None

    def _dispatch_event(
        self,
        *,
        allow_terminal: bool = True,
    ) -> dict[str, str] | None:
        if not self._data_lines:
            return None
        raw_data = b'\n'.join(self._data_lines)
        self._data_lines.clear()
        if raw_data.strip() == b'[DONE]':
            if allow_terminal:
                self.terminal_seen = True
            return None
        try:
            payload = json.loads(raw_data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        event_type = str(payload.get('type') or '').strip()
        if event_type in {'response.failed', 'response.incomplete'}:
            return _structured_stream_event_error(payload, event_type)
        if _is_terminal_stream_event(payload, event_type):
            if allow_terminal:
                self.terminal_seen = True
            return None
        return _top_level_stream_error(payload)


def _is_terminal_stream_event(payload: dict[str, Any], event_type: str) -> bool:
    if event_type in {'done', 'stream_end', 'response.completed'}:
        return True
    choices = payload.get('choices')
    return isinstance(choices, list) and any(
        isinstance(choice, dict) and bool(choice.get('finish_reason'))
        for choice in choices
    )


def _top_level_stream_error(payload: dict[str, Any]) -> dict[str, str] | None:
    error = payload.get('error')
    if error is None:
        return None
    if isinstance(error, dict):
        message = str(error.get('message') or '').strip()
        if not message:
            message = json.dumps(error, ensure_ascii=False, separators=(',', ':'))
    elif isinstance(error, str):
        message = error.strip()
    else:
        message = json.dumps(error, ensure_ascii=False, separators=(',', ':'))
    return {'code': 'model_stream_error', 'message': message or 'unknown model stream error'}


def _structured_stream_event_error(
    payload: dict[str, Any],
    event_type: str,
) -> dict[str, str]:
    error = payload.get('error')
    response = payload.get('response')
    if error is None and isinstance(response, dict):
        error = response.get('error') or response.get('incomplete_details')
    if isinstance(error, dict):
        message = str(error.get('message') or error.get('reason') or '').strip()
        if not message:
            message = json.dumps(error, ensure_ascii=False, separators=(',', ':'))
    elif isinstance(error, str):
        message = error.strip()
    else:
        message = event_type
    return {'code': 'model_stream_error', 'message': message or event_type}


class AgentModelAuthority:
    def __init__(
        self,
        *,
        operation_store,
        completion_handler: CompletionHandler = generate_chat_completion,
        user_loader: UserLoader = Users.get_user_by_id,
        model_access_checker: ModelAccessChecker = check_model_access,
        model_loader: ModelLoader = get_all_models,
    ) -> None:
        self.operation_store = operation_store
        self.completion_handler = completion_handler
        self.user_loader = user_loader
        self.model_access_checker = model_access_checker
        self.model_loader = model_loader

    async def execute_model_call(
        self,
        request,
        call: ModelCallRequest,
    ) -> dict[str, Any]:
        self._ensure_trusted_internal_guard(request, call.run_id)

        if not call.idempotency_key:
            raise ModelAuthorityError('idempotency_key_required')

        run = await self.operation_store.get_run(call.run_id)
        if run is None:
            raise ModelRunRejected(f'Agent run not found: {call.run_id}')
        if run.state != 'running':
            raise ModelRunRejected(
                f'Agent run {call.run_id} cannot execute model calls while {run.state}',
                current_state=run.state,
            )

        user = await self.user_loader(run.user_id)
        if user is None:
            raise ModelRunRejected(f'Agent run user not found: {run.user_id}')

        request_hash = _model_call_request_hash(call)
        try:
            claim = await self.operation_store.claim_operation(
                call.run_id,
                operation_type='model.call',
                idempotency_key=call.idempotency_key,
                request_hash=request_hash,
            )
        except AgentRunOperationConflict:
            raise

        if not claim.created:
            return _cached_operation_response(claim.operation)

        try:
            model = await self._resolve_authorized_model(request, user, call.model)
            response = await self._execute_provider_model_call(request, user, call, model)
        except ModelAuthorityError as exc:
            await self.operation_store.finish_operation_error(
                claim.operation.id,
                _structured_error(exc),
            )
            raise
        except Exception as exc:
            wrapped = ModelAuthorityError(str(exc))
            await self.operation_store.finish_operation_error(
                claim.operation.id,
                _structured_error(wrapped),
            )
            raise wrapped from exc

        await self.operation_store.finish_operation_success(claim.operation.id, response)
        return response

    def _ensure_trusted_internal_guard(self, request, run_id: str) -> None:
        if not getattr(request.state, 'agent_internal_model_call', False):
            raise ModelGuardRejected(
                'Trusted request.state.agent_internal_model_call is required',
            )
        if getattr(request.state, 'agent_run_id', None) != run_id:
            raise ModelGuardRejected('Trusted agent run guard does not match model call run')
        if getattr(request.state, 'agent_service_principal', None) != 'agentscope-runtime':
            raise ModelGuardRejected('Trusted agent service principal is required')

    async def _resolve_authorized_model(self, request, user, model_id: str) -> dict[str, Any]:
        models = getattr(request.app.state, 'MODELS', None)
        model = (models or {}).get(model_id)
        if model is None:
            loaded_models = await self.model_loader(request, user)
            model = _model_from_catalog(loaded_models, model_id)
        if model is None:
            models = getattr(request.app.state, 'MODELS', None)
            model = (models or {}).get(model_id)
        if model is None:
            raise ModelNotAllowed(f'Model is not available for this run: {model_id}')

        if not _model_access_check_bypassed(user):
            try:
                await self.model_access_checker(user, model)
            except Exception as exc:
                message = getattr(exc, 'detail', None) or str(exc)
                raise ModelNotAllowed(str(message)) from exc

        return model
    async def _execute_provider_model_call(
        self,
        request,
        user,
        call: ModelCallRequest,
        model: dict[str, Any],
    ) -> dict[str, Any]:
        form_data = _model_call_form_data(call)
        audit_metadata = {
            **call.metadata,
            'agent_run_id': call.run_id,
            'agent_internal_model_call': True,
            'agent_participant_id': call.participant_id,
            'agent_model_call_id': call.model_call_id,
        }
        form_data['metadata'] = audit_metadata
        request.state.metadata = audit_metadata

        raw_response = await self.completion_handler(request, form_data, user)
        return {
            'status': 'success',
            'model': model.get('id') or call.model,
            'response': await _jsonable_response(raw_response),
            'metadata': {
                'agent_run_id': call.run_id,
                'participant_id': call.participant_id,
                'model_call_id': call.model_call_id,
            },
        }

    async def stream_model_call(
        self,
        request,
        call: ModelCallRequest,
        *,
        prepared: PreparedStreamModelCall | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream a model call's provider response as SSE bytes.

        Yields raw SSE bytes from the provider's StreamingResponse. The
        caller (the /model-call endpoint) wraps this in a StreamingResponse
        with text/event-stream content type.
        """
        if prepared is None:
            prepared = await self.prepare_stream_model_call(request, call)
        outcome = ModelStreamOutcome()

        try:
            async for chunk in _stream_with_control_heartbeat(
                self._stream_provider_model_call(
                    request,
                    call,
                    prepared=prepared,
                    outcome=outcome,
                ),
                heartbeat_seconds=AGENT_MODEL_STREAM_HEARTBEAT_SECONDS,
            ):
                yield chunk
        except GeneratorExit:
            await self._finish_stream_error(
                prepared,
                outcome,
                outcome.error
                or {
                    'code': 'model_stream_closed',
                    'message': 'Streaming model response consumer closed before completion',
                },
            )
            raise
        except asyncio.CancelledError:
            await self._finish_stream_error(
                prepared,
                outcome,
                outcome.error
                or {
                    'code': 'model_stream_cancelled',
                    'message': 'Streaming model response was cancelled before completion',
                },
            )
            raise
        except Exception as exc:
            await self._finish_stream_error(
                prepared,
                outcome,
                {
                    'code': getattr(exc, 'code', 'model_stream_failed'),
                    'message': str(exc),
                },
            )
            raise
        else:
            if not outcome.finalized:
                await self._finish_stream_error(
                    prepared,
                    outcome,
                    outcome.error
                    or {
                        'code': 'model_stream_incomplete',
                        'message': 'Model stream ended without a terminal event',
                    },
                )

    async def _stream_provider_model_call(
        self,
        request,
        call: ModelCallRequest,
        *,
        prepared: PreparedStreamModelCall | None = None,
        outcome: ModelStreamOutcome | None = None,
    ) -> AsyncIterator[bytes]:
        if prepared is None:
            prepared = await self.prepare_stream_model_call(request, call)
        if outcome is None:
            outcome = ModelStreamOutcome()
        user = prepared.user
        model = prepared.model

        form_data = _model_call_form_data(call)
        audit_metadata = {
            **call.metadata,
            'agent_run_id': call.run_id,
            'agent_internal_model_call': True,
            'agent_participant_id': call.participant_id,
            'agent_model_call_id': call.model_call_id,
        }
        form_data['metadata'] = audit_metadata
        request.state.metadata = audit_metadata

        raw_response = await self.completion_handler(request, form_data, user)
        if not isinstance(raw_response, StreamingResponse):
            # Provider returned a non-streaming response despite stream=True
            # (e.g. model doesn't support streaming). Fall back to emitting
            # the full response as a single SSE event so the agentscope
            # runtime still gets a parseable payload.
            payload = await _jsonable_response(raw_response)
            await self._finish_stream_success(prepared, call, outcome)
            yield _format_model_stream_event(
                'done',
                {
                    'status': 'success',
                    'model': model.get('id') or call.model,
                    'response': payload,
                    'metadata': {
                        'agent_run_id': call.run_id,
                        'participant_id': call.participant_id,
                        'model_call_id': call.model_call_id,
                    },
                },
            )
            return

        error_detector = _ModelStreamErrorDetector()
        async for chunk in raw_response.body_iterator:
            if not chunk:
                continue
            if isinstance(chunk, str):
                wire_chunk = chunk.encode('utf-8')
            else:
                wire_chunk = chunk
            stream_error = error_detector.feed(wire_chunk)
            if stream_error is not None:
                await self._finish_stream_error(prepared, outcome, stream_error)
                yield wire_chunk
                return
            if error_detector.terminal_seen:
                await self._finish_stream_success(prepared, call, outcome)
                yield wire_chunk
                return
            yield wire_chunk
        stream_error = error_detector.finish()
        if stream_error is not None:
            await self._finish_stream_error(prepared, outcome, stream_error)
            return
        await self._finish_stream_error(
            prepared,
            outcome,
            {
                'code': 'model_stream_incomplete',
                'message': 'Model stream ended without a terminal event',
            },
        )

    async def _finish_stream_success(
        self,
        prepared: PreparedStreamModelCall,
        call: ModelCallRequest,
        outcome: ModelStreamOutcome,
    ) -> None:
        if outcome.finalized:
            return
        await _await_cancellation_safe(
            self.operation_store.finish_operation_success(
                prepared.operation_id,
                {
                    'status': 'succeeded',
                    'streamed': True,
                    'replayable': False,
                    'model': prepared.model.get('id') or call.model,
                    'model_call_id': call.model_call_id,
                },
            )
        )
        outcome.finalized = True

    async def _finish_stream_error(
        self,
        prepared: PreparedStreamModelCall,
        outcome: ModelStreamOutcome,
        error: dict[str, str],
    ) -> None:
        if outcome.finalized:
            return
        outcome.error = error
        await _await_cancellation_safe(
            self.operation_store.finish_operation_error(prepared.operation_id, error)
        )
        outcome.finalized = True

    async def finalize_abandoned_stream(
        self,
        prepared: PreparedStreamModelCall,
    ) -> None:
        await self.operation_store.finish_operation_error(
            prepared.operation_id,
            {
                'code': 'model_stream_abandoned',
                'message': 'Streaming model response closed before a terminal event',
            },
        )

    async def prepare_stream_model_call(
        self,
        request,
        call: ModelCallRequest,
    ) -> PreparedStreamModelCall:
        """Validate and claim a streaming call before response headers are sent."""
        self._ensure_trusted_internal_guard(request, call.run_id)

        if not call.idempotency_key:
            raise ModelAuthorityError('idempotency_key_required')

        run = await self.operation_store.get_run(call.run_id)
        if run is None:
            raise ModelRunRejected(f'Agent run not found: {call.run_id}')
        if run.state != 'running':
            raise ModelRunRejected(
                f'Agent run {call.run_id} cannot execute model calls while {run.state}',
                current_state=run.state,
            )

        user = await self.user_loader(run.user_id)
        if user is None:
            raise ModelRunRejected(f'Agent run user not found: {run.user_id}')

        model = await self._resolve_authorized_model(request, user, call.model)
        claim = await self.operation_store.claim_operation(
            call.run_id,
            operation_type='model.call',
            idempotency_key=call.idempotency_key,
            request_hash=_model_call_request_hash(call),
        )
        if not claim.created:
            operation_status = claim.operation.status
            if operation_status == 'in_progress':
                raise ModelOperationInProgress('operation_in_progress')
            if operation_status in {'succeeded', 'failed'}:
                raise ModelStreamNotReplayable(operation_status)
            raise ModelAuthorityError(
                f'Unsupported streaming model operation status: {operation_status}'
            )

        return PreparedStreamModelCall(
            user=user,
            model=model,
            operation_id=claim.operation.id,
        )


def _model_from_catalog(models: Any, model_id: str) -> dict[str, Any] | None:
    """Resolve a model from the loader result without depending on cache writes."""
    if isinstance(models, dict):
        direct = models.get(model_id)
        if isinstance(direct, dict):
            return direct
        candidates = models.get('data')
        if not isinstance(candidates, list):
            candidates = models.values()
    else:
        candidates = models or []

    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get('id') == model_id:
            return candidate
    return None


def _format_model_stream_event(event_type: str, payload: dict[str, Any]) -> bytes:
    """Format a model stream SSE event for agentscope runtime consumption."""
    data = json.dumps(
        {'type': event_type, 'payload': payload},
        ensure_ascii=False,
        separators=(',', ':'),
    )
    return f'data: {data}\n\n'.encode()


def _model_call_form_data(call: ModelCallRequest) -> dict[str, Any]:
    form_data = dict(call.model_extra or {})
    form_data.update(
        {
            'model': call.model,
            'messages': _normalize_agent_model_messages(call.messages),
            'stream': call.stream,
        }
    )
    if call.params:
        params = dict(call.params)
        reasoning = params.pop('reasoning', None)
        if params:
            form_data['params'] = params
        if isinstance(reasoning, dict) and reasoning:
            form_data['reasoning'] = reasoning
    return form_data


def _normalize_agent_model_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        item_type = str(message.get('type') or '')
        if item_type == 'function_call':
            tool_call = _agent_tool_call_from_responses_item(message)
            if tool_call is not None:
                pending_tool_calls.append(tool_call)
            continue

        _flush_agent_tool_calls(normalized, pending_tool_calls)

        if item_type == 'function_call_output':
            tool_message = _agent_tool_message_from_responses_item(message)
            if tool_message is not None:
                normalized.append(tool_message)
            continue

        normalized.append(_clean_agent_model_message(message, item_type=item_type))

    _flush_agent_tool_calls(normalized, pending_tool_calls)
    return normalized


def _flush_agent_tool_calls(
    normalized: list[dict[str, Any]],
    pending_tool_calls: list[dict[str, Any]],
) -> None:
    if not pending_tool_calls:
        return
    normalized.append(
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': list(pending_tool_calls),
        }
    )
    pending_tool_calls.clear()


def _agent_tool_call_from_responses_item(message: dict[str, Any]) -> dict[str, Any] | None:
    call_id = str(message.get('call_id') or '').strip()
    name = str(message.get('name') or '').strip()
    if not call_id or not name:
        return None
    return {
        'id': call_id,
        'type': 'function',
        'function': {
            'name': name,
            'arguments': _agent_json_string(message.get('arguments', '{}')),
        },
    }


def _agent_tool_message_from_responses_item(message: dict[str, Any]) -> dict[str, Any] | None:
    call_id = str(message.get('call_id') or '').strip()
    if not call_id:
        return None
    return {
        'role': 'tool',
        'tool_call_id': call_id,
        'content': _agent_json_string(message.get('output', '')),
    }


def _agent_json_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _clean_agent_model_message(message: dict[str, Any], *, item_type: str) -> dict[str, Any]:
    clean_message = dict(message)
    if item_type == 'message':
        clean_message.pop('type', None)
    clean_message.pop('id', None)
    clean_message.pop('status', None)
    return clean_message


def _model_access_check_bypassed(user) -> bool:
    return BYPASS_MODEL_ACCESS_CONTROL or (
        getattr(user, 'role', None) == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL
    )


def _cached_operation_response(operation) -> dict[str, Any]:
    if operation.status == 'succeeded' and operation.response is not None:
        return operation.response
    if operation.status == 'in_progress':
        raise ModelOperationInProgress('operation_in_progress')
    if operation.status == 'failed':
        error = operation.error or {
            'code': 'model_operation_failed',
            'message': 'Model operation failed before producing a response.',
        }
        raise ModelAuthorityError(error.get('message', 'model operation failed'))
    raise ModelAuthorityError(f'Unsupported operation status: {operation.status}')


def _model_call_request_hash(call: ModelCallRequest) -> str:
    payload = {
        'operation_type': 'model.call',
        'run_id': call.run_id,
        'participant_id': call.participant_id,
        'model_call_id': call.model_call_id,
        'model': call.model,
        'messages': call.messages,
        'stream': call.stream,
        'params': call.params,
        'metadata': call.metadata,
        'extra': call.model_extra or {},
        'service_principal': 'agentscope-runtime',
    }
    return canonical_sha256(payload)


def _structured_error(exc: Exception) -> dict[str, Any]:
    return {
        'code': getattr(exc, 'code', 'model_authority_error'),
        'message': str(exc),
        'retryable': False,
        'details': {},
    }


async def _jsonable_response(response: Any) -> Any:
    if isinstance(response, JSONResponse):
        return _decode_response_body(response)
    if isinstance(response, StreamingResponse):
        return {
            'type': 'streaming_response',
            'status_code': response.status_code,
        }
    if isinstance(response, Response):
        return _decode_response_body(response)
    if isinstance(response, BaseModel):
        return response.model_dump(mode='json')
    return response


def _decode_response_body(response: Response) -> Any:
    body = getattr(response, 'body', b'')
    if isinstance(body, bytes):
        text = body.decode('utf-8', 'replace')
    else:
        text = str(body)
    try:
        return json.loads(text)
    except Exception:
        return text
