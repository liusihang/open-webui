#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: str
    message: str
    details: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status == 'ok'


def evaluate_env_readiness(env: dict[str, str]) -> ProbeResult:
    enabled = _is_true(env.get('ENABLE_AGENT_MODE'))
    missing = []
    if enabled:
        for key in ('AGENT_RUNTIME_BASE_URL', 'AGENT_RUNTIME_SERVICE_TOKEN'):
            if not env.get(key):
                missing.append(key)

    cap_error = _validate_positive_int(env.get('AGENT_TEAM_MAX_SUBAGENTS'), 'AGENT_TEAM_MAX_SUBAGENTS')
    if cap_error:
        return ProbeResult(
            name='env',
            status='failed',
            message=cap_error,
            details={'enable_agent_mode': enabled},
        )

    if missing:
        return ProbeResult(
            name='env',
            status='failed',
            message='Agent Mode is enabled but required runtime env is missing.',
            details={'missing': missing, 'enable_agent_mode': enabled},
        )

    return ProbeResult(
        name='env',
        status='ok',
        message='Agent Mode runtime env is internally consistent.',
        details={
            'enable_agent_mode': enabled,
            'runtime_base_url_configured': bool(env.get('AGENT_RUNTIME_BASE_URL')),
            'service_token_configured': bool(env.get('AGENT_RUNTIME_SERVICE_TOKEN')),
            'team_max_subagents': env.get('AGENT_TEAM_MAX_SUBAGENTS') or 'default',
        },
    )


def evaluate_runtime_health(status_code: int, payload: Any) -> ProbeResult:
    status_value = payload.get('status') if isinstance(payload, dict) else None
    if status_code == 200 and status_value == 'ok':
        return ProbeResult(
            name='runtime_health',
            status='ok',
            message='AgentScope runtime health endpoint returned ok.',
            details={'http_status': status_code, 'payload': payload},
        )
    return ProbeResult(
        name='runtime_health',
        status='failed',
        message='AgentScope runtime health endpoint did not return status=ok.',
        details={'http_status': status_code, 'payload': payload},
    )


def evaluate_runtime_status(status_code: int, payload: Any, *, run_id: str) -> ProbeResult:
    if status_code == 200 and isinstance(payload, dict) and payload.get('run_id') == run_id:
        return ProbeResult(
            name='runtime_readiness',
            status='ok',
            message='AgentScope runtime protected status endpoint returned the requested run.',
            details={'http_status': status_code, 'payload': payload},
        )
    return ProbeResult(
        name='runtime_readiness',
        status='failed',
        message='AgentScope runtime protected status endpoint did not return the requested run.',
        details={'http_status': status_code, 'payload': payload, 'run_id': run_id},
    )


def probe_runtime_health(base_url: str, *, timeout: float) -> ProbeResult:
    status_code, payload = _get_json(f'{base_url.rstrip("/")}/health', timeout=timeout)
    return evaluate_runtime_health(status_code, payload)


def probe_runtime_status(
    base_url: str,
    *,
    run_id: str,
    service_token: str,
    timeout: float,
) -> ProbeResult:
    headers = {'Authorization': f'Bearer {service_token}'}
    status_code, payload = _get_json(
        f'{base_url.rstrip("/")}/v1/openwebui/runs/{run_id}/status',
        headers=headers,
        timeout=timeout,
    )
    return evaluate_runtime_status(status_code, payload, run_id=run_id)


def run_checks(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    results = []
    if args.check_env:
        results.append(evaluate_env_readiness(env))

    runtime_base_url = args.runtime_base_url or env.get('AGENT_RUNTIME_BASE_URL') or ''
    service_token = args.service_token or env.get('AGENT_RUNTIME_SERVICE_TOKEN') or ''
    if not args.skip_runtime:
        if runtime_base_url:
            results.append(probe_runtime_health(runtime_base_url, timeout=args.timeout))
        else:
            results.append(
                ProbeResult(
                    name='runtime_health',
                    status='failed',
                    message='No runtime base URL supplied.',
                    details={'required_arg': '--runtime-base-url'},
                )
            )

    if args.readiness_run_id:
        if not runtime_base_url or not service_token:
            results.append(
                ProbeResult(
                    name='runtime_readiness',
                    status='failed',
                    message='Readiness status check requires runtime URL and service token.',
                    details={
                        'runtime_base_url_configured': bool(runtime_base_url),
                        'service_token_configured': bool(service_token),
                    },
                )
            )
        else:
            results.append(
                probe_runtime_status(
                    runtime_base_url,
                    run_id=args.readiness_run_id,
                    service_token=service_token,
                    timeout=args.timeout,
                )
            )

    return {
        'status': 'ok' if all(result.ok for result in results) else 'failed',
        'probes': [asdict(result) for result in results],
    }


def format_text(summary: dict[str, Any]) -> str:
    lines = [f'Agent Mode healthcheck: {summary["status"]}']
    for probe in summary['probes']:
        lines.append(f'- {probe["name"]}: {probe["status"]} - {probe["message"]}')
    return '\n'.join(lines)


def _get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _decode_json(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode_json(exc.read())
    except urllib.error.URLError as exc:
        return 0, {'error': str(exc.reason)}
    except TimeoutError as exc:
        return 0, {'error': str(exc)}


def _decode_json(raw: bytes) -> Any:
    text = raw.decode('utf-8', errors='replace')
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {'body': text}


def _is_true(value: str | None) -> bool:
    return (value or '').lower() in {'1', 'true', 'yes', 'on'}


def _validate_positive_int(value: str | None, name: str) -> str | None:
    if value in (None, ''):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return f'{name} must be an integer.'
    if parsed <= 0:
        return f'{name} must be positive.'
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Agent Mode runtime health/readiness checks.')
    parser.add_argument('--runtime-base-url', default='')
    parser.add_argument('--service-token', default='')
    parser.add_argument('--readiness-run-id', default='')
    parser.add_argument('--timeout', type=float, default=5.0)
    parser.add_argument('--check-env', action='store_true')
    parser.add_argument('--skip-runtime', action='store_true')
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = run_checks(args, dict(os.environ))
    if args.format == 'json':
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(format_text(summary))
    return 0 if summary['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
