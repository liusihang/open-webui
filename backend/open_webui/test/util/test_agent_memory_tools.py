import importlib
import json
import os
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.agent_memories import AgentMemoryArtifact, AgentMemoryArtifacts
from open_webui.models.chats import Chat
from open_webui.models.folders import Folder
from open_webui.retrieval.vector.main import SearchResult


async def _session_factory(tmp_path):
    db_path = tmp_path / "agent-memory-tools.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    for table in [Chat.__table__, Folder.__table__, AgentMemoryArtifact.__table__]:
        table.create(sync_engine, checkfirst=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _request(
    *,
    enabled=True,
    use_enabled=None,
    dedicated_tools_enabled=None,
    embedding=None,
    relevance_threshold=0.9,
):
    if use_enabled is None:
        use_enabled = enabled
    if dedicated_tools_enabled is None:
        dedicated_tools_enabled = enabled

    async def default_embedding(text, prefix=None):
        if isinstance(text, list):
            return [[1.0] for _ in text]
        return [1.0]

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                EMBEDDING_FUNCTION=embedding or default_embedding,
                config=SimpleNamespace(
                    ENABLE_AGENT_MEMORY=enabled,
                    ENABLE_AGENT_MEMORY_USE=use_enabled,
                    ENABLE_AGENT_MEMORY_DEDICATED_TOOLS=dedicated_tools_enabled,
                    USER_PERMISSIONS={"features": {"agent_memory": True}},
                    RELEVANCE_THRESHOLD=relevance_threshold,
                ),
            )
        )
    )


def _user_dict(user_id="user-1", role="user"):
    return {"id": user_id, "role": role}


@pytest.fixture(autouse=True)
def _allow_agent_memory_tool_permission(monkeypatch):
    agent_tools = importlib.import_module("open_webui.tools.agent_memory")

    async def allow_permission(user_id, permission, user_permissions, db=None):
        return permission == "features.agent_memory"

    monkeypatch.setattr(agent_tools, "has_permission", allow_permission, raising=False)


async def _seed_scope(session):
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
    await AgentMemoryArtifacts.upsert_artifact(
        "user-1",
        "folder",
        "folder-1",
        "memory_summary.md",
        "folder summary",
        "hash",
        1,
        None,
        None,
        1000,
        db=session,
    )
    await AgentMemoryArtifacts.upsert_artifact(
        "user-1",
        "global",
        "",
        "MEMORY.md",
        "global details",
        "hash",
        1,
        None,
        None,
        1000,
        db=session,
    )


@pytest.mark.asyncio
async def test_search_returns_empty_without_vector_call_when_scope_has_no_artifacts(tmp_path, monkeypatch):
    agent_tools = importlib.import_module("open_webui.tools.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)

    class FailingVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            raise AssertionError("vector search must not run without Memory Artifact rows")

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FailingVectorClient())

    async with session_factory() as session:
        result = json.loads(
            await agent_tools.agent_memory_search(
                "runtime",
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": ""},
                __db__=session,
            )
        )

    assert result == {"results": []}
    await engine.dispose()


@pytest.mark.asyncio
async def test_direct_agent_memory_tools_deny_when_server_permission_denies(tmp_path, monkeypatch):
    agent_tools = importlib.import_module("open_webui.tools.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)

    async def deny_permission(user_id, permission, user_permissions, db=None):
        return False

    class FailingVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            raise AssertionError("permission-denied search must not reach vector search")

    monkeypatch.setattr(agent_tools, "has_permission", deny_permission, raising=False)
    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FailingVectorClient())

    async with session_factory() as session:
        await _seed_scope(session)
        search_result = json.loads(
            await agent_tools.agent_memory_search(
                "runtime",
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1", "features": {"agent_memory": True}},
                __db__=session,
            )
        )
        read_result = json.loads(
            await agent_tools.agent_memory_read(
                "MEMORY.md",
                scope="global",
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1", "features": {"agent_memory": True}},
                __db__=session,
            )
        )
        list_result = json.loads(
            await agent_tools.agent_memory_list(
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1", "features": {"agent_memory": True}},
                __db__=session,
            )
        )

    assert search_result == {"results": []}
    assert "error" in read_result
    assert list_result == {"artifacts": []}
    await engine.dispose()


