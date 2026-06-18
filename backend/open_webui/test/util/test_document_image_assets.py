import os

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import open_webui.retrieval.document_image_assets as document_image_assets
from open_webui.retrieval.document_image_assets import (
    ImageAssetMaterializer,
    build_image_assets_from_markdown,
)
from PIL import Image


class FakeDownloadResponse:
    def __init__(self, *, payload=b'', status=200, headers=None):
        self._payload = payload
        self.status = status
        self.headers = headers or {}
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_document_image_assets_does_not_expose_pdf_page_snapshot_renderer():
    assert not hasattr(document_image_assets, 'render_pdf_page_snapshots')


def test_build_image_assets_from_markdown_uses_materializer_and_page_text(tmp_path):
    source_dir = tmp_path / 'docs'
    source_dir.mkdir()
    source_pdf = source_dir / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    image_dir = source_dir / 'images'
    image_dir.mkdir()
    source_image = image_dir / 'box-a.png'
    Image.new('RGB', (16, 12), color='white').save(source_image)

    assets, skipped = build_image_assets_from_markdown(
        source_path=source_pdf,
        source_id='source',
        markdown={
            'text': '样品应放入 Box A。\n图 1 Box A',
            'images': {'images/box-a.png': None},
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


def test_build_image_assets_from_markdown_rejects_absolute_origin_paths(tmp_path):
    source_dir = tmp_path / 'docs'
    source_dir.mkdir()
    source_pdf = source_dir / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    outside_dir = tmp_path / 'outside'
    outside_dir.mkdir()
    outside_image = outside_dir / 'secret.png'
    Image.new('RGB', (8, 8), color='black').save(outside_image)

    assets, skipped = build_image_assets_from_markdown(
        source_path=source_pdf,
        source_id='source',
        markdown={
            'text': 'leaked image',
            'images': {'secret.png': str(outside_image)},
        },
        page_no=1,
    )

    assert assets == []
    assert skipped == [
        {
            'reference': 'secret.png',
            'origin_uri': str(outside_image),
            'reason': 'storage_path_unavailable',
        }
    ]


def test_build_image_assets_from_markdown_rejects_image_asset_root_symlink_escape(tmp_path):
    source_dir = tmp_path / 'docs'
    source_dir.mkdir()
    source_pdf = source_dir / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    image_root = tmp_path / 'allowed-images'
    image_root.mkdir()
    outside_dir = tmp_path / 'outside'
    outside_dir.mkdir()
    outside_image = outside_dir / 'escaped.png'
    Image.new('RGB', (8, 8), color='black').save(outside_image)
    (image_root / 'escaped.png').symlink_to(outside_image)

    assets, skipped = build_image_assets_from_markdown(
        source_path=source_pdf,
        source_id='source',
        markdown={
            'text': 'escaped image',
            'images': {'escaped.png': None},
        },
        page_no=1,
        image_asset_roots=[image_root],
    )

    assert assets == []
    assert skipped == [
        {
            'reference': 'escaped.png',
            'origin_uri': '',
            'reason': 'storage_path_unavailable',
        }
    ]


def test_image_asset_materializer_blocks_redirect_to_loopback(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    calls = []

    def fake_urlopen(url, timeout=0):
        calls.append(url)
        return FakeDownloadResponse(
            status=302,
            headers={'Location': 'http://127.0.0.1/private.png'},
        )

    monkeypatch.setattr(document_image_assets, '_resolve_host_ips', lambda host, port: ['93.184.216.34'], raising=False)
    monkeypatch.setattr(document_image_assets.urlrequest, 'urlopen', fake_urlopen)
    monkeypatch.setattr(document_image_assets, '_urlopen_no_redirect', fake_urlopen, raising=False)

    materializer = ImageAssetMaterializer(tmp_path / 'assets')
    materializer._allowed_remote_origins = ('https://cdn.example',)

    assets, skipped = build_image_assets_from_markdown(
        source_path=source_pdf,
        source_id='source',
        markdown={
            'text': 'remote image',
            'images': ['https://cdn.example/image.png'],
        },
        page_no=1,
        materializer=materializer,
    )

    assert assets == []
    assert calls == ['https://cdn.example/image.png']
    assert skipped == [
        {
            'reference': 'https://cdn.example/image.png',
            'origin_uri': 'https://cdn.example/image.png',
            'reason': 'storage_path_unavailable',
        }
    ]


def test_image_asset_materializer_rejects_oversized_content_length(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')

    def fake_urlopen(url, timeout=0):
        return FakeDownloadResponse(
            status=200,
            headers={'Content-Length': str(25 * 1024 * 1024)},
        )

    monkeypatch.setattr(document_image_assets, '_resolve_host_ips', lambda host, port: ['93.184.216.34'], raising=False)
    monkeypatch.setattr(document_image_assets.urlrequest, 'urlopen', fake_urlopen)
    monkeypatch.setattr(document_image_assets, '_urlopen_no_redirect', fake_urlopen, raising=False)

    materializer = ImageAssetMaterializer(tmp_path / 'assets')
    materializer._allowed_remote_origins = ('https://cdn.example',)

    assets, skipped = build_image_assets_from_markdown(
        source_path=source_pdf,
        source_id='source',
        markdown={
            'text': 'remote image',
            'images': ['https://cdn.example/large.png'],
        },
        page_no=1,
        materializer=materializer,
    )

    assert assets == []
    assert skipped == [
        {
            'reference': 'https://cdn.example/large.png',
            'origin_uri': 'https://cdn.example/large.png',
            'reason': 'storage_path_unavailable',
        }
    ]
