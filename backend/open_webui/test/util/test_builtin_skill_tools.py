import json
import types
import zipfile

import pytest
from open_webui.tools import builtin
from open_webui.utils import terminal_skill_packages as terminal_packages
from open_webui.utils import tools as tools_mod
from open_webui.utils.skill_packages import build_skill_package_manifest, build_skill_package_zip_bytes


def _skill(**overrides):
    data = {
        'id': 'demo-skill',
        'user_id': 'user-1',
        'name': 'Demo Skill',
        'description': 'Demo package',
        'content': '# Demo Skill\n\nUse the packaged helper.\n',
        'is_active': True,
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


def _package(**overrides):
    data = {
        'skill_id': 'demo-skill',
        'bundle_hash': 'a' * 64,
        'manifest': {
            'entrypoints': [
                {
                    'name': 'default',
                    'path': 'scripts/run.py',
                    'runtime': 'python',
                }
            ]
        },
        'storage_path': '/tmp/demo-skill.zip',
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


class _FakeTerminalClient:
    def __init__(self, listings=None, reads=None):
        self.listings = listings or {}
        self.reads = reads or {}
        self.read_paths = []
        self.writes = []

    async def list_files(self, directory):
        return {'entries': self.listings[directory]}

    async def read_file(self, path):
        self.read_paths.append(path)
        return {'content': self.reads[path]}

    async def write_file(self, path, content):
        self.writes.append((path, content))
        return {'path': path, 'size': len(content.encode())}


@pytest.mark.asyncio
async def test_read_skill_syncs_package_on_demand_and_returns_runtime_paths(monkeypatch):
    calls = []

    class FakeSkills:
        async def get_skill_by_id(self, skill_id):
            assert skill_id == 'demo-skill'
            return _skill()

        async def get_latest_skill_package_by_skill_id(self, skill_id):
            assert skill_id == 'demo-skill'
            return _package()

    async def fake_sync(request, terminal_id, user, skill, package, *, metadata=None, oauth_token=None):
        calls.append(
            {
                'terminal_id': terminal_id,
                'user_id': user['id'],
                'skill_id': skill.id,
                'bundle_hash': package.bundle_hash,
                'metadata': metadata,
                'oauth_token': oauth_token,
            }
        )
        return {
            'path': '/home/user/.openwebui/skills/demo-skill/aaaaaaaa',
            'entrypoints': [
                {
                    'name': 'default',
                    'path': '/home/user/.openwebui/skills/demo-skill/aaaaaaaa/scripts/run.py',
                    'runtime': 'python',
                }
            ],
        }

    monkeypatch.setattr(builtin, 'Skills', FakeSkills())
    monkeypatch.setattr(builtin, 'ensure_skill_synced_to_terminal', fake_sync)

    raw = await builtin.read_skill(
        'demo-skill',
        __request__=types.SimpleNamespace(),
        __user__={'id': 'user-1', 'role': 'user'},
        __terminal_id__='terminal-1',
        __metadata__={'chat_id': 'chat-1'},
        __oauth_token__={'access_token': 'oauth-token'},
    )

    payload = json.loads(raw)
    assert payload == {
        'name': 'Demo Skill',
        'content': '# Demo Skill\n\nUse the packaged helper.\n',
        'package': {'path': '/home/user/.openwebui/skills/demo-skill/aaaaaaaa'},
        'entrypoints': [
            {
                'name': 'default',
                'path': '/home/user/.openwebui/skills/demo-skill/aaaaaaaa/scripts/run.py',
                'runtime': 'python',
            }
        ],
    }
    assert calls == [
        {
            'terminal_id': 'terminal-1',
            'user_id': 'user-1',
            'skill_id': 'demo-skill',
            'bundle_hash': 'a' * 64,
            'metadata': {'chat_id': 'chat-1'},
            'oauth_token': {'access_token': 'oauth-token'},
        }
    ]


@pytest.mark.asyncio
async def test_install_skill_returns_only_id_and_does_not_sync_runtime(monkeypatch):
    async def fake_install(request, user, terminal_id, source_path, *, metadata=None, oauth_token=None):
        assert terminal_id == 'terminal-1'
        assert source_path == '/home/user/.openwebui/skill-worktrees/demo'
        assert metadata == {'chat_id': 'chat-1'}
        assert oauth_token == {'access_token': 'oauth-token'}
        return 'demo-skill'

    async def forbidden_sync(*args, **kwargs):
        raise AssertionError('install_skill must not sync runtime package files')

    monkeypatch.setattr(builtin, 'install_skill_from_terminal_source', fake_install)
    monkeypatch.setattr(builtin, 'ensure_skill_synced_to_terminal', forbidden_sync)

    raw = await builtin.install_skill(
        '/home/user/.openwebui/skill-worktrees/demo',
        __request__=types.SimpleNamespace(),
        __user__={'id': 'user-1', 'role': 'admin'},
        __terminal_id__='terminal-1',
        __metadata__={'chat_id': 'chat-1'},
        __oauth_token__={'access_token': 'oauth-token'},
    )

    assert json.loads(raw) == {'id': 'demo-skill'}


@pytest.mark.asyncio
async def test_update_skill_returns_only_id_and_defers_runtime_sync(monkeypatch):
    async def fake_update(
        request,
        user,
        terminal_id,
        skill_id,
        content=None,
        source_path=None,
        metadata=None,
        oauth_token=None,
    ):
        assert skill_id == 'demo-skill'
        assert content is None
        assert source_path == '/home/user/.openwebui/skill-worktrees/demo'
        assert metadata == {'chat_id': 'chat-1'}
        assert oauth_token == {'access_token': 'oauth-token'}
        return 'demo-skill'

    async def forbidden_sync(*args, **kwargs):
        raise AssertionError('update_skill must not sync runtime package files')

    monkeypatch.setattr(builtin, 'update_skill_from_tool', fake_update)
    monkeypatch.setattr(builtin, 'ensure_skill_synced_to_terminal', forbidden_sync)

    raw = await builtin.update_skill(
        'demo-skill',
        source_path='/home/user/.openwebui/skill-worktrees/demo',
        __request__=types.SimpleNamespace(),
        __user__={'id': 'user-1', 'role': 'user'},
        __terminal_id__='terminal-1',
        __metadata__={'chat_id': 'chat-1'},
        __oauth_token__={'access_token': 'oauth-token'},
    )

    assert json.loads(raw) == {'id': 'demo-skill'}


def test_builtin_skill_tool_descriptions_state_packages_are_text_only():
    for tool in (builtin.read_skill, builtin.install_skill, builtin.update_skill):
        description = tool.__doc__
        assert 'text-only' in description
        assert 'UTF-8 text' in description
        assert 'binary assets are not supported' in description


@pytest.mark.asyncio
async def test_builtin_registration_exposes_three_skill_tools_without_legacy_view(monkeypatch):
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace()))
    model = {
        'info': {
            'meta': {
                'builtinTools': {
                    'time': False,
                    'knowledge': False,
                    'chats': False,
                    'memory': False,
                    'web_search': False,
                    'image_generation': False,
                    'code_interpreter': False,
                    'notes': False,
                    'channels': False,
                    'tasks': False,
                    'automations': False,
                    'calendar': False,
                    'skills': True,
                }
            }
        }
    }

    async def fake_get_many(*keys):
        return {key: False for key in keys}

    monkeypatch.setattr(tools_mod.Config, 'get_many', fake_get_many)

    registered = await tools_mod.get_builtin_tools(
        request,
        {
            '__user__': {'id': 'user-1', 'role': 'admin'},
            '__skill_ids__': ['demo-skill'],
            '__metadata__': {'terminal_id': 'terminal-1'},
        },
        features={},
        model=model,
    )

    assert {'read_skill', 'install_skill', 'update_skill'} <= set(registered)
    assert 'view_skill' not in registered


