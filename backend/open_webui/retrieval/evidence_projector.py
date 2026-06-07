from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
import hashlib
import mimetypes
from pathlib import Path
import time
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_db_context
from open_webui.models.evidence import (
    ASSET_KINDS,
    KnowledgeEvidence,
    KnowledgeEvidenceAsset,
    compute_knowledge_evidence_asset_id,
    compute_knowledge_evidence_asset_ref,
    compute_knowledge_evidence_id,
    compute_knowledge_evidence_ref,
)
from open_webui.models.files import File, FileModel
from open_webui.models.retrieval_chunks import RetrievalChunk


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_image_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower().startswith("image/")


def _preview_text(content_text: str | None, limit: int = 240) -> str | None:
    if not content_text:
        return None
    text = " ".join(content_text.split())
    return text[:limit]


def _source_name_for_file(file: FileModel) -> str:
    if file.meta and isinstance(file.meta, dict):
        name = file.meta.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return file.filename


@asynccontextmanager
async def _session_scope(db: AsyncSession | None):
    if isinstance(db, AsyncSession):
        yield db
    else:
        async with get_async_db_context(db) as session:
            yield session


@dataclass
class EvidenceProjectionResult:
    scanned_chunks: int = 0
    text_evidence_upserted: int = 0
    image_assets_upserted: int = 0
    image_evidence_upserted: int = 0
    document_image_placeholders: int = 0
    failed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    asset_refs: list[str] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


async def backfill_text_evidence_from_active_chunks(
    *,
    collection_ids: Sequence[str] | None = None,
    knowledge_ids: Sequence[str] | None = None,
    file_ids: Sequence[str] | None = None,
    projection_profile: str = "text_only",
    projection_config_hash: str = "text-backfill-v1",
    db: AsyncSession | None = None,
) -> EvidenceProjectionResult:
    result = EvidenceProjectionResult()
    async with _session_scope(db) as session:
        stmt = select(RetrievalChunk).where(RetrievalChunk.is_active.is_(True))
        if collection_ids:
            stmt = stmt.where(
                or_(
                    RetrievalChunk.collection_id.in_(list(collection_ids)),
                    RetrievalChunk.collection_name.in_(list(collection_ids)),
                )
            )
        if knowledge_ids:
            stmt = stmt.where(RetrievalChunk.knowledge_id.in_(list(knowledge_ids)))
        if file_ids:
            stmt = stmt.where(RetrievalChunk.file_id.in_(list(file_ids)))
        stmt = stmt.order_by(RetrievalChunk.row_id.asc())
        rows = (await session.execute(stmt)).scalars().all()

        result.scanned_chunks = len(rows)
        for row in rows:
            metadata = _as_dict(getattr(row, "metadata_", None))
            knowledge_id = row.knowledge_id or row.collection_id or row.collection_name
            file_id = row.file_id
            if not knowledge_id or not file_id:
                result.failed += 1
                result.failures.append(
                    {
                        "stage": "text_backfill",
                        "error": "retrieval_chunk is missing knowledge_id or file_id",
                        "chunk_uid": row.chunk_uid,
                    }
                )
                continue

            try:
                evidence = await _upsert_evidence(
                    session,
                    knowledge_id=knowledge_id,
                    file_id=file_id,
                    asset_id=None,
                    retrieval_chunk_uid=row.chunk_uid,
                    retrieval_chunk_row_id=row.row_id,
                    modality="text",
                    evidence_kind="text_chunk",
                    title=metadata.get("title") if isinstance(metadata.get("title"), str) else None,
                    content_text=row.text,
                    preview_text=_preview_text(row.text),
                    source_name=str(metadata.get("name") or row.collection_name or file_id or row.chunk_uid),
                    page_index=_coerce_int(metadata.get("page_index")),
                    anchor_json=(
                        metadata.get("anchor_json") if isinstance(metadata.get("anchor_json"), dict) else metadata
                    ),
                    chunk_index=row.chunk_index or 0,
                    chunk_total=max(1, int(metadata.get("chunk_total") or 1)),
                    content_hash=row.content_hash,
                    projection_profile=projection_profile,
                    projection_config_hash=projection_config_hash,
                    is_active=True,
                    deleted_at=None,
                )
                result.text_evidence_upserted += 1
                result.evidence_refs.append(evidence.evidence_ref)
            except Exception as exc:
                result.failed += 1
                result.failures.append(
                    {
                        "stage": "text_backfill",
                        "error": str(exc),
                        "chunk_uid": row.chunk_uid,
                        "file_id": file_id,
                    }
                )

    return result


