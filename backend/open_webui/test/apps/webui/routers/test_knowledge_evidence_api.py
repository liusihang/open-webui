from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

from open_webui.routers import knowledge as knowledge_mod


def _fake_request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace())),
    )


def _fake_user(user_id: str = "user-1", role: str = "user"):
    return SimpleNamespace(id=user_id, role=role)


def _fake_knowledge(knowledge_id: str = "kb-1", user_id: str = "user-1"):
    return SimpleNamespace(
        id=knowledge_id,
        user_id=user_id,
        name="Knowledge",
        description="",
        meta=None,
        access_grants=[],
        created_at=1,
        updated_at=1,
        model_dump=lambda: {
            "id": knowledge_id,
            "user_id": user_id,
            "name": "Knowledge",
            "description": "",
            "meta": None,
            "access_grants": [],
            "created_at": 1,
            "updated_at": 1,
        },
    )


def _fake_text_evidence(
    *,
    knowledge_id: str = "kb-1",
    file_id: str = "file-1",
    evidence_ref: str = "ke:kb-1:file-1:text_chunk:1:abc123",
):
    return SimpleNamespace(
        id="evidence-1",
        evidence_ref=evidence_ref,
        knowledge_id=knowledge_id,
        file_id=file_id,
        asset_id=None,
        retrieval_chunk_uid="chunk-1",
        retrieval_chunk_row_id=1,
        modality="text",
        evidence_kind="text_chunk",
        title="Text evidence",
        content_text="This is the evidence body.",
        preview_text="Short preview.",
        source_name="source.txt",
        page_index=None,
        anchor_json=None,
        chunk_index=1,
        chunk_total=1,
        content_hash="hash-1",
        projection_profile="text_only",
        projection_config_hash="cfg-1",
        is_active=True,
        deleted_at=None,
        created_at=1,
        updated_at=1,
    )


def _fake_image_asset(
    *,
    knowledge_id: str = "kb-1",
    file_id: str = "file-1",
    mime_type: str = "image/png",
    storage_uri: str = "/tmp/evidence-image.png",
    asset_id: str = "asset-1",
    asset_ref: str = "ka:kb-1:file-1:standalone_image:abc123",
):
    return SimpleNamespace(
        id=asset_id,
        knowledge_id=knowledge_id,
        file_id=file_id,
        asset_ref=asset_ref,
        asset_kind="standalone_image",
        mime_type=mime_type,
        storage_uri=storage_uri,
        sha256="sha256-1",
        width=100,
        height=80,
        page_index=None,
        bbox_json=None,
        anchor_json=None,
        caption="Thumbnail caption",
        ocr_text="BOX A",
        surrounding_text=None,
        status="ready",
        error=None,
        created_at=1,
        updated_at=1,
    )


def _route(path, method):
    for route in knowledge_mod.router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"missing route {method} {path}")


def test_evidence_routes_are_registered_before_dynamic_id_routes():
    paths = [getattr(route, "path", None) for route in knowledge_mod.router.routes]

    assert paths.index("/{id}/evidence/{evidence_ref}") < paths.index("/{id}")
    assert paths.index("/{id}/evidence/{evidence_ref}/thumbnail") < paths.index("/{id}")
    assert paths.index("/{id}/evidence/{evidence_ref}/content") < paths.index("/{id}")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/{id}/evidence/{evidence_ref}", "GET"),
        ("/{id}/evidence/{evidence_ref}/thumbnail", "GET"),
        ("/{id}/evidence/{evidence_ref}/content", "GET"),
    ],
)
def test_evidence_routes_require_verified_user(path, method):
    route = _route(path, method)
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}

    assert knowledge_mod.get_verified_user in dependency_calls


@pytest.mark.asyncio
async def test_get_knowledge_evidence_metadata_returns_preview_and_content_urls(monkeypatch):
    async def fake_get_knowledge_by_id(*args, **kwargs):
        return _fake_knowledge()

    async def fake_get_evidence_by_ref(*args, **kwargs):
        return _fake_text_evidence()

    async def fake_has_access(*args, **kwargs):
        return True

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge_by_id, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref, raising=False)
    monkeypatch.setattr(knowledge_mod.AccessGrants, "has_access", fake_has_access, raising=False)

    response = await knowledge_mod.get_knowledge_evidence_by_ref(
        id="kb-1",
        evidence_ref="ke:kb-1:file-1:text_chunk:1:abc123",
        user=_fake_user(),
        db=None,
    )

    assert response["evidence_ref"] == "ke:kb-1:file-1:text_chunk:1:abc123"
    assert response["preview"]["type"] == "text"
    assert response["preview"]["text"] == "Short preview."
    assert response["content_url"].endswith("/content")
    assert response["thumbnail_url"] is None


