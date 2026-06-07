import os

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import open_webui.retrieval.document_image_assets as document_image_assets
from open_webui.retrieval.document_image_assets import (
    ImageAssetMaterializer,
    build_image_assets_from_markdown,
)
from PIL import Image


def test_document_image_assets_does_not_expose_pdf_page_snapshot_renderer():
    assert not hasattr(document_image_assets, 'render_pdf_page_snapshots')


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