async def project_standalone_image_evidence(
    *,
    knowledge_id: str,
    file: FileModel,
    projection_profile: str = "unified_multimodal_dense",
    projection_config_hash: str = "image-project-v1",
    db: AsyncSession | None = None,
) -> EvidenceProjectionResult:
    result = EvidenceProjectionResult()
    metadata = _as_dict(file.meta)
    content_type = metadata.get("content_type") if isinstance(metadata.get("content_type"), str) else None
    if not _is_image_content_type(content_type) and not _looks_like_image_filename(file.filename):
        result.document_image_placeholders += 1
        return result

    sha256 = file.hash or file.id
    storage_uri = file.path or file.id
    asset_ref = compute_knowledge_evidence_asset_ref(
        knowledge_id=knowledge_id,
        file_id=file.id,
        asset_kind="standalone_image",
        sha256=sha256,
    )

    async with _session_scope(db) as session:
        asset = await _upsert_asset(
            session,
            knowledge_id=knowledge_id,
            file_id=file.id,
            asset_kind="standalone_image",
            mime_type=content_type or mimetypes.guess_type(file.filename)[0] or "image/png",
            storage_uri=storage_uri,
            sha256=sha256,
            caption=_string_or_none(metadata.get("caption")),
            ocr_text=_string_or_none(metadata.get("ocr_text")),
            surrounding_text=_string_or_none(metadata.get("surrounding_text")),
            width=_coerce_int(metadata.get("width")),
            height=_coerce_int(metadata.get("height")),
            page_index=_coerce_int(metadata.get("page_index")),
            bbox_json=metadata.get("bbox_json") if isinstance(metadata.get("bbox_json"), dict) else None,
            anchor_json=(
                metadata.get("anchor_json") if isinstance(metadata.get("anchor_json"), dict) else metadata or None
            ),
            status="ready",
            asset_ref=asset_ref,
        )
        result.image_assets_upserted += 1
        result.asset_refs.append(asset.asset_ref)

        content_text = _build_image_content_text(file, metadata)
        evidence = await _upsert_evidence(
            session,
            knowledge_id=knowledge_id,
            file_id=file.id,
            asset_id=asset.id,
            retrieval_chunk_uid=None,
            retrieval_chunk_row_id=None,
            modality="image",
            evidence_kind="standalone_image",
            title=_string_or_none(metadata.get("caption")) or file.filename,
            content_text=content_text,
            preview_text=_preview_text(content_text),
            source_name=_source_name_for_file(file),
            page_index=_coerce_int(metadata.get("page_index")),
            anchor_json=(
                metadata.get("anchor_json") if isinstance(metadata.get("anchor_json"), dict) else metadata or None
            ),
            chunk_index=1,
            chunk_total=1,
            content_hash=sha256,
            projection_profile=projection_profile,
            projection_config_hash=projection_config_hash,
            asset_ref=asset.asset_ref,
            is_active=True,
            deleted_at=None,
        )
        result.image_evidence_upserted += 1
        result.evidence_refs.append(evidence.evidence_ref)
    return result


