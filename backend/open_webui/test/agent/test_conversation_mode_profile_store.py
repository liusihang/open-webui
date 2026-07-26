from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from open_webui.agent.conversation_mode_profiles import ConversationModeProfile, ProfileDefaults
from open_webui.internal.db import Base
from open_webui.models import conversation_mode_profiles as profile_store_module
from open_webui.models.chats import Chat, ChatForm, ChatImportForm, ChatModel, ChatResponse
from open_webui.models.conversation_mode_profiles import (
    AGENT_BASELINE_REVISION_ID,
    CHAT_BASELINE_REVISION_ID,
    ConversationModeProfileBindingConflict,
    ConversationModeProfileBindingIntegrityError,
    ConversationModeProfileHead,
    ConversationModeProfileIntegrityError,
    ConversationModeProfileRevision,
    ConversationModeProfileRevisionConflict,
    ConversationModeProfiles,
    ConversationModeProfileTemporaryBinding,
    ConversationModeProfileTransactionStateError,
)
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.sql import Select

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


def _chat_row(*, chat_id: str, user_id: str = 'user-1', mode: str = 'chat') -> Chat:
    return Chat(
        id=chat_id,
        user_id=user_id,
        title='Test chat',
        chat={
            'id': chat_id,
            'title': 'Test chat',
            'mode': mode,
            'history': {'currentId': None, 'messages': {}},
        },
        created_at=1,
        updated_at=1,
    )


async def _insert_chat(
    session_factory,
    *,
    chat_id: str,
    user_id: str = 'user-1',
    mode: str = 'chat',
) -> None:
    async with session_factory() as session:
        session.add(_chat_row(chat_id=chat_id, user_id=user_id, mode=mode))
        await session.commit()


CALLER_WRITE_METHODS = (
    'save',
    'restore',
    'chat_claim',
    'temporary_create',
    'temporary_transfer',
    'temporary_cleanup',
)


async def _invoke_caller_write(method_name: str, session) -> object:
    if method_name == 'save':
        return await ConversationModeProfiles.save_revision(
            mode='chat',
            content={'schema_version': 1, 'system_prompt': 'Caller save', 'defaults': {}},
            expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
            created_by='admin-1',
            db=session,
        )
    if method_name == 'restore':
        return await ConversationModeProfiles.restore_revision(
            mode='chat',
            source_revision_id=CHAT_BASELINE_REVISION_ID,
            expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
            created_by='admin-1',
            db=session,
        )
    if method_name == 'chat_claim':
        return await ConversationModeProfiles.claim_chat_binding(
            chat_id='caller-chat',
            user_id='user-1',
            revision_id=CHAT_BASELINE_REVISION_ID,
            db=session,
        )
    if method_name == 'temporary_create':
        return await ConversationModeProfiles.create_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='caller-temporary',
            mode='chat',
            expires_at=300,
            now=100,
            db=session,
        )
    if method_name == 'temporary_transfer':
        return await ConversationModeProfiles.transfer_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='caller-transfer',
            chat_id='caller-chat',
            now=150,
            db=session,
        )
    if method_name == 'temporary_cleanup':
        return await ConversationModeProfiles.cleanup_expired_temporary_bindings(
            now=250,
            db=session,
        )
    raise AssertionError(f'Unsupported write method: {method_name}')


async def _prepare_successful_caller_write(method_name: str, session_factory) -> None:
    if method_name in {'chat_claim', 'temporary_transfer'}:
        await _insert_chat(session_factory, chat_id='caller-chat')
    if method_name == 'temporary_transfer':
        await ConversationModeProfiles.create_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='caller-transfer',
            mode='chat',
            expires_at=300,
            now=100,
        )
    if method_name == 'temporary_cleanup':
        await ConversationModeProfiles.create_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='caller-cleanup',
            mode='chat',
            expires_at=200,
            now=100,
        )


