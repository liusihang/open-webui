from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Index, Integer, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, JSONField, get_async_db_context


ASSET_KINDS = ("standalone_image", "document_image", "figure", "region")
ASSET_VARIANT_KINDS = ("original", "thumbnail", "model_default", "model_openai_low", "model_anthropic_default", "region_crop")
ASSET_STATUSES = ("ready", "failed", "stale", "deleted")
EVIDENCE_MODALITIES = ("text", "image")
EVIDENCE_KINDS = ("text_chunk", "standalone_image", "document_image", "figure", "page_region")
VECTOR_BACKENDS = ("pgvector", "opensearch")
VECTOR_ROLES = (
    "text_chunk_dense",
    "image_dense",
    "image_caption_dense",
)
EMBEDDING_FORMATS = ("single_dense", "multi_vector", "sparse")
EMBEDDING_STATUSES = ("pending", "indexing", "ready", "failed", "stale")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_digest(prefix: str, payload: dict[str, Any]) -> str:
    return _sha256_hex(f"open-webui:evidence:{prefix}:{_canonical_json(payload)}")


def _stable_uuid(prefix: str, payload: dict[str, Any]) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-webui:evidence:{prefix}:{_canonical_json(payload)}"))


def _suffix(payload: dict[str, Any], length: int = 12) -> str:
    return _stable_digest("suffix", payload)[:length]


def compute_knowledge_evidence_asset_ref(
    *,
    knowledge_id: str,
    file_id: str,
    asset_kind: str,
    sha256: str,
    page_index: int | None = None,
    bbox_json: dict[str, Any] | None = None,
    anchor_json: dict[str, Any] | None = None,
) -> str:
    payload = {
        "knowledge_id": knowledge_id,
        "file_id": file_id,
        "asset_kind": asset_kind,
        "sha256": sha256,
        "page_index": page_index,
        "bbox_json": bbox_json,
        "anchor_json": anchor_json,
    }
    return f"ka:{knowledge_id}:{file_id}:{asset_kind}:{_suffix(payload)}"


def compute_knowledge_evidence_asset_id(
    *,
    knowledge_id: str,
    file_id: str,
    asset_kind: str,
    sha256: str,
    page_index: int | None = None,
    bbox_json: dict[str, Any] | None = None,
    anchor_json: dict[str, Any] | None = None,
) -> str:
    payload = {
        "knowledge_id": knowledge_id,
        "file_id": file_id,
        "asset_kind": asset_kind,
        "sha256": sha256,
        "page_index": page_index,
        "bbox_json": bbox_json,
        "anchor_json": anchor_json,
    }
    return _stable_uuid("asset", payload)


def compute_knowledge_evidence_asset_variant_id(
    *,
    asset_id: str,
    variant_kind: str,
    transform_config_hash: str,
) -> str:
    return _stable_uuid(
        "asset_variant",
        {
            "asset_id": asset_id,
            "variant_kind": variant_kind,
            "transform_config_hash": transform_config_hash,
        },
    )


def compute_knowledge_evidence_ref(
    *,
    knowledge_id: str,
    file_id: str,
    modality: str,
    evidence_kind: str,
    content_hash: str,
    projection_config_hash: str,
    chunk_index: int,
    chunk_total: int,
    retrieval_chunk_uid: str | None = None,
    asset_ref: str | None = None,
    page_index: int | None = None,
) -> str:
    payload = {
        "knowledge_id": knowledge_id,
        "file_id": file_id,
        "modality": modality,
        "evidence_kind": evidence_kind,
        "content_hash": content_hash,
        "projection_config_hash": projection_config_hash,
        "chunk_index": int(chunk_index or 0),
        "chunk_total": int(chunk_total or 1),
        "retrieval_chunk_uid": retrieval_chunk_uid,
        "asset_ref": asset_ref,
        "page_index": page_index,
    }
    return f"ke:{knowledge_id}:{file_id}:{evidence_kind}:{int(chunk_index or 0)}:{_suffix(payload)}"


def compute_knowledge_evidence_id(
    *,
    evidence_ref: str,
) -> str:
    return _stable_uuid("evidence", {"evidence_ref": evidence_ref})