async def project_evidence_for_knowledge_file(
    *,
    knowledge_id: str,
    file_id: str,
    project_document_images: bool = False,
    db: AsyncSession | None = None,
) -> EvidenceProjectionResult:
    async with _session_scope(db) as session:
        file = await _get_file_by_id(session, file_id)
    if file is None:
        raise ValueError(f"file {file_id!r} not found")

    result = EvidenceProjectionResult()
    metadata = _as_dict(file.meta)
    content_type = metadata.get("content_type") if isinstance(metadata.get("content_type"), str) else None

    text_result = await backfill_text_evidence_from_active_chunks(
        file_ids=[file_id],
        db=db,
    )
    _merge_projection_result(result, text_result)

    if _is_image_content_type(content_type) or _looks_like_image_filename(file.filename):
        image_result = await project_standalone_image_evidence(
            knowledge_id=knowledge_id,
            file=file,
            db=db,
        )
        _merge_projection_result(result, image_result)
    elif project_document_images:
        image_result = await project_document_image_assets_evidence(
            knowledge_id=knowledge_id,
            file=file,
            db=db,
        )
        _merge_projection_result(result, image_result)
        if image_result.image_assets_upserted == 0 and image_result.image_evidence_upserted == 0:
            result.document_image_placeholders += 1

    return result


async def project_document_image_assets_evidence(
    *,
    knowledge_id: str,
    file: FileModel,
    projection_profile: str = "unified_multimodal_dense",
    projection_config_hash: str = "document-image-project-v1",
    db: AsyncSession | None = None,
) -> EvidenceProjectionResult:
    result = EvidenceProjectionResult()
    assets = _normalize_document_image_assets(file)
    if not assets:
        return result

    async with _session_scope(db) as session:
        chunk_total = len(assets)
        for index, asset_input in enumerate(assets, start=1):
            storage_uri = _string_or_none(asset_input.get("storage_uri"))
            if not storage_uri:
                result.failed += 1
                result.failures.append(
                    {
                        "stage": "document_image_project",
                        "error": "document image asset is missing storage_uri",
                        "file_id": file.id,
                        "asset_index": index,
                    }
                )
                continue

            sha256 = _document_asset_fingerprint(asset_input, storage_uri=storage_uri)
            asset_kind = _normalize_asset_kind(asset_input.get("asset_kind"))
            anchor_json = _as_dict(asset_input.get("anchor_json")) or None
            bbox_json = _as_dict(asset_input.get("bbox_json")) or None
            page_index = _coerce_int(asset_input.get("page_index"))
            mime_type = (
                _string_or_none(asset_input.get("mime_type")) or mimetypes.guess_type(storage_uri)[0] or "image/png"
            )

            try:
                asset = await _upsert_asset(
                    session,
                    knowledge_id=knowledge_id,
                    file_id=file.id,
                    asset_kind=asset_kind,
                    mime_type=mime_type,
                    storage_uri=storage_uri,
                    sha256=sha256,
                    caption=_string_or_none(asset_input.get("caption")),
                    ocr_text=_string_or_none(asset_input.get("ocr_text")),
                    surrounding_text=_string_or_none(asset_input.get("surrounding_text")),
                    width=_coerce_int(asset_input.get("width")),
                    height=_coerce_int(asset_input.get("height")),
                    page_index=page_index,
                    bbox_json=bbox_json,
                    anchor_json=anchor_json,
                    status="ready",
                )
                result.image_assets_upserted += 1
                result.asset_refs.append(asset.asset_ref)

                content_text = _build_document_image_content_text(file, asset_input)
                evidence = await _upsert_evidence(
                    session,
                    knowledge_id=knowledge_id,
                    file_id=file.id,
                    asset_id=asset.id,
                    retrieval_chunk_uid=None,
                    retrieval_chunk_row_id=None,
                    modality="image",
                    evidence_kind=_evidence_kind_for_asset_kind(asset_kind),
                    title=_document_image_title(file, asset_input, index=index),
                    content_text=content_text,
                    preview_text=_preview_text(content_text),
                    source_name=_source_name_for_file(file),
                    page_index=page_index,
                    anchor_json=anchor_json,
                    chunk_index=index,
                    chunk_total=chunk_total,
                    content_hash=sha256,
                    projection_profile=projection_profile,
                    projection_config_hash=projection_config_hash,
                    asset_ref=asset.asset_ref,
                    is_active=True,
                    deleted_at=None,
                )
                result.image_evidence_upserted += 1
                result.evidence_refs.append(evidence.evidence_ref)
            except Exception as exc:
                result.failed += 1
                result.failures.append(
                    {
                        "stage": "document_image_project",
                        "error": str(exc),
                        "file_id": file.id,
                        "asset_index": index,
                    }
                )
    return result


