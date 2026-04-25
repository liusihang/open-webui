from typing import Any, Dict, List, Optional
from urllib.parse import urlencode


def build_tool_server_headers(
    server: Dict[str, Any],
    *,
    session_token: Optional[str] = None,
    oauth_token: Optional[dict] = None,
) -> Optional[dict]:
    auth_type = server.get('auth_type', 'bearer')
    token = None

    if auth_type == 'bearer':
        token = server.get('key', '')
    elif auth_type == 'session':
        token = session_token
    elif auth_type == 'system_oauth' and oauth_token:
        token = oauth_token.get('access_token', '')

    headers = {'Authorization': f'Bearer {token}'} if token else None

    connection_headers = server.get('headers', None)
    if connection_headers and isinstance(connection_headers, dict):
        headers = {**(headers or {}), **connection_headers}

    return headers


def tool_server_cache_requires_refresh(
    cached_servers: List[Dict[str, Any]],
    configured_servers: List[Dict[str, Any]],
    *,
    session_token: Optional[str] = None,
    oauth_token: Optional[dict] = None,
) -> bool:
    if not cached_servers:
        return False

    if not session_token and not oauth_token:
        return False

    cached_ids = {str(server.get('id')) for server in cached_servers if server.get('id') is not None}

    for idx, server in enumerate(configured_servers):
        enabled = server.get('config', {}).get('enable', server.get('enabled', True))
        if not enabled:
            continue

        auth_type = server.get('auth_type', 'bearer')
        if auth_type == 'session' and not session_token:
            continue
        if auth_type == 'system_oauth' and not oauth_token:
            continue
        if auth_type not in {'session', 'system_oauth'}:
            continue

        info = server.get('info', {})
        server_id = info.get('id') or server.get('id') or str(idx)
        if str(server_id) not in cached_ids:
            return True

    return False


def build_upstream_terminal_ws_request(
    connection: dict,
    session_id: str,
    user_id: str,
    client_token: str,
) -> tuple[str, Optional[dict]]:
    base_url = (connection.get('url') or '').rstrip('/')
    ws_base = base_url.replace('https://', 'wss://').replace('http://', 'ws://')

    auth_type = connection.get('auth_type', 'bearer')
    params = {'user_id': user_id}
    first_message = None

    if auth_type == 'session' and client_token:
        params['token'] = client_token
    elif auth_type == 'bearer':
        first_message = {'type': 'auth', 'token': connection.get('key', '')}

    policy_id = connection.get('policy_id')
    if policy_id:
        upstream_url = f'{ws_base}/p/{policy_id}/api/terminals/{session_id}'
    else:
        upstream_url = f'{ws_base}/api/terminals/{session_id}'

    if params:
        upstream_url += f'?{urlencode(params)}'

    return upstream_url, first_message
