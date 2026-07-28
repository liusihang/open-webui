from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import aiohttp
import jwt
import pytest
from fastapi import HTTPException

from open_webui.routers import onlyoffice as onlyoffice_mod


def _fake_request(
    *,
    terminal_connections=None,
    callback_allowlist=None,
    query_params=None,
    callback_ttl=None,
    onlyoffice_jwt_secret='',
):
    config = SimpleNamespace(
        ENABLE_ONLYOFFICE_PREVIEW=True,
        ONLYOFFICE_DOCUMENT_SERVER_URL='http://onlyoffice.internal',
        ONLYOFFICE_FILE_TOKEN_EXPIRES_IN='5m',
        ONLYOFFICE_PUBLIC_BASE_URL='https://webui.example',
        WEBUI_URL='',
        ONLYOFFICE_JWT_SECRET=onlyoffice_jwt_secret,
        ONLYOFFICE_EDIT_CALLBACK_TOKEN_EXPIRES_IN=callback_ttl,
        ONLYOFFICE_CALLBACK_ALLOWED_HOSTS=callback_allowlist or [],
        TERMINAL_SERVER_CONNECTIONS=terminal_connections or [],
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=config)),
        base_url='https://localhost/',
        headers={},
        query_params=query_params or {},
    )


def _fake_user():
    return SimpleNamespace(id='user-1', role='user')


def _extract_context_token(callback_url: str):
    query = parse_qs(urlparse(callback_url).query)
    values = query.get('context_token', [])
    return values[0] if values else None


def _extract_query_token(url: str, name: str):
    query = parse_qs(urlparse(url).query)
    values = query.get(name, [])
    return values[0] if values else None


def _terminal_connection():
    return {
        'id': 'terminals',
        'url': 'http://terminal.internal',
        'auth_type': 'session',
        'enabled': True,
    }


async def _terminal_connection_async(request, terminal_server_id, user):
    return _terminal_connection()


async def _fake_user_by_id_async(user_id, db=None):
    return _fake_user()


@pytest.mark.asyncio
async def test_get_terminal_connection_awaits_group_and_access_lookup(monkeypatch):
    groups_called = False
    access_args = None

    async def _groups_for_user(user_id):
        nonlocal groups_called
        groups_called = True
        assert user_id == 'user-1'
        return [SimpleNamespace(id='group-1')]

    async def _allow_access(user, connection, user_group_ids):
        nonlocal access_args
        access_args = (user.id, connection['id'], set(user_group_ids))
        return True

    monkeypatch.setattr(onlyoffice_mod.Groups, 'get_groups_by_member_id', _groups_for_user)
    monkeypatch.setattr(onlyoffice_mod, 'has_connection_access', _allow_access)

    connection = await onlyoffice_mod._get_terminal_connection(
        _fake_request(terminal_connections=[_terminal_connection()]),
        'terminals',
        _fake_user(),
    )

    assert connection['id'] == 'terminals'
    assert groups_called is True
    assert access_args == ('user-1', 'terminals', {'group-1'})


def _make_context_token(*, expires_delta=timedelta(hours=2)):
    return onlyoffice_mod.create_token(
        {
            'scope': 'onlyoffice:terminal_callback',
            'terminal_server_id': 'terminals',
            'terminal_file_path': '/workspace/demo.docx',
            'user_id': 'user-1',
            'session_signal': 'sig-1',
            'document_key': 'doc-key-1',
            'session_proxy_token': 'session-proxy',
        },
        expires_delta=expires_delta,
    )


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, chunk_size):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, *, status=200, body=b'', body_chunks=None, headers=None, json_body=None):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self._json_body = json_body if json_body is not None else {}
        self.content = _FakeContent(body_chunks if body_chunks is not None else [body])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return self._body

    async def json(self):
        return self._json_body


class _DownloadOnlyClientSession:
    def __init__(self, response):
        self._response = response
        self.get_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self._response


@pytest.mark.asyncio
async def test_create_onlyoffice_terminal_edit_session_returns_editable_config(monkeypatch):
    monkeypatch.setattr(
        onlyoffice_mod,
        '_get_terminal_connection',
        _terminal_connection_async,
    )

    response = await onlyoffice_mod.create_onlyoffice_session(
        onlyoffice_mod.OnlyOfficeSessionForm(
            source_type='terminal',
            terminal_server_id='terminals',
            terminal_file_path='/workspace/demo.docx',
            mode='edit',
        ),
        _fake_request(),
        user=_fake_user(),
        db=None,
    )

    assert response['config']['document']['permissions']['edit'] is True
    assert response['config']['editorConfig']['mode'] == 'edit'
    assert response['config']['type'] == 'desktop'
    assert response['config']['editorConfig']['customization']['forcesave'] is True
    callback_url = response['config']['editorConfig']['callbackUrl']
    assert urlparse(callback_url).path == '/api/v1/onlyoffice/callback/terminal'


