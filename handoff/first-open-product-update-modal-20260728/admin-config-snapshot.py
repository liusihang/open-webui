#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = 'http://127.0.0.1:18085'
PRIVATE_DIR = Path('/home/aiserver/staging/pr7-announcement-hotpatch-6ba5c1398/isolated/private')
TOKEN_PATH = PRIVATE_DIR / 'admin.token'
SNAPSHOT_PATH = PRIVATE_DIR / 'admin-config.before-e2e.json'


def request(method: str, body: dict | None = None) -> dict:
    token = TOKEN_PATH.read_text().strip()
    payload = None if body is None else json.dumps(body).encode()
    http_request = urllib.request.Request(
        f'{BASE_URL}/api/v1/auths/admin/config',
        data=payload,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        method=method,
    )
    with urllib.request.urlopen(http_request, timeout=15) as response:
        return json.load(response)


def main() -> None:
    action = sys.argv[1]
    if action == 'backup':
        if SNAPSHOT_PATH.exists():
            raise RuntimeError('admin config snapshot already exists')
        SNAPSHOT_PATH.write_text(json.dumps(request('GET'), separators=(',', ':')))
        os.chmod(SNAPSHOT_PATH, 0o600)
        print('admin_config_snapshot_created=true')
        return
    if action == 'restore':
        restored = request('POST', json.loads(SNAPSHOT_PATH.read_text()))
        if restored != json.loads(SNAPSHOT_PATH.read_text()):
            raise RuntimeError('admin config restore readback mismatch')
        print('admin_config_restored=true')
        return
    raise RuntimeError('usage: admin-config-snapshot.py backup|restore')


if __name__ == '__main__':
    main()
