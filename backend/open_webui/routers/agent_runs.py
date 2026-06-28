import asyncio
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from open_webui.agent.runtime_client import AgentRuntimeClient, AgentRuntimeError
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
log = logging.getLogger(__name__)

ACTIVE_CANCEL_FROM_STATES = ['queued', 'running', 'waiting_approval', 'finalizing']
TERMINAL_STATES = {'completed', 'failed', 'cancelled', 'budget_exceeded'}
TERMINAL_EVENT_TYPES = {'run.completed', 'run.failed', 'run.cancelled', 'run.budget_exceeded'}
AGENT_RUN_EVENTS_POLL_SECONDS = float(os.getenv('AGENT_RUN_EVENTS_POLL_SECONDS', '0.1'))
AGENT_RUN_EVENTS_HEARTBEAT_SECONDS = float(os.getenv('AGENT_RUN_EVENTS_HEARTBEAT_SECONDS', '15.0'))


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


@router.post('/{run_id}/cancel', response_model=AgentRunDetailResponse)
async def cancel_agent_run_endpoint(
    run_id: str,
    request: Request,
    user=Depends(get_verified_user),
):
    return await cancel_agent_run(request, run_id, user=user)


async def cancel_agent_run(
    request: Request,
    run_id: str,
    *,
    user,
    reason: str = 'user requested cancellation',
) -> AgentRunDetailResponse:
    run = await AgentRuns.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Agent Run not found',
        )
    if run.user_id != user.id and getattr(user, 'role', None) != 'admin':
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Agent Run not found',
        )
    if run.state in TERMINAL_STATES and run.state != 'cancelled':
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'Agent Run is already {run.state}',
        )

    if run.state == 'cancelled':
        updated = run
    else:
        updated = await AgentRuns.transition_state(
            run.id,
            from_states=ACTIVE_CANCEL_FROM_STATES,
            to_state='cancelled',
            reason=reason,
        )
        await AgentRuns.append_event(
            run.id,
            event_type='run.cancelled',
            participant_id='leader',
            phase='cancelled',
            summary='Agent run cancelled.',
            payload={'runtime_session_id': run.runtime_session_id},
        )

    await _request_runtime_cancel(request, run.id)
    return _agent_run_detail(updated)


async def cancel_agent_runs_for_chat(
    request: Request,
    chat_id: str,
    *,
    user,
) -> list[str]:
    runs = await AgentRuns.list_runs_by_chat(chat_id, user.id)
    cancelled_run_ids = []
    for run in runs:
        if run.state not in ACTIVE_CANCEL_FROM_STATES:
            continue
        await cancel_agent_run(
            request,
            run.id,
            user=user,
            reason='chat stop requested cancellation',
        )
        cancelled_run_ids.append(run.id)
    return cancelled_run_ids


async def _request_runtime_cancel(request: Request, run_id: str) -> None:
    client = AgentRuntimeClient(
        getattr(request.app.state.config, 'AGENT_RUNTIME_BASE_URL', ''),
        service_token=getattr(request.app.state.config, 'AGENT_RUNTIME_SERVICE_TOKEN', ''),
        timeout=getattr(request.app.state.config, 'AGENT_RUN_DEFAULT_TIMEOUT_SECONDS', None),
    )
    try:
        await client.cancel_run(run_id)
    except AgentRuntimeError as exc:
        log.warning('Agent runtime cancel request failed for run_id=%s: %s', run_id, exc)


def _agent_run_detail(run) -> AgentRunDetailResponse:
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
        current_after_seq = resolved_after_seq
        last_heartbeat_at = time.monotonic()
        if events:
            current_after_seq = events[-1].seq
            yield format_sse_backfill(events)
            if _contains_terminal_event(events):
                return

        while not await request.is_disconnected():
            await asyncio.sleep(max(0.01, AGENT_RUN_EVENTS_POLL_SECONDS))
            new_events = await _list_agent_run_events_for_tail(
                request,
                store,
                run_id,
                after_seq=current_after_seq,
            )
            if new_events:
                current_after_seq = new_events[-1].seq
                last_heartbeat_at = time.monotonic()
                yield format_sse_backfill(new_events)
                if _contains_terminal_event(new_events):
                    return
                continue
            if time.monotonic() - last_heartbeat_at >= AGENT_RUN_EVENTS_HEARTBEAT_SECONDS:
                last_heartbeat_at = time.monotonic()
                yield ': keep-alive\n\n'

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


async def _list_agent_run_events_for_tail(
    request: Request,
    store,
    run_id: str,
    *,
    after_seq: int,
):
    if store is not None:
        return list_events_for_reconnect(
            store,
            run_id,
            after_seq=after_seq,
        )
    return await list_events_for_reconnect_async(
        AgentRuns,
        run_id,
        after_seq=after_seq,
    )


def _contains_terminal_event(events) -> bool:
    return any(
        str(getattr(event.event_type, 'value', event.event_type)) in TERMINAL_EVENT_TYPES
        for event in events
    )


def agent_run_error_response(exc: Exception) -> dict[str, Any]:
    return {
        'error': {
            'code': getattr(exc, 'code', 'agent_run_error'),
            'message': str(exc),
        }
    }