@pytest.mark.asyncio
async def test_create_onlyoffice_terminal_view_session_does_not_force_save(monkeypatch):
    monkeypatch.setattr(
        onlyoffice_mod,
        '_get_terminal_connection',
        _terminal_connection_async,
    )

    response = await onlyoffice_mod.create_onlyoffice_session(
        onlyoffice_mod.OnlyOfficeSessionForm(
            source_type='terminal',
            terminal_server_id='terminals',
            terminal_file_path='/workspace/demo.docx',
            mode='view',
        ),
        _fake_request(),
        user=_fake_user(),
        db=None,
    )

    customization = response['config']['editorConfig']['customization']
    assert response['config']['editorConfig']['mode'] == 'view'
    assert 'forcesave' not in customization


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('file_ext', 'document_type'),
    [
        ('pdf', 'pdf'),
        ('docm', 'word'),
        ('dot', 'word'),
        ('dotm', 'word'),
        ('dotx', 'word'),
        ('odt', 'word'),
        ('ott', 'word'),
        ('ods', 'cell'),
        ('ots', 'cell'),
        ('xlsb', 'cell'),
        ('xlsm', 'cell'),
        ('xlt', 'cell'),
        ('xltm', 'cell'),
        ('xltx', 'cell'),
        ('odp', 'slide'),
        ('otp', 'slide'),
        ('pot', 'slide'),
        ('potm', 'slide'),
        ('potx', 'slide'),
        ('pps', 'slide'),
        ('ppsm', 'slide'),
        ('ppsx', 'slide'),
        ('pptm', 'slide'),
        ('rtf', 'word'),
    ],
)
async def test_create_onlyoffice_terminal_view_session_supports_common_formats(
    monkeypatch, file_ext, document_type
):
    monkeypatch.setattr(
        onlyoffice_mod,
        '_get_terminal_connection',
        _terminal_connection_async,
    )

    response = await onlyoffice_mod.create_onlyoffice_session(
        onlyoffice_mod.OnlyOfficeSessionForm(
            source_type='terminal',
            terminal_server_id='terminals',
            terminal_file_path=f'/workspace/demo.{file_ext}',
            mode='view',
        ),
        _fake_request(),
        user=_fake_user(),
        db=None,
    )

    assert response['config']['document']['fileType'] == file_ext
    assert response['config']['documentType'] == document_type


@pytest.mark.asyncio
async def test_terminal_edit_session_embeds_callback_context(monkeypatch):
    monkeypatch.setattr(
        onlyoffice_mod,
        '_get_terminal_connection',
        _terminal_connection_async,
    )

    response = await onlyoffice_mod.create_onlyoffice_session(
        onlyoffice_mod.OnlyOfficeSessionForm(
            source_type='terminal',
            terminal_server_id='terminals',
            terminal_file_path='/workspace/demo.docx',
            mode='edit',
        ),
        _fake_request(),
        user=_fake_user(),
        db=None,
    )

    callback_token = _extract_context_token(response['config']['editorConfig']['callbackUrl'])
    assert callback_token

    decoded = onlyoffice_mod.decode_token(callback_token)
    assert decoded is not None
    assert decoded['scope'] == 'onlyoffice:terminal_callback'
    assert decoded['terminal_server_id'] == 'terminals'
    assert decoded['terminal_file_path'] == '/workspace/demo.docx'
    assert decoded['user_id'] == 'user-1'
    assert decoded['document_key'] == response['config']['document']['key']
    assert isinstance(decoded.get('session_signal'), str) and decoded['session_signal']
    assert isinstance(decoded.get('session_proxy_token'), str)