@pytest.mark.asyncio
async def test_terminal_source_reader_recursively_reads_text_package(monkeypatch):
    client = _FakeTerminalClient(
        listings={
            '/home/user/.openwebui/skill-worktrees/demo': [
                {'name': 'SKILL.md', 'type': 'file'},
                {'name': 'scripts', 'type': 'directory'},
            ],
            '/home/user/.openwebui/skill-worktrees/demo/scripts': [
                {'name': 'run.py', 'type': 'file'},
            ],
        },
        reads={
            '/home/user/.openwebui/skill-worktrees/demo/SKILL.md': '---\nname: Demo\n---\nBody\n',
            '/home/user/.openwebui/skill-worktrees/demo/scripts/run.py': "print('ok')\n",
        },
    )

    async def fake_client(*args, **kwargs):
        return client

    monkeypatch.setattr(terminal_packages, '_get_terminal_file_client', fake_client)

    files = await terminal_packages.read_skill_package_source_from_terminal(
        types.SimpleNamespace(),
        'terminal-1',
        {'id': 'user-1', 'role': 'admin'},
        '/home/user/.openwebui/skill-worktrees/demo',
    )

    assert files == {
        'SKILL.md': '---\nname: Demo\n---\nBody\n',
        'scripts/run.py': "print('ok')\n",
    }


