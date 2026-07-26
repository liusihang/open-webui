from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio
from open_webui.agent import conversation_mode_profile_service as profile_service
from open_webui.agent.conversation_mode_profiles import (
    ConversationModeProfile,
    ProfileDefaults,
)
from open_webui.models import chat_messages as chat_messages_module
from open_webui.models import chats as chats_model_module
from open_webui.models import conversation_mode_profiles as profile_store_module
from open_webui.models import shared_chats as shared_chats_module
from open_webui.models.automations import AutomationRun
from open_webui.models.chat_messages import ChatMessage, ChatMessages
from open_webui.models.chats import Chat, ChatForm, ChatImportForm, Chats
from open_webui.models.conversation_mode_profiles import (
    ConversationModeProfileHead,
    ConversationModeProfileRevision,
    ConversationModeProfiles,
    ConversationModeProfileTemporaryBinding,
)
from open_webui.models.shared_chats import SharedChat
from open_webui.routers import chats as chats_router
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

OLD_REVISION_ID = '00000000-0000-0000-0000-000000000001'
NEW_REVISION_ID = '00000000-0000-0000-0000-000000000002'


@pytest_asyncio.fixture
async def atomic_profile_db(tmp_path, monkeypatch):
    engine = create_async_engine(
        f'sqlite+aiosqlite:///{tmp_path / "atomic-profile.db"}',
        connect_args={'timeout': 2},
    )
    async with engine.begin() as connection:
        await connection.run_sync(ConversationModeProfileRevision.__table__.create)
        await connection.run_sync(ConversationModeProfileHead.__table__.create)
        await connection.run_sync(ConversationModeProfileTemporaryBinding.__table__.create)
        await connection.run_sync(Chat.__table__.create)
        await connection.run_sync(ChatMessage.__table__.create)
        await connection.run_sync(SharedChat.__table__.create)
        await connection.run_sync(AutomationRun.__table__.create)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    old_profile = ConversationModeProfile(
        mode='chat',
        schema_version=1,
        system_prompt='',
        defaults=ProfileDefaults(),
    )
    new_profile = ConversationModeProfile(
        mode='chat',
        schema_version=1,
        system_prompt='new administrator prompt',
        defaults=ProfileDefaults(),
    )
    async with sessions() as session:
        session.add_all(
            [
                _revision_row(OLD_REVISION_ID, 1, old_profile),
                _revision_row(NEW_REVISION_ID, 2, new_profile),
                ConversationModeProfileHead(
                    mode='chat',
                    current_revision_id=OLD_REVISION_ID,
                    baseline_revision_id=OLD_REVISION_ID,
                    cutover_at=1,
                    updated_at=1,
                    updated_by='admin-1',
                ),
            ]
        )
        await session.commit()

    @asynccontextmanager
    async def session_context(db=None):
        if db is not None:
            yield db
            return
        async with sessions() as session:
            yield session

    monkeypatch.setattr(profile_service, 'get_async_db_context', session_context)
    monkeypatch.setattr(chats_model_module, 'get_async_db_context', session_context)
    monkeypatch.setattr(profile_store_module, 'get_async_db_context', session_context)
    monkeypatch.setattr(shared_chats_module, 'get_async_db_context', session_context)
    yield sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_head_switch_that_locks_first_wins_new_chat_binding(
    atomic_profile_db,
):
    atomic_insert = profile_service.insert_new_chat_with_current_mode_profile
    admin_locked = asyncio.Event()
    release_admin = asyncio.Event()

    async def switch_head():
        async with atomic_profile_db() as session:
            await _begin_write(session)
            head = await _lock_head(session)
            admin_locked.set()
            await release_admin.wait()
            head.current_revision_id = NEW_REVISION_ID
            head.updated_at = 2
            await session.commit()

    admin_task = asyncio.create_task(switch_head())
    await admin_locked.wait()
    chat_task = asyncio.create_task(
        atomic_insert(
            _app(),
            mode='chat',
            revision_hint=None,
            chat_id='chat-admin-first',
            user_id='user-1',
            form_data=_chat_form('chat-admin-first'),
        )
    )
    await _assert_task_waits(chat_task)

    release_admin.set()
    await admin_task
    result = await chat_task

    assert result.revision.id == NEW_REVISION_ID
    assert result.chat.mode_profile_revision_id == NEW_REVISION_ID
    assert await _chat_binding(atomic_profile_db, 'chat-admin-first') == NEW_REVISION_ID


