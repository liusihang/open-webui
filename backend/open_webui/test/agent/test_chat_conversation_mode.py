from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from open_webui.agent.conversation_mode import (
    ConversationModeMismatchError,
    InvalidConversationModeError,
    resolve_conversation_mode,
)
from open_webui.agent.conversation_mode_profile_service import ModeProfileServiceUnavailableError
from open_webui.models import chats as chats_model_module
from open_webui.models.chats import (
    Chat,
    ChatForm,
    ChatImportForm,
    ChatModel,
    Chats,
    ChatsImportForm,
)
from open_webui.models.conversation_mode_profiles import ConversationModeProfileIntegrityError
from open_webui.routers import chats as chats_router
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _chat_model(*, mode: str | None, agent_message: bool = False) -> ChatModel:
    assistant = {
        'id': 'assistant-1',
        'role': 'assistant',
        'content': 'response',
    }
    if agent_message:
        assistant['agent_run_id'] = 'run-1'

    chat = {
        'id': 'chat-1',
        'title': 'Conversation',
        'history': {
            'currentId': 'assistant-1',
            'messages': {'assistant-1': assistant},
        },
        'messages': [],
    }
    if mode is not None:
        chat['mode'] = mode

    return ChatModel(
        id='chat-1',
        user_id='user-1',
        title='Conversation',
        chat=chat,
        created_at=1,
        updated_at=1,
    )


def _patch_update_boundaries(
    monkeypatch,
    *,
    stored: ChatModel,
    has_agent_run: bool = False,
    claimed_mode: str | None = None,
):
    updates = []

    async def get_chat(chat_id, user_id, db=None):
        return stored

    async def update_chat(chat_id, updated_chat, db=None):
        updates.append(updated_chat)
        return stored.model_copy(
            update={
                'chat': updated_chat,
                'title': updated_chat.get('title', stored.title),
                'updated_at': stored.updated_at + 1,
            }
        )

    async def reconcile(*args, **kwargs):
        return None

    async def publish(*args, **kwargs):
        return None

    async def has_runs(chat_id, user_id, db=None):
        return has_agent_run

    async def resolve_binding(
        *,
        chat_id,
        user_id,
        requested_mode,
        has_agent_run,
        db=None,
    ):
        canonical = stored
        if claimed_mode is not None:
            canonical = stored.model_copy(
                update={'chat': {**stored.chat, 'mode': claimed_mode}}
            )
        resolution = resolve_conversation_mode(
            requested=requested_mode,
            persisted=(canonical.chat or {}).get('mode'),
            is_new=False,
            has_agent_run=has_agent_run,
        )
        if resolution.should_persist:
            canonical = canonical.model_copy(
                update={
                    'chat': {
                        **canonical.chat,
                        'mode': resolution.mode.value,
                    }
                }
            )
        return SimpleNamespace(
            mode=resolution.mode.value,
            mode_profile_revision_id=canonical.mode_profile_revision_id or 'chat-baseline',
        )

    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id_and_user_id', get_chat)
    monkeypatch.setattr(chats_router.Chats, 'update_chat_by_id', update_chat)
    monkeypatch.setattr(chats_router.ConversationModeProfiles, 'resolve_persisted_chat_binding', resolve_binding)
    monkeypatch.setattr(chats_router.Chats, 'reconcile_messages_by_chat_id', reconcile)
    monkeypatch.setattr(chats_router, 'publish_event', publish)
    monkeypatch.setattr(
        chats_router,
        'AgentRuns',
        SimpleNamespace(has_runs_by_chat=has_runs),
        raising=False,
    )
    return updates


def _patch_get_boundaries(
    monkeypatch,
    *,
    stored: ChatModel,
    has_agent_run: bool = False,
):
    async def get_chat(chat_id, user_id, db=None):
        return stored

    async def has_runs(chat_id, user_id, db=None):
        return has_agent_run

    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id_and_user_id', get_chat)
    monkeypatch.setattr(
        chats_router.AgentRuns,
        'has_runs_by_chat',
        has_runs,
    )


@pytest.mark.asyncio
async def test_get_chat_returns_inferred_legacy_agent_message_mode(monkeypatch) -> None:
    stored = _chat_model(mode=None, agent_message=True)
    _patch_get_boundaries(monkeypatch, stored=stored)

    response = await chats_router.get_chat_by_id(
        stored.id,
        SimpleNamespace(id=stored.user_id, role='user'),
        None,
    )

    assert response.chat['mode'] == 'agent'


