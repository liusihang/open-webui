import datetime as dt
import hashlib
import ipaddress
import json
import logging
import posixpath
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import quote, unquote, urlencode, urlparse
from uuid import uuid4

import aiohttp
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.config import Config
from open_webui.models.files import Files
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.storage.provider import Storage
from open_webui.utils.access_control import has_connection_access
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.auth import create_token, decode_token, get_verified_user
from open_webui.utils.misc import parse_duration
from open_webui.utils.terminals import (
    TERMINAL_CONTEXT_HEADER,
    get_terminal_server_url,
    terminal_context_available,
    terminal_context_config,
    terminal_context_id,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

router = APIRouter()

SUPPORTED_OFFICE_FILE_TYPES = {
    'doc': 'word',
    'docm': 'word',
    'docx': 'word',
    'dot': 'word',
    'dotm': 'word',
    'dotx': 'word',
    'odt': 'word',
    'ott': 'word',
    'rtf': 'word',
    'pdf': 'pdf',
    'xls': 'cell',
    'xlsb': 'cell',
    'xlsm': 'cell',
    'xlsx': 'cell',
    'xlt': 'cell',
    'xltm': 'cell',
    'xltx': 'cell',
    'csv': 'cell',
    'ods': 'cell',
    'ots': 'cell',
    'odp': 'slide',
    'otp': 'slide',
    'pot': 'slide',
    'potm': 'slide',
    'potx': 'slide',
    'pps': 'slide',
    'ppsm': 'slide',
    'ppsx': 'slide',
    'ppt': 'slide',
    'pptm': 'slide',
    'pptx': 'slide',
}
ONLYOFFICE_SAVE_STATUSES = {2, 6}
DEFAULT_ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN = '8h'
ONLYOFFICE_EDIT_CALLBACK_MAX_BYTES = 100 * 1024 * 1024
ONLYOFFICE_CALLBACK_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class OnlyOfficeSessionForm(BaseModel):
    source_type: Literal['file', 'terminal'] = 'file'
    file_id: str | None = None
    terminal_server_id: str | None = None
    terminal_file_path: str | None = None
    mode: Literal['view', 'edit'] = 'view'
    chat_id: str | None = None


class OnlyOfficeCallbackForm(BaseModel):
    status: int | None = None
    key: str | None = None
    url: str | None = None
    token: str | None = None
    context_token: str | None = None


def _get_display_name(file) -> str:
    return (file.meta or {}).get('name') or file.filename or file.id


def _get_file_ext(file) -> str:
    display_name = _get_display_name(file)
    return Path(display_name).suffix.lower().lstrip('.')


def _get_document_key(file) -> str:
    key_source = f'{file.id}:{file.hash or ""}:{file.updated_at or ""}'
    return hashlib.sha256(key_source.encode('utf-8')).hexdigest()[:48]


def _get_terminal_document_key(terminal_server_id: str, terminal_file_path: str, session_signal: str) -> str:
    key_source = f'terminal:{terminal_server_id}:{terminal_file_path}:{session_signal}'
    return hashlib.sha256(key_source.encode('utf-8')).hexdigest()[:48]


async def _require_onlyoffice_enabled(request: Request) -> str:
    # Keep the old public/env key names, but use v0.12's per-key async config API.
    keys = {
        'ENABLE_ONLYOFFICE_PREVIEW': 'onlyoffice.enable_preview',
        'ONLYOFFICE_DOCUMENT_SERVER_URL': 'onlyoffice.document_server_url',
        'ONLYOFFICE_PUBLIC_BASE_URL': 'onlyoffice.public_base_url',
        'ONLYOFFICE_JWT_SECRET': 'onlyoffice.jwt_secret',
        'ONLYOFFICE_FILE_TOKEN_EXPIRES_IN': 'onlyoffice.file_token_expires_in',
        'ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN': 'onlyoffice.edit_callback_token_expires_in',
        'ONLYOFFICE_CALLBACK_ALLOWED_HOSTS': 'onlyoffice.callback_allowed_hosts',
        'WEBUI_URL': 'webui.url',
    }
    values = await Config.get_many(*keys.values())
    request.state.onlyoffice_config = SimpleNamespace(**{name: values.get(key) for name, key in keys.items()})
    if not bool(getattr(request.state.onlyoffice_config, 'ENABLE_ONLYOFFICE_PREVIEW', False)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='OnlyOffice preview is disabled.',
        )

    document_server_url = (getattr(request.state.onlyoffice_config, 'ONLYOFFICE_DOCUMENT_SERVER_URL', '') or '').strip()
    if not document_server_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='OnlyOffice document server URL is not configured.',
        )
    return document_server_url.rstrip('/')


