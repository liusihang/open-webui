from types import SimpleNamespace

import pytest

from open_webui.routers import knowledge as knowledge_mod
import open_webui.utils.layered_knowledge as layered_mod


def _fake_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    OPEN_NOTEBOOK_BASE_URL="",
                    OPEN_NOTEBOOK_API_PASSWORD="",
                    OPEN_NOTEBOOK_TIMEOUT_SECS=12,
                )
            )
        )
    )


def _fake_user():
    return SimpleNamespace(id="user-1", role="user")


def _fake_knowledge():
    return SimpleNamespace(id="kb-1", user_id="user-1")


def _fake_file(file_id: str):
    return SimpleNamespace(id=file_id)


def _fake_layer(layer_type: str, status: str):
    return SimpleNamespace(layer_type=layer_type, status=status, part_index=1)


def test_backfill_layers_for_knowledge_selects_missing_failed_and_stale(monkeypatch):
    scheduled = []
    files = [_fake_file("f1"), _fake_file("f2"), _fake_file("f3"), _fake_file("f4")]

    def fake_get_layers_by_file(knowledge_id, file_id, db=None):
        if file_id == "f1":
            return []
        if file_id == "f2":
            return [_fake_layer("abstract", "failed")]
        if file_id == "f3":
            return [_fake_layer("abstract", "stale")]
        return [_fake_layer("abstract", "ready")]

    async def fake_regenerate(request, knowledge_id, file_id, layer_types=None, force=False, db=None):
        scheduled.append((file_id, tuple(layer_types or []), force))
        return []

    monkeypatch.setattr(layered_mod.Knowledges, "get_files_by_id", lambda *args, **kwargs: files)
    monkeypatch.setattr(layered_mod.KnowledgeLayers, "get_layers_by_file", fake_get_layers_by_file)
    monkeypatch.setattr(layered_mod, "regenerate_layers_for_file_async", fake_regenerate)

    summary = layered_mod.backfill_layers_for_knowledge(
        _fake_request(),
        "kb-1",
        layer_types=["abstract"],
        force=False,
    )

    assert summary == {"total_files": 4, "scheduled_files": 3, "skipped_files": 1}
    assert [item[0] for item in scheduled] == ["f1", "f2", "f3"]


def test_backfill_layers_for_knowledge_force_reprocesses_all(monkeypatch):
    scheduled = []
    files = [_fake_file("f1"), _fake_file("f2")]

    async def fake_regenerate(
        request, knowledge_id, file_id, layer_types=None, force=False, db=None
    ):
        scheduled.append(file_id)

    monkeypatch.setattr(layered_mod.Knowledges, "get_files_by_id", lambda *args, **kwargs: files)
    monkeypatch.setattr(
        layered_mod.KnowledgeLayers,
        "get_layers_by_file",
        lambda *args, **kwargs: [_fake_layer("abstract", "ready")],
    )
    monkeypatch.setattr(
        layered_mod,
        "regenerate_layers_for_file_async",
        fake_regenerate,
    )

    summary = layered_mod.backfill_layers_for_knowledge(
        _fake_request(),
        "kb-1",
        layer_types=["abstract"],
        force=True,
    )

    assert summary == {"total_files": 2, "scheduled_files": 2, "skipped_files": 0}
    assert scheduled == ["f1", "f2"]


def test_backfill_layers_for_knowledge_honors_selected_layer_types(monkeypatch):
    scheduled = []
    files = [_fake_file("f1"), _fake_file("f2")]

    async def fake_regenerate(
        request, knowledge_id, file_id, layer_types=None, force=False, db=None
    ):
        scheduled.append(file_id)

    def fake_get_layers_by_file(knowledge_id, file_id, db=None):
        if file_id == "f1":
            return [_fake_layer("abstract", "ready")]
        return []

    monkeypatch.setattr(layered_mod.Knowledges, "get_files_by_id", lambda *args, **kwargs: files)
    monkeypatch.setattr(layered_mod.KnowledgeLayers, "get_layers_by_file", fake_get_layers_by_file)
    monkeypatch.setattr(
        layered_mod,
        "regenerate_layers_for_file_async",
        fake_regenerate,
    )

    summary = layered_mod.backfill_layers_for_knowledge(
        _fake_request(),
        "kb-1",
        layer_types=["abstract"],
        force=False,
    )

    assert summary == {"total_files": 2, "scheduled_files": 1, "skipped_files": 1}
    assert scheduled == ["f2"]


@pytest.mark.asyncio
async def test_backfill_knowledge_layers_endpoint_returns_summary(monkeypatch):
    async def fake_backfill(request, knowledge_id, layer_types=None, force=False, db=None):
        return {
            "total_files": 5,
            "scheduled_files": 3,
            "skipped_files": 2,
        }

    monkeypatch.setattr(
        knowledge_mod.Knowledges,
        "get_knowledge_by_id",
        lambda *args, **kwargs: _fake_knowledge(),
        raising=False,
    )
    monkeypatch.setattr(
        knowledge_mod,
        "backfill_layers_for_knowledge_async",
        fake_backfill,
        raising=False,
    )

    response = await knowledge_mod.backfill_knowledge_layers(
        request=_fake_request(),
        id="kb-1",
        form_data=knowledge_mod.KnowledgeLayerBackfillForm(
            layer_types=["abstract"],
            force=False,
        ),
        user=_fake_user(),
        db=None,
    )

    assert response.total_files == 5
    assert response.scheduled_files == 3
    assert response.skipped_files == 2
