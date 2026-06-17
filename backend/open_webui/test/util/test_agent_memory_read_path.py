import importlib
import os
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
import tiktoken
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.agent_memories import AgentMemoryArtifact, AgentMemoryArtifacts
from open_webui.models.chats import Chat
from open_webui.models.folders import Folder


async def _session_factory(tmp_path):
    db_path = tmp_path / "agent-memory-read-path.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    for table in [Chat.__table__, Folder.__table__, AgentMemoryArtifact.__table__]:
        table.create(sync_engine, checkfirst=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _request(*, enabled=True, budget=1200):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_AGENT_MEMORY=enabled,
                    AGENT_MEMORY_SUMMARY_TOKEN_BUDGET=budget,
                    USER_PERMISSIONS={"features": {"agent_memory": True}},
                )
            )
        )
    )


def _user(user_id="user-1", role="user"):
    return SimpleNamespace(id=user_id, role=role)


def _form_data(function_calling="native", *, features=None, chat_id="chat-1"):
    return {
        "messages": [{"role": "user", "content": "What should I remember?"}],
        "metadata": {
            "chat_id": chat_id,
            "params": {"function_calling": function_calling},
            "features": features if features is not None else {"agent_memory": True},
        },
    }


async def _artifact(session, scope_type, scope_id, content):
    await AgentMemoryArtifacts.upsert_artifact(
        "user-1",
        scope_type,
        scope_id,
        "memory_summary.md",
        content,
        "input-hash",
        1,
        None,
        None,
        1000,
        db=session,
    )


@pytest.fixture(autouse=True)
def _allow_agent_memory_permission(monkeypatch):
    middleware = importlib.import_module("open_webui.utils.middleware")

    async def allow_permission(user_id, permission, user_permissions, db=None):
        return permission == "features.agent_memory"

    monkeypatch.setattr(middleware, "has_permission", allow_permission)


@pytest.mark.asyncio
async def test_native_read_path_injects_policy_folder_summary_before_global(tmp_path):
    middleware = importlib.import_module("open_webui.utils.middleware")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(Folder(id="folder-1", user_id="user-1", name="Project", meta={}, created_at=1, updated_at=1))
        session.add(
            Chat(
                id="chat-1",
                user_id="user-1",
                title="Chat",
                chat={"history": {"messages": {}}},
                created_at=1,
                updated_at=1,
                share_id=None,
                archived=False,
                pinned=False,
                meta={},
                folder_id="folder-1",
            )
        )
        await _artifact(session, "folder", "folder-1", "folder summary")
        await _artifact(session, "global", "", "global summary")

        form_data = await middleware.apply_agent_memory_read_path(
            _request(),
            _form_data(),
            user=_user(),
            db=session,
        )

    system_content = form_data["messages"][0]["content"]
    assert "Agent Memory" in system_content
    assert system_content.index("folder summary") < system_content.index("global summary")
    await engine.dispose()