def compute_knowledge_vector_space_id(
    *,
    knowledge_id: str,
    retrieval_profile: str,
    embedding_model: str,
    projection_config_hash: str,
    distance_metric: str,
    vector_backend: str,
) -> str:
    payload = {
        "knowledge_id": knowledge_id,
        "retrieval_profile": retrieval_profile,
        "embedding_model": embedding_model,
        "projection_config_hash": projection_config_hash,
        "distance_metric": distance_metric,
        "vector_backend": vector_backend,
    }
    return f"kvs:{knowledge_id}:{retrieval_profile}:{_suffix(payload)}"


def compute_knowledge_evidence_embedding_id(
    *,
    evidence_ref: str,
    vector_space_id: str,
    vector_role: str,
    vector_backend_collection: str,
) -> str:
    return _stable_uuid(
        "embedding",
        {
            "evidence_ref": evidence_ref,
            "vector_space_id": vector_space_id,
            "vector_role": vector_role,
            "vector_backend_collection": vector_backend_collection,
        },
    )


def _normalize_asset_kind(asset_kind: str) -> str:
    normalized = (asset_kind or "").strip().lower()
    if normalized not in ASSET_KINDS:
        raise ValueError(f"Unsupported evidence asset kind: {asset_kind}")
    return normalized


def _normalize_asset_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized not in ASSET_STATUSES:
        raise ValueError(f"Unsupported evidence asset status: {status}")
    return normalized


def _normalize_evidence_modality(modality: str) -> str:
    normalized = (modality or "").strip().lower()
    if normalized not in EVIDENCE_MODALITIES:
        raise ValueError(f"Unsupported evidence modality: {modality}")
    return normalized


def _normalize_evidence_kind(evidence_kind: str) -> str:
    normalized = (evidence_kind or "").strip().lower()
    if normalized not in EVIDENCE_KINDS:
        raise ValueError(f"Unsupported evidence kind: {evidence_kind}")
    return normalized


def _normalize_variant_kind(variant_kind: str) -> str:
    normalized = (variant_kind or "").strip().lower()
    if normalized not in ASSET_VARIANT_KINDS:
        raise ValueError(f"Unsupported evidence variant kind: {variant_kind}")
    return normalized


def _normalize_embedding_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized not in EMBEDDING_STATUSES:
        raise ValueError(f"Unsupported evidence embedding status: {status}")
    return normalized


def _normalize_embedding_format(embedding_format: str) -> str:
    normalized = (embedding_format or "").strip().lower()
    if normalized not in EMBEDDING_FORMATS:
        raise ValueError(f"Unsupported evidence embedding format: {embedding_format}")
    return normalized


def _normalize_vector_role(vector_role: str) -> str:
    normalized = (vector_role or "").strip().lower()
    if normalized not in VECTOR_ROLES:
        raise ValueError(f"Unsupported evidence vector role: {vector_role}")
    return normalized


class KnowledgeEvidenceAsset(Base):
    __tablename__ = "knowledge_evidence_asset"

    id = Column(Text, primary_key=True, unique=True)
    knowledge_id = Column(Text, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False)
    file_id = Column(Text, ForeignKey("file.id", ondelete="CASCADE"), nullable=False)
    asset_ref = Column(Text, nullable=False, unique=True)
    asset_kind = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=False)
    storage_uri = Column(Text, nullable=False)
    sha256 = Column(Text, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    page_index = Column(Integer, nullable=True)
    bbox_json = Column(JSONField, nullable=True)
    anchor_json = Column(JSONField, nullable=True)
    caption = Column(Text, nullable=True)
    ocr_text = Column(Text, nullable=True)
    surrounding_text = Column(Text, nullable=True)
    status = Column(Text, nullable=False)
    error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_knowledge_evidence_asset_knowledge_id", "knowledge_id"),
        Index("ix_knowledge_evidence_asset_file_id", "file_id"),
        Index("ix_knowledge_evidence_asset_status", "status"),
    )


class KnowledgeEvidenceAssetModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    knowledge_id: str
    file_id: str
    asset_ref: str
    asset_kind: Literal["standalone_image", "document_image", "figure", "region"]
    mime_type: str
    storage_uri: str
    sha256: str
    width: int | None = None
    height: int | None = None
    page_index: int | None = None
    bbox_json: dict[str, Any] | None = None
    anchor_json: dict[str, Any] | None = None
    caption: str | None = None
    ocr_text: str | None = None
    surrounding_text: str | None = None
    status: Literal["ready", "failed", "stale", "deleted"]
    error: str | None = None
    created_at: int
    updated_at: int


