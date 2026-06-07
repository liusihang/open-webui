import base64
import json
import types
from pathlib import Path

import pytest

from open_webui.models.evidence import KnowledgeEvidenceAssetModel, KnowledgeEvidenceModel, KnowledgeVectorSpaceModel
from open_webui.retrieval import evidence as evidence_mod
from open_webui.tools.builtin import query_knowledge_evidence
from open_webui.utils.middleware import get_citation_source_from_tool_result, process_tool_result


class _FakeRequest:
    def __init__(self, **state):
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=types.SimpleNamespace(),
                **state,
            )
        )


def _text_evidence(ref: str = "ke:kb-1:file-text:text_chunk:1:abc") -> KnowledgeEvidenceModel:
    return KnowledgeEvidenceModel(
        id="ev-text",
        evidence_ref=ref,
        knowledge_id="kb-1",
        file_id="file-text",
        asset_id=None,
        retrieval_chunk_uid="chunk-1",
        retrieval_chunk_row_id=7,
        modality="text",
        evidence_kind="text_chunk",
        title="Text finding",
        content_text="The capsid shell has a conserved HK97-like fold.",
        preview_text="Conserved HK97-like fold.",
        source_name="paper.pdf",
        page_index=2,
        anchor_json={"section": "Results"},
        chunk_index=1,
        chunk_total=3,
        content_hash="hash-text",
        projection_profile="multimodal_unified_v1",
        projection_config_hash="profile-hash",
        is_active=True,
        deleted_at=None,
        created_at=1,
        updated_at=2,
    )


def _image_evidence(ref: str = "ke:kb-1:file-img:standalone_image:1:def") -> KnowledgeEvidenceModel:
    return KnowledgeEvidenceModel(
        id="ev-image",
        evidence_ref=ref,
        knowledge_id="kb-1",
        file_id="file-img",
        asset_id="asset-1",
        retrieval_chunk_uid=None,
        retrieval_chunk_row_id=None,
        modality="image",
        evidence_kind="standalone_image",
        title="Gel image",
        content_text="A microscopy panel with ring-like capsid particles.",
        preview_text="Ring-like capsid particles.",
        source_name="image.png",
        page_index=None,
        anchor_json={"bbox": [0, 0, 100, 100]},
        chunk_index=1,
        chunk_total=1,
        content_hash="hash-image",
        projection_profile="multimodal_unified_v1",
        projection_config_hash="profile-hash",
        is_active=True,
        deleted_at=None,
        created_at=1,
        updated_at=2,
    )


def _image_asset(path: str) -> KnowledgeEvidenceAssetModel:
    return KnowledgeEvidenceAssetModel(
        id="asset-1",
        knowledge_id="kb-1",
        file_id="file-img",
        asset_ref="asset:kb-1:file-img:asset-1",
        asset_kind="standalone_image",
        mime_type="image/png",
        storage_uri=path,
        sha256="sha-image",
        width=64,
        height=48,
        page_index=None,
        bbox_json=None,
        anchor_json=None,
        caption="A microscopy panel.",
        ocr_text=None,
        surrounding_text=None,
        status="ready",
        error=None,
        created_at=1,
        updated_at=2,
    )


def _vector_space(*, supports_image_query: bool = True) -> KnowledgeVectorSpaceModel:
    return KnowledgeVectorSpaceModel(
        id="vs-1",
        knowledge_id="kb-1",
        retrieval_profile="unified_multimodal_dense",
        embedding_model="fake-clip",
        embedding_dim=3,
        distance_metric="cosine",
        vector_backend="pgvector",
        supports_text_query=True,
        supports_image_query=supports_image_query,
        supports_text_evidence=True,
        supports_image_evidence=True,
        supports_multivector=False,
        projection_config_hash="profile-hash",
        active=True,
        created_at=1,
        updated_at=2,
    )


