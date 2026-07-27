#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from typing import Any

from open_webui.utils.auth import create_token

sys.path.insert(0, '/tmp')
import pr7_dual_mode_four_worker_probe as worker_probe  # noqa: E402

BASE_URL = 'http://127.0.0.1:8080'
ADMIN_USER_ID = 'b6826286-1251-4576-b3a0-e109ff085a61'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

DESIRED_PROFILES: dict[str, dict[str, Any]] = {
    'chat': {
        'schema_version': 1,
        'system_prompt': '',
        'defaults': {
            'terminal_id': None,
            'tool_ids': [],
            'skill_ids': [],
            'filter_ids': 'inherit',
            'feature_ids': 'inherit',
        },
    },
    'agent': {
        'schema_version': 1,
        'system_prompt': '',
        'defaults': {
            'terminal_id': 'terminals',
            'tool_ids': ['sub_agent'],
            'skill_ids': [],
            'filter_ids': 'inherit',
            'feature_ids': 'inherit',
        },
    },
}


def request_json(
    token: str,
    path: str,
    *,
    method: str = 'GET',
    body: Any = None,
    timeout: float = 30,
) -> Any:
    data = None if body is None else json.dumps(body).encode()
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
    try:
        with OPENER.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail: Any = None
        else:
            detail = parsed.get('detail') if isinstance(parsed, dict) else None
        if isinstance(detail, dict):
            summary = {key: detail[key] for key in ('code', 'message', 'field', 'reason') if key in detail}
        elif isinstance(detail, str):
            summary = detail[:300]
        else:
            summary = None
        raise RuntimeError(f'{method} {path} returned HTTP {exc.code} detail={summary}') from exc


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ('data', 'items', 'models'):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def choose_model(payload: Any) -> dict[str, Any]:
    models = rows(payload)
    by_id = {item.get('id'): item for item in models if isinstance(item.get('id'), str)}
    for model_id in (
        'bifrostapi.Cliproxy/gpt-5.5',
        'bifrostapi.lucen/gpt-5.5',
        'openai/gpt-5.5',
        'gpt-5.5',
    ):
        if model_id in by_id:
            return by_id[model_id]
    for model_id, item in by_id.items():
        if model_id.endswith('/gpt-5.5') or model_id.endswith('gpt-5.5'):
            return item
    raise RuntimeError('gpt-5.5 is absent from the current model catalog')


def ids(payload: Any) -> set[str]:
    return {item['id'] for item in rows(payload) if isinstance(item.get('id'), str)}


def apply_profiles(token: str) -> dict[str, str]:
    resources = {
        'terminals': ids(request_json(token, '/api/v1/terminals/')),
        'tools': ids(request_json(token, '/api/v1/tools/')),
        'skills': ids(request_json(token, '/api/v1/skills/')),
    }
    required = {
        'terminals': {'terminals'},
        'tools': {'sub_agent'},
        'skills': set(),
    }
    missing = {
        kind: sorted(expected - resources[kind]) for kind, expected in required.items() if expected - resources[kind]
    }
    if missing:
        raise RuntimeError(f'profile resources missing: {missing}')

    revisions: dict[str, str] = {}
    for mode in ('agent', 'chat'):
        current = request_json(token, f'/api/v1/configs/conversation_mode_profiles/{mode}')
        current_revision = current.get('revision_id')
        if not isinstance(current_revision, str):
            raise RuntimeError(f'{mode} current revision missing')
        if (
            current.get('schema_version') == DESIRED_PROFILES[mode]['schema_version']
            and current.get('system_prompt') == DESIRED_PROFILES[mode]['system_prompt']
            and current.get('defaults') == DESIRED_PROFILES[mode]['defaults']
        ):
            revisions[mode] = current_revision
            continue
        saved = request_json(
            token,
            f'/api/v1/configs/conversation_mode_profiles/{mode}/revisions',
            method='POST',
            body={
                'expected_current_revision_id': current_revision,
                'profile': DESIRED_PROFILES[mode],
            },
        )
        saved_revision = saved.get('revision_id')
        if not isinstance(saved_revision, str):
            raise RuntimeError(f'{mode} saved revision missing')
        revisions[mode] = saved_revision
    return revisions


