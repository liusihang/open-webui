from __future__ import annotations

import hashlib
import ipaddress
import mimetypes
import re
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from uuid import uuid4

DocumentImageAssetPayload = dict[str, Any]
DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_DOCUMENT_IMAGE_DOWNLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_DOCUMENT_IMAGE_DOWNLOADS = 200
DEFAULT_MAX_REMOTE_REDIRECTS = 5
_METADATA_IPS = {
    ipaddress.ip_address('169.254.169.254'),
    ipaddress.ip_address('169.254.170.2'),
    ipaddress.ip_address('100.100.100.200'),
}


@dataclass(frozen=True)
class MaterializedImage:
    storage_path: Path
    sha256: str
    width: int | None
    height: int | None


class ImageAssetMaterializer:
    def __init__(
        self,
        asset_root: Path,
        *,
        download_timeout_s: float = 30.0,
        allowed_remote_origins: Sequence[str] | None = None,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES,
        max_document_images: int = DEFAULT_MAX_DOCUMENT_IMAGE_DOWNLOADS,
        max_document_bytes: int = DEFAULT_MAX_DOCUMENT_IMAGE_DOWNLOAD_BYTES,
    ) -> None:
        self._asset_root = Path(asset_root)
        self._download_timeout_s = download_timeout_s
        self._allowed_remote_origins = tuple(allowed_remote_origins or ())
        self._max_image_bytes = max(0, int(max_image_bytes))
        self._max_document_images = max(0, int(max_document_images))
        self._max_document_bytes = max(0, int(max_document_bytes))
        self._materialized_images = 0
        self._materialized_bytes = 0

    def materialize(
        self,
        source_id: str,
        page_no: int,
        ordinal: int,
        reference: str,
        origin_uri: str | None,
    ) -> MaterializedImage | None:
        remote_uri = _resolve_remote_uri(reference=reference, origin_uri=origin_uri)
        if remote_uri is None:
            return None
        return self._download_and_materialize(
            source_id=source_id,
            page_no=page_no,
            ordinal=ordinal,
            reference=reference,
            remote_uri=remote_uri,
        )

    def _download_and_materialize(
        self,
        *,
        source_id: str,
        page_no: int,
        ordinal: int,
        reference: str,
        remote_uri: str,
    ) -> MaterializedImage | None:
        if self._materialized_images >= self._max_document_images:
            return None
        max_bytes = min(self._max_image_bytes, self._max_document_bytes - self._materialized_bytes)
        if max_bytes <= 0:
            return None

        source_dir = self._asset_root / _safe_component(source_id)
        source_dir.mkdir(parents=True, exist_ok=True)
        temp_path = source_dir / f'.tmp-{uuid4().hex}'
        try:
            with self._open_validated_remote(remote_uri) as response:
                if not _response_fits_limit(response=response, max_bytes=max_bytes):
                    return None
                digest, downloaded_bytes = _write_limited_response(
                    response=response,
                    destination=temp_path,
                    max_bytes=max_bytes,
                )
                if digest is None:
                    return None

            sha256 = digest.hexdigest()
            suffix = _select_suffix(reference=reference, remote_uri=remote_uri, downloaded_path=temp_path)
            filename = f'page-{page_no:03d}-image-{ordinal:03d}-{sha256[:16]}{suffix}'
            storage_path = source_dir / filename
            if storage_path.exists():
                temp_path.unlink(missing_ok=True)
            else:
                temp_path.replace(storage_path)

            width, height = _probe_image_dimensions(storage_path)
            materialized = MaterializedImage(
                storage_path=storage_path.resolve(),
                sha256=sha256,
                width=width,
                height=height,
            )
            self._materialized_images += 1
            self._materialized_bytes += downloaded_bytes
            return materialized
        except (OSError, urlerror.URLError, ValueError):
            temp_path.unlink(missing_ok=True)
            return None

    def _open_validated_remote(self, remote_uri: str):
        current_uri = remote_uri
        for _ in range(DEFAULT_MAX_REMOTE_REDIRECTS + 1):
            current_uri = validate_remote_download_url(
                current_uri,
                allowed_remote_origins=self._allowed_remote_origins,
            )
            response = _urlopen_no_redirect(current_uri, timeout=self._download_timeout_s)
            status_code = _response_status_code(response)
            if status_code is not None and 300 <= status_code < 400:
                location = _response_header(response, 'Location')
                _close_response(response)
                if not location:
                    raise ValueError('redirect response missing Location header')
                current_uri = urlparse.urljoin(current_uri, location)
                continue
            if status_code is not None and status_code >= 400:
                _close_response(response)
                raise ValueError(f'remote download failed with status {status_code}')
            return response
        raise ValueError('remote redirect limit exceeded')


