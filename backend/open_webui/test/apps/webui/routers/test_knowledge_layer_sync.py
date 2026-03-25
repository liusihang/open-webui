from types import SimpleNamespace

import pytest

from open_webui.routers import knowledge as knowledge_mod


def _fake_request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace()))
    )


def _fake_user():
    return SimpleNamespace(id="user-1", role="user")


def _fake_knowledge():
    return SimpleNamespace(
        id="kb-1",
        user_id="user-1",
        model_dump=lambda: {
            "id": "kb-1",
            "user_id": "user-1",
            "name": "KB",
            "description": "desc",
            "meta": None,
            "access_grants": [],
            "created_at": 1,
            "updated_at": 1,
        },
    )


def _fake_file():
    return SimpleNamespace(id="file-1", hash="hash-1", data={"content": "hello"})


def test_add_file_to_knowledge_triggers_layer_sync(monkeypatch):
    captured = {}

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
    monkeypatch.setattr(knowledge_mod, "process_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "add_file_to_knowledge_by_id",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_file_metadatas_by_id",
        lambda *args, **kwargs: [],
        raising=False,
    )

    def fake_sync_layers_for_file(request, knowledge_id, file_id, db=None):
        captured["knowledge_id"] = knowledge_id
        captured["file_id"] = file_id

    monkeypatch.setattr(
        knowledge_mod,
        "sync_layers_for_file",
        fake_sync_layers_for_file,
        raising=False,
    )

    response = knowledge_mod.add_file_to_knowledge_by_id(
        request=_fake_request(),
        id="kb-1",
        form_data=knowledge_mod.KnowledgeFileIdForm(file_id="file-1"),
        user=_fake_user(),
        db=None,
    )

    assert captured == {"knowledge_id": "kb-1", "file_id": "file-1"}
    assert response.id == "kb-1"


def test_update_file_from_knowledge_marks_stale_and_syncs(monkeypatch):
    captured = {"stale": False, "sync": False}

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
        "get_file_metadatas_by_id",
        lambda *args, **kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.VECTOR_DB_CLIENT,
        "delete",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "invalidate_bm25_cache",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(knowledge_mod, "process_file", lambda *args, **kwargs: None)

    def fake_mark_stale(knowledge_id, file_id, db=None):
        captured["stale"] = (knowledge_id, file_id)

    def fake_sync(request, knowledge_id, file_id, db=None):
        captured["sync"] = (knowledge_id, file_id)

    monkeypatch.setattr(
        knowledge_mod,
        "mark_layers_for_file_stale",
        fake_mark_stale,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "sync_layers_for_file",
        fake_sync,
        raising=False,
    )

    response = knowledge_mod.update_file_from_knowledge_by_id(
        request=_fake_request(),
        id="kb-1",
        form_data=knowledge_mod.KnowledgeFileIdForm(file_id="file-1"),
        user=_fake_user(),
        db=None,
    )

    assert captured["stale"] == ("kb-1", "file-1")
    assert captured["sync"] == ("kb-1", "file-1")
    assert response.id == "kb-1"


@pytest.mark.asyncio
async def test_reset_knowledge_marks_all_layers_stale(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_knowledge_by_id",
        lambda *args, **kwargs: _fake_knowledge(),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "reset_knowledge_by_id",
        lambda *args, **kwargs: _fake_knowledge(),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.VECTOR_DB_CLIENT,
        "delete_collection",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "invalidate_bm25_cache",
        lambda *args, **kwargs: None,
        raising=False,
    )

    def fake_mark_layers_for_knowledge_stale(knowledge_id, db=None):
        captured["knowledge_id"] = knowledge_id

    monkeypatch.setattr(
        knowledge_mod,
        "mark_layers_for_knowledge_stale",
        fake_mark_layers_for_knowledge_stale,
        raising=False,
    )

    response = await knowledge_mod.reset_knowledge_by_id(
        id="kb-1",
        user=_fake_user(),
        db=None,
    )

    assert captured["knowledge_id"] == "kb-1"
    assert response.id == "kb-1"
