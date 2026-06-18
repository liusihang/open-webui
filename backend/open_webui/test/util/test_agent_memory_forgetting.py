import importlib
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.access_grants import AccessGrant
from open_webui.models.agent_memories import (
    AgentMemoryArtifact,
    AgentMemoryArtifacts,
    AgentMemoryConsolidationJob,
    AgentMemoryConsolidationJobs,
    AgentMemoryExtractionCache,
    AgentMemoryExtractionCaches,
    AgentMemoryExtractionJob,
    AgentMemoryExtractionJobs,
)
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, ChatModel
from open_webui.models.folders import Folder
from open_webui.models.groups import Group, GroupMember
from open_webui.models.notes import Note, PinnedNote
from open_webui.utils.auth import get_admin_user


async def _session_factory(tmp_path):
    db_path = tmp_path / "agent-memory-forgetting.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    for table in [
        Chat.__table__,
        ChatMessage.__table__,
        Folder.__table__,
        Note.__table__,
        PinnedNote.__table__,
        AccessGrant.__table__,
        Group.__table__,
        GroupMember.__table__,
        AgentMemoryExtractionCache.__table__,
        AgentMemoryExtractionJob.__table__,
        AgentMemoryConsolidationJob.__table__,
        AgentMemoryArtifact.__table__,
    ]:
        table.create(sync_engine, checkfirst=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _chat(chat_id, user_id="user-1", *, folder_id=None, updated_at=1000, meta=None):
    return Chat(
        id=chat_id,
        user_id=user_id,
        title=chat_id,
        chat={"title": chat_id, "history": {"messages": {}}},
        created_at=updated_at - 100,
        updated_at=updated_at,
        share_id=None,
        archived=False,
        pinned=False,
        meta=meta or {},
        folder_id=folder_id,
    )


def _message(chat_id, message_id, role, content, *, user_id="user-1", created_at=1000, done=True):
    return ChatMessage(
        id=f"{chat_id}-{message_id}",
        chat_id=chat_id,
        user_id=user_id,
        role=role,
        content=content,
        output=None,
        model_id="gpt-test" if role == "assistant" else None,
        done=done,
        error=None,
        created_at=created_at,
        updated_at=created_at,
    )


def _note(note_id, user_id="user-1", *, meta=None, md="linked note"):
    return Note(
        id=note_id,
        user_id=user_id,
        title=note_id,
        data={"content": {"md": md}},
        meta=meta or {},
        created_at=1000,
        updated_at=1000,
    )


def _request(*, embedding=None):
    async def default_embedding(text, prefix=None):
        if isinstance(text, list):
            return [[1.0] for _ in text]
        return [1.0]

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                EMBEDDING_FUNCTION=embedding or default_embedding,
                config=SimpleNamespace(
                    ENABLE_AGENT_MEMORY=True,
                    AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT=5,
                    AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT=5,
                    AGENT_MEMORY_LEASE_SECONDS=30,
                    USER_PERMISSIONS={"features": {"agent_memory": True}},
                ),
            )
        )
    )


async def _seed_cache(session, chat_id, *, raw_memory="raw", summary="summary", user_id="user-1"):
    await AgentMemoryExtractionCaches.upsert_cache(
        user_id=user_id,
        chat_id=chat_id,
        source_updated_at=1001,
        raw_memory=raw_memory,
        rollout_summary=summary,
        rollout_slug=None,
        generated_at=1002,
        status="succeeded",
        db=session,
    )


async def _seed_extraction_job(session, chat_id, *, status="queued", user_id="user-1"):
    await AgentMemoryExtractionJobs.upsert_job(
        user_id=user_id,
        chat_id=chat_id,
        status=status,
        lease_until=2000 if status == "leased" else None,
        retry_at=None,
        retry_count=2 if status == "failed" else 0,
        last_error="boom" if status == "failed" else None,
        updated_at=1003,
        db=session,
    )


async def _seed_consolidation_job(session, scope_type, scope_id, *, status="queued", user_id="user-1"):
    await AgentMemoryConsolidationJobs.upsert_job(
        user_id=user_id,
        scope_type=scope_type,
        scope_id=scope_id,
        status=status,
        lease_until=2000 if status == "leased" else None,
        retry_at=None,
        retry_count=2 if status == "failed" else 0,
        last_error="boom" if status == "failed" else None,
        input_hash="old-input",
        updated_at=1004,
        db=session,
    )


async def _seed_artifact(session, scope_type, scope_id, path, *, note_id=None, user_id="user-1", content="memory"):
    await AgentMemoryArtifacts.upsert_artifact(
        user_id,
        scope_type,
        scope_id,
        path,
        content,
        "input-hash",
        1,
        note_id,
        "note-hash" if note_id else None,
        1005,
        db=session,
    )


