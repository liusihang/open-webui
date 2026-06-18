import importlib
import json
import os
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
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
from open_webui.models.chats import Chat
from open_webui.models.folders import Folder
from open_webui.models.groups import Group, GroupMember
from open_webui.models.notes import Note, PinnedNote
from open_webui.retrieval.vector.main import SearchResult


async def _session_factory(tmp_path):
    db_path = tmp_path / "agent-memory-runtime-e2e.db"
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


def _config(**overrides):
    values = {
        "ENABLE_AGENT_MEMORY": True,
        "AGENT_MEMORY_EXTRACTION_MODEL": "",
        "AGENT_MEMORY_CONSOLIDATION_MODEL": "",
        "AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT": 5,
        "AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT": 5,
        "AGENT_MEMORY_IDLE_THRESHOLD_SECONDS": 60,
        "AGENT_MEMORY_LEASE_SECONDS": 30,
        "AGENT_MEMORY_RETRY_BACKOFF_SECONDS": 10,
        "AGENT_MEMORY_SUMMARY_TOKEN_BUDGET": 1200,
        "TASK_MODEL": "",
        "TASK_MODEL_EXTERNAL": "",
        "DEFAULT_MODELS": "gpt-test",
        "USER_PERMISSIONS": {"features": {"agent_memory": True}},
        "RELEVANCE_THRESHOLD": 0.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(config=None, *, vector_store=None):
    async def embedding_function(text, prefix=None):
        if isinstance(text, list):
            return [[float(len(item) % 7 + 1)] for item in text]
        return [float(len(str(text)) % 7 + 1)]

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={"gpt-test": {"id": "gpt-test", "owned_by": "openai", "info": {"params": {}}}},
                EMBEDDING_FUNCTION=embedding_function,
                config=config or _config(),
                vector_store=vector_store,
            )
        )
    )


def _chat(chat_id, *, user_id="user-1", folder_id=None, updated_at=1000, meta=None):
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


def _form_data(function_calling="native", *, chat_id="folder-chat"):
    return {
        "messages": [{"role": "user", "content": "Use my project memory."}],
        "metadata": {
            "chat_id": chat_id,
            "params": {"function_calling": function_calling},
            "features": {"agent_memory": True},
        },
    }


class FakeVectorClient:
    def __init__(self):
        self.collections = {}
        self.deleted_collections = []
        self.deleted_filters = []

    async def delete(self, collection_name, ids=None, filter=None):
        self.deleted_filters.append((collection_name, filter))
        if collection_name not in self.collections:
            return
        if filter and "path" in filter:
            self.collections[collection_name] = [
                item
                for item in self.collections[collection_name]
                if item.metadata.get("path") != filter["path"]
            ]
        elif ids:
            self.collections[collection_name] = [
                item for item in self.collections[collection_name] if item.id not in ids
            ]
        else:
            self.collections[collection_name] = []

    async def upsert(self, collection_name, items):
        self.collections.setdefault(collection_name, []).extend(items)

    async def search(self, collection_name, vectors, limit=10, filter=None):
        items = self.collections.get(collection_name, [])[:limit]
        return SearchResult(
            ids=[[item.id for item in items]],
            documents=[[item.text for item in items]],
            metadatas=[[item.metadata for item in items]],
            distances=[[1.0 for _ in items]],
        )

    async def delete_collection(self, collection_name):
        self.deleted_collections.append(collection_name)
        self.collections.pop(collection_name, None)


@pytest.fixture(autouse=True)
def _allow_agent_memory_permission(monkeypatch):
    async def allow_permission(user_id, permission, user_permissions, db=None):
        return permission == "features.agent_memory"

    for module_name in [
        "open_webui.utils.agent_memory_extraction",
        "open_webui.utils.agent_memory_consolidation",
        "open_webui.utils.middleware",
        "open_webui.tools.agent_memory",
        "open_webui.utils.tools",
    ]:
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, "has_permission", allow_permission, raising=False)