async def _check_read_access(file_id: str, file, user, db: AsyncSession) -> None:
    if user.role not in ('admin', 'user'):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    if not (file.user_id == user.id or user.role == 'admin' or await has_access_to_file(file_id, 'read', user, db=db)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


async def _get_file_or_404(file_id: str, db: AsyncSession):
    file = await Files.get_file_by_id(file_id, db=db)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    return file


def _resolve_public_base_url(request: Request) -> str:
    configured_webui_url = (getattr(request.state.onlyoffice_config, 'WEBUI_URL', '') or '').strip().rstrip('/')
    if configured_webui_url:
        return configured_webui_url
    return str(request.base_url).rstrip('/')


def _resolve_onlyoffice_public_base_url(request: Request) -> str:
    configured_onlyoffice_base_url = (
        (getattr(request.state.onlyoffice_config, 'ONLYOFFICE_PUBLIC_BASE_URL', '') or '').strip().rstrip('/')
    )
    if configured_onlyoffice_base_url:
        return configured_onlyoffice_base_url
    return _resolve_public_base_url(request)


def _parse_file_token_ttl(request: Request) -> dt.timedelta | None:
    raw = getattr(request.state.onlyoffice_config, 'ONLYOFFICE_FILE_TOKEN_EXPIRES_IN', '')
    try:
        ttl = parse_duration(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'Invalid ONLYOFFICE_FILE_TOKEN_EXPIRES_IN value: {raw}',
        ) from exc

    if ttl is None or ttl.total_seconds() <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='ONLYOFFICE_FILE_TOKEN_EXPIRES_IN must be a finite duration.',
        )

    return ttl


def _parse_edit_callback_token_ttl(request: Request, file_token_ttl: dt.timedelta) -> dt.timedelta:
    raw = (getattr(request.state.onlyoffice_config, 'ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN', None) or '').strip()

    if not raw:
        default_ttl = parse_duration(DEFAULT_ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN)
        if default_ttl is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN must be a finite duration.',
            )
        return max(default_ttl, file_token_ttl)

    try:
        ttl = parse_duration(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'Invalid ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN value: {raw}',
        ) from exc

    if ttl is None or ttl.total_seconds() <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN must be a finite duration.',
        )

    return ttl


def _normalize_terminal_file_path(file_path: str) -> str:
    decoded = unquote(file_path or '')
    if not decoded or '\x00' in decoded or '\\' in decoded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid terminal file path.',
        )

    if '..' in decoded.split('/'):
        raise HTTPException(status_code=400, detail='Invalid terminal file path traversal.')
    normalized = posixpath.normpath(decoded)
    if normalized.startswith('..'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid terminal file path traversal.',
        )

    if decoded.startswith('/'):
        return normalized if normalized.startswith('/') else f'/{normalized}'
    return normalized