@pytest.mark.asyncio
async def test_chat_delete_forgetting_removes_cache_job_and_enqueues_old_scope(tmp_path):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _seed_cache(session, "chat-1")
        await _seed_extraction_job(session, "chat-1", status="leased")

        result = await agent_memory.forget_chat_agent_memory(
            user_id="user-1",
            chat_id="chat-1",
            folder_id="folder-1",
            now=2000,
            db=session,
        )

        assert result["extraction_caches_deleted"] == 1
        assert result["extraction_jobs_deleted"] == 1
        assert await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session) is None
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session) is None
        consolidation_job = await AgentMemoryConsolidationJobs.get_job(
            "user-1", "folder", "folder-1", db=session
        )
        assert consolidation_job.status == "queued"
        assert consolidation_job.input_hash is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_chat_opt_out_marks_meta_preserves_keys_and_stops_future_extraction(tmp_path):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(_chat("chat-1", meta={"keep": "yes", "agent_memory": {"note": "keep"}}))
        session.add(_message("chat-1", "u1", "user", "remember no more"))
        session.add(_message("chat-1", "a1", "assistant", "ok", created_at=1001))
        await session.commit()
        await _seed_cache(session, "chat-1")
        await _seed_extraction_job(session, "chat-1")

        await agent_memory.set_chat_agent_memory_disabled(
            user_id="user-1",
            chat_id="chat-1",
            disabled=True,
            now=2000,
            db=session,
        )

        chat = await session.get(Chat, "chat-1")
        assert chat.meta == {"keep": "yes", "agent_memory": {"note": "keep", "disabled": True}}
        assert await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session) is None
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session) is None
        assert not await extraction.enqueue_chat_extraction_if_needed(
            "chat-1",
            config=SimpleNamespace(
                ENABLE_AGENT_MEMORY=True,
                AGENT_MEMORY_IDLE_THRESHOLD_SECONDS=60,
                USER_PERMISSIONS={"features": {"agent_memory": True}},
            ),
            now=2000,
            db=session,
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_folder_opt_out_removes_folder_artifacts_index_and_note_linkage(tmp_path, monkeypatch):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)
    deleted_collections = []

    class FakeVectorClient:
        async def delete_collection(self, collection_name):
            deleted_collections.append(collection_name)

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())
    monkeypatch.setattr(agent_memory, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        session.add(Folder(id="folder-1", user_id="user-1", name="Project", meta={"keep": "yes"}, created_at=1, updated_at=1))
        session.add(_chat("folder-chat", folder_id="folder-1"))
        session.add(
            _note(
                "note-folder-summary",
                meta={
                    "keep": "yes",
                    "agent_memory": {
                        "managed": True,
                        "scope_type": "folder",
                        "scope_id": "folder-1",
                        "path": "memory_summary.md",
                    },
                },
            )
        )
        await session.commit()
        await _seed_cache(session, "folder-chat")
        await _seed_extraction_job(session, "folder-chat")
        await _seed_artifact(
            session,
            "folder",
            "folder-1",
            "memory_summary.md",
            note_id="note-folder-summary",
            content="folder summary",
        )
        await _seed_artifact(session, "global", "", "memory_summary.md", content="global summary")

        result = await agent_memory.set_folder_agent_memory_disabled(
            user_id="user-1",
            folder_id="folder-1",
            disabled=True,
            now=2000,
            db=session,
        )

        folder = await session.get(Folder, "folder-1")
        note = await session.get(Note, "note-folder-summary")
        assert folder.meta == {"keep": "yes", "agent_memory": {"disabled": True}}
        assert result["extraction_caches_deleted"] == 1
        assert result["artifacts_deleted"] == 1
        assert await AgentMemoryExtractionCaches.get_cache("user-1", "folder-chat", db=session) is None
        assert await AgentMemoryExtractionJobs.get_job("user-1", "folder-chat", db=session) is None
        assert await AgentMemoryArtifacts.get_artifact("user-1", "folder", "folder-1", "memory_summary.md", db=session) is None
        assert await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "memory_summary.md", db=session)
        assert note.meta == {"keep": "yes"}
        assert deleted_collections == ["agent-memory-user-1-folder-folder-1"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_folder_move_enqueues_old_and_new_scopes_without_rewriting_cache(tmp_path):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(_chat("chat-1", folder_id="folder-new"))
        await session.commit()
        await _seed_cache(session, "chat-1", raw_memory="keep raw", summary="keep summary")

        await agent_memory.enqueue_consolidation_for_folder_move(
            user_id="user-1",
            chat_id="chat-1",
            old_folder_id="folder-old",
            new_folder_id="folder-new",
            now=2000,
            db=session,
        )

        assert await AgentMemoryConsolidationJobs.get_job("user-1", "folder", "folder-old", db=session)
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "folder", "folder-new", db=session)
        cache = await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session)
        assert cache.raw_memory == "keep raw"
        assert cache.source_updated_at == 1001

    await engine.dispose()


