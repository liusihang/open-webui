from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from open_webui.agent.conversation_mode_profiles import ConversationModeProfile
from open_webui.internal.db import Base
from open_webui.models import conversation_mode_profiles as profile_store_module
from open_webui.models.chats import Chat, ChatForm, ChatImportForm, ChatModel, ChatResponse
from open_webui.models.conversation_mode_profiles import (
    AGENT_BASELINE_REVISION_ID,
    CHAT_BASELINE_REVISION_ID,
    ConversationModeProfileBindingConflict,
    ConversationModeProfileHead,
    ConversationModeProfileIntegrityError,
    ConversationModeProfileRevision,
    ConversationModeProfileRevisionConflict,
    ConversationModeProfiles,
    ConversationModeProfileTemporaryBinding,
)
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BASELINE_CONTENT = {
    'schema_version': 1,
    'system_prompt': '',
    'defaults': {},
}


@pytest_asyncio.fixture
async def profile_db(monkeypatch, tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "conversation-mode-profiles.sqlite3"}')
    tables = [
        ConversationModeProfileRevision.__table__,
        ConversationModeProfileHead.__table__,
        ConversationModeProfileTemporaryBinding.__table__,
        Chat.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=tables,
            )
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        now = 100
        for mode, revision_id in (
            ('chat', CHAT_BASELINE_REVISION_ID),
            ('agent', AGENT_BASELINE_REVISION_ID),
        ):
            profile = ConversationModeProfile.from_mapping(mode, BASELINE_CONTENT)
            session.add(
                ConversationModeProfileRevision(
                    id=revision_id,
                    mode=mode,
                    revision_number=1,
                    schema_version=profile.schema_version,
                    system_prompt=profile.system_prompt,
                    defaults=profile.defaults.to_dict(),
                    content_hash=profile.content_hash,
                    created_at=now,
                    created_by=None,
                    restored_from_revision_id=None,
                )
            )
            session.add(
                ConversationModeProfileHead(
                    mode=mode,
                    current_revision_id=revision_id,
                    baseline_revision_id=revision_id,
                    cutover_at=now,
                    updated_at=now,
                    updated_by=None,
                )
            )
        await session.commit()

    @asynccontextmanager
    async def isolated_session(db=None):
        if db is not None:
            yield db
            return
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(profile_store_module, 'get_async_db_context', isolated_session)
    yield session_factory
    await engine.dispose()


async def _insert_chat(session_factory, *, chat_id: str, user_id: str = 'user-1') -> None:
    async with session_factory() as session:
        session.add(
            Chat(
                id=chat_id,
                user_id=user_id,
                title='Test chat',
                chat={
                    'id': chat_id,
                    'title': 'Test chat',
                    'mode': 'chat',
                    'history': {'currentId': None, 'messages': {}},
                },
                created_at=1,
                updated_at=1,
            )
        )
        await session.commit()


def test_chat_models_expose_server_owned_revision_without_form_or_import_authority() -> None:
    assert 'mode_profile_revision_id' in ChatModel.model_fields
    assert 'mode_profile_revision_id' in ChatResponse.model_fields
    assert ChatModel.model_fields['mode_profile_revision_id'].default is None
    assert ChatResponse.model_fields['mode_profile_revision_id'].default is None
    assert 'mode_profile_revision_id' not in ChatForm.model_fields
    assert 'mode_profile_revision_id' not in ChatImportForm.model_fields


@pytest.mark.asyncio
async def test_store_reads_heads_baselines_current_revisions_and_history(profile_db) -> None:
    heads = await ConversationModeProfiles.get_heads()
    assert [head.mode for head in heads] == ['agent', 'chat']

    for mode, baseline_id in (
        ('chat', CHAT_BASELINE_REVISION_ID),
        ('agent', AGENT_BASELINE_REVISION_ID),
    ):
        head = await ConversationModeProfiles.get_head(mode)
        current = await ConversationModeProfiles.get_current_revision(mode)
        baseline = await ConversationModeProfiles.get_baseline_revision(mode)
        by_id = await ConversationModeProfiles.get_revision(baseline_id)
        history = await ConversationModeProfiles.list_history(mode)

        assert head is not None
        assert head.current_revision_id == baseline_id
        assert head.baseline_revision_id == baseline_id
        assert current == baseline == by_id == history[0]
        assert current is not None
        assert current.mode == mode
        assert current.revision_number == 1
        assert current.restored_from_revision_id is None
        assert not isinstance(current, ConversationModeProfileRevision)

        reconstructed = ConversationModeProfile.from_mapping(mode, current.content)
        assert reconstructed.schema_version == 1
        assert reconstructed.system_prompt == ''
        assert reconstructed.defaults.to_dict() == {}
        assert reconstructed.content_hash == current.content_hash


