from __future__ import annotations

from typing import Optional

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, Integer, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

EXTRACTION_CACHE_STATUSES = {"succeeded", "succeeded_no_output", "stale"}
EXTRACTION_JOB_STATUSES = {"queued", "leased", "retry", "failed"}
CONSOLIDATION_JOB_STATUSES = {"queued", "leased", "retry", "failed"}
AGENT_MEMORY_SCOPE_TYPES = {"global", "folder"}
AGENT_MEMORY_ARTIFACT_PATHS = {"memory_summary.md", "MEMORY.md"}


class AgentMemoryExtractionCache(Base):
    __tablename__ = "agent_memory_extraction_cache"

    user_id = Column(Text, primary_key=True)
    chat_id = Column(Text, primary_key=True)
    source_updated_at = Column(BigInteger, nullable=False)
    raw_memory = Column(Text, nullable=False)
    rollout_summary = Column(Text, nullable=False)
    rollout_slug = Column(Text, nullable=True)
    generated_at = Column(BigInteger, nullable=False)
    status = Column(Text, nullable=False)


class AgentMemoryExtractionJob(Base):
    __tablename__ = "agent_memory_extraction_job"
    __table_args__ = (
        Index(
            "ix_agent_memory_extraction_job_claim",
            "status",
            "retry_at",
            "lease_until",
            "updated_at",
            "chat_id",
        ),
    )

    user_id = Column(Text, primary_key=True)
    chat_id = Column(Text, primary_key=True)
    status = Column(Text, nullable=False)
    lease_until = Column(BigInteger, nullable=True)
    retry_at = Column(BigInteger, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    updated_at = Column(BigInteger, nullable=False)


class AgentMemoryConsolidationJob(Base):
    __tablename__ = "agent_memory_consolidation_job"
    __table_args__ = (
        Index(
            "ix_agent_memory_consolidation_job_claim",
            "status",
            "retry_at",
            "lease_until",
            "updated_at",
            "scope_type",
            "scope_id",
        ),
    )

    user_id = Column(Text, primary_key=True)
    scope_type = Column(Text, primary_key=True)
    scope_id = Column(Text, primary_key=True)
    status = Column(Text, nullable=False)
    lease_until = Column(BigInteger, nullable=True)
    retry_at = Column(BigInteger, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    input_hash = Column(Text, nullable=True)
    updated_at = Column(BigInteger, nullable=False)


class AgentMemoryArtifact(Base):
    __tablename__ = "agent_memory_artifact"

    user_id = Column(Text, primary_key=True)
    scope_type = Column(Text, primary_key=True)
    scope_id = Column(Text, primary_key=True)
    path = Column(Text, primary_key=True)
    content = Column(Text, nullable=False)
    input_hash = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False)
    note_id = Column(Text, nullable=True)
    note_content_hash = Column(Text, nullable=True)
    updated_at = Column(BigInteger, nullable=False)


class AgentMemoryExtractionCacheModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    chat_id: str
    source_updated_at: int
    raw_memory: str
    rollout_summary: str
    rollout_slug: Optional[str] = None
    generated_at: int
    status: str


class AgentMemoryExtractionJobModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    chat_id: str
    status: str
    lease_until: Optional[int] = None
    retry_at: Optional[int] = None
    retry_count: int
    last_error: Optional[str] = None
    updated_at: int


class AgentMemoryConsolidationJobModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    scope_type: str
    scope_id: str
    status: str
    lease_until: Optional[int] = None
    retry_at: Optional[int] = None
    retry_count: int
    last_error: Optional[str] = None
    input_hash: Optional[str] = None
    updated_at: int


class AgentMemoryArtifactModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    scope_type: str
    scope_id: str
    path: str
    content: str
    input_hash: str
    revision: int
    note_id: Optional[str] = None
    note_content_hash: Optional[str] = None
    updated_at: int


def _require_member(value: str, allowed: set[str], field_name: str) -> None:
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of: {allowed_values}")


class AgentMemoryExtractionCacheTable:
    async def upsert_cache(
        self,
        user_id: str,
        chat_id: str,
        source_updated_at: int,
        raw_memory: str,
        rollout_summary: str,
        rollout_slug: Optional[str],
        generated_at: int,
        status: str,
        db: Optional[AsyncSession] = None,
    ) -> AgentMemoryExtractionCacheModel:
        _require_member(status, EXTRACTION_CACHE_STATUSES, "status")
        async with get_async_db_context(db) as db:
            row = await db.get(AgentMemoryExtractionCache, (user_id, chat_id))
            if row is None:
                row = AgentMemoryExtractionCache(user_id=user_id, chat_id=chat_id)
                db.add(row)

            row.source_updated_at = source_updated_at
            row.raw_memory = raw_memory
            row.rollout_summary = rollout_summary
            row.rollout_slug = rollout_slug
            row.generated_at = generated_at
            row.status = status
            await db.commit()
            await db.refresh(row)
            return AgentMemoryExtractionCacheModel.model_validate(row)

    async def get_cache(
        self, user_id: str, chat_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[AgentMemoryExtractionCacheModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentMemoryExtractionCache, (user_id, chat_id))
            return AgentMemoryExtractionCacheModel.model_validate(row) if row else None

    async def delete_cache(self, user_id: str, chat_id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                delete(AgentMemoryExtractionCache).filter_by(user_id=user_id, chat_id=chat_id)
            )
            await db.commit()
            return bool(result.rowcount)


class AgentMemoryExtractionJobTable:
    async def upsert_job(
        self,
        user_id: str,
        chat_id: str,
        status: str,
        lease_until: Optional[int],
        retry_at: Optional[int],
        retry_count: int,
        last_error: Optional[str],
        updated_at: int,
        db: Optional[AsyncSession] = None,
    ) -> AgentMemoryExtractionJobModel:
        _require_member(status, EXTRACTION_JOB_STATUSES, "status")
        async with get_async_db_context(db) as db:
            row = await db.get(AgentMemoryExtractionJob, (user_id, chat_id))
            if row is None:
                row = AgentMemoryExtractionJob(user_id=user_id, chat_id=chat_id)
                db.add(row)

            row.status = status
            row.lease_until = lease_until
            row.retry_at = retry_at
            row.retry_count = retry_count
            row.last_error = last_error
            row.updated_at = updated_at
            await db.commit()
            await db.refresh(row)
            return AgentMemoryExtractionJobModel.model_validate(row)

    async def get_job(
        self, user_id: str, chat_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[AgentMemoryExtractionJobModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentMemoryExtractionJob, (user_id, chat_id))
            return AgentMemoryExtractionJobModel.model_validate(row) if row else None

    async def delete_job(self, user_id: str, chat_id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(delete(AgentMemoryExtractionJob).filter_by(user_id=user_id, chat_id=chat_id))
            await db.commit()
            return bool(result.rowcount)


class AgentMemoryConsolidationJobTable:
    async def upsert_job(
        self,
        user_id: str,
        scope_type: str,
        scope_id: str,
        status: str,
        lease_until: Optional[int],
        retry_at: Optional[int],
        retry_count: int,
        last_error: Optional[str],
        input_hash: Optional[str],
        updated_at: int,
        db: Optional[AsyncSession] = None,
    ) -> AgentMemoryConsolidationJobModel:
        _require_member(scope_type, AGENT_MEMORY_SCOPE_TYPES, "scope_type")
        _require_member(status, CONSOLIDATION_JOB_STATUSES, "status")
        async with get_async_db_context(db) as db:
            row = await db.get(AgentMemoryConsolidationJob, (user_id, scope_type, scope_id))
            if row is None:
                row = AgentMemoryConsolidationJob(user_id=user_id, scope_type=scope_type, scope_id=scope_id)
                db.add(row)

            row.status = status
            row.lease_until = lease_until
            row.retry_at = retry_at
            row.retry_count = retry_count
            row.last_error = last_error
            row.input_hash = input_hash
            row.updated_at = updated_at
            await db.commit()
            await db.refresh(row)
            return AgentMemoryConsolidationJobModel.model_validate(row)

    async def get_job(
        self, user_id: str, scope_type: str, scope_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[AgentMemoryConsolidationJobModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentMemoryConsolidationJob, (user_id, scope_type, scope_id))
            return AgentMemoryConsolidationJobModel.model_validate(row) if row else None

    async def delete_job(
        self, user_id: str, scope_type: str, scope_id: str, db: Optional[AsyncSession] = None
    ) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                delete(AgentMemoryConsolidationJob).filter_by(
                    user_id=user_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
            )
            await db.commit()
            return bool(result.rowcount)


class AgentMemoryArtifactTable:
    async def upsert_artifact(
        self,
        user_id: str,
        scope_type: str,
        scope_id: str,
        path: str,
        content: str,
        input_hash: str,
        revision: int,
        note_id: Optional[str],
        note_content_hash: Optional[str],
        updated_at: int,
        db: Optional[AsyncSession] = None,
    ) -> AgentMemoryArtifactModel:
        _require_member(scope_type, AGENT_MEMORY_SCOPE_TYPES, "scope_type")
        _require_member(path, AGENT_MEMORY_ARTIFACT_PATHS, "path")
        async with get_async_db_context(db) as db:
            row = await db.get(AgentMemoryArtifact, (user_id, scope_type, scope_id, path))
            if row is None:
                row = AgentMemoryArtifact(user_id=user_id, scope_type=scope_type, scope_id=scope_id, path=path)
                db.add(row)

            row.content = content
            row.input_hash = input_hash
            row.revision = revision
            row.note_id = note_id
            row.note_content_hash = note_content_hash
            row.updated_at = updated_at
            await db.commit()
            await db.refresh(row)
            return AgentMemoryArtifactModel.model_validate(row)

    async def get_artifact(
        self, user_id: str, scope_type: str, scope_id: str, path: str, db: Optional[AsyncSession] = None
    ) -> Optional[AgentMemoryArtifactModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentMemoryArtifact, (user_id, scope_type, scope_id, path))
            return AgentMemoryArtifactModel.model_validate(row) if row else None

    async def list_artifacts(
        self, user_id: str, scope_type: str, scope_id: str, db: Optional[AsyncSession] = None
    ) -> list[AgentMemoryArtifactModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AgentMemoryArtifact)
                .filter_by(user_id=user_id, scope_type=scope_type, scope_id=scope_id)
                .order_by(AgentMemoryArtifact.path)
            )
            return [AgentMemoryArtifactModel.model_validate(row) for row in result.scalars().all()]

    async def delete_artifact(
        self, user_id: str, scope_type: str, scope_id: str, path: str, db: Optional[AsyncSession] = None
    ) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                delete(AgentMemoryArtifact).filter_by(
                    user_id=user_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    path=path,
                )
            )
            await db.commit()
            return bool(result.rowcount)


AgentMemoryExtractionCaches = AgentMemoryExtractionCacheTable()
AgentMemoryExtractionJobs = AgentMemoryExtractionJobTable()
AgentMemoryConsolidationJobs = AgentMemoryConsolidationJobTable()
AgentMemoryArtifacts = AgentMemoryArtifactTable()