class KnowledgeEvidenceAssetVariant(Base):
    __tablename__ = "knowledge_evidence_asset_variant"

    id = Column(Text, primary_key=True, unique=True)
    asset_id = Column(Text, ForeignKey("knowledge_evidence_asset.id", ondelete="CASCADE"), nullable=False)
    variant_kind = Column(Text, nullable=False)
    storage_uri = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    byte_size = Column(Integer, nullable=False)
    transform_config_hash = Column(Text, nullable=False)
    expires_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "variant_kind",
            "transform_config_hash",
            name="uq_knowledge_evidence_asset_variant_identity",
        ),
        Index("ix_knowledge_evidence_asset_variant_asset_id", "asset_id"),
        Index("ix_knowledge_evidence_asset_variant_variant_kind", "variant_kind"),
    )


class KnowledgeEvidenceAssetVariantModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    asset_id: str
    variant_kind: Literal["original", "thumbnail", "model_default", "model_openai_low", "model_anthropic_default", "region_crop"]
    storage_uri: str
    mime_type: str
    width: int | None = None
    height: int | None = None
    byte_size: int
    transform_config_hash: str
    expires_at: int | None = None
    created_at: int
    updated_at: int


class KnowledgeEvidence(Base):
    __tablename__ = "knowledge_evidence"

    id = Column(Text, primary_key=True, unique=True)
    evidence_ref = Column(Text, nullable=False, unique=True)
    knowledge_id = Column(Text, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False)
    file_id = Column(Text, ForeignKey("file.id", ondelete="CASCADE"), nullable=False)
    asset_id = Column(Text, ForeignKey("knowledge_evidence_asset.id", ondelete="CASCADE"), nullable=True)
    retrieval_chunk_uid = Column(Text, nullable=True)
    retrieval_chunk_row_id = Column(Integer, nullable=True)
    modality = Column(Text, nullable=False)
    evidence_kind = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    content_text = Column(Text, nullable=True)
    preview_text = Column(Text, nullable=True)
    source_name = Column(Text, nullable=False)
    page_index = Column(Integer, nullable=True)
    anchor_json = Column(JSONField, nullable=True)
    chunk_index = Column(Integer, nullable=False)
    chunk_total = Column(Integer, nullable=False)
    content_hash = Column(Text, nullable=False)
    projection_profile = Column(Text, nullable=False)
    projection_config_hash = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    deleted_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_knowledge_evidence_knowledge_id", "knowledge_id"),
        Index("ix_knowledge_evidence_file_id", "file_id"),
        Index("ix_knowledge_evidence_asset_id", "asset_id"),
        Index("ix_knowledge_evidence_retrieval_chunk_uid", "retrieval_chunk_uid"),
        Index("ix_knowledge_evidence_retrieval_chunk_row_id", "retrieval_chunk_row_id"),
    )


class KnowledgeEvidenceModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    evidence_ref: str
    knowledge_id: str
    file_id: str
    asset_id: str | None = None
    retrieval_chunk_uid: str | None = None
    retrieval_chunk_row_id: int | None = None
    modality: Literal["text", "image"]
    evidence_kind: Literal["text_chunk", "standalone_image", "document_image", "figure", "page_region"]
    title: str | None = None
    content_text: str | None = None
    preview_text: str | None = None
    source_name: str
    page_index: int | None = None
    anchor_json: dict[str, Any] | None = None
    chunk_index: int
    chunk_total: int
    content_hash: str
    projection_profile: str
    projection_config_hash: str
    is_active: bool = True
    deleted_at: int | None = None
    created_at: int
    updated_at: int


class KnowledgeVectorSpace(Base):
    __tablename__ = "knowledge_vector_space"

    id = Column(Text, primary_key=True, unique=True)
    knowledge_id = Column(Text, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False)
    retrieval_profile = Column(Text, nullable=False)
    embedding_model = Column(Text, nullable=False)
    embedding_dim = Column(Integer, nullable=True)
    distance_metric = Column(Text, nullable=False)
    vector_backend = Column(Text, nullable=False)
    supports_text_query = Column(Boolean, nullable=False, default=True)
    supports_image_query = Column(Boolean, nullable=False, default=True)
    supports_text_evidence = Column(Boolean, nullable=False, default=True)
    supports_image_evidence = Column(Boolean, nullable=False, default=True)
    supports_multivector = Column(Boolean, nullable=False, default=False)
    projection_config_hash = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "knowledge_id",
            "retrieval_profile",
            "projection_config_hash",
            name="uq_knowledge_vector_space_identity",
        ),
        Index("ix_knowledge_vector_space_knowledge_id", "knowledge_id"),
        Index("ix_knowledge_vector_space_active", "active"),
    )


class KnowledgeVectorSpaceModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    knowledge_id: str
    retrieval_profile: str
    embedding_model: str
    embedding_dim: int | None = None
    distance_metric: str
    vector_backend: str
    supports_text_query: bool = True
    supports_image_query: bool = True
    supports_text_evidence: bool = True
    supports_image_evidence: bool = True
    supports_multivector: bool = False
    projection_config_hash: str
    active: bool = True
    created_at: int
    updated_at: int


class KnowledgeEvidenceEmbedding(Base):
    __tablename__ = "knowledge_evidence_embedding"

    id = Column(Text, primary_key=True, unique=True)
    evidence_id = Column(Text, ForeignKey("knowledge_evidence.id", ondelete="CASCADE"), nullable=False)
    evidence_ref = Column(Text, nullable=False)
    vector_space_id = Column(Text, ForeignKey("knowledge_vector_space.id", ondelete="CASCADE"), nullable=False)
    vector_backend_collection = Column(Text, nullable=False)
    vector_backend_id = Column(Text, nullable=True)
    vector_role = Column(Text, nullable=False)
    embedding_format = Column(Text, nullable=False)
    embedding_status = Column(Text, nullable=False)
    embedding_error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "evidence_id",
            "vector_space_id",
            "vector_role",
            "vector_backend_collection",
            name="uq_knowledge_evidence_embedding_identity",
        ),
        Index("ix_knowledge_evidence_embedding_evidence_id", "evidence_id"),
        Index("ix_knowledge_evidence_embedding_evidence_ref", "evidence_ref"),
        Index("ix_knowledge_evidence_embedding_vector_space_id", "vector_space_id"),
        Index("ix_knowledge_evidence_embedding_status", "embedding_status"),
    )


class KnowledgeEvidenceEmbeddingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    evidence_id: str
    evidence_ref: str
    vector_space_id: str
    vector_backend_collection: str
    vector_backend_id: str | None = None
    vector_role: Literal["text_chunk_dense", "image_dense", "image_caption_dense"]
    embedding_format: Literal["single_dense", "multi_vector", "sparse"]
    embedding_status: Literal["pending", "indexing", "ready", "failed", "stale"]
    embedding_error: str | None = None
    created_at: int
    updated_at: int


class KnowledgeEvidenceAssetTable:
    async def create_asset(
        self,
        *,
        knowledge_id: str,
        file_id: str,
        asset_kind: str,
        mime_type: str,
        storage_uri: str,
        sha256: str,
        width: int | None = None,
        height: int | None = None,
        page_index: int | None = None,
        bbox_json: dict[str, Any] | None = None,
        anchor_json: dict[str, Any] | None = None,
        caption: str | None = None,
        ocr_text: str | None = None,
        surrounding_text: str | None = None,
        status: str = "ready",
        error: str | None = None,
        asset_ref: str | None = None,
        id: str | None = None,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceAssetModel:
        asset_kind = _normalize_asset_kind(asset_kind)
        status = _normalize_asset_status(status)
        asset_ref = asset_ref or compute_knowledge_evidence_asset_ref(
            knowledge_id=knowledge_id,
            file_id=file_id,
            asset_kind=asset_kind,
            sha256=sha256,
            page_index=page_index,
            bbox_json=bbox_json,
            anchor_json=anchor_json,
        )
        row_id = id or compute_knowledge_evidence_asset_id(
            knowledge_id=knowledge_id,
            file_id=file_id,
            asset_kind=asset_kind,
            sha256=sha256,
            page_index=page_index,
            bbox_json=bbox_json,
            anchor_json=anchor_json,
        )
        now = int(time.time())
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidenceAsset).filter_by(id=row_id))
            row = result.scalars().first()
            if row is None:
                row = KnowledgeEvidenceAsset(
                    id=row_id,
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
            return KnowledgeEvidenceAssetModel.model_validate(row)

    async def get_asset_by_id(
        self,
        asset_id: str,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceAssetModel | None:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidenceAsset).filter_by(id=asset_id))
            row = result.scalars().first()
            return KnowledgeEvidenceAssetModel.model_validate(row) if row else None

    async def get_asset_by_ref(
        self,
        asset_ref: str,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceAssetModel | None:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidenceAsset).filter_by(asset_ref=asset_ref))
            row = result.scalars().first()
            return KnowledgeEvidenceAssetModel.model_validate(row) if row else None

    async def list_assets(
        self,
        *,
        knowledge_id: str | None = None,
        file_id: str | None = None,
        asset_kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
        db: AsyncSession | None = None,
    ) -> list[KnowledgeEvidenceAssetModel]:
        async with get_async_db_context(db) as session:
            stmt = select(KnowledgeEvidenceAsset)
            if knowledge_id:
                stmt = stmt.where(KnowledgeEvidenceAsset.knowledge_id == knowledge_id)
            if file_id:
                stmt = stmt.where(KnowledgeEvidenceAsset.file_id == file_id)
            if asset_kind:
                stmt = stmt.where(KnowledgeEvidenceAsset.asset_kind == _normalize_asset_kind(asset_kind))
            if status:
                stmt = stmt.where(KnowledgeEvidenceAsset.status == status)
            stmt = stmt.order_by(KnowledgeEvidenceAsset.created_at.asc(), KnowledgeEvidenceAsset.id.asc()).limit(
                max(1, min(int(limit), 500))
            )
            result = await session.execute(stmt)
            return [KnowledgeEvidenceAssetModel.model_validate(row) for row in result.scalars().all()]