async def _assert_caller_write_rolled_back(method_name: str) -> None:
    if method_name in {'save', 'restore'}:
        head = await ConversationModeProfiles.get_head('chat')
        assert head.current_revision_id == CHAT_BASELINE_REVISION_ID
        assert [revision.revision_number for revision in await ConversationModeProfiles.list_history('chat')] == [1]
        return
    if method_name == 'chat_claim':
        assert (
            await ConversationModeProfiles.get_chat_binding(
                chat_id='caller-chat',
                user_id='user-1',
            )
            is None
        )
        return
    if method_name == 'temporary_create':
        assert (
            await ConversationModeProfiles.get_temporary_binding(
                user_id='user-1',
                temporary_conversation_id='caller-temporary',
                now=100,
            )
            is None
        )
        return
    if method_name == 'temporary_transfer':
        assert (
            await ConversationModeProfiles.get_chat_binding(
                chat_id='caller-chat',
                user_id='user-1',
            )
            is None
        )
        assert (
            await ConversationModeProfiles.get_temporary_binding(
                user_id='user-1',
                temporary_conversation_id='caller-transfer',
                now=150,
            )
            is not None
        )
        return
    if method_name == 'temporary_cleanup':
        assert (
            await ConversationModeProfiles.get_temporary_binding(
                user_id='user-1',
                temporary_conversation_id='caller-cleanup',
                now=150,
            )
            is not None
        )
        return
    raise AssertionError(f'Unsupported write method: {method_name}')


async def _assert_caller_write_visible_in_session(
    method_name: str,
    session,
    result: object,
) -> None:
    if method_name in {'save', 'restore'}:
        assert (
            await session.scalar(
                select(ConversationModeProfileHead.current_revision_id).where(
                    ConversationModeProfileHead.mode == 'chat'
                )
            )
            == result.id
        )
        return
    if method_name == 'chat_claim':
        assert (
            await session.scalar(select(Chat.mode_profile_revision_id).where(Chat.id == 'caller-chat'))
            == CHAT_BASELINE_REVISION_ID
        )
        return
    if method_name == 'temporary_create':
        assert (
            await session.scalar(
                select(ConversationModeProfileTemporaryBinding.id).where(
                    ConversationModeProfileTemporaryBinding.user_id == 'user-1',
                    ConversationModeProfileTemporaryBinding.temporary_conversation_id == 'caller-temporary',
                )
            )
            == result.id
        )
        return
    if method_name == 'temporary_transfer':
        assert (
            await session.scalar(select(Chat.mode_profile_revision_id).where(Chat.id == 'caller-chat'))
            == CHAT_BASELINE_REVISION_ID
        )
        assert (
            await session.scalar(
                select(ConversationModeProfileTemporaryBinding.id).where(
                    ConversationModeProfileTemporaryBinding.user_id == 'user-1',
                    ConversationModeProfileTemporaryBinding.temporary_conversation_id == 'caller-transfer',
                )
            )
            is None
        )
        return
    if method_name == 'temporary_cleanup':
        assert result == 1
        assert (
            await session.scalar(
                select(ConversationModeProfileTemporaryBinding.id).where(
                    ConversationModeProfileTemporaryBinding.user_id == 'user-1',
                    ConversationModeProfileTemporaryBinding.temporary_conversation_id == 'caller-cleanup',
                )
            )
            is None
        )
        return
    raise AssertionError(f'Unsupported write method: {method_name}')


class _EmptyTemporaryBindingResult:
    def scalars(self):
        return self

    def first(self):
        return None


def _hide_first_temporary_binding_lookup(session, monkeypatch) -> list[Select]:
    original_execute = session.execute
    hidden_statements: list[Select] = []

    async def execute(statement, *args, **kwargs):
        if (
            not hidden_statements
            and isinstance(statement, Select)
            and any(
                description.get('entity') is ConversationModeProfileTemporaryBinding
                for description in statement.column_descriptions
            )
        ):
            hidden_statements.append(statement)
            return _EmptyTemporaryBindingResult()
        return await original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, 'execute', execute)
    return hidden_statements


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


