from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from open_webui.config import UPLOAD_DIR
from open_webui.retrieval.document_image_assets import (
    DocumentImageAssetPayload,
    build_document_image_asset_payload,
)
from open_webui.storage.provider import Storage
from pypdf import PdfReader

log = logging.getLogger(__name__)

DEFAULT_MAX_PDF_IMAGE_ASSET_PAGES = 200
DEFAULT_MAX_PDF_IMAGE_ASSETS = 200
DEFAULT_MAX_PDF_IMAGE_ASSET_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_PDF_IMAGE_ASSET_TOTAL_BYTES = 100 * 1024 * 1024


@dataclass
class PdfImageAssetExtraction:
    assets_by_page: dict[int, list[DocumentImageAssetPayload]] = field(default_factory=dict)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @property
    def assets(self) -> list[DocumentImageAssetPayload]:
        return [asset for page_assets in self.assets_by_page.values() for asset in page_assets]


def extract_pdf_image_assets(
    file_path: str | Path,
    *,
    asset_root: str | Path | None = None,
    source_id: str | None = None,
    page_text_by_index: Mapping[int, str] | None = None,
) -> PdfImageAssetExtraction:
    source_path = Path(file_path)
    output_root = Path(asset_root) if asset_root else Path(UPLOAD_DIR) / 'pdf-image-assets'
    source_component = _safe_component(source_id or source_path.stem)
    page_text_by_index = page_text_by_index or {}
    result = PdfImageAssetExtraction()
    max_pages = _budget(DEFAULT_MAX_PDF_IMAGE_ASSET_PAGES)
    max_images = _budget(DEFAULT_MAX_PDF_IMAGE_ASSETS)
    max_image_bytes = _budget(DEFAULT_MAX_PDF_IMAGE_ASSET_BYTES)
    max_total_bytes = _budget(DEFAULT_MAX_PDF_IMAGE_ASSET_TOTAL_BYTES)
    stored_images = 0
    stored_bytes = 0

    try:
        reader = PdfReader(str(source_path))
    except Exception as exc:
        result.skipped.append(_skip(reason='pdf_read_error', error=exc))
        return result

    for page_no, page in enumerate(reader.pages, start=1):
        if page_no > max_pages:
            result.skipped.append(_skip(page_index=page_no, reason='pdf_image_asset_page_limit_exceeded'))
            break

        try:
            images = list(page.images)
        except Exception as exc:
            result.skipped.append(_skip(page_index=page_no, reason='pdf_page_images_unavailable', error=exc))
            continue

        for ordinal, image_file in enumerate(images, start=1):
            image_name = _image_name(image_file, ordinal=ordinal)
            try:
                image_bytes, suffix, mime_type, width, height = _extract_image_payload(image_file, image_name)
                if not image_bytes:
                    result.skipped.append(
                        _skip(page_index=page_no, image_name=image_name, reason='pdf_image_bytes_unavailable')
                    )
                    continue
                image_size = len(image_bytes)
                budget_skip_reason = _pdf_image_budget_skip_reason(
                    image_size=image_size,
                    stored_images=stored_images,
                    stored_bytes=stored_bytes,
                    max_images=max_images,
                    max_image_bytes=max_image_bytes,
                    max_total_bytes=max_total_bytes,
                )
                if budget_skip_reason is not None:
                    result.skipped.append(
                        _skip(
                            page_index=page_no,
                            image_name=image_name,
                            reason=budget_skip_reason,
                        )
                    )
                    continue

                digest = hashlib.sha256(image_bytes).hexdigest()
                storage_path, storage_uri = _materialize_image_bytes(
                    output_root=output_root,
                    source_component=source_component,
                    page_no=page_no,
                    ordinal=ordinal,
                    digest=digest,
                    suffix=suffix,
                    image_bytes=image_bytes,
                )
                asset = build_document_image_asset_payload(
                    storage_path=storage_path,
                    image_fingerprint=f'sha256:{digest}',
                    page_no=page_no,
                    ordinal=ordinal,
                    text=page_text_by_index.get(page_no, ''),
                    backend='pypdf',
                    origin_reference=image_name,
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    extra_metadata={'extractor': 'pypdf'},
                )
                asset['storage_uri'] = storage_uri
                asset['local_path'] = asset['storage_path']
                result.assets_by_page.setdefault(page_no, []).append(asset)
                stored_images += 1
                stored_bytes += image_size
            except Exception as exc:
                result.skipped.append(
                    _skip(
                        page_index=page_no,
                        image_name=image_name,
                        reason='pdf_image_materialization_error',
                        error=exc,
                    )
                )

    return result