class KnowledgeEvidenceAssetVariantTable:
    async def create_variant(
        self,
        *,
        asset_id: str,
        variant_kind: str,
        storage_uri: str,
        mime_type: str,
        width: int | None = None,
        height: int | None = None,
        byte_size: int,
        transform_config_hash: str,
        expires_at: int | None = None,
        id: str | None = None,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceAssetVariantModel:
        normalized_kind = _normalize_variant_kind(variant_kind)
        row_id = id or compute_knowledge_evidence_asset_variant_id(
            asset_id=asset_id,
            variant_kind=normalized_kind,
            transform_config_hash=transform_config_hash,
        )
        now = int(time.time())
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidenceAssetVariant).filter_by(id=row_id))
            row = result.scalars().first()
            if row is None:
                row = KnowledgeEvidenceAssetVariant(
                    id=row_id,
                    asset_id=asset_id,
                    variant_kind=normalized_kind,
                    storage_uri=storage_uri,
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    byte_size=byte_size,
                    transform_config_hash=transform_config_hash,
                    expires_at=expires_at,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.asset_id = asset_id
                row.variant_kind = normalized_kind
                row.storage_uri = storage_uri
                row.mime_type = mime_type
                row.width = width
                row.height = height
                row.byte_size = byte_size
                row.transform_config_hash = transform_config_hash
                row.expires_at = expires_at
                row.updated_at = now

            await session.commit()
            await session.refresh(row)
            return KnowledgeEvidenceAssetVariantModel.model_validate(row)

    async def get_variant_by_id(
        self,
        variant_id: str,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceAssetVariantModel | None:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidenceAssetVariant).filter_by(id=variant_id))
            row = result.scalars().first()
            return KnowledgeEvidenceAssetVariantModel.model_validate(row) if row else None

    async def list_variants(
        self,
        *,
        asset_id: str | None = None,
        variant_kind: str | None = None,
        limit: int = 100,
        db: AsyncSession | None = None,
    ) -> list[KnowledgeEvidenceAssetVariantModel]:
        async with get_async_db_context(db) as session:
            stmt = select(KnowledgeEvidenceAssetVariant)
            if asset_id:
                stmt = stmt.where(KnowledgeEvidenceAssetVariant.asset_id == asset_id)
            if variant_kind:
                stmt = stmt.where(KnowledgeEvidenceAssetVariant.variant_kind == variant_kind.strip().lower())
            stmt = stmt.order_by(
                KnowledgeEvidenceAssetVariant.created_at.asc(), KnowledgeEvidenceAssetVariant.id.asc()
            ).limit(max(1, min(int(limit), 500)))
            result = await session.execute(stmt)
            return [KnowledgeEvidenceAssetVariantModel.model_validate(row) for row in result.scalars().all()]


