import importlib
import os
import sys
import types
import uuid
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
from open_webui.retrieval import utils as retrieval_utils
from open_webui.retrieval.vector.main import SearchResult


async def _session_factory(tmp_path):
    db_path = tmp_path / "agent-memory-index.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    for table in [
        Chat.__table__,
        Folder.__table__,
        AgentMemoryArtifact.__table__,
    ]:
        table.create(sync_engine, checkfirst=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _user(user_id="user-1", role="user"):
    return SimpleNamespace(id=user_id, role=role)


def _request(*, embedding=None, relevance_threshold=0.5):
    async def default_embedding(text, prefix=None):
        if isinstance(text, list):
            return [[float(len(item) or 1)] for item in text]
        return [float(len(text) or 1)]

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                EMBEDDING_FUNCTION=embedding or default_embedding,
                config=SimpleNamespace(RELEVANCE_THRESHOLD=relevance_threshold),
            )
        )
    )


@pytest.mark.asyncio
async def test_filter_accessible_collections_denies_agent_memory_for_generic_retrieval(monkeypatch):
    async def fail_knowledge_fallback(collection_name, user_id, permission="read"):
        if collection_name.startswith("agent-memory-"):
            raise AssertionError("agent-memory collections must not fall through to knowledge ACL")
        return False

    monkeypatch.setattr(retrieval_utils.Knowledges, "check_access_by_user_id", fail_knowledge_fallback)

    result = await retrieval_utils.filter_accessible_collections(
        {"agent-memory-user-1-global"},
        _user(),
        access_type="read",
    )

    assert result == set()


@pytest.mark.asyncio
async def test_filter_accessible_collections_denies_agent_memory_for_admin_generic_retrieval(monkeypatch):
    async def fail_knowledge_fallback(collection_name, user_id, permission="read"):
        if collection_name.startswith("agent-memory-"):
            raise AssertionError("agent-memory collections must not fall through to knowledge ACL")
        return False

    monkeypatch.setattr(retrieval_utils.Knowledges, "check_access_by_user_id", fail_knowledge_fallback)

    result = await retrieval_utils.filter_accessible_collections(
        {"agent-memory-admin-global"},
        _user("admin", role="admin"),
        access_type="read",
    )

    assert result == set()