def _is_allowed_host(url: str, allowlist: list[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in ('http', 'https') or not host or parsed.username or parsed.password:
        return False

    normalized_allowlist = [entry.lower() for entry in allowlist if entry]
    if not normalized_allowlist:
        return False

    return any(host == entry or host.endswith(f'.{entry}') for entry in normalized_allowlist)


def _is_disallowed_callback_ip_host(host: str) -> bool:
    normalized_host = (host or '').strip().lower()
    if normalized_host == 'localhost' or normalized_host.endswith('.localhost'):
        return True

    try:
        ip = ipaddress.ip_address(normalized_host)
    except ValueError:
        return False

    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_unspecified


def _is_allowed_callback_blob_url(url: str, allowlist: list[str]) -> bool:
    if not _is_allowed_host(url, allowlist):
        return False

    host = (urlparse(url).hostname or '').lower()
    return not _is_disallowed_callback_ip_host(host)


def _extract_callback_token(request: Request, payload: dict[str, Any]) -> str:
    auth_header = request.headers.get('authorization', '')
    if auth_header.lower().startswith('bearer '):
        return auth_header[7:].strip()
    if isinstance(payload.get('token'), str):
        return payload['token']
    return ''


def _extract_callback_context_token(request: Request, payload: dict[str, Any]) -> str:
    query_params = getattr(request, 'query_params', {}) or {}
    query_token = query_params.get('context_token')
    if isinstance(query_token, str) and query_token:
        return query_token

    payload_token = payload.get('context_token')
    if isinstance(payload_token, str) and payload_token:
        return payload_token
    return ''


def _coerce_callback_status(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _decode_callback_token_without_verification(callback_token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            callback_token,
            options={'verify_signature': False, 'verify_exp': False},
            algorithms=['HS256', 'HS384', 'HS512', 'RS256', 'RS384', 'RS512'],
        )
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _parse_embedded_callback_payload(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get('payload')
    if isinstance(nested, dict):
        return nested
    if isinstance(nested, str):
        try:
            parsed = json.loads(nested)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _expand_callback_payload_without_jwt_secret(payload: dict[str, Any]) -> dict[str, Any]:
    expanded = dict(payload)

    embedded_payload = _parse_embedded_callback_payload(expanded)
    if embedded_payload:
        for field in ('status', 'key', 'url', 'context_token', 'token'):
            if expanded.get(field) is None and embedded_payload.get(field) is not None:
                expanded[field] = embedded_payload[field]

    status_value = _coerce_callback_status(expanded.get('status'))
    if status_value is not None:
        expanded['status'] = status_value
        return expanded

    callback_token = expanded.get('token')
    if not isinstance(callback_token, str) or not callback_token:
        return expanded

    decoded_payload = _decode_callback_token_without_verification(callback_token)
    if not decoded_payload:
        return expanded

    for field in ('status', 'key', 'url', 'context_token'):
        if expanded.get(field) is None and decoded_payload.get(field) is not None:
            expanded[field] = decoded_payload[field]

    status_value = _coerce_callback_status(expanded.get('status'))
    if status_value is not None:
        expanded['status'] = status_value
    return expanded


async def _get_terminal_connection(request: Request, terminal_server_id: str, user):
    connections = await Config.get('terminal_server.connections', []) or []
    connection = next((c for c in connections if c.get('id') == terminal_server_id), None)
    if user.role not in ('admin', 'user'):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    if connection is None or not connection.get('enabled', True):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Terminal server not found.',
        )

    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, include_inherited=True)}
    if not await has_connection_access(user, connection, user_group_ids):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    auth_type = connection.get('auth_type', 'bearer')
    if auth_type not in ('bearer', 'none', 'session'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Terminal auth_type '{auth_type}' is not supported for OnlyOffice (supports bearer/none/session).",
        )
    return connection


def _resolve_terminal_context(connection: dict, chat_id: str | None) -> str | None:
    if not terminal_context_available(connection, 'chat'):
        raise HTTPException(status_code=403, detail='Terminal is not available in chats.')
    context_id = terminal_context_id(connection, {'chat_id': chat_id}, 'chat')
    context_config = terminal_context_config(connection, 'chat')
    if isinstance(context_config, dict) and context_config.get('context_id') == 'chat_id' and not context_id:
        raise HTTPException(status_code=409, detail='A saved chat is required for this terminal.')
    return context_id


def _validate_session_context(connection: dict, claims: dict) -> str | None:
    context_id = _resolve_terminal_context(connection, claims.get('chat_id'))
    if context_id != claims.get('context_id'):
        raise HTTPException(status_code=403, detail='Terminal context has changed.')
    return context_id


def _get_terminal_target_base_url(connection: dict[str, Any]) -> str:
    base_url = get_terminal_server_url(connection)
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Terminal server URL is not configured.',
        )

    return base_url


def _get_terminal_proxy_headers(
    connection: dict[str, Any], user_id: str, session_proxy_token: str | None
) -> dict[str, str]:
    headers = {'X-User-Id': user_id}
    auth_type = connection.get('auth_type', 'bearer')
    if auth_type == 'bearer':
        headers['Authorization'] = f'Bearer {connection.get("key", "")}'
    elif auth_type == 'session':
        if not isinstance(session_proxy_token, str) or not session_proxy_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Missing session proxy token for terminal callback auth.',
            )
        headers['Authorization'] = f'Bearer {session_proxy_token}'
    elif auth_type not in ('none',):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Terminal auth_type '{auth_type}' is not supported for OnlyOffice callback writeback.",
        )
    return headers


