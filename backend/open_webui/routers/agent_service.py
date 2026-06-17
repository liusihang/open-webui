from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from open_webui.agent.events import (
    AgentEventError,
    AgentEventStore,
    append_agent_event,
    append_final_delta,
)
from open_webui.agent.protocol import AgentEventAppend, FinalDeltaAppend
from open_webui.agent.service.tool_call import execute_agent_tool_call
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
):
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await execute_agent_tool_call(
            authority,
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key}),
        )
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except ToolOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    except ToolAuthorityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': getattr(exc, 'code', 'tool_authority_error'),
                'message': str(exc),
            },
        ) from exc
