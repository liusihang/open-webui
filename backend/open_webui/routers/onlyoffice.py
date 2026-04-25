import hashlib
import json
import logging
import posixpath
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlencode, urlparse, quote, unquote
from uuid import uuid4

import aiohttp
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_session
from open_webui.models.files import Files
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.storage.provider import Storage
from open_webui.utils.access_control import has_connection_access
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.auth import create_token, decode_token, get_verified_user
from open_webui.utils.misc import parse_duration

log = logging.getLogger(__name__)

router = APIRouter()

SUPPORTED_OFFICE_FILE_TYPES = {
    "doc": "word",
    "docx": "word",
    "xls": "cell",
    "xlsx": "cell",
    "csv": "cell",
    "ppt": "slide",
    "pptx": "slide",
}
ONLYOFFICE_SAVE_STATUSES = {2, 6}
DEFAULT_ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN = "8h"


class OnlyOfficeSessionForm(BaseModel):
    source_type: Literal["file", "terminal"] = "file"
    file_id: Optional[str] = None
    terminal_server_id: Optional[str] = None
    terminal_file_path: Optional[str] = None
    mode: Literal["view", "edit"] = "view"


class OnlyOfficeCallbackForm(BaseModel):
    status: Optional[int] = None
    key: Optional[str] = None
    url: Optional[str] = None
    token: Optional[str] = None


def _get_display_name(file) -> str:
    return (file.meta or {}).get("name") or file.filename or file.id


def _get_file_ext(file) -> str:
    display_name = _get_display_name(file)
    return Path(display_name).suffix.lower().lstrip(".")


def _get_document_key(file) -> str:
    key_source = f"{file.id}:{file.hash or ''}:{file.updated_at or ''}"
    return hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:48]


def _get_terminal_document_key(
    terminal_server_id: str, terminal_file_path: str, session_signal: str
) -> str:
    key_source = f"terminal:{terminal_server_id}:{terminal_file_path}:{session_signal}"
    return hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:48]


def _require_onlyoffice_enabled(request: Request) -> str:
    if not bool(getattr(request.app.state.config, "ENABLE_ONLYOFFICE_PREVIEW", False)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OnlyOffice preview is disabled.",
        )

    document_server_url = (
        getattr(request.app.state.config, "ONLYOFFICE_DOCUMENT_SERVER_URL", "") or ""
    ).strip()
    if not document_server_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OnlyOffice document server URL is not configured.",
        )
    return document_server_url.rstrip("/")


def _check_read_access(file_id: str, file, user, db: Session) -> None:
    if not (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(file_id, "read", user, db=db)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


def _get_file_or_404(file_id: str, db: Session):
    file = Files.get_file_by_id(file_id, db=db)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    return file


def _resolve_public_base_url(request: Request) -> str:
    configured_webui_url = (
        getattr(request.app.state.config, "WEBUI_URL", "") or ""
    ).strip().rstrip("/")
    if configured_webui_url:
        return configured_webui_url
    return str(request.base_url).rstrip("/")


def _resolve_onlyoffice_public_base_url(request: Request) -> str:
    configured_onlyoffice_base_url = (
        getattr(request.app.state.config, "ONLYOFFICE_PUBLIC_BASE_URL", "") or ""
    ).strip().rstrip("/")
    if configured_onlyoffice_base_url:
        return configured_onlyoffice_base_url
    return _resolve_public_base_url(request)


def _parse_file_token_ttl(request: Request) -> Optional[timedelta]:
    raw = getattr(request.app.state.config, "ONLYOFFICE_FILE_TOKEN_EXPIRES_IN", "")
    try:
        ttl = parse_duration(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Invalid ONLYOFFICE_FILE_TOKEN_EXPIRES_IN value: {raw}",
        ) from exc

    if ttl is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ONLYOFFICE_FILE_TOKEN_EXPIRES_IN must be a finite duration.",
        )

    return ttl