@pytest.mark.asyncio
async def test_direct_agent_memory_tools_deny_when_dedicated_tools_disabled(tmp_path, monkeypatch):
    agent_tools = importlib.import_module("open_webui.tools.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)

    class FailingVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            raise AssertionError("dedicated-tools-disabled search must not reach vector search")

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FailingVectorClient())

    async with session_factory() as session:
        await _seed_scope(session)
        request = _request(enabled=True, use_enabled=True, dedicated_tools_enabled=False)
        search_result = json.loads(
            await agent_tools.agent_memory_search(
                "runtime",
                __request__=request,
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1", "features": {"agent_memory": True}},
                __db__=session,
            )
        )
        read_result = json.loads(
            await agent_tools.agent_memory_read(
                "MEMORY.md",
                scope="global",
                __request__=request,
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1", "features": {"agent_memory": True}},
                __db__=session,
            )
        )
        list_result = json.loads(
            await agent_tools.agent_memory_list(
                __request__=request,
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1", "features": {"agent_memory": True}},
                __db__=session,
            )
        )

    assert search_result == {"results": []}
    assert "error" in read_result
    assert list_result == {"artifacts": []}
    await engine.dispose()


@pytest.mark.asyncio
async def test_search_filters_vector_rows_to_current_artifact_path_and_revision(tmp_path, monkeypatch):
    agent_tools = importlib.import_module("open_webui.tools.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)
    searched_collections = []

    class FakeVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            searched_collections.append(collection_name)
            return SearchResult(
                ids=[["stale-revision", "current-revision", "deleted-path"]],
                documents=[["old MEMORY revision", "current MEMORY revision", "deleted artifact content"]],
                metadatas=[
                    [
                        {
                            "scope_type": "global",
                            "scope_id": "",
                            "path": "MEMORY.md",
                            "revision": 1,
                            "heading": "Old revision",
                        },
                        {
                            "scope_type": "global",
                            "scope_id": "",
                            "path": "MEMORY.md",
                            "revision": 2,
                            "heading": "Current revision",
                        },
                        {
                            "scope_type": "global",
                            "scope_id": "",
                            "path": "old.md",
                            "revision": 1,
                            "heading": "Deleted artifact",
                        },
                    ]
                ],
                distances=[[0.99, 0.98, 0.97]],
            )

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "MEMORY.md",
            "global details",
            "hash",
            2,
            None,
            None,
            1000,
            db=session,
        )
        result = json.loads(
            await agent_tools.agent_memory_search(
                "runtime",
                limit=5,
                __request__=_request(relevance_threshold=0.9),
                __user__=_user_dict(),
                __metadata__={"chat_id": ""},
                __db__=session,
            )
        )

    assert searched_collections == ["agent-memory-user-1-global"]
    assert result == {
        "results": [
            {
                "scope": "global",
                "path": "MEMORY.md",
                "heading": "Current revision",
                "content": "current MEMORY revision",
                "score": 0.98,
            }
        ]
    }
    assert all("collection" not in item for item in result["results"])
    await engine.dispose()


@pytest.mark.asyncio
async def test_exact_three_read_only_tools_registered_when_enabled(monkeypatch):
    tools = importlib.import_module("open_webui.utils.tools")

    async def allow_permission(user_id, permission, user_permissions):
        return permission == "features.agent_memory"

    monkeypatch.setattr(tools, "has_permission", allow_permission)

    registered = await tools.get_builtin_tools(
        _request(),
        {
            "__user__": _user_dict(),
            "__metadata__": {"chat_id": "chat-1"},
        },
        features={"agent_memory": True},
        model={"info": {"meta": {"builtinTools": {"agent_memory": True}}}},
    )

    agent_tool_names = sorted(name for name in registered if name.startswith("agent_memory_"))
    assert agent_tool_names == ["agent_memory_list", "agent_memory_read", "agent_memory_search"]
    assert not any(name in registered for name in ["agent_memory_add", "agent_memory_delete", "agent_memory_replace"])


@pytest.mark.asyncio
async def test_agent_memory_tools_do_not_register_when_use_enabled_but_dedicated_tools_disabled(monkeypatch):
    tools = importlib.import_module("open_webui.utils.tools")

    async def allow_permission(user_id, permission, user_permissions):
        return permission == "features.agent_memory"

    monkeypatch.setattr(tools, "has_permission", allow_permission)

    registered = await tools.get_builtin_tools(
        _request(enabled=True, use_enabled=True, dedicated_tools_enabled=False),
        {"__user__": _user_dict(), "__metadata__": {"chat_id": "chat-1"}},
        features={"agent_memory": True},
        model={"info": {"meta": {"builtinTools": {"agent_memory": True}}}},
    )

    assert not [name for name in registered if name.startswith("agent_memory_")]