@pytest.mark.parametrize('read_path', ['current', 'history'])
@pytest.mark.parametrize(
    ('row_type', 'malformed_values'),
    [
        ('head', {'updated_at': 'PRIVATE INVALID TIMESTAMP'}),
        ('head', {'updated_by': b'\xffPRIVATE INVALID AUTHOR'}),
        ('revision', {'created_at': 'PRIVATE INVALID TIMESTAMP'}),
        ('revision', {'created_by': b'\xffPRIVATE INVALID AUTHOR'}),
        ('revision', {'defaults': ['PRIVATE INVALID DEFAULTS']}),
    ],
)
@pytest.mark.asyncio
async def test_store_converts_malformed_persisted_rows_to_integrity_error(
    profile_db,
    read_path,
    row_type,
    malformed_values,
) -> None:
    model = ConversationModeProfileHead if row_type == 'head' else ConversationModeProfileRevision
    predicate = (
        ConversationModeProfileHead.mode == 'chat'
        if row_type == 'head'
        else ConversationModeProfileRevision.id == CHAT_BASELINE_REVISION_ID
    )
    async with profile_db() as session:
        await session.execute(update(model).where(predicate).values(**malformed_values))
        await session.commit()

    with pytest.raises(ConversationModeProfileIntegrityError) as exc_info:
        if read_path == 'current':
            await ConversationModeProfiles.get_current_revision('chat')
        else:
            await ConversationModeProfiles.get_history_snapshot('chat')

    assert exc_info.value.code == 'mode_profile_integrity_error'
    assert exc_info.value.revision_id == CHAT_BASELINE_REVISION_ID
    rendered = str(exc_info.value)
    assert 'PRIVATE INVALID' not in rendered
    assert 'system_prompt' not in rendered
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_revision_conversion_converts_persisted_type_error_without_row_content() -> None:
    raw_sentinel = 'PRIVATE INVALID DEFAULT ITERATION'

    class MalformedDefaults(list):
        def __iter__(self):
            raise TypeError(raw_sentinel)

    revision = ConversationModeProfileRevision(
        id=CHAT_BASELINE_REVISION_ID,
        mode='chat',
        revision_number=1,
        schema_version=1,
        system_prompt='PRIVATE SYSTEM PROMPT',
        defaults={'tool_ids': MalformedDefaults(['tool-1'])},
        content_hash='0' * 64,
        created_at=100,
        created_by=None,
        restored_from_revision_id=None,
    )

    with pytest.raises(ConversationModeProfileIntegrityError) as exc_info:
        profile_store_module._revision_to_model(revision, expected_mode='chat')

    assert exc_info.value.revision_id == CHAT_BASELINE_REVISION_ID
    assert raw_sentinel not in str(exc_info.value)
    assert 'PRIVATE SYSTEM PROMPT' not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_revision_dto_uses_deeply_immutable_phase_a_defaults(profile_db) -> None:
    saved = await ConversationModeProfiles.save_revision(
        mode='chat',
        content={
            'schema_version': 1,
            'system_prompt': 'Immutable prompt',
            'defaults': {'tool_ids': ['tool-1']},
        },
        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
        created_by='admin-1',
    )

    assert isinstance(saved.defaults, ProfileDefaults)
    assert saved.defaults.tool_ids == ('tool-1',)
    with pytest.raises(FrozenInstanceError):
        saved.defaults.tool_ids = ('changed',)


@pytest.mark.asyncio
async def test_revision_dto_content_projection_is_deeply_immutable(profile_db) -> None:
    saved = await ConversationModeProfiles.save_revision(
        mode='chat',
        content={
            'schema_version': 1,
            'system_prompt': 'Immutable prompt',
            'defaults': {'tool_ids': ['tool-1']},
        },
        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
        created_by='admin-1',
    )

    content = saved.content
    assert isinstance(content, Mapping)
    assert isinstance(content['defaults'], Mapping)
    assert content['defaults']['tool_ids'] == ('tool-1',)
    with pytest.raises(TypeError):
        content['system_prompt'] = 'changed'
    with pytest.raises(TypeError):
        content['defaults']['tool_ids'] = ('changed',)


@pytest.mark.asyncio
async def test_revision_dto_repr_redacts_system_prompt(profile_db) -> None:
    secret_prompt = 'SECRET ADMINISTRATOR PROMPT'
    saved = await ConversationModeProfiles.save_revision(
        mode='chat',
        content={'schema_version': 1, 'system_prompt': secret_prompt, 'defaults': {}},
        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
        created_by='admin-1',
    )

    assert secret_prompt not in repr(saved)


