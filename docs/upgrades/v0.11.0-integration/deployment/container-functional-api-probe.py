#!/usr/bin/env python3
"""Read-only authenticated API coverage for the isolated v0.11 test stack."""

from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from typing import Any

from open_webui.utils.auth import create_token


BASE_URL = 'http://127.0.0.1:8080'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FORBIDDEN_TOOL_IDS = {'list_chat_files', 'grep_chat_files', 'query_chat_files'}
EXPECTED_PROFILES = {
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


def request_raw(token: str, path: str) -> tuple[int, str, bytes]:
    req = urllib.request.Request(
        f'{BASE_URL}{path}',
        headers={
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
        },
    )
    try:
        with OPENER.open(req, timeout=45) as response:
            status = response.status
            content_type = response.headers.get('content-type', '')
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        content_type = exc.headers.get('content-type', '')
        raw = exc.read()
    return status, content_type, raw


def request(token: str, path: str, *, expected_status: int = 200) -> Any:
    status, _content_type, raw = request_raw(token, path)
    if status != expected_status:
        raise RuntimeError(f'GET {path} returned HTTP {status}, expected {expected_status}')
    if expected_status == 204 or not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'GET {path} did not return JSON') from exc


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ('items', 'data', 'models'):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def item_ids(payload: Any) -> set[str]:
    return {
        item['id']
        for item in rows(payload)
        if isinstance(item.get('id'), str)
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit('usage: container-functional-api-probe.py ADMIN_USER_ID')
    token = create_token({'id': sys.argv[1]}, expires_delta=dt.timedelta(minutes=15))

    payloads = {
        'provider_models': request(token, '/api/models'),
        'chats': request(token, '/api/v1/chats/?page=1&include_pinned=true&include_folders=true'),
        'channels': request(token, '/api/v1/channels/'),
        'notes': request(token, '/api/v1/notes/'),
        'workspace_models': request(token, '/api/v1/models/list?page=1'),
        'knowledge': request(token, '/api/v1/knowledge/?page=1'),
        'knowledge_index': request(token, '/api/v1/knowledge/index/status'),
        'prompts': request(token, '/api/v1/prompts/'),
        'tools': request(token, '/api/v1/tools/'),
        'functions': request(token, '/api/v1/functions/'),
        'skills': request(token, '/api/v1/skills/'),
        'terminals': request(token, '/api/v1/terminals/'),
    }
    profiles = {
        mode: request(token, f'/api/v1/configs/conversation_mode_profiles/{mode}')
        for mode in ('chat', 'agent')
    }
    subagents_status, subagents_content_type, _subagents_raw = request_raw(
        token,
        '/api/v1/configs/subagents',
    )
    if subagents_status != 200 or not subagents_content_type.startswith('text/html'):
        raise RuntimeError(
            'official Sub-agents config URL did not resolve through the expected SPA fallback'
        )

    terminal_ids = item_ids(payloads['terminals'])
    tool_ids = item_ids(payloads['tools'])
    if 'terminals' not in terminal_ids:
        raise RuntimeError('custom terminal resource is missing')
    if 'sub_agent' not in tool_ids:
        raise RuntimeError('custom AgentScope sub_agent tool is missing')
    forbidden_present = sorted(FORBIDDEN_TOOL_IDS & tool_ids)
    if forbidden_present:
        raise RuntimeError(f'forbidden official chat-file tools are present: {forbidden_present}')

    provider_model_ids = item_ids(payloads['provider_models'])
    if not provider_model_ids:
        raise RuntimeError('provider model catalog is empty')
    if not any(model_id.endswith('gpt-5.5') for model_id in provider_model_ids):
        raise RuntimeError('gpt-5.5 is missing from provider model catalog')

    profile_revisions: dict[str, str] = {}
    for mode, expected in EXPECTED_PROFILES.items():
        profile = profiles[mode]
        for key, value in expected.items():
            if profile.get(key) != value:
                raise RuntimeError(f'{mode} profile {key} differs from the accepted runtime profile')
        revision_id = profile.get('revision_id')
        if not isinstance(revision_id, str) or not revision_id:
            raise RuntimeError(f'{mode} profile revision is missing')
        profile_revisions[mode] = revision_id

    result = {
        'ok': True,
        'counts': {name: len(rows(payload)) for name, payload in payloads.items()},
        'provider_model_count': len(provider_model_ids),
        'required_resources': {
            'terminal': 'terminals',
            'tool': 'sub_agent',
        },
        'forbidden_chat_file_tools_present': forbidden_present,
        'official_subagents_config_probe': {
            'status': subagents_status,
            'content_type': subagents_content_type,
            'spa_fallback': True,
        },
        'profile_revisions': profile_revisions,
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
