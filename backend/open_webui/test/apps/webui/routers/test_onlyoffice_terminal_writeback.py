from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from open_webui.routers import onlyoffice as onlyoffice_mod


def _fake_request(
    *,
    terminal_connections=None,
    callback_allowlist=None,
    query_params=None,
):
    config = SimpleNamespace(
        ENABLE_ONLYOFFICE_PREVIEW=True,
        ONLYOFFICE_DOCUMENT_SERVER_URL="http://onlyoffice.internal",
        ONLYOFFICE_FILE_TOKEN_EXPIRES_IN="5m",
        ONLYOFFICE_PUBLIC_BASE_URL="https://webui.example",
        WEBUI_URL="",
        ONLYOFFICE_JWT_SECRET="",
        ONLYOFFICE_CALLBACK_ALLOWED_HOSTS=callback_allowlist or [],
        TERMINAL_SERVER_CONNECTIONS=terminal_connections or [],
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=config)),
        base_url="https://localhost/",
        headers={},
        query_params=query_params or {},
    )


def _fake_user():
    return SimpleNamespace(id="user-1", role="user")


def _extract_context_token(callback_url: str):
    query = parse_qs(urlparse(callback_url).query)
    values = query.get("context_token", [])
    return values[0] if values else None


def _terminal_connection():
    return {
        "id": "terminals",
        "url": "http://terminal.internal",
        "auth_type": "session",
        "enabled": True,
    }


class _FakeResponse:
    def __init__(self, *, status=200, body=b"", headers=None, json_body=None):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self._json_body = json_body if json_body is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return self._body

    async def json(self):
        return self._json_body


@pytest.mark.asyncio
async def test_create_onlyoffice_terminal_edit_session_returns_editable_config(monkeypatch):
    monkeypatch.setattr(
        onlyoffice_mod,
        "_get_terminal_connection",
        lambda request, terminal_server_id, user: _terminal_connection(),
    )

    response = await onlyoffice_mod.create_onlyoffice_session(
        onlyoffice_mod.OnlyOfficeSessionForm(
            source_type="terminal",
            terminal_server_id="terminals",
            terminal_file_path="/workspace/demo.docx",
            mode="edit",
        ),
        _fake_request(),
        user=_fake_user(),
        db=None,
    )

    assert response["config"]["document"]["permissions"]["edit"] is True
    assert response["config"]["editorConfig"]["mode"] == "edit"
    callback_url = response["config"]["editorConfig"]["callbackUrl"]
    assert urlparse(callback_url).path == "/api/v1/onlyoffice/callback/terminal"


@pytest.mark.asyncio
async def test_terminal_edit_session_embeds_callback_context(monkeypatch):
    monkeypatch.setattr(
        onlyoffice_mod,
        "_get_terminal_connection",
        lambda request, terminal_server_id, user: _terminal_connection(),
    )

    response = await onlyoffice_mod.create_onlyoffice_session(
        onlyoffice_mod.OnlyOfficeSessionForm(
            source_type="terminal",
            terminal_server_id="terminals",
            terminal_file_path="/workspace/demo.docx",
            mode="edit",
        ),
        _fake_request(),
        user=_fake_user(),
        db=None,
    )

    callback_token = _extract_context_token(response["config"]["editorConfig"]["callbackUrl"])
    assert callback_token

    decoded = onlyoffice_mod.decode_token(callback_token)
    assert decoded is not None
    assert decoded["scope"] == "onlyoffice:terminal_callback"
    assert decoded["terminal_server_id"] == "terminals"
    assert decoded["terminal_file_path"] == "/workspace/demo.docx"
    assert decoded["user_id"] == "user-1"
    assert decoded["document_key"] == response["config"]["document"]["key"]
    assert isinstance(decoded.get("session_signal"), str) and decoded["session_signal"]
    assert isinstance(decoded.get("session_proxy_token"), str)


