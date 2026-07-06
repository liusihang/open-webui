import io
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import open_webui.retrieval.loaders.office_image_assets as office_image_assets
import open_webui.retrieval.loaders.pdf_image_assets as pdf_image_assets
from PIL import Image


def _png_bytes(size: tuple[int, int] = (8, 6), color: str = 'red') -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size, color=color).save(buffer, format='PNG')
    return buffer.getvalue()


class FakePdfImage:
    def __init__(self, name: str, data: bytes, *, size: tuple[int, int] = (8, 6)) -> None:
        self.name = name
        self.data = data
        self.image = SimpleNamespace(width=size[0], height=size[1], format='PNG')


class FakePdfPage:
    def __init__(self, images: list[FakePdfImage]) -> None:
        self.images = images


class FakePdfReader:
    def __init__(self, pages: list[FakePdfPage]) -> None:
        self.pages = pages


def test_pdf_fallback_enforces_budgets_and_uploads_assets(tmp_path, monkeypatch):
    small = _png_bytes()
    too_large = small + (b'x' * 32)
    uploads: list[tuple[bytes, str, dict[str, str]]] = []

    monkeypatch.setattr(pdf_image_assets, 'DEFAULT_MAX_PDF_IMAGE_ASSET_PAGES', 1, raising=False)
    monkeypatch.setattr(pdf_image_assets, 'DEFAULT_MAX_PDF_IMAGE_ASSETS', 1, raising=False)
    monkeypatch.setattr(pdf_image_assets, 'DEFAULT_MAX_PDF_IMAGE_ASSET_BYTES', len(small), raising=False)
    monkeypatch.setattr(pdf_image_assets, 'DEFAULT_MAX_PDF_IMAGE_ASSET_TOTAL_BYTES', len(small), raising=False)
    monkeypatch.setattr(
        pdf_image_assets,
        'PdfReader',
        lambda _path: FakePdfReader(
            [
                FakePdfPage(
                    [
                        FakePdfImage('image1.png', small),
                        FakePdfImage('image2.png', too_large),
                        FakePdfImage('image3.png', small),
                    ]
                ),
                FakePdfPage([FakePdfImage('image4.png', small)]),
            ]
        ),
    )

    def fake_upload_file(file, filename, tags):
        contents = file.read()
        uploads.append((contents, filename, tags))
        return contents, f'storage://assets/{filename}'

    monkeypatch.setattr(pdf_image_assets, 'Storage', SimpleNamespace(upload_file=fake_upload_file), raising=False)

    result = pdf_image_assets.extract_pdf_image_assets(
        tmp_path / 'source.pdf',
        asset_root=tmp_path / 'pdf-assets',
        source_id='source doc',
        page_text_by_index={1: 'page one'},
    )

    assert len(result.assets) == 1
    assert len(uploads) == 1
    assert uploads[0] == (small, uploads[0][1], {'asset_kind': 'document_image', 'backend': 'pypdf'})
    assert uploads[0][1].startswith('pdf-image-assets/source-doc/')
    assert result.assets[0]['storage_uri'] == f'storage://assets/{uploads[0][1]}'
    assert result.assets[0]['local_path'] == result.assets[0]['storage_path']
    assert Path(result.assets[0]['storage_path']).is_file()
    assert {item['reason'] for item in result.skipped} == {
        'pdf_image_asset_too_large',
        'pdf_image_asset_limit_exceeded',
        'pdf_image_asset_page_limit_exceeded',
    }


class FakeZipEntry:
    def __init__(self, filename: str, file_size: int, *, is_dir: bool = False) -> None:
        self.filename = filename
        self.file_size = file_size
        self._is_dir = is_dir

    def is_dir(self) -> bool:
        return self._is_dir


class FakeZipArchive:
    def __init__(self, entries: list[FakeZipEntry], payloads: dict[str, bytes]) -> None:
        self.entries = entries
        self.payloads = payloads
        self.read_names: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def infolist(self) -> list[FakeZipEntry]:
        return self.entries

    def read(self, entry: FakeZipEntry) -> bytes:
        self.read_names.append(entry.filename)
        return self.payloads[entry.filename]