async def _read_upstream_error_message(upstream) -> str:
    try:
        body = await upstream.read()
    except Exception:
        return ''

    if not body:
        return ''

    try:
        return body.decode('utf-8', errors='ignore').strip()
    except Exception:
        return ''


async def _read_upstream_json(upstream) -> dict[str, Any]:
    try:
        payload = await upstream.json()
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


async def _download_onlyoffice_callback_blob(callback_url: str) -> tuple[bytes, str | None]:
    timeout = aiohttp.ClientTimeout(total=300, connect=10)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.get(callback_url, allow_redirects=False) as upstream:
            if 300 <= upstream.status < 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='OnlyOffice callback document blob redirects are not allowed.',
                )
            if upstream.status >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f'Failed to fetch callback document blob: HTTP {upstream.status}',
                )
            content_length = upstream.headers.get('Content-Length')
            if content_length:
                try:
                    parsed_content_length = int(content_length)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail='Failed to fetch callback document blob: invalid Content-Length.',
                    ) from exc
                if parsed_content_length > ONLYOFFICE_EDIT_CALLBACK_MAX_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            'OnlyOffice callback document blob exceeds the '
                            f'{ONLYOFFICE_EDIT_CALLBACK_MAX_BYTES} byte limit.'
                        ),
                    )

            body_chunks: list[bytes] = []
            total_size = 0
            async for chunk in upstream.content.iter_chunked(ONLYOFFICE_CALLBACK_DOWNLOAD_CHUNK_BYTES):
                if not chunk:
                    continue
                total_size += len(chunk)
                if total_size > ONLYOFFICE_EDIT_CALLBACK_MAX_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            'OnlyOffice callback document blob exceeds the '
                            f'{ONLYOFFICE_EDIT_CALLBACK_MAX_BYTES} byte limit.'
                        ),
                    )
                body_chunks.append(chunk)

            body = b''.join(body_chunks)
            return body, upstream.headers.get('Content-Type')


async def _cleanup_terminal_file(session, url: str, headers: dict) -> None:
    try:
        async with session.delete(url, headers=headers, allow_redirects=False) as response:
            if response.status >= 300:
                log.warning('OnlyOffice terminal cleanup failed: HTTP %s', response.status)
    except (aiohttp.ClientError, TimeoutError):
        log.warning('OnlyOffice terminal cleanup transport failure.')