@pytest.mark.asyncio
async def test_new_chat_transaction_that_locks_first_keeps_old_binding_and_admin_waits(
    atomic_profile_db,
    monkeypatch,
):
    atomic_insert = profile_service.insert_new_chat_with_current_mode_profile
    insert_reached = asyncio.Event()
    release_insert = asyncio.Event()
    admin_attempting = asyncio.Event()
    original_insert = Chats.insert_new_chat

    async def paused_insert(*args, **kwargs):
        insert_reached.set()
        await release_insert.wait()
        return await original_insert(*args, **kwargs)

    monkeypatch.setattr(Chats, 'insert_new_chat', paused_insert)
    chat_task = asyncio.create_task(
        atomic_insert(
            _app(),
            mode='chat',
            revision_hint=None,
            chat_id='chat-first',
            user_id='user-1',
            form_data=_chat_form('chat-first'),
        )
    )
    await insert_reached.wait()

    async def switch_head():
        admin_attempting.set()
        async with atomic_profile_db() as session:
            await _begin_write(session)
            head = await _lock_head(session)
            head.current_revision_id = NEW_REVISION_ID
            head.updated_at = 2
            await session.commit()

    admin_task = asyncio.create_task(switch_head())
    await admin_attempting.wait()
    await _assert_task_waits(admin_task)

    release_insert.set()
    result = await chat_task
    await admin_task

    assert result.revision.id == OLD_REVISION_ID
    assert result.chat.mode_profile_revision_id == OLD_REVISION_ID
    assert await _chat_binding(atomic_profile_db, 'chat-first') == OLD_REVISION_ID
    assert await _current_head(atomic_profile_db) == NEW_REVISION_ID


@pytest.mark.asyncio
async def test_atomic_new_chat_stale_hint_is_typed_conflict_without_chat_write(
    atomic_profile_db,
):
    atomic_insert = profile_service.insert_new_chat_with_current_mode_profile

    with pytest.raises(profile_service.ModeProfileRevisionHintConflictError) as exc_info:
        await atomic_insert(
            _app(),
            mode='chat',
            revision_hint=NEW_REVISION_ID,
            chat_id='chat-stale-hint',
            user_id='user-1',
            form_data=_chat_form('chat-stale-hint'),
        )

    assert exc_info.value.authoritative_revision_id == OLD_REVISION_ID
    assert exc_info.value.bound is False
    assert await _chat_binding(atomic_profile_db, 'chat-stale-hint') is None


@pytest.mark.asyncio
async def test_atomic_new_chat_rechecks_prevalidated_revision_and_rejects_head_drift(
    atomic_profile_db,
):
    async with atomic_profile_db() as session:
        await _begin_write(session)
        head = await _lock_head(session)
        head.current_revision_id = NEW_REVISION_ID
        head.updated_at = 2
        await session.commit()

    with pytest.raises(profile_service.ModeProfileRevisionHintConflictError) as exc_info:
        await profile_service.insert_new_chat_with_current_mode_profile(
            _app(),
            mode='chat',
            revision_hint=None,
            expected_revision_id=OLD_REVISION_ID,
            chat_id='chat-head-drift',
            user_id='user-1',
            form_data=_chat_form('chat-head-drift'),
        )

    assert exc_info.value.authoritative_revision_id == NEW_REVISION_ID
    assert exc_info.value.hinted_revision_id == OLD_REVISION_ID
    assert await _chat_binding(atomic_profile_db, 'chat-head-drift') is None


