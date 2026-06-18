from __future__ import annotations

from typing import Any

from open_webui.agent.protocol import AgentEventType

TMP_RETENTION_NS = 7 * 24 * 60 * 60 * 1_000_000_000

_ACTION_EVENTS = {AgentEventType.ACTION_SUMMARY.value}
_TOOL_RESULT_EVENTS = {
    AgentEventType.TOOL_COMPLETED.value,
    AgentEventType.TOOL_FAILED.value,
}
_APPROVAL_EVENTS = {
    AgentEventType.APPROVAL_REQUESTED.value,
    AgentEventType.APPROVAL_COMPLETED.value,
}
_SUBAGENT_EVENTS = {
    AgentEventType.SUBAGENT_CREATED.value,
    AgentEventType.SUBAGENT_UPDATED.value,
    AgentEventType.SUBAGENT_COMPLETED.value,
    AgentEventType.SUBAGENT_FAILED.value,
}
_TERMINAL_EVENTS = {
    AgentEventType.RUN_COMPLETED.value,
    AgentEventType.RUN_FAILED.value,
    AgentEventType.RUN_CANCELLED.value,
    AgentEventType.RUN_BUDGET_EXCEEDED.value,
}


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _event_type(event: Any) -> str:
    value = _value(event, 'event_type')
    return getattr(value, 'value', value)


def _payload(event: Any) -> dict[str, Any]:
    return dict(_value(event, 'payload', None) or {})


def _path_for_run(path: str, run_id: str, folder: str) -> bool:
    return path.startswith(f'/workspace/agent-runs/{run_id}/{folder}/')


def compact_artifact_for_summary(
    artifact: Any,
    *,
    run_id: str,
    now_ns: int,
) -> dict[str, Any]:
    path = _value(artifact, 'path')
    raw_metadata = _value(artifact, 'meta', None)
    if raw_metadata is None:
        raw_metadata = _value(artifact, 'metadata', None)
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}

    if _path_for_run(path, run_id, 'tmp'):
        metadata.update(
            {
                'cleanup_eligible': True,
                'cleanup_after_ns': now_ns + TMP_RETENTION_NS,
                'retention': 'temporary_debug',
            }
        )
    elif _path_for_run(path, run_id, 'outputs'):
        metadata.update(
            {
                'cleanup_eligible': False,
                'retention': 'user_visible_output',
            }
        )
    else:
        metadata.setdefault('cleanup_eligible', False)
        metadata.setdefault('retention', 'external_or_user_selected')

    return {
        'id': _value(artifact, 'id'),
        'kind': _value(artifact, 'kind'),
        'terminal_server_id': _value(artifact, 'terminal_server_id'),
        'path': path,
        'url': _value(artifact, 'url'),
        'mime_type': _value(artifact, 'mime_type'),
        'size': _value(artifact, 'size'),
        'metadata': metadata,
        'created_at': _value(artifact, 'created_at'),
    }


def build_compacted_run_summary(
    *,
    run: Any,
    events: list[Any],
    artifacts: list[Any],
    now_ns: int,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    subagents: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    retained_event_seqs: list[int] = []
    pruned_event_types: list[str] = []

    for event in events:
        event_type = _event_type(event)
        payload = _payload(event)
        retained = False

        if event_type in _ACTION_EVENTS and _value(event, 'summary'):
            actions.append(
                {
                    'seq': _value(event, 'seq'),
                    'participant_id': _value(event, 'participant_id'),
                    'summary': _value(event, 'summary'),
                }
            )
            retained = True
        elif event_type in _TOOL_RESULT_EVENTS:
            tool = {
                'seq': _value(event, 'seq'),
                'participant_id': _value(event, 'participant_id'),
                'name': payload.get('tool_name') or payload.get('name'),
                'summary': _value(event, 'summary'),
                'arguments_summary': payload.get('arguments_summary'),
                'result_status': payload.get('result_status')
                or payload.get('status'),
                'artifacts': payload.get('artifacts') or [],
                'process_refs': payload.get('process_refs') or [],
                'warnings': payload.get('warnings') or [],
                'structured_error': payload.get('structured_error'),
            }
            tools.append(tool)
            warnings.extend(tool['warnings'])
            if tool['structured_error']:
                errors.append(tool['structured_error'])
            retained = True
        elif event_type in _APPROVAL_EVENTS:
            approvals.append(
                {
                    'seq': _value(event, 'seq'),
                    'participant_id': _value(event, 'participant_id'),
                    'summary': _value(event, 'summary'),
                    'approval_id': payload.get('approval_id'),
                    'decision': payload.get('decision'),
                    'payload': payload,
                }
            )
            retained = True
        elif event_type in _SUBAGENT_EVENTS:
            subagents.append(
                {
                    'seq': _value(event, 'seq'),
                    'participant_id': _value(event, 'participant_id'),
                    'summary': _value(event, 'summary'),
                    'status': payload.get('status') or _subagent_status(event_type),
                    'payload': payload,
                }
            )
            retained = True
        elif event_type in _TERMINAL_EVENTS:
            retained = True

        if retained:
            retained_event_seqs.append(_value(event, 'seq'))
        else:
            pruned_event_types.append(event_type)

    run_id = _value(run, 'id')
    run_error = _value(run, 'error')
    if run_error:
        errors.append(run_error)

    return {
        'version': 1,
        'run_id': run_id,
        'state': _value(run, 'state'),
        'compacted_at_ns': now_ns,
        'final_text': _value(run, 'final_text', ''),
        'ui': {
            'participants': _value(run, 'participants', None) or [],
            'actions': actions,
            'tools': tools,
            'approvals': approvals,
            'subagents': subagents,
            'artifacts': [
                compact_artifact_for_summary(
                    artifact,
                    run_id=run_id,
                    now_ns=now_ns,
                )
                for artifact in artifacts
            ],
            'process_refs': _value(run, 'process_refs', None) or [],
            'budget': _value(run, 'budget', None) or {},
            'errors': errors,
            'warnings': warnings,
        },
        'audit': {
            'retained_event_seqs': retained_event_seqs,
            'retained_event_count': len(retained_event_seqs),
            'pruned_event_types': pruned_event_types,
            'first_seq': _value(events[0], 'seq') if events else None,
            'last_seq': _value(events[-1], 'seq') if events else None,
        },
    }


def _subagent_status(event_type: str) -> str:
    if event_type == AgentEventType.SUBAGENT_COMPLETED.value:
        return 'completed'
    if event_type == AgentEventType.SUBAGENT_FAILED.value:
        return 'failed'
    if event_type == AgentEventType.SUBAGENT_CREATED.value:
        return 'created'
    return 'updated'
