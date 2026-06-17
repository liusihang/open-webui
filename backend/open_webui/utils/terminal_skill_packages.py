from __future__ import annotations

import asyncio
import json
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import aiohttp
from fastapi import Request
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER
from open_webui.models.groups import Groups
from open_webui.storage.provider import Storage
from open_webui.utils.access_control import has_connection_access
from open_webui.utils.skill_packages import normalize_package_files, validate_package_file_path

TERMINAL_SKILL_RUNTIME_ROOT = '/home/user/.openwebui/skills'
TERMINAL_SKILL_RUNTIME_MARKER = '.openwebui-skill.json'
TERMINAL_SKILL_DERIVED_SOURCE_PREFIX = f'{TERMINAL_SKILL_RUNTIME_ROOT}/'


class TerminalSkillPackageError(ValueError):
    pass


@dataclass
class TerminalSkillSyncResult:
    path: str
    entrypoints: list[dict[str, str]]

    def model_dump(self) -> dict[str, Any]:
        return {'path': self.path, 'entrypoints': self.entrypoints}


@dataclass
class _TerminalFileClient:
    base_url: str
    headers: dict[str, str]
    cookies: dict[str, str]

    async def list_files(self, directory: str) -> dict[str, Any]:
        return await self._json_request('get', '/files/list', query={'directory': directory})

    async def read_file(self, path: str) -> dict[str, Any]:
        return await self._json_request('get', '/files/read', query={'path': path})

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        return await self._json_request(
            'post',
            '/files/write',
            body={'path': path, 'content': content, 'overwrite': True},
        )

    async def _json_request(
        self,
        method: str,
        endpoint: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f'{self.base_url}{endpoint}'
        if query:
            url = f'{url}?{urlencode(query)}'

        timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            request_method = getattr(session, method)
            async with request_method(
                url,
                json=body,
                headers=self.headers,
                cookies=self.cookies,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise TerminalSkillPackageError(f'Terminal file API failed: HTTP {response.status}: {text}')
                try:
                    payload = await response.json()
                except Exception as exc:
                    raise TerminalSkillPackageError('Terminal file API returned non-JSON response') from exc
                if not isinstance(payload, dict):
                    raise TerminalSkillPackageError('Terminal file API returned invalid JSON payload')
                return payload


async def read_skill_package_source_from_terminal(
    request: Request,
    terminal_id: str,
    user: Any,
    source_path: str,
    *,
    metadata: dict[str, Any] | None = None,
    oauth_token: dict[str, Any] | None = None,
) -> dict[str, str]:
    source_path = _validate_terminal_source_path(source_path)
    client = await _get_terminal_file_client(request, terminal_id, user, metadata=metadata, oauth_token=oauth_token)
    files: dict[str, str] = {}
    await _collect_terminal_source_files(client, source_path, '', files)
    return files


async def ensure_skill_synced_to_terminal(
    request: Request,
    terminal_id: str,
    user: Any,
    skill: Any,
    package: Any,
    *,
    metadata: dict[str, Any] | None = None,
    oauth_token: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = await _get_terminal_file_client(request, terminal_id, user, metadata=metadata, oauth_token=oauth_token)
    runtime_dir = _terminal_skill_runtime_dir(skill.id, package.bundle_hash)
    files = await _read_storage_package_files(package.storage_path)

    for path, content in files.items():
        await client.write_file(_join_terminal_path(runtime_dir, path), content.decode('utf-8'))

    marker = {
        'schema_version': 1,
        'skill_id': skill.id,
        'bundle_hash': package.bundle_hash,
        'manifest': package.manifest,
    }
    await client.write_file(
        _join_terminal_path(runtime_dir, TERMINAL_SKILL_RUNTIME_MARKER),
        json.dumps(marker, ensure_ascii=False, sort_keys=True) + '\n',
    )

    entrypoints = []
    for entrypoint in (package.manifest or {}).get('entrypoints', []):
        if not isinstance(entrypoint, dict):
            continue
        path = entrypoint.get('path')
        if not isinstance(path, str):
            continue
        entrypoints.append(
            {
                'name': str(entrypoint.get('name') or ''),
                'path': _join_terminal_path(runtime_dir, path),
                'runtime': str(entrypoint.get('runtime') or ''),
            }
        )

    return TerminalSkillSyncResult(path=runtime_dir, entrypoints=entrypoints).model_dump()


async def _collect_terminal_source_files(
    client: _TerminalFileClient,
    current_path: str,
    relative_prefix: str,
    files: dict[str, str],
) -> None:
    listing = await client.list_files(current_path)
    entries = listing.get('entries')
    if not isinstance(entries, list):
        raise TerminalSkillPackageError('Terminal list_files returned invalid entries')

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get('name')
        kind = entry.get('type')
        if not isinstance(name, str) or not name:
            continue
        if '/' in name or '\\' in name or name in {'.', '..'}:
            raise TerminalSkillPackageError(f'Unsafe terminal directory entry: {name}')

        child_path = _join_terminal_path(current_path, name)
        relative_path = _join_terminal_path(relative_prefix, name) if relative_prefix else name
        if kind == 'directory':
            await _collect_terminal_source_files(client, child_path, relative_path, files)
        elif kind == 'file':
            payload = await client.read_file(child_path)
            content = payload.get('content')
            if not isinstance(content, str):
                raise TerminalSkillPackageError(f'Terminal source file is not readable as text: {relative_path}')
            files[validate_package_file_path(relative_path)] = content


async def _get_terminal_file_client(
    request: Request,
    terminal_id: str,
    user: Any,
    *,
    metadata: dict[str, Any] | None = None,
    oauth_token: dict[str, Any] | None = None,
) -> _TerminalFileClient:
    if not terminal_id:
        raise TerminalSkillPackageError('Terminal context is required')

    user_ref = _terminal_user_ref(user)
    connection = await _get_accessible_terminal_connection(request, terminal_id, user_ref)
    base_url = _terminal_base_url(connection)
    headers, cookies = _terminal_headers_and_cookies(
        request,
        connection,
        user_ref,
        metadata=metadata,
        oauth_token=oauth_token,
    )

    return _TerminalFileClient(base_url=base_url, headers=headers, cookies=cookies)


async def _get_accessible_terminal_connection(
    request: Request,
    terminal_id: str,
    user_ref: SimpleNamespace,
) -> dict[str, Any]:
    connections = getattr(request.app.state.config, 'TERMINAL_SERVER_CONNECTIONS', None) or []
    connection = next((item for item in connections if item.get('id') == terminal_id), None)
    if connection is None or not connection.get('enabled', True):
        raise TerminalSkillPackageError(f"Terminal server '{terminal_id}' not found")

    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user_ref.id)}
    if not await has_connection_access(user_ref, connection, user_group_ids):
        raise TerminalSkillPackageError('Access denied to terminal')

    return connection


def _terminal_base_url(connection: dict[str, Any]) -> str:
    base_url = (connection.get('url') or '').rstrip('/')
    if not base_url:
        raise TerminalSkillPackageError('Terminal server URL is not configured')
    policy_id = connection.get('policy_id')
    if policy_id:
        base_url = f'{base_url}/p/{policy_id}'
    return base_url


def _terminal_headers_and_cookies(
    request: Request,
    connection: dict[str, Any],
    user_ref: SimpleNamespace,
    *,
    metadata: dict[str, Any] | None = None,
    oauth_token: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    headers = {'X-User-Id': user_ref.id}
    session_id = metadata.get('chat_id') if isinstance(metadata, dict) else None
    if session_id:
        headers['X-Session-Id'] = str(session_id)

    cookies = {}
    auth_type = connection.get('auth_type', 'bearer')
    if auth_type == 'bearer':
        headers['Authorization'] = f'Bearer {connection.get("key", "")}'
    elif auth_type == 'session':
        cookies = getattr(request, 'cookies', {}) or {}
        token = getattr(getattr(request, 'state', None), 'token', None)
        credentials = getattr(token, 'credentials', None)
        if not credentials:
            raise TerminalSkillPackageError('Missing session token for terminal access')
        headers['Authorization'] = f'Bearer {credentials}'
    elif auth_type == 'system_oauth':
        cookies = getattr(request, 'cookies', {}) or {}
        token = (oauth_token or {}).get('access_token')
        if not token:
            raise TerminalSkillPackageError('Missing OAuth token for terminal access')
        headers['Authorization'] = f'Bearer {token}'
    elif auth_type == 'none':
        pass
    else:
        raise TerminalSkillPackageError(f"Terminal auth_type '{auth_type}' is not supported for skill packages")

    return headers, cookies


async def _read_storage_package_files(storage_path: str) -> dict[str, bytes]:
    local_path = await asyncio.to_thread(Storage.get_file, storage_path)

    def _read_zip() -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        with zipfile.ZipFile(local_path, 'r') as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                files[validate_package_file_path(info.filename)] = archive.read(info)
        return normalize_package_files(files)

    return await asyncio.to_thread(_read_zip)


def _validate_terminal_source_path(source_path: str) -> str:
    if not isinstance(source_path, str) or not source_path.strip():
        raise TerminalSkillPackageError('source_path must be a non-empty terminal directory path')
    if source_path != source_path.strip():
        raise TerminalSkillPackageError('source_path must not include leading or trailing whitespace')
    if source_path.startswith('//'):
        raise TerminalSkillPackageError('source_path must not use double-slash absolute paths')

    pure_path = PurePosixPath(source_path)
    if not pure_path.is_absolute():
        raise TerminalSkillPackageError('source_path must be an absolute terminal directory path')
    if any(part in {'..', ''} for part in pure_path.parts):
        raise TerminalSkillPackageError('source_path must not contain traversal')

    normalized = str(pure_path)
    if normalized == TERMINAL_SKILL_RUNTIME_ROOT or normalized.startswith(TERMINAL_SKILL_DERIVED_SOURCE_PREFIX):
        raise TerminalSkillPackageError('runtime skill cache paths cannot be used as install sources')

    return normalized


def _terminal_skill_runtime_dir(skill_id: str, bundle_hash: str) -> str:
    safe_skill_id = _safe_terminal_path_segment(skill_id, field='skill id')
    safe_bundle_hash = _safe_terminal_path_segment(bundle_hash, field='bundle hash')
    return f'{TERMINAL_SKILL_RUNTIME_ROOT}/{safe_skill_id}/{safe_bundle_hash}'


def _safe_terminal_path_segment(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TerminalSkillPackageError(f'{field} must be a non-empty path segment')
    if value in {'.', '..'} or '/' in value or '\\' in value or '\x00' in value:
        raise TerminalSkillPackageError(f'{field} is not safe for terminal runtime paths')
    return value


def _join_terminal_path(base: str, child: str) -> str:
    if not base:
        return child
    return posixpath.join(base.rstrip('/'), child)


def _terminal_user_ref(user: Any) -> SimpleNamespace:
    if isinstance(user, dict):
        return SimpleNamespace(id=user.get('id'), role=user.get('role', 'user'))
    return SimpleNamespace(id=getattr(user, 'id', None), role=getattr(user, 'role', 'user'))