def _install_qdrant_stubs(monkeypatch):
    qdrant_client = types.ModuleType("qdrant_client")
    qdrant_client.QdrantClient = object
    http = types.ModuleType("qdrant_client.http")
    exceptions = types.ModuleType("qdrant_client.http.exceptions")
    exceptions.UnexpectedResponse = Exception
    http_models = types.ModuleType("qdrant_client.http.models")
    http_models.PointStruct = object
    qdrant_models = types.ModuleType("qdrant_client.models")
    qdrant_models.models = SimpleNamespace(
        FieldCondition=object,
        MatchValue=object,
        Filter=object,
        FilterSelector=object,
        VectorParams=object,
        Distance=SimpleNamespace(COSINE="Cosine"),
        PayloadSchemaType=SimpleNamespace(KEYWORD="keyword"),
    )
    for name, module in {
        "qdrant_client": qdrant_client,
        "qdrant_client.http": http,
        "qdrant_client.http.exceptions": exceptions,
        "qdrant_client.http.models": http_models,
        "qdrant_client.models": qdrant_models,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def _install_milvus_stubs(monkeypatch):
    pymilvus = types.ModuleType("pymilvus")
    pymilvus.Collection = object
    pymilvus.CollectionSchema = object
    pymilvus.DataType = SimpleNamespace(VARCHAR="VARCHAR", FLOAT_VECTOR="FLOAT_VECTOR", JSON="JSON")
    pymilvus.FieldSchema = object
    pymilvus.connections = SimpleNamespace(connect=lambda **kwargs: None)
    pymilvus.utility = SimpleNamespace(has_collection=lambda name: False, drop_collection=lambda name: None)
    monkeypatch.setitem(sys.modules, "pymilvus", pymilvus)


def test_qdrant_multitenancy_routes_agent_memory_away_from_knowledge(monkeypatch):
    _install_qdrant_stubs(monkeypatch)
    qdrant_mt = importlib.import_module("open_webui.retrieval.vector.dbs.qdrant_multitenancy")
    client = qdrant_mt.QdrantClient.__new__(qdrant_mt.QdrantClient)
    client.MEMORY_COLLECTION = "open_webui_memories"
    client.KNOWLEDGE_COLLECTION = "open_webui_knowledge"
    client.FILE_COLLECTION = "open_webui_files"
    client.WEB_SEARCH_COLLECTION = "open_webui_web-search"
    client.HASH_BASED_COLLECTION = "open_webui_hash-based"
    client.AGENT_MEMORY_COLLECTION = "open_webui_agent_memories"

    collection, tenant_id = client._get_collection_and_tenant_id("agent-memory-user-1-folder-folder-1")

    assert collection == "open_webui_agent_memories"
    assert tenant_id == "agent-memory-user-1-folder-folder-1"


def test_milvus_multitenancy_routes_agent_memory_away_from_knowledge(monkeypatch):
    _install_milvus_stubs(monkeypatch)
    milvus_mt = importlib.import_module("open_webui.retrieval.vector.dbs.milvus_multitenancy")
    client = milvus_mt.MilvusClient.__new__(milvus_mt.MilvusClient)
    client.MEMORY_COLLECTION = "open_webui_memories"
    client.KNOWLEDGE_COLLECTION = "open_webui_knowledge"
    client.FILE_COLLECTION = "open_webui_files"
    client.WEB_SEARCH_COLLECTION = "open_webui_web_search"
    client.HASH_BASED_COLLECTION = "open_webui_hash_based"
    client.AGENT_MEMORY_COLLECTION = "open_webui_agent_memories"

    collection, resource_id = client._get_collection_and_resource_id("agent-memory-user-1-global")

    assert collection == "open_webui_agent_memories"
    assert resource_id == "agent-memory-user-1-global"


def test_markdown_chunking_uses_stable_ids_and_metadata():
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    artifact = SimpleNamespace(
        user_id="user-1",
        scope_type="folder",
        scope_id="folder-1",
        path="MEMORY.md",
        content="# Project\nUse pytest.\n\n## Runtime\nOpenWebUI facts.\n",
        revision=3,
    )

    first = index.chunk_agent_memory_artifact(artifact)
    second = index.chunk_agent_memory_artifact(artifact)

    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    for chunk in first:
        parsed = uuid.UUID(chunk.id)
        assert str(parsed) == chunk.id
        assert len(chunk.id) <= 36
    assert [chunk.metadata["heading"] for chunk in first] == ["Project", "Runtime"]
    assert first[0].metadata == {
        "user_id": "user-1",
        "scope_type": "folder",
        "scope_id": "folder-1",
        "path": "MEMORY.md",
        "revision": 3,
        "heading": "Project",
        "chunk_index": 0,
    }


@pytest.mark.asyncio
async def test_rebuild_index_writes_scope_collection_and_deletes_stale_chunks(tmp_path, monkeypatch):
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)
    upsert_calls = []
    delete_calls = []

    class FakeVectorClient:
        async def delete(self, collection_name, ids=None, filter=None):
            delete_calls.append({"collection_name": collection_name, "ids": ids, "filter": filter})

        async def upsert(self, collection_name, items):
            upsert_calls.append({"collection_name": collection_name, "items": items})

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "folder",
            "folder-1",
            "MEMORY.md",
            "# Runtime\nUse focused tests.",
            "input-hash",
            7,
            None,
            None,
            1000,
            db=session,
        )

        await index.rebuild_agent_memory_index_for_scope(
            _request(),
            user_id="user-1",
            scope_type="folder",
            scope_id="folder-1",
            db=session,
        )

    assert delete_calls == [
        {
            "collection_name": "agent-memory-user-1-folder-folder-1",
            "ids": None,
            "filter": {"path": "MEMORY.md"},
        }
    ]
    assert upsert_calls[0]["collection_name"] == "agent-memory-user-1-folder-folder-1"
    assert upsert_calls[0]["items"][0].metadata["revision"] == 7
    await engine.dispose()