async def project_evidence_from_job_payload(
    job_payload: dict[str, Any],
    *,
    db: AsyncSession | None = None,
) -> EvidenceProjectionResult:
    knowledge_id = _string_or_none(job_payload.get("knowledge_id")) or _string_or_none(job_payload.get("collection_id"))
    if not knowledge_id:
        raise ValueError("evidence projection job payload requires knowledge_id or collection_id")

    file_ids = _normalize_string_sequence(job_payload.get("file_ids"))
    project_document_images = bool(job_payload.get("project_document_images", False))

    result = EvidenceProjectionResult()
    if file_ids:
        for file_id in file_ids:
            file_result = await project_evidence_for_knowledge_file(
                knowledge_id=knowledge_id,
                file_id=file_id,
                project_document_images=project_document_images,
                db=db,
            )
            _merge_projection_result(result, file_result)
    else:
        text_result = await backfill_text_evidence_from_active_chunks(
            collection_ids=[knowledge_id],
            db=db,
        )
        _merge_projection_result(result, text_result)

    return result


def _merge_projection_result(target: EvidenceProjectionResult, source: EvidenceProjectionResult) -> None:
    target.scanned_chunks += source.scanned_chunks
    target.text_evidence_upserted += source.text_evidence_upserted
    target.image_assets_upserted += source.image_assets_upserted
    target.image_evidence_upserted += source.image_evidence_upserted
    target.document_image_placeholders += source.document_image_placeholders
    target.failed += source.failed
    target.failures.extend(source.failures)
    target.evidence_refs.extend(source.evidence_refs)
    target.asset_refs.extend(source.asset_refs)


def _build_image_content_text(file: FileModel, metadata: dict[str, Any]) -> str:
    parts: list[str] = [file.filename]
    caption = _string_or_none(metadata.get("caption"))
    if caption:
        parts.append(caption)
    ocr_text = _string_or_none(metadata.get("ocr_text"))
    if ocr_text:
        parts.append(f"OCR: {ocr_text}")
    surrounding_text = _string_or_none(metadata.get("surrounding_text"))
    if surrounding_text:
        parts.append(f"Context: {surrounding_text}")
    return " | ".join(parts)


def _build_document_image_content_text(file: FileModel, asset: dict[str, Any]) -> str:
    parts: list[str] = [file.filename]
    figure_label = _string_or_none(asset.get("figure_label"))
    if figure_label:
        parts.append(figure_label)
    caption = _string_or_none(asset.get("caption"))
    if caption:
        parts.append(caption)
    page_index = _coerce_int(asset.get("page_index"))
    if page_index is not None:
        parts.append(f"Page: {page_index}")
    ocr_text = _string_or_none(asset.get("ocr_text"))
    if ocr_text:
        parts.append(f"OCR: {ocr_text}")
    surrounding_text = _string_or_none(asset.get("surrounding_text"))
    if surrounding_text:
        parts.append(f"Context: {surrounding_text}")
    return " | ".join(parts)


def _document_image_title(file: FileModel, asset: dict[str, Any], *, index: int) -> str:
    return (
        _string_or_none(asset.get("figure_label"))
        or _string_or_none(asset.get("caption"))
        or f"{file.filename} image {index}"
    )


