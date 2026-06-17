import json
from typing import Protocol, runtime_checkable

from open_webui.agent.protocol import (
    AgentEventAppend,
    AgentEventType,
    AgentRunEvent,
    AgentRunState,
    FinalDeltaAppend,
)


class AgentEventError(ValueError):
    code = 'agent_event_error'


class AgentEventRejected(AgentEventError):
    code = 'agent_event_rejected'


class FinalDeltaRejected(AgentEventError):
    code = 'final_delta_rejected'


class AgentEventStoreConflict(AgentEventError):
    code = 'agent_event_store_conflict'


@runtime_checkable
class AgentEventStore(Protocol):
    def get_run_state(self, run_id: str) -> AgentRunState: ...

    def has_final_started(self, run_id: str) -> bool: ...

    def append_event(self, event: AgentEventAppend) -> AgentRunEvent: ...

    def list_events_after(self, run_id: str, after_seq: int = 0) -> list[AgentRunEvent]: ...

    def append_final_text_delta(
        self,
        run_id: str,
        final_stream_id: str,
        delta_index: int,
        delta: str,
    ) -> str: ...


POST_FINAL_BLOCKED_EVENT_TYPES = {
    AgentEventType.TOOL_REQUESTED,
    AgentEventType.TOOL_STARTED,
    AgentEventType.TOOL_COMPLETED,
    AgentEventType.TOOL_FAILED,
    AgentEventType.ARTIFACT_REGISTERED,
    AgentEventType.SUBAGENT_CREATED,
    AgentEventType.SUBAGENT_UPDATED,
    AgentEventType.SUBAGENT_COMPLETED,
    AgentEventType.SUBAGENT_FAILED,
    AgentEventType.MODEL_SELECTION_REQUESTED,
    AgentEventType.MODEL_SELECTION_COMPLETED,
}

TERMINAL_EVENT_TYPES = {
    AgentEventType.RUN_COMPLETED,
    AgentEventType.RUN_FAILED,
    AgentEventType.RUN_CANCELLED,
    AgentEventType.RUN_BUDGET_EXCEEDED,
}


def append_agent_event(
    store: AgentEventStore,
    event: AgentEventAppend,
) -> AgentRunEvent:
    if (
        store.has_final_started(event.run_id)
        and event.event_type in POST_FINAL_BLOCKED_EVENT_TYPES
    ):
        raise AgentEventRejected(
            f'{event.event_type.value} cannot be appended after final.started'
        )

    return store.append_event(event)


def list_events_for_reconnect(
    store: AgentEventStore,
    run_id: str,
    after_seq: int = 0,
) -> list[AgentRunEvent]:
    if after_seq < 0:
        raise AgentEventRejected('after_seq must be greater than or equal to 0')
    return store.list_events_after(run_id, after_seq=after_seq)


def append_final_delta(
    store: AgentEventStore,
    delta: FinalDeltaAppend,
) -> AgentRunEvent:
    if store.get_run_state(delta.run_id) != AgentRunState.FINALIZING:
        raise FinalDeltaRejected('final.delta is only accepted while run is finalizing')
    if not store.has_final_started(delta.run_id):
        raise FinalDeltaRejected('final.delta requires final.started first')

    before_events = store.list_events_after(delta.run_id, after_seq=0)
    before_final_delta_count = _count_final_deltas(
        before_events,
        delta.final_stream_id,
        delta.delta_index,
    )

    try:
        text_after_delta = store.append_final_text_delta(
            delta.run_id,
            delta.final_stream_id,
            delta.delta_index,
            delta.delta,
        )
    except ValueError as exc:
        raise FinalDeltaRejected(f'final delta gap: {exc}') from exc

    after_events = store.list_events_after(delta.run_id, after_seq=0)
    after_final_delta_count = _count_final_deltas(
        after_events,
        delta.final_stream_id,
        delta.delta_index,
    )
    if after_final_delta_count > before_final_delta_count:
        return _find_final_delta_event(after_events, delta.final_stream_id, delta.delta_index)

    duplicate_event = _find_final_delta_event(
        after_events,
        delta.final_stream_id,
        delta.delta_index,
        required=False,
    )
    if duplicate_event is not None:
        return duplicate_event

    return append_agent_event(
        store,
        AgentEventAppend(
            run_id=delta.run_id,
            event_type=AgentEventType.FINAL_DELTA,
            participant_id=delta.participant_id,
            phase=AgentRunState.FINALIZING.value,
            summary=None,
            payload={
                **delta.payload,
                'final_stream_id': delta.final_stream_id,
                'delta_index': delta.delta_index,
                'delta': delta.delta,
                'text': text_after_delta,
            },
            idempotency_key=delta.idempotency_key,
        ),
    )


def format_sse_event(event: AgentRunEvent) -> str:
    data = event.model_dump(mode='json')
    rendered_data = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
    return f'id: {event.seq}\nevent: {event.event_type.value}\ndata: {rendered_data}\n\n'


def format_sse_backfill(events: list[AgentRunEvent]) -> str:
    return ''.join(format_sse_event(event) for event in events)


def resolve_after_seq(
    *,
    after_seq: int | None = None,
    last_event_id: str | None = None,
) -> int:
    if after_seq is not None:
        if after_seq < 0:
            raise AgentEventRejected('after_seq must be greater than or equal to 0')
        return after_seq
    if not last_event_id:
        return 0
    try:
        parsed = int(last_event_id)
    except ValueError as exc:
        raise AgentEventRejected('Last-Event-ID must be an integer sequence') from exc
    if parsed < 0:
        raise AgentEventRejected('Last-Event-ID must be greater than or equal to 0')
    return parsed


def _count_final_deltas(
    events: list[AgentRunEvent],
    final_stream_id: str,
    delta_index: int,
) -> int:
    return sum(
        1
        for event in events
        if event.event_type == AgentEventType.FINAL_DELTA
        and event.payload.get('final_stream_id') == final_stream_id
        and event.payload.get('delta_index') == delta_index
    )


def _find_final_delta_event(
    events: list[AgentRunEvent],
    final_stream_id: str,
    delta_index: int,
    *,
    required: bool = True,
) -> AgentRunEvent | None:
    for event in events:
        if (
            event.event_type == AgentEventType.FINAL_DELTA
            and event.payload.get('final_stream_id') == final_stream_id
            and event.payload.get('delta_index') == delta_index
        ):
            return event
    if required:
        raise AgentEventStoreConflict('final delta text was stored without an event')
    return None
