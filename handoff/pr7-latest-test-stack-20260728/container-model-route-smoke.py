#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.request
import uuid
from typing import Any

from open_webui.utils.auth import create_token

BASE_URL = 'http://127.0.0.1:8080'
ADMIN_USER_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get('data'), list):
        return [item for item in payload['data'] if isinstance(item, dict)]
    return []


def get_json(token: str, path: str) -> Any:
    request = urllib.request.Request(
        f'{BASE_URL}{path}',
        headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'},
    )
    with OPENER.open(request, timeout=30) as response:
        return json.load(response)


def probe(token: str, model: dict[str, Any]) -> dict[str, Any]:
    marker = f'ROUTE-OK-{uuid.uuid4().hex[:10]}'
    payload = {
        'stream': True,
        'model': model['id'],
        'model_item': model,
        'messages': [
            {
                'role': 'user',
                'content': f'Reply with exactly {marker} and no other text.',
            }
        ],
        'params': {},
    }
    request = urllib.request.Request(
        f'{BASE_URL}/api/chat/completions',
        method='POST',
        data=json.dumps(payload).encode(),
        headers={
            'Accept': 'text/event-stream',
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
    )
    try:
        with OPENER.open(request, timeout=150) as response:
            body = response.read()
            status = response.status
            content_type = response.headers.get('Content-Type', '')
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
        content_type = exc.headers.get('Content-Type', '')
    text = body.decode('utf-8', 'replace')
    data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith('data:')]
    deltas: list[str] = []
    for data in data_lines:
        if data == '[DONE]':
            continue
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        choices = parsed.get('choices')
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get('delta')
            if isinstance(delta, dict) and isinstance(delta.get('content'), str):
                deltas.append(delta['content'])
    return {
        'model_id': model['id'],
        'status': status,
        'content_type': content_type,
        'body_bytes': len(body),
        'data_lines': len(data_lines),
        'done': '[DONE]' in data_lines,
        'content_delta_count': len(deltas),
        'marker_seen': marker in ''.join(deltas),
    }


def main() -> int:
    token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
    models = rows(get_json(token, '/api/models'))
    selected = [
        model
        for model in models
        if model.get('id')
        in {
            'bifrostapi.Cliproxy/gpt-5.5',
            'bifrostapi.lucen/gpt-5.5',
        }
    ]
    result = [probe(token, model) for model in selected]
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if any(item['status'] == 200 and item['done'] and item['marker_seen'] for item in result) else 1


if __name__ == '__main__':
    raise SystemExit(main())
