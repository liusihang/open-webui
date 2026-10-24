"""PaddleOCR layout API and explicit AI Studio asynchronous jobs API.

Both modes consume images from the same parser response as text. No second OCR,
generated caption, outputImages debug rendering, or links scraped from markdown.
"""

import base64
import json
import logging
import time
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from langchain_core.documents import Document
from open_webui.retrieval.document_image_assets import (
    ImageAssetStore,
    download_service_resource,
    extract_markdown_image_assets,
)

log = logging.getLogger(__name__)
PADDLEOCR_VL_IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'bmp', 'tiff', 'webp']
PADDLEOCR_VL_SUPPORTED_EXTENSIONS = ['pdf', *PADDLEOCR_VL_IMAGE_EXTENSIONS]


class PaddleOCRVLLoader:
    def __init__(
        self,
        api_url: str,
        token: str,
        file_path: str,
        *,
        api_mode: str = 'layout',
        model: str = 'PaddleOCR-VL-1.6',
        optional_payload: dict | None = None,
        extract_images: bool = False,
        asset_root: str | Path | None = None,
        source_id: str | None = None,
        defer_image_conversion: bool = False,
        allowed_remote_origins: list[str] | tuple[str, ...] = (),
        request_timeout_s: float = 30,
        download_timeout_s: float = 60,
        poll_timeout_s: float = 300,
        poll_interval_s: float = 2,
    ):
        if not api_url or not token:
            raise ValueError('PaddleOCR API URL and token are required')
        if not Path(file_path).is_file():
            raise FileNotFoundError('PaddleOCR input file not found')
        parsed = urlsplit(api_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('PaddleOCR API URL must be HTTP(S) without credentials')
        if api_mode not in ('layout', 'jobs'):
            raise ValueError('PaddleOCR API mode must be layout or jobs')
        self.api_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))
        self.api_mode, self.model = api_mode, model
        self.token, self.file_path = token, file_path
        self.file_name = Path(file_path).name
        self.optional_payload = dict(optional_payload or {})
        self.extract_images = extract_images
        self.defer_image_conversion = defer_image_conversion
        if extract_images and asset_root is None:
            raise ValueError('Image extraction requires a private asset root')
        self.asset_root = asset_root
        self.source_id = source_id or str(Path(file_path).resolve())
        self.allowed_origins = (self.api_url, *allowed_remote_origins)
        self.request_timeout = max(1, float(request_timeout_s))
        self.download_timeout = max(1, float(download_timeout_s))
        self.poll_timeout = max(1, float(poll_timeout_s))
        self.poll_interval = max(0.1, min(float(poll_interval_s), 30))

    def load(self) -> list[Document]:
        # Parsing failures must not be indexed as invented OCR text.
        if self.api_mode == 'jobs':
            rows, resource_base = self._load_jobs()
        else:
            rows, resource_base = self._load_layout()
        return self._build_documents(rows, resource_base_url=resource_base)

    def _options(self):
        return {
            'useDocOrientationClassify': False,
            'useDocUnwarping': False,
            'useChartRecognition': False,
            **self.optional_payload,
            # Preserve per-page results, even when server supports merging.
            'restructurePages': False,
            'returnMarkdownImages': self.extract_images,
        }

    @staticmethod
    def _json(response, key):
        with response:
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f'PaddleOCR request failed with HTTP {response.status_code}')
            payload = response.json()
        if not isinstance(payload, dict) or payload.get('code', 0) not in (0, None):
            raise RuntimeError('PaddleOCR returned an API error')
        data = payload.get(key)
        if not isinstance(data, dict):
            raise RuntimeError('PaddleOCR response schema mismatch')
        return data

    def _load_layout(self):
        payload = {
            'file': base64.b64encode(Path(self.file_path).read_bytes()).decode('ascii'),
            'fileType': 1 if Path(self.file_path).suffix.lstrip('.').lower() in PADDLEOCR_VL_IMAGE_EXTENSIONS else 0,
            **self._options(),
        }
        url = self.api_url if self.api_url.endswith('/layout-parsing') else self.api_url + '/layout-parsing'
        result = self._json(
            requests.post(
                url,
                json=payload,
                headers={'Authorization': f'token {self.token}'},
                timeout=self.request_timeout,
                allow_redirects=False,
            ),
            'result',
        )
        rows = result.get('layoutParsingResults')
        if not isinstance(rows, list):
            raise RuntimeError('PaddleOCR response missing layoutParsingResults')
        return rows, url

    def _load_jobs(self):
        if self.api_url.endswith('/api/v2/ocr/jobs'):
            jobs_url = self.api_url
        elif self.api_url.endswith('/api/v2/ocr'):
            jobs_url = self.api_url + '/jobs'
        else:
            jobs_url = self.api_url + '/api/v2/ocr/jobs'
        headers = {'Authorization': f'Bearer {self.token}'}
        with open(self.file_path, 'rb') as handle:
            submitted = self._json(
                requests.post(
                    jobs_url,
                    headers=headers,
                    data={'model': self.model, 'optionalPayload': json.dumps(self._options())},
                    files={'file': (self.file_name, handle, 'application/octet-stream')},
                    timeout=self.request_timeout,
                    allow_redirects=False,
                ),
                'data',
            )
        job_id = submitted.get('jobId')
        if not isinstance(job_id, str) or not job_id:
            raise RuntimeError('PaddleOCR response missing jobId')
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            state = self._json(
                requests.get(
                    jobs_url + '/' + quote(job_id, safe=''),
                    headers=headers,
                    timeout=min(self.request_timeout, max(1, deadline - time.monotonic())),
                    allow_redirects=False,
                ),
                'data',
            )
            if state.get('state') == 'done':
                break
            if state.get('state') not in ('pending', 'running'):
                raise RuntimeError('PaddleOCR job failed or returned an unknown state')
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        else:
            raise TimeoutError('PaddleOCR job timed out')
        result_url = state.get('resultUrl', {})
        if not isinstance(result_url, dict) or not isinstance(result_url.get('jsonUrl'), str):
            raise RuntimeError('PaddleOCR done job missing resultUrl.jsonUrl')
        return self._download_job_results(result_url['jsonUrl']), result_url['jsonUrl']

    def _download_job_results(self, url):
        data = download_service_resource(
            url,
            allowed_origins=self.allowed_origins,
            max_bytes=100 * 1024 * 1024,
            timeout=self.download_timeout,
        )
        rows = []
        for index, line in enumerate(data.splitlines()):
            if index >= 10000 or len(line) > 30 * 1024 * 1024:
                raise RuntimeError('PaddleOCR JSONL budget exceeded')
            if not line.strip():
                continue
            payload = json.loads(line)
            page_rows = payload.get('result', {}).get('layoutParsingResults')
            if not isinstance(page_rows, list):
                raise RuntimeError('PaddleOCR JSONL schema mismatch')
            rows.extend(page_rows)
        return rows

    def _build_documents(self, rows: list, *, resource_base_url: str) -> list[Document]:
        documents = []
        store = (
            ImageAssetStore(self.asset_root, self.source_id, defer_conversion=self.defer_image_conversion)
            if self.extract_images
            else None
        )
        for ordinal, row in enumerate(rows):
            if not isinstance(row, dict):
                raise RuntimeError('PaddleOCR page schema mismatch')
            markdown = row.get('markdown') or {}
            if not isinstance(markdown, dict):
                raise RuntimeError('PaddleOCR markdown schema mismatch')
            text = markdown.get('text') or ''
            if not isinstance(text, str):
                raise RuntimeError('PaddleOCR markdown text must be a string')
            page_index = _page_index(row, ordinal)
            metadata = {
                'page': page_index,
                'page_label': page_index + 1,
                'total_pages': len(rows),
                'file_name': self.file_name,
                'processing_engine': 'paddleocr-vl',
            }
            if store:
                extracted = extract_markdown_image_assets(
                    markdown,
                    store=store,
                    page_index=page_index,
                    backend='paddleocr-vl',
                    resource_base_url=resource_base_url,
                    allowed_origins=self.allowed_origins,
                    timeout=self.download_timeout,
                )
                metadata['document_image_assets'] = extracted.assets
                metadata['document_image_assets_skipped'] = extracted.skipped
            if text.strip() or metadata.get('document_image_assets') or metadata.get('document_image_assets_skipped'):
                if not text.strip():
                    metadata['_metadata_only'] = True
                documents.append(Document(page_content=text.strip(), metadata=metadata))
        return documents


def _page_index(row: dict, ordinal: int) -> int:
    # page_index/pageIndex are zero-based; pageNo/page_label are one-based.
    # Full unfiltered result order is the documented fallback. Restructuring
    # is disabled, and this loader does not submit partial page ranges.
    for data in (row, row.get('prunedResult') or {}):
        if not isinstance(data, dict):
            continue
        for key in ('page_index', 'pageIndex'):
            if type(data.get(key)) is int and data[key] >= 0:
                return data[key]
        for key in ('pageNo', 'page_label'):
            if type(data.get(key)) is int and data[key] >= 1:
                return data[key] - 1
    return ordinal
