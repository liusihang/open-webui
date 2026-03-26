import asyncio
import os
from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

from open_webui.internal.db import Base
from open_webui.models.files import File
from open_webui.models.knowledge import Knowledge, KnowledgeFile
from open_webui.models.knowledge_layers import (
    KnowledgeFileLayer,
    KnowledgeFileLayerUpsertForm,
    KnowledgeLayers,
)
import open_webui.models.knowledge_layers as layers_mod
import open_webui.utils.layered_knowledge as layered_mod
from open_webui.retrieval.vector.main import SearchResult


@contextmanager
def _yield_session(session):
    yield session


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            Knowledge.__table__,
            File.__table__,
            KnowledgeFile.__table__,
            KnowledgeFileLayer.__table__,
        ],
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return session_factory()


def _fake_request():
    captured = {}

    async def fake_embedding(text):
        captured["text"] = text
        return [0.25]

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=fake_embedding))
    )
    return request, captured


def test_query_layer_rows_uses_vector_search_and_preserves_order(monkeypatch):
    session = _make_session()
    monkeypatch.setattr(
        layers_mod, "get_db_context", lambda db=None: _yield_session(session)
    )

    session.add(
        Knowledge(
            id="kb-1",
            user_id="user-1",
            name="KB",
            description="desc",
            meta=None,
            created_at=1,
            updated_at=1,
        )
    )
    session.add(File(id="file-1", user_id="user-1", filename="paper.pdf", path="paper"))
    session.add(
        KnowledgeFile(
            id="kf-1",
            knowledge_id="kb-1",
            file_id="file-1",
            user_id="user-1",
            created_at=1,
            updated_at=1,
        )
    )
    session.commit()

    first = KnowledgeLayers.upsert_layer(
        KnowledgeFileLayerUpsertForm(
            knowledge_id="kb-1",
            file_id="file-1",
            layer_type="abstract",
            content="first summary",
            status="ready",
            display_title="Abstract 1/2",
            source_system="open_notebook",
            part_index=1,
            part_total=2,
            embedding_status="ready",
        ),
        db=session,
    )
    second = KnowledgeLayers.upsert_layer(
        KnowledgeFileLayerUpsertForm(
            knowledge_id="kb-1",
            file_id="file-1",
            layer_type="abstract",
            content="second summary",
            status="ready",
            display_title="Abstract 2/2",
            source_system="open_notebook",
            part_index=2,
            part_total=2,
            embedding_status="ready",
        ),
        db=session,
    )

    request, embedding_call = _fake_request()
    search_call = {}

    monkeypatch.setattr(
        layers_mod.VECTOR_DB_CLIENT,
        "has_collection",
        lambda collection_name: True,
        raising=False,
    )

    monkeypatch.setattr(
        layers_mod.VECTOR_DB_CLIENT,
        "search",
        lambda collection_name, vectors, filter=None, limit=10: (
            search_call.update(
                {
                    "collection_name": collection_name,
                    "vectors": vectors,
                    "filter": filter,
                    "limit": limit,
                }
            )
            or SearchResult(
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
        ),
        raising=False,
    )

    rows = asyncio.run(
        KnowledgeLayers.query_layer_rows(
            layer_type="abstract",
            query="summary please",
            knowledge_ids=["kb-1"],
            file_ids=["file-1"],
            limit=2,
            request=request,
            db=session,
        )
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


def test_query_layers_passes_request_and_keeps_view_layers_direct(monkeypatch):
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
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "get_layers_for_scope_file",
        lambda *args, **kwargs: [],
        raising=False,
    )

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    rows = asyncio.run(
        layered_mod.query_layers(
            layer="abstract",
            query="semantic",
            scope_items=[{"id": "kb-1", "type": "collection"}],
            request=request,
            user={"id": "u1"},
        )
    )
    payload = layered_mod.get_file_layers(
        file_id="file-1",
        scope_items=[{"id": "kb-1", "type": "collection"}],
        request=request,
        user={"id": "u1"},
    )

    assert captured["request"] is request
    assert rows[0]["distance"] == 0.2
    assert payload == {"file_id": "file-1", "layers": {}}


def test_query_layer_rows_returns_empty_when_collection_missing(monkeypatch):
    session = _make_session()
    monkeypatch.setattr(
        layers_mod, "get_db_context", lambda db=None: _yield_session(session)
    )

    request, embedding_call = _fake_request()

    monkeypatch.setattr(
        layers_mod.VECTOR_DB_CLIENT,
        "has_collection",
        lambda collection_name: False,
        raising=False,
    )

    rows = asyncio.run(
        KnowledgeLayers.query_layer_rows(
            layer_type="abstract",
            query="summary please",
            knowledge_ids=["kb-1"],
            file_ids=["file-1"],
            limit=2,
            request=request,
            db=session,
        )
    )

    assert embedding_call["text"] == "summary please"
    assert rows == []
