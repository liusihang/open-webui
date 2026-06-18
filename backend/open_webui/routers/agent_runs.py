from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from open_webui.agent.events import (
    AgentEventRejected,
    AgentEventStore,
    format_sse_backfill,
    list_events_for_reconnect,
    list_events_for_reconnect_async,
    resolve_after_seq,
)
from open_webui.agent.protocol import AgentEventListResponse, AgentRunDetailResponse
from open_webui.models.agent_runs import AgentRunNotFound, AgentRuns
from open_webui.utils.auth import get_verified_user

router = APIRouter()


def get_configured_agent_event_store(request: Request) -> AgentEventStore | None:
    store = getattr(request.app.state, 'AGENT_EVENT_STORE', None)
    if store is None:
        store = getattr(request.app.state, 'agent_event_store', None)
    return store


def get_agent_event_store(request: Request) -> AgentEventStore:
    store = get_configured_agent_event_store(request)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Agent Run storage is not configured',
        )
    return store


@router.get('/{run_id}', response_model=AgentRunDetailResponse)
async def get_agent_run(
    run_id: str,
    request: Request,
    user=Depends(get_verified_user),
):
    store = get_configured_agent_event_store(request)
    if hasattr(store, 'get_run'):
        return store.get_run(run_id)

    if store is not None:
        return AgentRunDetailResponse(
            id=run_id,
            state=store.get_run_state(run_id),
        )

    run = await AgentRuns.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Agent Run not found',
        )
    return AgentRunDetailResponse(
        id=run.id,
        state=run.state,
        state_version=run.state_version,
        chat_id=run.chat_id,
        assistant_message_id=run.assistant_message_id,
        summary=run.summary,
        error=run.error,
    )


@router.get('/{run_id}/events/list', response_model=AgentEventListResponse)
async def list_agent_run_events(
    run_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    user=Depends(get_verified_user),
):
    store = get_configured_agent_event_store(request)
    if store is not None:
        events = list_events_for_reconnect(store, run_id, after_seq=after_seq)
    else:
        try:
            events = await list_events_for_reconnect_async(
                AgentRuns,
                run_id,
                after_seq=after_seq,
            )
        except AgentRunNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Agent Run not found',
            ) from exc
    return AgentEventListResponse(
        events=events,
        last_seq=events[-1].seq if events else after_seq,
    )


@router.get('/{run_id}/events')
async def stream_agent_run_events(
    run_id: str,
    request: Request,
    after_seq: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias='Last-Event-ID'),
    user=Depends(get_verified_user),
):
    try:
        resolved_after_seq = resolve_after_seq(
            after_seq=after_seq,
            last_event_id=last_event_id,
        )
        store = get_configured_agent_event_store(request)
        if store is not None:
            events = list_events_for_reconnect(
                store,
                run_id,
                after_seq=resolved_after_seq,
            )
        else:
            events = await list_events_for_reconnect_async(
                AgentRuns,
                run_id,
                after_seq=resolved_after_seq,
            )
    except AgentEventRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except AgentRunNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Agent Run not found',
        ) from exc

    async def event_stream():
        yield format_sse_backfill(events)

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


def agent_run_error_response(exc: Exception) -> dict[str, Any]:
    return {
        'error': {
            'code': getattr(exc, 'code', 'agent_run_error'),
            'message': str(exc),
        }
    }
