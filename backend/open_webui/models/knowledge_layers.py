import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import Session

from open_webui.internal.db import Base, get_db_context
from open_webui.models.files import File

LAYER_TYPES = ("abstract", "key_findings", "key_data")
LAYER_STATUSES = ("pending", "ready", "failed", "stale")


def _normalize_layer_type(layer_type: str) -> str:
    normalized = (layer_type or "").strip().lower()
    if normalized not in LAYER_TYPES:
        raise ValueError(f"Unsupported layer_type: {layer_type}")
    return normalized


def _normalize_layer_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized not in LAYER_STATUSES:
        raise ValueError(f"Unsupported layer status: {status}")
    return normalized


class KnowledgeFileLayer(Base):
    __tablename__ = "knowledge_file_layer"

    id = Column(Text, unique=True, primary_key=True)
    knowledge_id = Column(
        Text, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False
    )
    file_id = Column(Text, ForeignKey("file.id", ondelete="CASCADE"), nullable=False)
    layer_type = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    status = Column(Text, nullable=False)
    source_system = Column(Text, nullable=True)
    source_ref_id = Column(Text, nullable=True)
    transformation_ref_id = Column(Text, nullable=True)
    content_hash = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "knowledge_id",
            "file_id",
            "layer_type",
            name="uq_knowledge_file_layer_identity",
        ),
        Index("idx_knowledge_file_layer_knowledge_id", "knowledge_id"),
        Index("idx_knowledge_file_layer_file_id", "file_id"),
        Index("idx_knowledge_file_layer_status", "status"),
    )


class KnowledgeFileLayerModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    knowledge_id: str
    file_id: str
    layer_type: Literal["abstract", "key_findings", "key_data"]
    title: Optional[str] = None
    content: Optional[str] = None
    status: Literal["pending", "ready", "failed", "stale"]
    source_system: Optional[str] = None
    source_ref_id: Optional[str] = None
    transformation_ref_id: Optional[str] = None
    content_hash: Optional[str] = None
    created_at: int
    updated_at: int


class KnowledgeFileLayerUpsertForm(BaseModel):
    knowledge_id: str
    file_id: str
    layer_type: str
    title: Optional[str] = None
    content: Optional[str] = None
    status: str
    source_system: Optional[str] = None
    source_ref_id: Optional[str] = None
    transformation_ref_id: Optional[str] = None
    content_hash: Optional[str] = None


class KnowledgeFileLayerListResponse(BaseModel):
    items: list[KnowledgeFileLayerModel]
    total: int


class KnowledgeFileLayerQueryRow(BaseModel):
    layer_type: Literal["abstract", "key_findings", "key_data"]
    content: str
    source: str
    file_id: str
    knowledge_id: str
    distance: Optional[float] = None