@pytest.mark.asyncio
async def test_agent_memory_tools_do_not_register_when_use_disabled_even_if_dedicated_tools_enabled(monkeypatch):
    tools = importlib.import_module("open_webui.utils.tools")

    async def allow_permission(user_id, permission, user_permissions):
        return permission == "features.agent_memory"

    monkeypatch.setattr(tools, "has_permission", allow_permission)

    registered = await tools.get_builtin_tools(
        _request(enabled=True, use_enabled=False, dedicated_tools_enabled=True),
        {"__user__": _user_dict(), "__metadata__": {"chat_id": "chat-1"}},
        features={"agent_memory": True},
        model={"info": {"meta": {"builtinTools": {"agent_memory": True}}}},
    )

    assert not [name for name in registered if name.startswith("agent_memory_")]


@pytest.mark.asyncio
async def test_agent_memory_tools_do_not_register_when_builtin_category_disabled(monkeypatch):
    tools = importlib.import_module("open_webui.utils.tools")

    async def allow_permission(user_id, permission, user_permissions):
        return permission == "features.agent_memory"

    monkeypatch.setattr(tools, "has_permission", allow_permission)

    registered = await tools.get_builtin_tools(
        _request(),
        {"__user__": _user_dict(), "__metadata__": {"chat_id": "chat-1"}},
        features={"agent_memory": True},
        model={"info": {"meta": {"builtinTools": {"agent_memory": False}}}},
    )

    assert not [name for name in registered if name.startswith("agent_memory_")]


@pytest.mark.asyncio
async def test_agent_memory_tools_register_without_client_feature_toggle(monkeypatch):
    tools = importlib.import_module("open_webui.utils.tools")

    async def allow_permission(user_id, permission, user_permissions):
        return permission == "features.agent_memory"

    monkeypatch.setattr(tools, "has_permission", allow_permission)

    registered = await tools.get_builtin_tools(
        _request(),
        {
            "__user__": _user_dict(),
            "__metadata__": {"chat_id": "chat-1"},
        },
        features={},
        model={"info": {"meta": {"builtinTools": {"agent_memory": True}}}},
    )

    assert sorted(name for name in registered if name.startswith("agent_memory_")) == [
        "agent_memory_list",
        "agent_memory_read",
        "agent_memory_search",
    ]


@pytest.mark.asyncio
async def test_agent_memory_tools_register_none_when_disabled_or_not_permitted(monkeypatch):
    tools = importlib.import_module("open_webui.utils.tools")

    async def deny_permission(user_id, permission, user_permissions):
        return False

    monkeypatch.setattr(tools, "has_permission", deny_permission)

    disabled = await tools.get_builtin_tools(
        _request(enabled=False),
        {"__user__": _user_dict(), "__metadata__": {"chat_id": "chat-1"}},
        features={"agent_memory": True},
        model={"info": {"meta": {"builtinTools": {"agent_memory": True}}}},
    )
    denied = await tools.get_builtin_tools(
        _request(),
        {"__user__": _user_dict(), "__metadata__": {"chat_id": "chat-1"}},
        features={"agent_memory": True},
        model={"info": {"meta": {"builtinTools": {"agent_memory": True}}}},
    )

    assert not [name for name in disabled if name.startswith("agent_memory_")]
    assert not [name for name in denied if name.startswith("agent_memory_")]


@pytest.mark.asyncio
async def test_agent_memory_tools_register_none_when_current_scope_is_opted_out(monkeypatch):
    tools = importlib.import_module("open_webui.utils.tools")

    async def allow_permission(user_id, permission, user_permissions):
        return permission == "features.agent_memory"

    async def no_accessible_scopes(user_id, chat_id):
        return []

    monkeypatch.setattr(tools, "has_permission", allow_permission)
    monkeypatch.setattr(tools, "resolve_agent_memory_scopes", no_accessible_scopes)

    registered = await tools.get_builtin_tools(
        _request(),
        {"__user__": _user_dict(), "__metadata__": {"chat_id": "chat-1"}},
        features={"agent_memory": True},
        model={"info": {"meta": {"builtinTools": {"agent_memory": True}}}},
    )

    assert not [name for name in registered if name.startswith("agent_memory_")]