@pytest.mark.asyncio
async def test_save_precommit_validator_runs_after_head_lock_before_revision_insert(
    profile_db,
    monkeypatch,
) -> None:
    events = []
    original_lock_head = ConversationModeProfiles._lock_head

    async def record_head_lock(session, mode, dialect_name):
        head = await original_lock_head(session, mode, dialect_name)
        events.append(('head_locked', head.current_revision_id))
        return head

    async def validate_before_insert(session, profile):
        revision_ids = list(
            await session.scalars(
                select(ConversationModeProfileRevision.id).where(ConversationModeProfileRevision.mode == 'chat')
            )
        )
        events.append(('validated', tuple(revision_ids), profile.system_prompt))

    monkeypatch.setattr(ConversationModeProfiles, '_lock_head', record_head_lock)

    saved = await ConversationModeProfiles.save_revision(
        mode='chat',
        content={'schema_version': 1, 'system_prompt': 'Validated', 'defaults': {}},
        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
        created_by='admin-1',
        precommit_validator=validate_before_insert,
    )

    assert events == [
        ('head_locked', CHAT_BASELINE_REVISION_ID),
        ('validated', (CHAT_BASELINE_REVISION_ID,), 'Validated'),
    ]
    assert saved.revision_number == 2


@pytest.mark.asyncio
async def test_restore_precommit_validator_failure_rolls_back_without_new_revision(profile_db) -> None:
    async def reject_restore(session, profile):
        raise RuntimeError('precommit rejected')

    with pytest.raises(RuntimeError, match='precommit rejected'):
        await ConversationModeProfiles.restore_revision(
            mode='chat',
            source_revision_id=CHAT_BASELINE_REVISION_ID,
            expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
            created_by='admin-1',
            precommit_validator=reject_restore,
        )

    assert (await ConversationModeProfiles.get_head('chat')).current_revision_id == CHAT_BASELINE_REVISION_ID
    assert [revision.revision_number for revision in await ConversationModeProfiles.list_history('chat')] == [1]


@pytest.mark.asyncio
async def test_history_snapshot_returns_matching_head_and_revisions(profile_db) -> None:
    saved = await ConversationModeProfiles.save_revision(
        mode='chat',
        content={'schema_version': 1, 'system_prompt': 'Snapshot', 'defaults': {}},
        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
        created_by='admin-1',
    )

    snapshot = await ConversationModeProfiles.get_history_snapshot('chat')

    assert snapshot.head.current_revision_id == saved.id
    assert [revision.id for revision in snapshot.revisions] == [
        saved.id,
        CHAT_BASELINE_REVISION_ID,
    ]


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


@pytest.mark.asyncio
async def test_stale_identity_map_head_is_refreshed_before_optimistic_save(profile_db) -> None:
    async with profile_db() as stale_session:
        stale_head = await stale_session.get(ConversationModeProfileHead, 'chat')
        assert stale_head.current_revision_id == CHAT_BASELINE_REVISION_ID
        await stale_session.commit()

        saved = await ConversationModeProfiles.save_revision(
            mode='chat',
            content={'schema_version': 1, 'system_prompt': 'Other administrator', 'defaults': {}},
            expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
            created_by='admin-2',
        )
        assert stale_head.current_revision_id == CHAT_BASELINE_REVISION_ID

        with pytest.raises(ConversationModeProfileRevisionConflict) as exc_info:
            await ConversationModeProfiles.save_revision(
                mode='chat',
                content={'schema_version': 1, 'system_prompt': 'Stale administrator', 'defaults': {}},
                expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
                created_by='admin-1',
                db=stale_session,
            )

        assert exc_info.value.actual_revision_id == saved.id
        assert not stale_session.in_transaction()

    assert (await ConversationModeProfiles.get_head('chat')).current_revision_id == saved.id
    assert [revision.revision_number for revision in await ConversationModeProfiles.list_history('chat')] == [2, 1]


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
async def test_stale_identity_map_chat_is_refreshed_before_binding_claim(profile_db) -> None:
    await _insert_chat(profile_db, chat_id='stale-chat')
    newer_revision = await ConversationModeProfiles.save_revision(
        mode='chat',
        content={'schema_version': 1, 'system_prompt': 'New revision', 'defaults': {}},
        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
        created_by='admin-1',
    )

    async with profile_db() as stale_session:
        stale_chat = await stale_session.get(Chat, 'stale-chat')
        assert stale_chat.mode_profile_revision_id is None
        await stale_session.commit()

        await ConversationModeProfiles.claim_chat_binding(
            chat_id='stale-chat',
            user_id='user-1',
            revision_id=CHAT_BASELINE_REVISION_ID,
        )
        assert stale_chat.mode_profile_revision_id is None

        with pytest.raises(ConversationModeProfileBindingConflict) as exc_info:
            await ConversationModeProfiles.claim_chat_binding(
                chat_id='stale-chat',
                user_id='user-1',
                revision_id=newer_revision.id,
                db=stale_session,
            )

        assert exc_info.value.actual_revision_id == CHAT_BASELINE_REVISION_ID
        assert not stale_session.in_transaction()

    stored = await ConversationModeProfiles.get_chat_binding(
        chat_id='stale-chat',
        user_id='user-1',
    )
    assert stored.mode_profile_revision_id == CHAT_BASELINE_REVISION_ID


