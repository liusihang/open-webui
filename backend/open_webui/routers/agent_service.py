import secrets

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
    append_final_delta,
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
from open_webui.agent.protocol import AgentEventAppend, FinalDeltaAppend
from open_webui.agent.service.model_call import execute_agent_model_call
from open_webui.agent.service.tool_call import execute_agent_tool_call
from open_webui.agent.subagents import (
    AgentSubagentCoordinator,
    SubagentError,
    SubagentModelSelectionRequest,
)
from open_webui.agent.tool_authority import (
    AgentToolAuthority,
    ToolAuthorityError,
    ToolCallRequest,
    ToolOperationInProgress,
)
from open_webui.models.agent_runs import AgentRunOperationConflict
from open_webui.routers.agent_runs import get_agent_event_store

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

    return AgentModelAuthority(operation_store=get_agent_event_store(request))


def get_agent_tool_authority(request: Request) -> AgentToolAuthority:
    authority = getattr(request.app.state, 'AGENT_TOOL_AUTHORITY', None)
    if authority is not None:
        return authority

    registry = getattr(request.app.state, 'AGENT_TOOL_REGISTRY', None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Agent tool registry is not configured',
        )

    return AgentToolAuthority(
        operation_store=get_agent_event_store(request),
        registry=registry,
    )


def get_agent_approval_coordinator(request: Request) -> AgentApprovalCoordinator:
    coordinator = getattr(request.app.state, 'AGENT_APPROVAL_COORDINATOR', None)
    if coordinator is not None:
        return coordinator

    coordinator = AgentApprovalCoordinator(get_agent_event_store(request))
    request.app.state.AGENT_APPROVAL_COORDINATOR = coordinator
    return coordinator


def get_agent_subagent_coordinator(request: Request) -> AgentSubagentCoordinator:
    coordinator = getattr(request.app.state, 'AGENT_SUBAGENT_COORDINATOR', None)
    if coordinator is not None:
        return coordinator

    coordinator = AgentSubagentCoordinator()
    request.app.state.AGENT_SUBAGENT_COORDINATOR = coordinator
    return coordinator


@router.post('/runs/{run_id}/events')
async def append_agent_run_event(
    request: Request,
    run_id: str,
    form_data: AgentEventAppend,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    store: AgentEventStore = Depends(get_agent_event_store),
):
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        event = append_agent_event(
            store,
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key}),
        )
    except AgentEventError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
    store: AgentEventStore = Depends(get_agent_event_store),
):
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        event = append_final_delta(
            store,
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key}),
        )
    except AgentEventError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return event


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
    authority: AgentToolAuthority = Depends(get_agent_tool_authority),
    approval_coordinator: AgentApprovalCoordinator = Depends(get_agent_approval_coordinator),
):
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
