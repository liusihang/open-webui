import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def load_merge_module():
    path = ROOT / 'scripts' / 'agent_mode' / 'merge_w12b_evidence.py'
    spec = importlib.util.spec_from_file_location('merge_w12b_evidence', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_fragment(path: Path, scenarios: list[dict]):
    path.write_text(json.dumps({'scenarios': scenarios}), encoding='utf-8')


def test_merges_worker_fragments_into_live_evidence_document(tmp_path):
    merge = load_merge_module()
    first = tmp_path / 'runtime.json'
    second = tmp_path / 'subagents.json'
    write_fragment(
        first,
        [
            {
                'id': 'scenario_12_runtime_unavailable_failure',
                'status': 'live_passed',
                'live_status': 'passed',
                'observations': [
                    'ENABLE_AGENT_MODE:true',
                    'runtime_unavailable',
                    'event:run.failed',
                    'no_silent_legacy_fallback',
                ],
            }
        ],
    )
    write_fragment(
        second,
        [
            {
                'id': 'scenario_06_subagent_cap_concurrency',
                'status': 'live_passed',
                'live_status': 'passed',
                'observations': [
                    'event:subagent.created',
                    'subagent_concurrency:observed',
                    'subagent_cap:5',
                ],
            }
        ],
    )

    document = merge.merge_fragments([first, second], base_commit='abc123')

    assert document['mode'] == 'live'
    assert document['base_commit'] == 'abc123'
    assert document['scope'] == 'Agent Mode W12B live acceptance evidence'
    assert [scenario['id'] for scenario in document['scenarios']] == [
        'scenario_06_subagent_cap_concurrency',
        'scenario_12_runtime_unavailable_failure',
    ]


def test_duplicate_scenario_ids_are_rejected(tmp_path):
    merge = load_merge_module()
    first = tmp_path / 'first.json'
    second = tmp_path / 'second.json'
    scenario = {
        'id': 'scenario_08_sse_reconnect_backfill',
        'status': 'live_passed',
        'live_status': 'passed',
        'observations': ['last_event_id_reconnect', 'backfill_by_seq', 'dedupe_seq'],
    }
    write_fragment(first, [scenario])
    write_fragment(second, [scenario])

    try:
        merge.merge_fragments([first, second], base_commit='abc123')
    except ValueError as exc:
        assert 'duplicate scenario id' in str(exc)
    else:
        raise AssertionError('duplicate scenario ids should be rejected')