@pytest.mark.parametrize(
    'modes',
    [
        ('chat', 'agent'),
        ('agent', 'chat'),
    ],
)
def test_bulk_import_uses_one_canonical_mode_head_lock_order_independent_of_input_order(modes) -> None:
    forms = [
        ChatImportForm(chat={**_chat_form(f'import-{index}').chat, 'mode': mode})
        for index, mode in enumerate(modes)
    ]

    assert profile_service.canonical_import_mode_head_lock_order(forms) == ('agent', 'chat')


@pytest.mark.asyncio
async def test_shared_snapshot_read_uses_real_source_chat_binding_for_audit_metadata(atomic_profile_db) -> None:
    source = Chat(
        id='source-chat',
        user_id='user-1',
        title='Source',
        chat={'title': 'Source', 'mode': 'chat', 'history': {'currentId': None, 'messages': {}}},
        created_at=1,
        updated_at=1,
        mode_profile_revision_id=OLD_REVISION_ID,
    )
    snapshot = SharedChat(
        id='shared-source-chat',
        chat_id=source.id,
        user_id=source.user_id,
        title='Snapshot',
        chat={'title': 'Snapshot', 'mode': 'chat', 'history': {'currentId': None, 'messages': {}}},
        created_at=1,
        updated_at=1,
    )
    async with atomic_profile_db() as session:
        session.add_all([source, snapshot])
        await session.commit()

    response = await chats_router.get_shared_chat_by_id(
        snapshot.id,
        SimpleNamespace(id=source.user_id, role='user'),
        None,
    )

    assert response.id == snapshot.id
    assert response.mode_profile_revision_id == OLD_REVISION_ID


