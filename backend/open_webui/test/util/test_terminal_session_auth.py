import datetime as dt
from types import SimpleNamespace

import pytest

from open_webui.routers import terminals as terminals_mod
from open_webui.utils import auth as auth_mod
from open_webui.utils import tools as tools_mod


def _user():
    return SimpleNamespace(id='user-1', role='user')


def _request(*, headers=None, cookies=None):
    connection = {
        'id': 'terminals',
        'url': 'http://terminal.internal',
        'auth_type': 'session',
        'enabled': True,
    }
    config = SimpleNamespace(TERMINAL_SERVER_CONNECTIONS=[connection])
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=config, redis=None)),
        state=SimpleNamespace(token=SimpleNamespace(credentials='state-token')),
        headers=headers or {},
        cookies=cookies or {},
        query_params={},
        method='GET',
        body=lambda: b'',
    )


async def _allowed_groups(_user_id):
    return []


async def _allow_access(*_args, **_kwargs):
    return True


def test_create_terminal_session_token_mints_short_lived_user_jwt():
    token = auth_mod.create_terminal_session_token(_user(), expires_delta=dt.timedelta(seconds=60))

    decoded = auth_mod.decode_token(token)

    assert decoded['id'] == 'user-1'
    assert 0 < decoded['exp'] - decoded['iat'] <= 60


@pytest.mark.asyncio
async def test_terminal_tool_resolution_uses_minted_jwt_instead_of_request_token(monkeypatch):
    captured = {}

    def fake_create_terminal_session_token(user):
        captured['minted_for_user'] = user.id
        return f'minted-token-for-{user.id}'

    async def fake_get_terminal_servers(request, *, session_token=None, oauth_token=None):
        captured['session_token'] = session_token
        return [
            {
                'id': 'terminals',
                'url': 'http://terminal.internal',
                'specs': [
                    {
                        'name': 'run_command',
                        'description': 'Run a command',
                        'parameters': {'type': 'object', 'properties': {}},
                    }
                ],
                'openapi': {
                    'paths': {
                        '/execute': {
                            'post': {
                                'operationId': 'run_command',
                                'requestBody': {'content': {'application/json': {}}},
                            }
                        }
                    }
                },
            }
        ]

    async def fake_get_terminal_cwd(_url, headers, cookies=None):
        captured['cwd_headers'] = headers
        captured['cwd_cookies'] = cookies
        return None

    async def fake_execute_tool_server(**kwargs):
        captured['execute_headers'] = kwargs['headers']
        captured['execute_cookies'] = kwargs['cookies']
        return {'ok': True}, {}

    monkeypatch.setattr(tools_mod.Groups, 'get_groups_by_member_id', _allowed_groups)
    monkeypatch.setattr(tools_mod, 'has_connection_access', _allow_access)
    monkeypatch.setattr(tools_mod, 'get_terminal_servers', fake_get_terminal_servers)
    monkeypatch.setattr(tools_mod, 'get_terminal_cwd', fake_get_terminal_cwd)
    monkeypatch.setattr(tools_mod, 'execute_tool_server', fake_execute_tool_server)
    monkeypatch.setattr(tools_mod, 'create_terminal_session_token', fake_create_terminal_session_token)

    request = _request(
        headers={'Authorization': 'Bearer header-token'},
        cookies={'token': 'cookie-token'},
    )

    result, _system_prompt = await tools_mod.get_terminal_tools(
        request,
        'terminals',
        _user(),
        extra_params={'__metadata__': {'chat_id': 'chat-1'}},
    )

    await result['run_command']['callable']()

    assert captured['minted_for_user'] == 'user-1'
    assert captured['session_token'] == 'minted-token-for-user-1'
    assert captured['cwd_headers']['Authorization'] == 'Bearer minted-token-for-user-1'
    assert captured['execute_headers']['Authorization'] == 'Bearer minted-token-for-user-1'
    assert captured['execute_cookies'] == {'token': 'cookie-token'}


@pytest.mark.asyncio
async def test_terminal_proxy_uses_minted_jwt_instead_of_request_token(monkeypatch):
    captured = {}

    def fake_create_terminal_session_token(user):
        captured['minted_for_user'] = user.id
        return f'minted-token-for-{user.id}'

    class FakeResponse:
        status = 200
        headers = {'content-type': 'application/json'}

        async def read(self):
            return b'{}'

        async def release(self):
            return None

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def request(self, **kwargs):
            captured['request'] = kwargs
            return FakeResponse()

        async def close(self):
            captured['closed'] = True

    monkeypatch.setattr(terminals_mod.Groups, 'get_groups_by_member_id', _allowed_groups)
    monkeypatch.setattr(terminals_mod, 'has_connection_access', _allow_access)
    monkeypatch.setattr(terminals_mod.aiohttp, 'ClientSession', FakeSession)
    monkeypatch.setattr(terminals_mod, 'create_terminal_session_token', fake_create_terminal_session_token)

    async def body():
        return b''

    request = _request(cookies={'token': 'cookie-token'})
    request.body = body

    response = await terminals_mod.proxy_terminal(
        'terminals',
        'openapi.json',
        request,
        user=_user(),
    )

    assert response.status_code == 200
    assert captured['minted_for_user'] == 'user-1'
    assert captured['request']['headers']['Authorization'] == 'Bearer minted-token-for-user-1'
    assert captured['request']['cookies'] == {'token': 'cookie-token'}
