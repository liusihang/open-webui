#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path


BASE_URL = 'http://127.0.0.1:18085'
ADMIN_ENV_PATH = Path('/home/aiserver/staging/openwebui-pr7-eea11194ed-test/.test-admin.env')
QUERIES = ('test', 'pr7', 'e2e', 'codex')


def admin_token() -> str:
    values = {}
    for line in ADMIN_ENV_PATH.read_text().splitlines():
        if line and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
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
        return json.load(response)['token']


def main() -> None:
    token = admin_token()
    candidates: dict[str, dict] = {}
    for query in QUERIES:
        url = f'{BASE_URL}/api/v1/users/?{urllib.parse.urlencode({"query": query, "page": 1})}'
        request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
        for user in payload.get('users', []):
            candidate = candidates.setdefault(
                user['id'],
                {
                    'id_hash': hashlib.sha256(user['id'].encode()).hexdigest()[:12],
                    'role': user.get('role'),
                    'created_at': user.get('created_at'),
                    'matched_queries': [],
                },
            )
            candidate['matched_queries'].append(query)

    print(json.dumps({'candidate_count': len(candidates), 'candidates': list(candidates.values())}, separators=(',', ':')))


if __name__ == '__main__':
    main()