@pytest.mark.asyncio
async def test_search_requests_extra_candidates_before_filtering_stale_vector_rows(tmp_path, monkeypatch):
    index = importlib.import_module("open_webui.utils.agent_memory_index")
    engine, session_factory = await _session_factory(tmp_path)
    requested_limits = []

    all_rows = [
        {
            "id": "stale-revision",
            "document": "stale revision content",
            "metadata": {
                "scope_type": "global",
                "scope_id": "",
                "path": "MEMORY.md",
                "revision": 1,
                "heading": "Stale revision",
            },
            "score": 0.99,
        },
        {
            "id": "current-revision",
            "document": "current revision content",
            "metadata": {
                "scope_type": "global",
                "scope_id": "",
                "path": "MEMORY.md",
                "revision": 2,
                "heading": "Current revision",
            },
            "score": 0.98,
        },
    ]

    class FakeVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            requested_limits.append(limit)
            rows = all_rows[:limit]
            return SearchResult(
                ids=[[row["id"] for row in rows]],
                documents=[[row["document"] for row in rows]],
                metadatas=[[row["metadata"] for row in rows]],
                distances=[[row["score"] for row in rows]],
            )

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    async with session_factory() as session:
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "MEMORY.md",
            "current revision content",
            "hash",
            2,
            None,
            None,
            1000,
            db=session,
        )
        results = await index.search_agent_memory_for_chat(
            _request(relevance_threshold=0.5),
            "user-1",
            "",
            "runtime",
            limit=1,
            db=session,
        )

    assert requested_limits and requested_limits[0] > 1
    assert results == [
        {
            "scope": "global",
            "path": "MEMORY.md",
            "heading": "Current revision",
            "content": "current revision content",
            "score": 0.98,
        }
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_search_applies_relevance_threshold(monkeypatch):
    index = importlib.import_module("open_webui.utils.agent_memory_index")

    class FakeVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            return SearchResult(
                ids=[["keep", "drop"]],
                documents=[["kept content", "dropped content"]],
                metadatas=[
                    [
                        {"scope_type": "global", "scope_id": "", "path": "MEMORY.md", "heading": "Keep"},
                        {"scope_type": "global", "scope_id": "", "path": "MEMORY.md", "heading": "Drop"},
                    ]
                ],
                distances=[[0.8, 0.4]],
            )

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    results = await index.search_agent_memory_collections(
        _request(relevance_threshold=0.5),
        ["agent-memory-user-1-global"],
        "query",
        limit=5,
    )

    assert results == [
        {
            "scope": "global",
            "path": "MEMORY.md",
            "heading": "Keep",
            "content": "kept content",
            "score": 0.8,
        }
    ]


@pytest.mark.asyncio
async def test_search_drops_missing_score_when_threshold_configured(monkeypatch):
    index = importlib.import_module("open_webui.utils.agent_memory_index")

    class FakeVectorClient:
        async def search(self, collection_name, vectors, filter=None, limit=10):
            return SearchResult(
                ids=[["keep", "drop"]],
                documents=[["kept content", "scoreless content"]],
                metadatas=[
                    [
                        {"scope_type": "global", "scope_id": "", "path": "MEMORY.md", "heading": "Keep"},
                        {"scope_type": "global", "scope_id": "", "path": "MEMORY.md", "heading": "Scoreless"},
                    ]
                ],
                distances=[[0.8]],
            )

    monkeypatch.setattr(index, "ASYNC_VECTOR_DB_CLIENT", FakeVectorClient())

    results = await index.search_agent_memory_collections(
        _request(relevance_threshold=0.5),
        ["agent-memory-user-1-global"],
        "query",
        limit=5,
    )

    assert [item["heading"] for item in results] == ["Keep"]


def test_milvus_multitenancy_search_normalizes_scores_like_non_mt_milvus(monkeypatch):
    _install_milvus_stubs(monkeypatch)
    milvus_mt = importlib.import_module("open_webui.retrieval.vector.dbs.milvus_multitenancy")

    class FakeHit:
        def __init__(self, item_id, text, metadata, distance):
            self.entity = {"id": item_id, "text": text, "metadata": metadata}
            self.distance = distance

    class FakeCollection:
        def __init__(self, name):
            self.name = name

        def load(self):
            return None

        def search(self, data, anns_field, param, limit, expr, output_fields):
            return [
                [
                    FakeHit("a", "low raw score", {"path": "MEMORY.md"}, -0.2),
                    FakeHit("b", "high raw score", {"path": "MEMORY.md"}, 1.0),
                ]
            ]

    monkeypatch.setattr(milvus_mt.utility, "has_collection", lambda name: True)
    monkeypatch.setattr(milvus_mt, "Collection", FakeCollection)

    client = milvus_mt.MilvusClient.__new__(milvus_mt.MilvusClient)
    client.MEMORY_COLLECTION = "open_webui_memories"
    client.KNOWLEDGE_COLLECTION = "open_webui_knowledge"
    client.FILE_COLLECTION = "open_webui_files"
    client.WEB_SEARCH_COLLECTION = "open_webui_web_search"
    client.HASH_BASED_COLLECTION = "open_webui_hash_based"
    client.AGENT_MEMORY_COLLECTION = "open_webui_agent_memories"

    result = client.search("agent-memory-user-1-global", [[0.1]], limit=2)

    assert result.distances == [[0.4, 1.0]]
