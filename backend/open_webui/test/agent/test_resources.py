import pytest
from open_webui.agent.protocol import AgentRunState
from open_webui.agent.resources import AgentRunResourceManager


class FakeLifecycleStore:
    def __init__(self):
        self.states = {}
        self.transitions = []
        self.events = []

    def get_run_state(self, run_id):
        return self.states[run_id]

    def transition_state(self, run_id, *, from_states, to_state, reason, payload=None):
        self.transitions.append(
            {
                'run_id': run_id,
                'from_states': from_states,
                'to_state': to_state,
                'reason': reason,
                'payload': payload,
            }
        )
        self.states[run_id] = AgentRunState(to_state)
        return {'id': run_id, 'state': to_state, 'payload': payload}

    def append_event(
        self,
        run_id,
        *,
        event_type,
        participant_id=None,
        phase=None,
        summary=None,
        payload=None,
    ):
        self.events.append(
            {
                'run_id': run_id,
                'event_type': event_type,
                'participant_id': participant_id,
                'phase': phase,
                'summary': summary,
                'payload': payload or {},
            }
        )
        if event_type == 'run.failed':
            self.states[run_id] = AgentRunState.FAILED
        return self.events[-1]


@pytest.mark.asyncio
async def test_terminal_cleanup_closes_resources_once_and_keeps_terminal_processes():
    manager = AgentRunResourceManager()
    calls = {
        'mcp_close': 0,
        'oauth_close': 0,
        'sse_stop': 0,
        'kill_process': 0,
        'compact': 0,
    }
    async def close_mcp():
        calls['mcp_close'] += 1

    def close_oauth():
        calls['oauth_close'] += 1

    async def stop_sse():
        calls['sse_stop'] += 1

    def kill_process():
        calls['kill_process'] += 1

    def compact():
        calls['compact'] += 1
        return {'ui': {'actions': []}}

    manager.register_resource(
        'run-1',
        resource_type='mcp_client',
        resource_key='mcp-main',
        close=close_mcp,
        participant_id='leader',
    )
    manager.register_resource(
        'run-1',
        resource_type='oauth_session',
        resource_key='calendar',
        close=close_oauth,
    )
    manager.register_sse_tail('run-1', 'subscriber-1', stop_sse)
    manager.register_terminal_process(
        'run-1',
        {
            'terminal_server_id': 'terminal-main',
            'process_id': 'proc-1',
            'command': 'python long_job.py',
            'status': 'running',
            'exit_code': None,
        },
        kill=kill_process,
    )

    first = await manager.cleanup_terminal_state(
        'run-1',
        AgentRunState.CANCELLED,
        compact=compact,
    )
    second = await manager.cleanup_terminal_state(
        'run-1',
        AgentRunState.CANCELLED,
        compact=compact,
    )

    assert first.cleaned is True
    assert first.closed_resources == ['mcp_client:mcp-main', 'oauth_session:calendar']
    assert first.stopped_sse_tails == ['subscriber-1']
    assert first.retained_process_refs == [
        {
            'terminal_server_id': 'terminal-main',
            'process_id': 'proc-1',
            'command': 'python long_job.py',
            'status': 'running',
            'exit_code': None,
        }
    ]
    assert first.summary == {'ui': {'actions': []}}
    assert second.cleaned is False
    assert calls == {
        'mcp_close': 1,
        'oauth_close': 1,
        'sse_stop': 1,
        'kill_process': 0,
        'compact': 1,
    }
@pytest.mark.asyncio
async def test_stale_runtime_heartbeat_marks_run_failed_and_runs_cleanup_once():
    now_ns = 2_000_000_000_000
    timeout_seconds = 30
    manager = AgentRunResourceManager()
    store = FakeLifecycleStore()
    store.states = {
        'stale-run': AgentRunState.RUNNING,
        'fresh-run': AgentRunState.RUNNING,
        'completed-run': AgentRunState.COMPLETED,
    }
    calls = {'close_stale': 0, 'close_completed': 0}

    def close_stale():
        calls['close_stale'] += 1

    def close_completed():
        calls['close_completed'] += 1

    manager.record_runtime_heartbeat('stale-run', heartbeat_at_ns=now_ns - 31_000_000_000)
    manager.record_runtime_heartbeat('fresh-run', heartbeat_at_ns=now_ns - 5_000_000_000)
    manager.record_runtime_heartbeat('completed-run', heartbeat_at_ns=now_ns - 99_000_000_000)
    manager.register_resource('stale-run', resource_type='mcp_client', resource_key='mcp-main', close=close_stale)
    manager.register_resource(
        'completed-run',
        resource_type='mcp_client',
        resource_key='mcp-main',
        close=close_completed,
    )

    failed = await manager.fail_stale_heartbeats(
        store,
        now_ns=now_ns,
        timeout_seconds=timeout_seconds,
    )
    failed_again = await manager.fail_stale_heartbeats(
        store,
        now_ns=now_ns + 1_000_000_000,
        timeout_seconds=timeout_seconds,
    )

    assert failed == ['stale-run']
    assert failed_again == []
    assert calls == {'close_stale': 1, 'close_completed': 0}
    assert store.transitions == []
    assert store.events == [
        {
            'run_id': 'stale-run',
            'event_type': 'run.failed',
            'participant_id': 'leader',
            'phase': 'failed',
            'summary': 'Agent runtime heartbeat is stale.',
            'payload': {
                'error': {
                    'code': 'agent_runtime_lost',
                    'message': 'Agent runtime heartbeat is stale.',
                    'details': {
                        'heartbeat_at_ns': now_ns - 31_000_000_000,
                        'timeout_seconds': timeout_seconds,
                    },
                }
            },
        }
    ]
