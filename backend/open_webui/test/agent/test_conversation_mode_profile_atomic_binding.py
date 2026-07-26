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
from open_webui.models.chats import Chat, ChatForm, Chats
from open_webui.models.conversation_mode_profiles import (
    ConversationModeProfileHead,
    ConversationModeProfileRevision,
)
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
        await connection.run_sync(Chat.__table__.create)

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
async def test_atomic_new_chat_dual_write_runs_only_after_chat_commit(
    atomic_profile_db,
    monkeypatch,
):
    committed_bindings = []

    async def observe_post_commit(chat):
        committed_bindings.append(await _chat_binding(atomic_profile_db, chat.id))

    monkeypatch.setattr(Chats, 'dual_write_initial_messages', observe_post_commit)

    result = await profile_service.insert_new_chat_with_current_mode_profile(
        _app(),
        mode='chat',
        revision_hint=None,
        chat_id='chat-post-commit-dual-write',
        user_id='user-1',
        form_data=_chat_form('chat-post-commit-dual-write'),
    )

    assert result.chat.mode_profile_revision_id == OLD_REVISION_ID
    assert committed_bindings == [OLD_REVISION_ID]


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


def _app():
    return SimpleNamespace(state=SimpleNamespace())