@pytest.mark.parametrize(
    ('chat_mode', 'revision_id', 'revision_mode'),
    [
        ('chat', AGENT_BASELINE_REVISION_ID, 'agent'),
        ('agent', CHAT_BASELINE_REVISION_ID, 'chat'),
    ],
)
@pytest.mark.asyncio
async def test_explicit_chat_mode_rejects_mismatched_revision_claim(
    profile_db,
    chat_mode: str,
    revision_id: str,
    revision_mode: str,
) -> None:
    await _insert_chat(profile_db, chat_id='mode-mismatch-chat', mode=chat_mode)

    with pytest.raises(ConversationModeProfileBindingIntegrityError) as exc_info:
        await ConversationModeProfiles.claim_chat_binding(
            chat_id='mode-mismatch-chat',
            user_id='user-1',
            revision_id=revision_id,
        )

    assert exc_info.value.code == 'mode_profile_binding_integrity_error'
    assert exc_info.value.chat_mode == chat_mode
    assert exc_info.value.revision_mode == revision_mode
    assert (
        await ConversationModeProfiles.get_chat_binding(
            chat_id='mode-mismatch-chat',
            user_id='user-1',
        )
        is None
    )


@pytest.mark.parametrize(
    ('chat_mode', 'temporary_mode'),
    [
        ('chat', 'agent'),
        ('agent', 'chat'),
    ],
)
@pytest.mark.asyncio
async def test_explicit_chat_mode_rejects_mismatched_temporary_transfer(
    profile_db,
    chat_mode: str,
    temporary_mode: str,
) -> None:
    await _insert_chat(profile_db, chat_id='temporary-mode-mismatch', mode=chat_mode)
    temporary = await ConversationModeProfiles.create_temporary_binding(
        user_id='user-1',
        temporary_conversation_id='temporary-mode-mismatch',
        mode=temporary_mode,
        expires_at=300,
        now=100,
    )

    with pytest.raises(ConversationModeProfileBindingIntegrityError) as exc_info:
        await ConversationModeProfiles.transfer_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='temporary-mode-mismatch',
            chat_id='temporary-mode-mismatch',
            now=150,
        )

    assert exc_info.value.code == 'mode_profile_binding_integrity_error'
    assert exc_info.value.chat_mode == chat_mode
    assert exc_info.value.revision_mode == temporary_mode
    assert (
        await ConversationModeProfiles.get_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='temporary-mode-mismatch',
            now=150,
        )
        == temporary
    )
    assert (
        await ConversationModeProfiles.get_chat_binding(
            chat_id='temporary-mode-mismatch',
            user_id='user-1',
        )
        is None
    )


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


@pytest.mark.parametrize('method_name', CALLER_WRITE_METHODS)
@pytest.mark.asyncio
async def test_write_methods_reject_active_caller_transaction_without_ending_it(
    profile_db,
    method_name: str,
) -> None:
    async with profile_db() as session:
        await session.begin()

        with pytest.raises(ConversationModeProfileTransactionStateError) as exc_info:
            await _invoke_caller_write(method_name, session)

        assert exc_info.value.code == 'mode_profile_transaction_state_error'
        assert exc_info.value.reason == 'active_transaction'
        assert session.in_transaction()
        await session.rollback()