@pytest.mark.asyncio
async def test_search_uses_folder_before_global_and_non_folder_global_only(tmp_path, monkeypatch):
    agent_tools = importlib.import_module("open_webui.tools.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)
    searched_collections = []

    class FakeVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            searched_collections.append(collection_name)
            is_folder = "folder-" in collection_name
            current_path = "memory_summary.md" if is_folder else "MEMORY.md"
            return SearchResult(
                ids=[[f"{collection_name}-stale", collection_name]],
                documents=[[f"{collection_name} stale content", f"{collection_name} content"]],
                metadatas=[
                    [
                        {
                            "scope_type": "folder" if is_folder else "global",
                            "scope_id": "folder-1" if is_folder else "",
                            "path": current_path,
                            "revision": 0,
                            "heading": f"{collection_name} stale",
                        },
                        {
                            "scope_type": "folder" if is_folder else "global",
                            "scope_id": "folder-1" if is_folder else "",
                            "path": current_path,
                            "revision": 1,
                            "heading": collection_name,
                        },
                    ]
                ],
                distances=[[0.96, 0.95]],
            )

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        await _seed_scope(session)
        folder_result = json.loads(
            await agent_tools.agent_memory_search(
                "runtime",
                limit=2,
                __request__=_request(relevance_threshold=0.9),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1"},
                __db__=session,
            )
        )
        folder_searched_collections = list(searched_collections)
        searched_collections.clear()
        global_result = json.loads(
            await agent_tools.agent_memory_search(
                "runtime",
                limit=2,
                __request__=_request(relevance_threshold=0.9),
                __user__=_user_dict(),
                __metadata__={"chat_id": ""},
                __db__=session,
            )
        )

    assert folder_searched_collections == [
        "agent-memory-user-1-folder-folder-1",
        "agent-memory-user-1-global",
    ]
    assert searched_collections == ["agent-memory-user-1-global"]
    assert [item["scope"] for item in folder_result["results"]] == ["current_folder", "global"]
    assert [item["scope"] for item in global_result["results"]] == ["global"]
    assert [item["heading"] for item in folder_result["results"]] == [
        "agent-memory-user-1-folder-folder-1",
        "agent-memory-user-1-global",
    ]
    assert all("collection" not in item for item in folder_result["results"])
    assert all("collection" not in item for item in global_result["results"])
    await engine.dispose()


@pytest.mark.asyncio
async def test_read_and_list_reject_arbitrary_paths_and_scopes(tmp_path):
    agent_tools = importlib.import_module("open_webui.tools.agent_memory")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _seed_scope(session)
        bad_path = json.loads(
            await agent_tools.agent_memory_read(
                path="../secret",
                scope="global",
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1"},
                __db__=session,
            )
        )
        bad_scope = json.loads(
            await agent_tools.agent_memory_read(
                path="MEMORY.md",
                scope="other-user-global",
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1"},
                __db__=session,
            )
        )
        listed = json.loads(
            await agent_tools.agent_memory_list(
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1"},
                __db__=session,
            )
        )

    assert "error" in bad_path
    assert "error" in bad_scope
    assert listed == {
        "artifacts": [
            {"scope": "current_folder", "path": "memory_summary.md", "revision": 1},
            {"scope": "global", "path": "MEMORY.md", "revision": 1},
        ]
    }
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_memory_list_filters_scope_and_rejects_invalid_scope(tmp_path):
    agent_tools = importlib.import_module("open_webui.tools.agent_memory")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _seed_scope(session)
        global_only = json.loads(
            await agent_tools.agent_memory_list(
                scope="global",
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1"},
                __db__=session,
            )
        )
        folder_only = json.loads(
            await agent_tools.agent_memory_list(
                scope="current_folder",
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1"},
                __db__=session,
            )
        )
        all_current = json.loads(
            await agent_tools.agent_memory_list(
                scope="all_current",
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1"},
                __db__=session,
            )
        )
        invalid = json.loads(
            await agent_tools.agent_memory_list(
                scope="other-user",
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1"},
                __db__=session,
            )
        )

    assert global_only == {"artifacts": [{"scope": "global", "path": "MEMORY.md", "revision": 1}]}
    assert folder_only == {"artifacts": [{"scope": "current_folder", "path": "memory_summary.md", "revision": 1}]}
    assert all_current == {
        "artifacts": [
            {"scope": "current_folder", "path": "memory_summary.md", "revision": 1},
            {"scope": "global", "path": "MEMORY.md", "revision": 1},
        ]
    }
    assert "error" in invalid
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_memory_read_offsets_and_truncates_long_content(tmp_path):
    agent_tools = importlib.import_module("open_webui.tools.agent_memory")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _seed_scope(session)
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "MEMORY.md",
            "0123456789" * 20,
            "hash",
            2,
            None,
            None,
            1001,
            db=session,
        )
        result = json.loads(
            await agent_tools.agent_memory_read(
                path="MEMORY.md",
                scope="global",
                offset=7,
                max_chars=12,
                __request__=_request(),
                __user__=_user_dict(),
                __metadata__={"chat_id": "chat-1"},
                __db__=session,
            )
        )

    assert result["content"] == "789012345678"
    assert result["offset"] == 7
    assert result["max_chars"] == 12
    assert result["content_length"] == 200
    assert result["truncated"] is True
    await engine.dispose()