@pytest.mark.asyncio
async def test_clear_agent_memory_converts_notes_and_removes_rows_and_index(tmp_path, monkeypatch):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)
    deleted_collections = []

    class FakeVectorClient:
        async def delete_collection(self, collection_name):
            deleted_collections.append(collection_name)

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())
    monkeypatch.setattr(agent_memory, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        session.add(_note("note-global", meta={"agent_memory": {"managed": True}, "keep": "yes"}))
        await session.commit()
        await _seed_cache(session, "chat-1")
        await _seed_extraction_job(session, "chat-1", status="failed")
        await _seed_consolidation_job(session, "global", "", status="failed")
        await _seed_artifact(session, "global", "", "memory_summary.md", note_id="note-global")
        await _seed_artifact(session, "folder", "folder-1", "MEMORY.md")

        result = await agent_memory.clear_agent_memory(
            user_id="user-1",
            note_mode="convert",
            now=2000,
            db=session,
        )

        note = await session.get(Note, "note-global")
        assert result["extraction_caches_deleted"] == 1
        assert result["extraction_jobs_deleted"] == 1
        assert result["consolidation_jobs_deleted"] == 1
        assert result["artifacts_deleted"] == 2
        assert note.meta == {"keep": "yes"}
        assert await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session) is None
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session) is None
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None
        assert await AgentMemoryArtifacts.list_artifacts("user-1", "global", "", db=session) == []
        assert deleted_collections == ["agent-memory-user-1-global", "agent-memory-user-1-folder-folder-1"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_clear_agent_memory_can_delete_linked_notes(tmp_path, monkeypatch):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)

    class FakeVectorClient:
        async def delete_collection(self, collection_name):
            return None

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())
    monkeypatch.setattr(agent_memory, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        session.add(_note("note-global", meta={"agent_memory": {"managed": True}, "keep": "yes"}))
        await session.commit()
        await _seed_artifact(session, "global", "", "memory_summary.md", note_id="note-global")

        await agent_memory.clear_agent_memory(
            user_id="user-1",
            note_mode="delete",
            now=2000,
            db=session,
        )

        assert await session.get(Note, "note-global") is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_clear_agent_memory_delete_note_mode_removes_note_grants_and_pins(tmp_path, monkeypatch):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)

    class FakeVectorClient:
        async def delete_collection(self, collection_name):
            return None

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())
    monkeypatch.setattr(agent_memory, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        session.add(_note("note-global", meta={"agent_memory": {"managed": True}, "keep": "yes"}))
        session.add(PinnedNote(id="pin-1", user_id="user-1", note_id="note-global", created_at=1000))
        session.add(
            AccessGrant(
                id="grant-1",
                resource_type="note",
                resource_id="note-global",
                principal_type="user",
                principal_id="other-user",
                permission="read",
                created_at=1000,
            )
        )
        await session.commit()
        await _seed_artifact(session, "global", "", "memory_summary.md", note_id="note-global")

        await agent_memory.clear_agent_memory(
            user_id="user-1",
            note_mode="delete",
            now=2000,
            db=session,
        )

        pinned = await session.execute(select(PinnedNote).where(PinnedNote.note_id == "note-global"))
        grants = await session.execute(select(AccessGrant).where(AccessGrant.resource_id == "note-global"))
        assert await session.get(Note, "note-global") is None
        assert pinned.scalars().all() == []
        assert grants.scalars().all() == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_failed_jobs_requeues_failed_extraction_and_consolidation(tmp_path):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _seed_extraction_job(session, "chat-1", status="failed")
        await _seed_consolidation_job(session, "global", "", status="failed")

        result = await agent_memory.retry_failed_agent_memory_jobs(user_id="user-1", now=2000, db=session)

        extraction_job = await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        consolidation_job = await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)
        assert result == {"extraction_jobs_retried": 1, "consolidation_jobs_retried": 1}
        assert extraction_job.status == "queued"
        assert extraction_job.retry_count == 0
        assert extraction_job.last_error is None
        assert consolidation_job.status == "queued"
        assert consolidation_job.retry_count == 0
        assert consolidation_job.last_error is None
        assert consolidation_job.input_hash is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_rebuild_index_deletes_collection_before_reindexing_scope(tmp_path, monkeypatch):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)
    calls = []

    class FakeVectorClient:
        async def delete_collection(self, collection_name):
            calls.append(("delete_collection", collection_name))

        async def delete(self, collection_name, ids=None, filter=None):
            calls.append(("delete", collection_name, filter))

        async def upsert(self, collection_name, items):
            calls.append(("upsert", collection_name, [item.id for item in items]))

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())
    monkeypatch.setattr(agent_memory, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        await _seed_artifact(session, "global", "", "memory_summary.md", content="# Summary\nUse pytest.")

        await agent_memory.rebuild_agent_memory_index(
            _request(),
            user_id="user-1",
            db=session,
        )

        assert calls[0] == ("delete_collection", "agent-memory-user-1-global")
        assert calls[-1][0] == "upsert"

    await engine.dispose()


@pytest.mark.asyncio
async def test_forgetting_recomputation_prompt_uses_remaining_cache_not_stale_artifact_text(tmp_path):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(_chat("deleted-chat"))
        session.add(_chat("kept-chat"))
        await session.commit()
        await _seed_cache(session, "deleted-chat", raw_memory="deleted stale preference", summary="delete me")
        await _seed_cache(session, "kept-chat", raw_memory="kept preference", summary="keep me")
        await _seed_artifact(
            session,
            "global",
            "",
            "MEMORY.md",
            content="old artifact still mentions deleted stale preference",
        )

        await agent_memory.forget_chat_agent_memory(
            user_id="user-1",
            chat_id="deleted-chat",
            folder_id=None,
            now=2000,
            db=session,
        )
        built = await consolidation.build_consolidation_input("user-1", "global", "", db=session)
        prompt = consolidation._render_consolidation_prompt(built)

        assert [item["chat_id"] for item in built.cache_records] == ["kept-chat"]
        assert "kept preference" in prompt
        assert "deleted stale preference" not in prompt
        assert "old artifact still mentions" not in prompt

    await engine.dispose()


def _route(router, path, method):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"missing route {method} {path}")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/jobs/failed", "GET"),
        ("/jobs/failed/retry", "POST"),
        ("/extract/run", "POST"),
        ("/consolidate/run", "POST"),
        ("/index/rebuild", "POST"),
        ("/clear", "POST"),
    ],
)
def test_agent_memory_ops_routes_are_admin_only(path, method):
    router = importlib.import_module("open_webui.routers.agent_memory")

    route = _route(router.router, path, method)
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}

    assert get_admin_user in dependency_calls