def _extract_image_payload(image_file: Any, image_name: str) -> tuple[bytes, str, str | None, int | None, int | None]:
    pil_image = getattr(image_file, 'image', None)
    width = getattr(pil_image, 'width', None)
    height = getattr(pil_image, 'height', None)

    data = getattr(image_file, 'data', None)
    if isinstance(data, bytes) and data:
        suffix = _image_suffix(image_name=image_name, image_bytes=data, pil_image=pil_image)
        mime_type = _image_mime_type(image_name=image_name, suffix=suffix, pil_image=pil_image)
        return data, suffix, mime_type, width, height

    if pil_image is None or not callable(getattr(pil_image, 'save', None)):
        return b'', '.bin', None, width, height

    with io.BytesIO() as buffer:
        pil_image.save(buffer, format='PNG')
        return buffer.getvalue(), '.png', 'image/png', width, height


def _materialize_image_bytes(
    *,
    output_root: Path,
    source_component: str,
    page_no: int,
    ordinal: int,
    digest: str,
    suffix: str,
    image_bytes: bytes,
) -> tuple[Path, str]:
    source_dir = output_root / source_component
    source_dir.mkdir(parents=True, exist_ok=True)
    filename = f'page-{page_no:03d}-image-{ordinal:03d}-{digest[:16]}{suffix}'
    storage_path = source_dir / filename
    storage_filename = f'pdf-image-assets/{source_component}/{filename}'
    upload_parent = Path(UPLOAD_DIR) / storage_filename
    upload_parent.parent.mkdir(parents=True, exist_ok=True)

    if storage_path.exists():
        storage_uri = _upload_image_bytes(
            image_bytes=image_bytes,
            storage_filename=storage_filename,
            backend='pypdf',
        )
        return storage_path.resolve(), storage_uri

    temp_path = source_dir / f'.tmp-{uuid4().hex}'
    try:
        temp_path.write_bytes(image_bytes)
        temp_path.replace(storage_path)
    finally:
        temp_path.unlink(missing_ok=True)

    storage_uri = _upload_image_bytes(
        image_bytes=image_bytes,
        storage_filename=storage_filename,
        backend='pypdf',
    )
    return storage_path.resolve(), storage_uri


def _upload_image_bytes(*, image_bytes: bytes, storage_filename: str, backend: str) -> str:
    _, storage_uri = Storage.upload_file(
        io.BytesIO(image_bytes),
        storage_filename,
        tags={'asset_kind': 'document_image', 'backend': backend},
    )
    return storage_uri


def _image_name(image_file: Any, *, ordinal: int) -> str:
    name = getattr(image_file, 'name', None)
    if isinstance(name, str) and name.strip():
        return name
    return f'image-{ordinal}'


def _image_suffix(*, image_name: str, image_bytes: bytes, pil_image: Any) -> str:
    suffix = Path(image_name).suffix.lower()
    if suffix in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.tif'}:
        return suffix
    if image_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if image_bytes.startswith(b'\xff\xd8'):
        return '.jpg'
    if image_bytes.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    if image_bytes.startswith(b'RIFF') and image_bytes[8:12] == b'WEBP':
        return '.webp'
    image_format = getattr(pil_image, 'format', None)
    if isinstance(image_format, str) and image_format:
        return f'.{image_format.lower().replace("jpeg", "jpg")}'
    return '.bin'


def _image_mime_type(*, image_name: str, suffix: str, pil_image: Any) -> str | None:
    for value in (image_name, f'image{suffix}'):
        mime_type = mimetypes.guess_type(value)[0]
        if mime_type and mime_type.startswith('image/'):
            return mime_type
    image_format = getattr(pil_image, 'format', None)
    if isinstance(image_format, str) and image_format:
        normalized = image_format.lower().replace('jpeg', 'jpg')
        return 'image/jpeg' if normalized == 'jpg' else f'image/{normalized}'
    return None


def _safe_component(value: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9._-]+', '-', value).strip('-')
    return normalized or 'source'


def _pdf_image_budget_skip_reason(
    *,
    image_size: int,
    stored_images: int,
    stored_bytes: int,
    max_images: int,
    max_image_bytes: int,
    max_total_bytes: int,
) -> str | None:
    if image_size > max_image_bytes:
        return 'pdf_image_asset_too_large'
    if stored_images >= max_images:
        return 'pdf_image_asset_limit_exceeded'
    if stored_bytes + image_size > max_total_bytes:
        return 'pdf_image_asset_total_bytes_exceeded'
    return None


def _budget(value: int) -> int:
    return max(0, int(value))


def _skip(
    *,
    reason: str,
    page_index: int | None = None,
    image_name: str | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    skipped: dict[str, Any] = {
        'backend': 'pypdf',
        'reason': reason,
    }
    if page_index is not None:
        skipped['page_index'] = page_index
    if image_name is not None:
        skipped['image_name'] = image_name
    if error is not None:
        skipped['error'] = type(error).__name__
        message = str(error)
        if message:
            skipped['message'] = message
    return skipped
