from types import SimpleNamespace

import pytest

from open_webui.routers import knowledge as knowledge_mod


def _fake_user():
    return SimpleNamespace(id="user-1", role="user")


def _fake_admin():
    return SimpleNamespace(id="admin-1", role="admin")


def _fake_knowledge():
    payload = {
        "id": "kb-1",
        "user_id": "user-1",
        "name": "KB",
        "description": "desc",
        "data": {"file_ids": []},
        "meta": None,
        "access_grants": [],
        "created_at": 1,
        "updated_at": 1,
    }
    return SimpleNamespace(
        **payload,
        model_dump=lambda: payload,
    )


def _fake_file():
    return SimpleNamespace(id="file-1", hash="hash-1")


def test_remove_file_from_knowledge_cleans_layer_vectors(monkeypatch):
    cleaned = {}

    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_knowledge_by_id",
        lambda *args, **kwargs: _fake_knowledge(),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Files,
        "get_file_by_id",
        lambda *args, **kwargs: _fake_file(),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "has_file",
        lambda *args, **kwargs: True,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "remove_file_from_knowledge_by_id",
        lambda *args, **kwargs: True,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.KnowledgeLayers,
        "delete_layers_by_file",
        lambda *args, **kwargs: 1,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "invalidate_bm25_cache",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "delete_layer_embeddings_by_file_id",
        lambda file_id: cleaned.setdefault("file_id", file_id),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.VECTOR_DB_CLIENT,
        "delete",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.VECTOR_DB_CLIENT,
        "has_collection",
        lambda *args, **kwargs: False,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Files,
        "delete_file_by_id",
        lambda *args, **kwargs: True,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_file_metadatas_by_id",
        lambda *args, **kwargs: [],
        raising=False,
    )

    response = knowledge_mod.remove_file_from_knowledge_by_id(
        id="kb-1",
        form_data=knowledge_mod.KnowledgeFileIdForm(file_id="file-1"),
        delete_file=False,
        user=_fake_user(),
        db=None,
    )

    assert cleaned == {"file_id": "file-1"}
    assert response.id == "kb-1"


@pytest.mark.asyncio
async def test_delete_knowledge_cleans_layer_vectors(monkeypatch):
    cleaned = {}

    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_knowledge_by_id",
        lambda *args, **kwargs: _fake_knowledge(),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Models,
        "get_all_models",
        lambda *args, **kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "invalidate_bm25_cache",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "remove_knowledge_base_metadata_embedding",
        lambda *args, **kwargs: True,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "delete_layer_embeddings_by_knowledge_id",
        lambda knowledge_id: cleaned.setdefault("knowledge_id", knowledge_id),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.VECTOR_DB_CLIENT,
        "delete_collection",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "delete_knowledge_by_id",
        lambda *args, **kwargs: True,
        raising=False,
    )

    result = await knowledge_mod.delete_knowledge_by_id(
        id="kb-1",
        user=_fake_admin(),
        db=None,
    )

    assert cleaned == {"knowledge_id": "kb-1"}
    assert result is True