def test_chat_agent_memory_opt_out_route_is_owner_gated_and_registered():
    chats_router = importlib.import_module("open_webui.routers.chats")

    route = _route(chats_router.router, "/{id}/agent-memory", "POST")
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}

    assert chats_router.get_verified_user in dependency_calls


@pytest.mark.asyncio
async def test_retry_failed_jobs_endpoint_returns_helper_result(monkeypatch):
    router = importlib.import_module("open_webui.routers.agent_memory")
    calls = []

    async def fake_retry_failed_agent_memory_jobs(user_id=None, now=None, db=None):
        calls.append({"user_id": user_id, "db": db})
        return {"extraction_jobs_retried": 2, "consolidation_jobs_retried": 1}

    monkeypatch.setattr(
        router.agent_memory,
        "retry_failed_agent_memory_jobs",
        fake_retry_failed_agent_memory_jobs,
    )

    result = await router.retry_failed_jobs(
        form_data=router.RetryFailedJobsForm(user_id="user-1"),
        user=SimpleNamespace(id="admin-1", role="admin"),
        db=None,
    )

    assert result == {"extraction_jobs_retried": 2, "consolidation_jobs_retried": 1}
    assert calls == [{"user_id": "user-1", "db": None}]


@pytest.mark.asyncio
async def test_chat_delete_route_forgets_using_captured_owner_and_folder(monkeypatch):
    chats_router = importlib.import_module("open_webui.routers.chats")
    calls = []
    source_chat = SimpleNamespace(
        id="chat-1",
        user_id="owner-1",
        folder_id="folder-1",
        meta={"tags": []},
    )

    async def fake_stop_item_tasks(redis, chat_id):
        calls.append(("stop", redis, chat_id))

    async def fake_get_chat_by_id(chat_id, db=None):
        return source_chat

    async def fake_delete_orphan_tags_for_user(tags, user_id, threshold=1, db=None):
        calls.append(("tags", tags, user_id))

    async def fake_forget_chat_agent_memory(user_id, chat_id, folder_id, now=None, db=None):
        calls.append(("forget", user_id, chat_id, folder_id, db))
        return {}

    async def fake_delete_chat_by_id(chat_id, db=None):
        calls.append(("delete", chat_id, db))
        return True

    monkeypatch.setattr(chats_router, "stop_item_tasks", fake_stop_item_tasks)
    monkeypatch.setattr(chats_router.Chats, "get_chat_by_id", fake_get_chat_by_id)
    monkeypatch.setattr(chats_router.Chats, "delete_orphan_tags_for_user", fake_delete_orphan_tags_for_user)
    monkeypatch.setattr(chats_router.agent_memory, "forget_chat_agent_memory", fake_forget_chat_agent_memory)
    monkeypatch.setattr(chats_router.Chats, "delete_chat_by_id", fake_delete_chat_by_id)

    result = await chats_router.delete_chat_by_id(
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis="redis"))),
        id="chat-1",
        user=SimpleNamespace(id="admin-1", role="admin"),
        db="db",
    )

    assert result is True
    assert ("forget", "owner-1", "chat-1", "folder-1", "db") in calls
    assert calls.index(("forget", "owner-1", "chat-1", "folder-1", "db")) < calls.index(("delete", "chat-1", "db"))


@pytest.mark.asyncio
async def test_chat_folder_move_route_queues_old_and_new_scopes(monkeypatch):
    chats_router = importlib.import_module("open_webui.routers.chats")
    calls = []
    original = ChatModel.model_validate(_chat("chat-1", folder_id="old-folder"))
    updated = ChatModel.model_validate(_chat("chat-1", folder_id="new-folder"))

    async def fake_get_chat_by_id_and_user_id(chat_id, user_id, db=None):
        calls.append(("get", chat_id, user_id, db))
        return original

    async def fake_get_folder_by_id_and_user_id(folder_id, user_id, db=None):
        calls.append(("folder", folder_id, user_id, db))
        return SimpleNamespace(id=folder_id)

    async def fake_update_chat_folder_id_by_id_and_user_id(chat_id, user_id, folder_id, db=None):
        calls.append(("update", chat_id, user_id, folder_id, db))
        return updated

    async def fake_enqueue_consolidation_for_folder_move(
        user_id,
        chat_id,
        old_folder_id,
        new_folder_id,
        now=None,
        db=None,
    ):
        calls.append(("move", user_id, chat_id, old_folder_id, new_folder_id, db))
        return {}

    monkeypatch.setattr(chats_router.Chats, "get_chat_by_id_and_user_id", fake_get_chat_by_id_and_user_id)
    monkeypatch.setattr(chats_router.Folders, "get_folder_by_id_and_user_id", fake_get_folder_by_id_and_user_id)
    monkeypatch.setattr(
        chats_router.Chats,
        "update_chat_folder_id_by_id_and_user_id",
        fake_update_chat_folder_id_by_id_and_user_id,
    )
    monkeypatch.setattr(
        chats_router.agent_memory,
        "enqueue_consolidation_for_folder_move",
        fake_enqueue_consolidation_for_folder_move,
    )

    result = await chats_router.update_chat_folder_id_by_id(
        id="chat-1",
        form_data=chats_router.ChatFolderIdForm(folder_id="new-folder"),
        user=SimpleNamespace(id="user-1", role="user"),
        db="db",
    )

    assert result.folder_id == "new-folder"
    assert ("move", "user-1", "chat-1", "old-folder", "new-folder", "db") in calls


