#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AcceptanceCase:
    id: str
    title: str
    required_observations: tuple[str, ...]
    live_dependency: str


CASES: tuple[AcceptanceCase, ...] = (
    AcceptanceCase(
        id='scenario_01_ordinary_qa',
        title='Ordinary Q&A streams final answer through Agent Mode',
        required_observations=(
            'event:run.running',
            'event:final.started',
            'event:final.delta',
            'event:run.completed',
            'no_tool_events',
        ),
        live_dependency='W9B2 runtime adapter and W10A SSE UI integration',
    ),
    AcceptanceCase(
        id='scenario_02_single_tool_call',
        title='Single OpenWebUI tool call succeeds',
        required_observations=(
            'event:tool.requested',
            'event:tool.completed',
            'normalized_tool_result:success',
        ),
        live_dependency='W6 tool callback plus W10A event rendering',
    ),
    AcceptanceCase(
        id='scenario_03_terminal_output_artifact',
        title='Open Terminal command registers output artifact',
        required_observations=(
            'tool:run_command',
            'process_ref_registered',
            'artifact:/workspace/agent-runs/<run_id>/outputs',
        ),
        live_dependency='W8 terminal artifact tracking and live Open Terminal workspace',
    ),
    AcceptanceCase(
        id='scenario_04_tmp_artifact_retention',
        title='Tmp artifact is retained and cleanup-eligible',
        required_observations=(
            'artifact:/workspace/agent-runs/<run_id>/tmp',
            'cleanup_eligible:true',
            'retained_after_completion',
        ),
        live_dependency='W8 artifact registration and W11 compaction lifecycle',
    ),
    AcceptanceCase(
        id='scenario_05_destructive_approval',
        title='Destructive action waits for approval',
        required_observations=(
            'event:approval.requested',
            'state:waiting_approval',
            'normalized_tool_result:approval_required',
        ),
        live_dependency='W7 approval gate and W10A approval UI',
    ),
    AcceptanceCase(
        id='scenario_06_subagent_cap_concurrency',
        title='Leader creates concurrent subagents up to cap',
        required_observations=(
            'event:subagent.created',
            'subagent_concurrency:observed',
            'subagent_cap:5',
        ),
        live_dependency='W9B1 subagent control plane and W9B2 runtime adapter',
    ),
    AcceptanceCase(
        id='scenario_07_subagent_model_selection',
        title='Subagent model selection uses meta.agent_selection',
        required_observations=(
            'event:model.selection.requested',
            'event:model.selection.completed',
            'meta.agent_selection',
        ),
        live_dependency='W9A model catalog helper and W9B1 callback binding',
    ),
    AcceptanceCase(
        id='scenario_08_sse_reconnect_backfill',
        title='SSE reconnect backfills by sequence',
        required_observations=(
            'last_event_id_reconnect',
            'backfill_by_seq',
            'dedupe_seq',
        ),
        live_dependency='W2 event stream and W10A frontend SSE subscription',
    ),
    AcceptanceCase(
        id='scenario_09_final_phase_deltas',
        title='Final deltas only stream in final-answer phase',
        required_observations=(
            'event:final.started_before_delta',
            'final.delta_only_finalizing',
            'no_action_after_final.started',
        ),
        live_dependency='W2 final delta contract and W10A renderer',
    ),
    AcceptanceCase(
        id='scenario_10_cancel_keeps_terminal_process',
        title='Cancel stops runtime loop but not Open Terminal process',
        required_observations=(
            'event:run.cancelled',
            'runtime_cancel_requested',
            'process_refs_retained',
            'no_kill_process',
        ),
        live_dependency='W4 cancel endpoint, W8 process refs, and live runtime loop',
    ),
    AcceptanceCase(
        id='scenario_11_terminal_state_compaction',
        title='Terminal states trigger compaction',
        required_observations=(
            'compaction:completed',
            'compaction:failed',
            'compaction:cancelled',
            'compaction:budget_exceeded',
            'summary_retains_expandable_ui',
        ),
        live_dependency='W11 compaction plus terminal-state integration',
    ),
    AcceptanceCase(
        id='scenario_12_runtime_unavailable_failure',
        title='Runtime unavailable is a visible failure when enabled',
        required_observations=(
            'ENABLE_AGENT_MODE:true',
            'runtime_unavailable',
            'event:run.failed',
            'no_silent_legacy_fallback',
        ),
        live_dependency='W3 visible failure path and deployment flag wiring',
    ),
)


def default_fixture_path() -> Path:
    return Path(__file__).resolve().parent / 'fixtures' / 'w12_mvp_fixture.json'


def load_evidence(path: Path) -> dict[str, Any]:
    with path.open(encoding='utf-8') as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f'evidence file must contain a JSON object: {path}')
    return payload


def build_dry_run_summary() -> dict[str, Any]:
    return {
        'mode': 'dry-run',
        'total_cases': len(CASES),
        'valid_cases': 0,
        'live_acceptance': 'pending',
        'message': 'Dry run only lists required W12B evidence; no scenario was executed.',
        'cases': [_case_payload(case, status='pending_live') for case in CASES],
        'failures': [],
    }