def _normalize_document_image_assets(file: FileModel) -> list[dict[str, Any]]:
    containers = [_as_dict(file.meta), _as_dict(file.data)]
    raw_assets: Any = None
    for container in containers:
        raw_assets = container.get("document_image_assets")
        if raw_assets is None:
            raw_assets = container.get("image_assets")
        if raw_assets is not None:
            break
    if not isinstance(raw_assets, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        asset = dict(item)
        metadata = _as_dict(asset.get("metadata"))
        anchor = _as_dict(asset.get("anchor"))

        storage_uri = (
            _string_or_none(asset.get("storage_uri"))
            or _string_or_none(asset.get("storage_path"))
            or _string_or_none(asset.get("path"))
        )
        if storage_uri:
            asset["storage_uri"] = storage_uri
        if "mime_type" not in asset:
            asset["mime_type"] = _string_or_none(metadata.get("mime_type"))
        if "width" not in asset:
            asset["width"] = metadata.get("width")
        if "height" not in asset:
            asset["height"] = metadata.get("height")
        if "page_index" not in asset:
            asset["page_index"] = (
                asset.get("page") or anchor.get("page") or metadata.get("page") or metadata.get("page_index")
            )
        if "anchor_json" not in asset and anchor:
            asset["anchor_json"] = anchor
        if "bbox_json" not in asset and isinstance(metadata.get("bbox"), dict):
            asset["bbox_json"] = metadata.get("bbox")
        if "origin_reference" not in asset:
            asset["origin_reference"] = _string_or_none(metadata.get("origin_reference"))
        if "asset_kind" not in asset:
            asset["asset_kind"] = metadata.get("asset_kind")
        normalized.append(asset)
    return normalized


def _normalize_asset_kind(value: Any) -> str:
    asset_kind = _string_or_none(value) or "document_image"
    return asset_kind if asset_kind in ASSET_KINDS else "document_image"


def _evidence_kind_for_asset_kind(asset_kind: str) -> str:
    if asset_kind == "figure":
        return "figure"
    if asset_kind == "region":
        return "page_region"
    return "document_image"


def _document_asset_fingerprint(asset: dict[str, Any], *, storage_uri: str) -> str:
    for key in ("sha256", "hash"):
        value = _string_or_none(asset.get(key))
        if value:
            return _strip_sha256_prefix(value)
    image_fingerprint = _string_or_none(asset.get("image_fingerprint"))
    if image_fingerprint:
        return _strip_sha256_prefix(image_fingerprint)
    try:
        path = Path(storage_uri)
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        pass
    return hashlib.sha256(storage_uri.encode("utf-8")).hexdigest()


def _strip_sha256_prefix(value: str) -> str:
    return value.split("sha256:", 1)[1] if value.startswith("sha256:") else value


def _looks_like_image_filename(filename: str | None) -> bool:
    if not filename:
        return False
    guessed, _ = mimetypes.guess_type(filename)
    return bool(guessed and guessed.startswith("image/"))


def _normalize_string_sequence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _get_file_by_id(session: AsyncSession, file_id: str) -> FileModel | None:
    result = await session.execute(select(File).filter_by(id=file_id))
    row = result.scalars().first()
    return FileModel.model_validate(row) if row else None


async def _upsert_asset(
    session: AsyncSession,
    *,
    knowledge_id: str,
    file_id: str,
    asset_kind: str,
    mime_type: str,
    storage_uri: str,
    sha256: str,
    caption: str | None = None,
    ocr_text: str | None = None,
    surrounding_text: str | None = None,
    width: int | None = None,
    height: int | None = None,
    page_index: int | None = None,
    bbox_json: dict[str, Any] | None = None,
    anchor_json: dict[str, Any] | None = None,
    status: str = "ready",
    error: str | None = None,
    asset_ref: str | None = None,
) -> KnowledgeEvidenceAsset:
    now = int(time.time())
    asset_ref = asset_ref or compute_knowledge_evidence_asset_ref(
        knowledge_id=knowledge_id,
        file_id=file_id,
        asset_kind=asset_kind,
        sha256=sha256,
        page_index=page_index,
        bbox_json=bbox_json,
        anchor_json=anchor_json,
    )
    asset_id = compute_knowledge_evidence_asset_id(
        knowledge_id=knowledge_id,
        file_id=file_id,
        asset_kind=asset_kind,
        sha256=sha256,
        page_index=page_index,
        bbox_json=bbox_json,
        anchor_json=anchor_json,
    )
    result = await session.execute(select(KnowledgeEvidenceAsset).filter_by(id=asset_id))
    row = result.scalars().first()
    if row is None:
        row = KnowledgeEvidenceAsset(
            id=asset_id,
            knowledge_id=knowledge_id,
            file_id=file_id,
            asset_ref=asset_ref,
            asset_kind=asset_kind,
            mime_type=mime_type,
            storage_uri=storage_uri,
            sha256=sha256,
            width=width,
            height=height,
            page_index=page_index,
            bbox_json=bbox_json,
            anchor_json=anchor_json,
            caption=caption,
            ocr_text=ocr_text,
            surrounding_text=surrounding_text,
            status=status,
            error=error,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.knowledge_id = knowledge_id
        row.file_id = file_id
        row.asset_ref = asset_ref
        row.asset_kind = asset_kind
        row.mime_type = mime_type
        row.storage_uri = storage_uri
        row.sha256 = sha256
        row.width = width
        row.height = height
        row.page_index = page_index
        row.bbox_json = bbox_json
        row.anchor_json = anchor_json
        row.caption = caption
        row.ocr_text = ocr_text
        row.surrounding_text = surrounding_text
        row.status = status
        row.error = error
        row.updated_at = now

    await session.commit()
    await session.refresh(row)
    return row


async def _upsert_evidence(
    session: AsyncSession,
    *,
    knowledge_id: str,
    file_id: str,
    asset_id: str | None,
    retrieval_chunk_uid: str | None,
    retrieval_chunk_row_id: int | None,
    modality: str,
    evidence_kind: str,
    title: str | None,
    content_text: str | None,
    preview_text: str | None,
    source_name: str,
    page_index: int | None,
    anchor_json: dict[str, Any] | None,
    chunk_index: int,
    chunk_total: int,
    content_hash: str,
    projection_profile: str,
    projection_config_hash: str,
    asset_ref: str | None = None,
    is_active: bool = True,
    deleted_at: int | None = None,
) -> KnowledgeEvidence:
    now = int(time.time())
    evidence_ref = compute_knowledge_evidence_ref(
        knowledge_id=knowledge_id,
        file_id=file_id,
        modality=modality,
        evidence_kind=evidence_kind,
        content_hash=content_hash,
        projection_config_hash=projection_config_hash,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
        retrieval_chunk_uid=retrieval_chunk_uid,
        asset_ref=asset_ref,
        page_index=page_index,
    )
    evidence_id = compute_knowledge_evidence_id(evidence_ref=evidence_ref)
    result = await session.execute(select(KnowledgeEvidence).filter_by(id=evidence_id))
    row = result.scalars().first()
    if row is None:
        row = KnowledgeEvidence(
            id=evidence_id,
            evidence_ref=evidence_ref,
            knowledge_id=knowledge_id,
            file_id=file_id,
            asset_id=asset_id,
            retrieval_chunk_uid=retrieval_chunk_uid,
            retrieval_chunk_row_id=retrieval_chunk_row_id,
            modality=modality,
            evidence_kind=evidence_kind,
            title=title,
            content_text=content_text,
            preview_text=preview_text,
            source_name=source_name,
            page_index=page_index,
            anchor_json=anchor_json,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            content_hash=content_hash,
            projection_profile=projection_profile,
            projection_config_hash=projection_config_hash,
            is_active=is_active,
            deleted_at=deleted_at,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.evidence_ref = evidence_ref
        row.knowledge_id = knowledge_id
        row.file_id = file_id
        row.asset_id = asset_id
        row.retrieval_chunk_uid = retrieval_chunk_uid
        row.retrieval_chunk_row_id = retrieval_chunk_row_id
        row.modality = modality
        row.evidence_kind = evidence_kind
        row.title = title
        row.content_text = content_text
        row.preview_text = preview_text
        row.source_name = source_name
        row.page_index = page_index
        row.anchor_json = anchor_json
        row.chunk_index = chunk_index
        row.chunk_total = chunk_total
        row.content_hash = content_hash
        row.projection_profile = projection_profile
        row.projection_config_hash = projection_config_hash
        row.is_active = is_active
        row.deleted_at = deleted_at
        row.updated_at = now

    await session.commit()
    await session.refresh(row)
    return row