@pytest.mark.asyncio
async def test_agent_memory_runtime_e2e_chain_with_native_tools_forgetting_and_disable(tmp_path, monkeypatch):
    agent_memory = importlib.import_module("open_webui.utils.agent_memory")
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    middleware = importlib.import_module("open_webui.utils.middleware")
    tools = importlib.import_module("open_webui.utils.tools")

    vector_client = FakeVectorClient()
    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", vector_client)
    monkeypatch.setattr(agent_memory, "ASYNC_VECTOR_DB_CLIENT", vector_client)

    async def fake_extraction_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        assert form_data["metadata"]["task"] == "agent_memory_extraction"
        prompt = form_data["messages"][0]["content"]
        assert "sk-foldersecret" not in prompt
        assert "https://signed.example.test/folder" not in prompt
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "raw_memory": (
                                    "Folder project uses pytest before commits. "
                                    "Token sk-foldersecret should be redacted."
                                ),
                                "rollout_summary": (
                                    "Folder summary prefers strict pytest proof and avoids "
                                    "https://signed.example.test/folder"
                                ),
                                "rollout_slug": "folder_project_memory",
                            }
                        )
                    }
                }
            ]
        }

    async def fake_consolidation_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        assert form_data["metadata"]["task"] == "agent_memory_consolidation"
        scope_type = form_data["metadata"]["scope_type"]
        if scope_type == "folder":
            prompt_payload = json.loads(form_data["messages"][0]["content"].rsplit("\n\n", 1)[1])
            has_folder_evidence = bool(prompt_payload["extraction_caches"])
            if has_folder_evidence:
                payload = {
                    "memory_summary_md": "Folder summary: run pytest before commits.",
                    "memory_md": "# Folder Memory\nUse pytest before commits.\nRespect project-specific decisions.",
                }
            else:
                payload = {
                    "memory_summary_md": "Folder summary: no durable project memory remains.",
                    "memory_md": "# Folder Memory\nNo durable project memory remains.",
                }
        else:
            payload = {
                "memory_summary_md": "Global summary: prefer concise status updates.",
                "memory_md": "# Global Memory\nPrefer concise status updates.",
            }
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}

    monkeypatch.setattr(extraction, "generate_chat_completion", fake_extraction_completion)
    monkeypatch.setattr(consolidation, "generate_chat_completion", fake_consolidation_completion)

    engine, session_factory = await _session_factory(tmp_path)
    request = _request(vector_store=vector_client)

    async with session_factory() as session:
        session.add(Folder(id="folder-1", user_id="user-1", name="Project", meta={}, created_at=1, updated_at=1))
        session.add(_chat("folder-chat", folder_id="folder-1", updated_at=1000))
        session.add(_message("folder-chat", "u1", "user", "Remember that this project uses pytest."))
        session.add(
            _message(
                "folder-chat",
                "a1",
                "assistant",
                "Sure. Secret sk-foldersecret and URL https://signed.example.test/folder should not persist.",
                created_at=1001,
            )
        )
        session.add(_chat("global-chat", updated_at=900))
        await AgentMemoryExtractionCaches.upsert_cache(
            user_id="user-1",
            chat_id="global-chat",
            source_updated_at=901,
            raw_memory="User prefers concise status updates.",
            rollout_summary="Concise status updates.",
            rollout_slug=None,
            generated_at=902,
            status="succeeded",
            db=session,
        )
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "queued", None, None, 0, None, None, 902, db=session
        )

        assert await extraction.enqueue_chat_extraction_if_needed(
            "folder-chat",
            config=request.app.state.config,
            now=1200,
            db=session,
        )
        assert await extraction.run_agent_memory_extraction_jobs_once(
            request,
            now=1210,
            limit=1,
            db=session,
        ) == 1

        cache = await AgentMemoryExtractionCaches.get_cache("user-1", "folder-chat", db=session)
        assert cache.status == "succeeded"
        assert set(extraction.parse_extraction_response(
            {
                "raw_memory": cache.raw_memory,
                "rollout_summary": cache.rollout_summary,
                "rollout_slug": cache.rollout_slug,
            }
        ).keys()) == {"raw_memory", "rollout_summary", "rollout_slug"}
        assert "sk-foldersecret" not in cache.raw_memory
        assert "signed.example" not in cache.rollout_summary
        assert await AgentMemoryExtractionJobs.get_job("user-1", "folder-chat", db=session) is None
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "folder", "folder-1", db=session)

        assert await consolidation.run_agent_memory_consolidation_jobs_once(
            request,
            now=1220,
            limit=5,
            db=session,
        ) == 2

        folder_summary = await AgentMemoryArtifacts.get_artifact(
            "user-1", "folder", "folder-1", "memory_summary.md", db=session
        )
        folder_memory = await AgentMemoryArtifacts.get_artifact(
            "user-1", "folder", "folder-1", "MEMORY.md", db=session
        )
        global_summary = await AgentMemoryArtifacts.get_artifact(
            "user-1", "global", "", "memory_summary.md", db=session
        )
        assert folder_summary.content == "Folder summary: run pytest before commits."
        assert folder_memory.content.startswith("# Folder Memory")
        assert global_summary.content == "Global summary: prefer concise status updates."

        for artifact in [folder_summary, folder_memory, global_summary]:
            note = await session.get(Note, artifact.note_id)
            assert note is not None
            assert note.meta["agent_memory"]["managed"] is True
            assert note.meta["agent_memory"]["path"] == artifact.path
            assert note.data["content"]["md"] == artifact.content

        assert set(vector_client.collections) == {
            "agent-memory-user-1-folder-folder-1",
            "agent-memory-user-1-global",
        }
        assert vector_client.collections["agent-memory-user-1-folder-folder-1"]
        assert vector_client.collections["agent-memory-user-1-global"]

        form_data = await middleware.apply_agent_memory_read_path(
            request,
            _form_data("native", chat_id="folder-chat"),
            user=SimpleNamespace(id="user-1", role="user"),
            db=session,
        )
        system_content = form_data["messages"][0]["content"]
        assert "Agent Memory" in system_content
        assert system_content.index("Folder summary: run pytest") < system_content.index(
            "Global summary: prefer concise"
        )

        non_native = await middleware.apply_agent_memory_read_path(
            request,
            _form_data("default", chat_id="folder-chat"),
            user=SimpleNamespace(id="user-1", role="user"),
            db=session,
        )
        assert non_native["messages"][0]["role"] == "user"

        builtin_tools = await tools.get_builtin_tools(
            request,
            {
                "__user__": {"id": "user-1", "role": "user"},
                "__metadata__": {"chat_id": "folder-chat"},
                "__db__": session,
            },
            features={},
            model={"info": {"meta": {"builtinTools": {"knowledge": False, "agent_memory": True}}}},
        )
        assert {"agent_memory_search", "agent_memory_read", "agent_memory_list"}.issubset(builtin_tools)
        assert not {"agent_memory_add", "agent_memory_delete", "agent_memory_replace"} & set(builtin_tools)

        search_payload = json.loads(
            await builtin_tools["agent_memory_search"]["callable"]("pytest", limit=3)
        )
        assert search_payload["results"][0]["scope"] == "current_folder"
        assert "Use pytest before commits" in search_payload["results"][0]["content"]
        read_payload = json.loads(
            await builtin_tools["agent_memory_read"]["callable"]("MEMORY.md", scope="current_folder")
        )
        assert read_payload["scope"] == "current_folder"
        assert "Use pytest before commits" in read_payload["content"]

        note = await session.get(Note, folder_summary.note_id)
        note.data = {"content": {"md": "Human edited summary: pytest plus explicit review."}}
        await session.commit()
        built = await consolidation.build_consolidation_input("user-1", "folder", "folder-1", db=session)
        assert built.human_revisions[0]["content"] == "Human edited summary: pytest plus explicit review."

        await agent_memory.set_chat_agent_memory_disabled(
            "user-1",
            "folder-chat",
            True,
            now=1230,
            db=session,
        )
        assert await AgentMemoryExtractionCaches.get_cache("user-1", "folder-chat", db=session) is None
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "folder", "folder-1", db=session)
        assert await consolidation.run_agent_memory_consolidation_jobs_once(
            request,
            now=1240,
            limit=5,
            db=session,
        ) == 1
        folder_summary = await AgentMemoryArtifacts.get_artifact(
            "user-1", "folder", "folder-1", "memory_summary.md", db=session
        )
        assert folder_summary.content == "Folder summary: no durable project memory remains."
        assert all(
            "Use pytest before commits" not in item.text
            for item in vector_client.collections["agent-memory-user-1-folder-folder-1"]
        )
        assert "folder-chat" not in [
            row.chat_id
            for row in (
                await session.execute(select(AgentMemoryExtractionCache))
            ).scalars().all()
        ]
        opted_out = await middleware.apply_agent_memory_read_path(
            request,
            _form_data("native", chat_id="folder-chat"),
            user=SimpleNamespace(id="user-1", role="user"),
            db=session,
        )
        assert opted_out["messages"][0]["role"] == "user"

        disabled_request = _request(config=_config(ENABLE_AGENT_MEMORY=False), vector_store=vector_client)
        assert await extraction.run_agent_memory_extraction_jobs_once(
            disabled_request,
            now=1250,
            limit=1,
            db=session,
        ) == 0
        assert await consolidation.run_agent_memory_consolidation_jobs_once(
            disabled_request,
            now=1250,
            limit=1,
            db=session,
        ) == 0
        disabled_read = await middleware.apply_agent_memory_read_path(
            disabled_request,
            _form_data("native", chat_id="global-chat"),
            user=SimpleNamespace(id="user-1", role="user"),
            db=session,
        )
        assert disabled_read["messages"][0]["role"] == "user"
        disabled_builtin_tools = await tools.get_builtin_tools(
            disabled_request,
            {
                "__user__": {"id": "user-1", "role": "user"},
                "__metadata__": {"chat_id": "global-chat"},
                "__db__": session,
            },
            features={},
            model={"info": {"meta": {"builtinTools": {"knowledge": False, "agent_memory": True}}}},
        )
        assert "agent_memory_list" not in disabled_builtin_tools

    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_memory_worker_cycle_consumes_queued_jobs_and_builds_artifacts_without_manual_run_api(
    tmp_path, monkeypatch
):
    workers = importlib.import_module("open_webui.utils.agent_memory_workers")
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    index = importlib.import_module("open_webui.utils.agent_memory_index")

    vector_client = FakeVectorClient()
    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", vector_client)

    async def fake_extraction_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        assert form_data["metadata"]["task"] == "agent_memory_extraction"
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "raw_memory": "Worker-created memory prefers production loops.",
                                "rollout_summary": "Worker-created summary.",
                                "rollout_slug": "worker_memory",
                            }
                        )
                    }
                }
            ]
        }

    async def fake_consolidation_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        assert form_data["metadata"]["task"] == "agent_memory_consolidation"
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "memory_summary_md": "Worker summary: production loop consumed jobs.",
                                "memory_md": "# Worker Memory\nProduction worker loops consume queued jobs.",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(extraction, "generate_chat_completion", fake_extraction_completion)
    monkeypatch.setattr(consolidation, "generate_chat_completion", fake_consolidation_completion)

    engine, session_factory = await _session_factory(tmp_path)
    request = _request(
        config=_config(
            AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT=1,
            AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT=1,
        ),
        vector_store=vector_client,
    )

    async with session_factory() as session:
        session.add(_chat("worker-chat", updated_at=1000))
        session.add(_message("worker-chat", "u1", "user", "Remember to use production worker loops."))
        session.add(_message("worker-chat", "a1", "assistant", "Stored.", created_at=1001))
        await session.commit()

        assert await extraction.enqueue_chat_extraction_if_needed(
            "worker-chat",
            config=request.app.state.config,
            now=1200,
            db=session,
        )

        result = await workers.run_agent_memory_worker_cycle(request.app, db=session)

        assert result == {"extraction_completed": 1, "consolidation_completed": 1}
        cache = await AgentMemoryExtractionCaches.get_cache("user-1", "worker-chat", db=session)
        assert cache.status == "succeeded"
        assert "Worker-created memory" in cache.raw_memory
        assert await AgentMemoryExtractionJobs.get_job("user-1", "worker-chat", db=session) is None
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None

        summary = await AgentMemoryArtifacts.get_artifact(
            "user-1", "global", "", "memory_summary.md", db=session
        )
        memory = await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "MEMORY.md", db=session)
        assert summary.content == "Worker summary: production loop consumed jobs."
        assert memory.content.startswith("# Worker Memory")
        assert vector_client.collections["agent-memory-user-1-global"]

    await engine.dispose()
