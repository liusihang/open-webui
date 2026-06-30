from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from open_webui.agent.protocol import AgentEventType, AgentRunState


USER_INPUT_POLL_INTERVAL_SECONDS = 0.05
DEFAULT_USER_INPUT_TIMEOUT_SECONDS = 300.0

USER_INPUT_TERMINAL_EVENT_TYPES = {
    AgentEventType.USER_INPUT_COMPLETED.value,
    AgentEventType.USER_INPUT_DECLINED.value,
    AgentEventType.USER_INPUT_CANCELLED.value,
    AgentEventType.USER_INPUT_EXPIRED.value,
}

SENSITIVE_FIELD_WORDS = {
    'api_key',
    'apikey',
    'cookie',
    'credential',
    'credentials',
    'password',
    'private_key',
    'secret',
    'token',
}


class UserInputError(ValueError):
    code = 'user_input_error'


class UserInputNotFound(UserInputError):
    code = 'user_input_not_found'


class UserInputOperationInProgress(UserInputError):
    code = 'operation_in_progress'


class UserInputConflict(UserInputError):
    code = 'user_input_conflict'


class UserInputRequest(BaseModel):
    run_id: str
    participant_id: str
    user_input_id: str
    tool_call_id: str
    message: str
    requested_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = None
    allow_cancel: bool = True
    idempotency_key: str | None = None

    @field_validator('message')
    @classmethod
    def message_must_be_safe(cls, message: str) -> str:
        _reject_sensitive_text(message, 'message')
        if not message.strip():
            raise ValueError('message is required')
        return message

    @field_validator('requested_schema')
    @classmethod
    def schema_must_be_safe(cls, requested_schema: dict[str, Any]) -> dict[str, Any]:
        _reject_sensitive_schema(requested_schema)
        return requested_schema


class UserInputCompletionRequest(BaseModel):
    run_id: str
    user_input_id: str
    status: Literal['accepted', 'declined', 'cancelled', 'timeout']
    content: Any = None
    idempotency_key: str | None = None

    @model_validator(mode='after')
    def accepted_requires_content(self) -> 'UserInputCompletionRequest':
        if self.status == 'accepted' and self.content is None:
            raise ValueError('content is required when status is accepted')
        return self