async def _replace_terminal_file_via_temp_upload(
    connection: dict[str, Any],
    terminal_file_path: str,
    user_id: str,
    session_proxy_token: str | None,
    content: bytes,
    content_type: str | None,
    context_id: str | None = None,
) -> None:
    target_base_url = _get_terminal_target_base_url(connection)
    headers = _get_terminal_proxy_headers(connection, user_id, session_proxy_token)
    if context_id:
        headers[TERMINAL_CONTEXT_HEADER] = context_id

    directory = posixpath.dirname(terminal_file_path) or '/'
    suffix = Path(terminal_file_path).suffix
    temp_path = f'{terminal_file_path}.onlyoffice-{uuid4().hex}.tmp{suffix}'
    backup_path = f'{terminal_file_path}.onlyoffice-{uuid4().hex}.backup{suffix}'
    temp_filename = posixpath.basename(temp_path)
    upload_url = f'{target_base_url}/files/upload?{urlencode({"directory": directory})}'
    delete_backup_url = f'{target_base_url}/files/delete?{urlencode({"path": backup_path})}'
    move_url = f'{target_base_url}/files/move'

    timeout = aiohttp.ClientTimeout(total=300, connect=10)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        form = aiohttp.FormData()
        form.add_field(
            'file',
            content,
            filename=temp_filename,
            content_type=content_type or 'application/octet-stream',
        )
        async with session.post(upload_url, headers=headers, data=form, allow_redirects=False) as upload_resp:
            if upload_resp.status >= 300:
                error_message = await _read_upstream_error_message(upload_resp)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f'Terminal upload failed: HTTP {upload_resp.status} {error_message}'.strip(),
                )
            upload_json = await _read_upstream_json(upload_resp)
            uploaded_path = upload_json.get('path')
            if not isinstance(uploaded_path, str) or not uploaded_path:
                uploaded_path = temp_path

        # The original stays recoverable until the new file has been installed.
        # A transport error can occur after the server moved a file, so attempt
        # restoration on both an HTTP failure and a lost response.
        backup_attempted = False
        try:
            backup_attempted = True
            async with session.post(
                move_url,
                headers=headers,
                allow_redirects=False,
                json={'source': terminal_file_path, 'destination': backup_path},
            ) as response:
                if response.status >= 300:
                    raise HTTPException(status_code=502, detail=f'Terminal backup move failed: HTTP {response.status}')
            async with session.post(
                move_url,
                headers=headers,
                allow_redirects=False,
                json={'source': uploaded_path, 'destination': terminal_file_path},
            ) as response:
                if response.status >= 300:
                    raise HTTPException(status_code=502, detail=f'Terminal install move failed: HTTP {response.status}')
        except (HTTPException, aiohttp.ClientError, TimeoutError) as exc:
            restored = False
            if backup_attempted:
                try:
                    async with session.post(
                        move_url,
                        headers=headers,
                        allow_redirects=False,
                        json={'source': backup_path, 'destination': terminal_file_path},
                    ) as restore_response:
                        restored = restore_response.status < 300
                except (aiohttp.ClientError, TimeoutError):
                    log.exception('OnlyOffice original file restore failed; backup retained at %s', backup_path)
            cleanup_url = f'{target_base_url}/files/delete?{urlencode({"path": uploaded_path})}'
            await _cleanup_terminal_file(session, cleanup_url, headers)
            if not restored:
                log.error('OnlyOffice writeback failed; recovery backup may remain at %s', backup_path)
            detail = exc.detail if isinstance(exc, HTTPException) else 'Terminal writeback transport failure'
            raise HTTPException(
                status_code=502,
                detail=f'{detail}; backup_restore={"ok" if restored else "failed"}',
            ) from exc

        await _cleanup_terminal_file(session, delete_backup_url, headers)


async def _file_session_document(form_data, request, user, db, file_token_ttl, public_base_url):
    if not form_data.file_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='file_id is required for source_type=file.',
        )
    file = await _get_file_or_404(form_data.file_id, db=db)
    await _check_read_access(form_data.file_id, file, user, db=db)
    if not file.path:
        raise HTTPException(status_code=404, detail='Original file is not available.')

    file_ext = _get_file_ext(file)
    if file_ext not in SUPPORTED_OFFICE_FILE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Unsupported office file type: {file_ext or "unknown"}',
        )

    file_token = create_token(
        {
            'scope': 'onlyoffice:file',
            'file_id': file.id,
            'user_id': user.id,
            'mode': form_data.mode,
        },
        expires_delta=file_token_ttl,
    )

    document_title = _get_display_name(file)
    document_url = f'{public_base_url}/api/v1/onlyoffice/files/{quote(file.id)}?{urlencode({"token": file_token})}'
    document_file_type = file_ext
    document_key = _get_document_key(file)
    callback_url = f'{public_base_url}/api/v1/onlyoffice/callback/file/{quote(file.id)}'
    return document_title, document_url, document_file_type, document_key, callback_url