class KnowledgeEvidenceTable:
    async def create_evidence(
        self,
        *,
        knowledge_id: str,
        file_id: str,
        modality: str,
        evidence_kind: str,
        content_hash: str,
        projection_profile: str,
        projection_config_hash: str,
        chunk_index: int,
        chunk_total: int,
        source_name: str,
        asset_id: str | None = None,
        retrieval_chunk_uid: str | None = None,
        retrieval_chunk_row_id: int | None = None,
        title: str | None = None,
        content_text: str | None = None,
        preview_text: str | None = None,
        page_index: int | None = None,
        anchor_json: dict[str, Any] | None = None,
        is_active: bool = True,
        deleted_at: int | None = None,
        evidence_ref: str | None = None,
        id: str | None = None,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceModel:
        modality = _normalize_evidence_modality(modality)
        evidence_kind = _normalize_evidence_kind(evidence_kind)
        chunk_index = int(chunk_index)
        chunk_total = int(chunk_total or 1)
        evidence_ref = evidence_ref or compute_knowledge_evidence_ref(
            knowledge_id=knowledge_id,
            file_id=file_id,
            modality=modality,
            evidence_kind=evidence_kind,
            content_hash=content_hash,
            projection_config_hash=projection_config_hash,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            retrieval_chunk_uid=retrieval_chunk_uid,
            asset_ref=None,
            page_index=page_index,
        )
        row_id = id or compute_knowledge_evidence_id(evidence_ref=evidence_ref)
        now = int(time.time())
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidence).filter_by(id=row_id))
            row = result.scalars().first()
            if row is None:
                row = KnowledgeEvidence(
                    id=row_id,
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
            return KnowledgeEvidenceModel.model_validate(row)

    async def get_evidence_by_id(
        self,
        evidence_id: str,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceModel | None:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidence).filter_by(id=evidence_id))
            row = result.scalars().first()
            return KnowledgeEvidenceModel.model_validate(row) if row else None

    async def get_evidence_by_ref(
        self,
        evidence_ref: str,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceModel | None:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidence).filter_by(evidence_ref=evidence_ref))
            row = result.scalars().first()
            return KnowledgeEvidenceModel.model_validate(row) if row else None

    async def list_evidences(
        self,
        *,
        knowledge_id: str | None = None,
        file_id: str | None = None,
        asset_id: str | None = None,
        retrieval_chunk_uid: str | None = None,
        retrieval_chunk_row_id: int | None = None,
        modality: str | None = None,
        is_active: bool | None = None,
        limit: int = 100,
        db: AsyncSession | None = None,
    ) -> list[KnowledgeEvidenceModel]:
        async with get_async_db_context(db) as session:
            stmt = select(KnowledgeEvidence)
            if knowledge_id:
                stmt = stmt.where(KnowledgeEvidence.knowledge_id == knowledge_id)
            if file_id:
                stmt = stmt.where(KnowledgeEvidence.file_id == file_id)
            if asset_id:
                stmt = stmt.where(KnowledgeEvidence.asset_id == asset_id)
            if retrieval_chunk_uid:
                stmt = stmt.where(KnowledgeEvidence.retrieval_chunk_uid == retrieval_chunk_uid)
            if retrieval_chunk_row_id is not None:
                stmt = stmt.where(KnowledgeEvidence.retrieval_chunk_row_id == retrieval_chunk_row_id)
            if modality:
                stmt = stmt.where(KnowledgeEvidence.modality == _normalize_evidence_modality(modality))
            if is_active is not None:
                stmt = stmt.where(KnowledgeEvidence.is_active.is_(is_active))
            stmt = stmt.order_by(KnowledgeEvidence.created_at.asc(), KnowledgeEvidence.id.asc()).limit(
                max(1, min(int(limit), 500))
            )
            result = await session.execute(stmt)
            return [KnowledgeEvidenceModel.model_validate(row) for row in result.scalars().all()]