def _parse_edit_callback_token_ttl(request: Request, file_token_ttl: timedelta) -> timedelta:
    raw = (
        getattr(request.app.state.config, "ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN", None)
        or ""
    ).strip()

    if not raw:
        default_ttl = parse_duration(DEFAULT_ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN)
        if default_ttl is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN must be a finite duration.",
            )
        return max(default_ttl, file_token_ttl)

    try:
        ttl = parse_duration(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Invalid ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN value: {raw}",
        ) from exc

    if ttl is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN must be a finite duration.",
        )

    return ttl


def _normalize_terminal_file_path(file_path: str) -> str:
    decoded = unquote(file_path or "")
    if not decoded or "\x00" in decoded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid terminal file path.",
        )

    normalized = posixpath.normpath(decoded)
    if normalized.startswith(".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid terminal file path traversal.",
        )

    if decoded.startswith("/"):
        return normalized if normalized.startswith("/") else f"/{normalized}"
    return normalized


def _is_allowed_host(url: str, allowlist: list[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host:
        return False

    normalized_allowlist = [entry.lower() for entry in allowlist if entry]
    if not normalized_allowlist:
        return False

    return any(host == entry or host.endswith(f".{entry}") for entry in normalized_allowlist)


def _extract_callback_token(request: Request, payload: dict[str, Any]) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    if isinstance(payload.get("token"), str):
        return payload["token"]
    return ""


def _extract_callback_context_token(request: Request, payload: dict[str, Any]) -> str:
    query_params = getattr(request, "query_params", {}) or {}
    query_token = query_params.get("context_token")
    if isinstance(query_token, str) and query_token:
        return query_token

    payload_token = payload.get("context_token")
    if isinstance(payload_token, str) and payload_token:
        return payload_token
    return ""


def _coerce_callback_status(value: Any) -> Optional[int]:
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
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["HS256", "HS384", "HS512", "RS256", "RS384", "RS512"],
        )
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _parse_embedded_callback_payload(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("payload")
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
        for field in ("status", "key", "url", "context_token", "token"):
            if expanded.get(field) is None and embedded_payload.get(field) is not None:
                expanded[field] = embedded_payload[field]

    status_value = _coerce_callback_status(expanded.get("status"))
    if status_value is not None:
        expanded["status"] = status_value
        return expanded

    callback_token = expanded.get("token")
    if not isinstance(callback_token, str) or not callback_token:
        return expanded

    decoded_payload = _decode_callback_token_without_verification(callback_token)
    if not decoded_payload:
        return expanded

    for field in ("status", "key", "url", "context_token"):
        if expanded.get(field) is None and decoded_payload.get(field) is not None:
            expanded[field] = decoded_payload[field]

    status_value = _coerce_callback_status(expanded.get("status"))
    if status_value is not None:
        expanded["status"] = status_value
    return expanded


def _get_terminal_connection(request: Request, terminal_server_id: str, user):
    connections = getattr(request.app.state.config, "TERMINAL_SERVER_CONNECTIONS", None) or []
    connection = next((c for c in connections if c.get("id") == terminal_server_id), None)
    if connection is None or not connection.get("enabled", True):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Terminal server not found.",
        )

    user_group_ids = {group.id for group in Groups.get_groups_by_member_id(user.id)}
    if not has_connection_access(user, connection, user_group_ids):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    auth_type = connection.get("auth_type", "bearer")
    if auth_type not in ("bearer", "none", "session"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Terminal auth_type '{auth_type}' is not supported for OnlyOffice phase0+1 (supports bearer/none/session).",
        )
    return connection


def _get_terminal_connection_for_callback(request: Request, terminal_server_id: str) -> dict[str, Any]:
    connections = getattr(request.app.state.config, "TERMINAL_SERVER_CONNECTIONS", None) or []
    connection = next((c for c in connections if c.get("id") == terminal_server_id), None)
    if connection is None or not connection.get("enabled", True):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Terminal server not found.",
        )
    return connection


def _get_terminal_target_base_url(connection: dict[str, Any]) -> str:
    base_url = (connection.get("url") or "").rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Terminal server URL is not configured.",
        )

    policy_id = connection.get("policy_id")
    if policy_id:
        return f"{base_url}/p/{policy_id}"
    return base_url


