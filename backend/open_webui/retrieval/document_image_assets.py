"""Materialize parser-owned images once; never fetch links from document text.

The caller supplies a file-scoped source ID and a private asset root. Returned
paths are server metadata, not public URLs or model tool arguments. Authorization
and deletion remain responsibilities of the file/evidence lifecycle.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import requests
from PIL import Image

DocumentImageAssetPayload = dict[str, Any]
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_BYTES = 100 * 1024 * 1024
MAX_DOCUMENT_IMAGES = 200
MAX_IMAGE_PIXELS = 40_000_000
_FORMATS = {
    'PNG': ('png', 'image/png'),
    'JPEG': ('jpg', 'image/jpeg'),
    'WEBP': ('webp', 'image/webp'),
    'GIF': ('gif', 'image/gif'),
    'TIFF': ('tiff', 'image/tiff'),
    'BMP': ('bmp', 'image/bmp'),
}


@dataclass
class ImageAssetExtraction:
    assets: list[DocumentImageAssetPayload] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)


def safe_relative_reference(value: str) -> str:
    """A parser image key may label an image, but must never become a path."""
    decoded = unquote(value).replace('\\', '/')
    path = PurePosixPath(decoded)
    if not decoded or path.is_absolute() or '..' in path.parts or ':' in decoded or '\x00' in decoded:
        raise ValueError('unsafe_image_reference')
    return str(path)


def _origin(value: str) -> tuple[str, str, int]:
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('invalid_resource_origin')
    if parts.hostname.lower() in {'169.254.169.254', '169.254.170.2', '100.100.100.200', 'metadata.google.internal'}:
        raise ValueError('metadata_resource_forbidden')
    return parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == 'https' else 80)


def download_service_resource(
    url: str, *, allowed_origins: tuple[str, ...], max_bytes: int, timeout: float = 30
) -> bytes:
    """Only exact administrator-selected origins; no cookies, auth, or redirects.

    Private origins are allowed because the configured OCR may run on the LAN.
    An external object-storage origin must be explicitly configured, not inferred
    from markdown or from another response URL.
    """
    if _origin(url) not in {_origin(origin) for origin in allowed_origins}:
        raise ValueError('resource_origin_not_allowed')
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, timeout=timeout, stream=True, allow_redirects=False) as response:
            if 300 <= response.status_code < 400:
                raise ValueError('resource_redirect_not_allowed')
            response.raise_for_status()
            if int(response.headers.get('Content-Length', 0)) > max_bytes:
                raise ValueError('resource_too_large')
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError('resource_too_large')
                chunks.append(chunk)
            return b''.join(chunks)


class ImageAssetStore:
    def __init__(
        self,
        root: str | Path,
        source_id: str,
        *,
        max_image_bytes: int = MAX_IMAGE_BYTES,
        max_document_bytes: int = MAX_DOCUMENT_BYTES,
        max_images: int = MAX_DOCUMENT_IMAGES,
    ):
        # Hash file identity rather than accepting path components from callers.
        self.root = Path(root).resolve() / hashlib.sha256(source_id.encode()).hexdigest()[:32]
        self.max_image_bytes = max_image_bytes
        self.max_document_bytes = max_document_bytes
        self.max_images = max_images
        self.count = 0
        self.attempts = 0
        self.bytes = 0

    def put(
        self,
        data: bytes,
        *,
        backend: str,
        origin_reference: str,
        page_index: int | None = None,
        anchor: dict | None = None,
        caption: str = '',
        ocr_text: str = '',
        surrounding_text: str = '',
    ) -> dict:
        if self.count >= self.max_images or self.bytes + len(data) > self.max_document_bytes:
            raise ValueError('document_image_budget_exceeded')
        if not data or len(data) > self.max_image_bytes:
            raise ValueError('image_size_exceeded')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS or image.format not in _FORMATS:
                    raise ValueError('unsupported_image_or_pixel_limit')
                suffix, mime = _FORMATS[image.format]
                image.verify()
        if page_index is not None and (type(page_index) is not int or page_index < 0):
            raise ValueError('invalid_page_index')
        digest = hashlib.sha256(data).hexdigest()
        if self.root.is_symlink():
            raise ValueError('asset_symlink_forbidden')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = self.root / f'{digest}.{suffix}'
        if destination.is_symlink():
            raise ValueError('asset_symlink_forbidden')
        if not destination.exists():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.root, prefix='.image-', delete=False) as output:
                    temporary = output.name
                    output.write(data)
                os.replace(temporary, destination)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        self.count += 1
        self.bytes += len(data)
        return {
            'storage_uri': str(destination),
            'storage_path': str(destination),
            'sha256': digest,
            'mime_type': mime,
            'width': width,
            'height': height,
            'page_index': page_index,
            'anchor': anchor,
            'caption': caption[:4096],
            'ocr_text': ocr_text[:8192],
            'surrounding_text': surrounding_text[:8192],
            'backend': backend,
            'origin_reference': origin_reference,
            'status': 'ready',
        }


def _image_resource_bytes(value, *, store, resource_base_url, allowed_origins, timeout):
    if not isinstance(value, str) or not value:
        raise ValueError('image_resource_missing')
    # Raw JPEG base64 starts with /9j/, which is not a resource URL.
    if value.startswith(('http://', 'https://')):
        return download_service_resource(
            urljoin(resource_base_url, value),
            allowed_origins=allowed_origins,
            max_bytes=min(store.max_image_bytes, store.max_document_bytes - store.bytes),
            timeout=timeout,
        )
    encoded = value
    if value.startswith('data:'):
        header, encoded = value.split(',', 1)
        if not header.startswith('data:image/') or not header.endswith(';base64'):
            raise ValueError('invalid_image_data_uri')
    if len(encoded) > (store.max_image_bytes + 2) // 3 * 4:
        raise ValueError('image_size_exceeded')
    return base64.b64decode(encoded, validate=True)


def extract_markdown_image_assets(
    markdown: dict,
    *,
    store: ImageAssetStore,
    page_index: int | None,
    backend: str,
    resource_base_url: str,
    allowed_origins: tuple[str, ...],
    timeout: float = 30,
) -> ImageAssetExtraction:
    """Consume explicit markdown.images data, never scrape URLs from markdown."""
    result = ImageAssetExtraction()
    text = markdown.get('text') if isinstance(markdown.get('text'), str) else ''
    images = markdown.get('images')
    if images is None:
        # The absence may be intentional on text-only pages. A markdown image
        # with no resource is an explicit unavailable asset, not a usable image.
        if '![' in text or '<img' in text.lower():
            result.skipped.append({'page_index': page_index, 'reason': 'parser_image_resources_missing'})
        return result
    if not isinstance(images, dict):
        result.skipped.append({'page_index': page_index, 'reason': 'invalid_parser_images'})
        return result
    for ordinal, (reference, value) in enumerate(images.items()):
        if store.attempts >= store.max_images or store.bytes >= store.max_document_bytes:
            result.skipped.append({'page_index': page_index, 'reason': 'document_image_budget_exceeded'})
            break
        store.attempts += 1
        try:
            reference = safe_relative_reference(reference)
            data = _image_resource_bytes(
                value,
                store=store,
                resource_base_url=resource_base_url,
                allowed_origins=allowed_origins,
                timeout=timeout,
            )
            # Caption is only existing alt text; no generation or OCR.
            match = re.search(r'!\[([^\]]*)\]\(\s*' + re.escape(reference) + r'\s*\)', text)
            asset = store.put(
                data,
                backend=backend,
                origin_reference=reference,
                page_index=page_index,
                caption=match.group(1) if match else '',
                surrounding_text=text,
            )
            result.assets.append(asset)
        except Exception as exc:
            # Do not persist signed resource URLs or service response bodies.
            reason = (
                str(exc) if isinstance(exc, ValueError) and str(exc).isidentifier() else 'image_materialization_failed'
            )
            result.skipped.append({'page_index': page_index, 'reason': reason, 'ordinal': ordinal})
    return result
