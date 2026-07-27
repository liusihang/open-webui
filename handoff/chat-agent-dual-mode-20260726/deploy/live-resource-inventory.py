#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get('OPENWEBUI_BASE_URL', 'http://127.0.0.1').rstrip('/')
TOKEN = os.environ.get('OPENWEBUI_TOKEN', '').strip()
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request_json(path: str) -> Any:
    if not TOKEN:
        raise RuntimeError('OPENWEBUI_TOKEN is required')
    request = urllib.request.Request(
        f'{BASE_URL}{path}',
        headers={
            'Accept': 'application/json',
            'Authorization': f'Bearer {TOKEN}',
        },
    )
    try:
        with OPENER.open(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{path} returned HTTP {exc.code}') from exc


def item_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ('items', 'data', 'models'):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def sanitized_items(payload: Any) -> list[dict[str, Any]]:
    allowed = ('id', 'name', 'type', 'is_active', 'owned_by')
    result = []
    for item in item_list(payload):
        sanitized = {key: item[key] for key in allowed if key in item}
        if isinstance(sanitized.get('id'), str):
            result.append(sanitized)
    return sorted(result, key=lambda item: str(item.get('id', '')))


def sanitized_profiles(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    profiles = payload.get('profiles')
    if not isinstance(profiles, list):
        return []
    result = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        result.append(
            {
                key: profile[key]
                for key in ('mode', 'revision_id', 'revision_number', 'schema_version', 'defaults')
                if key in profile
            }
        )
    return result


def selected_ids(template: dict[str, Any], mode: str, field: str) -> list[str]:
    value = template.get(mode, {}).get('profile', {}).get('defaults', {}).get(field)
    if isinstance(value, str):
        return [] if value == 'inherit' else [value]
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    raise RuntimeError(f'{mode}.{field} has an invalid type')


def main() -> int:
    template_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    resources = {
        'terminals': sanitized_items(request_json('/api/v1/terminals/')),
        'tools': sanitized_items(request_json('/api/v1/tools/')),
        'skills': sanitized_items(request_json('/api/v1/skills/')),
        'functions': sanitized_items(request_json('/api/v1/functions/')),
        'models': sanitized_items(request_json('/api/models')),
    }
    output: dict[str, Any] = {'base_url': BASE_URL, 'resources': resources}

    try:
        profiles = request_json('/api/v1/configs/conversation_mode_profiles')
    except RuntimeError as exc:
        output['profiles_status'] = str(exc)
    else:
        output['profiles'] = sanitized_profiles(profiles)

    if template_path is not None:
        template = json.loads(template_path.read_text(encoding='utf-8'))
        mappings = {
            'terminal_id': 'terminals',
            'tool_ids': 'tools',
            'skill_ids': 'skills',
            'filter_ids': 'functions',
        }
        missing = []
        placeholders = []
        for mode in ('chat', 'agent'):
            for field, resource_type in mappings.items():
                available = {item['id'] for item in resources[resource_type]}
                for resource_id in selected_ids(template, mode, field):
                    if resource_id.startswith('__'):
                        placeholders.append(f'{mode}.{field}:{resource_id}')
                    elif resource_id not in available:
                        missing.append(f'{mode}.{field}:{resource_id}')
        output['template_validation'] = {
            'missing': missing,
            'placeholders': placeholders,
            'valid': not missing and not placeholders,
        }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write('\n')
    validation = output.get('template_validation')
    return 0 if not validation or validation['valid'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