class KnowledgeFileLayerTable:
    def upsert_layer(
        self, form_data: KnowledgeFileLayerUpsertForm, db: Optional[Session] = None
    ) -> KnowledgeFileLayerModel:
        with get_db_context(db) as db:
            now = int(time.time())
            layer_type = _normalize_layer_type(form_data.layer_type)
            status = _normalize_layer_status(form_data.status)

            row = (
                db.query(KnowledgeFileLayer)
                .filter_by(
                    knowledge_id=form_data.knowledge_id,
                    file_id=form_data.file_id,
                    layer_type=layer_type,
                )
                .first()
            )

            if row:
                row.title = form_data.title
                row.content = form_data.content
                row.status = status
                row.source_system = form_data.source_system
                row.source_ref_id = form_data.source_ref_id
                row.transformation_ref_id = form_data.transformation_ref_id
                row.content_hash = form_data.content_hash
                row.updated_at = now
            else:
                row = KnowledgeFileLayer(
                    id=str(uuid.uuid4()),
                    knowledge_id=form_data.knowledge_id,
                    file_id=form_data.file_id,
                    layer_type=layer_type,
                    title=form_data.title,
                    content=form_data.content,
                    status=status,
                    source_system=form_data.source_system,
                    source_ref_id=form_data.source_ref_id,
                    transformation_ref_id=form_data.transformation_ref_id,
                    content_hash=form_data.content_hash,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)

            db.commit()
            db.refresh(row)
            return KnowledgeFileLayerModel.model_validate(row)

    def get_layers_by_file(
        self, knowledge_id: str, file_id: str, db: Optional[Session] = None
    ) -> list[KnowledgeFileLayerModel]:
        with get_db_context(db) as db:
            rows = (
                db.query(KnowledgeFileLayer)
                .filter_by(knowledge_id=knowledge_id, file_id=file_id)
                .order_by(KnowledgeFileLayer.updated_at.desc())
                .all()
            )
            return [KnowledgeFileLayerModel.model_validate(row) for row in rows]

    def get_layers_for_scope_file(
        self,
        file_id: str,
        knowledge_ids: Optional[list[str]] = None,
        db: Optional[Session] = None,
    ) -> list[KnowledgeFileLayerModel]:
        with get_db_context(db) as db:
            query = db.query(KnowledgeFileLayer).filter_by(file_id=file_id)
            if knowledge_ids:
                query = query.filter(KnowledgeFileLayer.knowledge_id.in_(knowledge_ids))
            rows = query.order_by(KnowledgeFileLayer.updated_at.desc()).all()
            return [KnowledgeFileLayerModel.model_validate(row) for row in rows]

    def query_layer_rows(
        self,
        *,
        layer_type: str,
        query: str,
        knowledge_ids: Optional[list[str]] = None,
        file_ids: Optional[list[str]] = None,
        limit: int = 5,
        db: Optional[Session] = None,
    ) -> list[KnowledgeFileLayerQueryRow]:
        with get_db_context(db) as db:
            normalized_layer = _normalize_layer_type(layer_type)
            search_query = (query or "").strip()

            rows_query = (
                db.query(KnowledgeFileLayer, File)
                .join(File, File.id == KnowledgeFileLayer.file_id)
                .filter(
                    KnowledgeFileLayer.layer_type == normalized_layer,
                    KnowledgeFileLayer.status == "ready",
                )
            )

            if knowledge_ids:
                rows_query = rows_query.filter(
                    KnowledgeFileLayer.knowledge_id.in_(knowledge_ids)
                )
            if file_ids:
                rows_query = rows_query.filter(KnowledgeFileLayer.file_id.in_(file_ids))
            if search_query:
                rows_query = rows_query.filter(
                    KnowledgeFileLayer.content.ilike(f"%{search_query}%")
                )

            rows = (
                rows_query.order_by(KnowledgeFileLayer.updated_at.desc())
                .limit(limit)
                .all()
            )
            return [
                KnowledgeFileLayerQueryRow(
                    layer_type=row.layer_type,
                    content=row.content or "",
                    source=file.filename or row.title or row.file_id,
                    file_id=row.file_id,
                    knowledge_id=row.knowledge_id,
                )
                for row, file in rows
            ]

    def delete_layers_by_file(
        self, knowledge_id: str, file_id: str, db: Optional[Session] = None
    ) -> int:
        with get_db_context(db) as db:
            deleted = (
                db.query(KnowledgeFileLayer)
                .filter_by(knowledge_id=knowledge_id, file_id=file_id)
                .delete()
            )
            db.commit()
            return deleted

    def mark_layers_stale_for_file(
        self, knowledge_id: str, file_id: str, db: Optional[Session] = None
    ) -> int:
        with get_db_context(db) as db:
            now = int(time.time())
            rows = (
                db.query(KnowledgeFileLayer)
                .filter_by(knowledge_id=knowledge_id, file_id=file_id)
                .all()
            )
            for row in rows:
                row.status = "stale"
                row.updated_at = now
            db.commit()
            return len(rows)

    def mark_layers_stale_for_knowledge(
        self, knowledge_id: str, db: Optional[Session] = None
    ) -> int:
        with get_db_context(db) as db:
            now = int(time.time())
            rows = (
                db.query(KnowledgeFileLayer).filter_by(knowledge_id=knowledge_id).all()
            )
            for row in rows:
                row.status = "stale"
                row.updated_at = now
            db.commit()
            return len(rows)


KnowledgeLayers = KnowledgeFileLayerTable()
