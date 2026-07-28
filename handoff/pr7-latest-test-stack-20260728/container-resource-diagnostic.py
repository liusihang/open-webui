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


def get(token: str, path: str) -> Any:
    request = urllib.request.Request(
        f'{BASE_URL}{path}',
        headers={
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
        },
    )
    with OPENER.open(request, timeout=30) as response:
        return json.load(response)


def sanitize(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        items = payload
        keys: list[str] = []
    elif isinstance(payload, dict):
        keys = sorted(str(key) for key in payload)
        items = next(
            (payload[key] for key in ('data', 'items', 'models', 'skills') if isinstance(payload.get(key), list)),
            [],
        )
    else:
        return {'type': type(payload).__name__}
    return {
        'type': type(payload).__name__,
        'top_level_keys': keys,
        'items': [
            {key: item[key] for key in ('id', 'name', 'type', 'is_active') if key in item}
            for item in items
            if isinstance(item, dict)
        ],
    }


def main() -> int:
    token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
    result = {
        'skills': sanitize(get(token, '/api/v1/skills/')),
        'tools': sanitize(get(token, '/api/v1/tools/')),
        'terminals': sanitize(get(token, '/api/v1/terminals/')),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
