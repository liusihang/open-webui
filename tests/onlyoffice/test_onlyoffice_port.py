"""Offline regression tests: real router/token/context code, mocked DB and HTTP.

No app startup, database migrations, production credentials or network requests.
The small source-loader keeps these tests independent of heavyweight app imports.
"""

import ast
import asyncio
import datetime as dt
import importlib.util
import re
import sys
import types
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, quote, urlparse

import aiohttp
import jwt
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend/open_webui'
TEST_SECRET = 'onlyoffice-offline-test-secret-not-a-live-credential'


def source_functions(relative_path, names, namespace):
    source = ast.parse((BACKEND / relative_path).read_text())
    selected = [
        node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    assert len(selected) == len(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(relative_path), 'exec'), namespace)
    return namespace


@pytest.fixture
def route(monkeypatch, tmp_path):
    config = {
        'onlyoffice.enable_preview': True,
        'onlyoffice.document_server_url': 'https://docs.example',
        'onlyoffice.public_base_url': 'https://webui.example',
        'onlyoffice.jwt_secret': '',
        'onlyoffice.file_token_expires_in': '5m',
        'onlyoffice.edit_callback_token_expires_in': '8h',
        'onlyoffice.callback_allowed_hosts': ['docs.example'],
        'webui.url': '',
        'terminal_server.connections': [
            {
                'id': 'terminal',
                'url': 'https://terminal.example',
                'auth_type': 'session',
                'enabled': True,
                'policy_id': 'office',
            }
        ],
    }
    user = SimpleNamespace(id='user-1', role='user')
    file_path = tmp_path / 'demo.docx'
    file_path.write_bytes(b'local test document')
    file = SimpleNamespace(
        id='file-1',
        user_id=user.id,
        path=str(file_path),
        filename='demo.docx',
        meta={'name': 'demo.docx'},
        hash='hash',
        updated_at=1,
    )

    def stub(name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    async def get_many(*keys):
        return {key: config[key] for key in keys if key in config}

    async def get(key, default=None):
        return config.get(key, default)

    config_model = SimpleNamespace(get_many=AsyncMock(side_effect=get_many), get=AsyncMock(side_effect=get))
    files = SimpleNamespace(get_file_by_id=AsyncMock(return_value=file))
    users = SimpleNamespace(get_user_by_id=AsyncMock(return_value=user))
    groups = SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[SimpleNamespace(id='group-1')]))
    read_access = AsyncMock(return_value=False)
    connection_access = AsyncMock(return_value=True)
    stub(
        'open_webui.constants', ERROR_MESSAGES=SimpleNamespace(NOT_FOUND='Not found', ACCESS_PROHIBITED='Access denied')
    )
    stub('open_webui.internal.db', get_async_session=lambda: None)
    stub('open_webui.models.config', Config=config_model)
    stub('open_webui.models.files', Files=files)
    stub('open_webui.models.groups', Groups=groups)
    stub('open_webui.models.users', Users=users)
    stub('open_webui.storage.provider', Storage=SimpleNamespace(get_file=lambda path: path))
    stub('open_webui.utils.access_control', has_connection_access=connection_access)
    stub('open_webui.utils.access_control.files', has_access_to_file=read_access)
    auth = source_functions(
        'utils/auth.py',
        {'create_token', 'decode_token'},
        {
            'jwt': jwt,
            'SESSION_SECRET': TEST_SECRET,
            'ALGORITHM': 'HS256',
            'uuid': uuid,
            'datetime': dt.datetime,
            'UTC': dt.UTC,
            'timedelta': dt.timedelta,
            'Union': __import__('typing').Union,
        },
    )
    stub(
        'open_webui.utils.auth',
        create_token=auth['create_token'],
        decode_token=auth['decode_token'],
        get_verified_user=lambda: None,
    )
    misc = source_functions('utils/misc.py', {'parse_duration'}, {'re': re, 'timedelta': dt.timedelta})
    stub('open_webui.utils.misc', parse_duration=misc['parse_duration'])
    terminals = source_functions(
        'utils/terminals.py',
        {
            'is_terminal_orchestrator',
            'get_terminal_server_url',
            'terminal_context_config',
            'terminal_context_available',
            'terminal_context_id',
        },
        {
            'TERMINAL_CONTEXT_TYPES': {'chat', 'automation'},
            'TERMINAL_CONTEXT_DEFAULT': 'default',
            'TERMINAL_CONTEXT_ID_SOURCES': {'chat': 'chat_id', 'automation': 'automation_id'},
            'quote': quote,
            'is_saved_chat_id': lambda value: bool(value and len(value) == 36),
        },
    )
    stub(
        'open_webui.utils.terminals',
        TERMINAL_CONTEXT_HEADER='X-Terminal-Context-Id',
        **{name: value for name, value in terminals.items() if callable(value) and name != 'is_saved_chat_id'},
    )
    spec = importlib.util.spec_from_file_location('onlyoffice_offline', BACKEND / 'routers/onlyoffice.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Every outbound request must be explicitly mocked by a test.
    monkeypatch.setattr(module.aiohttp, 'ClientSession', lambda **kwargs: pytest.fail('Unexpected HTTP request'))
    return SimpleNamespace(
        mod=module,
        config=config,
        user=user,
        file=file,
        files=files,
        users=users,
        groups=groups,
        read_access=read_access,
        connection_access=connection_access,
        config_model=config_model,
    )


def request(**kwargs):
    return SimpleNamespace(
        state=SimpleNamespace(),
        base_url='https://webui.example/',
        headers=kwargs.get('headers', {}),
        query_params=kwargs.get('query', {}),
    )


def run(awaitable):
    return asyncio.run(awaitable)


def context_token(route, **overrides):
    payload = {
        'scope': 'onlyoffice:terminal_callback',
        'terminal_server_id': 'terminal',
        'terminal_file_path': '/workspace/demo.docx',
        'user_id': 'user-1',
        'document_key': 'document-key',
        'session_proxy_token': 'synthetic-proxy',
        'chat_id': None,
        'context_id': None,
    }
    payload.update(overrides)
    return route.mod.create_token(payload, expires_delta=dt.timedelta(hours=1))


def save_callback(route, *, token=None, key='document-key', url='https://docs.example/cache/demo.docx'):
    return route.mod.handle_onlyoffice_terminal_callback(
        route.mod.OnlyOfficeCallbackForm(status=2, key=key, url=url),
        request(query={'context_token': token or context_token(route)}),
    )


def test_uploaded_file_preview_uses_async_file_api_and_is_not_editable(route):
    mod = route.mod
    response = run(
        mod.create_onlyoffice_session(
            mod.OnlyOfficeSessionForm(file_id='file-1'),
            request(),
            user=route.user,
            db=None,
        )
    )
    assert response['config']['document']['permissions']['edit'] is False
    assert response['config']['editorConfig']['mode'] == 'view'
    route.files.get_file_by_id.assert_awaited_once_with('file-1', db=None)
    token = parse_qs(urlparse(response['config']['document']['url']).query)['token'][0]
    file_response = run(mod.get_onlyoffice_file_content('file-1', token, request(), db=None))
    assert Path(file_response.path).read_bytes() == b'local test document'
    with pytest.raises(HTTPException) as error:
        run(
            mod.create_onlyoffice_session(
                mod.OnlyOfficeSessionForm(file_id='file-1', mode='edit'),
                request(),
                user=route.user,
                db=None,
            )
        )
    assert error.value.status_code == 400


@pytest.mark.parametrize('allowed', [False, True])
def test_shared_file_permission_is_awaited(route, allowed):
    route.file.user_id = 'owner-2'
    route.read_access.return_value = allowed
    operation = route.mod.create_onlyoffice_session(
        route.mod.OnlyOfficeSessionForm(file_id='file-1'),
        request(),
        user=route.user,
        db=None,
    )
    if allowed:
        assert run(operation)['config']['document']['title'] == 'demo.docx'
    else:
        with pytest.raises(HTTPException) as error:
            run(operation)
        assert error.value.status_code == 404
    route.read_access.assert_awaited_once()


@pytest.mark.parametrize('scope,file_id', [('wrong', 'file-1'), ('onlyoffice:file', 'other')])
def test_file_content_rejects_wrong_token_scope_and_target(route, scope, file_id):
    token = route.mod.create_token({'scope': scope, 'file_id': file_id, 'user_id': 'user-1'}, dt.timedelta(minutes=1))
    with pytest.raises(HTTPException) as error:
        run(route.mod.get_onlyoffice_file_content('file-1', token, request(), db=None))
    assert error.value.status_code == 401
    route.files.get_file_by_id.assert_not_called()


def test_expired_file_token_is_rejected_before_file_lookup(route):
    token = route.mod.create_token(
        {'scope': 'onlyoffice:file', 'file_id': 'file-1', 'user_id': 'user-1'},
        dt.timedelta(seconds=-1),
    )
    with pytest.raises(HTTPException) as error:
        run(route.mod.get_onlyoffice_file_content('file-1', token, request(), db=None))
    assert error.value.status_code == 401
    route.files.get_file_by_id.assert_not_called()


def test_permission_revocation_after_file_session(route):
    token = route.mod.create_token(
        {'scope': 'onlyoffice:file', 'file_id': 'file-1', 'user_id': 'user-1'}, dt.timedelta(minutes=1)
    )
    route.file.user_id = 'owner-2'
    with pytest.raises(HTTPException) as error:
        run(route.mod.get_onlyoffice_file_content('file-1', token, request(), db=None))
    assert error.value.status_code == 404
    route.read_access.assert_awaited_once()


@pytest.mark.parametrize('mode', ['view', 'edit'])
def test_terminal_session_embeds_official_context_and_separate_ttl(route, mode):
    route.config['terminal_server.connections'][0]['config'] = {'contexts': {'chat': {'context_id': 'chat_id'}}}
    chat_id = '11111111-1111-1111-1111-111111111111'
    response = run(
        route.mod.create_onlyoffice_session(
            route.mod.OnlyOfficeSessionForm(
                source_type='terminal',
                terminal_server_id='terminal',
                terminal_file_path='/workspace/demo.docx',
                chat_id=chat_id,
                mode=mode,
            ),
            request(),
            user=route.user,
            db=None,
        )
    )
    config = response['config']
    preview_token = parse_qs(urlparse(config['document']['url']).query)['token'][0]
    preview = route.mod.decode_token(preview_token)
    assert preview['context_id'] == f'chat:{chat_id}'
    assert preview['chat_id'] == chat_id
    assert config['document']['permissions']['edit'] == (mode == 'edit')
    route.groups.get_groups_by_member_id.assert_awaited_with('user-1', include_inherited=True)
    if mode == 'edit':
        callback_token = parse_qs(urlparse(config['editorConfig']['callbackUrl']).query)['context_token'][0]
        callback = route.mod.decode_token(callback_token)
        assert callback['document_key'] == config['document']['key']
        assert callback['context_id'] == preview['context_id']
        assert callback['exp'] - preview['exp'] == 8 * 3600 - 5 * 60
    else:
        assert 'context_token' not in config['editorConfig']['callbackUrl']


@pytest.mark.parametrize('reason,status', [('disabled', 404), ('denied', 403), ('pending', 403), ('missing_chat', 409)])
def test_terminal_session_access_checks(route, reason, status):
    if reason == 'disabled':
        route.config['terminal_server.connections'][0]['enabled'] = False
    elif reason == 'denied':
        route.connection_access.return_value = False
    elif reason == 'pending':
        route.user.role = 'pending'
    else:
        route.config['terminal_server.connections'][0]['config'] = {'contexts': {'chat': {'context_id': 'chat_id'}}}
    with pytest.raises(HTTPException) as error:
        run(
            route.mod.create_onlyoffice_session(
                route.mod.OnlyOfficeSessionForm(
                    source_type='terminal', terminal_server_id='terminal', terminal_file_path='/workspace/demo.docx'
                ),
                request(),
                user=route.user,
                db=None,
            )
        )
    assert error.value.status_code == status


@pytest.mark.parametrize(
    'path', ['../a.docx', '/workspace/../a.docx', '%2e%2e/a.docx', '', '/a\x00.docx', '..\\a.docx']
)
def test_terminal_paths_reject_traversal_and_control_characters(route, path):
    with pytest.raises(HTTPException):
        route.mod._normalize_terminal_file_path(path)


@pytest.mark.parametrize(
    'url',
    [
        'https://evil.example/demo.docx',
        'https://docs.example.evil/demo.docx',
        'file:///workspace/demo.docx',
        'https://localhost/demo.docx',
        'http://127.0.0.1/demo.docx',
        'http://169.254.169.254/latest/meta-data',
        'https://user:password@docs.example/demo.docx',
    ],
)
def test_illegal_callback_url_never_fetches_or_writes(route, url):
    with pytest.raises(HTTPException) as error:
        run(save_callback(route, url=url))
    assert error.value.status_code == 400


@pytest.mark.parametrize('reason,status', [('expired', 401), ('key', 409), ('deleted_user', 401), ('revoked', 403)])
def test_save_callback_rejects_expiry_key_and_revoked_access(route, reason, status):
    token = context_token(route)
    key = 'document-key'
    if reason == 'expired':
        payload = route.mod.decode_token(token)
        token = route.mod.create_token(payload, dt.timedelta(seconds=-1))
    elif reason == 'key':
        key = 'other-key'
    elif reason == 'deleted_user':
        route.users.get_user_by_id.return_value = None
    else:
        route.connection_access.return_value = False
    with pytest.raises(HTTPException) as error:
        run(save_callback(route, token=token, key=key))
    assert error.value.status_code == status


def test_save_callback_passes_validated_identity_and_context(route, monkeypatch):
    route.config['terminal_server.connections'][0]['config'] = {'contexts': {'chat': {'context_id': 'chat_id'}}}
    chat_id = '11111111-1111-1111-1111-111111111111'
    token = context_token(route, chat_id=chat_id, context_id=f'chat:{chat_id}')
    download = AsyncMock(return_value=(b'edited', 'application/octet-stream'))
    replace = AsyncMock()
    monkeypatch.setattr(route.mod, '_download_onlyoffice_callback_blob', download)
    monkeypatch.setattr(route.mod, '_replace_terminal_file_via_temp_upload', replace)
    assert run(save_callback(route, token=token)) == {'error': 0}
    assert replace.await_args.kwargs['context_id'] == f'chat:{chat_id}'
    assert replace.await_args.kwargs['user_id'] == 'user-1'
    assert replace.await_args.kwargs['terminal_file_path'] == '/workspace/demo.docx'
    assert replace.await_args.kwargs['session_proxy_token'] == 'synthetic-proxy'


def test_changed_terminal_context_rejected(route):
    route.config['terminal_server.connections'][0]['config'] = {'contexts': {'chat': {'context_id': 'chat_id'}}}
    with pytest.raises(HTTPException) as error:
        run(save_callback(route))
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    'case,status', [('missing', 401), ('invalid', 401), ('expired', 401), ('unsigned_fields', 400)]
)
def test_callback_jwt_cannot_be_forged_or_supply_unsigned_save_fields(route, case, status):
    route.config['onlyoffice.jwt_secret'] = 'synthetic-document-server-secret'
    token = None
    if case == 'invalid':
        token = jwt.encode({'status': 2}, 'incorrect-secret-for-this-offline-test', algorithm='HS256')
    elif case == 'expired':
        token = jwt.encode({'status': 2, 'exp': 1}, route.config['onlyoffice.jwt_secret'], algorithm='HS256')
    elif case == 'unsigned_fields':
        token = jwt.encode({'key': 'document-key'}, route.config['onlyoffice.jwt_secret'], algorithm='HS256')
    form = route.mod.OnlyOfficeCallbackForm(
        status=2, key='document-key', url='https://docs.example/demo.docx', token=token
    )
    with pytest.raises(HTTPException) as error:
        run(route.mod.handle_onlyoffice_terminal_callback(form, request(query={'context_token': context_token(route)})))
    assert error.value.status_code == status


