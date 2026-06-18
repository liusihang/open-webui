import hashlib
import json
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from open_webui.agent.approval import (
    AgentApprovalCoordinator,
    ApprovalDecisionConflict,
    ApprovalDecisionRequest,
    ApprovalError,
    ApprovalNotFound,
    ApprovalOperationInProgress,
)
from open_webui.agent.events import (
    AgentEventError,
    AgentEventStore,
    append_agent_event,
    append_agent_event_async,
    append_final_delta,
    append_final_delta_async,
)
from open_webui.agent.model_authority import (
    AgentModelAuthority,
    ModelAuthorityError,
    ModelCallRequest,
    ModelGuardRejected,
    ModelNotAllowed,
    ModelOperationInProgress,
)
from open_webui.agent.model_catalog import ModelCatalogError, ModelSelectionNotAllowed
from open_webui.agent.protocol import (
    AgentEventAppend,
    AgentRunEvent,
    AgentStateTransitionAppend,
    FinalDeltaAppend,
)
from open_webui.agent.service.model_call import execute_agent_model_call
from open_webui.agent.service.tool_call import execute_agent_tool_call
from open_webui.agent.subagents import (
    AgentSubagentCoordinator,
    SubagentError,
    SubagentModelSelectionRequest,
    SubagentRegisterRequest,
)
from open_webui.agent.tool_authority import (
    AgentToolAuthority,
    ToolAuthorityError,
    ToolCallRequest,
    ToolOperationInProgress,
)
from open_webui.models.agent_runs import AgentRunError, AgentRunOperationConflict, AgentRuns
from open_webui.models.chats import Chats
from open_webui.routers.agent_runs import get_configured_agent_event_store

router = APIRouter()


def _require_matching_idempotency_key(
    body_key: str | None,
    header_key: str | None,
) -> str:
    if not body_key and not header_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='idempotency_key_required',
        )
    if body_key and header_key and body_key != header_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='idempotency_key_required',
        )
    return body_key or header_key or ''


class AgentServiceOperationInProgress(Exception):
    pass


def _require_agent_service_credential(request: Request, authorization: str | None) -> None:
    expected_token = getattr(request.app.state.config, 'AGENT_RUNTIME_SERVICE_TOKEN', '') or ''
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Agent service token is not configured',
        )
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='service token required',
        )

    token = authorization.removeprefix('Bearer ').strip()
    if not secrets.compare_digest(token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='invalid service token',
        )


def _trust_internal_model_call(request: Request, run_id: str) -> None:
    request.state.agent_internal_model_call = True
    request.state.agent_run_id = run_id
    request.state.agent_service_principal = 'agentscope-runtime'


def get_agent_model_authority(request: Request) -> AgentModelAuthority:
    authority = getattr(request.app.state, 'AGENT_MODEL_AUTHORITY', None)
    if authority is not None:
        return authority

    return AgentModelAuthority(operation_store=get_agent_operation_store(request))


def get_agent_tool_authority(request: Request, run_id: str | None = None) -> AgentToolAuthority:
    authority = getattr(request.app.state, 'AGENT_TOOL_AUTHORITY', None)
    if authority is not None:
        return authority

    registry = None
    registries = getattr(request.app.state, 'AGENT_TOOL_REGISTRIES', None)
    if run_id and isinstance(registries, dict):
        registry = registries.get(run_id)
    if registry is None:
        registry = getattr(request.app.state, 'AGENT_TOOL_REGISTRY', None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Agent tool registry is not configured',
        )

    return AgentToolAuthority(
        operation_store=get_agent_operation_store(request),
        registry=registry,
        resource_manager=getattr(request.app.state, 'AGENT_RUN_RESOURCE_MANAGER', None),
        artifact_registrar=getattr(request.app.state, 'AGENT_RUN_ARTIFACT_REGISTRAR', None),
    )


def get_agent_approval_coordinator(request: Request) -> AgentApprovalCoordinator:
    coordinator = getattr(request.app.state, 'AGENT_APPROVAL_COORDINATOR', None)
    if coordinator is not None:
        return coordinator

    coordinator = AgentApprovalCoordinator(get_agent_operation_store(request))
    request.app.state.AGENT_APPROVAL_COORDINATOR = coordinator
    return coordinator


def get_agent_subagent_coordinator(request: Request) -> AgentSubagentCoordinator:
    coordinator = getattr(request.app.state, 'AGENT_SUBAGENT_COORDINATOR', None)
    if coordinator is not None:
        return coordinator

    coordinator = AgentSubagentCoordinator()
    request.app.state.AGENT_SUBAGENT_COORDINATOR = coordinator
    return coordinator


def get_agent_operation_store(request: Request):
    return get_configured_agent_event_store(request) or AgentRuns


def get_optional_agent_event_store(request: Request) -> AgentEventStore | None:
    return get_configured_agent_event_store(request)


