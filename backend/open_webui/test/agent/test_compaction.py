from types import SimpleNamespace

from open_webui.agent.compaction import (
    build_compacted_run_summary,
    compact_artifact_for_summary,
)
from open_webui.agent.protocol import AgentEventType


def _event(seq, event_type, *, summary=None, participant_id='leader', payload=None):
    return SimpleNamespace(
        seq=seq,
        event_type=event_type,
        participant_id=participant_id,
        phase=None,
        summary=summary,
        payload=payload or {},
        created_at=1_718_000_000_000 + seq,
    )


def _artifact(path, metadata=None):
    return SimpleNamespace(
        id=path.rsplit('/', 1)[-1],
        kind='file',
        terminal_server_id='terminal-main',
        path=path,
        url=f'/api/artifacts/{path.rsplit("/", 1)[-1]}',
        mime_type='text/plain',
        size=42,
        metadata=metadata or {},
        created_at=1_718_000_100_000,
    )


def test_tmp_cleanup_eligibility_is_added_only_for_run_tmp_artifacts():
    now_ns = 1_718_000_000_000_000_000

    output = compact_artifact_for_summary(
        _artifact('/workspace/agent-runs/run-1/outputs/report.txt'),
        run_id='run-1',
        now_ns=now_ns,
    )
    tmp = compact_artifact_for_summary(
        _artifact('/workspace/agent-runs/run-1/tmp/scratch.json', {'producer': 'tool'}),
        run_id='run-1',
        now_ns=now_ns,
    )
    other_run_tmp = compact_artifact_for_summary(
        _artifact('/workspace/agent-runs/other-run/tmp/scratch.json'),
        run_id='run-1',
        now_ns=now_ns,
    )

    assert output['metadata']['cleanup_eligible'] is False
    assert output['metadata']['retention'] == 'user_visible_output'
    assert 'cleanup_after_ns' not in output['metadata']
    assert tmp['metadata'] == {
        'producer': 'tool',
        'cleanup_eligible': True,
        'cleanup_after_ns': now_ns + 7 * 24 * 60 * 60 * 1_000_000_000,
        'retention': 'temporary_debug',
    }
    assert other_run_tmp['metadata']['cleanup_eligible'] is False


def test_compacted_summary_reconstructs_expandable_ui_and_prunes_noise():
    now_ns = 1_718_000_000_000_000_000
    run = SimpleNamespace(
        id='run-1',
        state='completed',
        participants=[
            {'id': 'leader', 'role': 'leader', 'model': 'gpt-4.1'},
            {'id': 'subagent-1', 'role': 'subagent', 'model': 'o4-mini'},
        ],
        process_refs=[
            {
                'terminal_server_id': 'terminal-main',
                'process_id': 'proc-1',
                'command': 'python long_job.py',
                'status': 'running',
                'exit_code': None,
                'log_path': '/workspace/logs/proc-1.jsonl',
            }
        ],
        budget={'max_tool_calls': 5, 'tool_calls_used': 2},
        error=None,
        final_text='Final answer.',
    )
    events = [
        _event(1, AgentEventType.RUN_RUNNING, summary='Started'),
        _event(2, AgentEventType.ACTION_SUMMARY, summary='Inspecting files'),
        _event(
            3,
            AgentEventType.TOOL_STARTED,
            summary='Running command',
            payload={'tool_name': 'run_command', 'arguments_summary': 'python long_job.py'},
        ),
        _event(
            4,
            AgentEventType.TOOL_COMPLETED,
            summary='Command started',
            payload={
                'tool_name': 'run_command',
                'arguments_summary': 'python long_job.py',
                'result_status': 'success',
                'artifacts': [{'path': '/workspace/agent-runs/run-1/outputs/report.txt'}],
                'process_refs': [{'process_id': 'proc-1', 'status': 'running'}],
                'warnings': [{'code': 'still_running', 'message': 'Process remains active'}],
            },
        ),
        _event(
            5,
            AgentEventType.APPROVAL_REQUESTED,
            summary='Approval requested',
            payload={'approval_id': 'approval-1', 'action': 'overwrite report.txt'},
        ),
        _event(
            6,
            AgentEventType.APPROVAL_COMPLETED,
            summary='Approval rejected',
            payload={'approval_id': 'approval-1', 'decision': 'rejected'},
        ),
        _event(
            7,
            AgentEventType.SUBAGENT_COMPLETED,
            participant_id='subagent-1',
            summary='Subagent checked citations',
            payload={'status': 'completed'},
        ),
        _event(
            8,
            AgentEventType.FINAL_DELTA,
            summary=None,
            payload={'delta': 'Fine-grained token that should not be retained'},
        ),
        _event(9, 'runtime.heartbeat', summary=None, payload={'heartbeat_at': now_ns}),
        _event(10, AgentEventType.RUN_COMPLETED, summary='Run completed'),
    ]
    artifacts = [
        _artifact('/workspace/agent-runs/run-1/outputs/report.txt'),
        _artifact('/workspace/agent-runs/run-1/tmp/scratch.json'),
    ]

    summary = build_compacted_run_summary(
        run=run,
        events=events,
        artifacts=artifacts,
        now_ns=now_ns,
    )

    assert summary['version'] == 1
    assert summary['run_id'] == 'run-1'
    assert summary['state'] == 'completed'
    assert summary['final_text'] == 'Final answer.'
    assert summary['ui']['participants'] == run.participants
    assert summary['ui']['actions'] == [
        {'seq': 2, 'participant_id': 'leader', 'summary': 'Inspecting files'}
    ]
    assert summary['ui']['tools'] == [
        {
            'seq': 4,
            'participant_id': 'leader',
            'name': 'run_command',
            'summary': 'Command started',
            'arguments_summary': 'python long_job.py',
            'result_status': 'success',
            'artifacts': [{'path': '/workspace/agent-runs/run-1/outputs/report.txt'}],
            'process_refs': [{'process_id': 'proc-1', 'status': 'running'}],
            'warnings': [{'code': 'still_running', 'message': 'Process remains active'}],
            'structured_error': None,
        }
    ]
    assert summary['ui']['approvals'] == [
        {
            'seq': 5,
            'participant_id': 'leader',
            'summary': 'Approval requested',
            'approval_id': 'approval-1',
            'decision': None,
            'payload': {'approval_id': 'approval-1', 'action': 'overwrite report.txt'},
        },
        {
            'seq': 6,
            'participant_id': 'leader',
            'summary': 'Approval rejected',
            'approval_id': 'approval-1',
            'decision': 'rejected',
            'payload': {'approval_id': 'approval-1', 'decision': 'rejected'},
        },
    ]
    assert summary['ui']['subagents'] == [
        {
            'seq': 7,
            'participant_id': 'subagent-1',
            'summary': 'Subagent checked citations',
            'status': 'completed',
            'payload': {'status': 'completed'},
        }
    ]
    assert [artifact['path'] for artifact in summary['ui']['artifacts']] == [
        '/workspace/agent-runs/run-1/outputs/report.txt',
        '/workspace/agent-runs/run-1/tmp/scratch.json',
    ]
    assert summary['ui']['artifacts'][1]['metadata']['cleanup_eligible'] is True
    assert summary['ui']['process_refs'] == run.process_refs
    assert summary['ui']['budget'] == {'max_tool_calls': 5, 'tool_calls_used': 2}
    assert summary['audit']['retained_event_seqs'] == [2, 4, 5, 6, 7, 10]
    assert summary['audit']['pruned_event_types'] == [
        'run.running',
        'tool.started',
        'final.delta',
        'runtime.heartbeat',
    ]