def test_signed_nested_callback_payload_is_supported(route, monkeypatch):
    secret = route.config['onlyoffice.jwt_secret'] = 'synthetic-document-server-secret'
    signed = jwt.encode(
        {'payload': {'status': 2, 'key': 'document-key', 'url': 'https://docs.example/signed.docx'}},
        secret,
        algorithm='HS256',
    )
    download = AsyncMock(return_value=(b'edited', None))
    monkeypatch.setattr(route.mod, '_download_onlyoffice_callback_blob', download)
    monkeypatch.setattr(route.mod, '_replace_terminal_file_via_temp_upload', AsyncMock())
    result = run(
        route.mod.handle_onlyoffice_terminal_callback(
            route.mod.OnlyOfficeCallbackForm(status=2, key='evil', url='https://evil.example/wrong', token=signed),
            request(query={'context_token': context_token(route)}),
        )
    )
    assert result == {'error': 0}
    download.assert_awaited_once_with('https://docs.example/signed.docx')


class FakeResponse:
    def __init__(self, *, status=200, headers=None, chunks=None, json_body=None):
        self.status, self.headers = status, headers or {}
        self.chunks, self.json_body = chunks or [b'document'], json_body or {}
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk

    async def read(self):
        return b'upstream response'

    async def json(self):
        return self.json_body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, **kwargs):
        return self.request('get', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('post', url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request('delete', url, **kwargs)


@pytest.mark.parametrize(
    'response,status',
    [
        (FakeResponse(status=302), 400),
        (FakeResponse(headers={'Content-Length': str(101 * 1024 * 1024)}), 413),
        (FakeResponse(chunks=[b'123456', b'789012']), 413),
    ],
)
def test_callback_download_rejects_redirects_and_size_limits(route, monkeypatch, response, status):
    if status == 413 and not response.headers:
        monkeypatch.setattr(route.mod, 'ONLYOFFICE_EDIT_CALLBACK_MAX_BYTES', 10)
    session = FakeSession([response])
    monkeypatch.setattr(route.mod.aiohttp, 'ClientSession', lambda **kwargs: session)
    with pytest.raises(HTTPException) as error:
        run(route.mod._download_onlyoffice_callback_blob('https://docs.example/cache/demo.docx'))
    assert error.value.status_code == status
    assert session.calls[0][2]['allow_redirects'] is False


def test_temp_upload_writeback_preserves_identity_and_context(route, monkeypatch):
    session = FakeSession([FakeResponse(), FakeResponse(), FakeResponse(), FakeResponse()])
    monkeypatch.setattr(route.mod.aiohttp, 'ClientSession', lambda **kwargs: session)
    run(
        route.mod._replace_terminal_file_via_temp_upload(
            route.config['terminal_server.connections'][0],
            '/workspace/demo.docx',
            'user-1',
            'synthetic-proxy',
            b'edited',
            None,
            context_id='chat:example',
        )
    )
    assert [call[0] for call in session.calls] == ['post', 'post', 'post', 'delete']
    for _, url, kwargs in session.calls:
        assert url.startswith('https://terminal.example/p/office/files/')
        assert kwargs['headers']['X-User-Id'] == 'user-1'
        assert kwargs['headers']['Authorization'] == 'Bearer synthetic-proxy'
        assert kwargs['headers']['X-Terminal-Context-Id'] == 'chat:example'
    backup = session.calls[1][2]['json']
    install = session.calls[2][2]['json']
    assert backup['source'] == install['destination'] == '/workspace/demo.docx'
    assert '.backup.docx' in backup['destination']
    assert '.tmp.docx' in install['source']


@pytest.mark.parametrize(
    'install_response', [FakeResponse(status=500), aiohttp.ClientConnectionError('lost install response')]
)
def test_temp_install_failure_restores_original(route, monkeypatch, install_response):
    session = FakeSession([FakeResponse(), FakeResponse(), install_response, FakeResponse(), FakeResponse()])
    monkeypatch.setattr(route.mod.aiohttp, 'ClientSession', lambda **kwargs: session)
    with pytest.raises(HTTPException) as error:
        run(
            route.mod._replace_terminal_file_via_temp_upload(
                route.config['terminal_server.connections'][0],
                '/workspace/demo.docx',
                'user-1',
                'synthetic-proxy',
                b'edited',
                None,
            )
        )
    assert error.value.status_code == 502
    assert 'backup_restore=ok' in error.value.detail
    assert session.calls[3][2]['json']['source'] == session.calls[1][2]['json']['destination']


def test_onlyoffice_config_and_mount_are_wired_without_exposing_secret():
    config_source = (BACKEND / 'config.py').read_text()
    main_source = (BACKEND / 'main.py').read_text()
    assert "'onlyoffice.enable_preview': ENABLE_ONLYOFFICE_PREVIEW" in config_source
    assert "'onlyoffice.jwt_secret': ONLYOFFICE_JWT_SECRET" in config_source
    assert "app.include_router(onlyoffice.router, prefix='/api/v1/onlyoffice'" in main_source
    function = next(
        node
        for node in ast.parse(main_source).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'get_app_config'
    )
    config_api_source = ast.unparse(function)
    assert 'enable_onlyoffice_preview' in config_api_source
    assert 'onlyoffice.jwt_secret' not in config_api_source
    assert 'ONLYOFFICE_JWT_SECRET' not in config_api_source


def test_onlyoffice_router_http_session_and_validation(route):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(route.mod.router, prefix='/api/v1/onlyoffice')
    app.dependency_overrides[route.mod.get_verified_user] = lambda: route.user
    client = TestClient(app)
    response = client.post('/api/v1/onlyoffice/session', json={'file_id': 'file-1', 'mode': 'view'})
    assert response.status_code == 200
    assert response.json()['config']['document']['permissions']['edit'] is False
    response = client.post('/api/v1/onlyoffice/session', json={'file_id': 'file-1', 'mode': 'edit'})
    assert response.status_code == 400
    response = client.post('/api/v1/onlyoffice/session', json={'source_type': 'invalid'})
    assert response.status_code == 422


def test_missing_original_falls_back_with_not_found(route):
    route.file.path = None
    with pytest.raises(HTTPException) as error:
        run(
            route.mod.create_onlyoffice_session(
                route.mod.OnlyOfficeSessionForm(file_id='file-1'),
                request(),
                user=route.user,
                db=None,
            )
        )
    assert error.value.status_code == 404


@pytest.mark.parametrize('ttl', ['0s', '-1s', 'invalid'])
def test_non_positive_or_invalid_file_ttl_is_rejected(route, ttl):
    route.config['onlyoffice.file_token_expires_in'] = ttl
    with pytest.raises(HTTPException) as error:
        run(
            route.mod.create_onlyoffice_session(
                route.mod.OnlyOfficeSessionForm(file_id='file-1'),
                request(),
                user=route.user,
                db=None,
            )
        )
    assert error.value.status_code == 500


def test_terminal_download_uses_official_context_and_no_redirects(route, monkeypatch):
    chat_id = '11111111-1111-1111-1111-111111111111'
    route.config['terminal_server.connections'][0]['config'] = {'contexts': {'chat': {'context_id': 'chat_id'}}}
    token = route.mod.create_token(
        {
            'scope': 'onlyoffice:terminal_file',
            'terminal_server_id': 'terminal',
            'terminal_file_path': '/workspace/demo.docx',
            'user_id': 'user-1',
            'chat_id': chat_id,
            'context_id': f'chat:{chat_id}',
            'session_proxy_token': 'synthetic-proxy',
        },
        dt.timedelta(minutes=5),
    )
    session = FakeSession([FakeResponse(headers={'Content-Type': 'application/octet-stream'})])
    monkeypatch.setattr(route.mod.aiohttp, 'ClientSession', lambda **kwargs: session)
    response = run(route.mod.get_onlyoffice_terminal_file_content(token, request(), db=None))
    assert response.body == b'upstream response'
    _, url, options = session.calls[0]
    assert url.startswith('https://terminal.example/p/office/files/view')
    assert options['headers']['Authorization'] == 'Bearer synthetic-proxy'
    assert options['headers']['X-Terminal-Context-Id'] == f'chat:{chat_id}'
    assert options['allow_redirects'] is False


def test_disabled_preview_is_not_exposed_as_working_route(route):
    route.config['onlyoffice.enable_preview'] = False
    with pytest.raises(HTTPException) as error:
        run(
            route.mod.create_onlyoffice_session(
                route.mod.OnlyOfficeSessionForm(file_id='file-1'),
                request(),
                user=route.user,
                db=None,
            )
        )
    assert error.value.status_code == 404
    route.files.get_file_by_id.assert_not_called()