@pytest.mark.asyncio
async def test_terminal_edit_session_uses_separate_callback_ttl(monkeypatch):
    monkeypatch.setattr(
        onlyoffice_mod,
        '_get_terminal_connection',
        _terminal_connection_async,
    )

    response = await onlyoffice_mod.create_onlyoffice_session(
        onlyoffice_mod.OnlyOfficeSessionForm(
            source_type='terminal',
            terminal_server_id='terminals',
            terminal_file_path='/workspace/demo.docx',
            mode='edit',
        ),
        _fake_request(callback_ttl='2h'),
        user=_fake_user(),
        db=None,
    )

    file_token = _extract_query_token(response['config']['document']['url'], 'token')
    context_token = _extract_context_token(response['config']['editorConfig']['callbackUrl'])
    assert file_token and context_token

    decoded_file_token = onlyoffice_mod.decode_token(file_token)
    decoded_context_token = onlyoffice_mod.decode_token(context_token)
    assert decoded_file_token is not None and decoded_context_token is not None

    assert decoded_context_token['exp'] > decoded_file_token['exp'] + 60 * 30
    callback_proxy_token = decoded_context_token.get('session_proxy_token')
    preview_proxy_token = decoded_file_token.get('session_proxy_token')
    assert isinstance(callback_proxy_token, str) and isinstance(preview_proxy_token, str)
    decoded_callback_proxy = onlyoffice_mod.decode_token(callback_proxy_token)
    decoded_preview_proxy = onlyoffice_mod.decode_token(preview_proxy_token)
    assert decoded_callback_proxy is not None and decoded_preview_proxy is not None
    assert decoded_callback_proxy['exp'] > decoded_preview_proxy['exp'] + 60 * 30


@pytest.mark.asyncio
async def test_terminal_file_content_awaits_connection_and_streams_upstream(monkeypatch):
    get_calls = []

    class _FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers=None, **kwargs):
            get_calls.append((url, headers, kwargs))
            assert url == 'http://terminal.internal/files/view?path=/workspace/demo.docx'
            assert headers == {
                'X-User-Id': 'user-1',
                'Authorization': 'Bearer session-proxy',
            }
            return _FakeResponse(
                status=200,
                body=b'docx-bytes',
                headers={
                    'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'Content-Disposition': 'attachment; filename="demo.docx"',
                },
            )

    token = onlyoffice_mod.create_token(
        {
            'scope': 'onlyoffice:terminal_file',
            'terminal_server_id': 'terminals',
            'terminal_file_path': '/workspace/demo.docx',
            'user_id': 'user-1',
            'mode': 'view',
            'session_signal': 'sig-1',
            'session_proxy_token': 'session-proxy',
        },
        expires_delta=timedelta(minutes=5),
    )

    monkeypatch.setattr(onlyoffice_mod.Users, 'get_user_by_id', _fake_user_by_id_async)
    monkeypatch.setattr(
        onlyoffice_mod,
        '_get_terminal_connection',
        _terminal_connection_async,
    )
    monkeypatch.setattr(onlyoffice_mod.aiohttp, 'ClientSession', _FakeClientSession)

    response = await onlyoffice_mod.get_onlyoffice_terminal_file_content(
        token,
        _fake_request(),
        db=None,
    )

    assert response.body == b'docx-bytes'
    assert response.media_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    assert response.headers['content-disposition'] == 'attachment; filename="demo.docx"'
    assert len(get_calls) == 1


@pytest.mark.asyncio
async def test_create_onlyoffice_file_edit_session_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        await onlyoffice_mod.create_onlyoffice_session(
            onlyoffice_mod.OnlyOfficeSessionForm(
                source_type='file',
                file_id='file-1',
                mode='edit',
            ),
            _fake_request(),
            user=_fake_user(),
            db=None,
        )

    assert exc_info.value.status_code == 400
    assert 'only enabled for terminal sources' in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_terminal_callback_ignores_non_save_status(monkeypatch):
    context_token = _make_context_token(expires_delta=timedelta(minutes=5))

    def _unexpected_session(*args, **kwargs):
        raise AssertionError('ClientSession should not be created for non-save statuses')

    monkeypatch.setattr(onlyoffice_mod.aiohttp, 'ClientSession', _unexpected_session)

    result = await onlyoffice_mod.handle_onlyoffice_terminal_callback(
        onlyoffice_mod.OnlyOfficeCallbackForm(status=1),
        _fake_request(
            terminal_connections=[_terminal_connection()],
            query_params={'context_token': context_token},
        ),
    )

    assert result == {'error': 0}