@pytest.mark.asyncio
async def test_create_onlyoffice_file_edit_session_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        await onlyoffice_mod.create_onlyoffice_session(
            onlyoffice_mod.OnlyOfficeSessionForm(
                source_type="file",
                file_id="file-1",
                mode="edit",
            ),
            _fake_request(),
            user=_fake_user(),
            db=None,
        )

    assert exc_info.value.status_code == 400
    assert "only enabled for terminal sources" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_terminal_callback_ignores_non_save_status(monkeypatch):
    context_token = onlyoffice_mod.create_token(
        {
            "scope": "onlyoffice:terminal_callback",
            "terminal_server_id": "terminals",
            "terminal_file_path": "/workspace/demo.docx",
            "user_id": "user-1",
            "session_signal": "sig-1",
            "document_key": "doc-key-1",
            "session_proxy_token": "session-proxy",
        },
        expires_delta=timedelta(minutes=5),
    )

    def _unexpected_session(*args, **kwargs):
        raise AssertionError("ClientSession should not be created for non-save statuses")

    monkeypatch.setattr(onlyoffice_mod.aiohttp, "ClientSession", _unexpected_session)

    result = await onlyoffice_mod.handle_onlyoffice_terminal_callback(
        onlyoffice_mod.OnlyOfficeCallbackForm(status=1),
        _fake_request(
            terminal_connections=[_terminal_connection()],
            query_params={"context_token": context_token},
        ),
    )

    assert result == {"error": 0}


@pytest.mark.asyncio
async def test_terminal_callback_save_downloads_and_writes_back_via_terminal_api(monkeypatch):
    call_log = []

    class _FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers=None):
            call_log.append(("get", url, headers, None))
            assert url == "https://onlyoffice.example/cache/edited.docx"
            return _FakeResponse(
                status=200,
                body=b"edited-document-binary",
                headers={"Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
            )

        def post(self, url, headers=None, data=None, json=None):
            call_log.append(("post", url, headers, {"data": data, "json": json}))
            if "/files/upload?" in url:
                return _FakeResponse(status=200, json_body={"path": "/workspace/demo.docx.onlyoffice-fixed.tmp.docx"})
            if url.endswith("/files/move"):
                assert json == {
                    "source": "/workspace/demo.docx.onlyoffice-fixed.tmp.docx",
                    "destination": "/workspace/demo.docx",
                }
                return _FakeResponse(status=200, json_body={"ok": True})
            raise AssertionError(f"Unexpected POST URL: {url}")

        def delete(self, url, headers=None):
            call_log.append(("delete", url, headers, None))
            assert "path=%2Fworkspace%2Fdemo.docx" in url
            return _FakeResponse(status=200, json_body={"ok": True})

    monkeypatch.setattr(onlyoffice_mod.aiohttp, "ClientSession", _FakeClientSession)
    monkeypatch.setattr(
        onlyoffice_mod,
        "uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )

    context_token = onlyoffice_mod.create_token(
        {
            "scope": "onlyoffice:terminal_callback",
            "terminal_server_id": "terminals",
            "terminal_file_path": "/workspace/demo.docx",
            "user_id": "user-1",
            "session_signal": "sig-1",
            "document_key": "doc-key-1",
            "session_proxy_token": "session-proxy",
        },
        expires_delta=timedelta(minutes=5),
    )

    result = await onlyoffice_mod.handle_onlyoffice_terminal_callback(
        onlyoffice_mod.OnlyOfficeCallbackForm(
            status=2,
            key="doc-key-1",
            url="https://onlyoffice.example/cache/edited.docx",
        ),
        _fake_request(
            terminal_connections=[_terminal_connection()],
            callback_allowlist=["onlyoffice.example"],
            query_params={"context_token": context_token},
        ),
    )

    assert result == {"error": 0}
    assert len(call_log) == 4
    assert call_log[0][0] == "get"
    assert "/files/upload?directory=%2Fworkspace" in call_log[1][1]
    assert call_log[2][0] == "delete"
    assert call_log[3][0] == "post"
    assert call_log[3][1].endswith("/files/move")
