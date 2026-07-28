#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.request
import uuid

from open_webui.utils.auth import create_token


BASE_URL = 'http://127.0.0.1:8080'
MODEL_ID = 'bifrostapi.Cliproxy/gpt-5.5'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request_json(token: str, path: str, *, method: str = 'GET', body=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = urllib.request.Request(
        f'{BASE_URL}{path}',
        method=method,
        data=data,
        headers={
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
            **({'Content-Type': 'application/json'} if data is not None else {}),
        },
    )
    with OPENER.open(request, timeout=60) as response:
        return json.load(response)


def main() -> None:
    token = create_token(
        {'id': os.environ['ADMIN_USER_ID']},
        expires_delta=dt.timedelta(minutes=20),
    )
    models = request_json(token, '/api/models')
    rows = models.get('data') if isinstance(models, dict) else models
    model = next(item for item in rows if item.get('id') == MODEL_ID)
    profile = request_json(token, '/api/v1/configs/conversation_mode_profiles/agent')
    suffix = uuid.uuid4().hex[:10]
    user_id = str(uuid.uuid4())
    assistant_id = str(uuid.uuid4())
    prompt = (
        'Your first action must be request_user_input. Ask for one required string '
        f'named answer and wait. Cancellation marker {suffix}.'
    )
    payload = {
        'stream': True,
        'model': MODEL_ID,
        'model_item': model,
        'chat_mode': 'agent',
        'mode_profile_revision_id': profile['revision_id'],
        'messages': [{'role': 'user', 'content': prompt}],
        'params': {'function_calling': 'native', 'temperature': 0},
        'chat_id': f'local:pr7-live-cancel-{suffix}',
        'id': assistant_id,
        'parent_id': None,
        'user_message': {
            'id': user_id,
            'parentId': None,
            'childrenIds': [assistant_id],
            'role': 'user',
            'content': prompt,
            'timestamp': int(time.time()),
            'models': [MODEL_ID],
        },
        'tool_ids': [],
        'background_tasks': {},
        'features': {},
        'variables': {},
    }
    started = time.monotonic()
    created = request_json(token, '/api/chat/completions', method='POST', body=payload)
    run_id = created.get('agent_run_id')
    if created.get('status') is not True or not isinstance(run_id, str):
        raise RuntimeError('cancel probe did not start an Agent run')

    events = []
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        event_payload = request_json(token, f'/api/agent/runs/{run_id}/events/list')
        events = event_payload.get('events') or []
        if any(event.get('event_type') == 'user_input.requested' for event in events):
            break
        if any(event.get('event_type') in {'run.failed', 'run.completed'} for event in events):
            raise RuntimeError('cancel probe terminated before waiting state')
        time.sleep(0.25)
    else:
        raise RuntimeError('cancel probe never reached waiting_user_input')

    cancelled = request_json(token, f'/api/agent/runs/{run_id}/cancel', method='POST')
    if cancelled.get('state') != 'cancelled':
        raise RuntimeError(f'cancel endpoint returned state {cancelled.get("state")}')

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        event_payload = request_json(token, f'/api/agent/runs/{run_id}/events/list')
        events = event_payload.get('events') or []
        if any(event.get('event_type') == 'run.cancelled' for event in events):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError('run.cancelled event did not converge')

    print(
        json.dumps(
            {
                'ok': True,
                'run_id': run_id,
                'chat_id': payload['chat_id'],
                'state': cancelled['state'],
                'event_types': [event.get('event_type') for event in events],
                'duration_seconds': round(time.monotonic() - started, 3),
            },
            separators=(',', ':'),
        )
    )


if __name__ == '__main__':
    main()
