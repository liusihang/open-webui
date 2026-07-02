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
from open_webui.retrieval.loaders.main import Loader
from open_webui.retrieval.loaders.paddleocr_vl import PaddleOCRVLLoader
from open_webui.retrieval.utils import build_loader_from_config


class FakeResponse:
    def __init__(self, *, json_data=None, lines=None, status_code=200, text='OK', headers=None):
        self._json_data = json_data
        self._lines = lines or []
        self.status_code = status_code
        self.text = text
        self.content = text.encode('utf-8')
        self.headers = headers or {}

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
            if decode_unicode:
                yield line.decode('utf-8') if isinstance(line, bytes) else line
            else:
                yield line if isinstance(line, bytes) else line.encode('utf-8')

    def iter_content(self, chunk_size=1):
        for line in self._lines:
            payload = line if isinstance(line, bytes) else line.encode('utf-8')
            payload += b'\n'
            for offset in range(0, len(payload), chunk_size):
                yield payload[offset : offset + chunk_size]


class FakeDownloadResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.status = 200
        self.headers = {}
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

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


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
        'https://paddleocr.aistudio-app.com/markdown-image.png': png_payload,
        'https://paddleocr.aistudio-app.com/output-image.png': png_payload,
        'https://paddleocr.aistudio-app.com/output-only.png': png_payload,
    }
    poll_url = 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs/job-123'
    jsonl_url = 'https://paddleocr.aistudio-app.com/result.jsonl'

    def fake_post(url, data=None, files=None, headers=None, timeout=None):
        assert url == 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs'
        assert headers == {'Authorization': 'bearer secret-token'}
        assert data['model'] == 'PaddleOCR-VL-1.6'
        assert files['file'][0] == 'source.pdf'
        assert timeout == 45
        return FakeResponse(json_data={'jobId': 'job-123'})

    poll_calls = []

    def fake_get(url, headers=None, timeout=None, stream=False, allow_redirects=True):
        if url == poll_url:
            assert headers == {'Authorization': 'bearer secret-token'}
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
            assert headers is None
            assert stream is True
            assert timeout == 90
            assert allow_redirects is False
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
                                                'markdown-image.png': (
                                                    'https://paddleocr.aistudio-app.com/markdown-image.png'
                                                )
                                            },
                                        },
                                        'outputImages': ['https://paddleocr.aistudio-app.com/output-image.png'],
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
                                        'outputImages': ['https://paddleocr.aistudio-app.com/output-only.png'],
                                    }
                                ]
                            }
                        }
                    ),
                ]
            )
        raise AssertionError(f'unexpected GET {url}')

    def fake_urlopen(url, timeout=0):
        assert timeout == 90.0
        return FakeDownloadResponse(image_payloads[url])

    monkeypatch.setattr(paddleocr_vl.requests, 'post', fake_post)
    monkeypatch.setattr(paddleocr_vl.requests, 'get', fake_get)
    monkeypatch.setattr(document_image_assets, '_resolve_host_ips', lambda host, port: ['93.184.216.34'], raising=False)
    monkeypatch.setattr(document_image_assets.urlrequest, 'urlopen', fake_urlopen)
    monkeypatch.setattr(document_image_assets, '_urlopen_no_redirect', fake_urlopen, raising=False)
    monkeypatch.setattr(paddleocr_vl.time, 'sleep', lambda _: None)
    monkeypatch.setattr(paddleocr_vl, 'UPLOAD_DIR', uploads_dir, raising=False)

    docs = PaddleOCRVLLoader(
        api_url='https://paddleocr.aistudio-app.com',
        token='secret-token',
        file_path=str(source_pdf),
        request_timeout_s=45,
        download_timeout_s=90,
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
        'https://paddleocr.aistudio-app.com/output-image.png',
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
        'https://paddleocr.aistudio-app.com/output-only.png'
    )