@pytest.mark.asyncio
async def test_shared_snapshot_read_fails_closed_for_post_cutover_unbound_source(atomic_profile_db) -> None:
    source = Chat(
        id='unbound-source-chat',
        user_id='user-1',
        title='Source',
        chat={'title': 'Source', 'mode': 'chat', 'history': {'currentId': None, 'messages': {}}},
        created_at=2,
        updated_at=2,
        mode_profile_revision_id=None,
    )
    snapshot = SharedChat(
        id='shared-unbound-source-chat',
        chat_id=source.id,
        user_id=source.user_id,
        title='Snapshot',
        chat={'title': 'Snapshot', 'mode': 'chat', 'history': {'currentId': None, 'messages': {}}},
        created_at=2,
        updated_at=2,
    )
    async with atomic_profile_db() as session:
        session.add_all([source, snapshot])
        await session.commit()

    with pytest.raises(chats_router.HTTPException) as exc_info:
        await chats_router.get_shared_chat_by_id(
            snapshot.id,
            SimpleNamespace(id=source.user_id, role='user'),
            None,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {
        'code': 'mode_profile_integrity_error',
        'message': 'The conversation mode profile binding failed integrity verification.',
    }


@pytest.mark.asyncio
async def test_chat_insert_with_caller_session_does_not_commit(
    atomic_profile_db,
):
    async with atomic_profile_db() as session:
        await _begin_write(session)
        chat = await Chats.insert_new_chat(
            'chat-caller-transaction',
            'user-1',
            _chat_form('chat-caller-transaction'),
            db=session,
            mode_profile_revision_id=OLD_REVISION_ID,
            commit=False,
        )
        assert chat.mode_profile_revision_id == OLD_REVISION_ID
        assert session.in_transaction()
        await session.rollback()

    assert await _chat_binding(atomic_profile_db, 'chat-caller-transaction') is None


@pytest.mark.asyncio
async def test_chat_message_no_commit_always_uses_caller_session(
    atomic_profile_db,
    monkeypatch,
):
    @asynccontextmanager
    async def fail_context(db=None):
        raise AssertionError('caller-owned no-commit path must not open a context')
        yield

    monkeypatch.setattr(
        chat_messages_module,
        'get_async_db_context',
        fail_context,
    )

    async with atomic_profile_db() as session:
        await _begin_write(session)
        message = await ChatMessages.upsert_message(
            message_id='message-caller-transaction',
            chat_id='chat-caller-transaction',
            user_id='user-1',
            data={'role': 'user', 'content': 'hello'},
            db=session,
            commit=False,
        )
        assert message.role == 'user'
        assert session.in_transaction()
        await session.rollback()


@pytest.mark.asyncio
async def test_atomic_new_chat_rolls_back_chat_when_initial_message_write_fails(
    atomic_profile_db,
    monkeypatch,
):
    async def fail_message_write(*args, **kwargs):
        raise RuntimeError('initial message write failed')

    monkeypatch.setattr(ChatMessages, 'upsert_message', fail_message_write)

    with pytest.raises(RuntimeError, match='initial message write failed'):
        await profile_service.insert_new_chat_with_current_mode_profile(
            _app(),
            mode='chat',
            revision_hint=None,
            chat_id='chat-dual-write-failure',
            user_id='user-1',
            form_data=_chat_form_with_messages('chat-dual-write-failure'),
        )

    assert await _chat_binding(atomic_profile_db, 'chat-dual-write-failure') is None


@pytest.mark.asyncio
async def test_atomic_new_chat_commits_chat_and_initial_messages_together(
    atomic_profile_db,
):
    result = await profile_service.insert_new_chat_with_current_mode_profile(
        _app(),
        mode='chat',
        revision_hint=None,
        expected_revision_id=OLD_REVISION_ID,
        chat_id='chat-dual-write-success',
        user_id='user-1',
        form_data=_chat_form_with_messages('chat-dual-write-success'),
    )

    assert result.chat.mode_profile_revision_id == OLD_REVISION_ID
    async with atomic_profile_db() as session:
        messages = (
            (await session.execute(select(ChatMessage).where(ChatMessage.chat_id == 'chat-dual-write-success')))
            .scalars()
            .all()
        )
    assert {message.role for message in messages} == {'user', 'assistant'}


@pytest.mark.asyncio
async def test_atomic_new_chat_transfers_server_temporary_binding_without_reselecting_head(
    atomic_profile_db,
):
    async with atomic_profile_db() as session:
        temporary = await ConversationModeProfiles.create_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='local:save-to-history',
            mode='chat',
            expires_at=4_000_000_000,
            db=session,
        )
        await session.commit()

    async with atomic_profile_db() as session:
        await _begin_write(session)
        head = await _lock_head(session)
        head.current_revision_id = NEW_REVISION_ID
        await session.commit()

    result = await profile_service.insert_new_chat_with_current_mode_profile(
        _app(),
        mode='chat',
        revision_hint=None,
        chat_id='chat-from-temporary-atomic',
        user_id='user-1',
        form_data=_chat_form('chat-from-temporary-atomic'),
        source_temporary_conversation_id='local:save-to-history',
    )

    assert result.chat.mode_profile_revision_id == temporary.mode_profile_revision_id
    assert await _chat_binding(atomic_profile_db, 'chat-from-temporary-atomic') == temporary.mode_profile_revision_id
    async with atomic_profile_db() as session:
        assert (
            await session.scalar(
                select(ConversationModeProfileTemporaryBinding).where(
                    ConversationModeProfileTemporaryBinding.user_id == 'user-1',
                    ConversationModeProfileTemporaryBinding.temporary_conversation_id == 'local:save-to-history',
                )
            )
            is None
        )


