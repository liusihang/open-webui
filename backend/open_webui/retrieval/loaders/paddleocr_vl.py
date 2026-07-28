import json
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from langchain_core.documents import Document
from open_webui.config import UPLOAD_DIR
from open_webui.env import GLOBAL_LOG_LEVEL
from open_webui.retrieval.document_image_assets import (
    ImageAssetMaterializer,
    build_image_assets_from_markdown,
    validate_remote_download_url,
)

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)

DEFAULT_PADDLEOCR_VL_MODEL = 'PaddleOCR-VL-1.6'
DEFAULT_REQUEST_TIMEOUT_S = 30
DEFAULT_DOWNLOAD_TIMEOUT_S = 60
DEFAULT_POLL_TIMEOUT_S = 300
DEFAULT_POLL_INTERVAL_S = 2
DEFAULT_MAX_JSONL_RESPONSE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_JSONL_LINE_BYTES = 1024 * 1024
DEFAULT_MAX_JSONL_LINES = 10_000
DEFAULT_MAX_JSONL_REDIRECTS = 5
PADDLEOCR_VL_JOBS_PATH = '/api/v2/ocr/jobs'
TERMINAL_FAILURE_STATES = {'failed', 'error', 'cancelled', 'canceled'}


class PaddleOCRVLLoader:
    """Loader that uses the PaddleOCR-VL async jobs API to extract text from documents."""

    def __init__(
        self,
        api_url: str,
        token: str,
        file_path: str,
        *,
        model: str = DEFAULT_PADDLEOCR_VL_MODEL,
        optional_payload: dict[str, Any] | None = None,
        request_timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
        download_timeout_s: int = DEFAULT_DOWNLOAD_TIMEOUT_S,
        poll_timeout_s: int = DEFAULT_POLL_TIMEOUT_S,
        poll_interval_s: int | float = DEFAULT_POLL_INTERVAL_S,
        allowed_remote_origins: list[str] | tuple[str, ...] | None = None,
        max_jsonl_response_bytes: int = DEFAULT_MAX_JSONL_RESPONSE_BYTES,
        max_jsonl_line_bytes: int = DEFAULT_MAX_JSONL_LINE_BYTES,
        max_jsonl_lines: int = DEFAULT_MAX_JSONL_LINES,
    ):
        if not api_url or not token:
            raise ValueError('PaddleOCR-vl API URL and Token are required.')
        if not os.path.exists(file_path):
            raise FileNotFoundError(f'File not found at {file_path}')

        self.jobs_url = _normalize_jobs_url(api_url)
        self.token = token
        self.file_path = file_path
        self.file_name = os.path.basename(file_path)
        self.model = model
        self.optional_payload = optional_payload
        self.request_timeout_s = request_timeout_s
        self.download_timeout_s = download_timeout_s
        self.poll_timeout_s = poll_timeout_s
        self.poll_interval_s = poll_interval_s
        self.allowed_remote_origins = tuple(allowed_remote_origins or ())
        self.max_jsonl_response_bytes = max(0, int(max_jsonl_response_bytes))
        self.max_jsonl_line_bytes = max(0, int(max_jsonl_line_bytes))
        self.max_jsonl_lines = max(0, int(max_jsonl_lines))

    def load(self) -> list[Document]:
        log.info('Processing with PaddleOCR-vl: %s', self.file_path)

        job_id = self._submit_job()
        job_result = self._poll_job(job_id)
        jsonl_url = self._extract_jsonl_url(job_result, job_id=job_id)
        layout_results = self._download_layout_results(jsonl_url)
        return self._build_documents(layout_results, result_url=jsonl_url)

    def _submit_job(self) -> str:
        mime_type = mimetypes.guess_type(self.file_name)[0] or 'application/octet-stream'
        data = {'model': self.model}
        if self.optional_payload:
            data['optionalPayload'] = json.dumps(self.optional_payload, ensure_ascii=False)

        with open(self.file_path, 'rb') as handle:
            response = requests.post(
                self.jobs_url,
                data=data,
                files={'file': (self.file_name, handle, mime_type)},
                headers=self._auth_headers(),
                timeout=self.request_timeout_s,
            )

        payload = self._read_json_response(response, operation='submit')
        job_data = _job_payload_data(payload)
        job_id = job_data.get('jobId') or job_data.get('id') or payload.get('jobId') or payload.get('id')
        if not isinstance(job_id, str) or not job_id.strip():
            raise RuntimeError('PaddleOCR-vl submit response missing jobId.')
        return job_id

    def _poll_job(self, job_id: str) -> dict[str, Any]:
        poll_url = f'{self.jobs_url}/{job_id}'
        started_at = time.monotonic()
        while True:
            if time.monotonic() - started_at > self.poll_timeout_s:
                raise TimeoutError(
                    f'PaddleOCR-vl job {job_id} timed out after {self.poll_timeout_s} seconds.'
                )

            payload = self._read_json_response(
                requests.get(
                    poll_url,
                    headers=self._auth_headers(),
                    timeout=self.request_timeout_s,
                ),
                operation='poll',
            )
            job_data = _job_payload_data(payload)
            state = job_data.get('state') or payload.get('state')
            if state == 'done':
                return job_data or payload

            if isinstance(state, str) and state.lower() in TERMINAL_FAILURE_STATES:
                detail = _extract_error_detail(job_data) or _extract_error_detail(payload) or f'job entered terminal state {state!r}'
                raise RuntimeError(f'PaddleOCR-vl job {job_id} failed: {detail}')

            if not isinstance(state, str) or not state:
                raise RuntimeError(f'PaddleOCR-vl job {job_id} returned no valid state.')

            time.sleep(self.poll_interval_s)

    def _extract_jsonl_url(self, payload: dict[str, Any], *, job_id: str) -> str:
        result_url = payload.get('resultUrl')
        if isinstance(result_url, dict):
            jsonl_url = result_url.get('jsonUrl')
        elif isinstance(result_url, str):
            jsonl_url = result_url
        else:
            jsonl_url = None

        if not isinstance(jsonl_url, str) or not jsonl_url.strip():
            raise RuntimeError(f'PaddleOCR-vl job {job_id} completed without resultUrl.jsonUrl.')
        return jsonl_url

    def _download_layout_results(self, jsonl_url: str) -> list[dict[str, Any]]:
        response = self._get_result_response(jsonl_url)
        self._raise_for_status(response, operation='download results')

        content_length = _response_content_length(response)
        if content_length is not None and content_length > self.max_jsonl_response_bytes:
            _close_response(response)
            raise RuntimeError('PaddleOCR-vl results response size exceeded limit.')

        layout_results: list[dict[str, Any]] = []
        for line_no, raw_bytes in self._iter_limited_jsonl_lines(response):
            if not raw_bytes or not raw_bytes.strip():
                continue
            raw_line_text = raw_bytes.decode('utf-8')
            try:
                payload = json.loads(raw_line_text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f'PaddleOCR-vl results JSONL parse failed at line {line_no}: {exc}'
                ) from exc

            result = payload.get('result', {})
            if not isinstance(result, dict):
                continue
            rows = result.get('layoutParsingResults', [])
            if isinstance(rows, list):
                layout_results.extend(item for item in rows if isinstance(item, dict))

        return layout_results

    def _iter_limited_jsonl_lines(self, response: requests.Response):
        total_bytes = 0
        line_no = 0
        pending = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                chunk_bytes = chunk.encode('utf-8') if isinstance(chunk, str) else chunk
                total_bytes += len(chunk_bytes)
                if total_bytes > self.max_jsonl_response_bytes:
                    raise RuntimeError('PaddleOCR-vl results response size exceeded limit.')

                pending.extend(chunk_bytes)
                while True:
                    newline_index = pending.find(b'\n')
                    if newline_index < 0:
                        if len(pending) > self.max_jsonl_line_bytes:
                            raise RuntimeError(f'PaddleOCR-vl results line {line_no + 1} exceeded maximum length.')
                        break
                    raw_line = bytes(pending[:newline_index]).rstrip(b'\r')
                    del pending[: newline_index + 1]
                    line_no += 1
                    yield self._validate_jsonl_line(line_no=line_no, raw_line=raw_line)

            if pending:
                line_no += 1
                yield self._validate_jsonl_line(line_no=line_no, raw_line=bytes(pending).rstrip(b'\r'))
        finally:
            _close_response(response)

    def _validate_jsonl_line(self, *, line_no: int, raw_line: bytes) -> tuple[int, bytes]:
        if line_no > self.max_jsonl_lines:
            raise RuntimeError('PaddleOCR-vl results line count exceeded limit.')
        if len(raw_line) > self.max_jsonl_line_bytes:
            raise RuntimeError(f'PaddleOCR-vl results line {line_no} exceeded maximum length.')
        return line_no, raw_line

    def _get_result_response(self, jsonl_url: str) -> requests.Response:
        current_url = jsonl_url
        redirects = 0
        allowed_origins = (self.jobs_url, *self.allowed_remote_origins)

        while True:
            try:
                current_url = validate_remote_download_url(
                    current_url,
                    allowed_remote_origins=allowed_origins,
                )
            except ValueError as exc:
                raise RuntimeError(f'PaddleOCR-vl results URL not allowed: {exc}') from exc

            response = requests.get(
                current_url,
                timeout=self.download_timeout_s,
                stream=True,
                allow_redirects=False,
            )
            status_code = getattr(response, 'status_code', None)
            if not isinstance(status_code, int) or not 300 <= status_code < 400:
                return response

            if redirects >= DEFAULT_MAX_JSONL_REDIRECTS:
                _close_response(response)
                raise RuntimeError('PaddleOCR-vl results redirect limit exceeded.')
            location = response.headers.get('Location') if hasattr(response, 'headers') else None
            if not location:
                _close_response(response)
                raise RuntimeError('PaddleOCR-vl results redirect response missing Location header.')
            _close_response(response)
            current_url = urljoin(current_url, location)
            redirects += 1

    def _build_documents(
        self,
        layout_results: list[dict[str, Any]],
        *,
        result_url: str | None = None,
    ) -> list[Document]:
        documents: list[Document] = []
        total_pages = len(layout_results)
        skipped_pages = 0
        image_materializer = ImageAssetMaterializer(
            Path(UPLOAD_DIR) / 'paddleocr-vl-image-assets',
            download_timeout_s=float(self.download_timeout_s),
            allowed_remote_origins=(
                self.jobs_url,
                *([result_url] if result_url else []),
                *self.allowed_remote_origins,
            ),
        )

        for index, result in enumerate(layout_results):
            markdown = result.get('markdown', {}) if isinstance(result.get('markdown'), dict) else {}
            markdown_text = markdown.get('text', '')
            page_no = _extract_page_number(result, fallback=index + 1)
            image_assets, skipped_images = build_image_assets_from_markdown(
                source_path=Path(self.file_path),
                source_id=Path(self.file_path).stem,
                markdown=_with_output_images(markdown=markdown, output_images=result.get('outputImages')),
                markdown_text=markdown_text if isinstance(markdown_text, str) else '',
                page_no=page_no,
                materializer=image_materializer,
                backend='paddleocr-vl',
            )

            cleaned_content = markdown_text.strip() if isinstance(markdown_text, str) else str(markdown_text).strip()
            metadata = {
                'page': page_no - 1,
                'page_label': page_no,
                'total_pages': total_pages,
                'file_name': self.file_name,
                'processing_engine': 'paddleocr-vl',
                **({'document_image_assets': image_assets} if image_assets else {}),
                **({'skipped_images': skipped_images} if skipped_images else {}),
            }

            if not cleaned_content:
                skipped_pages += 1
                if image_assets:
                    metadata['_metadata_only'] = True
                    documents.append(Document(page_content='', metadata=metadata))
                continue

            documents.append(Document(page_content=cleaned_content, metadata=metadata))

        if skipped_pages > 0:
            log.info('PaddleOCR-vl: Processed %s pages, skipped %s empty pages.', len(documents), skipped_pages)

        if documents:
            return documents

        log.warning('No valid text content found by PaddleOCR-vl.')
        return [
            Document(
                page_content='No valid text content found in document',
                metadata={
                    'error': 'no_valid_pages',
                    'file_name': self.file_name,
                    'processing_engine': 'paddleocr-vl',
                },
            )
        ]

    def _auth_headers(self) -> dict[str, str]:
        return {'Authorization': f'bearer {self.token}'}

    def _read_json_response(self, response: requests.Response, *, operation: str) -> dict[str, Any]:
        self._raise_for_status(response, operation=operation)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f'PaddleOCR-vl {operation} returned invalid JSON: {exc}') from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f'PaddleOCR-vl {operation} returned an unexpected JSON payload.')
        return payload

    def _raise_for_status(self, response: requests.Response, *, operation: str) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            status_code = getattr(getattr(exc, 'response', None), 'status_code', None) or getattr(
                response, 'status_code', 'unknown'
            )
            body = getattr(getattr(exc, 'response', None), 'text', None) or getattr(response, 'text', '')
            body = body.strip()
            suffix = f': {body[:300]}' if body else ''
            raise RuntimeError(
                f'PaddleOCR-vl {operation} request failed with status {status_code}{suffix}'
            ) from exc


