import os
import sys
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

from open_webui.retrieval.document_image_assets import (
    ImageAssetMaterializer,
    build_image_assets_from_markdown,
    render_pdf_page_snapshots,
)
from PIL import Image


def test_build_image_assets_from_markdown_uses_materializer_and_page_text(tmp_path):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    source_image = tmp_path / 'box-a.png'
    Image.new('RGB', (16, 12), color='white').save(source_image)

    assets, skipped = build_image_assets_from_markdown(
        source_path=source_pdf,
        source_id='source',
        markdown={
            'text': '样品应放入 Box A。\n图 1 Box A',
            'images': {'images/box-a.png': str(source_image)},
        },
        page_no=1,
        materializer=ImageAssetMaterializer(tmp_path / 'assets'),
    )

    assert skipped == []
    assert len(assets) == 1
    assert assets[0]['asset_kind'] == 'document_image'
    assert assets[0]['page_index'] == 1
    assert assets[0]['caption'] == '样品应放入 Box A。'
    assert assets[0]['surrounding_text'] == '样品应放入 Box A。 图 1 Box A'
    assert assets[0]['metadata']['width'] == 16
    assert assets[0]['metadata']['height'] == 12
    assert assets[0]['metadata']['origin_reference'] == 'images/box-a.png'


def test_render_pdf_page_snapshots_uses_pdfium_when_available(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')

    class FakeBitmap:
        def to_pil(self):
            return Image.new('RGB', (20, 30), color='white')

    class FakePage:
        def render(self, scale):
            assert scale == 1.0
            return FakeBitmap()

        def close(self):
            pass

    class FakePdf:
        def __init__(self, path):
            assert path == str(source_pdf)

        def __len__(self):
            return 2

        def __getitem__(self, index):
            assert index == 0
            return FakePage()

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, 'pypdfium2', SimpleNamespace(PdfDocument=FakePdf))

    assets = render_pdf_page_snapshots(
        source_path=source_pdf,
        source_id='source',
        asset_root=tmp_path / 'snapshots',
        pages=[1],
        scale=1.0,
    )

    assert len(assets) == 1
    assert assets[0]['asset_kind'] == 'page_snapshot'
    assert assets[0]['page_index'] == 1
    assert assets[0]['anchor'] == {'page': 1, 'block_id': 'page-001-snapshot'}
    assert assets[0]['metadata']['width'] == 20
    assert assets[0]['metadata']['height'] == 30
    assert os.path.exists(assets[0]['storage_path'])


def test_render_pdf_page_snapshots_uses_pymupdf_when_pdfium_unavailable(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')

    class FakePixmap:
        width = 22
        height = 33

        def save(self, path):
            Image.new('RGB', (self.width, self.height), color='white').save(path)

    class FakePage:
        def get_pixmap(self, matrix, alpha):
            assert matrix.scale == (1.0, 1.0)
            assert alpha is False
            return FakePixmap()

    class FakeDoc:
        page_count = 2

        def __init__(self, path):
            assert path == str(source_pdf)

        def load_page(self, index):
            assert index == 0
            return FakePage()

        def close(self):
            pass

    class FakeMatrix:
        def __init__(self, x, y):
            self.scale = (x, y)

    monkeypatch.delitem(sys.modules, 'pypdfium2', raising=False)
    monkeypatch.setattr('builtins.__import__', __import__)
    monkeypatch.setitem(
        sys.modules,
        'fitz',
        SimpleNamespace(open=FakeDoc, Matrix=FakeMatrix),
    )

    assets = render_pdf_page_snapshots(
        source_path=source_pdf,
        source_id='source',
        asset_root=tmp_path / 'snapshots',
        pages=[1],
        scale=1.0,
    )

    assert len(assets) == 1
    assert assets[0]['asset_kind'] == 'page_snapshot'
    assert assets[0]['metadata']['width'] == 22
    assert assets[0]['metadata']['height'] == 33
    assert os.path.exists(assets[0]['storage_path'])