def _get_terminal_proxy_headers(
    connection: dict[str, Any], user_id: str, session_proxy_token: Optional[str]
) -> dict[str, str]:
    headers = {"X-User-Id": user_id}
    auth_type = connection.get("auth_type", "bearer")
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {connection.get('key', '')}"
    elif auth_type == "session":
        if not isinstance(session_proxy_token, str) or not session_proxy_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing session proxy token for terminal callback auth.",
            )
        headers["Authorization"] = f"Bearer {session_proxy_token}"
    elif auth_type not in ("none",):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Terminal auth_type '{auth_type}' is not supported for OnlyOffice callback writeback.",
        )
    return headers


async def _read_upstream_error_message(upstream) -> str:
    try:
        body = await upstream.read()
    except Exception:
        return ""

    if not body:
        return ""

    try:
        return body.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


async def _read_upstream_json(upstream) -> dict[str, Any]:
    try:
        payload = await upstream.json()
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


async def _download_onlyoffice_callback_blob(callback_url: str) -> tuple[bytes, Optional[str]]:
    timeout = aiohttp.ClientTimeout(total=300, connect=10)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.get(callback_url) as upstream:
            body = await upstream.read()
            if upstream.status >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to fetch callback document blob: HTTP {upstream.status}",
                )
            return body, upstream.headers.get("Content-Type")


async def _replace_terminal_file_via_temp_upload(
    connection: dict[str, Any],
    terminal_file_path: str,
    user_id: str,
    session_proxy_token: Optional[str],
    content: bytes,
    content_type: Optional[str],
) -> None:
    target_base_url = _get_terminal_target_base_url(connection)
    headers = _get_terminal_proxy_headers(connection, user_id, session_proxy_token)

    directory = posixpath.dirname(terminal_file_path) or "/"
    suffix = Path(terminal_file_path).suffix
    temp_path = f"{terminal_file_path}.onlyoffice-{uuid4().hex}.tmp{suffix}"
    backup_path = f"{terminal_file_path}.onlyoffice-{uuid4().hex}.backup{suffix}"
    temp_filename = posixpath.basename(temp_path)
    upload_url = f"{target_base_url}/files/upload?{urlencode({'directory': directory})}"
    delete_backup_url = f"{target_base_url}/files/delete?{urlencode({'path': backup_path})}"
    move_url = f"{target_base_url}/files/move"

    timeout = aiohttp.ClientTimeout(total=300, connect=10)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        form = aiohttp.FormData()
        form.add_field(
            "file",
            content,
            filename=temp_filename,
            content_type=content_type or "application/octet-stream",
        )
        async with session.post(upload_url, headers=headers, data=form) as upload_resp:
            if upload_resp.status >= 400:
                error_message = await _read_upstream_error_message(upload_resp)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Terminal upload failed: HTTP {upload_resp.status} {error_message}".strip(),
                )
            upload_json = await _read_upstream_json(upload_resp)
            uploaded_path = upload_json.get("path")
            if not isinstance(uploaded_path, str) or not uploaded_path:
                uploaded_path = temp_path

        async with session.post(
            move_url,
            headers=headers,
            json={"source": terminal_file_path, "destination": backup_path},
        ) as backup_move_resp:
            if backup_move_resp.status >= 400:
                error_message = await _read_upstream_error_message(backup_move_resp)
                cleanup_url = f"{target_base_url}/files/delete?{urlencode({'path': uploaded_path})}"
                try:
                    async with session.delete(cleanup_url, headers=headers):
                        pass
                except Exception as cleanup_exc:
                    log.exception(
                        "Failed to cleanup uploaded temp file after backup move failure temp_path=%s error=%s",
                        uploaded_path,
                        cleanup_exc,
                    )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Terminal backup move failed: HTTP {backup_move_resp.status} {error_message}".strip(),
                )

        async with session.post(
            move_url,
            headers=headers,
            json={"source": uploaded_path, "destination": terminal_file_path},
        ) as move_resp:
            if move_resp.status >= 400:
                error_message = await _read_upstream_error_message(move_resp)
                restored = False
                try:
                    async with session.post(
                        move_url,
                        headers=headers,
                        json={"source": backup_path, "destination": terminal_file_path},
                    ) as restore_resp:
                        restored = restore_resp.status < 400
                        if not restored:
                            restore_error = await _read_upstream_error_message(restore_resp)
                            log.error(
                                "Failed to restore backup after install move failure backup_path=%s original_path=%s status=%s error=%s",
                                backup_path,
                                terminal_file_path,
                                restore_resp.status,
                                restore_error,
                            )
                except Exception as restore_exc:
                    log.exception(
                        "Failed to restore backup after install move failure backup_path=%s original_path=%s error=%s",
                        backup_path,
                        terminal_file_path,
                        restore_exc,
                    )

                cleanup_url = f"{target_base_url}/files/delete?{urlencode({'path': uploaded_path})}"
                try:
                    async with session.delete(cleanup_url, headers=headers):
                        pass
                except Exception as cleanup_exc:
                    log.exception(
                        "Failed to cleanup uploaded temp file after install move failure temp_path=%s error=%s",
                        uploaded_path,
                        cleanup_exc,
                    )

                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        f"Terminal install move failed: HTTP {move_resp.status} {error_message}; "
                        f"backup_restore={'ok' if restored else 'failed'}"
                    ).strip(),
                )

        try:
            async with session.delete(delete_backup_url, headers=headers) as delete_backup_resp:
                if delete_backup_resp.status >= 400:
                    backup_delete_error = await _read_upstream_error_message(delete_backup_resp)
                    log.warning(
                        "Failed to cleanup terminal backup after successful install backup_path=%s status=%s error=%s",
                        backup_path,
                        delete_backup_resp.status,
                        backup_delete_error,
                    )
        except Exception as delete_backup_exc:
            log.exception(
                "Failed to cleanup terminal backup after successful install backup_path=%s error=%s",
                backup_path,
                delete_backup_exc,
            )


