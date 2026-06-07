from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from uuid import uuid4

DocumentImageAssetPayload = dict[str, Any]


@dataclass(frozen=True)
class MaterializedImage:
    storage_path: Path
    sha256: str
    width: int | None
    height: int | None


class ImageAssetMaterializer:
    def __init__(self, asset_root: Path, *, download_timeout_s: float = 30.0) -> None:
        self._asset_root = Path(asset_root)
        self._download_timeout_s = download_timeout_s

    def materialize(
        self,
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
        source_dir = self._asset_root / _safe_component(source_id)
        source_dir.mkdir(parents=True, exist_ok=True)
        temp_path = source_dir / f'.tmp-{uuid4().hex}'
        try:
            with urlrequest.urlopen(remote_uri, timeout=self._download_timeout_s) as response:
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
            filename = f'page-{page_no:03d}-image-{ordinal:03d}-{sha256[:16]}{suffix}'
            storage_path = source_dir / filename
            if storage_path.exists():
                temp_path.unlink(missing_ok=True)
            else:
                temp_path.replace(storage_path)

            width, height = _probe_image_dimensions(storage_path)
            return MaterializedImage(
                storage_path=storage_path.resolve(),
                sha256=sha256,
                width=width,
                height=height,
            )
        except (OSError, urlerror.URLError, ValueError):
            temp_path.unlink(missing_ok=True)
            return None


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
    image_asset_roots: Iterable[Path] | None = None,
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

        block_id = f'page-{page_no:03d}-image-{ordinal:03d}'
        metadata = {
            'backend': backend,
            'page': page_no,
            'origin_reference': reference,
            **(extra_metadata or {}),
        }
        if width is not None:
            metadata['width'] = width
        if height is not None:
            metadata['height'] = height

        assets.append(
            {
                'storage_path': str(storage_path),
                'asset_kind': 'document_image',
                'image_fingerprint': image_fingerprint,
                'page_index': page_no,
                'caption': _first_non_empty_line(text),
                'surrounding_text': _short_context(text),
                'anchor': {'page': page_no, 'block_id': block_id},
                'origin_uri': origin_uri,
                'metadata': metadata,
            }
        )
    return assets, skipped


def render_pdf_page_snapshots(
    *,
    source_path: Path,
    source_id: str,
    asset_root: Path,
    pages: Iterable[int] | None = None,
    scale: float = 2.0,
) -> list[DocumentImageAssetPayload]:
    try:
        import pypdfium2
    except ImportError:
        pypdfium2 = None

    if pypdfium2 is None:
        return _render_pdf_page_snapshots_with_pymupdf(
            source_path=source_path,
            source_id=source_id,
            asset_root=asset_root,
            pages=pages,
            scale=scale,
        )
    return _render_pdf_page_snapshots_with_pdfium(
        pypdfium2=pypdfium2,
        source_path=source_path,
        source_id=source_id,
        asset_root=asset_root,
        pages=pages,
        scale=scale,
    )


def _render_pdf_page_snapshots_with_pdfium(
    *,
    pypdfium2: Any,
    source_path: Path,
    source_id: str,
    asset_root: Path,
    pages: Iterable[int] | None,
    scale: float,
) -> list[DocumentImageAssetPayload]:
    pdf = pypdfium2.PdfDocument(str(source_path))
    assets: list[DocumentImageAssetPayload] = []
    source_dir = Path(asset_root) / _safe_component(source_id)
    source_dir.mkdir(parents=True, exist_ok=True)
    page_numbers = list(pages) if pages is not None else list(range(1, len(pdf) + 1))
    try:
        for page_no in page_numbers:
            if page_no < 1 or page_no > len(pdf):
                continue
            page = pdf[page_no - 1]
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                storage_path = (source_dir / f'page-{page_no:03d}-snapshot.png').resolve()
                image.save(storage_path)
            finally:
                close = getattr(page, 'close', None)
                if callable(close):
                    close()

            width, height = _probe_image_dimensions(storage_path)
            block_id = f'page-{page_no:03d}-snapshot'
            assets.append(
                {
                    'storage_path': str(storage_path),
                    'asset_kind': 'page_snapshot',
                    'image_fingerprint': _fingerprint_file(storage_path),
                    'page_index': page_no,
                    'caption': None,
                    'surrounding_text': None,
                    'anchor': {'page': page_no, 'block_id': block_id},
                    'origin_uri': str(source_path),
                    'metadata': {
                        'backend': 'pdf-page-snapshot',
                        'page': page_no,
                        'origin_reference': str(source_path),
                        **({'width': width} if width is not None else {}),
                        **({'height': height} if height is not None else {}),
                    },
                }
            )
    finally:
        close = getattr(pdf, 'close', None)
        if callable(close):
            close()
    return assets


def _render_pdf_page_snapshots_with_pymupdf(
    *,
    source_path: Path,
    source_id: str,
    asset_root: Path,
    pages: Iterable[int] | None,
    scale: float,
) -> list[DocumentImageAssetPayload]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError('PDF page snapshot extraction requires pypdfium2 or PyMuPDF') from exc

    pdf = fitz.open(str(source_path))
    assets: list[DocumentImageAssetPayload] = []
    source_dir = Path(asset_root) / _safe_component(source_id)
    source_dir.mkdir(parents=True, exist_ok=True)
    page_count = int(getattr(pdf, 'page_count', 0))
    page_numbers = list(pages) if pages is not None else list(range(1, page_count + 1))
    try:
        for page_no in page_numbers:
            if page_no < 1 or page_no > page_count:
                continue
            page = pdf.load_page(page_no - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            storage_path = (source_dir / f'page-{page_no:03d}-snapshot.png').resolve()
            pixmap.save(str(storage_path))
            width = getattr(pixmap, 'width', None)
            height = getattr(pixmap, 'height', None)
            if not isinstance(width, int) or not isinstance(height, int):
                width, height = _probe_image_dimensions(storage_path)

            block_id = f'page-{page_no:03d}-snapshot'
            assets.append(
                {
                    'storage_path': str(storage_path),
                    'asset_kind': 'page_snapshot',
                    'image_fingerprint': _fingerprint_file(storage_path),
                    'page_index': page_no,
                    'caption': None,
                    'surrounding_text': None,
                    'anchor': {'page': page_no, 'block_id': block_id},
                    'origin_uri': str(source_path),
                    'metadata': {
                        'backend': 'pdf-page-snapshot',
                        'page': page_no,
                        'origin_reference': str(source_path),
                        **({'width': width} if isinstance(width, int) else {}),
                        **({'height': height} if isinstance(height, int) else {}),
                    },
                }
            )
    finally:
        close = getattr(pdf, 'close', None)
        if callable(close):
            close()
    return assets


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
    image_asset_roots: Iterable[Path] | None = None,
) -> Path | None:
    for value in (reference, origin_uri):
        if not value or _is_remote_uri(value):
            continue
        path = Path(value)
        candidates: list[Path] = []
        if path.is_absolute():
            candidates.append(path)
        for asset_root in image_asset_roots or []:
            candidates.append(Path(asset_root) / path)
        candidates.append(Path(source_path).parent / path)
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
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
