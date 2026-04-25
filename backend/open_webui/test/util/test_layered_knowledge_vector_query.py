import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

from open_webui.models.knowledge_layers import KnowledgeLayers
import open_webui.models.knowledge_layers as layers_mod
import open_webui.utils.layered_knowledge as layered_mod
from open_webui.retrieval.vector.main import SearchResult


def _fake_request():
    captured = {}

    async def fake_embedding(text):
        captured["text"] = text
        return [0.25]

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=fake_embedding))
    )
    return request, captured


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, stmt):
        return _FakeResult(self.rows)


@pytest.mark.asyncio
async def test_query_layer_rows_uses_vector_search_and_preserves_order(monkeypatch):
    first = SimpleNamespace(
        id="row-1",
        knowledge_id="kb-1",
        file_id="file-1",
        layer_type="abstract",
        content="first summary",
        status="ready",
        display_title="Abstract 1/2",
    )
    second = SimpleNamespace(
        id="row-2",
        knowledge_id="kb-1",
        file_id="file-1",
        layer_type="abstract",
        content="second summary",
        status="ready",
        display_title="Abstract 2/2",
    )
    fake_db = _FakeDB(
        [
            (first, SimpleNamespace(id="file-1", filename="paper.pdf")),
            (second, SimpleNamespace(id="file-1", filename="paper.pdf")),
        ]
    )

    @asynccontextmanager
    async def _yield_session(db=None):
        yield fake_db

    monkeypatch.setattr(layers_mod, "get_async_db_context", _yield_session)

    request, embedding_call = _fake_request()
    search_call = {}

    async def fake_has_collection(collection_name):
        return True

    async def fake_search(collection_name, vectors, filter=None, limit=10):
        search_call.update(
            {
                "collection_name": collection_name,
                "vectors": vectors,
                "filter": filter,
                "limit": limit,
            }
        )
        return SearchResult(
            ids=[[f"knowledge-layer:{second.id}", f"knowledge-layer:{first.id}"]],
            documents=[["second summary", "first summary"]],
            metadatas=[
                [
                    {"layer_row_id": second.id},
                    {"layer_row_id": first.id},
                ]
            ],
            distances=[[0.11, 0.22]],
        )

    monkeypatch.setattr(layers_mod.ASYNC_VECTOR_DB_CLIENT, "has_collection", fake_has_collection, raising=False)
    monkeypatch.setattr(layers_mod.ASYNC_VECTOR_DB_CLIENT, "search", fake_search, raising=False)

    rows = await KnowledgeLayers.query_layer_rows(
        layer_type="abstract",
        query="summary please",
        knowledge_ids=["kb-1"],
        file_ids=["file-1"],
        limit=2,
        request=request,
        db=fake_db,
    )

    assert embedding_call["text"] == "summary please"
    assert search_call == {
        "collection_name": "knowledge-layers",
        "vectors": [[0.25]],
        "filter": {
            "knowledge_id": "kb-1",
            "file_id": "file-1",
            "layer_type": "abstract",
        },
        "limit": 2,
    }
    assert [row.content for row in rows] == ["second summary", "first summary"]
    assert [row.distance for row in rows] == [0.11, 0.22]
    assert rows[0].source == "Abstract 2/2: paper.pdf"


@pytest.mark.asyncio
async def test_query_layers_passes_request_and_keeps_view_layers_direct(monkeypatch):
    captured = {}

    async def fake_query_layer_rows(**kwargs):
        captured["request"] = kwargs["request"]
        return [
            {
                "layer_type": "abstract",
                "content": "semantic hit",
                "source": "paper.pdf",
                "file_id": "file-1",
                "knowledge_id": "kb-1",
                "distance": 0.2,
            }
        ]

    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "query_layer_rows",
        fake_query_layer_rows,
        raising=False,
    )
    async def fake_get_layers_for_scope_file(*args, **kwargs):
        return []

    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "get_layers_for_scope_file",
        fake_get_layers_for_scope_file,
        raising=False,
    )

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    rows = await layered_mod.query_layers(
        layer="abstract",
        query="semantic",
        scope_items=[{"id": "kb-1", "type": "collection"}],
        request=request,
        user={"id": "u1"},
    )
    payload = await layered_mod.get_file_layers(
        file_id="file-1",
        scope_items=[{"id": "kb-1", "type": "collection"}],
        request=request,
        user={"id": "u1"},
    )

    assert captured["request"] is request
    assert rows[0]["distance"] == 0.2
    assert payload == {"file_id": "file-1", "layers": {}}


@pytest.mark.asyncio
async def test_query_layer_rows_returns_empty_when_collection_missing(monkeypatch):
    fake_db = _FakeDB([])

    @asynccontextmanager
    async def _yield_session(db=None):
        yield fake_db

    monkeypatch.setattr(layers_mod, "get_async_db_context", _yield_session)
    request, embedding_call = _fake_request()

    async def fake_has_collection(collection_name):
        return False

    monkeypatch.setattr(layers_mod.ASYNC_VECTOR_DB_CLIENT, "has_collection", fake_has_collection, raising=False)

    rows = await KnowledgeLayers.query_layer_rows(
        layer_type="abstract",
        query="summary please",
        knowledge_ids=["kb-1"],
        file_ids=["file-1"],
        limit=2,
        request=request,
        db=fake_db,
    )

    assert embedding_call["text"] == "summary please"
    assert rows == []
