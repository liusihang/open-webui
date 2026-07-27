#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.error
import urllib.request
import uuid

from open_webui.utils.auth import create_token

sys.path.insert(0, '/tmp')
import container_acceptance as acceptance  # type: ignore[import-not-found]  # noqa: E402

ADMIN_USER_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'
MODEL_ID = 'bifrostapi.Cliproxy/gpt-5.5'


def main() -> int:
    token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
    models = acceptance.rows(acceptance.request_json(token, '/api/models'))
    model = next(item for item in models if item.get('id') == MODEL_ID)
    profile = acceptance.request_json(
        token,
        '/api/v1/configs/conversation_mode_profiles/chat',
    )
    suffix = uuid.uuid4().hex[:10]
    marker = f'PRODUCT-CHAT-OK-{suffix}'
    payload = acceptance.request_payload(
        mode='chat',
        revision_id=profile['revision_id'],
        model=model,
        prompt=f'Reply with exactly {marker} and no other text.',
        chat_id=f'local:unused-{suffix}',
    )
    payload.pop('chat_id')
    request = urllib.request.Request(
        f'{acceptance.BASE_URL}/api/chat/completions',
        method='POST',
        data=json.dumps(payload).encode(),
        headers={
            'Accept': 'text/event-stream',
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
    )
    try:
        with acceptance.OPENER.open(request, timeout=150) as response:
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
        choices = parsed.get('choices') if isinstance(parsed, dict) else None
        if not isinstance(choices, list):
            continue
        for choice in choices:
            delta = choice.get('delta') if isinstance(choice, dict) else None
            if isinstance(delta, dict) and isinstance(delta.get('content'), str):
                deltas.append(delta['content'])
    result = {
        'model_id': model['id'],
        'status': status,
        'content_type': content_type,
        'body_bytes': len(body),
        'data_lines': len(data_lines),
        'content_delta_count': len(deltas),
        'done': '[DONE]' in data_lines,
        'marker_seen': marker in ''.join(deltas),
        'user_message_id': payload['user_message']['id'],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result['status'] == 200 and result['done'] and result['marker_seen'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