def test_paddleocr_loader_submits_configured_model_and_optional_payload(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    submitted = {}

    def fake_post(url, data=None, files=None, headers=None, timeout=None):
        submitted.update(
            {
                'url': url,
                'data': data,
                'headers': headers,
                'timeout': timeout,
                'filename': files['file'][0],
            }
        )
        return FakeResponse(json_data={'jobId': 'job-123'})

    monkeypatch.setattr(paddleocr_vl.requests, 'post', fake_post)

    job_id = PaddleOCRVLLoader(
        api_url='https://paddleocr.aistudio-app.com',
        token='secret-token',
        file_path=str(source_pdf),
        model='PaddleOCR-VL-1.6',
        optional_payload={
            'useDocOrientationClassify': False,
            'useDocUnwarping': False,
            'useChartRecognition': False,
        },
        request_timeout_s=45,
    )._submit_job()

    assert job_id == 'job-123'
    assert submitted['url'] == 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs'
    assert submitted['headers'] == {'Authorization': 'bearer secret-token'}
    assert submitted['timeout'] == 45
    assert submitted['filename'] == 'source.pdf'
    assert submitted['data']['model'] == 'PaddleOCR-VL-1.6'
    assert json.loads(submitted['data']['optionalPayload']) == {
        'useDocOrientationClassify': False,
        'useDocUnwarping': False,
        'useChartRecognition': False,
    }


def test_loader_passes_paddleocr_async_options(monkeypatch):
    captured = {}

    class FakePaddleOCRVLLoader:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        'open_webui.retrieval.loaders.main.PaddleOCRVLLoader',
        FakePaddleOCRVLLoader,
    )

    loader = Loader(
        engine='paddleocr_vl',
        PADDLEOCR_VL_BASE_URL='https://paddleocr.aistudio-app.com',
        PADDLEOCR_VL_TOKEN='secret-token',
        PADDLEOCR_VL_MODEL='PaddleOCR-VL-1.6',
        PADDLEOCR_VL_OPTIONAL_PAYLOAD={
            'useDocOrientationClassify': False,
            'useDocUnwarping': False,
            'useChartRecognition': False,
        },
        PADDLEOCR_VL_REQUEST_TIMEOUT=45,
        PADDLEOCR_VL_DOWNLOAD_TIMEOUT=90,
        PADDLEOCR_VL_POLL_TIMEOUT=600,
        PADDLEOCR_VL_POLL_INTERVAL=5,
        PADDLEOCR_VL_ALLOWED_REMOTE_ORIGINS=[
            'https://paddleocr.aistudio-app.com',
            'https://paddleocr-cdn.example',
        ],
    )

    assert loader._get_loader('source.pdf', 'application/pdf', '/tmp/source.pdf') is not None
    assert captured == {
        'api_url': 'https://paddleocr.aistudio-app.com',
        'token': 'secret-token',
        'file_path': '/tmp/source.pdf',
        'model': 'PaddleOCR-VL-1.6',
        'optional_payload': {
            'useDocOrientationClassify': False,
            'useDocUnwarping': False,
            'useChartRecognition': False,
        },
        'request_timeout_s': 45,
        'download_timeout_s': 90,
        'poll_timeout_s': 600,
        'poll_interval_s': 5,
        'allowed_remote_origins': [
            'https://paddleocr.aistudio-app.com',
            'https://paddleocr-cdn.example',
        ],
    }


def test_build_loader_from_config_includes_paddleocr_async_options():
    class Config:
        CONTENT_EXTRACTION_ENGINE = 'paddleocr_vl'
        PADDLEOCR_VL_BASE_URL = 'https://paddleocr.aistudio-app.com'
        PADDLEOCR_VL_TOKEN = 'secret-token'
        PADDLEOCR_VL_MODEL = 'PaddleOCR-VL-1.6'
        PADDLEOCR_VL_OPTIONAL_PAYLOAD = {
            'useDocOrientationClassify': False,
            'useDocUnwarping': False,
            'useChartRecognition': False,
        }
        PADDLEOCR_VL_REQUEST_TIMEOUT = 45
        PADDLEOCR_VL_DOWNLOAD_TIMEOUT = 90
        PADDLEOCR_VL_POLL_TIMEOUT = 600
        PADDLEOCR_VL_POLL_INTERVAL = 5
        PADDLEOCR_VL_ALLOWED_REMOTE_ORIGINS = [
            'https://paddleocr.aistudio-app.com',
            'https://paddleocr-cdn.example',
        ]
        RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS = False

        def __getattr__(self, name):
            return None

    request = type(
        'Request',
        (),
        {'app': type('App', (), {'state': type('State', (), {'config': Config()})()})()},
    )()

    loader = build_loader_from_config(request)

    assert loader.engine == 'paddleocr_vl'
    assert loader.kwargs['PADDLEOCR_VL_MODEL'] == 'PaddleOCR-VL-1.6'
    assert loader.kwargs['PADDLEOCR_VL_OPTIONAL_PAYLOAD'] == {
        'useDocOrientationClassify': False,
        'useDocUnwarping': False,
        'useChartRecognition': False,
    }
    assert loader.kwargs['PADDLEOCR_VL_REQUEST_TIMEOUT'] == 45
    assert loader.kwargs['PADDLEOCR_VL_DOWNLOAD_TIMEOUT'] == 90
    assert loader.kwargs['PADDLEOCR_VL_POLL_TIMEOUT'] == 600
    assert loader.kwargs['PADDLEOCR_VL_POLL_INTERVAL'] == 5
    assert loader.kwargs['PADDLEOCR_VL_ALLOWED_REMOTE_ORIGINS'] == [
        'https://paddleocr.aistudio-app.com',
        'https://paddleocr-cdn.example',
    ]
    assert loader.kwargs['RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS'] is False


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


