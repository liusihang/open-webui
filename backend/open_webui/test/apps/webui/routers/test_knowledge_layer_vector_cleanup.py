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
    return SimpleNamespace(id="file-1", hash="hash-1", user_id="user-1")


@pytest.mark.asyncio
async def test_remove_file_from_knowledge_cleans_layer_vectors(monkeypatch):
    cleaned = {}

    async def fake_get_knowledge(*args, **kwargs):
        return _fake_knowledge()

    async def fake_get_file(*args, **kwargs):
        return _fake_file()

    async def fake_has_file(*args, **kwargs):
        return True

    async def fake_remove_file(*args, **kwargs):
        return True

    async def fake_delete_layers(*args, **kwargs):
        return 1

    async def fake_delete_layer_embeddings(file_id):
        cleaned["file_id"] = file_id

    async def fake_delete(*args, **kwargs):
        return None

    async def fake_has_collection(*args, **kwargs):
        return False

    async def fake_get_file_metadatas(*args, **kwargs):
        return []

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge, raising=False)
    monkeypatch.setattr(knowledge_mod.Files, "get_file_by_id", fake_get_file, raising=False)
    monkeypatch.setattr(knowledge_mod.Knowledges, "has_file", fake_has_file, raising=False)
    monkeypatch.setattr(knowledge_mod.Knowledges, "remove_file_from_knowledge_by_id", fake_remove_file, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeLayers, "delete_layers_by_file", fake_delete_layers, raising=False)
    monkeypatch.setattr(knowledge_mod, "delete_layer_embeddings_by_file_id", fake_delete_layer_embeddings, raising=False)
    monkeypatch.setattr(knowledge_mod.ASYNC_VECTOR_DB_CLIENT, "delete", fake_delete, raising=False)
    monkeypatch.setattr(knowledge_mod.ASYNC_VECTOR_DB_CLIENT, "has_collection", fake_has_collection, raising=False)
    monkeypatch.setattr(knowledge_mod.Knowledges, "get_file_metadatas_by_id", fake_get_file_metadatas, raising=False)

    response = await knowledge_mod.remove_file_from_knowledge_by_id(
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

    async def fake_get_knowledge(*args, **kwargs):
        return _fake_knowledge()

    async def fake_get_models(*args, **kwargs):
        return []

    async def fake_remove_embedding(*args, **kwargs):
        return True

    async def fake_delete_layer_embeddings(knowledge_id):
        cleaned["knowledge_id"] = knowledge_id

    async def fake_delete_collection(*args, **kwargs):
        return None

    async def fake_delete_layers(*args, **kwargs):
        return 1

    async def fake_delete_knowledge(*args, **kwargs):
        return True

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge, raising=False)
    monkeypatch.setattr(knowledge_mod.Models, "get_all_models", fake_get_models, raising=False)
    async def fake_has_access(*args, **kwargs):
        return False

    monkeypatch.setattr(knowledge_mod.AccessGrants, "has_access", fake_has_access, raising=False)
    monkeypatch.setattr(knowledge_mod, "remove_knowledge_base_metadata_embedding", fake_remove_embedding, raising=False)
    monkeypatch.setattr(knowledge_mod, "delete_layer_embeddings_by_knowledge_id", fake_delete_layer_embeddings, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeLayers, "delete_layers_by_knowledge", fake_delete_layers, raising=False)
    monkeypatch.setattr(knowledge_mod.ASYNC_VECTOR_DB_CLIENT, "delete_collection", fake_delete_collection, raising=False)
    monkeypatch.setattr(knowledge_mod.Knowledges, "delete_knowledge_by_id", fake_delete_knowledge, raising=False)

    result = await knowledge_mod.delete_knowledge_by_id(
        id="kb-1",
        user=_fake_admin(),
        db=None,
    )

    assert cleaned == {"knowledge_id": "kb-1"}
    assert result is True