@router.post("/session")
async def create_onlyoffice_session(
    form_data: OnlyOfficeSessionForm,
    request: Request,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    document_server_url = _require_onlyoffice_enabled(request)

    if form_data.mode == "edit" and form_data.source_type != "terminal":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OnlyOffice edit mode is only enabled for terminal sources.",
        )

    file_token_ttl = _parse_file_token_ttl(request)
    public_base_url = _resolve_onlyoffice_public_base_url(request)

    document_title = ""
    document_url = ""
    document_file_type = ""
    document_key = ""
    callback_url = ""

    if form_data.source_type == "file":
        if not form_data.file_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file_id is required for source_type=file.",
            )
        file = _get_file_or_404(form_data.file_id, db=db)
        _check_read_access(form_data.file_id, file, user, db=db)

        file_ext = _get_file_ext(file)
        if file_ext not in SUPPORTED_OFFICE_FILE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported office file type: {file_ext or 'unknown'}",
            )

        file_token = create_token(
            {
                "scope": "onlyoffice:file",
                "file_id": file.id,
                "user_id": user.id,
                "mode": form_data.mode,
            },
            expires_delta=file_token_ttl,
        )

        document_title = _get_display_name(file)
        document_url = (
            f"{public_base_url}/api/v1/onlyoffice/files/{quote(file.id)}?"
            f"{urlencode({'token': file_token})}"
        )
        document_file_type = file_ext
        document_key = _get_document_key(file)
        callback_url = f"{public_base_url}/api/v1/onlyoffice/callback/file/{quote(file.id)}"
    else:
        if not form_data.terminal_server_id or not form_data.terminal_file_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="terminal_server_id and terminal_file_path are required for source_type=terminal.",
            )

        terminal_file_path = _normalize_terminal_file_path(form_data.terminal_file_path)
        connection = _get_terminal_connection(request, form_data.terminal_server_id, user)
        file_ext = Path(terminal_file_path).suffix.lower().lstrip(".")
        if file_ext not in SUPPORTED_OFFICE_FILE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported office file type: {file_ext or 'unknown'}",
            )

        terminal_session_signal = uuid4().hex
        preview_session_proxy_token = None
        if connection.get("auth_type", "bearer") == "session":
            preview_session_proxy_token = create_token(
                {
                    "id": user.id,
                    "role": user.role,
                },
                expires_delta=file_token_ttl,
            )

        terminal_token_payload = {
            "scope": "onlyoffice:terminal_file",
            "terminal_server_id": connection.get("id"),
            "terminal_file_path": terminal_file_path,
            "user_id": user.id,
            "mode": form_data.mode,
            "session_signal": terminal_session_signal,
        }
        if preview_session_proxy_token:
            terminal_token_payload["session_proxy_token"] = preview_session_proxy_token

        terminal_token = create_token(
            terminal_token_payload,
            expires_delta=file_token_ttl,
        )
        file_name = Path(terminal_file_path).name or terminal_file_path
        document_title = file_name
        document_url = (
            f"{public_base_url}/api/v1/onlyoffice/terminal/files?"
            f"{urlencode({'token': terminal_token})}"
        )
        document_file_type = file_ext
        document_key = _get_terminal_document_key(
            connection.get("id"),
            terminal_file_path,
            terminal_session_signal,
        )
        callback_url = f"{public_base_url}/api/v1/onlyoffice/callback/terminal"
        if form_data.mode == "edit":
            callback_token_ttl = _parse_edit_callback_token_ttl(request, file_token_ttl)
            callback_context_payload = {
                "scope": "onlyoffice:terminal_callback",
                "terminal_server_id": connection.get("id"),
                "terminal_file_path": terminal_file_path,
                "user_id": user.id,
                "session_signal": terminal_session_signal,
                "document_key": document_key,
            }
            if connection.get("auth_type", "bearer") == "session":
                callback_context_payload["session_proxy_token"] = create_token(
                    {
                        "id": user.id,
                        "role": user.role,
                    },
                    expires_delta=callback_token_ttl,
                )

            callback_context_token = create_token(
                callback_context_payload,
                expires_delta=callback_token_ttl,
            )
            callback_url = f"{callback_url}?{urlencode({'context_token': callback_context_token})}"

    document_type = SUPPORTED_OFFICE_FILE_TYPES.get(document_file_type)
    config = {
        "documentType": document_type,
        "type": "desktop" if form_data.mode == "edit" else "embedded",
        "document": {
            "title": document_title,
            "url": document_url,
            "fileType": document_file_type,
            "key": document_key,
            "permissions": {
                "edit": form_data.mode == "edit",
                "download": True,
                "print": True,
                "review": False,
            },
        },
        "editorConfig": {
            "mode": form_data.mode,
            "callbackUrl": callback_url,
            "lang": "zh-CN",
            "customization": {
                "anonymous": {"request": False},
                "compactHeader": True,
                "hideRightMenu": True,
                "hideRulers": False,
                "toolbarHideFileName": True,
            },
        },
    }
    if form_data.mode == "edit":
        config["editorConfig"]["customization"]["forcesave"] = True

    onlyoffice_jwt_secret = (
        getattr(request.app.state.config, "ONLYOFFICE_JWT_SECRET", "") or ""
    ).strip()
    if onlyoffice_jwt_secret:
        config["token"] = jwt.encode(config, onlyoffice_jwt_secret, algorithm="HS256")

    return {
        "document_server_url": document_server_url,
        "config": config,
    }


