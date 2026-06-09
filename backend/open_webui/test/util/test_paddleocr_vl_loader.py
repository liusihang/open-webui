import json
import os
from pathlib import Path

import pytest
import requests
from PIL import Image

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

from open_webui.retrieval import document_image_assets
from open_webui.retrieval.loaders import paddleocr_vl
from open_webui.retrieval.loaders.paddleocr_vl import PaddleOCRVLLoader


class FakeResponse:
    def __init__(self, *, json_data=None, lines=None, status_code=200, text='OK'):
        self._json_data = json_data
        self._lines = lines or []
        self.status_code = status_code
        self.text = text
        self.content = text.encode('utf-8')

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f'{self.status_code} error')
            error.response = self
            raise error

    def json(self):
        if self._json_data is None:
            raise ValueError('no json payload')
        return self._json_data

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line if decode_unicode else line.encode('utf-8')


class FakeDownloadResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.status = 200
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def _png_bytes(tmp_path: Path, name: str) -> bytes:
    image_path = tmp_path / name
    Image.new('RGB', (16, 12), color='white').save(image_path)
    return image_path.read_bytes()


def test_paddleocr_loader_submits_async_job_polls_jsonl_and_downloads_assets(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    png_payload = _png_bytes(tmp_path, 'sample.png')
    uploads_dir = tmp_path / 'uploads'
    image_payloads = {
        'https://cdn.test/markdown-image.png': png_payload,
        'https://cdn.test/output-image.png': png_payload,
        'https://cdn.test/output-only.png': png_payload,
    }
    poll_url = 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs/job-123'
    jsonl_url = 'https://cdn.test/result.jsonl'

    def fake_post(url, data=None, files=None, headers=None, timeout=None):
        assert url == 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs'
        assert headers == {'Authorization': 'bearer secret-token'}
        assert data['model'] == 'PaddleOCR-VL-1.6'
        assert files['file'][0] == 'source.pdf'
        assert timeout == 30
        return FakeResponse(json_data={'jobId': 'job-123'})

    poll_calls = []

    def fake_get(url, headers=None, timeout=None, stream=False):
        assert headers == {'Authorization': 'bearer secret-token'}
        if url == poll_url:
            poll_calls.append(url)
            if len(poll_calls) == 1:
                return FakeResponse(json_data={'state': 'running'})
            return FakeResponse(
                json_data={
                    'state': 'done',
                    'resultUrl': {'jsonUrl': jsonl_url},
                }
            )
        if url == jsonl_url:
            assert stream is True
            assert timeout == 60
            return FakeResponse(
                lines=[
                    json.dumps(
                        {
                            'result': {
                                'layoutParsingResults': [
                                    {
                                        'pageNo': 2,
                                        'markdown': {
                                            'text': 'Page two text.\nFigure 1 markdown image.',
                                            'images': {
                                                'markdown-image.png': 'https://cdn.test/markdown-image.png'
                                            },
                                        },
                                        'outputImages': ['https://cdn.test/output-image.png'],
                                    }
                                ]
                            }
                        }
                    ),
                    json.dumps(
                        {
                            'result': {
                                'layoutParsingResults': [
                                    {
                                        'pageNo': 3,
                                        'markdown': {'text': '', 'images': {}},
                                        'outputImages': ['https://cdn.test/output-only.png'],
                                    }
                                ]
                            }
                        }
                    ),
                ]
            )
        raise AssertionError(f'unexpected GET {url}')

    def fake_urlopen(url, timeout=0):
        assert timeout == 30.0
        return FakeDownloadResponse(image_payloads[url])

    monkeypatch.setattr(paddleocr_vl.requests, 'post', fake_post)
    monkeypatch.setattr(paddleocr_vl.requests, 'get', fake_get)
    monkeypatch.setattr(document_image_assets.urlrequest, 'urlopen', fake_urlopen)
    monkeypatch.setattr(paddleocr_vl.time, 'sleep', lambda _: None)
    monkeypatch.setattr(paddleocr_vl, 'UPLOAD_DIR', uploads_dir, raising=False)

    docs = PaddleOCRVLLoader(
        api_url='https://paddleocr.aistudio-app.com',
        token='secret-token',
        file_path=str(source_pdf),
    ).load()

    assert len(docs) == 2
    assert poll_calls == [poll_url, poll_url]

    assert docs[0].page_content == 'Page two text.\nFigure 1 markdown image.'
    assert docs[0].metadata['page'] == 1
    assert docs[0].metadata['page_label'] == 2
    assert docs[0].metadata['total_pages'] == 2
    assets = docs[0].metadata['document_image_assets']
    assert len(assets) == 2
    assert {asset['metadata']['origin_reference'] for asset in assets} == {
        'markdown-image.png',
        'https://cdn.test/output-image.png',
    }
    for asset in assets:
        assert asset['asset_kind'] == 'document_image'
        assert asset['page_index'] == 2
        assert asset['storage_path'].startswith(
            str((uploads_dir / 'paddleocr-vl-image-assets' / 'source').resolve())
        )

    assert docs[1].page_content == ''
    assert docs[1].metadata['_metadata_only'] is True
    assert docs[1].metadata['page_label'] == 3
    assert docs[1].metadata['document_image_assets'][0]['metadata']['origin_reference'] == (
        'https://cdn.test/output-only.png'
    )


def test_paddleocr_loader_accepts_jobs_endpoint_base_url_and_raises_for_failed_job(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    poll_urls = []

    def fake_post(url, data=None, files=None, headers=None, timeout=None):
        assert url == 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs'
        return FakeResponse(json_data={'data': {'jobId': 'job-999'}})

    def fake_get(url, headers=None, timeout=None, stream=False):
        poll_urls.append(url)
        return FakeResponse(
            json_data={
                'data': {
                    'state': 'failed',
                    'error': {'message': 'quota exceeded'},
                },
            }
        )

    monkeypatch.setattr(paddleocr_vl.requests, 'post', fake_post)
    monkeypatch.setattr(paddleocr_vl.requests, 'get', fake_get)
    monkeypatch.setattr(paddleocr_vl.time, 'sleep', lambda _: None)

    with pytest.raises(RuntimeError, match='quota exceeded'):
        PaddleOCRVLLoader(
            api_url='https://paddleocr.aistudio-app.com/api/v2/ocr/jobs',
            token='secret-token',
            file_path=str(source_pdf),
        ).load()

    assert poll_urls == ['https://paddleocr.aistudio-app.com/api/v2/ocr/jobs/job-999']


def test_paddleocr_loader_raises_timeout_when_job_never_finishes(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    monotonic_values = iter([0.0, 1.0, 6.5])

    monkeypatch.setattr(
        paddleocr_vl.requests,
        'post',
        lambda *args, **kwargs: FakeResponse(json_data={'jobId': 'job-timeout'}),
    )
    monkeypatch.setattr(
        paddleocr_vl.requests,
        'get',
        lambda *args, **kwargs: FakeResponse(json_data={'state': 'running'}),
    )
    monkeypatch.setattr(paddleocr_vl.time, 'sleep', lambda _: None)
    monkeypatch.setattr(paddleocr_vl.time, 'monotonic', lambda: next(monotonic_values))

    with pytest.raises(TimeoutError, match='job-timeout'):
        PaddleOCRVLLoader(
            api_url='https://paddleocr.aistudio-app.com',
            token='secret-token',
            file_path=str(source_pdf),
            poll_timeout_s=5,
            poll_interval_s=0,
        ).load()


def test_paddleocr_loader_raises_clear_submit_http_error_without_leaking_token(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')

    def fake_post(*args, **kwargs):
        return FakeResponse(status_code=401, text='unauthorized')

    monkeypatch.setattr(paddleocr_vl.requests, 'post', fake_post)

    with pytest.raises(RuntimeError) as exc_info:
        PaddleOCRVLLoader(
            api_url='https://paddleocr.aistudio-app.com',
            token='secret-token',
            file_path=str(source_pdf),
        ).load()

    message = str(exc_info.value)
    assert 'submit' in message
    assert '401' in message
    assert 'secret-token' not in message