@pytest.mark.asyncio
async def test_get_chat_returns_inferred_legacy_agent_run_mode(monkeypatch) -> None:
    stored = _chat_model(mode=None)
    _patch_get_boundaries(monkeypatch, stored=stored, has_agent_run=True)

    response = await chats_router.get_chat_by_id(
        stored.id,
        SimpleNamespace(id=stored.user_id, role='user'),
        None,
    )

    assert response.chat['mode'] == 'agent'


@pytest.mark.asyncio
async def test_get_chat_reports_invalid_persisted_mode(monkeypatch) -> None:
    stored = _chat_model(mode='work')
    _patch_get_boundaries(monkeypatch, stored=stored)

    with pytest.raises(chats_router.HTTPException) as exc_info:
        await chats_router.get_chat_by_id(
            stored.id,
            SimpleNamespace(id=stored.user_id, role='user'),
            None,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail['code'] == 'invalid_persisted_conversation_mode'


@pytest.mark.asyncio
async def test_shared_get_uses_original_chat_id_for_legacy_agent_run_mode(
    monkeypatch,
) -> None:
    snapshot = _chat_model(mode=None).model_copy(update={'id': 'share-1'})
    run_queries = []

    async def get_snapshot(share_id, db=None):
        return snapshot

    async def get_shared(share_id, db=None):
        return SimpleNamespace(
            id=share_id,
            chat_id='chat-1',
            user_id='user-1',
        )

    async def has_runs(chat_id, user_id, db=None):
        run_queries.append((chat_id, user_id))
        return True

    async def get_source(chat_id, *, db=None, repair=True, strict=False):
        assert chat_id == 'chat-1'
        return _chat_model(mode=None).model_copy(update={'mode_profile_revision_id': 'chat-source-revision'})

    async def resolve_source(**kwargs):
        return SimpleNamespace(mode_profile_revision_id='chat-source-revision')

    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_share_id', get_snapshot)
    monkeypatch.setattr(chats_router.SharedChats, 'get_by_id', get_shared)
    monkeypatch.setattr(chats_router.AgentRuns, 'has_runs_by_chat', has_runs)
    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id', get_source)
    monkeypatch.setattr(chats_router.ConversationModeProfiles, 'resolve_persisted_chat_binding', resolve_source)

    response = await chats_router.get_shared_chat_by_id(
        'share-1',
        SimpleNamespace(id='user-1', role='user'),
        None,
    )

    assert response.chat['mode'] == 'agent'
    assert run_queries == [('chat-1', 'user-1'), ('chat-1', 'user-1')]


@pytest.mark.asyncio
async def test_concurrent_legacy_mode_claim_allows_only_one_mode(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mode-claim.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Chat.__table__.create)

    async with session_factory() as session:
        session.add(
            Chat(
                id='chat-1',
                user_id='user-1',
                title='Legacy',
                chat=_chat_model(mode=None).chat,
                created_at=1,
                updated_at=1,
            )
        )
        await session.commit()

    @asynccontextmanager
    async def isolated_session(db=None):
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(chats_model_module, 'get_async_db_context', isolated_session)

    results = await asyncio.gather(
        Chats.claim_conversation_mode(
            'chat-1',
            requested='chat',
            user_id='user-1',
            has_agent_run=False,
        ),
        Chats.claim_conversation_mode(
            'chat-1',
            requested='agent',
            user_id='user-1',
            has_agent_run=False,
        ),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, Exception)]
    conflicts = [
        result
        for result in results
        if isinstance(result, ConversationModeMismatchError)
    ]
    assert len(successes) == 1
    assert len(conflicts) == 1

    async with session_factory() as session:
        stored = await session.scalar(select(Chat).where(Chat.id == 'chat-1'))
        assert stored.chat['mode'] == successes[0][1].mode.value

    await engine.dispose()


