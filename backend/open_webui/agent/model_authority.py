from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse, Response, StreamingResponse

from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL
from open_webui.models.agent_runs import AgentRunOperationConflict
from open_webui.models.users import Users
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.models import check_model_access, get_all_models


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
        if not models:
            await self.model_loader(request, user)
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


def _model_call_form_data(call: ModelCallRequest) -> dict[str, Any]:
    form_data = dict(call.model_extra or {})
    form_data.update(
        {
            'model': call.model,
            'messages': call.messages,
            'stream': call.stream,
        }
    )
    if call.params:
        form_data['params'] = call.params
    return form_data


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
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


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