@pytest.mark.parametrize('method_name', CALLER_WRITE_METHODS)
@pytest.mark.asyncio
async def test_write_methods_reject_pending_caller_work_without_committing_or_rolling_back(
    profile_db,
    method_name: str,
) -> None:
    unrelated = _chat_row(chat_id=f'unrelated-{method_name}')
    async with profile_db() as session:
        session.add(unrelated)
        assert unrelated in session.new

        with pytest.raises(ConversationModeProfileTransactionStateError) as exc_info:
            await _invoke_caller_write(method_name, session)

        assert exc_info.value.code == 'mode_profile_transaction_state_error'
        assert exc_info.value.reason == 'pending_work'
        assert unrelated in session.new
        assert session.in_transaction()
        await session.commit()

    async with profile_db() as verification_session:
        assert await verification_session.get(Chat, unrelated.id) is not None


@pytest.mark.parametrize('method_name', CALLER_WRITE_METHODS)
@pytest.mark.asyncio
async def test_clean_caller_session_write_remains_uncommitted_until_caller_finishes(
    profile_db,
    method_name: str,
) -> None:
    await _prepare_successful_caller_write(method_name, profile_db)

    async with profile_db() as session:
        await _invoke_caller_write(method_name, session)
        assert session.in_transaction()
        await session.rollback()

    await _assert_caller_write_rolled_back(method_name)


@pytest.mark.parametrize('method_name', CALLER_WRITE_METHODS)
@pytest.mark.asyncio
async def test_clean_caller_session_with_autoflush_disabled_flushes_before_return(
    profile_db,
    method_name: str,
) -> None:
    await _prepare_successful_caller_write(method_name, profile_db)
    caller_sessions = async_sessionmaker(
        profile_db.kw['bind'],
        expire_on_commit=False,
        autoflush=False,
    )

    async with caller_sessions() as session:
        assert session.autoflush is False
        result = await _invoke_caller_write(method_name, session)

        assert session.in_transaction()
        await _assert_caller_write_visible_in_session(method_name, session, result)
        await session.rollback()

    await _assert_caller_write_rolled_back(method_name)


@pytest.mark.asyncio
async def test_sqlite_immediate_begin_failure_clears_attempted_caller_transaction(
    profile_db,
) -> None:
    engine = profile_db.kw['bind']
    async with engine.connect() as blocker, engine.connect() as caller_connection:
        await caller_connection.exec_driver_sql('PRAGMA busy_timeout = 0')
        await caller_connection.commit()
        await blocker.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            caller_sessions = async_sessionmaker(
                caller_connection,
                expire_on_commit=False,
                autoflush=False,
            )
            async with caller_sessions() as session:
                with pytest.raises(OperationalError, match='database is locked'):
                    await ConversationModeProfiles.save_revision(
                        mode='chat',
                        content={
                            'schema_version': 1,
                            'system_prompt': 'Never saved',
                            'defaults': {},
                        },
                        expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
                        created_by='admin-1',
                        db=session,
                    )

                assert not session.in_transaction()
        finally:
            await blocker.rollback()


@pytest.mark.asyncio
async def test_cancellation_during_managed_write_rolls_back_caller_transaction(
    profile_db,
    monkeypatch,
) -> None:
    caller_sessions = async_sessionmaker(
        profile_db.kw['bind'],
        expire_on_commit=False,
        autoflush=False,
    )

    async def cancel_lock(session, mode, dialect_name):
        raise asyncio.CancelledError

    monkeypatch.setattr(ConversationModeProfiles, '_lock_head', cancel_lock)

    async with caller_sessions() as session:
        with pytest.raises(asyncio.CancelledError):
            await ConversationModeProfiles.save_revision(
                mode='chat',
                content={'schema_version': 1, 'system_prompt': 'Cancelled', 'defaults': {}},
                expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
                created_by='admin-1',
                db=session,
            )

        assert not session.in_transaction()