@pytest.mark.asyncio
async def test_generic_update_rejects_explicit_mode_mutation(monkeypatch) -> None:
    stored = _chat_model(mode='chat')
    updates = _patch_update_boundaries(monkeypatch, stored=stored)

    with pytest.raises(chats_router.HTTPException) as exc_info:
        await chats_router.update_chat_by_id(
            SimpleNamespace(app=SimpleNamespace()),
            stored.id,
            ChatForm(chat={'mode': 'agent', 'title': 'Changed'}),
            SimpleNamespace(id=stored.user_id),
            None,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail['code'] == 'conversation_mode_mismatch'
    assert updates == []


@pytest.mark.asyncio
async def test_generic_update_rejects_invalid_mode(monkeypatch) -> None:
    stored = _chat_model(mode='chat')
    updates = _patch_update_boundaries(monkeypatch, stored=stored)

    with pytest.raises(chats_router.HTTPException) as exc_info:
        await chats_router.update_chat_by_id(
            SimpleNamespace(),
            stored.id,
            ChatForm(chat={'mode': 'work'}),
            SimpleNamespace(id=stored.user_id),
            None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail['code'] == 'invalid_conversation_mode'
    assert updates == []


@pytest.mark.asyncio
async def test_generic_update_preserves_mode_when_payload_omits_it(monkeypatch) -> None:
    stored = _chat_model(mode='agent')
    updates = _patch_update_boundaries(monkeypatch, stored=stored)

    response = await chats_router.update_chat_by_id(
        SimpleNamespace(),
        stored.id,
        ChatForm(chat={'title': 'Changed'}),
        SimpleNamespace(id=stored.user_id),
        None,
    )

    assert response.chat['mode'] == 'agent'
    assert updates[0]['mode'] == 'agent'


@pytest.mark.asyncio
async def test_generic_update_locks_legacy_agent_message_mode(monkeypatch) -> None:
    stored = _chat_model(mode=None, agent_message=True)
    updates = _patch_update_boundaries(monkeypatch, stored=stored)

    with pytest.raises(chats_router.HTTPException) as exc_info:
        await chats_router.update_chat_by_id(
            SimpleNamespace(),
            stored.id,
            ChatForm(chat={'mode': 'chat'}),
            SimpleNamespace(id=stored.user_id),
            None,
        )

    assert exc_info.value.status_code == 409
    assert updates == []


@pytest.mark.asyncio
async def test_generic_update_locks_legacy_agent_run_mode(monkeypatch) -> None:
    stored = _chat_model(mode=None)
    updates = _patch_update_boundaries(
        monkeypatch,
        stored=stored,
        has_agent_run=True,
    )

    with pytest.raises(chats_router.HTTPException) as exc_info:
        await chats_router.update_chat_by_id(
            SimpleNamespace(),
            stored.id,
            ChatForm(chat={'mode': 'chat'}),
            SimpleNamespace(id=stored.user_id),
            None,
        )

    assert exc_info.value.status_code == 409
    assert updates == []


@pytest.mark.asyncio
async def test_generic_update_persists_inferred_legacy_chat_mode(monkeypatch) -> None:
    stored = _chat_model(mode=None)
    updates = _patch_update_boundaries(monkeypatch, stored=stored)

    response = await chats_router.update_chat_by_id(
        SimpleNamespace(),
        stored.id,
        ChatForm(chat={'title': 'Changed'}),
        SimpleNamespace(id=stored.user_id),
        None,
    )

    assert response.chat['mode'] == 'chat'
    assert updates[0]['mode'] == 'chat'


@pytest.mark.asyncio
async def test_generic_update_merges_from_atomic_mode_claim_result(monkeypatch) -> None:
    stored = _chat_model(mode=None)
    updates = _patch_update_boundaries(
        monkeypatch,
        stored=stored,
        claimed_mode='agent',
    )

    response = await chats_router.update_chat_by_id(
        SimpleNamespace(),
        stored.id,
        ChatForm(chat={'title': 'Changed'}),
        SimpleNamespace(id=stored.user_id),
        None,
    )

    assert response.chat['mode'] == 'agent'
    assert updates[0]['mode'] == 'agent'


@pytest.mark.asyncio
async def test_generic_update_resolves_and_claims_profile_binding_before_save(monkeypatch) -> None:
    stored = _chat_model(mode=None)
    updates = []
    resolutions = []

    async def get_chat(chat_id, user_id, db=None):
        return stored

    async def resolve_binding(**kwargs):
        resolutions.append(kwargs)
        return SimpleNamespace(mode='chat', mode_profile_revision_id='chat-baseline')

    async def update_chat(chat_id, updated_chat, db=None):
        updates.append(updated_chat)
        return stored.model_copy(
            update={
                'chat': updated_chat,
                'mode_profile_revision_id': 'chat-baseline',
            }
        )

    async def old_claim(*args, **kwargs):
        raise AssertionError('generic update must not use the mode-only legacy claimant')

    async def reconcile(*args, **kwargs):
        return None

    async def has_runs(*args, **kwargs):
        return False

    async def publish(*args, **kwargs):
        return None

    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id_and_user_id', get_chat)
    monkeypatch.setattr(chats_router.Chats, 'update_chat_by_id', update_chat)
    monkeypatch.setattr(chats_router.Chats, 'claim_conversation_mode', old_claim)
    monkeypatch.setattr(chats_router.ConversationModeProfiles, 'resolve_persisted_chat_binding', resolve_binding)
    monkeypatch.setattr(chats_router.Chats, 'reconcile_messages_by_chat_id', reconcile)
    monkeypatch.setattr(chats_router, 'publish_event', publish)
    monkeypatch.setattr(chats_router.AgentRuns, 'has_runs_by_chat', has_runs)

    response = await chats_router.update_chat_by_id(
        SimpleNamespace(),
        stored.id,
        ChatForm(chat={'title': 'Updated', 'mode_profile_revision_id': 'spoofed'}),
        SimpleNamespace(id=stored.user_id),
        None,
    )

    assert resolutions == [
        {
            'chat_id': stored.id,
            'user_id': stored.user_id,
            'requested_mode': None,
            'has_agent_run': False,
        }
    ]
    assert updates == [
        {
            'id': 'chat-1',
            'title': 'Updated',
            'history': stored.chat['history'],
            'messages': [],
            'mode': 'chat',
        }
    ]
    assert response.mode_profile_revision_id == 'chat-baseline'


@pytest.mark.asyncio
async def test_create_and_import_map_profile_integrity_to_stable_500(monkeypatch) -> None:
    async def fail_create(*args, **kwargs):
        raise ConversationModeProfileIntegrityError('revision-id', 'private profile corruption')

    async def allow_import(*args, **kwargs):
        return None

    async def fail_import(*args, **kwargs):
        raise ConversationModeProfileIntegrityError('revision-id', 'private profile corruption')

    monkeypatch.setattr(chats_router, 'insert_new_chat_with_current_mode_profile', fail_create)
    with pytest.raises(chats_router.HTTPException) as create_exc:
        await chats_router.create_new_chat(
            SimpleNamespace(app=SimpleNamespace()),
            ChatForm(chat=_chat_model(mode='chat').chat),
            SimpleNamespace(id='user-1'),
            None,
        )

    monkeypatch.setattr(chats_router, 'require_chat_import_permission', allow_import)
    monkeypatch.setattr(chats_router, 'import_chats_with_mode_profile_bindings', fail_import)
    with pytest.raises(chats_router.HTTPException) as import_exc:
        await chats_router.import_chats(
            SimpleNamespace(app=SimpleNamespace()),
            ChatsImportForm(chats=[ChatImportForm(chat=_chat_model(mode='chat').chat)]),
            SimpleNamespace(id='user-1'),
            None,
        )

    for exc_info in (create_exc, import_exc):
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == {
            'code': 'mode_profile_integrity_error',
            'message': 'The conversation mode profile binding failed integrity verification.',
        }


@pytest.mark.asyncio
@pytest.mark.parametrize('shared', [False, True])
async def test_clone_paths_map_profile_service_unavailable_to_stable_503(monkeypatch, shared) -> None:
    source = _chat_model(mode='chat').model_copy(update={'mode_profile_revision_id': 'chat-source'})

    async def allow_import(*args, **kwargs):
        return None

    async def get_owned(*args, **kwargs):
        return source

    async def get_shared(*args, **kwargs):
        return SimpleNamespace(id='share-1', chat_id=source.id, user_id=source.user_id)

    async def unavailable(*args, **kwargs):
        raise ModeProfileServiceUnavailableError('read_current_head', mode='chat')

    monkeypatch.setattr(chats_router, 'require_chat_import_permission', allow_import)
    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id_and_user_id', get_owned)
    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id', get_owned)
    monkeypatch.setattr(chats_router.SharedChats, 'get_by_id', get_shared)
    monkeypatch.setattr(chats_router.ConversationModeProfiles, 'resolve_persisted_chat_binding', unavailable)

    with pytest.raises(chats_router.HTTPException) as exc_info:
        if shared:
            await chats_router.clone_shared_chat_by_id(
                SimpleNamespace(app=SimpleNamespace()), 'share-1', SimpleNamespace(id='user-1', role='user'), None
            )
        else:
            await chats_router.clone_chat_by_id(
                SimpleNamespace(app=SimpleNamespace()),
                chats_router.CloneForm(),
                source.id,
                SimpleNamespace(id='user-1'),
                None,
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        'code': 'mode_profile_unavailable',
        'message': 'The conversation mode profile service is temporarily unavailable.',
    }


def test_import_preserves_explicit_mode() -> None:
    imported = Chats._chat_import_form_to_chat_model(
        'user-1',
        ChatImportForm(chat=_chat_model(mode='agent').chat),
    )

    assert imported.chat['mode'] == 'agent'


def test_import_infers_missing_agent_mode_from_history() -> None:
    imported = Chats._chat_import_form_to_chat_model(
        'user-1',
        ChatImportForm(chat=_chat_model(mode=None, agent_message=True).chat),
    )

    assert imported.chat['mode'] == 'agent'


def test_import_defaults_missing_ordinary_mode_to_chat() -> None:
    imported = Chats._chat_import_form_to_chat_model(
        'user-1',
        ChatImportForm(chat=_chat_model(mode=None).chat),
    )

    assert imported.chat['mode'] == 'chat'


def test_import_rejects_invalid_mode() -> None:
    with pytest.raises(InvalidConversationModeError):
        Chats._chat_import_form_to_chat_model(
            'user-1',
            ChatImportForm(chat=_chat_model(mode='work').chat),
        )


@pytest.mark.asyncio
async def test_create_chat_endpoint_defaults_missing_mode_to_chat(monkeypatch) -> None:
    captured = []

    async def insert_bound_chat(
        app,
        *,
        mode,
        revision_hint,
        chat_id,
        user_id,
        form_data,
        source_temporary_conversation_id=None,
    ):
        captured.append(form_data.chat)
        return SimpleNamespace(
            chat=_chat_model(mode=form_data.chat.get('mode')).model_copy(
                update={'id': chat_id, 'chat': form_data.chat}
            )
        )

    async def publish(*args, **kwargs):
        return None

    monkeypatch.setattr(chats_router, 'insert_new_chat_with_current_mode_profile', insert_bound_chat)
    monkeypatch.setattr(chats_router, 'publish_event', publish)

    response = await chats_router.create_new_chat(
        SimpleNamespace(app=SimpleNamespace()),
        ChatForm(chat=_chat_model(mode=None).chat),
        SimpleNamespace(id='user-1'),
        None,
    )

    assert response.chat['mode'] == 'chat'
    assert captured[0]['mode'] == 'chat'


@pytest.mark.asyncio
async def test_create_chat_endpoint_uses_server_validated_temporary_source(monkeypatch) -> None:
    captured = {}

    async def insert_bound_chat(
        app,
        *,
        mode,
        revision_hint,
        chat_id,
        user_id,
        form_data,
        source_temporary_conversation_id=None,
    ):
        captured['mode'] = mode
        captured['source_temporary_conversation_id'] = source_temporary_conversation_id
        return SimpleNamespace(
            chat=_chat_model(mode='chat').model_copy(
                update={'id': chat_id, 'user_id': user_id, 'chat': form_data.chat}
            )
        )

    async def unexpected_unbound_insert(*args, **kwargs):
        raise AssertionError('new chat must use the bound mode profile service')

    async def publish(*args, **kwargs):
        return None

    monkeypatch.setattr(chats_router, 'insert_new_chat_with_current_mode_profile', insert_bound_chat)
    monkeypatch.setattr(chats_router.Chats, 'insert_new_chat', unexpected_unbound_insert)
    monkeypatch.setattr(chats_router, 'publish_event', publish)

    response = await chats_router.create_new_chat(
        SimpleNamespace(app=SimpleNamespace()),
        ChatForm(
            chat=_chat_model(mode='chat').chat,
            source_temporary_conversation_id='local:save-to-history',
        ),
        SimpleNamespace(id='user-1'),
        None,
    )

    assert response.mode_profile_revision_id is None
    assert captured['mode'] == 'chat'
    assert captured['source_temporary_conversation_id'] == 'local:save-to-history'


@pytest.mark.asyncio
async def test_create_chat_endpoint_rejects_invalid_mode_before_insert(monkeypatch) -> None:
    inserted = False

    async def insert_chat(*args, **kwargs):
        nonlocal inserted
        inserted = True
        return _chat_model(mode='work')

    async def publish(*args, **kwargs):
        return None

    monkeypatch.setattr(chats_router.Chats, 'insert_new_chat', insert_chat)
    monkeypatch.setattr(chats_router, 'publish_event', publish)

    with pytest.raises(chats_router.HTTPException) as exc_info:
        await chats_router.create_new_chat(
            SimpleNamespace(),
            ChatForm(chat=_chat_model(mode='work').chat),
            SimpleNamespace(id='user-1'),
            None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail['code'] == 'invalid_conversation_mode'
    assert inserted is False


@pytest.mark.asyncio
async def test_import_endpoint_preserves_invalid_mode_error_code(monkeypatch) -> None:
    async def allow_import(*args, **kwargs):
        return None

    async def import_chats_with_bindings(*args, **kwargs):
        raise InvalidConversationModeError('work')

    monkeypatch.setattr(chats_router, 'require_chat_import_permission', allow_import)
    monkeypatch.setattr(chats_router, 'import_chats_with_mode_profile_bindings', import_chats_with_bindings)

    with pytest.raises(chats_router.HTTPException) as exc_info:
        await chats_router.import_chats(
            SimpleNamespace(app=SimpleNamespace()),
            ChatsImportForm(
                chats=[ChatImportForm(chat=_chat_model(mode='work').chat)]
            ),
            SimpleNamespace(id='user-1'),
            None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail['code'] == 'invalid_conversation_mode'


@pytest.mark.asyncio
async def test_local_clone_preserves_only_server_resolved_source_binding(monkeypatch) -> None:
    source = _chat_model(mode='chat').model_copy(update={'mode_profile_revision_id': 'chat-source-revision'})
    captured = {}

    async def allow_import(*args, **kwargs):
        return None

    async def get_owned_chat(chat_id, user_id, db=None):
        return source

    async def resolve_source_binding(**kwargs):
        captured['resolved_source'] = kwargs
        return SimpleNamespace(mode_profile_revision_id='chat-source-revision')

    async def import_with_bindings(
        app,
        *,
        user_id,
        chat_import_forms,
        source_mode_profile_revision_ids=None,
    ):
        captured['source_mode_profile_revision_ids'] = source_mode_profile_revision_ids
        return [
            source.model_copy(
                update={
                    'id': 'cloned-chat',
                    'user_id': user_id,
                    'chat': chat_import_forms[0].chat,
                    'mode_profile_revision_id': source_mode_profile_revision_ids[0],
                }
            )
        ]

    async def publish(*args, **kwargs):
        return None

    monkeypatch.setattr(chats_router, 'require_chat_import_permission', allow_import)
    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id_and_user_id', get_owned_chat)
    monkeypatch.setattr(
        chats_router.ConversationModeProfiles,
        'resolve_persisted_chat_binding',
        resolve_source_binding,
    )
    monkeypatch.setattr(chats_router, 'import_chats_with_mode_profile_bindings', import_with_bindings)
    monkeypatch.setattr(chats_router, 'publish_event', publish)

    response = await chats_router.clone_chat_by_id(
        SimpleNamespace(app=SimpleNamespace()),
        chats_router.CloneForm(),
        'chat-1',
        SimpleNamespace(id='user-1'),
        None,
    )

    assert response.mode_profile_revision_id == 'chat-source-revision'
    assert captured['resolved_source']['chat_id'] == 'chat-1'
    assert captured['source_mode_profile_revision_ids'] == ['chat-source-revision']


@pytest.mark.asyncio
async def test_shared_clone_resolves_binding_from_shared_original_not_snapshot(monkeypatch) -> None:
    original = _chat_model(mode='chat').model_copy(
        update={'id': 'original-chat', 'user_id': 'owner-1', 'mode_profile_revision_id': 'chat-source-revision'}
    )
    captured = {}

    async def allow_import(*args, **kwargs):
        return None

    async def get_shared(share_id, db=None):
        return SimpleNamespace(id=share_id, chat_id='original-chat', user_id='owner-1', chat={'mode': 'agent'})

    async def get_original(chat_id, db=None):
        assert chat_id == 'original-chat'
        return original

    async def unexpected_snapshot_lookup(*args, **kwargs):
        raise AssertionError('shared clone must resolve the original chat through SharedChat.chat_id')

    async def resolve_source_binding(**kwargs):
        captured['resolved_source'] = kwargs
        return SimpleNamespace(mode_profile_revision_id='chat-source-revision')

    async def import_with_bindings(
        app,
        *,
        user_id,
        chat_import_forms,
        source_mode_profile_revision_ids=None,
    ):
        captured['source_mode_profile_revision_ids'] = source_mode_profile_revision_ids
        return [
            original.model_copy(
                update={
                    'id': 'shared-clone',
                    'user_id': user_id,
                    'chat': chat_import_forms[0].chat,
                    'mode_profile_revision_id': source_mode_profile_revision_ids[0],
                }
            )
        ]

    monkeypatch.setattr(chats_router, 'require_chat_import_permission', allow_import)
    monkeypatch.setattr(chats_router.SharedChats, 'get_by_id', get_shared)
    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id', get_original)
    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_share_id', unexpected_snapshot_lookup)
    monkeypatch.setattr(
        chats_router.ConversationModeProfiles,
        'resolve_persisted_chat_binding',
        resolve_source_binding,
    )
    monkeypatch.setattr(chats_router, 'import_chats_with_mode_profile_bindings', import_with_bindings)

    response = await chats_router.clone_shared_chat_by_id(
        SimpleNamespace(app=SimpleNamespace()),
        'share-token',
        SimpleNamespace(id='owner-1', role='user'),
        None,
    )

    assert response.mode_profile_revision_id == 'chat-source-revision'
    assert captured['resolved_source']['chat_id'] == 'original-chat'
    assert captured['source_mode_profile_revision_ids'] == ['chat-source-revision']


@pytest.mark.asyncio
async def test_share_read_and_export_expose_only_revision_audit_metadata(monkeypatch) -> None:
    administrator_prompt = 'administrator profile prompt must never be exported'
    chat = _chat_model(mode='chat').model_copy(
        update={'mode_profile_revision_id': 'chat-audit-revision'}
    )

    async def get_owned(chat_id, user_id, db=None):
        return chat

    async def create_share(chat_id, user_id, db=None):
        return SimpleNamespace(id='share-audit')

    async def update_share_id(chat_id, share_id, db=None):
        return chat.model_copy(update={'share_id': share_id})

    async def publish(*args, **kwargs):
        return None

    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id_and_user_id', get_owned)
    monkeypatch.setattr(chats_router.SharedChats, 'create', create_share)
    monkeypatch.setattr(chats_router.Chats, 'update_chat_share_id_by_id', update_share_id)
    monkeypatch.setattr(chats_router, 'publish_event', publish)

    shared = await chats_router.share_chat_by_id(
        SimpleNamespace(),
        'chat-1',
        SimpleNamespace(id='user-1', role='admin'),
        None,
    )

    async def get_shared(share_id, db=None):
        return SimpleNamespace(id=share_id, chat_id='chat-1', user_id='user-1')

    async def get_by_share_id(share_id, db=None):
        return chat.model_copy(update={'share_id': share_id})

    async def get_source(chat_id, *, db=None, repair=True, strict=False):
        return chat

    async def resolve_source(**kwargs):
        return SimpleNamespace(mode_profile_revision_id='chat-audit-revision')

    monkeypatch.setattr(chats_router.SharedChats, 'get_by_id', get_shared)
    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_share_id', get_by_share_id)
    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id', get_source)
    monkeypatch.setattr(chats_router.ConversationModeProfiles, 'resolve_persisted_chat_binding', resolve_source)

    read = await chats_router.get_shared_chat_by_id(
        'share-audit',
        SimpleNamespace(id='user-1', role='user'),
        None,
    )

    calls = 0

    async def get_export_batch(user_id, skip, limit, db=None):
        nonlocal calls
        calls += 1
        return SimpleNamespace(items=[chat] if calls == 1 else [])

    monkeypatch.setattr(chats_router.Chats, 'get_chats_by_user_id', get_export_batch)
    exported = ''.join([line async for line in chats_router.generate_chat_export_ndjson('user-1')])

    for payload in (shared.model_dump_json(), read.model_dump_json(), exported):
        assert 'chat-audit-revision' in payload
        assert administrator_prompt not in payload
        assert 'system_prompt' not in payload