@pytest.mark.asyncio
async def test_terminal_source_reader_reserves_parent_files_before_recursing(monkeypatch):
    root = '/home/user/.openwebui/skill-worktrees/demo'
    nested_file_count = terminal_packages.MAX_TERMINAL_SOURCE_FILES
    listings = {
        root: [
            {'name': 'templates', 'type': 'directory'},
            {'name': 'SKILL.md', 'type': 'file'},
        ],
        f'{root}/templates': [
            {'name': f'file-{index}.txt', 'type': 'file'}
            for index in range(nested_file_count)
        ],
    }
    reads = {f'{root}/SKILL.md': '---\nname: Demo\n---\nBody\n'}
    reads.update({f'{root}/templates/file-{index}.txt': 'ok\n' for index in range(nested_file_count)})
    client = _FakeTerminalClient(listings=listings, reads=reads)

    async def fake_client(*args, **kwargs):
        return client

    monkeypatch.setattr(terminal_packages, '_get_terminal_file_client', fake_client)

    with pytest.raises(terminal_packages.TerminalSkillPackageError, match='too many text files'):
        await terminal_packages.read_skill_package_source_from_terminal(
            types.SimpleNamespace(),
            'terminal-1',
            {'id': 'user-1', 'role': 'admin'},
            root,
        )

    assert f'{root}/SKILL.md' not in client.read_paths


@pytest.mark.asyncio
async def test_terminal_sync_writes_runtime_package_files_and_marker_last(monkeypatch, tmp_path):
    files = {
        'SKILL.md': '---\nname: Demo\n---\nBody\n',
        'skill.json': json.dumps({'entrypoints': [{'name': 'default', 'path': 'scripts/run.py', 'runtime': 'python'}]}),
        'scripts/run.py': "print('ok')\n",
    }
    manifest = build_skill_package_manifest(files)
    zip_path = tmp_path / 'demo.zip'
    zip_path.write_bytes(build_skill_package_zip_bytes(files))
    client = _FakeTerminalClient()

    async def fake_client(*args, **kwargs):
        return client

    monkeypatch.setattr(terminal_packages, '_get_terminal_file_client', fake_client)
    monkeypatch.setattr(terminal_packages.Storage, 'get_file', lambda storage_path: str(zip_path))

    result = await terminal_packages.ensure_skill_synced_to_terminal(
        types.SimpleNamespace(),
        'terminal-1',
        {'id': 'user-1', 'role': 'admin'},
        _skill(),
        _package(bundle_hash=manifest.hash, manifest=manifest.model_dump(), storage_path='storage://demo.zip'),
    )

    runtime_dir = f'/home/user/.openwebui/skills/demo-skill/{manifest.hash}'
    assert result == {
        'path': runtime_dir,
        'entrypoints': [{'name': 'default', 'path': f'{runtime_dir}/scripts/run.py', 'runtime': 'python'}],
    }
    assert client.writes[:-1] == [
        (f'{runtime_dir}/SKILL.md', '---\nname: Demo\n---\nBody\n'),
        (f'{runtime_dir}/scripts/run.py', "print('ok')\n"),
        (f'{runtime_dir}/skill.json', files['skill.json']),
    ]
    marker_path, marker_content = client.writes[-1]
    assert marker_path == f'{runtime_dir}/.openwebui-skill.json'
    assert json.loads(marker_content)['bundle_hash'] == manifest.hash


@pytest.mark.asyncio
async def test_storage_package_zip_readback_rejects_over_budget_entries_before_read(monkeypatch, tmp_path):
    zip_path = tmp_path / 'demo.zip'
    with zipfile.ZipFile(zip_path, 'w') as archive:
        archive.writestr('SKILL.md', '---\nname: Demo\n---\nBody\n')
        archive.writestr('templates/large.txt', 'x' * (terminal_packages.MAX_STORAGE_ZIP_SINGLE_ENTRY_BYTES + 1))

    monkeypatch.setattr(terminal_packages.Storage, 'get_file', lambda storage_path: str(zip_path))

    def forbidden_read(self, name, *args, **kwargs):
        raise AssertionError('oversized zip entries must be rejected from ZipInfo before archive.read')

    monkeypatch.setattr(zipfile.ZipFile, 'read', forbidden_read)

    with pytest.raises(terminal_packages.TerminalSkillPackageError, match='zip entry templates/large.txt.*exceeds'):
        await terminal_packages._read_storage_package_files('storage://demo.zip')