@pytest.mark.asyncio
async def test_chat_agent_memory_opt_out_route_delegates_cleanup(monkeypatch):
    chats_router = importlib.import_module("open_webui.routers.chats")
    calls = []

    async def fake_get_chat_by_id_and_user_id(chat_id, user_id, db=None):
        calls.append(("get", chat_id, user_id, db))
        return ChatModel.model_validate(_chat(chat_id, user_id=user_id))

    async def fake_set_chat_agent_memory_disabled(user_id, chat_id, disabled, now=None, db=None):
        calls.append(("disable", user_id, chat_id, disabled, db))
        return {"updated": True}

    monkeypatch.setattr(chats_router.Chats, "get_chat_by_id_and_user_id", fake_get_chat_by_id_and_user_id)
    monkeypatch.setattr(
        chats_router.agent_memory,
        "set_chat_agent_memory_disabled",
        fake_set_chat_agent_memory_disabled,
    )

    result = await chats_router.update_chat_agent_memory_by_id(
        id="chat-1",
        form_data=chats_router.ChatAgentMemoryForm(disabled=True),
        user=SimpleNamespace(id="user-1", role="user"),
        db="db",
    )

    assert result == {"updated": True}
    assert calls == [
        ("get", "chat-1", "user-1", "db"),
        ("disable", "user-1", "chat-1", True, "db"),
    ]


@pytest.mark.asyncio
async def test_folder_delete_denies_non_admin_when_descendant_folder_has_chats(monkeypatch):
    folders_router = importlib.import_module("open_webui.routers.folders")
    calls = []
    root = SimpleNamespace(id="root-folder")
    child = SimpleNamespace(id="child-folder")

    async def fake_count_chats_by_folder_id_and_user_id(folder_id, user_id, db=None):
        calls.append(("count", folder_id, user_id))
        return 0

    async def fake_has_permission(user_id, permission, permissions, db=None):
        calls.append(("permission", user_id, permission))
        return False

    async def fake_get_folder_by_id_and_user_id(folder_id, user_id, db=None):
        return root if folder_id == "root-folder" else None

    async def fake_get_children_folders_by_id_and_user_id(folder_id, user_id, db=None):
        return [child]

    async def fake_list_chat_ids_in_folder(user_id, folder_id, db=None):
        return ["child-chat"] if folder_id == "child-folder" else []

    async def fake_delete_folder_by_id_and_user_id(folder_id, user_id, db=None):
        calls.append(("delete-folder", folder_id))
        return ["root-folder", "child-folder"]

    async def fake_get_folders_by_parent_id_and_user_id(parent_id, user_id, db=None):
        return []

    async def fake_remove_agent_memory_scope_outputs(user_id, scope_type, scope_id, note_mode="convert", db=None):
        calls.append(("agent-cleanup", scope_id))
        return {}

    async def fake_forget_chat_agent_memory(user_id, chat_id, folder_id, now=None, db=None):
        calls.append(("forget", chat_id, folder_id))
        return {}

    async def fake_delete_chats_by_user_id_and_folder_id(user_id, folder_id, db=None):
        calls.append(("delete-chats", folder_id))
        return True

    monkeypatch.setattr(
        folders_router.Chats,
        "count_chats_by_folder_id_and_user_id",
        fake_count_chats_by_folder_id_and_user_id,
    )
    monkeypatch.setattr(folders_router, "has_permission", fake_has_permission)
    monkeypatch.setattr(folders_router.Folders, "get_folder_by_id_and_user_id", fake_get_folder_by_id_and_user_id)
    monkeypatch.setattr(
        folders_router.Folders,
        "get_children_folders_by_id_and_user_id",
        fake_get_children_folders_by_id_and_user_id,
    )
    monkeypatch.setattr(folders_router.agent_memory, "list_chat_ids_in_folder", fake_list_chat_ids_in_folder)
    monkeypatch.setattr(
        folders_router.Folders,
        "delete_folder_by_id_and_user_id",
        fake_delete_folder_by_id_and_user_id,
    )
    monkeypatch.setattr(
        folders_router.Folders,
        "get_folders_by_parent_id_and_user_id",
        fake_get_folders_by_parent_id_and_user_id,
    )
    monkeypatch.setattr(
        folders_router.agent_memory,
        "remove_agent_memory_scope_outputs",
        fake_remove_agent_memory_scope_outputs,
    )
    monkeypatch.setattr(
        folders_router.agent_memory,
        "forget_chat_agent_memory",
        fake_forget_chat_agent_memory,
    )
    monkeypatch.setattr(
        folders_router.Chats,
        "delete_chats_by_user_id_and_folder_id",
        fake_delete_chats_by_user_id_and_folder_id,
    )

    with pytest.raises(HTTPException) as exc:
        await folders_router.delete_folder_by_id(
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(USER_PERMISSIONS={})))),
            id="root-folder",
            delete_contents=True,
            user=SimpleNamespace(id="user-1", role="user"),
            db="db",
        )

    assert exc.value.status_code == 403
    assert ("permission", "user-1", "chat.delete") in calls
    assert not any(call[0] == "delete-folder" for call in calls)


