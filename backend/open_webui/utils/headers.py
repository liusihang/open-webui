import logging
import time
from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import quote

import jwt
from open_webui.env import (
    FORWARD_USER_INFO_HEADER_JWT,
    FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS,
    FORWARD_USER_INFO_HEADER_JWT_SECRET,
    FORWARD_USER_INFO_HEADER_USER_EMAIL,
    FORWARD_USER_INFO_HEADER_USER_ID,
    FORWARD_USER_INFO_HEADER_USER_NAME,
    FORWARD_USER_INFO_HEADER_USER_ROLE,
)

log = logging.getLogger(__name__)


def _get_user_value(user: Any, key: str) -> str:
    if isinstance(user, Mapping):
        return str(user.get(key) or '')
    return str(getattr(user, key, '') or '')


def _mint_forward_user_jwt(user: Any) -> str:
    now = int(time.time())
    payload = {
        'sub': _get_user_value(user, 'id'),
        'email': _get_user_value(user, 'email'),
        'name': _get_user_value(user, 'name'),
        'role': _get_user_value(user, 'role'),
        'iss': 'open-webui',
        'iat': now,
        'exp': now + FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS,
    }
    return jwt.encode(payload, FORWARD_USER_INFO_HEADER_JWT_SECRET, algorithm='HS256')


def include_user_info_headers(headers: dict, user: Optional[Any] = None) -> dict:
    """
    Forward user identity to external backends: signed JWT in
    FORWARD_USER_INFO_HEADER_JWT if FORWARD_USER_INFO_HEADER_JWT_SECRET is set;
    otherwise the legacy X-OpenWebUI-User-* headers.
    """
    if user is None:
        return headers

    if FORWARD_USER_INFO_HEADER_JWT_SECRET:
        try:
            token = _mint_forward_user_jwt(user)
            return {**headers, FORWARD_USER_INFO_HEADER_JWT: token}
        except Exception:
            log.exception(
                'Failed to mint %s; falling back to plain user-info headers.',
                FORWARD_USER_INFO_HEADER_JWT,
            )

    return {
        **headers,
        FORWARD_USER_INFO_HEADER_USER_NAME: quote(_get_user_value(user, 'name'), safe=' '),
        FORWARD_USER_INFO_HEADER_USER_ID: _get_user_value(user, 'id'),
        FORWARD_USER_INFO_HEADER_USER_EMAIL: _get_user_value(user, 'email'),
        FORWARD_USER_INFO_HEADER_USER_ROLE: _get_user_value(user, 'role'),
    }


def get_custom_headers(custom_headers: dict, user=None, metadata: dict = None) -> dict:
    if not custom_headers or not isinstance(custom_headers, dict):
        return {}

    metadata = metadata or {}
    template_vars = {
        '{{CHAT_ID}}': metadata.get('chat_id', '') or '',
        '{{MESSAGE_ID}}': metadata.get('message_id', '') or '',
        '{{USER_ID}}': (user.id if user else '') or '',
        '{{USER_NAME}}': (user.name if user else '') or '',
        '{{USER_EMAIL}}': (user.email if user else '') or '',
        '{{USER_ROLE}}': (user.role if user else '') or '',
    }

    parsed_headers = {}
    for key, value in custom_headers.items():
        if not isinstance(value, str):
            value = str(value)
        for token, val in template_vars.items():
            value = value.replace(token, val)
        parsed_headers[key] = value

    return parsed_headers
