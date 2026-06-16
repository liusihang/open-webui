import os
from pathlib import Path

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import open_webui.retrieval.loaders.main as loader_main
from fpdf import FPDF
from open_webui.retrieval.loaders.main import Loader
from PIL import Image


def _write_pdf_with_embedded_image(tmp_path: Path) -> Path:
    image_path = tmp_path / 'embedded.png'
    Image.new('RGB', (20, 10), color=(255, 0, 0)).save(image_path)

    pdf_path = tmp_path / 'with-image.pdf'
    pdf = FPDF(unit='pt', format=(300, 300))
    pdf.add_page()
    pdf.set_font('Helvetica', size=12)
    pdf.text(30, 30, 'Visible PDF text')
    pdf.image(str(image_path), x=30, y=50, w=40, h=20)
    pdf.output(str(pdf_path))
    return pdf_path


def test_pdf_loader_materializes_embedded_images_as_document_image_assets(tmp_path):
    pdf_path = _write_pdf_with_embedded_image(tmp_path)
    asset_root = tmp_path / 'pdf-assets'

    docs = Loader(
        PDF_EXTRACT_IMAGES=True,
        PDF_LOADER_MODE='page',
        PDF_IMAGE_ASSET_ROOT=str(asset_root),
    ).load('with-image.pdf', 'application/pdf', str(pdf_path))

    assert len(docs) == 1
    assert 'Visible PDF text' in docs[0].page_content

    assets = docs[0].metadata.get('document_image_assets')
    assert isinstance(assets, list)
    assert len(assets) == 1

    asset = assets[0]
    assert Path(asset['storage_path']).is_file()
    assert asset['asset_kind'] == 'document_image'
    assert asset['image_fingerprint'].startswith('sha256:')
    assert asset['page_index'] == 1
    assert asset['anchor'] == {'page': 1, 'block_id': 'page-001-image-001'}
    assert asset['mime_type'] == 'image/png'
    assert asset['width'] == 20
    assert asset['height'] == 10
    assert asset['caption'] == 'Visible PDF text'
    assert asset['surrounding_text'] == 'Visible PDF text'
    assert asset['metadata']['backend'] == 'pypdf'
    assert asset['metadata']['origin_reference'] == 'I1.png'


def test_pdf_loader_records_image_asset_errors_without_breaking_text_load(tmp_path, monkeypatch):
    pdf_path = _write_pdf_with_embedded_image(tmp_path)

    def fail_extraction(*args, **kwargs):
        raise RuntimeError('extractor exploded')

    monkeypatch.setattr(loader_main, 'extract_pdf_image_assets', fail_extraction)

    docs = Loader(PDF_EXTRACT_IMAGES=True, PDF_LOADER_MODE='page').load(
        'with-image.pdf',
        'application/pdf',
        str(pdf_path),
    )

    assert len(docs) == 1
    assert 'Visible PDF text' in docs[0].page_content
    assert 'document_image_assets' not in docs[0].metadata
    assert docs[0].metadata['document_image_assets_skipped'] == [
        {
            'backend': 'pypdf',
            'reason': 'pdf_image_asset_extraction_error',
            'error': 'RuntimeError',
            'message': 'extractor exploded',
        }
    ]
