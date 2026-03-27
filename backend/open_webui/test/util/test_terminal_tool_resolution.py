import asyncio
from types import SimpleNamespace

from open_webui.utils import tools as tools_module


def _fake_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    TERMINAL_SERVER_CONNECTIONS=[
                        {
                            "id": "terminals",
                            "name": "terminals",
                            "enabled": True,
                            "url": "http://terminals.internal:3000",
                            "path": "/openapi.json",
                            "auth_type": "session",
                        }
                    ]
                ),
                redis=None,
                TERMINAL_SERVERS=[],
            )
        ),
        state=SimpleNamespace(token=SimpleNamespace(credentials="jwt-token-abc")),
        cookies={},
    )


def test_get_terminal_tools_fetches_session_terminal_specs_with_request_token(
    monkeypatch,
):
    request = _fake_request()
    user = SimpleNamespace(id="user-1")
    observed_headers = []

    async def fake_get_tool_server_data(url: str, headers: dict | None):
        observed_headers.append(headers)
        if headers != {"Authorization": "Bearer jwt-token-abc"}:
            raise AssertionError(f"missing session auth headers: {headers!r}")

        return {
            "openapi": "3.1.0",
            "info": {"title": "Terminals"},
            "paths": {
                "/run_command": {
                    "post": {
                        "operationId": "run_command",
                        "description": "Run a shell command",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "command": {"type": "string"}
                                        },
                                        "required": ["command"],
                                    }
                                }
                            }
                        },
                    }
                }
            },
        }

    async def fake_get_terminal_cwd(base_url: str, headers: dict, cookies=None):
        return "/workspace"

    monkeypatch.setattr(
        tools_module,
        "get_tool_server_data",
        fake_get_tool_server_data,
    )
    monkeypatch.setattr(
        tools_module,
        "get_terminal_cwd",
        fake_get_terminal_cwd,
    )
    monkeypatch.setattr(
        tools_module.Groups,
        "get_groups_by_member_id",
        lambda user_id: [],
    )
    monkeypatch.setattr(
        tools_module,
        "has_connection_access",
        lambda user, connection, user_group_ids=None: True,
    )

    tools = asyncio.run(
        tools_module.get_terminal_tools(
            request=request,
            terminal_id="terminals",
            user=user,
            extra_params={},
        )
    )

    assert observed_headers == [{"Authorization": "Bearer jwt-token-abc"}]
    assert "run_command" in tools
    assert tools["run_command"]["tool_id"] == "terminal:terminals"
    assert "/workspace" in tools["run_command"]["spec"]["description"]