@pytest.mark.asyncio
async def test_query_knowledge_evidence_hydrates_exact_sql_truth_rows(monkeypatch):
    async def fake_get_evidence_by_ref(ref, db=None):
        return _text_evidence(ref) if ref == "ke:kb-1:file-text:text_chunk:1:abc" else None

    monkeypatch.setattr(evidence_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref)

    result = await query_knowledge_evidence(
        evidence_refs=["ke:kb-1:file-text:text_chunk:1:abc"],
        __request__=_FakeRequest(),
        __user__={"id": "user-1", "role": "user"},
        __metadata__={"files": []},
    )

    payload = json.loads(result)

    assert payload["ok"] is True
    assert payload["results"][0]["evidence_ref"] == "ke:kb-1:file-text:text_chunk:1:abc"
    assert payload["results"][0]["modality"] == "text"
    assert payload["results"][0]["content"] == "The capsid shell has a conserved HK97-like fold."
    assert payload["results"][0]["source"]["file_id"] == "file-text"
    assert payload["results"][0]["source"]["evidence_ref"] == "ke:kb-1:file-text:text_chunk:1:abc"
    assert payload["model_only_files"] == []


@pytest.mark.asyncio
async def test_query_knowledge_evidence_uses_search_adapter_then_hydrates_refs(monkeypatch):
    calls = []

    async def fake_get_active_vector_space(**kwargs):
        return _vector_space()

    async def fake_get_evidence_by_ref(ref, db=None):
        return _image_evidence(ref)

    async def fake_search(*, query, vector_spaces, user, request):
        calls.append(
            {
                "query_text": query.query_text,
                "query_image_refs": query.query_image_refs,
                "vector_spaces": [space.id for space in vector_spaces],
            }
        )
        return [{"evidence_ref": "ke:kb-1:file-img:standalone_image:1:def", "score": 0.91}]

    monkeypatch.setattr(evidence_mod.KnowledgeVectorSpaces, "get_active_vector_space", fake_get_active_vector_space)
    monkeypatch.setattr(evidence_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref)

    result = await query_knowledge_evidence(
        query_text="find similar capsid rings",
        query_image_refs=["chat:file:query-image"],
        knowledge_ids=["kb-1"],
        include_images=False,
        __request__=_FakeRequest(EVIDENCE_RETRIEVAL_SEARCH=fake_search),
        __user__={"id": "user-1", "role": "user"},
        __metadata__={"files": [{"id": "chat:file:query-image", "type": "image"}]},
    )

    payload = json.loads(result)

    assert payload["ok"] is True
    assert calls == [
        {
            "query_text": "find similar capsid rings",
            "query_image_refs": ["chat:file:query-image"],
            "vector_spaces": ["vs-1"],
        }
    ]
    assert payload["results"][0]["evidence_ref"] == "ke:kb-1:file-img:standalone_image:1:def"
    assert payload["results"][0]["score"] == 0.91
    assert payload["results"][0]["source"]["modality"] == "image"


@pytest.mark.asyncio
async def test_query_knowledge_evidence_fails_image_query_when_vector_space_does_not_support_it(monkeypatch):
    async def fake_get_active_vector_space(**kwargs):
        return _vector_space(supports_image_query=False)

    monkeypatch.setattr(evidence_mod.KnowledgeVectorSpaces, "get_active_vector_space", fake_get_active_vector_space)

    result = await query_knowledge_evidence(
        query_image_refs=["chat:file:query-image"],
        knowledge_ids=["kb-1"],
        __request__=_FakeRequest(EVIDENCE_RETRIEVAL_SEARCH=lambda **kwargs: []),
        __user__={"id": "user-1", "role": "user"},
        __metadata__={"files": [{"id": "chat:file:query-image", "type": "image"}]},
    )

    payload = json.loads(result)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "unsupported_image_query"


