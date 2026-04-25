import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / 'backend' / 'open_webui' / 'socket' / 'terminal_substrate.py'
SPEC = importlib.util.spec_from_file_location('terminal_substrate', MODULE_PATH)
terminal_substrate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(terminal_substrate)

build_tool_server_headers = terminal_substrate.build_tool_server_headers
build_upstream_terminal_ws_request = terminal_substrate.build_upstream_terminal_ws_request
tool_server_cache_requires_refresh = terminal_substrate.tool_server_cache_requires_refresh


def test_build_tool_server_headers_prefers_session_token_and_merges_connection_headers():
    headers = build_tool_server_headers(
        {
            'auth_type': 'session',
            'headers': {'X-Test': '1'},
        },
        session_token='session-token',
    )

    assert headers == {'Authorization': 'Bearer session-token', 'X-Test': '1'}


def test_tool_server_cache_requires_refresh_when_session_server_missing_from_cached_specs():
    should_refresh = tool_server_cache_requires_refresh(
        cached_servers=[{'id': 'bearer-terminal'}],
        configured_servers=[
            {'id': 'bearer-terminal', 'enabled': True, 'auth_type': 'bearer'},
            {'id': 'session-terminal', 'enabled': True, 'auth_type': 'session'},
        ],
        session_token='session-token',
    )

    assert should_refresh is True


def test_tool_server_cache_requires_refresh_ignores_missing_session_servers_without_auth_context():
    should_refresh = tool_server_cache_requires_refresh(
        cached_servers=[{'id': 'bearer-terminal'}],
        configured_servers=[
            {'id': 'bearer-terminal', 'enabled': True, 'auth_type': 'bearer'},
            {'id': 'session-terminal', 'enabled': True, 'auth_type': 'session'},
        ],
    )

    assert should_refresh is False


def test_build_upstream_terminal_ws_request_uses_query_token_for_session_auth():
    upstream_url, upstream_first_message = build_upstream_terminal_ws_request(
        connection={'url': 'http://terminal.example', 'auth_type': 'session'},
        session_id='session-1',
        user_id='user-1',
        client_token='client-jwt',
    )

    assert upstream_url == 'ws://terminal.example/api/terminals/session-1?user_id=user-1&token=client-jwt'
    assert upstream_first_message is None


def test_build_upstream_terminal_ws_request_preserves_policy_path_for_bearer_auth():
    upstream_url, upstream_first_message = build_upstream_terminal_ws_request(
        connection={
            'url': 'https://terminal.example',
            'auth_type': 'bearer',
            'key': 'terminal-api-key',
            'policy_id': 'policy-1',
        },
        session_id='session-1',
        user_id='user-1',
        client_token='client-jwt',
    )

    assert upstream_url == 'wss://terminal.example/p/policy-1/api/terminals/session-1?user_id=user-1'
    assert upstream_first_message == {'type': 'auth', 'token': 'terminal-api-key'}