class KnowledgeVectorSpaceTable:
    async def create_vector_space(
        self,
        *,
        knowledge_id: str,
        retrieval_profile: str,
        embedding_model: str,
        projection_config_hash: str | None = None,
        embedding_dim: int | None = None,
        distance_metric: str = "cosine",
        vector_backend: str = "pgvector",
        supports_text_query: bool = True,
        supports_image_query: bool = True,
        supports_text_evidence: bool = True,
        supports_image_evidence: bool = True,
        supports_multivector: bool = False,
        active: bool = True,
        id: str | None = None,
        db: AsyncSession | None = None,
    ) -> KnowledgeVectorSpaceModel:
        projection_payload = {
            "knowledge_id": knowledge_id,
            "retrieval_profile": retrieval_profile,
            "embedding_model": embedding_model,
            "embedding_dim": embedding_dim,
            "distance_metric": distance_metric,
            "vector_backend": vector_backend,
            "supports_text_query": supports_text_query,
            "supports_image_query": supports_image_query,
            "supports_text_evidence": supports_text_evidence,
            "supports_image_evidence": supports_image_evidence,
            "supports_multivector": supports_multivector,
            "active": active,
        }
        projection_config_hash = projection_config_hash or _stable_digest("vector_space", projection_payload)
        row_id = id or compute_knowledge_vector_space_id(
            knowledge_id=knowledge_id,
            retrieval_profile=retrieval_profile,
            embedding_model=embedding_model,
            projection_config_hash=projection_config_hash,
            distance_metric=distance_metric,
            vector_backend=vector_backend,
        )
        now = int(time.time())
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeVectorSpace).filter_by(id=row_id))
            row = result.scalars().first()
            if row is None:
                row = KnowledgeVectorSpace(
                    id=row_id,
                    knowledge_id=knowledge_id,
                    retrieval_profile=retrieval_profile,
                    embedding_model=embedding_model,
                    embedding_dim=embedding_dim,
                    distance_metric=distance_metric,
                    vector_backend=vector_backend,
                    supports_text_query=supports_text_query,
                    supports_image_query=supports_image_query,
                    supports_text_evidence=supports_text_evidence,
                    supports_image_evidence=supports_image_evidence,
                    supports_multivector=supports_multivector,
                    projection_config_hash=projection_config_hash,
                    active=active,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.knowledge_id = knowledge_id
                row.retrieval_profile = retrieval_profile
                row.embedding_model = embedding_model
                row.embedding_dim = embedding_dim
                row.distance_metric = distance_metric
                row.vector_backend = vector_backend
                row.supports_text_query = supports_text_query
                row.supports_image_query = supports_image_query
                row.supports_text_evidence = supports_text_evidence
                row.supports_image_evidence = supports_image_evidence
                row.supports_multivector = supports_multivector
                row.projection_config_hash = projection_config_hash
                row.active = active
                row.updated_at = now

            await session.commit()
            await session.refresh(row)
            return KnowledgeVectorSpaceModel.model_validate(row)

    async def get_vector_space_by_id(
        self,
        vector_space_id: str,
        db: AsyncSession | None = None,
    ) -> KnowledgeVectorSpaceModel | None:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeVectorSpace).filter_by(id=vector_space_id))
            row = result.scalars().first()
            return KnowledgeVectorSpaceModel.model_validate(row) if row else None

    async def get_active_vector_space(
        self,
        *,
        knowledge_id: str,
        retrieval_profile: str | None = None,
        db: AsyncSession | None = None,
    ) -> KnowledgeVectorSpaceModel | None:
        async with get_async_db_context(db) as session:
            stmt = select(KnowledgeVectorSpace).where(
                KnowledgeVectorSpace.knowledge_id == knowledge_id,
                KnowledgeVectorSpace.active.is_(True),
            )
            if retrieval_profile:
                stmt = stmt.where(KnowledgeVectorSpace.retrieval_profile == retrieval_profile)
            stmt = stmt.order_by(KnowledgeVectorSpace.updated_at.desc(), KnowledgeVectorSpace.id.desc())
            result = await session.execute(stmt)
            row = result.scalars().first()
            return KnowledgeVectorSpaceModel.model_validate(row) if row else None

    async def list_vector_spaces(
        self,
        *,
        knowledge_id: str | None = None,
        retrieval_profile: str | None = None,
        active: bool | None = None,
        limit: int = 100,
        db: AsyncSession | None = None,
    ) -> list[KnowledgeVectorSpaceModel]:
        async with get_async_db_context(db) as session:
            stmt = select(KnowledgeVectorSpace)
            if knowledge_id:
                stmt = stmt.where(KnowledgeVectorSpace.knowledge_id == knowledge_id)
            if retrieval_profile:
                stmt = stmt.where(KnowledgeVectorSpace.retrieval_profile == retrieval_profile)
            if active is not None:
                stmt = stmt.where(KnowledgeVectorSpace.active.is_(active))
            stmt = stmt.order_by(KnowledgeVectorSpace.updated_at.desc(), KnowledgeVectorSpace.id.desc()).limit(
                max(1, min(int(limit), 500))
            )
            result = await session.execute(stmt)
            return [KnowledgeVectorSpaceModel.model_validate(row) for row in result.scalars().all()]


