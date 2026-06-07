import base64
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from uuid import uuid4

import requests
from langchain_core.documents import Document
from open_webui.config import UPLOAD_DIR
from open_webui.env import GLOBAL_LOG_LEVEL

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaterializedImage:
    storage_path: Path
    sha256: str
    width: int | None
    height: int | None


class PaddleOCRVLLoader:
    """Loader that uses PaddleOCR-vl API to extract text from PDF/images."""

    def __init__(
        self,
        api_url: str,
        token: str,
        file_path: str,
    ):
        if not api_url or not token:
            raise ValueError('PaddleOCR-vl API URL and Token are required.')
        if not os.path.exists(file_path):
            raise FileNotFoundError(f'File not found at {file_path}')

        self.api_url = api_url.rstrip('/')
        self.token = token
        self.file_path = file_path
        self.file_name = os.path.basename(file_path)

    def load(self) -> List[Document]:
        log.info(f'Processing with PaddleOCR-vl: {self.file_path}')

        try:
            with open(self.file_path, 'rb') as file:
                file_bytes = file.read()
                file_data = base64.b64encode(file_bytes).decode('ascii')
        except Exception as e:
            log.error(f'Failed to read file {self.file_path}: {e}')
            raise

        headers = {'Authorization': f'token {self.token}', 'Content-Type': 'application/json'}

        # Detect fileType based on file extension
        ext = self.file_path.lower().split('.')[-1]
        image_extensions = ['png', 'jpg', 'jpeg', 'bmp', 'tiff', 'webp']
        file_type = 1 if ext in image_extensions else 0

        payload = {
            'file': file_data,
            'fileType': file_type,
            'useDocOrientationClassify': False,
            'useDocUnwarping': False,
            'useChartRecognition': False,
        }

        try:
            response = requests.post(f'{self.api_url}/layout-parsing', json=payload, headers=headers)
            response.raise_for_status()

            raw_result = response.json()
            result = raw_result.get('result', {})
            layout_results = result.get('layoutParsingResults', [])

            documents = []
            total_pages = len(layout_results)
            skipped_pages = 0

            for i, res in enumerate(layout_results):
                markdown = res.get('markdown', {}) if isinstance(res.get('markdown'), dict) else {}
                markdown_text = markdown.get('text', '')
                page_no = _extract_page_number(res, fallback=i + 1)
                image_assets, skipped_images = _build_image_assets(
                    source_path=Path(self.file_path),
                    source_id=Path(self.file_path).stem,
                    markdown=markdown,
                    markdown_text=markdown_text if isinstance(markdown_text, str) else '',
                    page_no=page_no,
                    asset_root=Path(UPLOAD_DIR) / 'paddleocr-vl-image-assets',
                )

                if isinstance(markdown_text, str):
                    cleaned_content = markdown_text.strip()
                else:
                    cleaned_content = str(markdown_text).strip()

                if not cleaned_content:
                    skipped_pages += 1
                    if image_assets:
                        documents.append(
                            Document(
                                page_content='',
                                metadata={
                                    'page': page_no - 1,
                                    'page_label': page_no,
                                    'total_pages': total_pages,
                                    'file_name': self.file_name,
                                    'processing_engine': 'paddleocr-vl',
                                    'document_image_assets': image_assets,
                                    '_metadata_only': True,
                                    **({'skipped_images': skipped_images} if skipped_images else {}),
                                },
                            )
                        )
                    continue

                documents.append(
                    Document(
                        page_content=cleaned_content,
                        metadata={
                            'page': page_no - 1,
                            'page_label': page_no,
                            'total_pages': total_pages,
                            'file_name': self.file_name,
                            'processing_engine': 'paddleocr-vl',
                            **({'document_image_assets': image_assets} if image_assets else {}),
                            **({'skipped_images': skipped_images} if skipped_images else {}),
                        },
                    )
                )

            if skipped_pages > 0:
                log.info(f'PaddleOCR-vl: Processed {len(documents)} pages, skipped {skipped_pages} empty pages.')

            if not documents:
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

            return documents

        except Exception as e:
            log.error(f'Error calling PaddleOCR-vl: {e}')
            return [
                Document(
                    page_content=f'Error during OCR processing: {e}',
                    metadata={
                        'error': 'processing_failed',
                        'file_name': self.file_name,
                        'processing_engine': 'paddleocr-vl',
                    },
                )
            ]


