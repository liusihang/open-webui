#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_SCOPE = 'Agent Mode W12 live acceptance evidence'


def load_document(path: Path) -> dict[str, Any]:
    with path.open(encoding='utf-8') as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f'evidence fragment must be a JSON object: {path}')
    return document


def merge_fragments(
    paths: list[Path],
    *,
    base_commit: str | None = None,
    scope: str = DEFAULT_SCOPE,
) -> dict[str, Any]:
    scenarios: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}

    for path in paths:
        document = load_document(path)
        raw_scenarios = document.get('scenarios')
        if not isinstance(raw_scenarios, list):
            raise ValueError(f'evidence fragment missing scenarios list: {path}')

        for scenario in raw_scenarios:
            if not isinstance(scenario, dict):
                raise ValueError(f'scenario entry must be an object in {path}')
            scenario_id = scenario.get('id')
            if not isinstance(scenario_id, str) or not scenario_id:
                raise ValueError(f'scenario entry missing string id in {path}')
            if scenario_id in scenarios:
                raise ValueError(
                    f'duplicate scenario id {scenario_id!r} in {path} and {sources[scenario_id]}'
                )
            scenarios[scenario_id] = scenario
            sources[scenario_id] = str(path)

    return {
        'mode': 'live',
        'base_commit': base_commit or 'unknown',
        'scope': scope,
        'scenarios': [scenarios[scenario_id] for scenario_id in sorted(scenarios)],
    }


def write_document(document: dict[str, Any], output: Path | None) -> None:
    payload = json.dumps(document, indent=2, sort_keys=True)
    if output is None:
        print(payload)
        return
    output.write_text(f'{payload}\n', encoding='utf-8')


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Merge W12 worker evidence fragments.')
    parser.add_argument('fragments', nargs='+', type=Path)
    parser.add_argument('--base-commit', default=None)
    parser.add_argument('--scope', default=DEFAULT_SCOPE)
    parser.add_argument('--output', type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        document = merge_fragments(
            args.fragments,
            base_commit=args.base_commit,
            scope=args.scope,
        )
        write_document(document, args.output)
    except Exception as exc:
        print(f'merge W12 evidence failed: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
