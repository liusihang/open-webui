import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, Text, UniqueConstraint, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.files import File
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

LAYER_TYPES = ("abstract",)
LAYER_TYPE_ALIASES = {
    "abstract": "abstract",
    "key_findings": "abstract",
    "key_data": "abstract",
}
LAYER_STATUSES = ("pending", "ready", "failed", "stale")
EMBEDDING_STATUSES = ("pending", "indexing", "ready", "failed", "stale")


def _normalize_layer_type(layer_type: str) -> str:
    normalized = (layer_type or "").strip().lower()
    normalized = LAYER_TYPE_ALIASES.get(normalized, normalized)
    if normalized not in LAYER_TYPES:
        raise ValueError(f"Unsupported layer_type: {layer_type}")
    return normalized


def _normalize_layer_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized not in LAYER_STATUSES:
        raise ValueError(f"Unsupported layer status: {status}")
    return normalized


def _normalize_embedding_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized not in EMBEDDING_STATUSES:
        raise ValueError(f"Unsupported embedding status: {status}")
    return normalized


class KnowledgeFileLayer(Base):
    __tablename__ = "knowledge_file_layer"

    id = Column(Text, unique=True, primary_key=True)
    knowledge_id = Column(Text, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False)
    file_id = Column(Text, ForeignKey("file.id", ondelete="CASCADE"), nullable=False)
    layer_type = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    status = Column(Text, nullable=False)
    source_system = Column(Text, nullable=True)
    source_ref_id = Column(Text, nullable=True)
    transformation_ref_id = Column(Text, nullable=True)
    content_hash = Column(Text, nullable=True)
    part_index = Column(Integer, nullable=False, default=1)
    part_total = Column(Integer, nullable=False, default=1)
    display_title = Column(Text, nullable=True)
    embedding_status = Column(Text, nullable=False, default="pending")
    embedding_error = Column(Text, nullable=True)
    embedding_updated_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "knowledge_id",
            "file_id",
            "layer_type",
            "part_index",
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
    layer_type: Literal["abstract"]
    title: Optional[str] = None
    content: Optional[str] = None
    status: Literal["pending", "ready", "failed", "stale"]
    source_system: Optional[str] = None
    source_ref_id: Optional[str] = None
    transformation_ref_id: Optional[str] = None
    content_hash: Optional[str] = None
    part_index: int
    part_total: int
    display_title: Optional[str] = None
    embedding_status: Literal["pending", "indexing", "ready", "failed", "stale"] = "pending"
    embedding_error: Optional[str] = None
    embedding_updated_at: Optional[int] = None
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
    part_index: int = 1
    part_total: int = 1
    display_title: Optional[str] = None
    embedding_status: str = "pending"
    embedding_error: Optional[str] = None
    embedding_updated_at: Optional[int] = None


class KnowledgeFileLayerListResponse(BaseModel):
    items: list[KnowledgeFileLayerModel]
    total: int


class KnowledgeFileLayerQueryRow(BaseModel):
    layer_type: Literal["abstract"]
    content: str
    source: str
    file_id: str
    knowledge_id: str
    distance: Optional[float] = None