def _extract_page_number(item: dict, *, fallback: int) -> int:
    for key in ('page', 'pageNo', 'pageIndex'):
        value = item.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return fallback


def _build_image_assets(
    *,
    source_path: Path,
    source_id: str,
    markdown: dict,
    markdown_text: str,
    page_no: int,
    asset_root: Path,
) -> tuple[list[dict], list[dict[str, str]]]:
    assets: list[dict] = []
    skipped: list[dict[str, str]] = []
    for ordinal, (reference, origin_uri) in enumerate(_iter_markdown_image_entries(markdown.get('images')), start=1):
        materialized = None
        storage_path = _resolve_image_storage_path(
            source_path=source_path,
            reference=reference,
            origin_uri=origin_uri,
        )
        if storage_path is None:
            materialized = _materialize_image(
                asset_root=asset_root,
                source_id=source_id,
                page_no=page_no,
                ordinal=ordinal,
                reference=reference,
                origin_uri=origin_uri,
            )
            if materialized is not None:
                storage_path = materialized.storage_path
        if storage_path is None:
            skipped.append(
                {
                    'reference': reference,
                    'origin_uri': origin_uri or '',
                    'reason': 'storage_path_unavailable',
                }
            )
            continue

        image_fingerprint = (
            f"sha256:{materialized.sha256}" if materialized is not None else _fingerprint_file(storage_path)
        )
        width = materialized.width if materialized is not None else None
        height = materialized.height if materialized is not None else None
        if width is None or height is None:
            width, height = _probe_image_dimensions(storage_path)

        block_id = f'page-{page_no:03d}-image-{ordinal:03d}'
        assets.append(
            {
                'storage_path': str(storage_path),
                'asset_kind': 'document_image',
                'image_fingerprint': image_fingerprint,
                'page_index': page_no,
                'caption': _first_non_empty_line(markdown_text),
                'surrounding_text': _short_context(markdown_text),
                'anchor': {'page': page_no, 'block_id': block_id},
                'origin_uri': origin_uri,
                'metadata': {
                    'backend': 'paddleocr-vl',
                    'page': page_no,
                    'origin_reference': reference,
                    **({'width': width} if width is not None else {}),
                    **({'height': height} if height is not None else {}),
                },
            }
        )
    return assets, skipped


def _iter_markdown_image_entries(images) -> list[tuple[str, str | None]]:
    if isinstance(images, dict):
        entries: list[tuple[str, str | None]] = []
        for reference, origin_uri in images.items():
            if not isinstance(reference, str) or not reference:
                continue
            entries.append((reference, origin_uri if isinstance(origin_uri, str) else None))
        return entries
    if isinstance(images, list):
        return [(image, image if _is_remote_uri(image) else None) for image in images if isinstance(image, str)]
    return []


def _resolve_image_storage_path(*, source_path: Path, reference: str, origin_uri: str | None) -> Path | None:
    for value in (reference, origin_uri):
        if not value or _is_remote_uri(value):
            continue
        path = Path(value)
        candidates: list[Path] = []
        if path.is_absolute():
            candidates.append(path)
        candidates.append(source_path.parent / path)
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
    return None