@pytest.mark.asyncio
async def test_clear_agent_memory_retry_reattempts_vector_collections_after_delete_failure(tmp_path, monkeypatch):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)
    deleted_collections = []

    class FlakyVectorClient:
        async def delete_collection(self, collection_name):
            deleted_collections.append(collection_name)
            if len(deleted_collections) == 1:
                raise RuntimeError("vector unavailable")

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FlakyVectorClient())
    monkeypatch.setattr(agent_memory, "ASYNC_VECTOR_DB_CLIENT", FlakyVectorClient())

    async with session_factory() as session:
        await _seed_artifact(session, "global", "", "memory_summary.md")
        await _seed_artifact(session, "folder", "folder-1", "MEMORY.md")

        with pytest.raises(RuntimeError):
            await agent_memory.clear_agent_memory(
                user_id="user-1",
                note_mode="convert",
                db=session,
            )

        await agent_memory.clear_agent_memory(
            user_id="user-1",
            note_mode="convert",
            db=session,
        )

        assert deleted_collections == [
            "agent-memory-user-1-global",
            "agent-memory-user-1-global",
            "agent-memory-user-1-folder-folder-1",
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_clear_agent_memory_retry_continues_past_missing_collection_after_partial_success(tmp_path, monkeypatch):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)
    deleted_collections = []
    existing_collections = {
        "agent-memory-user-1-global",
        "agent-memory-user-1-folder-folder-1",
    }
    fail_folder_once = True

    class PartiallyFlakyVectorClient:
        async def delete_collection(self, collection_name):
            nonlocal fail_folder_once
            deleted_collections.append(collection_name)
            if collection_name not in existing_collections:
                raise RuntimeError(f"Collection {collection_name} does not exist")
            if collection_name == "agent-memory-user-1-folder-folder-1" and fail_folder_once:
                fail_folder_once = False
                raise RuntimeError("vector unavailable")
            existing_collections.remove(collection_name)

    vector_client = PartiallyFlakyVectorClient()
    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", vector_client)
    monkeypatch.setattr(agent_memory, "ASYNC_VECTOR_DB_CLIENT", vector_client)

    async with session_factory() as session:
        await _seed_artifact(session, "global", "", "memory_summary.md")
        await _seed_artifact(session, "folder", "folder-1", "MEMORY.md")

        with pytest.raises(RuntimeError):
            await agent_memory.clear_agent_memory(
                user_id="user-1",
                note_mode="convert",
                db=session,
            )

        result = await agent_memory.clear_agent_memory(
            user_id="user-1",
            note_mode="convert",
            db=session,
        )

        assert deleted_collections == [
            "agent-memory-user-1-global",
            "agent-memory-user-1-folder-folder-1",
            "agent-memory-user-1-global",
            "agent-memory-user-1-folder-folder-1",
        ]
        assert result["artifacts_deleted"] == 2
        artifacts = await session.execute(select(AgentMemoryArtifact))
        assert artifacts.scalars().all() == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_folder_delete_runs_agent_memory_cleanup_before_folder_rows_are_deleted(monkeypatch):
    folders_router = importlib.import_module("open_webui.routers.folders")
    calls = []
    root = SimpleNamespace(id="folder-1")

    async def fake_count_chats_by_folder_id_and_user_id(folder_id, user_id, db=None):
        return 0

    async def fake_get_folder_by_id_and_user_id(folder_id, user_id, db=None):
        return root

    async def fake_get_children_folders_by_id_and_user_id(folder_id, user_id, db=None):
        return []

    async def fake_list_chat_ids_in_folder(user_id, folder_id, db=None):
        return []

    async def fake_remove_agent_memory_scope_outputs(user_id, scope_type, scope_id, note_mode="convert", db=None):
        calls.append(("agent-cleanup", scope_id))
        return {}

    async def fake_delete_chats_by_user_id_and_folder_id(user_id, folder_id, db=None):
        calls.append(("delete-chats", folder_id))
        return True

    async def fake_delete_folder_by_id_and_user_id(folder_id, user_id, db=None):
        calls.append(("delete-folder", folder_id))
        return [folder_id]

    async def fake_get_folders_by_parent_id_and_user_id(parent_id, user_id, db=None):
        return []

    monkeypatch.setattr(
        folders_router.Chats,
        "count_chats_by_folder_id_and_user_id",
        fake_count_chats_by_folder_id_and_user_id,
    )
    monkeypatch.setattr(folders_router.Folders, "get_folder_by_id_and_user_id", fake_get_folder_by_id_and_user_id)
    monkeypatch.setattr(
        folders_router.Folders,
        "get_children_folders_by_id_and_user_id",
        fake_get_children_folders_by_id_and_user_id,
    )
    monkeypatch.setattr(folders_router.agent_memory, "list_chat_ids_in_folder", fake_list_chat_ids_in_folder)
    monkeypatch.setattr(
        folders_router.agent_memory,
        "remove_agent_memory_scope_outputs",
        fake_remove_agent_memory_scope_outputs,
    )
    monkeypatch.setattr(
        folders_router.Chats,
        "delete_chats_by_user_id_and_folder_id",
        fake_delete_chats_by_user_id_and_folder_id,
    )
    monkeypatch.setattr(
        folders_router.Folders,
        "delete_folder_by_id_and_user_id",
        fake_delete_folder_by_id_and_user_id,
    )
    monkeypatch.setattr(
        folders_router.Folders,
        "get_folders_by_parent_id_and_user_id",
        fake_get_folders_by_parent_id_and_user_id,
    )

    result = await folders_router.delete_folder_by_id(
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(USER_PERMISSIONS={})))),
        id="folder-1",
        delete_contents=True,
        user=SimpleNamespace(id="user-1", role="admin"),
        db="db",
    )

    assert result is True
    assert calls.index(("agent-cleanup", "folder-1")) < calls.index(("delete-folder", "folder-1"))


