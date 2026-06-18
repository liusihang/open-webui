from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

from open_webui.models.evidence import KnowledgeEvidenceAssetModel, KnowledgeEvidenceModel
from open_webui.retrieval import evidence as evidence_mod
from open_webui.routers import knowledge as knowledge_mod
from open_webui.tools.builtin import query_knowledge_evidence


class _FakeRequest:
    def __init__(self) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace()))


def _fake_user(user_id: str = 'user-1', role: str = 'user'):
    return SimpleNamespace(id=user_id, role=role)


def _fake_knowledge(knowledge_id: str = 'kb-1', user_id: str = 'user-1'):
    return SimpleNamespace(id=knowledge_id, user_id=user_id)


def _image_evidence(ref: str, *, asset_id: str) -> KnowledgeEvidenceModel:
    return KnowledgeEvidenceModel(
        id=f'ev-{asset_id}',
        evidence_ref=ref,
        knowledge_id='kb-1',
        file_id=f'file-{asset_id}',
        asset_id=asset_id,
        retrieval_chunk_uid=None,
        retrieval_chunk_row_id=None,
        modality='image',
        evidence_kind='document_image',
        title='Figure',
        content_text='Readable caption text remains available.',
        preview_text='Figure preview.',
        source_name='source.pdf',
        page_index=1,
        anchor_json=None,
        chunk_index=1,
        chunk_total=1,
        content_hash=f'hash-{asset_id}',
        projection_profile='multimodal_unified_v1',
        projection_config_hash='profile-hash',
        is_active=True,
        deleted_at=None,
        created_at=1,
        updated_at=1,
    )


def _image_asset(
    *,
    asset_id: str,
    storage_uri: str,
    mime_type: str = 'image/png',
    width: int = 64,
    height: int = 48,
) -> KnowledgeEvidenceAssetModel:
    return KnowledgeEvidenceAssetModel(
        id=asset_id,
        knowledge_id='kb-1',
        file_id=f'file-{asset_id}',
        asset_ref=f'asset:kb-1:file-{asset_id}:{asset_id}',
        asset_kind='document_image',
        mime_type=mime_type,
        storage_uri=storage_uri,
        sha256=f'sha-{asset_id}',
        width=width,
        height=height,
        page_index=1,
        bbox_json=None,
        anchor_json=None,
        caption='Figure caption',
        ocr_text=None,
        surrounding_text=None,
        status='ready',
        error=None,
        created_at=1,
        updated_at=1,
    )


@pytest.mark.asyncio
async def test_model_image_hydration_applies_per_image_and_total_byte_budgets(monkeypatch, tmp_path):
    big_path = tmp_path / 'big.png'
    small_a_path = tmp_path / 'small-a.png'
    small_b_path = tmp_path / 'small-b.png'
    big_path.write_bytes(b'123456789')
    small_a_path.write_bytes(b'123456')
    small_b_path.write_bytes(b'abcdef')

    refs = {
        'ke:kb-1:file-big:document_image:1:big': _image_evidence(
            'ke:kb-1:file-big:document_image:1:big', asset_id='big'
        ),
        'ke:kb-1:file-small-a:document_image:1:a': _image_evidence(
            'ke:kb-1:file-small-a:document_image:1:a', asset_id='small-a'
        ),
        'ke:kb-1:file-small-b:document_image:1:b': _image_evidence(
            'ke:kb-1:file-small-b:document_image:1:b', asset_id='small-b'
        ),
    }
    assets = {
        'big': _image_asset(asset_id='big', storage_uri=str(big_path)),
        'small-a': _image_asset(asset_id='small-a', storage_uri=str(small_a_path)),
        'small-b': _image_asset(asset_id='small-b', storage_uri=str(small_b_path)),
    }

    async def fake_get_evidence_by_ref(ref, db=None):
        return refs.get(ref)

    async def fake_get_asset_by_id(asset_id, db=None):
        return assets.get(asset_id)

    monkeypatch.setattr(evidence_mod.KnowledgeEvidences, 'get_evidence_by_ref', fake_get_evidence_by_ref)
    monkeypatch.setattr(evidence_mod.KnowledgeEvidenceAssets, 'get_asset_by_id', fake_get_asset_by_id)
    monkeypatch.setattr(evidence_mod.Storage, 'get_file', lambda storage_uri: storage_uri)
    monkeypatch.setattr(evidence_mod, '_MODEL_IMAGE_SINGLE_BYTE_BUDGET', 8, raising=False)
    monkeypatch.setattr(evidence_mod, '_MODEL_IMAGE_TOTAL_BYTE_BUDGET', 10, raising=False)

    result = await query_knowledge_evidence(
        evidence_refs=list(refs),
        knowledge_ids=['kb-1'],
        include_images=True,
        __request__=_FakeRequest(),
        __user__={'id': 'user-1', 'role': 'user'},
        __metadata__={
            'files': [],
            'effective_knowledge_scope': [{'id': 'kb-1', 'type': 'knowledge'}],
        },
    )

    payload = json.loads(result)

    assert payload['ok'] is True
    assert [item['content'] for item in payload['results']] == [
        'Readable caption text remains available.',
        'Readable caption text remains available.',
        'Readable caption text remains available.',
    ]
    assert [item['evidence_ref'] for item in payload['model_only_files']] == [
        'ke:kb-1:file-small-a:document_image:1:a'
    ]
    skipped = {
        item['evidence_ref']: item['metadata'].get('model_image')
        for item in payload['results']
        if item['metadata'].get('model_image', {}).get('code') == 'image_budget_exceeded'
    }
    assert set(skipped) == {
        'ke:kb-1:file-big:document_image:1:big',
        'ke:kb-1:file-small-b:document_image:1:b',
    }