@router.post('/runs/{run_id}/subagents')
async def execute_agent_run_subagent_registration(
    request: Request,
    run_id: str,
    form_data: SubagentRegisterRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    coordinator: AgentSubagentCoordinator = Depends(get_agent_subagent_coordinator),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await coordinator.register_subagent(
            request,
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key}),
        )
    except SubagentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': getattr(exc, 'code', 'subagent_error'),
                'message': str(exc),
            },
        ) from exc


@router.post('/runs/{run_id}/events')
async def append_agent_run_event(
    request: Request,
    run_id: str,
    form_data: AgentEventAppend,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    store: AgentEventStore | None = Depends(get_optional_agent_event_store),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        event_payload = form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key})
        if store is not None:
            event = append_agent_event(store, event_payload)
        else:
            event = await _append_agent_event_with_operation(event_payload)
    except AgentEventError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except AgentServiceOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    return event


@router.post('/runs/{run_id}/model-selection')
async def execute_agent_run_model_selection(
    request: Request,
    run_id: str,
    form_data: SubagentModelSelectionRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    coordinator: AgentSubagentCoordinator = Depends(get_agent_subagent_coordinator),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await coordinator.select_subagent_model(
            request,
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key}),
        )
    except ModelSelectionNotAllowed as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': exc.code,
                'message': str(exc),
                'warnings': exc.warnings,
            },
        ) from exc
    except ModelCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': getattr(exc, 'code', 'model_catalog_error'),
                'message': str(exc),
            },
        ) from exc
    except SubagentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': getattr(exc, 'code', 'subagent_error'),
                'message': str(exc),
            },
        ) from exc


@router.post('/runs/{run_id}/final-delta')
async def append_agent_run_final_delta(
    request: Request,
    run_id: str,
    form_data: FinalDeltaAppend,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    store: AgentEventStore | None = Depends(get_optional_agent_event_store),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        delta_payload = form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key})
        if store is not None:
            event = append_final_delta(store, delta_payload)
        else:
            event = await _append_final_delta_with_operation(delta_payload)
    except AgentEventError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except AgentServiceOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    return event


@router.post('/runs/{run_id}/state-transition')
async def transition_agent_run_state(
    request: Request,
    run_id: str,
    form_data: AgentStateTransitionAppend,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await _transition_state_with_operation(
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key})
        )
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except AgentServiceOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    except AgentRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': getattr(exc, 'code', 'agent_run_error'),
                'message': str(exc),
            },
        ) from exc


@router.post('/runs/{run_id}/model-call')
async def execute_agent_run_model_call(
    request: Request,
    run_id: str,
    form_data: ModelCallRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    authority: AgentModelAuthority = Depends(get_agent_model_authority),
):
    _require_agent_service_credential(request, authorization)
    _trust_internal_model_call(request, run_id)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await execute_agent_model_call(
            authority,
            request,
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key}),
        )
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except ModelOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    except (ModelGuardRejected, ModelNotAllowed, ModelAuthorityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': getattr(exc, 'code', 'model_authority_error'),
                'message': str(exc),
            },
        ) from exc


@router.post('/runs/{run_id}/tool-call')
async def execute_agent_run_tool_call(
    request: Request,
    run_id: str,
    form_data: ToolCallRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    authority: AgentToolAuthority = Depends(get_agent_tool_authority),
    approval_coordinator: AgentApprovalCoordinator = Depends(get_agent_approval_coordinator),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    tool_request = form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key})
    try:
        tool = authority.registry.get(tool_request.tool_id)
        if tool is not None:
            async def resume_tool_call():
                return await execute_agent_tool_call(authority, tool_request)

            approval_result = await approval_coordinator.request_tool_approval(
                tool_request,
                tool,
                resume=resume_tool_call,
            )
            if approval_result is not None:
                return approval_result

        return await execute_agent_tool_call(
            authority,
            tool_request,
        )
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except (ToolOperationInProgress, ApprovalOperationInProgress):
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    except ApprovalDecisionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': exc.code,
                'message': str(exc),
            },
        ) from exc
    except ApprovalNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                'code': exc.code,
                'message': str(exc),
            },
        ) from exc
    except ApprovalError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': getattr(exc, 'code', 'approval_error'),
                'message': str(exc),
            },
        ) from exc
    except ToolAuthorityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': getattr(exc, 'code', 'tool_authority_error'),
                'message': str(exc),
            },
        ) from exc


async def _append_agent_event_with_operation(event: AgentEventAppend) -> AgentRunEvent:
    claim = await AgentRuns.claim_operation(
        event.run_id,
        operation_type='event.append',
        idempotency_key=event.idempotency_key or '',
        request_hash=_callback_request_hash('event.append', event),
    )
    if not claim.created:
        return _cached_event_operation_response(claim.operation)

    try:
        stored = await append_agent_event_async(AgentRuns, event)
    except Exception as exc:
        await AgentRuns.finish_operation_error(
            claim.operation.id,
            {
                'code': getattr(exc, 'code', 'event_append_failed'),
                'message': str(exc),
            },
        )
        raise

    await AgentRuns.finish_operation_success(
        claim.operation.id,
        stored.model_dump(mode='json'),
    )
    return stored