def test_terminal_source_reader_rejects_runtime_cache_as_install_source():
    with pytest.raises(terminal_packages.TerminalSkillPackageError, match='runtime skill cache'):
        terminal_packages._validate_terminal_source_path('/home/user/.openwebui/skills/demo-skill/hash')

    with pytest.raises(terminal_packages.TerminalSkillPackageError, match='double-slash'):
        terminal_packages._validate_terminal_source_path('//home/user/.openwebui/skills/demo-skill/hash')

    with pytest.raises(terminal_packages.TerminalSkillPackageError, match='absolute'):
        terminal_packages._validate_terminal_source_path('skills/demo-skill/hash')


def test_terminal_headers_include_session_and_system_oauth_token():
    request = types.SimpleNamespace(cookies={'session': 'cookie'}, state=types.SimpleNamespace())
    headers, cookies = terminal_packages._terminal_headers_and_cookies(
        request,
        {'auth_type': 'system_oauth'},
        types.SimpleNamespace(id='user-1'),
        metadata={'chat_id': 'chat-1'},
        oauth_token={'access_token': 'oauth-token'},
    )

    assert headers == {
        'X-User-Id': 'user-1',
        'X-Session-Id': 'chat-1',
        'Authorization': 'Bearer oauth-token',
    }
    assert cookies == {'session': 'cookie'}


def test_terminal_skill_package_session_auth_uses_minted_terminal_token(monkeypatch):
    captured = {}

    def fake_create_terminal_session_token(user):
        captured['user_id'] = user.id
        return f'minted-token-for-{user.id}'

    monkeypatch.setattr(terminal_packages, 'create_terminal_session_token', fake_create_terminal_session_token)

    request = types.SimpleNamespace(
        cookies={'session': 'cookie'},
        state=types.SimpleNamespace(token=types.SimpleNamespace(credentials='service-token')),
    )
    headers, cookies = terminal_packages._terminal_headers_and_cookies(
        request,
        {'auth_type': 'session'},
        types.SimpleNamespace(id='user-1'),
        metadata={'chat_id': 'chat-1'},
    )

    assert captured['user_id'] == 'user-1'
    assert headers == {
        'X-User-Id': 'user-1',
        'X-Session-Id': 'chat-1',
        'Authorization': 'Bearer minted-token-for-user-1',
    }
    assert cookies == {'session': 'cookie'}


@pytest.mark.asyncio
async def test_package_update_uses_transaction_facade_and_does_not_independently_upsert(monkeypatch):
    files = {
        'SKILL.md': '---\nname: Taken Name\n---\nUpdated body\n',
        'scripts/run.py': "print('ok')\n",
    }
    calls = []

    class FakeSkills:
        async def get_skill_by_id(self, skill_id):
            return _skill(id=skill_id)

        async def update_skill_and_upsert_package_by_id(self, skill_id, updated, **package_kwargs):
            calls.append((skill_id, updated, package_kwargs))
            return None

        async def upsert_skill_package(self, *args, **kwargs):
            raise AssertionError('package-backed update must use the transaction facade')

    async def fake_read_source(*args, **kwargs):
        return files

    async def fake_upload(*args, **kwargs):
        return 'storage://bundle.zip'

    monkeypatch.setattr(builtin, 'Skills', FakeSkills())
    monkeypatch.setattr(builtin, 'read_skill_package_source_from_terminal', fake_read_source)
    monkeypatch.setattr(builtin, '_upload_skill_package_bundle', fake_upload)

    with pytest.raises(ValueError, match='Error updating skill'):
        await builtin.update_skill_from_tool(
            types.SimpleNamespace(),
            {'id': 'user-1', 'role': 'admin'},
            'terminal-1',
            'demo-skill',
            source_path='/home/user/.openwebui/skill-worktrees/demo',
            metadata={'chat_id': 'chat-1'},
            oauth_token={'access_token': 'oauth-token'},
        )

    assert calls