@pytest.mark.asyncio
async def test_atomic_new_chat_rejects_missing_temporary_source_without_creating_chat(
    atomic_profile_db,
):
    with pytest.raises(profile_service.ModeProfileTemporaryBindingError) as exc_info:
        await profile_service.insert_new_chat_with_current_mode_profile(
            _app(),
            mode='chat',
            revision_hint=None,
            chat_id='chat-from-missing-temporary',
            user_id='user-1',
            form_data=_chat_form('chat-from-missing-temporary'),
            source_temporary_conversation_id='local:missing',
        )

    assert exc_info.value.code == 'mode_profile_temporary_binding_missing'
    assert await _chat_binding(atomic_profile_db, 'chat-from-missing-temporary') is None


@pytest.mark.asyncio
async def test_external_import_ignores_client_revision_and_binds_current_server_revision(
    atomic_profile_db,
):
    async with atomic_profile_db() as session:
        await _begin_write(session)
        head = await _lock_head(session)
        head.current_revision_id = NEW_REVISION_ID
        await session.commit()

    imported = await profile_service.import_chats_with_mode_profile_bindings(
        _app(),
        user_id='user-1',
        chat_import_forms=[
            ChatImportForm.model_validate(
                {
                    'chat': {
                        **_chat_form('external-import').chat,
                        'mode_profile_revision_id': OLD_REVISION_ID,
                    },
                    'mode_profile_revision_id': OLD_REVISION_ID,
                }
            )
        ],
    )

    assert len(imported) == 1
    assert imported[0].mode_profile_revision_id == NEW_REVISION_ID
    assert imported[0].chat['mode'] == 'chat'
    assert 'mode_profile_revision_id' not in imported[0].chat


@pytest.mark.asyncio
async def test_external_import_rolls_back_the_whole_batch_when_any_form_is_invalid(
    atomic_profile_db,
):
    with pytest.raises(ValueError):
        await profile_service.import_chats_with_mode_profile_bindings(
            _app(),
            user_id='user-1',
            chat_import_forms=[
                ChatImportForm(chat=_chat_form('import-would-be-first').chat),
                ChatImportForm(chat={**_chat_form('import-invalid').chat, 'mode': 'unsupported'}),
            ],
        )

    async with atomic_profile_db() as session:
        assert (await session.execute(select(Chat))).scalars().all() == []


@pytest.mark.asyncio
async def test_generic_update_cannot_spoof_or_mutate_a_chat_mode_profile_binding(
    atomic_profile_db,
):
    await profile_service.insert_new_chat_with_current_mode_profile(
        _app(),
        mode='chat',
        revision_hint=None,
        chat_id='bound-chat-update',
        user_id='user-1',
        form_data=_chat_form('bound-chat-update'),
    )

    updated = await Chats.update_chat_by_id(
        'bound-chat-update',
        {
            **_chat_form('bound-chat-update').chat,
            'title': 'Updated',
            'mode_profile_revision_id': NEW_REVISION_ID,
        },
    )

    assert updated is not None
    assert updated.mode_profile_revision_id == OLD_REVISION_ID
    assert 'mode_profile_revision_id' not in updated.chat
    assert await _chat_binding(atomic_profile_db, 'bound-chat-update') == OLD_REVISION_ID


