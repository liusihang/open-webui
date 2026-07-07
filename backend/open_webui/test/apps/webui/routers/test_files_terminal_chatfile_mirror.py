from types import SimpleNamespace

import pytest

from open_webui.routers import files as files_router


class _FakeResponse:
    def __init__(self, *, status=200, json_body=None, text=''):
        self.status = status
        self._json_body = json_body or {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._json_body

    async def text(self):
        return self._text


def _fake_user():
    return SimpleNamespace(id='user-1', role='user')


async def _groups_for_user(user_id):
    assert user_id == 'user-1'
    return [SimpleNamespace(id='group-1')]


async def _allow_access(user, connection, user_group_ids):
    assert user.id == 'user-1'
    assert connection['id'] == 'terminals'
    assert user_group_ids == {'group-1'}
    return True


@pytest.mark.asyncio
async def test_chat_upload_terminal_mirror_uploads_to_chatfile(monkeypatch):
    calls = []

    async def _get_config(key, default=None):
        values = {
            'chat_upload_terminal.enabled': True,
            'chat_upload_terminal.server_id': 'terminals',
            'chat_upload_terminal.directory': 'Chatfile',
            'terminal_server.connections': [
                {
                    'id': 'terminals',
                    'url': 'http://terminal.internal',
                    'auth_type': 'session',
                    'enabled': True,
                }
            ],
        }
        return values.get(key, default)

    class _FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if url == 'http://terminal.internal/files/mkdir':
                return _FakeResponse(status=200, json_body={'path': '/home/user/Chatfile'})
            if url == 'http://terminal.internal/files/upload?directory=Chatfile':
                return _FakeResponse(
                    status=200,
                    json_body={'path': '/home/user/Chatfile/report.txt', 'size': 5},
                )
            raise AssertionError(f'unexpected URL: {url}')

    monkeypatch.setattr(files_router.Config, 'get', _get_config)
    monkeypatch.setattr(files_router.Groups, 'get_groups_by_member_id', _groups_for_user)
    monkeypatch.setattr(files_router, 'has_connection_access', _allow_access)
    monkeypatch.setattr(files_router, 'create_terminal_session_token', lambda user: 'session-token')
    monkeypatch.setattr(files_router.aiohttp, 'ClientSession', _FakeClientSession)

    result = await files_router._sync_chat_upload_to_terminal_chatfile(
        request=SimpleNamespace(cookies={}),
        user=_fake_user(),
        filename='../report.txt',
        contents=b'hello',
    )

    assert result['status'] == 'synced'
    assert result['server_id'] == 'terminals'
    assert result['directory'] == 'Chatfile'
    assert result['filename'] == 'report.txt'
    assert result['path'] == '/home/user/Chatfile/report.txt'
    assert result['size'] == 5
    assert isinstance(result['synced_at'], int)
    assert calls[0][1]['json'] == {'path': 'Chatfile'}
    assert calls[1][1]['headers'] == {
        'X-User-Id': 'user-1',
        'Authorization': 'Bearer session-token',
    }


@pytest.mark.asyncio
async def test_chat_upload_terminal_mirror_returns_failed_metadata_on_upload_error(monkeypatch):
    async def _get_config(key, default=None):
        values = {
            'chat_upload_terminal.enabled': True,
            'chat_upload_terminal.server_id': 'terminals',
            'chat_upload_terminal.directory': 'Chatfile',
            'terminal_server.connections': [
                {
                    'id': 'terminals',
                    'url': 'http://terminal.internal',
                    'auth_type': 'session',
                    'enabled': True,
                }
            ],
        }
        return values.get(key, default)

    class _FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            if url == 'http://terminal.internal/files/mkdir':
                return _FakeResponse(status=200, json_body={'path': '/home/user/Chatfile'})
            return _FakeResponse(status=403, text='permission denied')

    monkeypatch.setattr(files_router.Config, 'get', _get_config)
    monkeypatch.setattr(files_router.Groups, 'get_groups_by_member_id', _groups_for_user)
    monkeypatch.setattr(files_router, 'has_connection_access', _allow_access)
    monkeypatch.setattr(files_router, 'create_terminal_session_token', lambda user: 'session-token')
    monkeypatch.setattr(files_router.aiohttp, 'ClientSession', _FakeClientSession)

    result = await files_router._sync_chat_upload_to_terminal_chatfile(
        request=SimpleNamespace(cookies={}),
        user=_fake_user(),
        filename='report.txt',
        contents=b'hello',
    )

    assert result['status'] == 'failed'
    assert result['server_id'] == 'terminals'
    assert result['directory'] == 'Chatfile'
    assert result['filename'] == 'report.txt'
    assert 'HTTP 403' in result['error']


def test_terminal_chatfile_mirror_requires_chat_upload_context():
    assert files_router._should_mirror_upload_to_terminal({'upload_context': 'chat'}) is True
    assert files_router._should_mirror_upload_to_terminal({'knowledge_id': 'kb-1'}) is False
    assert files_router._should_mirror_upload_to_terminal({'channel_id': 'channel-1'}) is False
    assert files_router._should_mirror_upload_to_terminal({}) is False