async def _terminal_session_document(form_data, request, user, file_token_ttl, public_base_url):
    if not form_data.terminal_server_id or not form_data.terminal_file_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='terminal_server_id and terminal_file_path are required for source_type=terminal.',
        )

    terminal_file_path = _normalize_terminal_file_path(form_data.terminal_file_path)
    connection = await _get_terminal_connection(request, form_data.terminal_server_id, user)
    context_id = _resolve_terminal_context(connection, form_data.chat_id)
    file_ext = Path(terminal_file_path).suffix.lower().lstrip('.')
    if file_ext not in SUPPORTED_OFFICE_FILE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Unsupported office file type: {file_ext or "unknown"}',
        )

    terminal_session_signal = uuid4().hex
    preview_session_proxy_token = None
    if connection.get('auth_type', 'bearer') == 'session':
        preview_session_proxy_token = create_token(
            {
                'id': user.id,
                'role': user.role,
            },
            expires_delta=file_token_ttl,
        )

    terminal_token_payload = {
        'scope': 'onlyoffice:terminal_file',
        'terminal_server_id': connection.get('id'),
        'terminal_file_path': terminal_file_path,
        'user_id': user.id,
        'mode': form_data.mode,
        'session_signal': terminal_session_signal,
        'chat_id': form_data.chat_id,
        'context_id': context_id,
    }
    if preview_session_proxy_token:
        terminal_token_payload['session_proxy_token'] = preview_session_proxy_token

    terminal_token = create_token(
        terminal_token_payload,
        expires_delta=file_token_ttl,
    )
    file_name = Path(terminal_file_path).name or terminal_file_path
    document_title = file_name
    document_url = f'{public_base_url}/api/v1/onlyoffice/terminal/files?{urlencode({"token": terminal_token})}'
    document_file_type = file_ext
    document_key = _get_terminal_document_key(
        connection.get('id'),
        terminal_file_path,
        terminal_session_signal,
    )
    callback_url = f'{public_base_url}/api/v1/onlyoffice/callback/terminal'
    if form_data.mode == 'edit':
        callback_token_ttl = _parse_edit_callback_token_ttl(request, file_token_ttl)
        callback_context_payload = {
            'scope': 'onlyoffice:terminal_callback',
            'terminal_server_id': connection.get('id'),
            'terminal_file_path': terminal_file_path,
            'user_id': user.id,
            'session_signal': terminal_session_signal,
            'document_key': document_key,
            'chat_id': form_data.chat_id,
            'context_id': context_id,
        }
        if connection.get('auth_type', 'bearer') == 'session':
            callback_context_payload['session_proxy_token'] = create_token(
                {
                    'id': user.id,
                    'role': user.role,
                },
                expires_delta=callback_token_ttl,
            )

        callback_context_token = create_token(
            callback_context_payload,
            expires_delta=callback_token_ttl,
        )
        callback_url = f'{callback_url}?{urlencode({"context_token": callback_context_token})}'
    return document_title, document_url, document_file_type, document_key, callback_url


@router.post('/session')
async def create_onlyoffice_session(
    form_data: OnlyOfficeSessionForm,
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    document_server_url = await _require_onlyoffice_enabled(request)

    if form_data.mode == 'edit' and form_data.source_type != 'terminal':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='OnlyOffice edit mode is only enabled for terminal sources.',
        )

    file_token_ttl = _parse_file_token_ttl(request)
    public_base_url = _resolve_onlyoffice_public_base_url(request)

    if form_data.source_type == 'file':
        document = await _file_session_document(form_data, request, user, db, file_token_ttl, public_base_url)
    else:
        document = await _terminal_session_document(form_data, request, user, file_token_ttl, public_base_url)
    document_title, document_url, document_file_type, document_key, callback_url = document

    document_type = SUPPORTED_OFFICE_FILE_TYPES.get(document_file_type)
    config = {
        'documentType': document_type,
        'type': 'desktop' if form_data.mode == 'edit' else 'embedded',
        'document': {
            'title': document_title,
            'url': document_url,
            'fileType': document_file_type,
            'key': document_key,
            'permissions': {
                'edit': form_data.mode == 'edit',
                'download': True,
                'print': True,
                'review': False,
            },
        },
        'editorConfig': {
            'mode': form_data.mode,
            'callbackUrl': callback_url,
            'lang': 'zh-CN',
            'customization': {
                'anonymous': {'request': False},
                'compactHeader': True,
                'hideRightMenu': True,
                'hideRulers': False,
                'toolbarHideFileName': True,
            },
        },
    }
    if form_data.mode == 'edit':
        config['editorConfig']['customization']['forcesave'] = True

    onlyoffice_jwt_secret = (getattr(request.state.onlyoffice_config, 'ONLYOFFICE_JWT_SECRET', '') or '').strip()
    if onlyoffice_jwt_secret:
        config['token'] = jwt.encode(config, onlyoffice_jwt_secret, algorithm='HS256')

    return {
        'document_server_url': document_server_url,
        'config': config,
    }


@router.get('/files/{file_id}', name='get_onlyoffice_file_content')
async def get_onlyoffice_file_content(
    file_id: str,
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    await _require_onlyoffice_enabled(request)

    decoded = decode_token(token)
    if not decoded or decoded.get('scope') != 'onlyoffice:file' or decoded.get('file_id') != file_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid OnlyOffice access token.',
        )

    token_user_id = decoded.get('user_id')
    if not token_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid OnlyOffice access token user context.',
        )

    file = await _get_file_or_404(file_id, db=db)
    token_user = await Users.get_user_by_id(token_user_id, db=db)
    if not token_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid OnlyOffice access token user context.',
        )

    await _check_read_access(file_id, file, token_user, db=db)

    file_path = Path(Storage.get_file(file.path))
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    filename = _get_display_name(file)
    headers = {
        'Content-Disposition': f"inline; filename*=UTF-8''{quote(filename)}",
    }

    return FileResponse(
        path=file_path,
        media_type=(file.meta or {}).get('content_type'),
        headers=headers,
    )