def validate_evidence(document: dict[str, Any], *, require_live: bool = False) -> dict[str, Any]:
    scenarios = _scenario_map(document.get('scenarios'))
    failures = []
    cases = []

    for case in CASES:
        scenario = scenarios.get(case.id)
        errors = _validate_scenario(document, case, scenario, require_live=require_live)
        if errors:
            failures.append({'case_id': case.id, 'errors': errors})
        cases.append(
            {
                **_case_payload(case, status=_scenario_status(scenario)),
                'live_status': _scenario_live_status(scenario),
                'evidence': _scenario_evidence(scenario),
            }
        )

    expected_ids = {case.id for case in CASES}
    extra_ids = sorted(set(scenarios) - expected_ids)
    for scenario_id in extra_ids:
        failures.append({'case_id': scenario_id, 'errors': ['unknown scenario id']})

    mode = str(document.get('mode') or 'unknown')
    live_acceptance = _live_acceptance_status(require_live, failures)
    failed_expected_ids = {failure['case_id'] for failure in failures if failure['case_id'] in expected_ids}
    return {
        'mode': mode,
        'total_cases': len(CASES),
        'valid_cases': len(CASES) - len(failed_expected_ids),
        'live_acceptance': live_acceptance,
        'message': _summary_message(mode, live_acceptance),
        'cases': cases,
        'failures': failures,
    }


def _validate_scenario(
    document: dict[str, Any],
    case: AcceptanceCase,
    scenario: dict[str, Any] | None,
    *,
    require_live: bool,
) -> list[str]:
    if scenario is None:
        return ['missing scenario evidence']

    errors = _missing_observation_errors(case, scenario)
    if require_live:
        errors.extend(_live_status_errors(document, scenario))
        return errors

    status = scenario.get('status')
    if status not in {'fixture_passed', 'dry_run_pending', 'live_passed', 'passed'}:
        errors.append(f'unsupported non-live status: {status}')
    if document.get('mode') != 'live' and scenario.get('live_status') == 'passed':
        errors.append('non-live evidence must not claim live acceptance')
    return errors


def _missing_observation_errors(case: AcceptanceCase, scenario: dict[str, Any]) -> list[str]:
    observations = scenario.get('observations')
    if not isinstance(observations, list):
        return ['observations must be a list']

    observed = {str(item) for item in observations}
    return [
        f'missing required observation: {observation}'
        for observation in case.required_observations
        if observation not in observed
    ]


def _live_status_errors(document: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    errors = []
    if document.get('mode') != 'live':
        errors.append('live validation requires top-level mode=live')
    if scenario.get('status') not in {'live_passed', 'passed'}:
        errors.append(f'live scenario status is not passed: {scenario.get("status")}')
    if scenario.get('live_status') != 'passed':
        errors.append(f'live_status is not passed: {scenario.get("live_status")}')
    return errors


def _scenario_map(raw_scenarios: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_scenarios, list):
        return {}
    scenarios = {}
    for scenario in raw_scenarios:
        if not isinstance(scenario, dict):
            continue
        scenario_id = scenario.get('id')
        if isinstance(scenario_id, str):
            scenarios[scenario_id] = scenario
    return scenarios


def _case_payload(case: AcceptanceCase, *, status: str) -> dict[str, Any]:
    return {
        'id': case.id,
        'title': case.title,
        'status': status,
        'required_observations': list(case.required_observations),
        'live_dependency': case.live_dependency,
    }


def _scenario_status(scenario: dict[str, Any] | None) -> str:
    if scenario is None:
        return 'missing'
    return str(scenario.get('status') or 'unknown')


def _scenario_live_status(scenario: dict[str, Any] | None) -> str:
    if scenario is None:
        return 'missing'
    return str(scenario.get('live_status') or 'unknown')


def _scenario_evidence(scenario: dict[str, Any] | None) -> dict[str, Any]:
    if scenario is None:
        return {}
    evidence = scenario.get('evidence') or {}
    return evidence if isinstance(evidence, dict) else {}


def _live_acceptance_status(require_live: bool, failures: list[dict[str, Any]]) -> str:
    if require_live:
        return 'passed' if not failures else 'failed'
    return 'pending'


def _summary_message(mode: str, live_acceptance: str) -> str:
    if live_acceptance == 'passed':
        return 'Live W12B acceptance evidence satisfies all 12 MVP scenarios.'
    if mode == 'fixture':
        return 'Fixture contract is satisfied; W12B live acceptance remains pending.'
    return 'Evidence checked, but W12B live acceptance remains pending.'


def format_text(summary: dict[str, Any]) -> str:
    lines = [
        'Agent Mode W12 acceptance harness',
        f'mode: {summary["mode"]}',
        f'case contract: {summary["valid_cases"]}/{summary["total_cases"]} satisfied',
        f'live acceptance: {summary["live_acceptance"]}',
        f'message: {summary["message"]}',
    ]
    if summary['failures']:
        lines.append('failures:')
        for failure in summary['failures']:
            lines.append(f'- {failure["case_id"]}: {"; ".join(failure["errors"])}')
    else:
        lines.append('failures: none')
    return '\n'.join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Agent Mode W12 MVP acceptance harness.')
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    subparsers = parser.add_subparsers(dest='command', required=True)

    subparsers.add_parser('dry-run', help='List required evidence without executing scenarios.')

    fixture_parser = subparsers.add_parser('fixture', help='Validate the checked-in fixture transcript.')
    fixture_parser.add_argument('--fixture', type=Path, default=default_fixture_path())

    live_parser = subparsers.add_parser('live', help='Validate a real W12B acceptance evidence JSON file.')
    live_parser.add_argument('--evidence', type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == 'dry-run':
            summary = build_dry_run_summary()
        elif args.command == 'fixture':
            summary = validate_evidence(load_evidence(args.fixture), require_live=False)
        else:
            summary = validate_evidence(load_evidence(args.evidence), require_live=True)
    except Exception as exc:
        print(f'acceptance harness failed: {exc}', file=sys.stderr)
        return 2

    if args.format == 'json':
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(format_text(summary))
    return 0 if not summary['failures'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