def build_image_assets_from_markdown(
    *,
    source_path: Path,
    source_id: str,
    markdown: dict[str, Any],
    page_no: int,
    materializer: ImageAssetMaterializer | None = None,
    markdown_text: str | None = None,
    backend: str = 'paddleocr-vl',
    extra_metadata: dict[str, Any] | None = None,
    image_asset_roots: list[Path] | None = None,
) -> tuple[list[DocumentImageAssetPayload], list[dict[str, str]]]:
    text = markdown_text if markdown_text is not None else markdown.get('text', '')
    if not isinstance(text, str):
        text = str(text)

    assets: list[DocumentImageAssetPayload] = []
    skipped: list[dict[str, str]] = []
    for ordinal, (reference, origin_uri) in enumerate(
        _iter_markdown_image_entries(markdown.get('images')),
        start=1,
    ):
        materialized = None
        storage_path = _resolve_image_storage_path(
            source_path=source_path,
            reference=reference,
            origin_uri=origin_uri,
            image_asset_roots=image_asset_roots,
        )
        if storage_path is None and materializer is not None:
            materialized = materializer.materialize(
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
            f'sha256:{materialized.sha256}' if materialized is not None else _fingerprint_file(storage_path)
        )
        width = materialized.width if materialized is not None else None
        height = materialized.height if materialized is not None else None
        if width is None or height is None:
            width, height = _probe_image_dimensions(storage_path)

        asset = build_document_image_asset_payload(
            storage_path=storage_path,
            image_fingerprint=image_fingerprint,
            page_no=page_no,
            ordinal=ordinal,
            text=text,
            backend=backend,
            origin_reference=reference,
            origin_uri=origin_uri,
            width=width,
            height=height,
            extra_metadata=extra_metadata,
        )
        assets.append(asset)
    return assets, skipped


