import os

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


def test_retrieval_router_does_not_expose_pdf_page_snapshot_ingestion_path():
    assert not hasattr(retrieval_mod, 'render_pdf_page_snapshots')
    assert not hasattr(retrieval_mod, '_collect_evidence_pdf_page_snapshot_assets')