class KnowledgeEvidenceEmbeddingTable:
    async def create_embedding(
        self,
        *,
        evidence_id: str,
        evidence_ref: str,
        vector_space_id: str,
        vector_backend_collection: str,
        vector_role: str,
        embedding_format: str,
        embedding_status: str,
        vector_backend_id: str | None = None,
        embedding_error: str | None = None,
        id: str | None = None,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceEmbeddingModel:
        vector_role = _normalize_vector_role(vector_role)
        embedding_format = _normalize_embedding_format(embedding_format)
        embedding_status = _normalize_embedding_status(embedding_status)
        row_id = id or compute_knowledge_evidence_embedding_id(
            evidence_ref=evidence_ref,
            vector_space_id=vector_space_id,
            vector_role=vector_role,
            vector_backend_collection=vector_backend_collection,
        )
        now = int(time.time())
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidenceEmbedding).filter_by(id=row_id))
            row = result.scalars().first()
            if row is None:
                row = KnowledgeEvidenceEmbedding(
                    id=row_id,
                    evidence_id=evidence_id,
                    evidence_ref=evidence_ref,
                    vector_space_id=vector_space_id,
                    vector_backend_collection=vector_backend_collection,
                    vector_backend_id=vector_backend_id,
                    vector_role=vector_role,
                    embedding_format=embedding_format,
                    embedding_status=embedding_status,
                    embedding_error=embedding_error,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.evidence_id = evidence_id
                row.evidence_ref = evidence_ref
                row.vector_space_id = vector_space_id
                row.vector_backend_collection = vector_backend_collection
                row.vector_backend_id = vector_backend_id
                row.vector_role = vector_role
                row.embedding_format = embedding_format
                row.embedding_status = embedding_status
                row.embedding_error = embedding_error
                row.updated_at = now

            await session.commit()
            await session.refresh(row)
            return KnowledgeEvidenceEmbeddingModel.model_validate(row)

    async def get_embedding_by_id(
        self,
        embedding_id: str,
        db: AsyncSession | None = None,
    ) -> KnowledgeEvidenceEmbeddingModel | None:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(KnowledgeEvidenceEmbedding).filter_by(id=embedding_id))
            row = result.scalars().first()
            return KnowledgeEvidenceEmbeddingModel.model_validate(row) if row else None

    async def list_embeddings(
        self,
        *,
        evidence_id: str | None = None,
        evidence_ref: str | None = None,
        vector_space_id: str | None = None,
        embedding_status: str | None = None,
        limit: int = 100,
        db: AsyncSession | None = None,
    ) -> list[KnowledgeEvidenceEmbeddingModel]:
        async with get_async_db_context(db) as session:
            stmt = select(KnowledgeEvidenceEmbedding)
            if evidence_id:
                stmt = stmt.where(KnowledgeEvidenceEmbedding.evidence_id == evidence_id)
            if evidence_ref:
                stmt = stmt.where(KnowledgeEvidenceEmbedding.evidence_ref == evidence_ref)
            if vector_space_id:
                stmt = stmt.where(KnowledgeEvidenceEmbedding.vector_space_id == vector_space_id)
            if embedding_status:
                stmt = stmt.where(
                    KnowledgeEvidenceEmbedding.embedding_status == _normalize_embedding_status(embedding_status)
                )
            stmt = stmt.order_by(
                KnowledgeEvidenceEmbedding.created_at.asc(), KnowledgeEvidenceEmbedding.id.asc()
            ).limit(max(1, min(int(limit), 500)))
            result = await session.execute(stmt)
            return [KnowledgeEvidenceEmbeddingModel.model_validate(row) for row in result.scalars().all()]


KnowledgeEvidenceAssets = KnowledgeEvidenceAssetTable()
KnowledgeEvidenceAssetVariants = KnowledgeEvidenceAssetVariantTable()
KnowledgeEvidences = KnowledgeEvidenceTable()
KnowledgeVectorSpaces = KnowledgeVectorSpaceTable()
KnowledgeEvidenceEmbeddings = KnowledgeEvidenceEmbeddingTable()