def build_document_image_asset_payload(
    *,
    storage_path: Path,
    page_no: int,
    ordinal: int,
    text: str | None,
    backend: str,
    image_fingerprint: str | None = None,
    origin_reference: str | None = None,
    origin_uri: str | None = None,
    mime_type: str | None = None,
    width: int | None = None,
    height: int | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> DocumentImageAssetPayload:
    storage_path = Path(storage_path)
    image_fingerprint = image_fingerprint or _fingerprint_file(storage_path)
    width, height = _resolve_image_dimensions(storage_path=storage_path, width=width, height=height)
    mime_type = mime_type or mimetypes.guess_type(str(storage_path))[0]
    text = text or ''
    block_id = f'page-{page_no:03d}-image-{ordinal:03d}'
    metadata = {
        'backend': backend,
        'page': page_no,
        **(extra_metadata or {}),
        **_present_values(
            {
                'origin_reference': origin_reference,
                'mime_type': mime_type,
                'width': width,
                'height': height,
            }
        ),
    }

    asset: DocumentImageAssetPayload = {
        'storage_path': str(storage_path),
        'asset_kind': 'document_image',
        'image_fingerprint': image_fingerprint,
        'page_index': page_no,
        'caption': _first_non_empty_line(text),
        'surrounding_text': _short_context(text),
        'anchor': {'page': page_no, 'block_id': block_id},
        'metadata': metadata,
        **_present_values(
            {
                'origin_uri': origin_uri,
                'mime_type': mime_type,
                'width': width,
                'height': height,
            }
        ),
    }
    return asset


def _resolve_image_dimensions(
    *,
    storage_path: Path,
    width: int | None,
    height: int | None,
) -> tuple[int | None, int | None]:
    if width is not None and height is not None:
        return width, height
    probed_width, probed_height = _probe_image_dimensions(storage_path)
    return width if width is not None else probed_width, height if height is not None else probed_height


def _present_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _iter_markdown_image_entries(images: Any) -> list[tuple[str, str | None]]:
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


def _resolve_image_storage_path(
    *,
    source_path: Path,
    reference: str,
    origin_uri: str | None,
    image_asset_roots: list[Path] | None = None,
) -> Path | None:
    for value in (reference, origin_uri):
        if not value or _is_remote_uri(value):
            continue
        path = Path(value)
        if path.is_absolute():
            continue
        candidates: list[tuple[Path, Path]] = []
        for asset_root in image_asset_roots or []:
            root = Path(asset_root)
            candidates.append((root / path, root))
        source_root = Path(source_path).parent
        candidates.append((source_root / path, source_root))
        for candidate, root in candidates:
            resolved = _resolve_contained_file(candidate=candidate, root=root)
            if resolved is not None:
                return resolved
    return None


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
    parsed = urlparse.urlparse(value)
    return parsed.scheme in {'http', 'https'} and bool(parsed.netloc)


def _resolve_contained_file(*, candidate: Path, root: Path) -> Path | None:
    try:
        resolved_root = Path(root).resolve(strict=True)
        resolved_candidate = Path(candidate).resolve(strict=True)
    except OSError:
        return None
    if not resolved_candidate.is_file():
        return None
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved_candidate


def validate_remote_download_url(url: str, *, allowed_remote_origins: Sequence[str]) -> str:
    parsed = urlparse.urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.hostname is None:
        raise ValueError('remote download URL must be absolute http/https')

    allowed_origin_keys = {_remote_origin_key(origin) for origin in allowed_remote_origins}
    allowed_origin_keys.discard(None)
    if not allowed_origin_keys or _remote_origin_key(url) not in allowed_origin_keys:
        raise ValueError('remote origin is not allowed')

    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        resolved_ips = _resolve_host_ips(parsed.hostname, port)
    except OSError as exc:
        raise ValueError('remote host could not be resolved') from exc
    if not resolved_ips:
        raise ValueError('remote host did not resolve')
    for ip_text in resolved_ips:
        ip = ipaddress.ip_address(ip_text)
        if _is_blocked_remote_ip(ip):
            raise ValueError('remote host resolved to a blocked address')
    return url


def _remote_origin_key(url: str) -> tuple[str, str, int] | None:
    parsed = urlparse.urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    return parsed.scheme, parsed.hostname.lower().rstrip('.'), port


def _resolve_host_ips(hostname: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _is_blocked_remote_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or ip in _METADATA_IPS
    )


class _NoRedirectHTTPRedirectHandler(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _NoHTTPErrorProcessor(urlrequest.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def _urlopen_no_redirect(url: str, *, timeout: float):
    opener = urlrequest.build_opener(_NoRedirectHTTPRedirectHandler, _NoHTTPErrorProcessor)
    return opener.open(url, timeout=timeout)


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


def _response_header(response: object, name: str) -> str | None:
    getter = getattr(response, 'getheader', None)
    if callable(getter):
        value = getter(name)
        if isinstance(value, str):
            return value
    headers = getattr(response, 'headers', None)
    if isinstance(headers, dict):
        value = headers.get(name) or headers.get(name.lower())
        if isinstance(value, str):
            return value
    return None


def _response_content_length(response: object) -> int | None:
    value = _response_header(response, 'Content-Length')
    if value is None:
        return None
    try:
        content_length = int(value)
    except ValueError:
        return None
    return content_length if content_length >= 0 else None


def _response_fits_limit(*, response: object, max_bytes: int) -> bool:
    content_length = _response_content_length(response)
    return content_length is None or content_length <= max_bytes


def _write_limited_response(*, response: object, destination: Path, max_bytes: int) -> tuple[Any | None, int]:
    digest = hashlib.sha256()
    downloaded_bytes = 0
    with destination.open('wb') as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            downloaded_bytes += len(chunk)
            if downloaded_bytes > max_bytes:
                destination.unlink(missing_ok=True)
                return None, downloaded_bytes
            digest.update(chunk)
            handle.write(chunk)
    return digest, downloaded_bytes


def _close_response(response: object) -> None:
    close = getattr(response, 'close', None)
    if callable(close):
        close()


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
        from PIL import Image

        with Image.open(path) as image:
            return image.width, image.height
    except (OSError, ImportError):
        return None, None
    return None, None