@router.get('/terminal/files', name='get_onlyoffice_terminal_file_content')
async def get_onlyoffice_terminal_file_content(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    await _require_onlyoffice_enabled(request)
    decoded = decode_token(token)
    if not decoded or decoded.get('scope') != 'onlyoffice:terminal_file':
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid OnlyOffice terminal access token.',
        )

    token_user_id = decoded.get('user_id')
    terminal_server_id = decoded.get('terminal_server_id')
    terminal_file_path = decoded.get('terminal_file_path')
    session_proxy_token = decoded.get('session_proxy_token')

    if not token_user_id or not terminal_server_id or not terminal_file_path:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid OnlyOffice terminal access token claims.',
        )

    token_user = await Users.get_user_by_id(token_user_id, db=db)
    if not token_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid OnlyOffice terminal user context.',
        )

    terminal_file_path = _normalize_terminal_file_path(terminal_file_path)
    connection = await _get_terminal_connection(request, terminal_server_id, token_user)

    target_base_url = _get_terminal_target_base_url(connection)
    target_url = f'{target_base_url}/files/view?path={quote(terminal_file_path, safe="/")}'

    headers = _get_terminal_proxy_headers(connection, token_user.id, session_proxy_token)
    context_id = _validate_session_context(connection, decoded)
    if context_id:
        headers[TERMINAL_CONTEXT_HEADER] = context_id

    timeout = aiohttp.ClientTimeout(total=300, connect=10)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        try:
            async with session.get(target_url, headers=headers, allow_redirects=False) as upstream:
                body = await upstream.read()
                if upstream.status >= 300:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f'Terminal file upstream error: HTTP {upstream.status}',
                    )
                filtered_headers = {}
                content_disposition = upstream.headers.get('Content-Disposition')
                if content_disposition:
                    filtered_headers['Content-Disposition'] = content_disposition
                content_type = upstream.headers.get('Content-Type')
                return Response(
                    content=body,
                    media_type=content_type,
                    headers=filtered_headers,
                )
        except HTTPException:
            raise
        except Exception as exc:
            log.exception('Failed to fetch terminal file for OnlyOffice: %s', exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail='Failed to fetch terminal file for OnlyOffice.',
            ) from exc


@router.post('/callback/file/{file_id}')
async def handle_onlyoffice_callback(
    file_id: str,
    form_data: OnlyOfficeCallbackForm,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    await _require_onlyoffice_enabled(request)
    payload = form_data.model_dump(exclude_none=True)
    onlyoffice_jwt_secret = (getattr(request.state.onlyoffice_config, 'ONLYOFFICE_JWT_SECRET', '') or '').strip()

    # Avoid file existence probing by using uniform not-found responses
    # before any file lookup when callback JWT is enabled.
    def _raise_callback_not_found():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if onlyoffice_jwt_secret:
        callback_token = _extract_callback_token(request, payload)
        if not callback_token:
            _raise_callback_not_found()
        try:
            decoded_payload = jwt.decode(callback_token, onlyoffice_jwt_secret, algorithms=['HS256'])
            if isinstance(decoded_payload, dict):
                payload = decoded_payload
        except jwt.InvalidTokenError:
            _raise_callback_not_found()

    file = await _get_file_or_404(file_id, db=db)

    callback_key = payload.get('key')
    if callback_key and callback_key != _get_document_key(file):
        _raise_callback_not_found()

    callback_url = payload.get('url')
    allowlist = getattr(request.state.onlyoffice_config, 'ONLYOFFICE_CALLBACK_ALLOWED_HOSTS', None) or []
    if callback_url and not _is_allowed_host(callback_url, allowlist):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='OnlyOffice callback URL host is not allowlisted.',
        )

    # Phase0+1 is read-only preview, so callback currently only acknowledges.
    log.info(
        'OnlyOffice callback received for file=%s status=%s',
        file_id,
        payload.get('status'),
    )
    return {'error': 0}


