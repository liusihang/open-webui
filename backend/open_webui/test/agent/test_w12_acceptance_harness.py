import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def load_acceptance_harness():
    path = ROOT / 'scripts' / 'agent_mode' / 'acceptance_harness.py'
    spec = importlib.util.spec_from_file_location('acceptance_harness', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_contract_covers_all_twelve_mvp_scenarios_without_live_claim():
    harness = load_acceptance_harness()
    document = harness.load_evidence(harness.default_fixture_path())

    summary = harness.validate_evidence(document, require_live=False)

    assert summary['mode'] == 'fixture'
    assert summary['total_cases'] == 12
    assert summary['valid_cases'] == 12
    assert summary['live_acceptance'] == 'pending'
    assert summary['failures'] == []
    assert all(case['live_status'] == 'pending_w9b2_w10a' for case in summary['cases'])


def test_live_validation_rejects_fixture_mode_and_pending_live_status():
    harness = load_acceptance_harness()
    document = harness.load_evidence(harness.default_fixture_path())

    summary = harness.validate_evidence(document, require_live=True)

    assert summary['live_acceptance'] == 'failed'
    assert len(summary['failures']) == 12
    assert 'live validation requires top-level mode=live' in summary['failures'][0]['errors']


def test_missing_required_scenario_reduces_satisfied_count():
    harness = load_acceptance_harness()
    document = harness.load_evidence(harness.default_fixture_path())
    broken = json.loads(json.dumps(document))
    broken['scenarios'] = [
        scenario for scenario in broken['scenarios'] if scenario['id'] != 'scenario_12_runtime_unavailable_failure'
    ]

    summary = harness.validate_evidence(broken, require_live=False)

    assert summary['valid_cases'] == 11
    assert summary['failures'] == [
        {
            'case_id': 'scenario_12_runtime_unavailable_failure',
            'errors': ['missing scenario evidence'],
        }
    ]


def test_non_live_fixture_cannot_claim_live_passed():
    harness = load_acceptance_harness()
    document = harness.load_evidence(harness.default_fixture_path())
    broken = json.loads(json.dumps(document))
    broken['scenarios'][0]['live_status'] = 'passed'

    summary = harness.validate_evidence(broken, require_live=False)

    assert summary['valid_cases'] == 11
    assert summary['failures'] == [
        {
            'case_id': 'scenario_01_ordinary_qa',
            'errors': ['non-live evidence must not claim live acceptance'],
        }
    ]
