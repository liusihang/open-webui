#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get('OPENWEBUI_BASE_URL', 'http://127.0.0.1').rstrip('/')
TOKEN = os.environ.get('OPENWEBUI_TOKEN', '').strip()
CONFIRMATION = os.environ.get('CONFIRM_LIVE_PROFILE_APPLY', '')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TEMPLATE_PATH = Path(
    os.environ.get(
        'PROFILE_TEMPLATE',
        '/home/aiserver/staging/pr7-live-prep-20260727/release/live-admin-mode-profile-template.json',
    )
)


def request_json(path: str, *, method: str = 'GET', body: Any = None) -> Any:
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {TOKEN}',
    }
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        f'{BASE_URL}{path}',
        method=method,
        headers=headers,
        data=data,
    )
    try:
        with OPENER.open(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{method} {path} returned HTTP {exc.code}') from exc


def sanitized_revision(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError('profile response is not an object')
    return {
        key: payload[key]
        for key in (
            'mode',
            'revision_id',
            'revision_number',
            'schema_version',
            'defaults',
            'warnings',
        )
        if key in payload
    }


def forbidden_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if 'model' in key.lower() or 'reasoning' in key.lower():
                return key
            nested = forbidden_key(child)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = forbidden_key(child)
            if nested is not None:
                return nested
    return None


def main() -> int:
    if CONFIRMATION != 'apply-reviewed-chat-agent-profiles-on-aiserver-live':
        raise RuntimeError('live profile confirmation missing')
    if not TOKEN:
        raise RuntimeError('OPENWEBUI_TOKEN is required')
    template = json.loads(TEMPLATE_PATH.read_text(encoding='utf-8'))
    invalid_key = forbidden_key(template)
    if invalid_key is not None:
        raise RuntimeError(f'forbidden profile key: {invalid_key}')

    results: dict[str, Any] = {}
    for mode in ('agent', 'chat'):
        current = request_json(f'/api/v1/configs/conversation_mode_profiles/{mode}')
        current_revision_id = current.get('revision_id') if isinstance(current, dict) else None
        if not isinstance(current_revision_id, str) or not current_revision_id:
            raise RuntimeError(f'{mode} current revision missing')
        profile = template.get(mode, {}).get('profile')
        if not isinstance(profile, dict):
            raise RuntimeError(f'{mode} profile template missing')
        saved = request_json(
            f'/api/v1/configs/conversation_mode_profiles/{mode}/revisions',
            method='POST',
            body={
                'expected_current_revision_id': current_revision_id,
                'profile': profile,
            },
        )
        results[mode] = sanitized_revision(saved)

    expected = {mode: payload.get('revision_id') for mode, payload in results.items()}
    observations = []
    for _ in range(16):
        profiles = request_json('/api/v1/configs/conversation_mode_profiles')
        rows = profiles.get('profiles') if isinstance(profiles, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError('profile convergence response missing')
        heads = {row.get('mode'): row.get('revision_id') for row in rows if isinstance(row, dict)}
        observations.append(heads)
        if any(heads.get(mode) != revision_id for mode, revision_id in expected.items()):
            raise RuntimeError('profile convergence mismatch')

    json.dump(
        {
            'base_url': BASE_URL,
            'profiles': results,
            'convergence_observations': observations,
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