@pytest.mark.asyncio
async def test_native_read_path_uses_server_permission_without_client_feature_toggle(tmp_path, monkeypatch):
    middleware = importlib.import_module("open_webui.utils.middleware")
    engine, session_factory = await _session_factory(tmp_path)

    async def allow_permission(user_id, permission, user_permissions, db=None):
        return permission == "features.agent_memory"

    monkeypatch.setattr(middleware, "has_permission", allow_permission)

    async with session_factory() as session:
        await _artifact(session, "global", "", "server allowed summary")

        form_data = await middleware.apply_agent_memory_read_path(
            _request(),
            _form_data(features={}, chat_id=""),
            user=_user(),
            db=session,
        )

    assert "server allowed summary" in form_data["messages"][0]["content"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_native_read_path_denies_when_server_permission_denies_client_feature_true(tmp_path, monkeypatch):
    middleware = importlib.import_module("open_webui.utils.middleware")
    engine, session_factory = await _session_factory(tmp_path)

    async def deny_permission(user_id, permission, user_permissions, db=None):
        return False

    monkeypatch.setattr(middleware, "has_permission", deny_permission)

    async with session_factory() as session:
        await _artifact(session, "global", "", "denied summary")

        form_data = await middleware.apply_agent_memory_read_path(
            _request(),
            _form_data(features={"agent_memory": True}, chat_id=""),
            user=_user(),
            db=session,
        )

    assert form_data["messages"][0]["role"] == "user"
    await engine.dispose()


@pytest.mark.asyncio
async def test_read_path_does_not_inject_for_non_native_disabled_permission_or_opt_out(tmp_path):
    middleware = importlib.import_module("open_webui.utils.middleware")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(Folder(id="folder-off", user_id="user-1", name="Off", meta={"agent_memory": {"disabled": True}}, created_at=1, updated_at=1))
        session.add(
            Chat(
                id="chat-off",
                user_id="user-1",
                title="Chat",
                chat={"history": {"messages": {}}},
                created_at=1,
                updated_at=1,
                share_id=None,
                archived=False,
                pinned=False,
                meta={"agent_memory": {"disabled": True}},
                folder_id="folder-off",
            )
        )
        await _artifact(session, "global", "", "global summary")

        cases = [
            (_request(), _form_data(function_calling="default")),
            (_request(enabled=False), _form_data()),
            (_request(), _form_data(chat_id="chat-off")),
        ]
        for request, form_data in cases:
            result = await middleware.apply_agent_memory_read_path(request, form_data, user=_user(), db=session)
            assert result["messages"][0]["role"] == "user"

    await engine.dispose()


@pytest.mark.asyncio
async def test_read_path_truncates_to_summary_budget(tmp_path):
    middleware = importlib.import_module("open_webui.utils.middleware")
    engine, session_factory = await _session_factory(tmp_path)
    encoding = tiktoken.get_encoding("cl100k_base")

    async with session_factory() as session:
        await _artifact(session, "global", "", "one two three four five six seven eight")

        form_data = await middleware.apply_agent_memory_read_path(
            _request(budget=5),
            _form_data(chat_id=""),
            user=_user(),
            db=session,
        )

    system_content = form_data["messages"][0]["content"]
    injected_summary = system_content.split("Global Agent Memory:\n", 1)[1]
    assert len(encoding.encode(injected_summary)) <= 5
    assert "one two three" in injected_summary
    assert "six seven eight" not in system_content
    await engine.dispose()


@pytest.mark.asyncio
async def test_read_path_bounds_cjk_summary_by_configured_budget(tmp_path):
    middleware = importlib.import_module("open_webui.utils.middleware")
    engine, session_factory = await _session_factory(tmp_path)
    encoding = tiktoken.get_encoding("cl100k_base")
    long_cjk_summary = "龘" * 120

    async with session_factory() as session:
        await _artifact(session, "global", "", long_cjk_summary)

        form_data = await middleware.apply_agent_memory_read_path(
            _request(budget=5),
            _form_data(chat_id=""),
            user=_user(),
            db=session,
        )

    system_content = form_data["messages"][0]["content"]
    injected_summary = system_content.split("Global Agent Memory:\n", 1)[1]
    assert len(encoding.encode(injected_summary)) <= 5
    assert "龘" * 80 not in injected_summary
    await engine.dispose()


@pytest.mark.asyncio
async def test_read_path_omits_summary_when_tokenizer_fails(tmp_path, monkeypatch):
    middleware = importlib.import_module("open_webui.utils.middleware")
    engine, session_factory = await _session_factory(tmp_path)

    def fail_get_encoding(name):
        raise RuntimeError("tokenizer unavailable")

    monkeypatch.setattr(tiktoken, "get_encoding", fail_get_encoding)

    async with session_factory() as session:
        await _artifact(session, "global", "", "LEAKME-SUMMARY-CONTENT")

        form_data = await middleware.apply_agent_memory_read_path(
            _request(budget=5),
            _form_data(chat_id=""),
            user=_user(),
            db=session,
        )

    rendered_content = "\n".join(message.get("content", "") for message in form_data["messages"])
    assert "LEAK" not in rendered_content
    assert "SUMMARY-CONTENT" not in rendered_content
    await engine.dispose()
