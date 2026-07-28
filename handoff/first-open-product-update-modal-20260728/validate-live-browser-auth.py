#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

BASE_URL = 'http://127.0.0.1'
PRIVATE_DIR = Path('/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/live/private')


def session_role(token_path: Path) -> str:
    request = urllib.request.Request(
        f'{BASE_URL}/api/v1/auths/',
        headers={'Authorization': f'Bearer {token_path.read_text().strip()}'},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    return payload['role']


def main() -> None:
    role = session_role(PRIVATE_DIR / 'admin.token')
    if role != 'admin':
        raise RuntimeError(f'unexpected live acceptance role: {role}')
    request = urllib.request.Request(
        f'{BASE_URL}/api/v1/auths/admin/config',
        headers={
            'Authorization': f'Bearer {(PRIVATE_DIR / "admin.token").read_text().strip()}'
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        json.load(response)
    settings_request = urllib.request.Request(
        f'{BASE_URL}/api/v1/users/user/settings',
        headers={
            'Authorization': f'Bearer {(PRIVATE_DIR / "admin.token").read_text().strip()}'
        },
    )
    with urllib.request.urlopen(settings_request, timeout=10) as response:
        settings = json.load(response) or {}
    acknowledged = (
        settings.get('ui', {}).get('announcementModalKey')
        == '2026-07-chat-agent-memory-v1'
    )
    print(
        json.dumps(
            {
                'live_admin_token_role': role,
                'admin_config_read': True,
                'announcement_acknowledged': acknowledged,
            },
            separators=(',', ':'),
        )
    )


if __name__ == '__main__':
    main()