@router.get("/files/{file_id}", name="get_onlyoffice_file_content")
async def get_onlyoffice_file_content(
    file_id: str,
    token: str,
    request: Request,
    db: Session = Depends(get_session),
):
    _require_onlyoffice_enabled(request)

    decoded = decode_token(token)
    if not decoded or decoded.get("scope") != "onlyoffice:file" or decoded.get("file_id") != file_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OnlyOffice access token.",
        )

    token_user_id = decoded.get("user_id")
    if not token_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OnlyOffice access token user context.",
        )

    file = _get_file_or_404(file_id, db=db)
    token_user = Users.get_user_by_id(token_user_id, db=db)
    if not token_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OnlyOffice access token user context.",
        )

    _check_read_access(file_id, file, token_user, db=db)

    file_path = Path(Storage.get_file(file.path))
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    filename = _get_display_name(file)
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
    }

    return FileResponse(
        path=file_path,
        media_type=(file.meta or {}).get("content_type"),
        headers=headers,
    )


@router.get("/terminal/files", name="get_onlyoffice_terminal_file_content")
async def get_onlyoffice_terminal_file_content(
    token: str,
    request: Request,
    db: Session = Depends(get_session),
):
    _require_onlyoffice_enabled(request)
    decoded = decode_token(token)
    if not decoded or decoded.get("scope") != "onlyoffice:terminal_file":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OnlyOffice terminal access token.",
        )

    token_user_id = decoded.get("user_id")
    terminal_server_id = decoded.get("terminal_server_id")
    terminal_file_path = decoded.get("terminal_file_path")
    session_proxy_token = decoded.get("session_proxy_token")

    if not token_user_id or not terminal_server_id or not terminal_file_path:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OnlyOffice terminal access token claims.",
        )

    token_user = Users.get_user_by_id(token_user_id, db=db)
    if not token_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OnlyOffice terminal user context.",
        )

    terminal_file_path = _normalize_terminal_file_path(terminal_file_path)
    connection = _get_terminal_connection(request, terminal_server_id, token_user)

    base_url = (connection.get("url") or "").rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Terminal server URL is not configured.",
        )

    target_base_url = base_url
    policy_id = connection.get("policy_id")
    if policy_id:
        target_base_url = f"{base_url}/p/{policy_id}"
    target_url = f"{target_base_url}/files/view?path={quote(terminal_file_path, safe='/')}"

    headers = {"X-User-Id": token_user.id}
    auth_type = connection.get("auth_type", "bearer")
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {connection.get('key', '')}"
    elif auth_type == "session":
        if not isinstance(session_proxy_token, str) or not session_proxy_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing session proxy token for terminal session auth.",
            )
        headers["Authorization"] = f"Bearer {session_proxy_token}"

    timeout = aiohttp.ClientTimeout(total=300, connect=10)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        try:
            async with session.get(target_url, headers=headers) as upstream:
                body = await upstream.read()
                if upstream.status >= 400:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Terminal file upstream error: HTTP {upstream.status}",
                    )
                filtered_headers = {}
                content_disposition = upstream.headers.get("Content-Disposition")
                if content_disposition:
                    filtered_headers["Content-Disposition"] = content_disposition
                content_type = upstream.headers.get("Content-Type")
                return Response(
                    content=body,
                    media_type=content_type,
                    headers=filtered_headers,
                )
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("Failed to fetch terminal file for OnlyOffice: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to fetch terminal file for OnlyOffice.",
            ) from exc