@pytest.mark.asyncio
async def test_clean_caller_session_can_commit_successful_repository_write(profile_db) -> None:
    async with profile_db() as session:
        saved = await ConversationModeProfiles.save_revision(
            mode='chat',
            content={'schema_version': 1, 'system_prompt': 'Caller commits', 'defaults': {}},
            expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
            created_by='admin-1',
            db=session,
        )
        assert session.in_transaction()
        assert (await ConversationModeProfiles.get_head('chat')).current_revision_id == CHAT_BASELINE_REVISION_ID
        await session.commit()

    assert (await ConversationModeProfiles.get_head('chat')).current_revision_id == saved.id


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
async def test_temporary_binding_creation_locks_only_requested_mode_head(profile_db) -> None:
    statements: list[tuple[str, object]] = []
    engine = profile_db.kw['bind']

    def capture_statement(connection, cursor, statement, parameters, context, executemany):
        if 'FROM conversation_mode_profile_head' in statement:
            statements.append((statement, parameters))

    event.listen(engine.sync_engine, 'before_cursor_execute', capture_statement)
    try:
        await ConversationModeProfiles.create_temporary_binding(
            user_id='user-1',
            temporary_conversation_id='requested-head-only',
            mode='agent',
            expires_at=300,
            now=100,
        )
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', capture_statement)

    assert len(statements) == 1
    statement, parameters = statements[0]
    assert 'WHERE conversation_mode_profile_head.mode = ?' in ' '.join(statement.split())
    assert parameters == ('agent',)


@pytest.mark.asyncio
async def test_temporary_binding_insert_race_returns_fresh_same_mode_winner(
    profile_db,
    monkeypatch,
) -> None:
    winner = await ConversationModeProfiles.create_temporary_binding(
        user_id='race-user',
        temporary_conversation_id='race-temporary',
        mode='agent',
        expires_at=250,
        now=100,
    )
    caller_sessions = async_sessionmaker(
        profile_db.kw['bind'],
        expire_on_commit=False,
        autoflush=False,
    )

    async with caller_sessions() as session:
        hidden_statements = _hide_first_temporary_binding_lookup(session, monkeypatch)

        recovered = await ConversationModeProfiles.create_temporary_binding(
            user_id='race-user',
            temporary_conversation_id='race-temporary',
            mode='agent',
            expires_at=300,
            now=150,
            db=session,
        )

        assert len(hidden_statements) == 1
        assert recovered.id == winner.id
        assert recovered.mode_profile_revision_id == winner.mode_profile_revision_id
        assert recovered.expires_at == 300
        assert session.in_transaction()
        assert (
            await session.scalar(
                select(ConversationModeProfileTemporaryBinding.id).where(
                    ConversationModeProfileTemporaryBinding.user_id == 'race-user',
                    ConversationModeProfileTemporaryBinding.temporary_conversation_id == 'race-temporary',
                )
            )
            == winner.id
        )
        await session.rollback()


@pytest.mark.asyncio
async def test_temporary_binding_insert_race_raises_typed_cross_mode_conflict(
    profile_db,
    monkeypatch,
) -> None:
    winner = await ConversationModeProfiles.create_temporary_binding(
        user_id='cross-mode-race-user',
        temporary_conversation_id='cross-mode-race-temporary',
        mode='agent',
        expires_at=300,
        now=100,
    )
    caller_sessions = async_sessionmaker(
        profile_db.kw['bind'],
        expire_on_commit=False,
        autoflush=False,
    )

    async with caller_sessions() as session:
        hidden_statements = _hide_first_temporary_binding_lookup(session, monkeypatch)

        with pytest.raises(ConversationModeProfileBindingConflict) as exc_info:
            await ConversationModeProfiles.create_temporary_binding(
                user_id='cross-mode-race-user',
                temporary_conversation_id='cross-mode-race-temporary',
                mode='chat',
                expires_at=300,
                now=150,
                db=session,
            )

        assert len(hidden_statements) == 1
        assert exc_info.value.binding_id == 'cross-mode-race-user:cross-mode-race-temporary'
        assert exc_info.value.expected_revision_id == CHAT_BASELINE_REVISION_ID
        assert exc_info.value.actual_revision_id == AGENT_BASELINE_REVISION_ID
        assert not session.in_transaction()

    stored = await ConversationModeProfiles.get_temporary_binding(
        user_id='cross-mode-race-user',
        temporary_conversation_id='cross-mode-race-temporary',
        now=150,
    )
    assert stored == winner


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
