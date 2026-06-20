import pytest
from open_webui.agent.events import (
    AgentEventRejected,
    AgentEventStore,
    FinalDeltaRejected,
    TextDeltaRejected,
    append_agent_event,
    append_final_delta,
    append_text_delta,
    format_sse_event,
    list_events_for_reconnect,
)
from open_webui.agent.protocol import (
    AgentEventAppend,
    AgentEventType,
    AgentRunEvent,
    AgentRunState,
    FinalDeltaAppend,
    TextDeltaAppend,
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


def test_text_delta_accepts_running_state_without_final_started():
    """text.delta is emitted during the ReAct loop (running state) before
    any final.started — it must not require finalizing or final.started.
    """
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.RUNNING

    event = append_text_delta(
        store,
        TextDeltaAppend(
            run_id='run-1',
            block_id='block-1',
            delta_index=0,
            delta='Let me check',
            participant_id='leader',
            phase='running',
        ),
    )

    assert event.event_type == AgentEventType.TEXT_DELTA
    assert event.seq == 1
    assert event.payload['block_id'] == 'block-1'
    assert event.payload['delta_index'] == 0
    assert event.payload['delta'] == 'Let me check'
    assert store.final_text['run-1'] == 'Let me check'


def test_text_delta_duplicates_are_idempotent():
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.RUNNING

    first = append_text_delta(
        store,
        TextDeltaAppend(
            run_id='run-1',
            block_id='block-1',
            delta_index=0,
            delta='hel',
        ),
    )
    duplicate = append_text_delta(
        store,
        TextDeltaAppend(
            run_id='run-1',
            block_id='block-1',
            delta_index=0,
            delta='hel',
        ),
    )

    assert first.seq == 1
    assert duplicate.seq == 1
    assert duplicate.model_dump() == first.model_dump()
    assert store.final_text['run-1'] == 'hel'
    assert [e.event_type for e in store.events['run-1']].count(
        AgentEventType.TEXT_DELTA
    ) == 1


def test_text_delta_gaps_are_rejected():
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.RUNNING

    append_text_delta(
        store,
        TextDeltaAppend(
            run_id='run-1',
            block_id='block-1',
            delta_index=0,
            delta='hel',
        ),
    )

    with pytest.raises(TextDeltaRejected, match='gap'):
        append_text_delta(
            store,
            TextDeltaAppend(
                run_id='run-1',
                block_id='block-1',
                delta_index=2,
                delta='lo',
            ),
        )


def test_text_delta_multiple_blocks_are_independent_and_concatenate_into_final_text():
    """Two text blocks (e.g. text → tool → text) accumulate independently
    per block_id but the run's final_text sees them concatenated in append
    order.
    """
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.RUNNING

    append_text_delta(
        store,
        TextDeltaAppend(run_id='run-1', block_id='block-a', delta_index=0, delta='First '),
    )
    append_text_delta(
        store,
        TextDeltaAppend(run_id='run-1', block_id='block-a', delta_index=1, delta='thought.'),
    )
    append_text_delta(
        store,
        TextDeltaAppend(run_id='run-1', block_id='block-b', delta_index=0, delta='Second '),
    )
    append_text_delta(
        store,
        TextDeltaAppend(run_id='run-1', block_id='block-b', delta_index=1, delta='answer.'),
    )

    assert store.final_text['run-1'] == 'First thought.Second answer.'
    text_events = [e for e in store.events['run-1'] if e.event_type == AgentEventType.TEXT_DELTA]
    assert len(text_events) == 4
    assert [e.payload['block_id'] for e in text_events] == [
        'block-a',
        'block-a',
        'block-b',
        'block-b',
    ]
    assert [e.payload['delta_index'] for e in text_events] == [0, 1, 0, 1]


def test_text_delta_is_allowed_after_final_started():
    """text.delta is not in POST_FINAL_BLOCKED_EVENT_TYPES — model may keep
    streaming the final answer after final.started."""
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.FINALIZING
    append_agent_event(
        store,
        AgentEventAppend(run_id='run-1', event_type=AgentEventType.FINAL_STARTED),
    )

    event = append_text_delta(
        store,
        TextDeltaAppend(
            run_id='run-1',
            block_id='final-block',
            delta_index=0,
            delta='Streaming final.',
        ),
    )

    assert event.seq == 2
    assert event.event_type == AgentEventType.TEXT_DELTA


def test_text_delta_interleaves_with_tool_events_by_seq():
    """Simulate Claude Code-style 'think → tool → think' interleaving."""
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.RUNNING

    text1 = append_text_delta(
        store,
        TextDeltaAppend(
            run_id='run-1',
            block_id='block-pre',
            delta_index=0,
            delta='Let me check the repo.',
            participant_id='leader',
            phase='running',
        ),
    )
    tool_req = append_agent_event(
        store,
        AgentEventAppend(
            run_id='run-1',
            event_type=AgentEventType.TOOL_REQUESTED,
            participant_id='leader',
            phase='running',
            summary='Running git status',
            payload={'tool_call_id': 'call-1'},
        ),
    )
    tool_done = append_agent_event(
        store,
        AgentEventAppend(
            run_id='run-1',
            event_type=AgentEventType.TOOL_COMPLETED,
            participant_id='leader',
            phase='running',
            summary='git status succeeded',
            payload={'tool_call_id': 'call-1'},
        ),
    )
    text2 = append_text_delta(
        store,
        TextDeltaAppend(
            run_id='run-1',
            block_id='block-post',
            delta_index=0,
            delta='Now I have the info.',
            participant_id='leader',
            phase='running',
        ),
    )

    seqs = [e.seq for e in store.events['run-1']]
    assert seqs == [1, 2, 3, 4]
    assert [e.event_type for e in store.events['run-1']] == [
        AgentEventType.TEXT_DELTA,
        AgentEventType.TOOL_REQUESTED,
        AgentEventType.TOOL_COMPLETED,
        AgentEventType.TEXT_DELTA,
    ]
    assert text1.seq < tool_req.seq < tool_done.seq < text2.seq
    assert store.final_text['run-1'] == 'Let me check the repo.Now I have the info.'


def test_text_delta_payload_is_preserved_and_includes_runtime_metadata():
    store = FakeAgentEventStore()
    store.run_states['run-1'] = AgentRunState.RUNNING

    event = append_text_delta(
        store,
        TextDeltaAppend(
            run_id='run-1',
            block_id='block-meta',
            delta_index=0,
            delta='hello',
            participant_id='leader',
            phase='running',
            payload={'model_call_id': 'mc-1'},
        ),
    )

    assert event.payload['model_call_id'] == 'mc-1'
    assert event.payload['block_id'] == 'block-meta'
    assert event.payload['delta'] == 'hello'
    assert event.payload['text'] == 'hello'
    assert event.participant_id == 'leader'
    assert event.phase == 'running'
