import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest
from open_webui.routers import terminals as terminals_mod
from open_webui.utils import auth as auth_mod
from open_webui.utils import tools as tools_mod


def _user():
    return SimpleNamespace(id='user-1', role='user')


def _connection():
    return {
        'id': 'terminals',
        'url': 'http://terminal.internal',
        'auth_type': 'session',
        'enabled': True,
    }


def _request(*, headers=None, cookies=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=None)),
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


async def _config_get(key, default=None):
    if key == 'terminal_server.connections':
        return [_connection()]
    return default


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
        return {'id': 'proc-1', 'status': 'done'}, {}

    monkeypatch.setattr(tools_mod.Config, 'get', _config_get)
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
async def test_terminal_run_command_cancel_kills_exact_process_with_same_session(monkeypatch):
    calls = []
    status_started = asyncio.Event()

    async def fake_execute_tool_server(**kwargs):
        calls.append(kwargs)
        if kwargs['name'] == 'run_command':
            return {'id': 'proc-123', 'status': 'running'}, {'launch': 'headers'}
        if kwargs['name'] == 'get_process_status':
            status_started.set()
            await asyncio.Event().wait()
        if kwargs['name'] == 'kill_process':
            return {'status': 'killed'}, {}
        raise AssertionError(kwargs['name'])

    monkeypatch.setattr(tools_mod, 'execute_tool_server', fake_execute_tool_server)
    headers = {
        'Authorization': 'Bearer session-token',
        'Content-Type': 'application/json',
        'X-User-Id': 'user-1',
        'X-Session-Id': 'chat-1',
    }
    cookies = {'token': 'cookie-token'}

    task = asyncio.create_task(
        tools_mod.execute_terminal_tool_server(
            url='http://terminal.internal',
            headers=headers,
            cookies=cookies,
            name='run_command',
            params={'command': 'sleep 180', 'wait': 180, 'tail': 20},
            server_data={'openapi': {}},
        )
    )
    await status_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [(call['name'], call['params']) for call in calls] == [
        ('run_command', {'command': 'sleep 180', 'wait': 0, 'tail': 20}),
        ('get_process_status', {'process_id': 'proc-123', 'wait': 180, 'tail': 20}),
        ('kill_process', {'process_id': 'proc-123'}),
    ]
    assert all(call['headers'] == headers for call in calls)
    assert all(call['cookies'] == cookies for call in calls)


@pytest.mark.asyncio
async def test_terminal_run_command_cancel_during_launch_waits_for_id_then_kills(monkeypatch):
    launch_started = asyncio.Event()
    release_launch = asyncio.Event()
    killed = []

    async def fake_execute_tool_server(**kwargs):
        if kwargs['name'] == 'run_command':
            launch_started.set()
            await release_launch.wait()
            return {'id': 'proc-delayed', 'status': 'running'}, {}
        if kwargs['name'] == 'kill_process':
            killed.append(kwargs['params']['process_id'])
            return {'status': 'killed'}, {}
        raise AssertionError(kwargs['name'])

    monkeypatch.setattr(tools_mod, 'execute_tool_server', fake_execute_tool_server)
    task = asyncio.create_task(
        tools_mod.execute_terminal_tool_server(
            url='http://terminal.internal',
            headers={'X-User-Id': 'user-1', 'X-Session-Id': 'chat-1'},
            cookies={},
            name='run_command',
            params={'command': 'sleep 180', 'wait': 180},
            server_data={'openapi': {}},
        )
    )
    await launch_started.wait()
    task.cancel()
    release_launch.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert killed == ['proc-delayed']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('params', 'expected_calls'),
    [
        (
            {'command': 'echo ok', 'wait': 30, 'tail': 5},
            [
                ('run_command', {'command': 'echo ok', 'wait': 0, 'tail': 5}),
                ('get_process_status', {'process_id': 'proc-ok', 'wait': 30, 'tail': 5}),
            ],
        ),
        (
            {'command': 'echo ok'},
            [
                ('run_command', {'command': 'echo ok', 'wait': 0}),
                ('get_process_status', {'process_id': 'proc-ok'}),
            ],
        ),
        (
            {'command': 'echo ok', 'wait': 0},
            [('run_command', {'command': 'echo ok', 'wait': 0})],
        ),
    ],
)
async def test_terminal_run_command_preserves_wait_contract(monkeypatch, params, expected_calls):
    calls = []

    async def fake_execute_tool_server(**kwargs):
        calls.append((kwargs['name'], kwargs['params']))
        if kwargs['name'] == 'run_command':
            return {'id': 'proc-ok', 'status': 'running'}, {}
        return {'id': 'proc-ok', 'status': 'done', 'output': ['ok']}, {}

    monkeypatch.setattr(tools_mod, 'execute_tool_server', fake_execute_tool_server)

    result, _headers = await tools_mod.execute_terminal_tool_server(
        url='http://terminal.internal',
        headers={'X-User-Id': 'user-1', 'X-Session-Id': 'chat-1'},
        cookies={},
        name='run_command',
        params=params,
        server_data={'openapi': {}},
    )

    assert calls == expected_calls
    assert result['process_id'] == 'proc-ok'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'params',
    [
        {'command': 'sleep 180', 'wait': -1},
        {'command': 'sleep 180', 'wait': 'invalid'},
        {'command': 'sleep 180', 'wait': False},
        {'command': 'sleep 180', 'wait': True},
        {'command': 'sleep 180', 'tail': 0},
        {'command': 'sleep 180', 'tail': '1e0'},
        {'command': 'sleep 180', 'tail': False},
        {'command': 'sleep 180', 'tail': True},
    ],
)
async def test_terminal_run_command_invalid_wait_or_tail_does_not_prelaunch(monkeypatch, params):
    calls = []

    async def fake_execute_tool_server(**kwargs):
        calls.append((kwargs['name'], kwargs['params']))
        return {'error': 'HTTP error 422: invalid query'}, None

    monkeypatch.setattr(tools_mod, 'execute_tool_server', fake_execute_tool_server)

    result, _headers = await tools_mod.execute_terminal_tool_server(
        url='http://terminal.internal',
        headers={'X-User-Id': 'user-1', 'X-Session-Id': 'chat-1'},
        cookies={},
        name='run_command',
        params=params,
        server_data={'openapi': {}},
    )

    assert calls == [('run_command', params)]
    assert result == {'error': 'HTTP error 422: invalid query'}