class KnowledgeFileLayerTable:
    async def upsert_layer(
        self, form_data: KnowledgeFileLayerUpsertForm, db: Optional[AsyncSession] = None
    ) -> KnowledgeFileLayerModel:
        async with get_async_db_context(db) as db:
            now = int(time.time())
            layer_type = _normalize_layer_type(form_data.layer_type)
            status = _normalize_layer_status(form_data.status)
            embedding_status = _normalize_embedding_status(form_data.embedding_status)
            part_index = int(form_data.part_index or 1)
            part_total = int(form_data.part_total or 1)

            stmt = select(KnowledgeFileLayer).filter_by(
                knowledge_id=form_data.knowledge_id,
                file_id=form_data.file_id,
                layer_type=layer_type,
                part_index=part_index,
            )
            row = (await db.execute(stmt)).scalars().first()

            if row:
                row.title = form_data.title
                row.content = form_data.content
                row.status = status
                row.source_system = form_data.source_system
                row.source_ref_id = form_data.source_ref_id
                row.transformation_ref_id = form_data.transformation_ref_id
                row.content_hash = form_data.content_hash
                row.part_total = part_total
                row.display_title = form_data.display_title
                row.embedding_status = embedding_status
                row.embedding_error = form_data.embedding_error
                row.embedding_updated_at = form_data.embedding_updated_at
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
                    part_index=part_index,
                    part_total=part_total,
                    display_title=form_data.display_title,
                    embedding_status=embedding_status,
                    embedding_error=form_data.embedding_error,
                    embedding_updated_at=form_data.embedding_updated_at,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)

            await db.commit()
            await db.refresh(row)
            return KnowledgeFileLayerModel.model_validate(row)

    async def get_layers_by_file(
        self, knowledge_id: str, file_id: str, db: Optional[AsyncSession] = None
    ) -> list[KnowledgeFileLayerModel]:
        async with get_async_db_context(db) as db:
            stmt = (
                select(KnowledgeFileLayer)
                .filter_by(knowledge_id=knowledge_id, file_id=file_id)
                .order_by(
                    KnowledgeFileLayer.layer_type.asc(),
                    KnowledgeFileLayer.part_index.asc(),
                    KnowledgeFileLayer.updated_at.desc(),
                )
            )
            rows = (await db.execute(stmt)).scalars().all()
            return [KnowledgeFileLayerModel.model_validate(row) for row in rows]

    async def get_layers_for_scope_file(
        self,
        file_id: str,
        knowledge_ids: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> list[KnowledgeFileLayerModel]:
        async with get_async_db_context(db) as db:
            stmt = select(KnowledgeFileLayer).filter_by(file_id=file_id)
            if knowledge_ids:
                stmt = stmt.filter(KnowledgeFileLayer.knowledge_id.in_(knowledge_ids))
            stmt = stmt.order_by(
                KnowledgeFileLayer.layer_type.asc(),
                KnowledgeFileLayer.part_index.asc(),
                KnowledgeFileLayer.updated_at.desc(),
            )
            rows = (await db.execute(stmt)).scalars().all()
            return [KnowledgeFileLayerModel.model_validate(row) for row in rows]

    async def query_layer_rows(
        self,
        *,
        layer_type: str,
        query: str,
        knowledge_ids: Optional[list[str]] = None,
        file_ids: Optional[list[str]] = None,
        limit: int = 5,
        request=None,
        db: Optional[AsyncSession] = None,
    ) -> list[KnowledgeFileLayerQueryRow]:
        async with get_async_db_context(db) as db:
            normalized_layer = _normalize_layer_type(layer_type)
            search_query = (query or "").strip()
            if request is None or not getattr(request.app.state, "EMBEDDING_FUNCTION", None):
                raise RuntimeError("Layered knowledge vector retrieval requires request embedding context")

            embedding = await request.app.state.EMBEDDING_FUNCTION(search_query)
            filter_payload: dict = {"layer_type": normalized_layer}
            if knowledge_ids:
                filter_payload["knowledge_id"] = knowledge_ids[0] if len(knowledge_ids) == 1 else {"$in": knowledge_ids}
            if file_ids:
                filter_payload["file_id"] = file_ids[0] if len(file_ids) == 1 else {"$in": file_ids}

            if not await ASYNC_VECTOR_DB_CLIENT.has_collection("knowledge-layers"):
                return []

            search_results = await ASYNC_VECTOR_DB_CLIENT.search(
                collection_name="knowledge-layers",
                vectors=[embedding],
                filter=filter_payload,
                limit=limit,
            )
            if not search_results or not search_results.metadatas:
                return []

            metadatas = search_results.metadatas[0] or []
            distances = search_results.distances[0] if search_results.distances and search_results.distances[0] else []
            row_ids = [
                metadata.get("layer_row_id")
                for metadata in metadatas
                if isinstance(metadata, dict) and metadata.get("layer_row_id")
            ]
            if not row_ids:
                return []

            stmt = (
                select(KnowledgeFileLayer, File)
                .join(File, File.id == KnowledgeFileLayer.file_id)
                .filter(
                    KnowledgeFileLayer.id.in_(row_ids),
                    KnowledgeFileLayer.layer_type == normalized_layer,
                    KnowledgeFileLayer.status == "ready",
                )
            )
            db_rows = (await db.execute(stmt)).all()
            row_map = {row.id: (row, file) for row, file in db_rows}

            hydrated: list[KnowledgeFileLayerQueryRow] = []
            for index, metadata in enumerate(metadatas):
                if not isinstance(metadata, dict):
                    continue
                row_id = metadata.get("layer_row_id")
                if not row_id or row_id not in row_map:
                    continue
                row, file = row_map[row_id]
                source = (
                    f"{row.display_title}: {file.filename}" if row.display_title else file.filename
                )
                hydrated.append(
                    KnowledgeFileLayerQueryRow(
                        layer_type=row.layer_type,
                        content=row.content or "",
                        source=source,
                        file_id=row.file_id,
                        knowledge_id=row.knowledge_id,
                        distance=distances[index] if index < len(distances) else None,
                    )
                )
            return hydrated

    async def delete_layers_by_file(
        self,
        knowledge_id: str,
        file_id: str,
        layer_types: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> int:
        async with get_async_db_context(db) as db:
            stmt = delete(KnowledgeFileLayer).where(
                KnowledgeFileLayer.knowledge_id == knowledge_id,
                KnowledgeFileLayer.file_id == file_id,
            )
            if layer_types:
                normalized_layer_types = [_normalize_layer_type(layer_type) for layer_type in layer_types]
                stmt = stmt.where(KnowledgeFileLayer.layer_type.in_(normalized_layer_types))
            result = await db.execute(stmt)
            await db.commit()
            return result.rowcount or 0

    async def delete_layers_by_knowledge(
        self, knowledge_id: str, db: Optional[AsyncSession] = None
    ) -> int:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                delete(KnowledgeFileLayer).where(KnowledgeFileLayer.knowledge_id == knowledge_id)
            )
            await db.commit()
            return result.rowcount or 0

    async def mark_layers_stale_for_file(
        self, knowledge_id: str, file_id: str, db: Optional[AsyncSession] = None
    ) -> int:
        async with get_async_db_context(db) as db:
            now = int(time.time())
            result = await db.execute(
                update(KnowledgeFileLayer)
                .where(
                    KnowledgeFileLayer.knowledge_id == knowledge_id,
                    KnowledgeFileLayer.file_id == file_id,
                )
                .values(
                    status="stale",
                    embedding_status="stale",
                    embedding_error=None,
                    embedding_updated_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
            return result.rowcount or 0

    async def mark_layers_stale_for_knowledge(
        self, knowledge_id: str, db: Optional[AsyncSession] = None
    ) -> int:
        async with get_async_db_context(db) as db:
            now = int(time.time())
            result = await db.execute(
                update(KnowledgeFileLayer)
                .where(KnowledgeFileLayer.knowledge_id == knowledge_id)
                .values(
                    status="stale",
                    embedding_status="stale",
                    embedding_error=None,
                    embedding_updated_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
            return result.rowcount or 0

    async def _mark_embedding_state(
        self,
        row_id: str,
        *,
        embedding_status: str,
        embedding_error: Optional[str],
        db: Optional[AsyncSession] = None,
    ) -> Optional[KnowledgeFileLayerModel]:
        async with get_async_db_context(db) as db:
            now = int(time.time())
            await db.execute(
                update(KnowledgeFileLayer)
                .where(KnowledgeFileLayer.id == row_id)
                .values(
                    embedding_status=_normalize_embedding_status(embedding_status),
                    embedding_error=embedding_error,
                    embedding_updated_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
            row = (await db.execute(select(KnowledgeFileLayer).where(KnowledgeFileLayer.id == row_id))).scalars().first()
            return KnowledgeFileLayerModel.model_validate(row) if row else None

    async def mark_embedding_indexing(
        self, row_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeFileLayerModel]:
        return await self._mark_embedding_state(
            row_id,
            embedding_status="indexing",
            embedding_error=None,
            db=db,
        )

    async def mark_embedding_ready(
        self, row_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeFileLayerModel]:
        return await self._mark_embedding_state(
            row_id,
            embedding_status="ready",
            embedding_error=None,
            db=db,
        )

    async def mark_embedding_failed(
        self, row_id: str, error: str, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeFileLayerModel]:
        return await self._mark_embedding_state(
            row_id,
            embedding_status="failed",
            embedding_error=error,
            db=db,
        )


KnowledgeLayers = KnowledgeFileLayerTable()