@pytest.mark.asyncio
@pytest.mark.parametrize('save_status', [2, 6])
async def test_terminal_callback_save_downloads_and_writes_back_via_terminal_api(
    monkeypatch, save_status
):
    call_log = []

    class _FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers=None, **kwargs):
            call_log.append(('get', url, headers, kwargs))
            assert url == 'https://onlyoffice.example/cache/edited.docx'
            return _FakeResponse(
                status=200,
                body=b'edited-document-binary',
                headers={'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
            )

        def post(self, url, headers=None, data=None, json=None):
            call_log.append(('post', url, headers, {'data': data, 'json': json}))
            if '/files/upload?' in url:
                return _FakeResponse(status=200, json_body={'path': '/workspace/.upload-random-name.docx'})
            if url.endswith('/files/move'):
                if json == {'source': '/workspace/demo.docx', 'destination': '/workspace/demo.docx.onlyoffice-fixed.backup.docx'}:
                    return _FakeResponse(status=200, json_body={'ok': True})
                if json == {'source': '/workspace/.upload-random-name.docx', 'destination': '/workspace/demo.docx'}:
                    return _FakeResponse(status=200, json_body={'ok': True})
                return _FakeResponse(status=200, json_body={'ok': True})
            raise AssertionError(f'Unexpected POST URL: {url}')

        def delete(self, url, headers=None):
            call_log.append(('delete', url, headers, None))
            assert 'path=%2Fworkspace%2Fdemo.docx.onlyoffice-fixed.backup.docx' in url
            return _FakeResponse(status=200, json_body={'ok': True})

    monkeypatch.setattr(onlyoffice_mod.aiohttp, 'ClientSession', _FakeClientSession)
    monkeypatch.setattr(
        onlyoffice_mod,
        'uuid4',
        lambda: SimpleNamespace(hex='fixed'),
    )

    context_token = _make_context_token(expires_delta=timedelta(minutes=5))

    result = await onlyoffice_mod.handle_onlyoffice_terminal_callback(
        onlyoffice_mod.OnlyOfficeCallbackForm(
            status=save_status,
            key='doc-key-1',
            url='https://onlyoffice.example/cache/edited.docx',
        ),
        _fake_request(
            terminal_connections=[_terminal_connection()],
            callback_allowlist=['onlyoffice.example'],
            query_params={'context_token': context_token},
        ),
    )

    assert result == {'error': 0}
    assert len(call_log) == 5
    assert call_log[0][0] == 'get'
    assert '/files/upload?directory=%2Fworkspace' in call_log[1][1]
    assert call_log[2][0] == 'post'
    assert call_log[2][1].endswith('/files/move')
    assert call_log[2][3]['json'] == {
        'source': '/workspace/demo.docx',
        'destination': '/workspace/demo.docx.onlyoffice-fixed.backup.docx',
    }
    assert call_log[3][0] == 'post'
    assert call_log[3][1].endswith('/files/move')
    assert call_log[3][3]['json'] == {
        'source': '/workspace/.upload-random-name.docx',
        'destination': '/workspace/demo.docx',
    }
    assert call_log[4][0] == 'delete'
    assert 'path=%2Fworkspace%2Fdemo.docx.onlyoffice-fixed.backup.docx' in call_log[4][1]


@pytest.mark.asyncio
async def test_terminal_callback_rejects_callback_blob_redirect_to_loopback(monkeypatch):
    response = _FakeResponse(
        status=302,
        headers={'Location': 'http://127.0.0.1/latest/meta-data'},
    )
    session = _DownloadOnlyClientSession(response)
    writeback_calls = []

    async def _fake_writeback(**kwargs):
        writeback_calls.append(kwargs)

    monkeypatch.setattr(
        onlyoffice_mod.aiohttp,
        'ClientSession',
        lambda *args, **kwargs: session,
    )
    monkeypatch.setattr(onlyoffice_mod, '_replace_terminal_file_via_temp_upload', _fake_writeback)

    with pytest.raises(HTTPException) as exc_info:
        await onlyoffice_mod.handle_onlyoffice_terminal_callback(
            onlyoffice_mod.OnlyOfficeCallbackForm(
                status=2,
                key='doc-key-1',
                url='https://onlyoffice.example/cache/redirected.docx',
            ),
            _fake_request(
                terminal_connections=[_terminal_connection()],
                callback_allowlist=['onlyoffice.example'],
                query_params={'context_token': _make_context_token()},
            ),
        )

    assert exc_info.value.status_code == 400
    assert 'redirect' in str(exc_info.value.detail).lower()
    assert session.get_calls == [
        ('https://onlyoffice.example/cache/redirected.docx', {'allow_redirects': False})
    ]
    assert writeback_calls == []


@pytest.mark.asyncio
async def test_download_onlyoffice_callback_blob_rejects_oversized_content_length(monkeypatch):
    class _UnexpectedContent:
        async def iter_chunked(self, chunk_size):
            raise AssertionError('oversized Content-Length should be rejected before streaming')

    response = _FakeResponse(
        status=200,
        body=b'',
        headers={'Content-Length': '9'},
    )
    response.content = _UnexpectedContent()
    session = _DownloadOnlyClientSession(response)

    monkeypatch.setattr(onlyoffice_mod, 'ONLYOFFICE_EDIT_CALLBACK_MAX_BYTES', 8, raising=False)
    monkeypatch.setattr(
        onlyoffice_mod.aiohttp,
        'ClientSession',
        lambda *args, **kwargs: session,
    )

    with pytest.raises(HTTPException) as exc_info:
        await onlyoffice_mod._download_onlyoffice_callback_blob(
            'https://onlyoffice.example/cache/too-large.docx'
        )

    assert exc_info.value.status_code == 413
    assert 'exceeds' in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_download_onlyoffice_callback_blob_rejects_chunked_body_over_limit(monkeypatch):
    response = _FakeResponse(
        status=200,
        body_chunks=[b'12345', b'67890'],
        headers={'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
    )
    session = _DownloadOnlyClientSession(response)

    monkeypatch.setattr(onlyoffice_mod, 'ONLYOFFICE_EDIT_CALLBACK_MAX_BYTES', 8, raising=False)
    monkeypatch.setattr(
        onlyoffice_mod.aiohttp,
        'ClientSession',
        lambda *args, **kwargs: session,
    )

    with pytest.raises(HTTPException) as exc_info:
        await onlyoffice_mod._download_onlyoffice_callback_blob(
            'https://onlyoffice.example/cache/chunked-too-large.docx'
        )

    assert exc_info.value.status_code == 413
    assert 'exceeds' in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_terminal_callback_jwt_enabled_uses_outer_save_status_when_token_missing_status(
    monkeypatch,
):
    download_calls = []
    writeback_calls = []

    async def _fake_download(callback_url):
        download_calls.append(callback_url)
        return (b'edited-content', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    async def _fake_writeback(**kwargs):
        writeback_calls.append(kwargs)

    monkeypatch.setattr(onlyoffice_mod, '_download_onlyoffice_callback_blob', _fake_download)
    monkeypatch.setattr(onlyoffice_mod, '_replace_terminal_file_via_temp_upload', _fake_writeback)

    context_token = _make_context_token(expires_delta=timedelta(minutes=5))
    callback_jwt_secret = 'onlyoffice-callback-secret'
    callback_payload_token = jwt.encode(
        {
            'key': 'doc-key-1',
            'url': 'https://onlyoffice.example/cache/edited.docx',
            'context_token': context_token,
        },
        callback_jwt_secret,
        algorithm='HS256',
    )

    result = await onlyoffice_mod.handle_onlyoffice_terminal_callback(
        onlyoffice_mod.OnlyOfficeCallbackForm(
            status=2,
            key='doc-key-1',
            url='https://onlyoffice.example/cache/edited.docx',
            token=callback_payload_token,
        ),
        _fake_request(
            terminal_connections=[_terminal_connection()],
            callback_allowlist=['onlyoffice.example'],
            query_params={'context_token': context_token},
            onlyoffice_jwt_secret=callback_jwt_secret,
        ),
    )

    assert result == {'error': 0}
    assert download_calls == ['https://onlyoffice.example/cache/edited.docx']
    assert len(writeback_calls) == 1
    assert writeback_calls[0]['terminal_file_path'] == '/workspace/demo.docx'
    assert writeback_calls[0]['user_id'] == 'user-1'


@pytest.mark.asyncio
async def test_terminal_callback_saveback_transport_exception_returns_502(monkeypatch):
    async def _fake_download(*args, **kwargs):
        return b'edited-document-binary', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

    async def _failing_saveback(*args, **kwargs):
        raise aiohttp.ClientConnectionError('terminal transport down')

    monkeypatch.setattr(onlyoffice_mod, '_download_onlyoffice_callback_blob', _fake_download)
    monkeypatch.setattr(onlyoffice_mod, '_replace_terminal_file_via_temp_upload', _failing_saveback)

    with pytest.raises(HTTPException) as exc_info:
        await onlyoffice_mod.handle_onlyoffice_terminal_callback(
            onlyoffice_mod.OnlyOfficeCallbackForm(
                status=2,
                key='doc-key-1',
                url='https://onlyoffice.example/cache/edited.docx',
            ),
            _fake_request(
                terminal_connections=[_terminal_connection()],
                callback_allowlist=['onlyoffice.example'],
                query_params={'context_token': _make_context_token()},
            ),
        )

    assert exc_info.value.status_code == 502