@pytest.mark.asyncio
async def test_terminal_run_command_status_failure_kills_owned_process(monkeypatch):
    calls = []

    async def fake_execute_tool_server(**kwargs):
        calls.append((kwargs['name'], kwargs['params']))
        if kwargs['name'] == 'run_command':
            return {'id': 'proc-status-fail', 'status': 'running'}, {}
        if kwargs['name'] == 'get_process_status':
            return {'error': 'HTTP error 500: status unavailable'}, None
        if kwargs['name'] == 'kill_process':
            return {'status': 'killed'}, {}
        raise AssertionError(kwargs['name'])

    monkeypatch.setattr(tools_mod, 'execute_tool_server', fake_execute_tool_server)

    result, _headers = await tools_mod.execute_terminal_tool_server(
        url='http://terminal.internal',
        headers={'X-User-Id': 'user-1', 'X-Session-Id': 'chat-1'},
        cookies={},
        name='run_command',
        params={'command': 'sleep 180', 'wait': 180},
        server_data={'openapi': {}},
    )

    assert calls == [
        ('run_command', {'command': 'sleep 180', 'wait': 0}),
        ('get_process_status', {'process_id': 'proc-status-fail', 'wait': 180}),
        ('kill_process', {'process_id': 'proc-status-fail'}),
    ]
    assert result == {
        'error': 'HTTP error 500: status unavailable',
        'process_id': 'proc-status-fail',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('kill_error', ['HTTP error 404: gone', None])
async def test_terminal_run_command_cancel_accepts_exact_process_absence(monkeypatch, kill_error):
    status_started = asyncio.Event()

    async def fake_execute_tool_server(**kwargs):
        if kwargs['name'] == 'run_command':
            return {'id': 'proc-race', 'status': 'running'}, {}
        if kwargs['name'] == 'get_process_status':
            status_started.set()
            await asyncio.Event().wait()
        if kwargs['name'] == 'kill_process':
            if kill_error is None:
                return {'status': 'killed'}, {}
            return {'error': kill_error}, None
        raise AssertionError(kwargs['name'])

    monkeypatch.setattr(tools_mod, 'execute_tool_server', fake_execute_tool_server)
    task = asyncio.create_task(
        tools_mod.execute_terminal_tool_server(
            url='http://terminal.internal',
            headers={'X-User-Id': 'user-1', 'X-Session-Id': 'chat-1'},
            cookies={},
            name='run_command',
            params={'command': 'sleep 180', 'wait': 180},
            server_data={'openapi': {}},
        )
    )
    await status_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_terminal_run_command_cancel_surfaces_remote_kill_failure(monkeypatch):
    status_started = asyncio.Event()

    async def fake_execute_tool_server(**kwargs):
        if kwargs['name'] == 'run_command':
            return {'id': 'proc-fail', 'status': 'running'}, {}
        if kwargs['name'] == 'get_process_status':
            status_started.set()
            await asyncio.Event().wait()
        if kwargs['name'] == 'kill_process':
            return {'error': 'HTTP error 500: unavailable'}, None
        raise AssertionError(kwargs['name'])

    monkeypatch.setattr(tools_mod, 'execute_tool_server', fake_execute_tool_server)
    task = asyncio.create_task(
        tools_mod.execute_terminal_tool_server(
            url='http://terminal.internal',
            headers={'X-User-Id': 'user-1', 'X-Session-Id': 'chat-1'},
            cookies={},
            name='run_command',
            params={'command': 'sleep 180', 'wait': 180},
            server_data={'openapi': {}},
        )
    )
    await status_started.wait()
    task.cancel()

    with pytest.raises(tools_mod.RemoteProcessKillFailed, match='proc-fail'):
        await task


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

    monkeypatch.setattr(terminals_mod.Config, 'get', _config_get)
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
