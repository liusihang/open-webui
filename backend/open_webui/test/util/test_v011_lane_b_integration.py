from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[4]

CONFLICT_MODULES = (
    "backend/open_webui/functions.py",
    "backend/open_webui/main.py",
    "backend/open_webui/models/chat_messages.py",
    "backend/open_webui/models/chats.py",
    "backend/open_webui/routers/chats.py",
    "backend/open_webui/routers/ollama.py",
    "backend/open_webui/socket/main.py",
    "backend/open_webui/utils/automations.py",
    "backend/open_webui/utils/filter.py",
    "backend/open_webui/utils/middleware.py",
    "backend/open_webui/utils/plugin.py",
)


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def _top_level_names(relative_path: str) -> set[str]:
    tree = ast.parse(_source(relative_path), filename=relative_path)
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


@pytest.mark.parametrize("relative_path", CONFLICT_MODULES)
def test_provisional_conflict_modules_are_syntax_valid(relative_path: str) -> None:
    source = _source(relative_path)
    compile(source, relative_path, "exec")


def test_chat_router_keeps_v011_runtime_helpers_and_fork_endpoint() -> None:
    names = _top_level_names("backend/open_webui/routers/chats.py")

    assert {
        "get_optional_verified_user",
        "is_open_shared_chat",
        "can_read_shared_chat",
        "add_active_state_to_chat_list",
        "get_folder_unread_counts",
        "ForkForm",
        "fork_chat_by_id",
    } <= names


def test_owned_runtime_has_no_excluded_official_subagent_or_chat_files_wiring() -> None:
    runtime_paths = (
        "backend/open_webui/main.py",
        "backend/open_webui/tasks.py",
        "backend/open_webui/utils/middleware.py",
        "backend/open_webui/utils/timers.py",
    )
    combined = "\n".join(_source(path) for path in runtime_paths)

    assert "open_webui.utils.subagents" not in combined
    assert "process_pending_internal_messages" not in combined
    assert "delegate_task" not in combined
    assert "query_chat_files" not in combined
    assert "list_chat_files" not in combined
    assert "grep_chat_files" not in combined


def test_chat_response_exposes_v011_cursor_and_context_fields() -> None:
    from open_webui.models.chats import ChatResponse

    response = ChatResponse(
        id="chat-1",
        user_id="user-1",
        title="Chat",
        chat={},
        updated_at=1,
        created_at=1,
        archived=False,
        variables=None,
        current_message_id="message-1",
        context_usage={"used": 12},
    )

    assert response.variables == {}
    assert response.current_message_id == "message-1"
    assert response.context_usage == {"used": 12}


@pytest.mark.asyncio
async def test_chat_insert_preserves_v011_row_fields_with_custom_mode_profile(monkeypatch) -> None:
    from open_webui.models.chats import ChatForm, ChatTable

    added = []

    class FakeSession:
        def add(self, item):
            added.append(item)

        async def commit(self):
            return None

        async def refresh(self, item):
            return None

    table = ChatTable()

    async def no_dual_write(*args, **kwargs):
        return None

    monkeypatch.setattr(table, "dual_write_initial_messages", no_dual_write)
    result = await table._insert_new_chat_in_session(
        FakeSession(),
        id="chat-1",
        user_id="user-1",
        form_data=ChatForm(
            chat={
                "title": "Chat",
                "history": {"currentId": "message-1", "messages": {}},
            },
            variables={"project": "v0.11"},
        ),
        mode_profile_revision_id="profile-revision-1",
        internal_meta={"forked_from": "chat-0"},
        commit=True,
    )

    assert len(added) == 1
    assert result.variables == {"project": "v0.11"}
    assert result.meta == {"forked_from": "chat-0"}
    assert result.current_message_id == "message-1"
    assert result.mode_profile_revision_id == "profile-revision-1"


@pytest.mark.asyncio
async def test_chat_message_upsert_persists_structured_meta_without_committing() -> None:
    from open_webui.models.chat_messages import ChatMessage, ChatMessageTable

    existing = ChatMessage(
        id="chat-1-message-1",
        chat_id="chat-1",
        user_id="user-1",
        role="assistant",
        meta={"old": True},
        done=True,
        created_at=1,
        updated_at=1,
    )

    class FakeSession:
        async def get(self, model, composite_id):
            assert composite_id == existing.id
            return existing

        async def flush(self):
            return None

    result = await ChatMessageTable()._upsert_message_in_session(
        FakeSession(),
        message_id="message-1",
        chat_id="chat-1",
        user_id="user-1",
        data={"meta": {"structured": {"answer": 42}}},
        commit=False,
    )

    assert result.meta == {"structured": {"answer": 42}}


def test_chat_config_includes_official_compaction_model_setting() -> None:
    source = _source("backend/open_webui/routers/chats.py")

    assert "'CONTEXT_COMPACTION_MODEL': 'chat.context_compaction.model'" in source


def test_middleware_preserves_system_prompt_for_native_tool_continuations() -> None:
    source = _source("backend/open_webui/utils/middleware.py")

    assert "resolved_model_system_prompt = await resolve_system_prompt(" in source
    assert "metadata['system_prompt'] = system_content or None" in source


@pytest.mark.asyncio
async def test_closed_shared_chat_with_no_authenticated_user_returns_http_error(monkeypatch) -> None:
    from fastapi import HTTPException
    from open_webui.routers import chats as chat_router

    shared = SimpleNamespace(chat_id="chat-1", user_id="owner-1")

    async def get_shared(*args, **kwargs):
        return shared

    async def closed_share(*args, **kwargs):
        return False

    monkeypatch.setattr(chat_router.SharedChats, "get_by_id", get_shared)
    monkeypatch.setattr(chat_router, "is_open_shared_chat", closed_share)

    with pytest.raises(HTTPException) as error:
        await chat_router.get_shared_chat_by_id("share-1", user=None, db=SimpleNamespace())

    assert error.value.status_code == 401