def test_paddleocr_jsonl_download_blocks_redirect_to_loopback(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    calls = []

    def fake_get(url, headers=None, timeout=None, stream=False, allow_redirects=True):
        calls.append(url)
        assert stream is True
        assert allow_redirects is False
        return FakeResponse(
            status_code=302,
            text='',
            headers={'Location': 'http://127.0.0.1/result.jsonl'},
        )

    monkeypatch.setattr(paddleocr_vl.requests, 'get', fake_get)
    monkeypatch.setattr(document_image_assets, '_resolve_host_ips', lambda host, port: ['93.184.216.34'], raising=False)

    loader = PaddleOCRVLLoader(
        api_url='https://paddleocr.aistudio-app.com',
        token='secret-token',
        file_path=str(source_pdf),
    )

    with pytest.raises(RuntimeError, match='not allowed'):
        loader._download_layout_results('https://paddleocr.aistudio-app.com/result.jsonl')

    assert calls == ['https://paddleocr.aistudio-app.com/result.jsonl']


def test_paddleocr_jsonl_download_rejects_oversized_response_bytes(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    line = json.dumps({'result': {'layoutParsingResults': []}})

    def fake_get(url, headers=None, timeout=None, stream=False, allow_redirects=True):
        assert stream is True
        assert allow_redirects is False
        return FakeResponse(lines=[line, line])

    monkeypatch.setattr(paddleocr_vl.requests, 'get', fake_get)
    monkeypatch.setattr(document_image_assets, '_resolve_host_ips', lambda host, port: ['93.184.216.34'], raising=False)

    loader = PaddleOCRVLLoader(
        api_url='https://paddleocr.aistudio-app.com',
        token='secret-token',
        file_path=str(source_pdf),
    )
    loader.max_jsonl_response_bytes = len(line) + 1
    loader.max_jsonl_line_bytes = 1024
    loader.max_jsonl_lines = 10

    with pytest.raises(RuntimeError, match='response size exceeded'):
        loader._download_layout_results('https://paddleocr.aistudio-app.com/result.jsonl')


def test_paddleocr_jsonl_download_rejects_oversized_line(tmp_path, monkeypatch):
    source_pdf = tmp_path / 'source.pdf'
    source_pdf.write_bytes(b'%PDF-1.4\n')
    line = json.dumps({'result': {'layoutParsingResults': []}})

    def fake_get(url, headers=None, timeout=None, stream=False, allow_redirects=True):
        assert stream is True
        assert allow_redirects is False
        return FakeResponse(lines=[line])

    monkeypatch.setattr(paddleocr_vl.requests, 'get', fake_get)
    monkeypatch.setattr(document_image_assets, '_resolve_host_ips', lambda host, port: ['93.184.216.34'], raising=False)

    loader = PaddleOCRVLLoader(
        api_url='https://paddleocr.aistudio-app.com',
        token='secret-token',
        file_path=str(source_pdf),
    )
    loader.max_jsonl_response_bytes = 1024
    loader.max_jsonl_line_bytes = 8
    loader.max_jsonl_lines = 10

    with pytest.raises(RuntimeError, match='line 1 exceeded'):
        loader._download_layout_results('https://paddleocr.aistudio-app.com/result.jsonl')
