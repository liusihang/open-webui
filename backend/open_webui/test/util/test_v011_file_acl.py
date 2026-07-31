from types import SimpleNamespace

import pytest

from open_webui.utils import access_control
from open_webui.utils.access_control import files as file_acl


@pytest.mark.asyncio
async def test_connection_without_grants_remains_admin_only(monkeypatch):
    monkeypatch.setattr('open_webui.config.BYPASS_ADMIN_ACCESS_CONTROL', False)

    private_connection = {'config': {'access_grants': []}}
    admin = SimpleNamespace(id='admin-1', role='admin')
    user = SimpleNamespace(id='user-1', role='user')

    assert await access_control.has_connection_access(admin, private_connection) is True
    assert await access_control.has_connection_access(user, private_connection) is False


@pytest.mark.asyncio
async def test_folder_file_acl_filters_unknown_entries_and_reuses_group_ids(monkeypatch):
    group_calls = []
    file_calls = []

    async def fake_groups(user_id, db=None):
        group_calls.append((user_id, db))
        return [SimpleNamespace(id='group-1')]

    async def fake_file_access(file_id, access_type, user, db=None, user_group_ids=None):
        file_calls.append((file_id, access_type, user.id, db, user_group_ids))
        return file_id == 'file-1'

    async def fake_knowledge_access(*args, **kwargs):
        return False

    async def fake_note(note_id, db=None):
        return SimpleNamespace(id=note_id, user_id='user-1') if note_id == 'note-1' else None

    monkeypatch.setattr(file_acl.Groups, 'get_groups_by_member_id', fake_groups)
    monkeypatch.setattr(file_acl, 'has_access_to_file', fake_file_access)
    monkeypatch.setattr(file_acl.Knowledges, 'check_access_by_user_id', fake_knowledge_access)
    monkeypatch.setattr('open_webui.models.notes.Notes.get_note_by_id', fake_note)

    user = SimpleNamespace(id='user-1', role='user')
    db = object()
    entries = [
        {'type': 'file', 'id': 'file-1'},
        {'type': 'file', 'id': 'file-2'},
        {'type': 'collection', 'id': 'kb-1'},
        {'type': 'note', 'id': 'note-1'},
        {'type': 'url', 'id': 'https://example.com'},
        {'type': 'file'},
        'invalid',
    ]

    accessible = await file_acl.get_accessible_folder_files(entries, user, db=db)

    assert accessible == [
        {'type': 'file', 'id': 'file-1'},
        {'type': 'note', 'id': 'note-1'},
    ]
    assert group_calls == [('user-1', db)]
    assert file_calls == [
        ('file-1', 'read', 'user-1', db, {'group-1'}),
        ('file-2', 'read', 'user-1', db, {'group-1'}),
    ]


@pytest.mark.asyncio
async def test_can_read_all_folder_files_fails_closed_for_invalid_shapes():
    user = SimpleNamespace(id='user-1', role='user')

    assert await file_acl.can_read_all_folder_files(None, user) is True
    assert await file_acl.can_read_all_folder_files([], user) is True
    assert await file_acl.can_read_all_folder_files({'type': 'file'}, user) is False
