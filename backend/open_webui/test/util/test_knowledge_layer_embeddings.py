import asyncio
from types import SimpleNamespace

import open_webui.utils.knowledge_layer_embeddings as embeddings_mod
import open_webui.utils.layered_knowledge as layered_mod


def _fake_request():
    async def embedding_function(text, user=None):
        return [float(len(text or ""))]

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                EMBEDDING_FUNCTION=embedding_function,
            )
        )
    )


def _fake_row(**overrides):
    payload = {
        "id": "row-1",
        "knowledge_id": "kb-1",
        "file_id": "file-1",
        "layer_type": "abstract",
        "content": "summary text",
        "status": "ready",
        "embedding_status": "pending",
        "part_index": 1,
        "part_total": 2,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_sync_file_layer_embeddings_upserts_ready_rows_with_stable_ids_and_metadata(monkeypatch):
    captured = {}
    rows = [_fake_row(id="row-1"), _fake_row(id="row-2", part_index=2)]

    monkeypatch.setattr(
        embeddings_mod.KnowledgeLayers,
        "get_layers_by_file",
        lambda knowledge_id, file_id, db=None: rows,
        raising=False,
    )
    monkeypatch.setattr(
        embeddings_mod.KnowledgeLayers,
        "mark_embedding_indexing",
        lambda row_id, db=None: None,
        raising=False,
    )
    monkeypatch.setattr(
        embeddings_mod.KnowledgeLayers,
        "mark_embedding_ready",
        lambda row_id, db=None: None,
        raising=False,
    )
    monkeypatch.setattr(
        embeddings_mod.VECTOR_DB_CLIENT,
        "upsert",
        lambda collection_name, items: captured.update(
            {"collection_name": collection_name, "items": items}
        ),
        raising=False,
    )

    count = asyncio.run(
        embeddings_mod.sync_file_layer_embeddings(_fake_request(), "kb-1", "file-1")
    )

    assert count == 2
    assert captured["collection_name"] == "knowledge-layers"
    assert [item["id"] for item in captured["items"]] == [
        "knowledge-layer:row-1",
        "knowledge-layer:row-2",
    ]
    assert captured["items"][0]["metadata"] == {
        "knowledge_id": "kb-1",
        "file_id": "file-1",
        "layer_type": "abstract",
        "part_index": 1,
        "part_total": 2,
        "layer_row_id": "row-1",
    }


def test_file_needs_backfill_when_embedding_not_ready():
    rows = [_fake_row(status="ready", embedding_status="pending")]

    assert layered_mod._file_needs_backfill(rows, ["abstract"]) is True