@pytest.mark.asyncio
async def test_store_rejects_tampered_revision_content(profile_db) -> None:
    async with profile_db() as session:
        revision = await session.get(
            ConversationModeProfileRevision,
            CHAT_BASELINE_REVISION_ID,
        )
        revision.content_hash = '0' * 64
        await session.commit()

    with pytest.raises(ConversationModeProfileIntegrityError) as exc_info:
        await ConversationModeProfiles.get_revision(CHAT_BASELINE_REVISION_ID)

    assert exc_info.value.code == 'mode_profile_integrity_error'
    assert exc_info.value.revision_id == CHAT_BASELINE_REVISION_ID


@pytest.mark.asyncio
async def test_save_switches_head_stale_expected_conflicts_and_restore_creates_revision(
    profile_db,
) -> None:
    saved = await ConversationModeProfiles.save_revision(
        mode='chat',
        content={
            'schema_version': 1,
            'system_prompt': 'Administrator prompt',
            'defaults': {'tool_ids': ['tool-1']},
        },
        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
        created_by='admin-1',
        now=200,
    )
    assert saved.revision_number == 2
    assert saved.created_by == 'admin-1'
    assert saved.restored_from_revision_id is None
    assert (await ConversationModeProfiles.get_head('chat')).current_revision_id == saved.id

    with pytest.raises(ConversationModeProfileRevisionConflict) as exc_info:
        await ConversationModeProfiles.save_revision(
            mode='chat',
            content={
                'schema_version': 1,
                'system_prompt': 'Stale overwrite',
                'defaults': {},
            },
            expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
            created_by='admin-2',
            now=201,
        )
    assert exc_info.value.code == 'mode_profile_revision_conflict'
    assert exc_info.value.expected_revision_id == CHAT_BASELINE_REVISION_ID
    assert exc_info.value.actual_revision_id == saved.id

    restored = await ConversationModeProfiles.restore_revision(
        mode='chat',
        source_revision_id=CHAT_BASELINE_REVISION_ID,
        expected_current_revision_id=saved.id,
        created_by='admin-2',
        now=300,
    )
    assert restored.id not in {saved.id, CHAT_BASELINE_REVISION_ID}
    assert restored.revision_number == 3
    assert restored.content == BASELINE_CONTENT
    assert restored.restored_from_revision_id == CHAT_BASELINE_REVISION_ID
    assert (await ConversationModeProfiles.get_current_revision('chat')).id == restored.id
    assert [revision.revision_number for revision in await ConversationModeProfiles.list_history('chat')] == [
        3,
        2,
        1,
    ]


@pytest.mark.asyncio
async def test_concurrent_saves_do_not_share_revision_number_or_lose_head_update(profile_db) -> None:
    start = asyncio.Event()

    async def save(system_prompt: str):
        await start.wait()
        return await ConversationModeProfiles.save_revision(
            mode='agent',
            content={
                'schema_version': 1,
                'system_prompt': system_prompt,
                'defaults': {},
            },
            expected_current_revision_id=AGENT_BASELINE_REVISION_ID,
            created_by='admin-1',
        )

    first = asyncio.create_task(save('First'))
    second = asyncio.create_task(save('Second'))
    start.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    successes = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, ConversationModeProfileRevisionConflict)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert successes[0].revision_number == 2
    assert conflicts[0].actual_revision_id == successes[0].id
    assert (await ConversationModeProfiles.get_head('agent')).current_revision_id == successes[0].id
    assert [revision.revision_number for revision in await ConversationModeProfiles.list_history('agent')] == [
        2,
        1,
    ]


def test_revision_store_has_no_public_content_update_or_delete_path() -> None:
    for method_name in (
        'update_revision',
        'update_revision_content',
        'delete_revision',
        'delete_revision_content',
    ):
        assert not hasattr(ConversationModeProfiles, method_name)


@pytest.mark.asyncio
async def test_persistent_chat_binding_claim_load_and_conflict_are_atomic(profile_db) -> None:
    await _insert_chat(profile_db, chat_id='chat-1')
    new_revision = await ConversationModeProfiles.save_revision(
        mode='chat',
        content={
            'schema_version': 1,
            'system_prompt': 'New',
            'defaults': {},
        },
        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
        created_by='admin-1',
    )

    claimed = await ConversationModeProfiles.claim_chat_binding(
        chat_id='chat-1',
        user_id='user-1',
        revision_id=CHAT_BASELINE_REVISION_ID,
    )
    assert claimed is not None
    assert claimed.mode_profile_revision_id == CHAT_BASELINE_REVISION_ID
    assert (
        await ConversationModeProfiles.get_chat_binding(
            chat_id='chat-1',
            user_id='user-1',
        )
        == claimed
    )
    assert (
        await ConversationModeProfiles.claim_chat_binding(
            chat_id='chat-1',
            user_id='user-1',
            revision_id=CHAT_BASELINE_REVISION_ID,
        )
        == claimed
    )

    with pytest.raises(ConversationModeProfileBindingConflict) as exc_info:
        await ConversationModeProfiles.claim_chat_binding(
            chat_id='chat-1',
            user_id='user-1',
            revision_id=new_revision.id,
        )
    assert exc_info.value.code == 'mode_profile_binding_mismatch'
    assert exc_info.value.actual_revision_id == CHAT_BASELINE_REVISION_ID


