import pytest
from open_webui.agent.events import (
    AgentEventRejected,
    AgentEventStore,
    FinalDeltaRejected,
    append_agent_event,
    append_final_delta,
    format_sse_event,
    list_events_for_reconnect,
)
from open_webui.agent.protocol import (
    AgentEventAppend,
    AgentEventType,
    AgentRunEvent,
    AgentRunState,
    FinalDeltaAppend,
)


class FakeAgentEventStore:
    def __init__(self):
        self.events: dict[str, list[AgentRunEvent]] = {}
        self.run_states: dict[str, AgentRunState] = {}
        self.final_started: set[str] = set()
        self.final_text: dict[str, str] = {}
        self.final_delta_indexes: dict[tuple[str, str], set[int]] = {}
        self.socket_emitter_called = False

    def get_run_state(self, run_id: str) -> AgentRunState:
        return self.run_states[run_id]

    def has_final_started(self, run_id: str) -> bool:
        return run_id in self.final_started

    def append_event(self, event: AgentEventAppend) -> AgentRunEvent:
        seq = len(self.events.setdefault(event.run_id, [])) + 1
        stored = AgentRunEvent(
            run_id=event.run_id,
            seq=seq,
            event_type=event.event_type,
            participant_id=event.participant_id,
            phase=event.phase,
            summary=event.summary,
            payload=event.payload,
            created_at=1_718_000_000_000 + seq,
        )
        self.events[event.run_id].append(stored)
        if event.event_type == AgentEventType.FINAL_STARTED:
            self.final_started.add(event.run_id)
        return stored

    def list_events_after(self, run_id: str, after_seq: int = 0) -> list[AgentRunEvent]:
        return [event for event in self.events.get(run_id, []) if event.seq > after_seq]

    def append_final_text_delta(
        self,
        run_id: str,
        final_stream_id: str,
        delta_index: int,
        delta: str,
    ) -> str:
        key = (run_id, final_stream_id)
        seen = self.final_delta_indexes.setdefault(key, set())
        if delta_index in seen:
            return self.final_text.get(run_id, '')
        expected = len(seen)
        if delta_index != expected:
            raise ValueError(f'expected delta_index {expected}, got {delta_index}')
        seen.add(delta_index)
        self.final_text[run_id] = self.final_text.get(run_id, '') + delta
        return self.final_text[run_id]


def test_fake_store_satisfies_event_store_protocol():
    assert isinstance(FakeAgentEventStore(), AgentEventStore)


def test_append_agent_event_assigns_monotonic_sequences_and_lists_after_seq():
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.RUNNING

    first = append_agent_event(
        store,
        AgentEventAppend(
            run_id='run-1',
            event_type=AgentEventType.RUN_RUNNING,
            summary='Started',
        ),
    )
    second = append_agent_event(
        store,
        AgentEventAppend(
            run_id='run-1',
            event_type=AgentEventType.ACTION_SUMMARY,
            participant_id='leader',
            summary='Working',
            payload={'step': 'inspect'},
        ),
    )

    assert first.seq == 1
    assert second.seq == 2
    assert list_events_for_reconnect(store, 'run-1', after_seq=1) == [second]


def test_sse_backfill_formats_event_id_type_and_json_payload():
    event = AgentRunEvent(
        run_id='run-1',
        seq=3,
        event_type=AgentEventType.TOOL_COMPLETED,
        participant_id='leader',
        phase='running',
        summary='Tool finished',
        payload={'ok': True},
        created_at=1_718_000_000_003,
    )

    rendered = format_sse_event(event)

    assert rendered.startswith('id: 3\n')
    assert 'event: tool.completed\n' in rendered
    assert '"run_id":"run-1"' in rendered
    assert '"seq":3' in rendered
    assert rendered.endswith('\n\n')


def test_final_delta_is_rejected_unless_run_is_finalizing():
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.RUNNING

    with pytest.raises(FinalDeltaRejected, match='finalizing'):
        append_final_delta(
            store,
            FinalDeltaAppend(
                run_id='run-1',
                final_stream_id='answer',
                delta_index=0,
                delta='hello',
            ),
        )

    assert store.events.get('run-1') is None
    assert store.final_text.get('run-1') is None


def test_final_delta_duplicates_are_idempotent_and_gaps_are_rejected():
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.FINALIZING
    append_agent_event(
        store,
        AgentEventAppend(run_id='run-1', event_type=AgentEventType.FINAL_STARTED),
    )

    first = append_final_delta(
        store,
        FinalDeltaAppend(
            run_id='run-1',
            final_stream_id='answer',
            delta_index=0,
            delta='hel',
        ),
    )
    duplicate = append_final_delta(
        store,
        FinalDeltaAppend(
            run_id='run-1',
            final_stream_id='answer',
            delta_index=0,
            delta='hel',
        ),
    )

    assert first.seq == 2
    assert duplicate.seq == 2
    assert store.final_text['run-1'] == 'hel'
    assert [event.event_type for event in store.events['run-1']].count(
        AgentEventType.FINAL_DELTA
    ) == 1

    with pytest.raises(FinalDeltaRejected, match='gap'):
        append_final_delta(
            store,
            FinalDeltaAppend(
                run_id='run-1',
                final_stream_id='answer',
                delta_index=2,
                delta='lo',
            ),
        )


@pytest.mark.parametrize(
    'event_type',
    [
        AgentEventType.TOOL_REQUESTED,
        AgentEventType.TOOL_STARTED,
        AgentEventType.TOOL_COMPLETED,
        AgentEventType.TOOL_FAILED,
        AgentEventType.SUBAGENT_CREATED,
        AgentEventType.SUBAGENT_UPDATED,
        AgentEventType.SUBAGENT_COMPLETED,
        AgentEventType.SUBAGENT_FAILED,
        AgentEventType.ARTIFACT_REGISTERED,
        AgentEventType.MODEL_SELECTION_REQUESTED,
        AgentEventType.MODEL_SELECTION_COMPLETED,
    ],
)
def test_tool_subagent_artifact_and_model_events_are_rejected_after_final_started(
    event_type,
):
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.FINALIZING
    append_agent_event(
        store,
        AgentEventAppend(run_id='run-1', event_type=AgentEventType.FINAL_STARTED),
    )

    with pytest.raises(AgentEventRejected, match='final.started'):
        append_agent_event(
            store,
            AgentEventAppend(run_id='run-1', event_type=event_type),
        )


@pytest.mark.parametrize(
    'event_type',
    [
        AgentEventType.RUN_COMPLETED,
        AgentEventType.RUN_FAILED,
        AgentEventType.RUN_CANCELLED,
        AgentEventType.RUN_BUDGET_EXCEEDED,
    ],
)
def test_terminal_status_events_are_allowed_after_final_started(event_type):
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.FINALIZING
    append_agent_event(
        store,
        AgentEventAppend(run_id='run-1', event_type=AgentEventType.FINAL_STARTED),
    )

    event = append_agent_event(
        store,
        AgentEventAppend(run_id='run-1', event_type=event_type),
    )

    assert event.event_type == event_type


def test_final_delta_helper_does_not_depend_on_socket_event_emitter():
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.FINALIZING
    append_agent_event(
        store,
        AgentEventAppend(run_id='run-1', event_type=AgentEventType.FINAL_STARTED),
    )

    append_final_delta(
        store,
        FinalDeltaAppend(
            run_id='run-1',
            final_stream_id='answer',
            delta_index=0,
            delta='Only SSE owns this.',
        ),
    )

    assert store.socket_emitter_called is False