def _normalize_jobs_url(api_url: str) -> str:
    parsed = urlparse(api_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError('PaddleOCR-vl API URL must be an absolute URL.')

    path = parsed.path.rstrip('/')
    if path.endswith(PADDLEOCR_VL_JOBS_PATH):
        normalized_path = path
    elif path.endswith('/api/v2/ocr'):
        normalized_path = f'{path}/jobs'
    else:
        normalized_path = f'{path}{PADDLEOCR_VL_JOBS_PATH}' if path else PADDLEOCR_VL_JOBS_PATH

    return parsed._replace(path=normalized_path, params='', query='', fragment='').geturl()


def _job_payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get('data')
    return data if isinstance(data, dict) else payload


def _extract_error_detail(payload: dict[str, Any]) -> str | None:
    for key in ('error', 'detail', 'message'):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested_key in ('message', 'detail', 'msg'):
                nested_value = value.get(nested_key)
                if isinstance(nested_value, str) and nested_value.strip():
                    return nested_value.strip()
    return None


def _with_output_images(markdown: dict[str, Any], output_images: Any) -> dict[str, Any]:
    merged = dict(markdown)
    merged_images: dict[str, str | None] = {}

    for reference, origin_uri in _iter_markdown_images(markdown.get('images')):
        merged_images[reference] = origin_uri

    for reference, origin_uri in _iter_output_images(output_images):
        if reference not in merged_images:
            merged_images[reference] = origin_uri

    merged['images'] = merged_images
    return merged


def _iter_markdown_images(images: Any) -> list[tuple[str, str | None]]:
    if isinstance(images, dict):
        return [
            (reference, origin_uri if isinstance(origin_uri, str) else None)
            for reference, origin_uri in images.items()
            if isinstance(reference, str) and reference
        ]
    if isinstance(images, list):
        return [(image, image if _is_remote_uri(image) else None) for image in images if isinstance(image, str)]
    return []


def _iter_output_images(output_images: Any) -> list[tuple[str, str | None]]:
    if not isinstance(output_images, list):
        return []

    entries: list[tuple[str, str | None]] = []
    for item in output_images:
        entry = _normalize_output_image_entry(item)
        if entry is not None:
            entries.append(entry)
    return entries


def _normalize_output_image_entry(item: Any) -> tuple[str, str | None] | None:
    if isinstance(item, str) and item:
        return item, item if _is_remote_uri(item) else None

    if not isinstance(item, dict):
        return None

    reference = _first_non_empty_string(
        item.get('path'),
        item.get('filePath'),
        item.get('name'),
        item.get('key'),
        item.get('url'),
        item.get('imageUrl'),
        item.get('downloadUrl'),
        item.get('originUrl'),
        item.get('id'),
    )
    origin_uri = _first_remote_string(
        item.get('url'),
        item.get('imageUrl'),
        item.get('downloadUrl'),
        item.get('originUrl'),
    )
    if reference is None and origin_uri is None:
        return None
    return reference or origin_uri, origin_uri


def _first_non_empty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_remote_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and _is_remote_uri(value):
            return value
    return None


def _response_content_length(response: requests.Response) -> int | None:
    value = response.headers.get('Content-Length') if hasattr(response, 'headers') else None
    if value is None:
        return None
    try:
        content_length = int(value)
    except ValueError:
        return None
    return content_length if content_length >= 0 else None


def _close_response(response: requests.Response) -> None:
    close = getattr(response, 'close', None)
    if callable(close):
        close()


def _is_remote_uri(value: str) -> bool:
    return value.startswith(('http://', 'https://'))


def _extract_page_number(item: dict, *, fallback: int) -> int:
    for key in ('page', 'pageNo', 'pageIndex'):
        value = item.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return fallback
