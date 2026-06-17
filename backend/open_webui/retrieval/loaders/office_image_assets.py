from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from open_webui.config import UPLOAD_DIR
from open_webui.retrieval.document_image_assets import (
    DocumentImageAssetPayload,
    build_document_image_asset_payload,
)

log = logging.getLogger(__name__)

_OFFICE_IMAGE_PREFIXES = ('word/media/', 'ppt/media/', 'xl/media/')
_OPENDOCUMENT_IMAGE_PREFIX = 'pictures/'
_IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff'}


@dataclass
class OfficeImageAssetExtraction:
    assets: list[DocumentImageAssetPayload] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)


def extract_office_image_assets(
    file_path: str | Path,
    *,
    asset_root: str | Path | None = None,
    source_id: str | None = None,
    document_text: str | None = None,
) -> OfficeImageAssetExtraction:
    source_path = Path(file_path)
    output_root = Path(asset_root) if asset_root else Path(UPLOAD_DIR) / 'office-image-assets'
    source_component = _safe_component(source_id or source_path.stem)
    result = OfficeImageAssetExtraction()

    try:
        with zipfile.ZipFile(source_path) as archive:
            entries = list(archive.infolist())
            for entry in entries:
                if entry.is_dir() or not _is_image_asset_entry(entry.filename):
                    continue

                try:
                    image_bytes = archive.read(entry)
                except Exception as exc:
                    result.skipped.append(
                        _skip(
                            origin_reference=entry.filename,
                            reason='office_zip_entry_read_error',
                            error=exc,
                        )
                    )
                    continue

                image_info = _identify_image(entry.filename, image_bytes)
                if image_info is None:
                    result.skipped.append(
                        _skip(
                            origin_reference=entry.filename,
                            reason='office_zip_unrecognized_image',
                        )
                    )
                    continue

                suffix, mime_type = image_info
                digest = hashlib.sha256(image_bytes).hexdigest()
                sequence = len(result.assets) + 1
                try:
                    storage_path = _materialize_image_bytes(
                        output_root=output_root,
                        source_component=source_component,
                        sequence=sequence,
                        digest=digest,
                        suffix=suffix,
                        image_bytes=image_bytes,
                    )
                    result.assets.append(
                        build_document_image_asset_payload(
                            storage_path=storage_path,
                            image_fingerprint=f'sha256:{digest}',
                            page_no=sequence,
                            ordinal=1,
                            text=document_text or '',
                            backend='office_zip',
                            origin_reference=entry.filename,
                            mime_type=mime_type,
                            extra_metadata={
                                'extractor': 'zipfile',
                                'sequence': sequence,
                            },
                        )
                    )
                except Exception as exc:
                    result.skipped.append(
                        _skip(
                            origin_reference=entry.filename,
                            reason='office_zip_image_materialization_error',
                            error=exc,
                        )
                    )
    except zipfile.BadZipFile as exc:
        result.skipped.append(_skip(reason='office_zip_read_error', error=exc))
    except OSError as exc:
        result.skipped.append(_skip(reason='office_zip_read_error', error=exc))

    return result


def _is_image_asset_entry(entry_name: str) -> bool:
    normalized = entry_name.replace('\\', '/').lstrip('/').lower()
    if normalized.startswith(_OFFICE_IMAGE_PREFIXES):
        return True
    return normalized.startswith(_OPENDOCUMENT_IMAGE_PREFIX)


def _identify_image(entry_name: str, image_bytes: bytes) -> tuple[str, str] | None:
    if not image_bytes:
        return None

    detected = _detect_image_type(image_bytes)
    if detected is None:
        return None

    suffix, mime_type = detected
    entry_suffix = Path(entry_name).suffix.lower()
    if entry_suffix in _IMAGE_SUFFIXES and _mime_type_for_suffix(entry_suffix) == mime_type:
        suffix = '.jpg' if entry_suffix == '.jpeg' else entry_suffix
    return suffix, mime_type


def _detect_image_type(image_bytes: bytes) -> tuple[str, str] | None:
    if image_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png', 'image/png'
    if image_bytes.startswith(b'\xff\xd8'):
        return '.jpg', 'image/jpeg'
    if image_bytes.startswith((b'GIF87a', b'GIF89a')):
        return '.gif', 'image/gif'
    if image_bytes.startswith(b'RIFF') and image_bytes[8:12] == b'WEBP':
        return '.webp', 'image/webp'
    if image_bytes.startswith(b'BM'):
        return '.bmp', 'image/bmp'
    if image_bytes.startswith((b'II*\x00', b'MM\x00*')):
        return '.tiff', 'image/tiff'
    return None


def _mime_type_for_suffix(suffix: str) -> str | None:
    if suffix == '.jpg':
        return 'image/jpeg'
    return mimetypes.guess_type(f'image{suffix}')[0]


def _materialize_image_bytes(
    *,
    output_root: Path,
    source_component: str,
    sequence: int,
    digest: str,
    suffix: str,
    image_bytes: bytes,
) -> Path:
    source_dir = output_root / source_component
    source_dir.mkdir(parents=True, exist_ok=True)
    storage_path = source_dir / f'image-{sequence:03d}-{digest[:16]}{suffix}'
    if storage_path.exists():
        return storage_path.resolve()

    temp_path = source_dir / f'.tmp-{uuid4().hex}'
    try:
        temp_path.write_bytes(image_bytes)
        temp_path.replace(storage_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return storage_path.resolve()


def _safe_component(value: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9._-]+', '-', value).strip('-')
    return normalized or 'source'


def _skip(
    *,
    reason: str,
    origin_reference: str | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    skipped: dict[str, Any] = {
        'backend': 'office_zip',
        'reason': reason,
    }
    if origin_reference is not None:
        skipped['origin_reference'] = origin_reference
    if error is not None:
        skipped['error'] = type(error).__name__
        message = str(error)
        if message:
            skipped['message'] = message
    return skipped
