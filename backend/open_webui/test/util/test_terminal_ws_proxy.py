from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_terminal_ws_proxy_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "utils"
        / "terminal_ws_proxy.py"
    )
    spec = spec_from_file_location("terminal_ws_proxy", module_path)
    module = module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_upstream_terminal_ws_request_uses_client_jwt_for_session_auth():
    proxy = _load_terminal_ws_proxy_module()

    connection = {
        "url": "http://terminals.internal:3000",
        "auth_type": "session",
    }

    upstream_url, upstream_first_message = proxy.build_upstream_terminal_ws_request(
        connection=connection,
        session_id="session-1",
        user_id="user-123",
        client_token="jwt-token-abc",
    )

    assert (
        upstream_url
        == "ws://terminals.internal:3000/api/terminals/session-1?user_id=user-123&token=jwt-token-abc"
    )
    assert upstream_first_message is None


def test_build_upstream_terminal_ws_request_preserves_bearer_first_message_auth():
    proxy = _load_terminal_ws_proxy_module()

    connection = {
        "url": "https://terminal.example.com",
        "auth_type": "bearer",
        "key": "terminal-api-key",
    }

    upstream_url, upstream_first_message = proxy.build_upstream_terminal_ws_request(
        connection=connection,
        session_id="session-2",
        user_id="user-456",
        client_token="ignored-client-token",
    )

    assert (
        upstream_url
        == "wss://terminal.example.com/api/terminals/session-2?user_id=user-456"
    )
    assert upstream_first_message == {"type": "auth", "token": "terminal-api-key"}