@pytest.mark.asyncio
async def test_folder_delete_raises_when_chat_delete_fails(monkeypatch):
    folders_router = importlib.import_module("open_webui.routers.folders")
    calls = []
    root = SimpleNamespace(id="folder-1")

    async def fake_count_chats_by_folder_id_and_user_id(folder_id, user_id, db=None):
        return 1

    async def fake_get_folder_by_id_and_user_id(folder_id, user_id, db=None):
        return root

    async def fake_get_children_folders_by_id_and_user_id(folder_id, user_id, db=None):
        return []

    async def fake_list_chat_ids_in_folder(user_id, folder_id, db=None):
        return ["chat-1"]

    async def fake_forget_chat_agent_memory(user_id, chat_id, folder_id, now=None, db=None):
        calls.append(("forget", chat_id))
        return {}

    async def fake_remove_agent_memory_scope_outputs(user_id, scope_type, scope_id, note_mode="convert", db=None):
        calls.append(("agent-cleanup", scope_id))
        return {}

    async def fake_delete_chats_by_user_id_and_folder_id(user_id, folder_id, db=None):
        calls.append(("delete-chats", folder_id))
        return False

    async def fake_delete_folder_by_id_and_user_id(folder_id, user_id, db=None):
        calls.append(("delete-folder", folder_id))
        return [folder_id]

    async def fake_has_permission(user_id, permission, permissions, db=None):
        return True

    async def fake_get_folders_by_parent_id_and_user_id(parent_id, user_id, db=None):
        return []

    monkeypatch.setattr(
        folders_router.Chats,
        "count_chats_by_folder_id_and_user_id",
        fake_count_chats_by_folder_id_and_user_id,
    )
    monkeypatch.setattr(folders_router, "has_permission", fake_has_permission)
    monkeypatch.setattr(folders_router.Folders, "get_folder_by_id_and_user_id", fake_get_folder_by_id_and_user_id)
    monkeypatch.setattr(
        folders_router.Folders,
        "get_children_folders_by_id_and_user_id",
        fake_get_children_folders_by_id_and_user_id,
    )
    monkeypatch.setattr(folders_router.agent_memory, "list_chat_ids_in_folder", fake_list_chat_ids_in_folder)
    monkeypatch.setattr(
        folders_router.agent_memory,
        "forget_chat_agent_memory",
        fake_forget_chat_agent_memory,
    )
    monkeypatch.setattr(
        folders_router.agent_memory,
        "remove_agent_memory_scope_outputs",
        fake_remove_agent_memory_scope_outputs,
    )
    monkeypatch.setattr(
        folders_router.Chats,
        "delete_chats_by_user_id_and_folder_id",
        fake_delete_chats_by_user_id_and_folder_id,
    )
    monkeypatch.setattr(
        folders_router.Folders,
        "delete_folder_by_id_and_user_id",
        fake_delete_folder_by_id_and_user_id,
    )
    monkeypatch.setattr(
        folders_router.Folders,
        "get_folders_by_parent_id_and_user_id",
        fake_get_folders_by_parent_id_and_user_id,
    )

    with pytest.raises(HTTPException) as exc:
        await folders_router.delete_folder_by_id(
            request=SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(USER_PERMISSIONS={})))
            ),
            id="folder-1",
            delete_contents=True,
            user=SimpleNamespace(id="user-1", role="admin"),
            db="db",
        )

    assert exc.value.status_code == 400
    assert not any(call[0] == "forget" for call in calls)
    assert not any(call[0] == "agent-cleanup" for call in calls)
    assert not any(call[0] == "delete-folder" for call in calls)


