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
from open_webui.models import chats as chats_model_module
from open_webui.models.chats import (
    Chat,
    ChatForm,
    ChatImportForm,
    ChatModel,
    Chats,
    ChatsImportForm,
)
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

    async def claim_mode(
        chat_id,
        *,
        requested,
        user_id,
        has_agent_run,
        db=None,
    ):
        canonical = stored
        if claimed_mode is not None:
            canonical = stored.model_copy(
                update={'chat': {**stored.chat, 'mode': claimed_mode}}
            )
        resolution = resolve_conversation_mode(
            requested=requested,
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
        return canonical, resolution

    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_id_and_user_id', get_chat)
    monkeypatch.setattr(chats_router.Chats, 'update_chat_by_id', update_chat)
    monkeypatch.setattr(chats_router.Chats, 'claim_conversation_mode', claim_mode)
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

    monkeypatch.setattr(chats_router.Chats, 'get_chat_by_share_id', get_snapshot)
    monkeypatch.setattr(chats_router.SharedChats, 'get_by_id', get_shared)
    monkeypatch.setattr(chats_router.AgentRuns, 'has_runs_by_chat', has_runs)

    response = await chats_router.get_shared_chat_by_id(
        'share-1',
        SimpleNamespace(id='user-1', role='user'),
        None,
    )

    assert response.chat['mode'] == 'agent'
    assert run_queries == [('chat-1', 'user-1')]


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
            SimpleNamespace(),
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

    async def insert_chat(chat_id, user_id, form_data, db=None):
        captured.append(form_data.chat)
        return _chat_model(mode=form_data.chat.get('mode')).model_copy(
            update={'id': chat_id, 'chat': form_data.chat}
        )

    async def publish(*args, **kwargs):
        return None

    monkeypatch.setattr(chats_router.Chats, 'insert_new_chat', insert_chat)
    monkeypatch.setattr(chats_router, 'publish_event', publish)

    response = await chats_router.create_new_chat(
        SimpleNamespace(),
        ChatForm(chat=_chat_model(mode=None).chat),
        SimpleNamespace(id='user-1'),
        None,
    )

    assert response.chat['mode'] == 'chat'
    assert captured[0]['mode'] == 'chat'


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

    async def import_chats(*args, **kwargs):
        raise InvalidConversationModeError('work')

    monkeypatch.setattr(chats_router, 'require_chat_import_permission', allow_import)
    monkeypatch.setattr(chats_router.Chats, 'import_chats', import_chats)

    with pytest.raises(chats_router.HTTPException) as exc_info:
        await chats_router.import_chats(
            SimpleNamespace(),
            ChatsImportForm(
                chats=[ChatImportForm(chat=_chat_model(mode='work').chat)]
            ),
            SimpleNamespace(id='user-1'),
            None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail['code'] == 'invalid_conversation_mode'