@pytest.mark.asyncio
async def test_atomic_new_chat_rejects_expired_or_wrong_mode_temporary_sources_without_writes(
    atomic_profile_db,
):
    async with atomic_profile_db() as session:
        session.add_all(
            [
                ConversationModeProfileTemporaryBinding(
                    id='temporary-expired',
                    user_id='user-1',
                    temporary_conversation_id='local:expired',
                    mode='chat',
                    mode_profile_revision_id=OLD_REVISION_ID,
                    created_at=1,
                    updated_at=1,
                    expires_at=1,
                ),
                ConversationModeProfileTemporaryBinding(
                    id='temporary-wrong-mode',
                    user_id='user-1',
                    temporary_conversation_id='local:agent',
                    mode='agent',
                    mode_profile_revision_id=OLD_REVISION_ID,
                    created_at=1,
                    updated_at=1,
                    expires_at=4_000_000_000,
                ),
            ]
        )
        await session.commit()

    for temporary_id, expected_code in (
        ('local:expired', 'mode_profile_temporary_binding_expired'),
        ('local:agent', 'mode_profile_temporary_binding_mode_mismatch'),
    ):
        with pytest.raises(profile_service.ModeProfileTemporaryBindingError) as exc_info:
            await profile_service.insert_new_chat_with_current_mode_profile(
                _app(),
                mode='chat',
                revision_hint=None,
                chat_id=f'chat-{temporary_id}',
                user_id='user-1',
                form_data=_chat_form(f'chat-{temporary_id}'),
                source_temporary_conversation_id=temporary_id,
            )
        assert exc_info.value.code == expected_code
        assert await _chat_binding(atomic_profile_db, f'chat-{temporary_id}') is None


@pytest.mark.asyncio
async def test_delete_removes_only_chat_and_keeps_immutable_profile_revision(
    atomic_profile_db,
):
    await profile_service.insert_new_chat_with_current_mode_profile(
        _app(),
        mode='chat',
        revision_hint=None,
        chat_id='bound-chat-delete',
        user_id='user-1',
        form_data=_chat_form('bound-chat-delete'),
    )

    assert await Chats.delete_chat_by_id('bound-chat-delete') is True
    async with atomic_profile_db() as session:
        assert await session.get(Chat, 'bound-chat-delete') is None
        assert await session.get(ConversationModeProfileRevision, OLD_REVISION_ID) is not None


def _revision_row(
    revision_id: str,
    revision_number: int,
    profile: ConversationModeProfile,
) -> ConversationModeProfileRevision:
    content = profile.to_content_dict()
    return ConversationModeProfileRevision(
        id=revision_id,
        mode=profile.mode.value,
        revision_number=revision_number,
        schema_version=profile.schema_version,
        system_prompt=profile.system_prompt,
        defaults=content['defaults'],
        content_hash=profile.content_hash,
        created_at=revision_number,
        created_by='admin-1',
        restored_from_revision_id=None,
    )


async def _begin_write(session) -> None:
    if session.get_bind().dialect.name == 'sqlite':
        await session.execute(text('BEGIN IMMEDIATE'))
    else:
        await session.begin()


async def _lock_head(session) -> ConversationModeProfileHead:
    statement = (
        select(ConversationModeProfileHead)
        .where(ConversationModeProfileHead.mode == 'chat')
        .execution_options(populate_existing=True)
    )
    if session.get_bind().dialect.name != 'sqlite':
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one()


async def _assert_task_waits(task) -> None:
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.05)


async def _chat_binding(sessions, chat_id: str) -> str | None:
    async with sessions() as session:
        row = await session.get(Chat, chat_id)
        return row.mode_profile_revision_id if row is not None else None


async def _current_head(sessions) -> str:
    async with sessions() as session:
        head = await session.get(ConversationModeProfileHead, 'chat')
        return head.current_revision_id


def _chat_form(chat_id: str) -> ChatForm:
    return ChatForm(
        chat={
            'id': chat_id,
            'title': 'New Chat',
            'mode': 'chat',
            'history': {'currentId': None, 'messages': {}},
            'messages': [],
        }
    )


def _chat_form_with_messages(chat_id: str) -> ChatForm:
    form = _chat_form(chat_id)
    form.chat['history'] = {
        'currentId': 'assistant-1',
        'messages': {
            'user-1': {
                'id': 'user-1',
                'role': 'user',
                'content': 'hello',
                'childrenIds': ['assistant-1'],
            },
            'assistant-1': {
                'id': 'assistant-1',
                'role': 'assistant',
                'content': '',
                'parentId': 'user-1',
                'childrenIds': [],
                'done': False,
            },
        },
    }
    return form


def _app():
    return SimpleNamespace(state=SimpleNamespace())
