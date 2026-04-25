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
        source_system="open_webui",
        source_ref_id="chunk:1",
        transformation_ref_id="tr-1",
        content_hash=None,
        part_index=1,
        part_total=1,
        display_title=None,
        embedding_status="ready",
        embedding_error=None,
        embedding_updated_at=2,
        created_at=1,
        updated_at=2,
    )


@pytest.mark.asyncio
async def test_get_knowledge_file_layers_returns_rows(monkeypatch):
    async def fake_get_knowledge(*args, **kwargs):
        return _fake_knowledge()

    async def fake_has_file(*args, **kwargs):
        return True

    async def fake_get_layers(*args, **kwargs):
        return [_fake_layer()]

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge, raising=False)
    monkeypatch.setattr(knowledge_mod.Knowledges, "has_file", fake_has_file, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeLayers, "get_layers_by_file", fake_get_layers, raising=False)

    response = await knowledge_mod.get_knowledge_file_layers(
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

    async def fake_regenerate(request, knowledge_id, file_id, layer_types=None, force=False, db=None):
        captured["knowledge_id"] = knowledge_id
        captured["file_id"] = file_id
        captured["layer_types"] = layer_types
        captured["force"] = force
        return []

    async def fake_get_knowledge(*args, **kwargs):
        return _fake_knowledge()

    async def fake_has_file(*args, **kwargs):
        return True

    async def fake_get_layers(*args, **kwargs):
        return [_fake_layer(layer_type="abstract", status="pending")]

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge, raising=False)
    monkeypatch.setattr(knowledge_mod.Knowledges, "has_file", fake_has_file, raising=False)
    monkeypatch.setattr(knowledge_mod, "regenerate_layers_for_file_async", fake_regenerate, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeLayers, "get_layers_by_file", fake_get_layers, raising=False)

    response = await knowledge_mod.regenerate_knowledge_file_layer_by_type(
        request=_fake_request(),
        id="kb-1",
        file_id="file-1",
        layer_type="abstract",
        user=_fake_user(),
        db=None,
    )

    assert captured == {
        "knowledge_id": "kb-1",
        "file_id": "file-1",
        "layer_types": ["abstract"],
        "force": False,
    }
    assert response.total == 1


@pytest.mark.asyncio
async def test_regenerate_knowledge_file_layers_accepts_layer_types(monkeypatch):
    captured = {}

    async def fake_regenerate(request, knowledge_id, file_id, layer_types=None, force=False, db=None):
        captured["knowledge_id"] = knowledge_id
        captured["file_id"] = file_id
        captured["layer_types"] = layer_types
        captured["force"] = force
        return []

    async def fake_get_knowledge(*args, **kwargs):
        return _fake_knowledge()

    async def fake_has_file(*args, **kwargs):
        return True

    async def fake_get_layers(*args, **kwargs):
        return [_fake_layer(layer_type="abstract", status="pending")]

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge, raising=False)
    monkeypatch.setattr(knowledge_mod.Knowledges, "has_file", fake_has_file, raising=False)
    monkeypatch.setattr(knowledge_mod, "regenerate_layers_for_file_async", fake_regenerate, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeLayers, "get_layers_by_file", fake_get_layers, raising=False)

    response = await knowledge_mod.regenerate_knowledge_file_layers(
        request=_fake_request(),
        id="kb-1",
        file_id="file-1",
        form_data=knowledge_mod.KnowledgeLayerRegenerateForm(
            layer_types=["abstract"],
            force=False,
        ),
        user=_fake_user(),
        db=None,
    )

    assert captured == {
        "knowledge_id": "kb-1",
        "file_id": "file-1",
        "layer_types": ["abstract"],
        "force": False,
    }
    assert response.total == 1


@pytest.mark.asyncio
async def test_regenerate_knowledge_file_layers_rejects_invalid_layer_types(monkeypatch):
    async def fake_regenerate(request, knowledge_id, file_id, layer_types=None, force=False, db=None):
        raise ValueError("Unsupported layer_type: unknown")

    async def fake_get_knowledge(*args, **kwargs):
        return _fake_knowledge()

    async def fake_has_file(*args, **kwargs):
        return True

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge, raising=False)
    monkeypatch.setattr(knowledge_mod.Knowledges, "has_file", fake_has_file, raising=False)
    monkeypatch.setattr(knowledge_mod, "regenerate_layers_for_file_async", fake_regenerate, raising=False)

    with pytest.raises(knowledge_mod.HTTPException) as exc:
        await knowledge_mod.regenerate_knowledge_file_layers(
            request=_fake_request(),
            id="kb-1",
            file_id="file-1",
            form_data=knowledge_mod.KnowledgeLayerRegenerateForm(
                layer_types=["unknown"],
                force=False,
            ),
            user=_fake_user(),
            db=None,
        )

    assert exc.value.status_code == 400
