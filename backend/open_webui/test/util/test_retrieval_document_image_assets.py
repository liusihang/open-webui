import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import open_webui.routers.retrieval as retrieval_mod
from langchain_core.documents import Document


def test_collect_document_image_assets_from_docs_flattens_loader_metadata():
    assert hasattr(retrieval_mod, '_collect_document_image_assets_from_docs')

    docs = [
        Document(
            page_content='page one',
            metadata={
                'document_image_assets': [
                    {'storage_path': '/tmp/page-001-image-001.png', 'page_index': 1},
                ],
            },
        ),
        Document(
            page_content='page two',
            metadata={
                'document_image_assets': [
                    {'storage_path': '/tmp/page-002-image-001.png', 'page_index': 2},
                    'not-a-dict',
                ],
            },
        ),
        Document(page_content='page three', metadata={'document_image_assets': {'bad': 'shape'}}),
    ]

    assert retrieval_mod._collect_document_image_assets_from_docs(docs) == [
        {'storage_path': '/tmp/page-001-image-001.png', 'page_index': 1},
        {'storage_path': '/tmp/page-002-image-001.png', 'page_index': 2},
    ]


@pytest.mark.asyncio
async def test_collect_pdf_page_snapshots_is_disabled_for_legacy_knowledge(monkeypatch, tmp_path):
    calls = []

    async def fake_get_knowledge_by_id(id, db=None):
        return SimpleNamespace(meta={'evidence_mode': 'legacy_text'})

    def fake_render(**kwargs):
        calls.append(kwargs)
        return [{'asset_kind': 'page_snapshot'}]

    monkeypatch.setattr(retrieval_mod.Knowledges, 'get_knowledge_by_id', fake_get_knowledge_by_id)
    monkeypatch.setattr(retrieval_mod, 'render_pdf_page_snapshots', fake_render)

    assets = await retrieval_mod._collect_evidence_pdf_page_snapshot_assets(
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(
            ENABLE_MULTIMODAL_KNOWLEDGE_EVIDENCE=True,
        )))),
        file=SimpleNamespace(
            id='file-1',
            filename='manual.pdf',
            data={},
            meta={'content_type': 'application/pdf'},
        ),
        collection_name='kb-legacy',
        local_file_path=tmp_path / 'manual.pdf',
        db=None,
    )

    assert assets == []
    assert calls == []


@pytest.mark.asyncio
async def test_collect_pdf_page_snapshots_appends_for_evidence_knowledge(monkeypatch, tmp_path):
    source_pdf = tmp_path / 'manual.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')

    async def fake_get_knowledge_by_id(id, db=None):
        return SimpleNamespace(meta={'evidence_mode': 'evidence_primary'})

    def fake_render(**kwargs):
        assert kwargs['source_path'] == source_pdf
        assert kwargs['source_id'] == 'file-1'
        assert Path(kwargs['asset_root']).name == 'document-image-assets'
        return [
            {
                'asset_kind': 'page_snapshot',
                'storage_path': str(tmp_path / 'page-001-snapshot.png'),
                'page_index': 1,
            }
        ]

    monkeypatch.setattr(retrieval_mod.Knowledges, 'get_knowledge_by_id', fake_get_knowledge_by_id)
    monkeypatch.setattr(retrieval_mod, 'render_pdf_page_snapshots', fake_render)

    assets = await retrieval_mod._collect_evidence_pdf_page_snapshot_assets(
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(
            ENABLE_MULTIMODAL_KNOWLEDGE_EVIDENCE=True,
        )))),
        file=SimpleNamespace(
            id='file-1',
            filename='manual.pdf',
            data={'document_image_assets': [{'asset_kind': 'document_image', 'page_index': 1}]},
            meta={'content_type': 'application/pdf'},
        ),
        collection_name='kb-evidence',
        local_file_path=source_pdf,
        db=None,
    )

    assert assets == [
        {'asset_kind': 'document_image', 'page_index': 1},
        {
            'asset_kind': 'page_snapshot',
            'storage_path': str(tmp_path / 'page-001-snapshot.png'),
            'page_index': 1,
        },
    ]