@pytest.mark.asyncio
async def test_query_knowledge_evidence_adds_budgeted_model_only_image_files(monkeypatch, tmp_path):
    image_path = tmp_path / "evidence.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")

    async def fake_get_evidence_by_ref(ref, db=None):
        return _image_evidence(ref)

    async def fake_get_asset_by_id(asset_id, db=None):
        return _image_asset(str(image_path))

    monkeypatch.setattr(evidence_mod.KnowledgeEvidences, "get_evidence_by_ref", fake_get_evidence_by_ref)
    monkeypatch.setattr(evidence_mod.KnowledgeEvidenceAssets, "get_asset_by_id", fake_get_asset_by_id)
    monkeypatch.setattr(evidence_mod.Storage, "get_file", lambda storage_uri: storage_uri)

    result = await query_knowledge_evidence(
        evidence_refs=["ke:kb-1:file-img:standalone_image:1:def"],
        include_images=True,
        __request__=_FakeRequest(),
        __user__={"id": "user-1", "role": "user"},
        __metadata__={"files": []},
    )

    payload = json.loads(result)

    assert payload["ok"] is True
    assert payload["results"][0]["content"] == "A microscopy panel with ring-like capsid particles."
    assert "data:image" not in json.dumps(payload["results"])
    assert payload["model_only_files"][0]["type"] == "image"
    assert payload["model_only_files"][0]["evidence_ref"] == "ke:kb-1:file-img:standalone_image:1:def"
    assert payload["model_only_files"][0]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(payload["model_only_files"][0]["url"].split(",", 1)[1]) == image_path.read_bytes()


@pytest.mark.asyncio
async def test_process_tool_result_moves_evidence_model_images_out_of_text(monkeypatch, tmp_path):
    image_path = tmp_path / "evidence.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")

    data_url = "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode()
    payload = {
        "ok": True,
        "query": {"evidence_refs": ["ke:img"]},
        "results": [{"evidence_ref": "ke:img", "modality": "image", "content": "caption"}],
        "model_only_files": [
            {
                "type": "image",
                "evidence_ref": "ke:img",
                "mime_type": "image/png",
                "url": data_url,
            }
        ],
    }

    text, files, embeds = await process_tool_result(
        _FakeRequest(),
        "query_knowledge_evidence",
        json.dumps(payload),
        "",
        False,
        {"chat_id": "chat-1"},
        {"id": "user-1", "role": "user"},
    )

    assert files == [{"type": "image", "url": data_url, "evidence_ref": "ke:img", "mime_type": "image/png"}]
    assert embeds == []
    assert "data:image" not in text
    compact = json.loads(text)
    assert compact["model_only_files"] == [
        {"type": "image", "evidence_ref": "ke:img", "mime_type": "image/png"}
    ]


def test_citation_sources_from_query_knowledge_evidence_preserve_typed_metadata():
    payload = {
        "ok": True,
        "results": [
            {
                "evidence_ref": "ke:kb-1:file-img:standalone_image:1:def",
                "modality": "image",
                "evidence_kind": "standalone_image",
                "content": "Ring-like capsid particles.",
                "source": {
                    "id": "ke:kb-1:file-img:standalone_image:1:def",
                    "file_id": "file-img",
                    "knowledge_id": "kb-1",
                    "name": "image.png",
                    "type": "evidence",
                    "evidence_ref": "ke:kb-1:file-img:standalone_image:1:def",
                    "modality": "image",
                    "evidence_kind": "standalone_image",
                    "preview_url": "/api/v1/knowledge/kb-1/evidence/ke:kb-1:file-img:standalone_image:1:def/thumbnail",
                    "content_url": "/api/v1/knowledge/kb-1/evidence/ke:kb-1:file-img:standalone_image:1:def/content",
                },
                "metadata": {
                    "evidence_ref": "ke:kb-1:file-img:standalone_image:1:def",
                    "file_id": "file-img",
                    "knowledge_id": "kb-1",
                    "source": "image.png",
                    "modality": "image",
                    "evidence_kind": "standalone_image",
                },
            }
        ],
        "model_only_files": [],
    }

    sources = get_citation_source_from_tool_result(
        "query_knowledge_evidence",
        {},
        json.dumps(payload),
        "tool-call-1",
    )

    assert sources == [
        {
            "source": payload["results"][0]["source"],
            "document": ["Ring-like capsid particles."],
            "metadata": [payload["results"][0]["metadata"]],
        }
    ]