@pytest.mark.asyncio
async def test_concurrent_persistent_binding_claims_converge_without_silent_overwrite(profile_db) -> None:
    await _insert_chat(profile_db, chat_id='chat-concurrent')
    new_revision = await ConversationModeProfiles.save_revision(
        mode='chat',
        content={'schema_version': 1, 'system_prompt': 'New', 'defaults': {}},
        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
        created_by='admin-1',
    )
    start = asyncio.Event()

    async def claim(revision_id: str):
        await start.wait()
        return await ConversationModeProfiles.claim_chat_binding(
            chat_id='chat-concurrent',
            user_id='user-1',
            revision_id=revision_id,
        )

    first = asyncio.create_task(claim(CHAT_BASELINE_REVISION_ID))
    second = asyncio.create_task(claim(new_revision.id))
    start.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    successes = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, ConversationModeProfileBindingConflict)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    stored = await ConversationModeProfiles.get_chat_binding(
        chat_id='chat-concurrent',
        user_id='user-1',
    )
    assert stored == successes[0]
    assert conflicts[0].actual_revision_id == stored.mode_profile_revision_id


@pytest.mark.asyncio
async def test_passed_session_chat_claim_starts_with_sqlite_immediate_transaction(profile_db) -> None:
    await _insert_chat(profile_db, chat_id='chat-passed-session')
    statements: list[str] = []
    engine = profile_db.kw['bind']

    def capture_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip())

    event.listen(engine.sync_engine, 'before_cursor_execute', capture_statement)
    try:
        async with profile_db() as session:
            await ConversationModeProfiles.claim_chat_binding(
                chat_id='chat-passed-session',
                user_id='user-1',
                revision_id=CHAT_BASELINE_REVISION_ID,
                db=session,
            )
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', capture_statement)

    assert statements[0] == 'BEGIN IMMEDIATE'


@pytest.mark.asyncio
async def test_passed_session_restore_starts_with_sqlite_immediate_transaction(profile_db) -> None:
    statements: list[str] = []
    engine = profile_db.kw['bind']

    def capture_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip())

    event.listen(engine.sync_engine, 'before_cursor_execute', capture_statement)
    try:
        async with profile_db() as session:
            await ConversationModeProfiles.restore_revision(
                mode='agent',
                source_revision_id=AGENT_BASELINE_REVISION_ID,
                expected_current_revision_id=AGENT_BASELINE_REVISION_ID,
                created_by='admin-1',
                db=session,
            )
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', capture_statement)

    assert statements[0] == 'BEGIN IMMEDIATE'


@pytest.mark.asyncio
async def test_temporary_binding_create_load_refresh_and_expiry_cleanup(profile_db) -> None:
    created = await ConversationModeProfiles.create_temporary_binding(
        user_id='user-1',
        temporary_conversation_id='temporary-1',
        mode='agent',
        expires_at=200,
        now=100,
    )
    assert created.mode == 'agent'
    assert created.mode_profile_revision_id == AGENT_BASELINE_REVISION_ID
    assert (
        await ConversationModeProfiles.get_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='temporary-1',
            now=150,
        )
        == created
    )

    refreshed = await ConversationModeProfiles.create_temporary_binding(
        user_id='user-1',
        temporary_conversation_id='temporary-1',
        mode='agent',
        expires_at=300,
        now=150,
    )
    assert refreshed.id == created.id
    assert refreshed.expires_at == 300

    with pytest.raises(ConversationModeProfileBindingConflict):
        await ConversationModeProfiles.create_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='temporary-1',
            mode='chat',
            expires_at=300,
            now=160,
        )

    assert await ConversationModeProfiles.cleanup_expired_temporary_bindings(now=250) == 0
    assert await ConversationModeProfiles.cleanup_expired_temporary_bindings(now=301) == 1
    assert (
        await ConversationModeProfiles.get_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='temporary-1',
            now=301,
        )
        is None
    )


@pytest.mark.asyncio
async def test_temporary_binding_transfer_claims_chat_and_deletes_temporary_row(profile_db) -> None:
    await _insert_chat(profile_db, chat_id='chat-from-temporary')
    temporary = await ConversationModeProfiles.create_temporary_binding(
        user_id='user-1',
        temporary_conversation_id='temporary-transfer',
        mode='chat',
        expires_at=500,
        now=100,
    )

    transferred = await ConversationModeProfiles.transfer_temporary_binding(
        user_id='user-1',
        temporary_conversation_id='temporary-transfer',
        chat_id='chat-from-temporary',
        now=200,
    )
    assert transferred is not None
    assert transferred.mode_profile_revision_id == temporary.mode_profile_revision_id
    assert (
        await ConversationModeProfiles.get_chat_binding(
            chat_id='chat-from-temporary',
            user_id='user-1',
        )
        == transferred
    )
    assert (
        await ConversationModeProfiles.get_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='temporary-transfer',
            now=200,
        )
        is None
    )

    async with profile_db() as session:
        assert (
            await session.scalar(
                select(ConversationModeProfileTemporaryBinding).where(
                    ConversationModeProfileTemporaryBinding.id == temporary.id
                )
            )
            is None
        )
