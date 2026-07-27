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


def main() -> int:
    token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(minutes=15))
    model = acceptance.choose_model(acceptance.request_json(token, '/api/models'))
    profile = acceptance.request_json(
        token,
        '/api/v1/configs/conversation_mode_profiles/chat',
    )
    suffix = uuid.uuid4().hex[:10]
    marker = f'CHAT-PROTOCOL-OK-{suffix}'
    payload = acceptance.request_payload(
        mode='chat',
        revision_id=profile['revision_id'],
        model=model,
        prompt=f'Reply with exactly {marker} and no other text.',
        chat_id=f'local:pr7-chat-protocol-{suffix}',
    )
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
    lines = text.splitlines()
    sse_data = [line[5:].strip() for line in lines if line.startswith('data:')]
    result = {
        'model_id': model['id'],
        'status': status,
        'content_type': content_type,
        'body_bytes': len(body),
        'line_count': len(lines),
        'sse_data_lines': len(sse_data),
        'sse_done': '[DONE]' in sse_data,
        'marker_seen': marker in text,
    }
    if content_type.startswith('application/json'):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            result['json_top_level_keys'] = sorted(parsed)
            result['json_status'] = parsed.get('status')
            task_ids = parsed.get('task_ids')
            result['json_task_count'] = len(task_ids) if isinstance(task_ids, list) else None
            result['json_has_error'] = bool(parsed.get('error'))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