@router.post("/callback/file/{file_id}")
async def handle_onlyoffice_callback(
    file_id: str,
    form_data: OnlyOfficeCallbackForm,
    request: Request,
    db: Session = Depends(get_session),
):
    _require_onlyoffice_enabled(request)
    payload = form_data.model_dump(exclude_none=True)
    onlyoffice_jwt_secret = (
        getattr(request.app.state.config, "ONLYOFFICE_JWT_SECRET", "") or ""
    ).strip()

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
            decoded_payload = jwt.decode(callback_token, onlyoffice_jwt_secret, algorithms=["HS256"])
            if isinstance(decoded_payload, dict):
                payload = decoded_payload
        except jwt.InvalidTokenError:
            _raise_callback_not_found()

    file = _get_file_or_404(file_id, db=db)

    callback_key = payload.get("key")
    if callback_key and callback_key != _get_document_key(file):
        _raise_callback_not_found()

    callback_url = payload.get("url")
    allowlist = (
        getattr(request.app.state.config, "ONLYOFFICE_CALLBACK_ALLOWED_HOSTS", None) or []
    )
    if callback_url and not _is_allowed_host(callback_url, allowlist):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OnlyOffice callback URL host is not allowlisted.",
        )

    # Phase0+1 is read-only preview, so callback currently only acknowledges.
    log.info(
        "OnlyOffice callback received for file=%s status=%s",
        file_id,
        payload.get("status"),
    )
    return {"error": 0}