def _materialize_image(
    *,
    asset_root: Path,
    source_id: str,
    page_no: int,
    ordinal: int,
    reference: str,
    origin_uri: str | None,
) -> MaterializedImage | None:
    local_path = _resolve_local_file(reference=reference, origin_uri=origin_uri)
    if local_path is not None:
        return _describe_local_image(local_path)

    remote_uri = _resolve_remote_uri(reference=reference, origin_uri=origin_uri)
    if remote_uri is None:
        return None
    source_dir = asset_root / _safe_component(source_id)
    source_dir.mkdir(parents=True, exist_ok=True)
    temp_path = source_dir / f'.tmp-{uuid4().hex}'
    try:
        with urlrequest.urlopen(remote_uri, timeout=30.0) as response:
            status_code = _response_status_code(response)
            if status_code is not None and status_code >= 400:
                return None

            digest = hashlib.sha256()
            with temp_path.open('wb') as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    handle.write(chunk)

        sha256 = digest.hexdigest()
        suffix = _select_suffix(reference=reference, remote_uri=remote_uri, downloaded_path=temp_path)
        storage_path = source_dir / f'page-{page_no:03d}-image-{ordinal:03d}-{sha256[:16]}{suffix}'
        if storage_path.exists():
            temp_path.unlink(missing_ok=True)
        else:
            temp_path.replace(storage_path)
        width, height = _probe_image_dimensions(storage_path)
        return MaterializedImage(storage_path=storage_path.resolve(), sha256=sha256, width=width, height=height)
    except (OSError, urlerror.URLError, ValueError):
        temp_path.unlink(missing_ok=True)
        return None


def _resolve_local_file(*, reference: str, origin_uri: str | None) -> Path | None:
    for candidate in (reference, origin_uri):
        if not candidate or _is_remote_uri(candidate):
            continue
        path = Path(candidate)
        if path.is_file():
            return path.resolve()
    return None


def _describe_local_image(path: Path) -> MaterializedImage:
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    width, height = _probe_image_dimensions(path)
    return MaterializedImage(storage_path=path, sha256=sha256, width=width, height=height)


def _resolve_remote_uri(*, reference: str, origin_uri: str | None) -> str | None:
    if origin_uri and _is_remote_uri(origin_uri):
        return origin_uri
    if _is_remote_uri(reference):
        return reference
    return None


def _fingerprint_file(path: Path) -> str:
    return f'sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}'


def _first_non_empty_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _short_context(text: str, *, max_chars: int = 500) -> str | None:
    collapsed = ' '.join(text.split())
    if not collapsed:
        return None
    return collapsed[:max_chars]


def _is_remote_uri(value: str) -> bool:
    return value.startswith(('http://', 'https://'))


def _safe_component(value: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9._-]+', '-', value).strip('-')
    return normalized or 'source'


def _response_status_code(response: object) -> int | None:
    status = getattr(response, 'status', None)
    if isinstance(status, int):
        return status
    getter = getattr(response, 'getcode', None)
    if callable(getter):
        code = getter()
        if isinstance(code, int):
            return code
    return None


def _select_suffix(*, reference: str, remote_uri: str, downloaded_path: Path) -> str:
    for value in (reference, remote_uri):
        suffix = _suffix_from_reference(value)
        if suffix is not None:
            return suffix
    with downloaded_path.open('rb') as handle:
        header = handle.read(24)
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if header.startswith(b'\xff\xd8'):
        return '.jpg'
    return '.bin'


def _suffix_from_reference(value: str) -> str | None:
    path_text = urlparse.urlparse(value).path if _is_remote_uri(value) else value
    suffix = Path(path_text).suffix.lower()
    if suffix in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.tif'}:
        return suffix
    return None


def _probe_image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        with path.open('rb') as handle:
            header = handle.read(32)
        if len(header) >= 24 and header.startswith(b'\x89PNG\r\n\x1a\n'):
            return (
                int.from_bytes(header[16:20], 'big'),
                int.from_bytes(header[20:24], 'big'),
            )
        if len(header) >= 4 and header.startswith(b'\xff\xd8'):
            from PIL import Image

            with Image.open(path) as image:
                return image.width, image.height
    except OSError:
        return None, None
    return None, None
