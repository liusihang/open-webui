import os
import zipfile
from pathlib import Path

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

from langchain_core.documents import Document
from open_webui.retrieval.loaders.main import Loader
from PIL import Image


class FakeTextLoader:
    def __init__(self, text: str = 'Office document text') -> None:
        self.text = text

    def load(self) -> list[Document]:
        return [Document(page_content=self.text, metadata={'source': 'fake-office'})]


def _image_bytes(tmp_path: Path, *, filename: str, size: tuple[int, int], color: str) -> bytes:
    image_path = tmp_path / filename
    Image.new('RGB', size, color=color).save(image_path)
    return image_path.read_bytes()


def _write_zip_document(tmp_path: Path, filename: str, entries: dict[str, bytes]) -> Path:
    document_path = tmp_path / filename
    with zipfile.ZipFile(document_path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types />')
        for entry_name, entry_bytes in entries.items():
            archive.writestr(entry_name, entry_bytes)
    return document_path


def test_loader_materializes_docx_embedded_images_as_document_image_assets(tmp_path, monkeypatch):
    image_bytes = _image_bytes(tmp_path, filename='embedded.png', size=(24, 12), color='red')
    docx_path = _write_zip_document(
        tmp_path,
        'with-image.docx',
        {
            'word/media/image1.png': image_bytes,
            'word/media/not-image.txt': b'not an image',
        },
    )
    asset_root = tmp_path / 'office-assets'

    monkeypatch.setattr(Loader, '_get_loader', lambda *args, **kwargs: FakeTextLoader())

    docs = Loader(OFFICE_IMAGE_ASSET_ROOT=str(asset_root)).load(
        'with-image.docx',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        str(docx_path),
    )

    assert len(docs) == 1
    assert docs[0].page_content == 'Office document text'

    assets = docs[0].metadata.get('document_image_assets')
    assert isinstance(assets, list)
    assert len(assets) == 1

    asset = assets[0]
    assert Path(asset['storage_path']).is_file()
    assert Path(asset['storage_path']).read_bytes() == image_bytes
    assert asset['asset_kind'] == 'document_image'
    assert asset['image_fingerprint'].startswith('sha256:')
    assert asset['page_index'] == 1
    assert asset['anchor'] == {'page': 1, 'block_id': 'page-001-image-001'}
    assert asset['mime_type'] == 'image/png'
    assert asset['width'] == 24
    assert asset['height'] == 12
    assert asset['caption'] is None
    assert asset['surrounding_text'] is None
    assert asset['metadata']['backend'] == 'office_zip'
    assert asset['metadata']['origin_reference'] == 'word/media/image1.png'
    assert asset['metadata']['extractor'] == 'zipfile'


def test_loader_materializes_ooxml_and_opendocument_picture_entries(tmp_path, monkeypatch):
    ppt_image = _image_bytes(tmp_path, filename='slide.png', size=(30, 18), color='blue')
    xlsx_image = _image_bytes(tmp_path, filename='sheet.png', size=(16, 14), color='green')
    odp_image = _image_bytes(tmp_path, filename='odp.png', size=(18, 16), color='purple')
    cases = [
        (
            'slides.pptx',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'ppt/media/image1.png',
            ppt_image,
        ),
        (
            'sheet.xlsx',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'xl/media/image1.png',
            xlsx_image,
        ),
        (
            'deck.odp',
            'application/vnd.oasis.opendocument.presentation',
            'Pictures/100000000000012C000000B4.png',
            odp_image,
        ),
    ]

    monkeypatch.setattr(Loader, '_get_loader', lambda *args, **kwargs: FakeTextLoader())

    for filename, content_type, entry_name, image_bytes in cases:
        document_path = _write_zip_document(tmp_path, filename, {entry_name: image_bytes})

        docs = Loader(OFFICE_IMAGE_ASSET_ROOT=str(tmp_path / f'{Path(filename).stem}-assets')).load(
            filename,
            content_type,
            str(document_path),
        )

        assets = docs[0].metadata.get('document_image_assets')
        assert isinstance(assets, list)
        assert len(assets) == 1
        assert Path(assets[0]['storage_path']).read_bytes() == image_bytes
        assert assets[0]['metadata']['backend'] == 'office_zip'
        assert assets[0]['metadata']['origin_reference'] == entry_name


def test_loader_records_office_image_asset_errors_without_breaking_text_load(tmp_path, monkeypatch):
    document_path = tmp_path / 'not-a-real-docx.docx'
    document_path.write_bytes(b'this is not a zip')

    monkeypatch.setattr(Loader, '_get_loader', lambda *args, **kwargs: FakeTextLoader())

    docs = Loader().load(
        'not-a-real-docx.docx',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        str(document_path),
    )

    assert len(docs) == 1
    assert docs[0].page_content == 'Office document text'
    assert 'document_image_assets' not in docs[0].metadata
    assert docs[0].metadata['document_image_assets_skipped'] == [
        {
            'backend': 'office_zip',
            'reason': 'office_zip_read_error',
            'error': 'BadZipFile',
            'message': 'File is not a zip file',
        }
    ]
