#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from typing import Any

from open_webui.utils.auth import create_token

BASE_URL = 'http://127.0.0.1:8080'
ADMIN_USER_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ('data', 'items', 'models'):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def main() -> int:
    token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
    request = urllib.request.Request(
        f'{BASE_URL}/api/models',
        headers={
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
        },
    )
    with OPENER.open(request, timeout=30) as response:
        payload = json.load(response)
    matches = sorted(
        item['id'] for item in rows(payload) if isinstance(item.get('id'), str) and 'gpt-5.5' in item['id'].lower()
    )
    print(json.dumps({'matching_model_ids': matches}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