@pytest.mark.asyncio
async def test_get_knowledge_evidence_metadata_rejects_missing_rows(monkeypatch):
    async def fake_get_knowledge_by_id(*args, **kwargs):
        return _fake_knowledge()

    async def fake_get_evidence_by_ref(*args, **kwargs):
        return None

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge_by_id, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref, raising=False)

    with pytest.raises(knowledge_mod.HTTPException) as exc:
        await knowledge_mod.get_knowledge_evidence_by_ref(
            id="kb-1",
            evidence_ref="ke:missing",
            user=_fake_user(),
            db=None,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_knowledge_evidence_thumbnail_rejects_svg_assets(monkeypatch):
    async def fake_get_knowledge_by_id(*args, **kwargs):
        return _fake_knowledge()

    async def fake_get_evidence_by_ref(*args, **kwargs):
        evidence = _fake_text_evidence()
        return SimpleNamespace(**{**evidence.__dict__, "modality": "image", "asset_id": "asset-1"})

    async def fake_get_asset_by_id(*args, **kwargs):
        return _fake_image_asset(mime_type="image/svg+xml")

    async def fake_list_variants(*args, **kwargs):
        return []

    async def fake_has_access(*args, **kwargs):
        return True

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge_by_id, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidenceAssets, "get_asset_by_id", fake_get_asset_by_id, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidenceAssetVariants, "list_variants", fake_list_variants, raising=False)
    monkeypatch.setattr(knowledge_mod.AccessGrants, "has_access", fake_has_access, raising=False)

    with pytest.raises(knowledge_mod.HTTPException) as exc:
        await knowledge_mod.get_knowledge_evidence_thumbnail_by_ref(
            id="kb-1",
            evidence_ref="ke:kb-1:file-1:figure:1:abc123",
            user=_fake_user(),
            db=None,
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_knowledge_evidence_content_streams_text_evidence(monkeypatch):
    async def fake_get_knowledge_by_id(*args, **kwargs):
        return _fake_knowledge()

    async def fake_get_evidence_by_ref(*args, **kwargs):
        return _fake_text_evidence()

    async def fake_has_access(*args, **kwargs):
        return True

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge_by_id, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref, raising=False)
    monkeypatch.setattr(knowledge_mod.AccessGrants, "has_access", fake_has_access, raising=False)

    response = await knowledge_mod.get_knowledge_evidence_content_by_ref(
        id="kb-1",
        evidence_ref="ke:kb-1:file-1:text_chunk:1:abc123",
        user=_fake_user(),
        db=None,
    )

    assert response.media_type == "text/plain"
    assert response.body.decode("utf-8") == "This is the evidence body."


@pytest.mark.asyncio
async def test_get_knowledge_evidence_content_streams_image_bytes(monkeypatch):
    tmp_file = tempfile.NamedTemporaryFile(delete=False)
    tmp_file.write(b"image-bytes")
    tmp_file.flush()
    tmp_file.close()

    async def fake_get_knowledge_by_id(*args, **kwargs):
        return _fake_knowledge()

    async def fake_get_evidence_by_ref(*args, **kwargs):
        evidence = _fake_text_evidence()
        return SimpleNamespace(**{**evidence.__dict__, "modality": "image", "asset_id": "asset-1"})

    async def fake_get_asset_by_id(*args, **kwargs):
        return _fake_image_asset(storage_uri=tmp_file.name)

    async def fake_list_variants(*args, **kwargs):
        return []

    async def fake_has_access(*args, **kwargs):
        return True

    monkeypatch.setattr(knowledge_mod.Knowledges, "get_knowledge_by_id", fake_get_knowledge_by_id, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidenceAssets, "get_asset_by_id", fake_get_asset_by_id, raising=False)
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidenceAssetVariants, "list_variants", fake_list_variants, raising=False)
    monkeypatch.setattr(knowledge_mod.AccessGrants, "has_access", fake_has_access, raising=False)
    monkeypatch.setattr(knowledge_mod.Storage, "get_file", lambda path: path, raising=False)

    response = await knowledge_mod.get_knowledge_evidence_content_by_ref(
        id="kb-1",
        evidence_ref="ke:kb-1:file-1:figure:1:abc123",
        user=_fake_user(),
        db=None,
    )

    assert response.media_type == "image/png"
    assert str(getattr(response, "path", None)) == tmp_file.name