def test_office_zip_fallback_enforces_budgets_before_reading_and_uploads(tmp_path, monkeypatch):
    small = _png_bytes(color='blue')
    archive_ref: dict[str, FakeZipArchive] = {}
    entries = [
        FakeZipEntry('word/media/image1.png', len(small)),
        FakeZipEntry('word/media/image2.png', len(small)),
        FakeZipEntry('word/media/huge.png', len(small) + 1),
        FakeZipEntry('word/media/nested/too/deep/image3.png', len(small)),
    ]
    payloads = {entry.filename: small for entry in entries}
    uploads: list[tuple[bytes, str, dict[str, str]]] = []

    monkeypatch.setattr(office_image_assets, 'DEFAULT_MAX_OFFICE_ZIP_ENTRIES', 10, raising=False)
    monkeypatch.setattr(office_image_assets, 'DEFAULT_MAX_OFFICE_IMAGE_ASSET_DEPTH', 2, raising=False)
    monkeypatch.setattr(office_image_assets, 'DEFAULT_MAX_OFFICE_IMAGE_ASSET_BYTES', len(small), raising=False)
    monkeypatch.setattr(office_image_assets, 'DEFAULT_MAX_OFFICE_IMAGE_ASSET_TOTAL_BYTES', len(small), raising=False)
    monkeypatch.setattr(office_image_assets, 'DEFAULT_MAX_OFFICE_IMAGE_ASSETS', 1, raising=False)

    def fake_zip_file(_path):
        archive = FakeZipArchive(entries, payloads)
        archive_ref['archive'] = archive
        return archive

    def fake_upload_file(file, filename, tags):
        contents = file.read()
        uploads.append((contents, filename, tags))
        return contents, f's3://bucket/{filename}'

    monkeypatch.setattr(office_image_assets.zipfile, 'ZipFile', fake_zip_file)
    monkeypatch.setattr(office_image_assets, 'Storage', SimpleNamespace(upload_file=fake_upload_file), raising=False)

    result = office_image_assets.extract_office_image_assets(
        tmp_path / 'source.docx',
        asset_root=tmp_path / 'office-assets',
        source_id='source doc',
        document_text='document text',
    )

    assert len(result.assets) == 1
    assert len(uploads) == 1
    assert uploads[0] == (small, uploads[0][1], {'asset_kind': 'document_image', 'backend': 'office_zip'})
    assert uploads[0][1].startswith('office-image-assets/source-doc/')
    assert result.assets[0]['storage_uri'] == f's3://bucket/{uploads[0][1]}'
    assert result.assets[0]['local_path'] == result.assets[0]['storage_path']
    assert archive_ref['archive'].read_names == ['word/media/image1.png']
    assert {item['reason'] for item in result.skipped} == {
        'office_zip_image_asset_limit_exceeded',
        'office_zip_entry_too_large',
        'office_zip_entry_too_deep',
    }


def test_office_zip_entry_budget_stops_archive_scan_before_reading_later_entries(tmp_path, monkeypatch):
    small = _png_bytes(color='green')
    archive_ref: dict[str, FakeZipArchive] = {}
    entries = [
        FakeZipEntry('word/media/image1.png', len(small)),
        FakeZipEntry('word/media/image2.png', len(small)),
    ]
    payloads = {entry.filename: small for entry in entries}

    monkeypatch.setattr(office_image_assets, 'DEFAULT_MAX_OFFICE_ZIP_ENTRIES', 1, raising=False)
    monkeypatch.setattr(office_image_assets, 'DEFAULT_MAX_OFFICE_IMAGE_ASSET_DEPTH', 2, raising=False)
    monkeypatch.setattr(office_image_assets, 'DEFAULT_MAX_OFFICE_IMAGE_ASSET_BYTES', len(small), raising=False)
    monkeypatch.setattr(
        office_image_assets,
        'DEFAULT_MAX_OFFICE_IMAGE_ASSET_TOTAL_BYTES',
        len(small) * 2,
        raising=False,
    )
    monkeypatch.setattr(office_image_assets, 'DEFAULT_MAX_OFFICE_IMAGE_ASSETS', 2, raising=False)

    def fake_zip_file(_path):
        archive = FakeZipArchive(entries, payloads)
        archive_ref['archive'] = archive
        return archive

    monkeypatch.setattr(office_image_assets.zipfile, 'ZipFile', fake_zip_file)
    monkeypatch.setattr(
        office_image_assets,
        'Storage',
        SimpleNamespace(upload_file=lambda file, filename, tags: (file.read(), f'storage://{filename}')),
        raising=False,
    )

    result = office_image_assets.extract_office_image_assets(
        tmp_path / 'source.docx',
        asset_root=tmp_path / 'office-assets',
        source_id='source doc',
    )

    assert len(result.assets) == 1
    assert archive_ref['archive'].read_names == ['word/media/image1.png']
    assert result.skipped == [
        {
            'backend': 'office_zip',
            'origin_reference': 'word/media/image2.png',
            'reason': 'office_zip_entry_limit_exceeded',
        }
    ]