def public_profiles(payload: Any) -> dict[str, dict[str, Any]]:
    profiles = payload.get('conversation_mode_profiles') if isinstance(payload, dict) else None
    if isinstance(profiles, dict):
        return {str(mode): item for mode, item in profiles.items() if isinstance(item, dict)}
    if isinstance(profiles, list):
        return {item['mode']: item for item in profiles if isinstance(item, dict) and isinstance(item.get('mode'), str)}
    raise RuntimeError('public profile projection missing')


def prove_workers(token: str, expected_revisions: dict[str, str], model_id: str) -> dict[str, Any]:
    pids = worker_probe.worker_pids()
    if len(pids) != 4:
        raise RuntimeError(f'expected four container worker PIDs, got {pids}')
    retained, pinned = worker_probe.pin_sessions(token, pids)
    observations: dict[str, Any] = {}
    try:
        for pid, session in pinned.items():
            models = worker_probe.expect(session, 'GET', '/api/models?refresh=true')
            model_rows = models.get('data') if isinstance(models, dict) else None
            model_ids = {
                item.get('id')
                for item in model_rows or []
                if isinstance(item, dict) and isinstance(item.get('id'), str)
            }
            if model_id not in model_ids:
                raise RuntimeError(f'model {model_id} missing on worker {pid}')
            config = worker_probe.expect(session, 'GET', '/api/config')
            public = public_profiles(config)
            mode_observations: dict[str, Any] = {}
            for mode in ('chat', 'agent'):
                private = worker_probe.expect(
                    session,
                    'GET',
                    f'/api/v1/configs/conversation_mode_profiles/{mode}',
                )
                public_mode = public[mode]
                private_revision = private.get('revision_id')
                public_revision = public_mode.get('current_revision_id') or public_mode.get('revision_id')
                if private_revision != expected_revisions[mode] or public_revision != expected_revisions[mode]:
                    raise RuntimeError(f'{mode} revision mismatch on worker {pid}')
                if private.get('defaults') != DESIRED_PROFILES[mode]['defaults']:
                    raise RuntimeError(f'{mode} private defaults mismatch on worker {pid}')
                expected_public_defaults = {
                    key: value for key, value in DESIRED_PROFILES[mode]['defaults'].items() if value != 'inherit'
                }
                if public_mode.get('defaults') != expected_public_defaults:
                    raise RuntimeError(f'{mode} public defaults mismatch on worker {pid}')
                if 'system_prompt' in json.dumps(public_mode).lower():
                    raise RuntimeError(f'{mode} public prompt exposure on worker {pid}')
                mode_observations[mode] = {
                    'revision_id': private_revision,
                    'defaults_match': True,
                    'public_prompt_exposed': False,
                }
            observations[str(pid)] = {
                'session_port': session.local_port,
                'model_present': True,
                'modes': mode_observations,
            }
    finally:
        for session in retained:
            session.close()
    return {'container_worker_pids': pids, 'observations': observations}


def request_payload(
    *,
    mode: str,
    revision_id: str,
    model: dict[str, Any],
    prompt: str,
    chat_id: str,
) -> dict[str, Any]:
    user_id = str(uuid.uuid4())
    assistant_id = str(uuid.uuid4())
    model_id = model['id']
    user_message = {
        'id': user_id,
        'parentId': None,
        'childrenIds': [assistant_id],
        'role': 'user',
        'content': prompt,
        'timestamp': int(time.time()),
        'models': [model_id],
    }
    return {
        'stream': True,
        'model': model_id,
        'chat_mode': mode,
        'mode_profile_revision_id': revision_id,
        'messages': [{'role': 'user', 'content': prompt}],
        'params': {},
        'model_item': model,
        'chat_id': chat_id,
        'id': assistant_id,
        'parent_id': None,
        'user_message': user_message,
        'background_tasks': {},
        'features': {},
        'variables': {},
    }


def openai_content_deltas(data: str) -> list[str]:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    choices = parsed.get('choices') if isinstance(parsed, dict) else None
    if not isinstance(choices, list):
        return []
    result = []
    for choice in choices:
        delta = choice.get('delta') if isinstance(choice, dict) else None
        if isinstance(delta, dict) and isinstance(delta.get('content'), str):
            result.append(delta['content'])
    return result