@pytest.mark.asyncio
async def test_model_image_hydration_applies_pixel_budget_before_reading_bytes(monkeypatch, tmp_path):
    image_path = tmp_path / 'huge-pixels.png'
    image_path.write_bytes(b'tiny')
    evidence_ref = 'ke:kb-1:file-huge:document_image:1:huge'

    async def fake_get_evidence_by_ref(ref, db=None):
        return _image_evidence(ref, asset_id='huge')

    async def fake_get_asset_by_id(asset_id, db=None):
        return _image_asset(asset_id=asset_id, storage_uri=str(image_path), width=100_000, height=100_000)

    monkeypatch.setattr(evidence_mod.KnowledgeEvidences, 'get_evidence_by_ref', fake_get_evidence_by_ref)
    monkeypatch.setattr(evidence_mod.KnowledgeEvidenceAssets, 'get_asset_by_id', fake_get_asset_by_id)
    monkeypatch.setattr(evidence_mod.Storage, 'get_file', lambda storage_uri: storage_uri)
    monkeypatch.setattr(evidence_mod, '_MODEL_IMAGE_PIXEL_BUDGET', 1_000_000, raising=False)

    result = await query_knowledge_evidence(
        evidence_refs=[evidence_ref],
        knowledge_ids=['kb-1'],
        include_images=True,
        __request__=_FakeRequest(),
        __user__={'id': 'user-1', 'role': 'user'},
        __metadata__={
            'files': [],
            'effective_knowledge_scope': [{'id': 'kb-1', 'type': 'knowledge'}],
        },
    )

    payload = json.loads(result)

    assert payload['ok'] is True
    assert payload['results'][0]['content'] == 'Readable caption text remains available.'
    assert payload['model_only_files'] == []
    assert payload['results'][0]['metadata']['model_image']['code'] == 'image_budget_exceeded'
    assert payload['results'][0]['metadata']['model_image']['reason'] == 'pixel_budget'


async def _allow_evidence_access(monkeypatch, *, mime_type: str, storage_uri: str, endpoint: str):
    async def fake_get_knowledge_by_id(*args, **kwargs):
        return _fake_knowledge()

    async def fake_get_evidence_by_ref(*args, **kwargs):
        return SimpleNamespace(
            **_image_evidence('ke:kb-1:file-img:document_image:1:abc', asset_id='asset-1').model_dump()
        )

    async def fake_get_asset_by_id(*args, **kwargs):
        return _image_asset(asset_id='asset-1', storage_uri=storage_uri, mime_type=mime_type)

    async def fake_list_variants(*args, **kwargs):
        return []

    async def fake_has_access(*args, **kwargs):
        return True

    monkeypatch.setattr(knowledge_mod.Knowledges, 'get_knowledge_by_id', fake_get_knowledge_by_id, raising=False)
    monkeypatch.setattr(
        knowledge_mod.KnowledgeEvidences,
        'get_evidence_by_ref',
        fake_get_evidence_by_ref,
        raising=False,
    )
    monkeypatch.setattr(knowledge_mod.KnowledgeEvidenceAssets, 'get_asset_by_id', fake_get_asset_by_id, raising=False)
    monkeypatch.setattr(
        knowledge_mod.KnowledgeEvidenceAssetVariants,
        'list_variants',
        fake_list_variants,
        raising=False,
    )
    monkeypatch.setattr(knowledge_mod.AccessGrants, 'has_access', fake_has_access, raising=False)
    monkeypatch.setattr(knowledge_mod.Storage, 'get_file', lambda path: path, raising=False)

    if endpoint == 'content':
        return await knowledge_mod.get_knowledge_evidence_content_by_ref(
            id='kb-1',
            evidence_ref='ke:kb-1:file-img:document_image:1:abc',
            user=_fake_user(),
            db=None,
        )
    return await knowledge_mod.get_knowledge_evidence_thumbnail_by_ref(
        id='kb-1',
        evidence_ref='ke:kb-1:file-img:document_image:1:abc',
        user=_fake_user(),
        db=None,
    )


@pytest.mark.asyncio
async def test_evidence_content_file_response_sets_nosniff_for_safe_inline_image(monkeypatch, tmp_path):
    image_path = tmp_path / 'safe.png'
    image_path.write_bytes(b'image-bytes')

    response = await _allow_evidence_access(
        monkeypatch,
        mime_type='image/png',
        storage_uri=str(image_path),
        endpoint='content',
    )

    assert response.media_type == 'image/png'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'content-disposition' not in response.headers


@pytest.mark.asyncio
async def test_evidence_thumbnail_rejects_non_safe_inline_mime(monkeypatch, tmp_path):
    html_path = tmp_path / 'unsafe.html'
    html_path.write_text('<script>alert(1)</script>')

    with pytest.raises(knowledge_mod.HTTPException) as exc:
        await _allow_evidence_access(
            monkeypatch,
            mime_type='text/html',
            storage_uri=str(html_path),
            endpoint='thumbnail',
        )

    assert exc.value.status_code == 400