@pytest.mark.asyncio
async def test_folder_delete_raises_when_chat_move_fails(monkeypatch):
    folders_router = importlib.import_module("open_webui.routers.folders")
    calls = []
    root = SimpleNamespace(id="folder-1")

    async def fake_count_chats_by_folder_id_and_user_id(folder_id, user_id, db=None):
        return 1

    async def fake_get_folder_by_id_and_user_id(folder_id, user_id, db=None):
        return root

    async def fake_get_children_folders_by_id_and_user_id(folder_id, user_id, db=None):
        return []

    async def fake_list_chat_ids_in_folder(user_id, folder_id, db=None):
        return ["chat-1"]

    async def fake_remove_agent_memory_scope_outputs(user_id, scope_type, scope_id, note_mode="convert", db=None):
        calls.append(("agent-cleanup", scope_id))
        return {}

    async def fake_move_chats_by_user_id_and_folder_id(user_id, folder_id, new_folder_id, db=None):
        calls.append(("move-chats", folder_id, new_folder_id))
        return False

    async def fake_enqueue_consolidation_for_scope(user_id, scope_type, scope_id="", now=None, db=None):
        calls.append(("enqueue", scope_type, scope_id))
        return {}

    async def fake_delete_folder_by_id_and_user_id(folder_id, user_id, db=None):
        calls.append(("delete-folder", folder_id))
        return [folder_id]

    async def fake_has_permission(user_id, permission, permissions, db=None):
        return True

    async def fake_get_folders_by_parent_id_and_user_id(parent_id, user_id, db=None):
        return []

    monkeypatch.setattr(
        folders_router.Chats,
        "count_chats_by_folder_id_and_user_id",
        fake_count_chats_by_folder_id_and_user_id,
    )
    monkeypatch.setattr(folders_router, "has_permission", fake_has_permission)
    monkeypatch.setattr(folders_router.Folders, "get_folder_by_id_and_user_id", fake_get_folder_by_id_and_user_id)
    monkeypatch.setattr(
        folders_router.Folders,
        "get_children_folders_by_id_and_user_id",
        fake_get_children_folders_by_id_and_user_id,
    )
    monkeypatch.setattr(folders_router.agent_memory, "list_chat_ids_in_folder", fake_list_chat_ids_in_folder)
    monkeypatch.setattr(
        folders_router.agent_memory,
        "remove_agent_memory_scope_outputs",
        fake_remove_agent_memory_scope_outputs,
    )
    monkeypatch.setattr(
        folders_router.Chats,
        "move_chats_by_user_id_and_folder_id",
        fake_move_chats_by_user_id_and_folder_id,
    )
    monkeypatch.setattr(
        folders_router.agent_memory,
        "enqueue_consolidation_for_scope",
        fake_enqueue_consolidation_for_scope,
    )
    monkeypatch.setattr(
        folders_router.Folders,
        "delete_folder_by_id_and_user_id",
        fake_delete_folder_by_id_and_user_id,
    )
    monkeypatch.setattr(
        folders_router.Folders,
        "get_folders_by_parent_id_and_user_id",
        fake_get_folders_by_parent_id_and_user_id,
    )

    with pytest.raises(HTTPException) as exc:
        await folders_router.delete_folder_by_id(
            request=SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(USER_PERMISSIONS={})))
            ),
            id="folder-1",
            delete_contents=False,
            user=SimpleNamespace(id="user-1", role="admin"),
            db="db",
        )

    assert exc.value.status_code == 400
    assert not any(call[0] == "agent-cleanup" for call in calls)
    assert not any(call[0] == "enqueue" for call in calls)
    assert not any(call[0] == "delete-folder" for call in calls)


@pytest.mark.parametrize("form_class_name", ["RebuildIndexForm", "ClearAgentMemoryForm"])
def test_agent_memory_folder_scope_operations_require_folder_id(form_class_name):
    router = importlib.import_module("open_webui.routers.agent_memory")
    form_class = getattr(router, form_class_name)

    with pytest.raises(ValidationError):
        form_class(user_id="user-1", scope_type="folder", scope_id="")

    with pytest.raises(ValidationError):
        form_class(user_id="user-1", scope_type="folder", scope_id=None)


@pytest.mark.asyncio
async def test_folder_delete_move_out_does_not_queue_deleted_folder_scope(tmp_path, monkeypatch):
    folders_router = importlib.import_module("open_webui.routers.folders")
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)

    class FakeVectorClient:
        async def delete_collection(self, collection_name):
            return None

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())
    monkeypatch.setattr(agent_memory, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        session.add(Folder(id="folder-1", user_id="user-1", name="Project", meta={}, created_at=1, updated_at=1))
        session.add(_chat("chat-1", folder_id="folder-1"))
        await session.commit()
        await _seed_cache(session, "chat-1")
        await _seed_artifact(session, "folder", "folder-1", "memory_summary.md")

        result = await folders_router.delete_folder_by_id(
            request=SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(config=SimpleNamespace(USER_PERMISSIONS={"chat": {"delete": True}}))
                )
            ),
            id="folder-1",
            delete_contents=False,
            user=SimpleNamespace(id="user-1", role="admin"),
            db=session,
        )

        assert result is True
        chat = await session.get(Chat, "chat-1")
        assert chat.folder_id is None
        assert await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session)
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "folder", "folder-1", db=session) is None
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)

    await engine.dispose()


def test_agent_memory_admin_settings_wire_operational_controls():
    repo_root = Path(__file__).resolve().parents[4]
    component = (
        repo_root / "src/lib/components/admin/Settings/AgentMemory.svelte"
    ).read_text()
    api = (repo_root / "src/lib/apis/agent-memory/index.ts").read_text()

    assert "getAgentMemoryFailedJobs" in component
    assert "runAgentMemoryExtraction" in component
    assert "runAgentMemoryConsolidation" in component
    assert "rebuildAgentMemoryIndex" in component
    assert "clearAgentMemory" in component
    assert "retryFailedAgentMemoryJobs" in component
    assert "failedExtractionJobs" in component
    assert "failedConsolidationJobs" in component
    assert "Inspect Failed Jobs" in component
    assert "No failed extraction jobs" in component
    assert "No failed consolidation jobs" in component
    for snippet in [
        "job.user_id",
        "job.chat_id",
        "job.status",
        "job.retry_count",
        "job.last_error",
        "job.updated_at",
        "job.scope_type",
        "job.scope_id",
        "job.input_hash",
    ]:
        assert snippet in component
    assert 'value="--"' not in component
    assert "cursor-not-allowed" not in component
    for endpoint in [
        "/agent-memory/jobs/failed",
        "/agent-memory/jobs/failed/retry",
        "/agent-memory/extract/run",
        "/agent-memory/consolidate/run",
        "/agent-memory/index/rebuild",
        "/agent-memory/clear",
    ]:
        assert endpoint in api