def chat_smoke(
    token: str,
    model: dict[str, Any],
    revision_id: str,
    suffix: str,
) -> dict[str, Any]:
    marker = f'CHAT-STACK-OK-{suffix}'
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
    started = time.monotonic()
    data_lines = 0
    content_deltas: list[str] = []
    done = False
    try:
        with OPENER.open(request, timeout=150) as response:
            if response.status != 200:
                raise RuntimeError(f'Chat smoke returned HTTP {response.status}')
            for raw_line in response:
                line = raw_line.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    done = True
                    break
                data_lines += 1
                content_deltas.extend(openai_content_deltas(data))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Chat smoke returned HTTP {exc.code}') from exc
    marker_seen = marker in ''.join(content_deltas)
    if not done or not marker_seen or len(content_deltas) < 2:
        raise RuntimeError(
            f'Chat SSE incomplete: done={done} marker={marker_seen} content_deltas={len(content_deltas)}'
        )
    return {
        'done': done,
        'marker_seen': marker_seen,
        'data_lines': data_lines,
        'content_delta_count': len(content_deltas),
        'profile_revision_validated_separately': revision_id,
        'duration_seconds': round(time.monotonic() - started, 3),
    }


def agent_smoke(
    token: str,
    model: dict[str, Any],
    revision_id: str,
    suffix: str,
) -> dict[str, Any]:
    marker = f'AGENT-STACK-OK-{suffix}'
    chat_id = f'local:pr7-latest-agent-{suffix}'
    payload = request_payload(
        mode='agent',
        revision_id=revision_id,
        model=model,
        prompt=f'Do not use tools. Return the final answer exactly as {marker}.',
        chat_id=chat_id,
    )
    started = time.monotonic()
    created = request_json(
        token,
        '/api/chat/completions',
        method='POST',
        body=payload,
        timeout=60,
    )
    if created.get('status') is not True or not isinstance(created.get('agent_run_id'), str):
        raise RuntimeError('Agent smoke did not start a native run')
    run_id = created['agent_run_id']
    detail: dict[str, Any] = {}
    events_payload: dict[str, Any] = {}
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        detail = request_json(token, f'/api/agent/runs/{run_id}')
        events_payload = request_json(token, f'/api/agent/runs/{run_id}/events/list')
        state = detail.get('state')
        if state in {'completed', 'failed', 'cancelled', 'budget_exceeded'}:
            break
        if state in {'waiting_approval', 'waiting_user_input'}:
            raise RuntimeError(f'Agent smoke unexpectedly paused in {state}')
        time.sleep(0.5)
    if detail.get('state') != 'completed':
        raise RuntimeError(f'Agent smoke ended in {detail.get("state")}')
    events = events_payload.get('events') if isinstance(events_payload, dict) else None
    if not isinstance(events, list):
        raise RuntimeError('Agent event list missing')
    event_types = [item.get('event_type') for item in events if isinstance(item, dict)]
    counts = Counter(item for item in event_types if isinstance(item, str))
    final_text = ''.join(
        str((item.get('payload') or {}).get('delta') or '')
        for item in events
        if isinstance(item, dict) and item.get('event_type') == 'final.delta'
    )
    required = {'run.running', 'final.started', 'final.delta', 'run.completed'}
    if not required.issubset(counts):
        raise RuntimeError(f'Agent event sequence missing {sorted(required - set(counts))}')
    if marker not in final_text:
        raise RuntimeError('Agent final marker missing')
    return {
        'chat_id': chat_id,
        'run_id': run_id,
        'state': detail['state'],
        'event_counts': dict(sorted(counts.items())),
        'final_delta_count': counts['final.delta'],
        'commentary_delta_count': counts.get('text.delta', 0),
        'marker_seen': True,
        'duration_seconds': round(time.monotonic() - started, 3),
    }


def main() -> int:
    token = create_token({'id': ADMIN_USER_ID}, expires_delta=dt.timedelta(hours=1))
    model = choose_model(request_json(token, '/api/models'))
    revisions = apply_profiles(token)
    worker_evidence = prove_workers(token, revisions, model['id'])
    suffix = uuid.uuid4().hex[:10]
    chat = chat_smoke(token, model, revisions['chat'], suffix)
    agent = agent_smoke(token, model, revisions['agent'], suffix)
    result = {
        'ok': True,
        'model_id': model['id'],
        'environment_differences': {
            'planned_agent_skill_available': False,
            'agent_default_skill_ids': [],
            'excluded_default_tool': 'web_search_and_crawl',
            'excluded_tool_reason': 'crawl4ai is absent while OFFLINE_MODE=true',
        },
        'profile_revisions': revisions,
        'worker_evidence': worker_evidence,
        'chat': chat,
        'agent': agent,
    }
    with open('/tmp/pr7-latest-stack-acceptance.json', 'w', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write('\n')
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
