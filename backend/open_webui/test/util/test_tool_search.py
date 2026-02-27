import types

import pytest

from open_webui.models.functions import FunctionModel
from open_webui.models.tools import ToolModel
from open_webui.utils import tool_search as ts


class FakeVectorDB:
    def __init__(self):
        self.upsert_calls = 0
        self.deleted_ids = []

    def get(self, collection_name):
        return types.SimpleNamespace(ids=[[]], documents=[[]], metadatas=[[]])

    def upsert(self, collection_name, items):
        self.upsert_calls += 1

    def delete(self, collection_name, ids=None, filter=None):
        if ids:
            self.deleted_ids.extend(ids)

    def search(self, collection_name, vectors, filter=None, limit=10):
        return types.SimpleNamespace(ids=[[]], distances=[[]], documents=[[]], metadatas=[[]])


def _fake_app(tool_server_connections=None):
    async def embed(text):
        return [float(len(str(text or "")))]

    config = types.SimpleNamespace(
        TOOL_SERVER_CONNECTIONS=tool_server_connections or [],
        TOOL_SEARCH_MCP_REBUILD_INTERVAL_HOURS=24,
        TOOL_SEARCH_MCP_REBUILD_ENABLED=True,
        TOOL_SEARCH_MCP_REBUILD_ON_STARTUP=False,
    )

    state = types.SimpleNamespace(
        config=config,
        EMBEDDING_FUNCTION=embed,
        FUNCTIONS={},
        MODELS={},
    )
    return types.SimpleNamespace(state=state)


def test_doc_builders_include_search_fields():
    tool = ToolModel(
        id="weather_tool",
        user_id="user_1",
        name="Weather Tool",
        content="",
        specs=[
            {
                "name": "get_weather",
                "description": "Get weather by city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "City name",
                        }
                    },
                },
            }
        ],
        meta={
            "description": "Base description",
            "search_description": "Search weather tools",
            "search_keywords": ["forecast", "weather"],
            "search_examples": ["weather in SF"],
            "search_enabled": True,
        },
        access_grants=[],
        updated_at=1,
        created_at=1,
    )

    function = FunctionModel(
        id="pipe_weather",
        user_id="user_1",
        name="Weather Pipe",
        type="pipe",
        content="",
        meta={
            "description": "Pipe description",
            "search_description": "Searchable pipe",
            "search_keywords": ["pipe", "weather"],
            "search_examples": ["summarize weather"],
            "search_enabled": True,
        },
        is_active=True,
        is_global=False,
        updated_at=1,
        created_at=1,
    )

    tool_docs = ts.build_catalog_docs_from_tool(tool)
    assert len(tool_docs) == 1
    assert "Search weather tools" in tool_docs[0].search_text
    assert "city" in tool_docs[0].search_text

    function_doc = ts.build_catalog_doc_from_function_pipe(
        function=function,
        model_id="pipe_weather",
        display_name="Weather Pipe",
        is_manifold=False,
        subpipe_id=None,
    )
    assert function_doc.source_type == "function_pipe"
    assert "Searchable pipe" in function_doc.search_text

    mcp_doc = ts.build_catalog_doc_from_mcp_tool(
        server_id="mcp_server",
        server_name="MCP Server",
        server_description="MCP weather server",
        auth_type="none",
        tool_spec={
            "name": "weather_lookup",
            "description": "Lookup weather",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    assert mcp_doc.source_type == "mcp"
    assert "mcp_server" in mcp_doc.search_text


@pytest.mark.asyncio
async def test_hash_diff_only_upserts_changed_docs(monkeypatch):
    fake_vector = FakeVectorDB()
    monkeypatch.setattr(ts, "VECTOR_DB_CLIENT", fake_vector)

    service = ts.ToolSearchService(_fake_app())
    service._cache_loaded = True

    spec = {
        "name": "tool_a",
        "description": "desc",
        "parameters": {"type": "object", "properties": {}},
    }

    doc_a = ts.CatalogDoc(
        doc_id="tool:a:fn:tool_a",
        source_type="local_tool",
        spec_snapshot=spec,
        search_text="tool a",
        text_hash=ts.compute_text_hash("tool a", spec),
        metadata={"resource_id": "a"},
    )

    await service._upsert_docs([doc_a])
    await service._upsert_docs([doc_a])

    changed_doc_a = ts.CatalogDoc(
        doc_id="tool:a:fn:tool_a",
        source_type="local_tool",
        spec_snapshot=spec,
        search_text="tool a changed",
        text_hash=ts.compute_text_hash("tool a changed", spec),
        metadata={"resource_id": "a"},
    )
    await service._upsert_docs([changed_doc_a])

    assert fake_vector.upsert_calls == 2


def test_hybrid_rank_merges_vector_and_bm25_scores():
    ranked = ts.hybrid_rank(
        vector_scores={"a": 0.9, "b": 0.1},
        bm25_scores={"a": 1.0, "c": 2.0},
        bm25_weight=0.35,
    )

    assert ranked[0][0] == "a"
    assert {doc_id for doc_id, _ in ranked} == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_mcp_oauth_server_is_skipped_and_recorded(monkeypatch):
    fake_vector = FakeVectorDB()
    monkeypatch.setattr(ts, "VECTOR_DB_CLIENT", fake_vector)

    class FailingMCPClient:
        async def connect(self, url, headers=None):
            raise AssertionError("oauth_2.1 server should be skipped before connect")

    monkeypatch.setattr(ts, "MCPClient", FailingMCPClient)

    app = _fake_app(
        [
            {
                "type": "mcp",
                "auth_type": "oauth_2.1",
                "url": "https://example.test/mcp",
                "info": {"id": "oauth_server", "name": "OAuth Server"},
                "config": {"enable": True},
            }
        ]
    )

    service = ts.ToolSearchService(app)
    await service.rebuild(scope="mcp")

    status = await service.get_status()
    assert "oauth_server" in status["oauth_skipped_servers"]