class AgentUserInputCoordinator:
    def __init__(self, store):
        self.store = store

    async def request_user_input(
        self,
        request: UserInputRequest,
        *,
        wait_for_response: bool = True,
        response_timeout_seconds: float | None = None,
        poll_interval_seconds: float = USER_INPUT_POLL_INTERVAL_SECONDS,
    ) -> dict[str, Any]:
        if not request.idempotency_key:
            raise UserInputError('idempotency_key_required')

        request_hash = _request_hash(request)
        claim = await _maybe_await(
            self.store.claim_operation(
                request.run_id,
                operation_type='user_input.request',
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
            )
        )
        if not claim.created:
            cached = _cached_operation_response(claim.operation)
            if wait_for_response and cached.get('status') == 'requested':
                requested = await self._find_request(
                    run_id=request.run_id,
                    user_input_id=request.user_input_id,
                )
                if requested is not None:
                    return await self._wait_for_response(
                        request=request,
                        requested_seq=_event_seq(requested),
                        timeout_seconds=_timeout_seconds(
                            response_timeout_seconds,
                            request.timeout_seconds,
                        ),
                        poll_interval_seconds=poll_interval_seconds,
                    )
            return cached

        response = {
            'status': 'requested',
            'user_input_id': request.user_input_id,
        }
        try:
            await _maybe_await(
                self.store.transition_state(
                    request.run_id,
                    from_states=[AgentRunState.RUNNING.value],
                    to_state=AgentRunState.WAITING_USER_INPUT.value,
                    reason='agent requested user input',
                    payload={
                        'user_input_id': request.user_input_id,
                        'tool_call_id': request.tool_call_id,
                    },
                )
            )
            requested_event = await _append_user_input_event(
                self.store,
                request.run_id,
                event_type=AgentEventType.USER_INPUT_REQUESTED.value,
                participant_id=request.participant_id,
                phase=AgentRunState.WAITING_USER_INPUT.value,
                summary='Needs your input',
                payload=_request_event_payload(request),
            )
            await _maybe_await(
                self.store.finish_operation_success(claim.operation.id, response)
            )
            if not wait_for_response:
                return response
            return await self._wait_for_response(
                request=request,
                requested_seq=_event_seq(requested_event),
                timeout_seconds=_timeout_seconds(
                    response_timeout_seconds,
                    request.timeout_seconds,
                ),
                poll_interval_seconds=poll_interval_seconds,
            )
        except Exception as exc:
            await _maybe_await(
                self.store.finish_operation_error(
                    claim.operation.id,
                    {
                        'code': getattr(exc, 'code', 'user_input_request_failed'),
                        'message': str(exc),
                    },
                )
            )
            raise

    async def complete(self, request: UserInputCompletionRequest) -> dict[str, Any]:
        if not request.idempotency_key:
            raise UserInputError('idempotency_key_required')

        request_hash = _completion_hash(request)
        claim = await _maybe_await(
            self.store.claim_operation(
                request.run_id,
                operation_type='user_input.result',
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
            )
        )
        if not claim.created:
            return _cached_operation_response(claim.operation)

        try:
            recorded = await self._find_recorded_response(
                run_id=request.run_id,
                user_input_id=request.user_input_id,
                after_seq=0,
            )
            if recorded is not None:
                response = _response_from_recorded(recorded)
                if response != _completion_response(request):
                    await _finish_operation_error(
                        self.store,
                        claim.operation.id,
                        code=UserInputConflict.code,
                        message='user input already has a different result',
                    )
                    raise UserInputConflict('user input already has a different result')
                await _maybe_await(
                    self.store.finish_operation_success(claim.operation.id, response)
                )
                return response

            requested = await self._find_request(
                run_id=request.run_id,
                user_input_id=request.user_input_id,
            )
            if requested is None:
                await _finish_operation_error(
                    self.store,
                    claim.operation.id,
                    code=UserInputNotFound.code,
                    message=f'Unknown user input request: {request.user_input_id}',
                )
                raise UserInputNotFound(f'Unknown user input request: {request.user_input_id}')

            response = await self._record_completion(request, requested)
            await _maybe_await(
                self.store.finish_operation_success(claim.operation.id, response)
            )
            return response
        except Exception as exc:
            if not isinstance(exc, (UserInputNotFound, UserInputConflict)):
                await _finish_operation_error(
                    self.store,
                    claim.operation.id,
                    code=getattr(exc, 'code', 'user_input_result_failed'),
                    message=str(exc),
                )
            raise

    async def _wait_for_response(
        self,
        *,
        request: UserInputRequest,
        requested_seq: int,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 0.0)
        while True:
            recorded = await self._find_recorded_response(
                run_id=request.run_id,
                user_input_id=request.user_input_id,
                after_seq=requested_seq,
            )
            if recorded is not None:
                return _response_from_recorded(recorded)

            now = asyncio.get_running_loop().time()
            if now >= deadline:
                completion = UserInputCompletionRequest(
                    run_id=request.run_id,
                    user_input_id=request.user_input_id,
                    status='timeout',
                    idempotency_key=f'user-input-timeout:{request.user_input_id}',
                )
                requested = await self._find_request(
                    run_id=request.run_id,
                    user_input_id=request.user_input_id,
                )
                if requested is None:
                    return _completion_response(completion)
                return await self._record_completion(completion, requested)
            await asyncio.sleep(min(poll_interval_seconds, max(deadline - now, 0.0)))

    async def _record_completion(
        self,
        request: UserInputCompletionRequest,
        requested: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            **(requested.get('payload') or {}),
            'status': request.status,
        }
        if request.content is not None:
            payload['content'] = request.content

        await _maybe_await(
            self.store.transition_state(
                request.run_id,
                from_states=[AgentRunState.WAITING_USER_INPUT.value],
                to_state=AgentRunState.RUNNING.value,
                reason=f'user input {request.status}',
                payload={
                    'user_input_id': request.user_input_id,
                    'status': request.status,
                },
            )
        )
        await _append_user_input_event(
            self.store,
            request.run_id,
            event_type=_completion_event_type(request.status),
            participant_id=requested.get('participant_id'),
            phase=AgentRunState.RUNNING.value,
            summary=_completion_summary(request.status),
            payload=payload,
        )
        return _completion_response(request)

    async def _find_request(
        self,
        *,
        run_id: str,
        user_input_id: str,
    ) -> dict[str, Any] | None:
        return await self._find_event(
            run_id=run_id,
            user_input_id=user_input_id,
            event_types={AgentEventType.USER_INPUT_REQUESTED.value},
            after_seq=0,
        )

    async def _find_recorded_response(
        self,
        *,
        run_id: str,
        user_input_id: str,
        after_seq: int,
    ) -> dict[str, Any] | None:
        return await self._find_event(
            run_id=run_id,
            user_input_id=user_input_id,
            event_types=USER_INPUT_TERMINAL_EVENT_TYPES,
            after_seq=after_seq,
        )

    async def _find_event(
        self,
        *,
        run_id: str,
        user_input_id: str,
        event_types: set[str],
        after_seq: int,
    ) -> dict[str, Any] | None:
        list_events_after = getattr(self.store, 'list_events_after', None)
        if list_events_after is None:
            return None
        events = await _maybe_await(list_events_after(run_id, after_seq=after_seq))
        for event in events:
            normalized = _event_dict(event)
            payload = normalized.get('payload') or {}
            if (
                normalized.get('event_type') in event_types
                and payload.get('user_input_id') == user_input_id
            ):
                return normalized
        return None


def _request_event_payload(request: UserInputRequest) -> dict[str, Any]:
    return {
        'user_input_id': request.user_input_id,
        'tool_call_id': request.tool_call_id,
        'message': request.message,
        'requested_schema': request.requested_schema,
        'timeout_seconds': request.timeout_seconds,
        'allow_cancel': request.allow_cancel,
    }