@router.post("/callback/terminal")
async def handle_onlyoffice_terminal_callback(
    form_data: OnlyOfficeCallbackForm,
    request: Request,
):
    _require_onlyoffice_enabled(request)
    payload = form_data.model_dump(exclude_none=True)
    onlyoffice_jwt_secret = (
        getattr(request.app.state.config, "ONLYOFFICE_JWT_SECRET", "") or ""
    ).strip()

    if onlyoffice_jwt_secret:
        callback_token = _extract_callback_token(request, payload)
        if not callback_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing OnlyOffice callback token.",
            )
        try:
            decoded_payload = jwt.decode(callback_token, onlyoffice_jwt_secret, algorithms=["HS256"])
            if isinstance(decoded_payload, dict):
                merged_payload = dict(payload)
                for field in ("status", "key", "url", "context_token"):
                    if decoded_payload.get(field) is not None:
                        merged_payload[field] = decoded_payload[field]
                payload = merged_payload
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid OnlyOffice callback token.",
            ) from exc
    else:
        payload = _expand_callback_payload_without_jwt_secret(payload)

    callback_url = payload.get("url")
    allowlist = (
        getattr(request.app.state.config, "ONLYOFFICE_CALLBACK_ALLOWED_HOSTS", None) or []
    )
    if callback_url and not _is_allowed_host(callback_url, allowlist):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OnlyOffice callback URL host is not allowlisted.",
        )

    callback_status = payload.get("status")
    coerced_status = _coerce_callback_status(callback_status)
    if coerced_status is not None:
        callback_status = coerced_status
        payload["status"] = callback_status
    if callback_status not in ONLYOFFICE_SAVE_STATUSES:
        log.info("OnlyOffice terminal callback acknowledged without save status=%s", callback_status)
        return {"error": 0}

    callback_context_token = _extract_callback_context_token(request, payload)
    if not callback_context_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing OnlyOffice terminal callback context token.",
        )

    callback_context = decode_token(callback_context_token)
    if not callback_context or callback_context.get("scope") != "onlyoffice:terminal_callback":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OnlyOffice terminal callback context token.",
        )

    if not callback_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing callback URL for save status.",
        )

    terminal_server_id = callback_context.get("terminal_server_id")
    terminal_file_path = callback_context.get("terminal_file_path")
    user_id = callback_context.get("user_id")
    document_key = callback_context.get("document_key")
    session_proxy_token = callback_context.get("session_proxy_token")

    if not terminal_server_id or not terminal_file_path or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OnlyOffice terminal callback context claims.",
        )

    callback_key = payload.get("key")
    if callback_key and document_key and callback_key != document_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="OnlyOffice callback key does not match session context.",
        )

    terminal_file_path = _normalize_terminal_file_path(terminal_file_path)
    connection = _get_terminal_connection_for_callback(request, terminal_server_id)
    try:
        content, content_type = await _download_onlyoffice_callback_blob(callback_url)
        await _replace_terminal_file_via_temp_upload(
            connection=connection,
            terminal_file_path=terminal_file_path,
            user_id=user_id,
            session_proxy_token=session_proxy_token,
            content=content,
            content_type=content_type,
        )
    except HTTPException:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OnlyOffice terminal callback dependency transport failure.",
        ) from exc

    log.info(
        "OnlyOffice terminal callback saved status=%s terminal_server_id=%s path=%s",
        callback_status,
        terminal_server_id,
        terminal_file_path,
    )
    return {"error": 0}