async def _append_final_delta_with_operation(delta: FinalDeltaAppend) -> AgentRunEvent:
    claim = await AgentRuns.claim_operation(
        delta.run_id,
        operation_type='final.delta',
        idempotency_key=delta.idempotency_key or '',
        request_hash=_callback_request_hash('final.delta', delta),
    )
    if not claim.created:
        return _cached_event_operation_response(claim.operation)

    try:
        stored = await append_final_delta_async(AgentRuns, delta)
    except Exception as exc:
        await AgentRuns.finish_operation_error(
            claim.operation.id,
            {
                'code': getattr(exc, 'code', 'final_delta_failed'),
                'message': str(exc),
            },
        )
        raise

    await AgentRuns.finish_operation_success(
        claim.operation.id,
        stored.model_dump(mode='json'),
    )
    return stored


async def _transition_state_with_operation(
    transition: AgentStateTransitionAppend,
) -> dict[str, Any]:
    claim = await AgentRuns.claim_operation(
        transition.run_id,
        operation_type='state.transition',
        idempotency_key=transition.idempotency_key or '',
        request_hash=_callback_request_hash('state.transition', transition),
    )
    if not claim.created:
        return _cached_state_operation_response(claim.operation)

    try:
        updated = await AgentRuns.transition_state(
            transition.run_id,
            from_states=list(transition.from_states),
            to_state=transition.to_state,
            reason=transition.reason,
            payload=transition.payload,
        )
        if updated.state == 'completed':
            await _write_completed_agent_run_message(updated)
    except Exception as exc:
        await AgentRuns.finish_operation_error(
            claim.operation.id,
            {
                'code': getattr(exc, 'code', 'state_transition_failed'),
                'message': str(exc),
            },
        )
        raise

    response = updated.model_dump(mode='json')
    await AgentRuns.finish_operation_success(claim.operation.id, response)
    return response


def _cached_event_operation_response(operation) -> AgentRunEvent:
    if operation.status == 'succeeded' and operation.response is not None:
        return AgentRunEvent.model_validate(operation.response)
    if operation.status == 'in_progress':
        raise AgentServiceOperationInProgress('operation_in_progress')
    if operation.status == 'failed':
        error = operation.error or {
            'code': 'agent_event_operation_failed',
            'message': 'Agent event operation failed before producing a response.',
        }
        raise AgentEventError(error.get('message', 'agent event operation failed'))
    raise AgentEventError(f'Unsupported operation status: {operation.status}')


def _cached_state_operation_response(operation) -> dict[str, Any]:
    if operation.status == 'succeeded' and operation.response is not None:
        return operation.response
    if operation.status == 'in_progress':
        raise AgentServiceOperationInProgress('operation_in_progress')
    if operation.status == 'failed':
        error = operation.error or {
            'code': 'agent_state_operation_failed',
            'message': 'Agent state operation failed before producing a response.',
        }
        raise AgentRunError(error.get('message', 'agent state operation failed'))
    raise AgentRunError(f'Unsupported operation status: {operation.status}')


async def _write_completed_agent_run_message(run) -> None:
    if not run.chat_id or not run.assistant_message_id:
        return
    if run.chat_id.startswith('local:') or run.chat_id.startswith('channel:'):
        return

    await Chats.upsert_message_to_chat_by_id_and_message_id(
        run.chat_id,
        run.assistant_message_id,
        {
            'agent_run_id': run.id,
            'content': run.final_text or '',
            'done': True,
        },
    )


def _callback_request_hash(operation_type: str, model: Any) -> str:
    body = model.model_dump(mode='json', exclude={'idempotency_key'})
    payload = {
        'operation_type': operation_type,
        'body': body,
        'service_principal': 'agentscope-runtime',
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post('/runs/{run_id}/approvals/{approval_id}/decision')
async def decide_agent_run_approval(
    request: Request,
    run_id: str,
    approval_id: str,
    form_data: ApprovalDecisionRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    approval_coordinator: AgentApprovalCoordinator = Depends(get_agent_approval_coordinator),
):
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await approval_coordinator.decide(
            form_data.model_copy(
                update={
                    'run_id': run_id,
                    'approval_id': approval_id,
                    'idempotency_key': key,
                }
            )
        )
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except ApprovalOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    except ApprovalDecisionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': exc.code,
                'message': str(exc),
            },
        ) from exc
    except ApprovalNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                'code': exc.code,
                'message': str(exc),
            },
        ) from exc
    except ApprovalError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': getattr(exc, 'code', 'approval_error'),
                'message': str(exc),
            },
        ) from exc