def _completion_response(request: UserInputCompletionRequest) -> dict[str, Any]:
    response = {
        'status': request.status,
        'user_input_id': request.user_input_id,
    }
    if request.content is not None:
        response['content'] = request.content
    return response


def _response_from_recorded(recorded: dict[str, Any]) -> dict[str, Any]:
    payload = recorded.get('payload') or {}
    status = payload.get('status')
    if status not in {'accepted', 'declined', 'cancelled', 'timeout'}:
        event_type = recorded.get('event_type')
        status = {
            AgentEventType.USER_INPUT_COMPLETED.value: 'accepted',
            AgentEventType.USER_INPUT_DECLINED.value: 'declined',
            AgentEventType.USER_INPUT_CANCELLED.value: 'cancelled',
            AgentEventType.USER_INPUT_EXPIRED.value: 'timeout',
        }.get(event_type, 'cancelled')
    response = {
        'status': status,
        'user_input_id': payload.get('user_input_id'),
    }
    if 'content' in payload:
        response['content'] = payload['content']
    return response


def _completion_event_type(status: str) -> str:
    if status == 'accepted':
        return AgentEventType.USER_INPUT_COMPLETED.value
    if status == 'declined':
        return AgentEventType.USER_INPUT_DECLINED.value
    if status == 'cancelled':
        return AgentEventType.USER_INPUT_CANCELLED.value
    return AgentEventType.USER_INPUT_EXPIRED.value


def _completion_summary(status: str) -> str:
    if status == 'accepted':
        return 'User input submitted'
    if status == 'declined':
        return 'User input declined'
    if status == 'cancelled':
        return 'User input cancelled'
    return 'User input timed out'


async def _append_user_input_event(
    store,
    run_id: str,
    *,
    event_type: str,
    participant_id: str | None,
    phase: str,
    summary: str,
    payload: dict[str, Any],
) -> Any:
    return await _maybe_await(
        store.append_event(
            run_id,
            event_type=event_type,
            participant_id=participant_id,
            phase=phase,
            summary=summary,
            payload=payload,
        )
    )


async def _finish_operation_error(store, operation_id: str, *, code: str, message: str) -> None:
    await _maybe_await(
        store.finish_operation_error(
            operation_id,
            {
                'code': code,
                'message': message,
            },
        )
    )


def _request_hash(request: UserInputRequest) -> str:
    return _hash_payload(
        {
            'operation_type': 'user_input.request',
            'run_id': request.run_id,
            'participant_id': request.participant_id,
            'user_input_id': request.user_input_id,
            'tool_call_id': request.tool_call_id,
            'message': request.message,
            'requested_schema': request.requested_schema,
            'timeout_seconds': request.timeout_seconds,
            'allow_cancel': request.allow_cancel,
        }
    )


def _completion_hash(request: UserInputCompletionRequest) -> str:
    return _hash_payload(
        {
            'operation_type': 'user_input.result',
            'run_id': request.run_id,
            'user_input_id': request.user_input_id,
            'status': request.status,
            'content': request.content,
        }
    )


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _cached_operation_response(operation) -> dict[str, Any]:
    if operation.status == 'succeeded' and operation.response is not None:
        return operation.response
    if operation.status == 'in_progress':
        raise UserInputOperationInProgress('operation_in_progress')
    if operation.status == 'failed':
        error = operation.error or {
            'code': 'user_input_operation_failed',
            'message': 'User input operation failed before producing a response.',
        }
        raise UserInputError(error.get('message', 'user input operation failed'))
    raise UserInputError(f'Unsupported operation status: {operation.status}')


def _timeout_seconds(*values: float | None) -> float:
    for value in values:
        if value is None:
            continue
        try:
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            continue
    return DEFAULT_USER_INPUT_TIMEOUT_SECONDS


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _event_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    if hasattr(event, 'model_dump'):
        return event.model_dump(mode='json')
    return dict(getattr(event, '__dict__', {}))


def _event_seq(event: Any) -> int:
    value = _event_dict(event).get('seq', 0)
    return value if isinstance(value, int) else 0


def _reject_sensitive_schema(schema: Any, path: str = 'requested_schema') -> None:
    if isinstance(schema, dict):
        for key, value in schema.items():
            key_text = str(key)
            _reject_sensitive_text(key_text, f'{path}.{key_text}')
            if key_text in {'title', 'description'} and isinstance(value, str):
                _reject_sensitive_text(value, f'{path}.{key_text}')
            _reject_sensitive_schema(value, f'{path}.{key_text}')
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            _reject_sensitive_schema(value, f'{path}[{index}]')
    elif isinstance(schema, str):
        _reject_sensitive_text(schema, path)


def _reject_sensitive_text(text: str, path: str) -> None:
    normalized = text.lower().replace('-', '_').replace(' ', '_')
    if any(word in normalized for word in SENSITIVE_FIELD_WORDS):
        raise ValueError(f'{path} must not request secrets or credentials')