def _terminal_callback_payload(form_data: OnlyOfficeCallbackForm, request: Request) -> dict:
    payload = form_data.model_dump(exclude_none=True)
    onlyoffice_jwt_secret = (getattr(request.state.onlyoffice_config, 'ONLYOFFICE_JWT_SECRET', '') or '').strip()

    if onlyoffice_jwt_secret:
        callback_token = _extract_callback_token(request, payload)
        if not callback_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Missing OnlyOffice callback token.',
            )
        try:
            decoded_payload = jwt.decode(callback_token, onlyoffice_jwt_secret, algorithms=['HS256'])
            if isinstance(decoded_payload, dict):
                # Only use authenticated callback fields. OnlyOffice can wrap them
                # in "payload"; never fill missing signed fields from the HTTP body.
                payload = _parse_embedded_callback_payload(decoded_payload) or decoded_payload
                if _coerce_callback_status(payload.get('status')) is None:
                    raise HTTPException(status_code=400, detail='Missing signed callback status.')
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid OnlyOffice callback token.',
            ) from exc
    else:
        payload = _expand_callback_payload_without_jwt_secret(payload)
    return payload


def _callback_is_save(request: Request, payload: dict) -> bool:
    callback_url = payload.get('url')
    allowlist = getattr(request.state.onlyoffice_config, 'ONLYOFFICE_CALLBACK_ALLOWED_HOSTS', None) or []
    if callback_url and not _is_allowed_callback_blob_url(callback_url, allowlist):
        raise HTTPException(status_code=400, detail='OnlyOffice callback URL host is not allowlisted.')
    payload['status'] = _coerce_callback_status(payload.get('status'))
    return payload['status'] in ONLYOFFICE_SAVE_STATUSES


@router.post('/callback/terminal')
async def handle_onlyoffice_terminal_callback(
    form_data: OnlyOfficeCallbackForm,
    request: Request,
):
    await _require_onlyoffice_enabled(request)
    payload = _terminal_callback_payload(form_data, request)

    callback_url = payload.get('url')
    if not _callback_is_save(request, payload):
        log.info('OnlyOffice terminal callback acknowledged without save status=%s', payload.get('status'))
        return {'error': 0}
    callback_status = payload['status']

    callback_context_token = _extract_callback_context_token(request, payload)
    if not callback_context_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Missing OnlyOffice terminal callback context token.',
        )

    callback_context = decode_token(callback_context_token)
    if not callback_context or callback_context.get('scope') != 'onlyoffice:terminal_callback':
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid OnlyOffice terminal callback context token.',
        )

    if not callback_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Missing callback URL for save status.',
        )

    terminal_server_id = callback_context.get('terminal_server_id')
    terminal_file_path = callback_context.get('terminal_file_path')
    user_id = callback_context.get('user_id')
    document_key = callback_context.get('document_key')
    session_proxy_token = callback_context.get('session_proxy_token')

    if not terminal_server_id or not terminal_file_path or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid OnlyOffice terminal callback context claims.',
        )

    callback_key = payload.get('key')
    if not document_key or callback_key != document_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='OnlyOffice callback key does not match session context.',
        )

    terminal_file_path = _normalize_terminal_file_path(terminal_file_path)
    token_user = await Users.get_user_by_id(user_id)
    if not token_user or token_user.role not in ('admin', 'user'):
        raise HTTPException(status_code=401, detail='Invalid OnlyOffice callback user context.')
    connection = await _get_terminal_connection(request, terminal_server_id, token_user)
    context_id = _validate_session_context(connection, callback_context)
    try:
        content, content_type = await _download_onlyoffice_callback_blob(callback_url)
        await _replace_terminal_file_via_temp_upload(
            connection=connection,
            terminal_file_path=terminal_file_path,
            user_id=user_id,
            session_proxy_token=session_proxy_token,
            content=content,
            content_type=content_type,
            context_id=context_id,
        )
    except HTTPException:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='OnlyOffice terminal callback dependency transport failure.',
        ) from exc

    log.info(
        'OnlyOffice terminal callback saved status=%s terminal_server_id=%s path=%s',
        callback_status,
        terminal_server_id,
        terminal_file_path,
    )
    return {'error': 0}
