#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = 'http://127.0.0.1:18085'
ADMIN_ENV_PATH = Path('/home/aiserver/staging/openwebui-pr7-eea11194ed-test/.test-admin.env')
PRIVATE_DIR = Path('/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/isolated/private')
QUERIES = ('test', 'pr7')


def read_admin_env() -> dict[str, str]:
    values = {}
    for line in ADMIN_ENV_PATH.read_text().splitlines():
        if line and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def signin() -> str:
    values = read_admin_env()
    body = json.dumps(
        {
            'email': values['OPENWEBUI_PR7_ADMIN_EMAIL'],
            'password': values['OPENWEBUI_PR7_ADMIN_PASSWORD'],
        }
    ).encode()
    request = urllib.request.Request(
        f'{BASE_URL}/api/v1/auths/signin',
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    if payload.get('role') != 'admin':
        raise RuntimeError('dedicated test administrator no longer has the admin role')
    return payload['token']


def dedicated_users(admin_token: str) -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    for query in QUERIES:
        url = f'{BASE_URL}/api/v1/users/?{urllib.parse.urlencode({"query": query, "page": 1})}'
        request = urllib.request.Request(url, headers={'Authorization': f'Bearer {admin_token}'})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
        for user in payload.get('users', []):
            candidates[user['id']] = user
    return candidates


def main() -> None:
    admin_token = signin()
    users = dedicated_users(admin_token)
    ordinary = sorted(
        (user for user in users.values() if user.get('role') == 'user'),
        key=lambda user: user.get('created_at') or 0,
        reverse=True,
    )
    if not ordinary:
        raise RuntimeError('no dedicated ordinary test user found')

    selected = ordinary[0]
    PRIVATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    (PRIVATE_DIR / 'admin.token').write_text(admin_token)
    (PRIVATE_DIR / 'user.id').write_text(selected['id'])
    os.chmod(PRIVATE_DIR / 'admin.token', 0o600)
    os.chmod(PRIVATE_DIR / 'user.id', 0o600)

    print(
        json.dumps(
            {
                'admin_token_prepared': True,
                'ordinary_user_id_hash': hashlib.sha256(selected['id'].encode()).hexdigest()[:12],
                'ordinary_user_role': selected['role'],
                'ordinary_user_created_at': selected.get('created_at'),
            },
            separators=(',', ':'),
        )
    )


if __name__ == '__main__':
    main()
