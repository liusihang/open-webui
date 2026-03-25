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
    return SimpleNamespace(id="kb-1", user_id="user-1")


def _fake_layer(layer_type="abstract", status="ready"):
    return SimpleNamespace(
        id="layer-1",
        knowledge_id="kb-1",
        file_id="file-1",
        layer_type=layer_type,
        title=None,
        content="content",
        status=status,
        source_system="open_notebook",
        source_ref_id="ins-1",
        transformation_ref_id="tr-1",
        content_hash=None,
        created_at=1,
        updated_at=2,
    )


def test_get_knowledge_file_layers_returns_rows(monkeypatch):
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_knowledge_by_id",
        lambda *args, **kwargs: _fake_knowledge(),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "has_file",
        lambda *args, **kwargs: True,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.KnowledgeLayers,
        "get_layers_by_file",
        lambda *args, **kwargs: [_fake_layer()],
        raising=False,
    )

    response = knowledge_mod.get_knowledge_file_layers(
        id="kb-1",
        file_id="file-1",
        user=_fake_user(),
        db=None,
    )

    assert response.total == 1
    assert response.items[0].layer_type == "abstract"


@pytest.mark.asyncio
async def test_regenerate_knowledge_file_layer_by_type_calls_service(monkeypatch):
    captured = {}

    def fake_regenerate(request, knowledge_id, file_id, layer_type, db=None):
        captured["knowledge_id"] = knowledge_id
        captured["file_id"] = file_id
        captured["layer_type"] = layer_type
        return []

    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_knowledge_by_id",
        lambda *args, **kwargs: _fake_knowledge(),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "has_file",
        lambda *args, **kwargs: True,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "regenerate_layer_for_file",
        fake_regenerate,
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod.KnowledgeLayers,
        "get_layers_by_file",
        lambda *args, **kwargs: [_fake_layer(layer_type="key_data", status="pending")],
        raising=False,
    )

    response = await knowledge_mod.regenerate_knowledge_file_layer_by_type(
        request=_fake_request(),
        id="kb-1",
        file_id="file-1",
        layer_type="key_data",
        user=_fake_user(),
        db=None,
    )

    assert captured == {
        "knowledge_id": "kb-1",
        "file_id": "file-1",
        "layer_type": "key_data",
    }
    assert response.total == 1
