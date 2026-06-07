from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, Integer, Text, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, JSONField, get_async_db_context


INDEX_KINDS = ("embedding", "lexical", "full", "delete", "rechunk", "project")
JOB_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
INDEX_STATUSES = ("pending", "indexing", "ready", "stale", "failed", "deleted")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_target_config_hash(config: dict[str, Any] | None) -> str:
    return _sha256_hex(_canonical_json(config or {}))


def compute_index_state_id(
    *,
    index_kind: str,
    collection_id: str | None = None,
    knowledge_id: str | None = None,
    collection_name: str | None = None,
    file_id: str | None = None,
    chunker_config_hash: str | None = None,
    target_config_hash: str | None = None,
) -> str:
    payload = {
        "index_kind": normalize_index_kind(index_kind),
        "collection_id": collection_id,
        "knowledge_id": knowledge_id,
        "collection_name": collection_name,
        "file_id": file_id,
        "chunker_config_hash": chunker_config_hash,
        "target_config_hash": target_config_hash,
    }
    return f"retrieval_index_state_{_sha256_hex(_canonical_json(payload))}"


def normalize_index_kind(index_kind: str) -> str:
    normalized = (index_kind or "").strip().lower()
    if normalized not in INDEX_KINDS:
        raise ValueError(f"Unsupported retrieval index kind: {index_kind}")
    return normalized


def normalize_job_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized not in JOB_STATUSES:
        raise ValueError(f"Unsupported retrieval index job status: {status}")
    return normalized


def normalize_index_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized not in INDEX_STATUSES:
        raise ValueError(f"Unsupported retrieval index status: {status}")
    return normalized


class RetrievalIndexJob(Base):
    __tablename__ = "retrieval_index_job"

    job_id = Column(Text, unique=True, primary_key=True)
    index_kind = Column(Text, nullable=False)

    collection_id = Column(Text, nullable=True)
    knowledge_id = Column(Text, nullable=True)
    collection_name = Column(Text, nullable=True)
    file_id = Column(Text, nullable=True)

    chunker_config_hash = Column(Text, nullable=True)
    target_config_hash = Column(Text, nullable=True)

    status = Column(Text, nullable=False)
    payload = Column(JSONField, nullable=True)
    result = Column(JSONField, nullable=True)
    error = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)

    created_at = Column(BigInteger, nullable=False)
    started_at = Column(BigInteger, nullable=True)
    finished_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_retrieval_index_job_status", "status"),
        Index("ix_retrieval_index_job_kind_status", "index_kind", "status"),
        Index("ix_retrieval_index_job_collection_id", "collection_id"),
        Index("ix_retrieval_index_job_file_id", "file_id"),
    )


class RetrievalIndexState(Base):
    __tablename__ = "retrieval_index_state"

    state_id = Column(Text, unique=True, primary_key=True)
    index_kind = Column(Text, nullable=False)

    collection_id = Column(Text, nullable=True)
    knowledge_id = Column(Text, nullable=True)
    collection_name = Column(Text, nullable=True)
    file_id = Column(Text, nullable=True)

    chunker_config_hash = Column(Text, nullable=True)
    target_config_hash = Column(Text, nullable=True)

    status = Column(Text, nullable=False)
    active_chunk_count = Column(Integer, nullable=True)
    indexed_chunk_count = Column(Integer, nullable=True)
    last_job_id = Column(Text, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_retrieval_index_state_kind_status", "index_kind", "status"),
        Index("ix_retrieval_index_state_collection_id", "collection_id"),
        Index("ix_retrieval_index_state_file_id", "file_id"),
    )


class RetrievalIndexJobModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    index_kind: Literal["embedding", "lexical", "full", "delete", "rechunk", "project"]
    collection_id: str | None = None
    knowledge_id: str | None = None
    collection_name: str | None = None
    file_id: str | None = None
    chunker_config_hash: str | None = None
    target_config_hash: str | None = None
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    retry_count: int
    max_retries: int
    created_at: int
    started_at: int | None = None
    finished_at: int | None = None
    updated_at: int


class RetrievalIndexStateModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    state_id: str
    index_kind: Literal["embedding", "lexical", "full", "delete", "rechunk", "project"]
    collection_id: str | None = None
    knowledge_id: str | None = None
    collection_name: str | None = None
    file_id: str | None = None
    chunker_config_hash: str | None = None
    target_config_hash: str | None = None
    status: Literal["pending", "indexing", "ready", "stale", "failed", "deleted"]
    active_chunk_count: int | None = None
    indexed_chunk_count: int | None = None
    last_job_id: str | None = None
    error: str | None = None
    created_at: int
    updated_at: int


class RetrievalIndexJobTable:
    async def enqueue_job(
        self,
        *,
        index_kind: str,
        collection_id: str | None = None,
        knowledge_id: str | None = None,
        collection_name: str | None = None,
        file_id: str | None = None,
        chunker_config_hash: str | None = None,
        target_config_hash: str | None = None,
        payload: dict[str, Any] | None = None,
        max_retries: int = 3,
        db: AsyncSession | None = None,
    ) -> RetrievalIndexJobModel:
        now = int(time.time())
        async with get_async_db_context(db) as session:
            row = RetrievalIndexJob(
                job_id=str(uuid.uuid4()),
                index_kind=normalize_index_kind(index_kind),
                collection_id=collection_id,
                knowledge_id=knowledge_id,
                collection_name=collection_name,
                file_id=file_id,
                chunker_config_hash=chunker_config_hash,
                target_config_hash=target_config_hash,
                status="pending",
                payload=payload or {},
                result=None,
                error=None,
                retry_count=0,
                max_retries=max(0, int(max_retries)),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return RetrievalIndexJobModel.model_validate(row)

    async def get_job_by_id(
        self,
        job_id: str,
        db: AsyncSession | None = None,
    ) -> RetrievalIndexJobModel | None:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(RetrievalIndexJob).filter_by(job_id=job_id))
            row = result.scalars().first()
            return RetrievalIndexJobModel.model_validate(row) if row else None

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        index_kind: str | None = None,
        limit: int = 20,
        db: AsyncSession | None = None,
    ) -> list[RetrievalIndexJobModel]:
        async with get_async_db_context(db) as session:
            stmt = select(RetrievalIndexJob)
            if status:
                stmt = stmt.where(RetrievalIndexJob.status == normalize_job_status(status))
            if index_kind:
                stmt = stmt.where(RetrievalIndexJob.index_kind == normalize_index_kind(index_kind))
            stmt = stmt.order_by(RetrievalIndexJob.created_at.desc()).limit(max(1, min(int(limit), 100)))
            result = await session.execute(stmt)
            return [RetrievalIndexJobModel.model_validate(row) for row in result.scalars().all()]

    async def update_job_status(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        db: AsyncSession | None = None,
    ) -> RetrievalIndexJobModel | None:
        normalized = normalize_job_status(status)
        now = int(time.time())
        async with get_async_db_context(db) as session:
            query_result = await session.execute(select(RetrievalIndexJob).filter_by(job_id=job_id))
            row = query_result.scalars().first()
            if row is None:
                return None

            row.status = normalized
            row.updated_at = now
            if normalized == "running" and row.started_at is None:
                row.started_at = now
            if normalized in {"succeeded", "failed", "cancelled"}:
                row.finished_at = now
            if normalized == "failed":
                row.retry_count = int(row.retry_count or 0) + 1
            if result is not None:
                row.result = result
            row.error = error

            await session.commit()
            await session.refresh(row)
            return RetrievalIndexJobModel.model_validate(row)


class RetrievalIndexStateTable:
    async def upsert_state(
        self,
        *,
        index_kind: str,
        status: str,
        collection_id: str | None = None,
        knowledge_id: str | None = None,
        collection_name: str | None = None,
        file_id: str | None = None,
        chunker_config_hash: str | None = None,
        target_config_hash: str | None = None,
        active_chunk_count: int | None = None,
        indexed_chunk_count: int | None = None,
        last_job_id: str | None = None,
        error: str | None = None,
        db: AsyncSession | None = None,
    ) -> RetrievalIndexStateModel:
        normalized_kind = normalize_index_kind(index_kind)
        normalized_status = normalize_index_status(status)
        state_id = compute_index_state_id(
            index_kind=normalized_kind,
            collection_id=collection_id,
            knowledge_id=knowledge_id,
            collection_name=collection_name,
            file_id=file_id,
            chunker_config_hash=chunker_config_hash,
            target_config_hash=target_config_hash,
        )
        now = int(time.time())
        async with get_async_db_context(db) as session:
            result = await session.execute(select(RetrievalIndexState).filter_by(state_id=state_id))
            row = result.scalars().first()
            if row is None:
                row = RetrievalIndexState(
                    state_id=state_id,
                    index_kind=normalized_kind,
                    collection_id=collection_id,
                    knowledge_id=knowledge_id,
                    collection_name=collection_name,
                    file_id=file_id,
                    chunker_config_hash=chunker_config_hash,
                    target_config_hash=target_config_hash,
                    created_at=now,
                )
                session.add(row)

            row.status = normalized_status
            row.active_chunk_count = active_chunk_count
            row.indexed_chunk_count = indexed_chunk_count
            row.last_job_id = last_job_id
            row.error = error
            row.updated_at = now

            await session.commit()
            await session.refresh(row)
            return RetrievalIndexStateModel.model_validate(row)

    async def get_state(
        self,
        *,
        index_kind: str,
        collection_id: str | None = None,
        knowledge_id: str | None = None,
        collection_name: str | None = None,
        file_id: str | None = None,
        chunker_config_hash: str | None = None,
        target_config_hash: str | None = None,
        db: AsyncSession | None = None,
    ) -> RetrievalIndexStateModel | None:
        state_id = compute_index_state_id(
            index_kind=index_kind,
            collection_id=collection_id,
            knowledge_id=knowledge_id,
            collection_name=collection_name,
            file_id=file_id,
            chunker_config_hash=chunker_config_hash,
            target_config_hash=target_config_hash,
        )
        async with get_async_db_context(db) as session:
            result = await session.execute(select(RetrievalIndexState).filter_by(state_id=state_id))
            row = result.scalars().first()
            return RetrievalIndexStateModel.model_validate(row) if row else None

    async def list_states(
        self,
        *,
        index_kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
        db: AsyncSession | None = None,
    ) -> list[RetrievalIndexStateModel]:
        async with get_async_db_context(db) as session:
            stmt = select(RetrievalIndexState)
            if index_kind:
                stmt = stmt.where(RetrievalIndexState.index_kind == normalize_index_kind(index_kind))
            if status:
                stmt = stmt.where(RetrievalIndexState.status == normalize_index_status(status))
            stmt = stmt.order_by(RetrievalIndexState.updated_at.desc()).limit(max(1, min(int(limit), 100)))
            result = await session.execute(stmt)
            return [RetrievalIndexStateModel.model_validate(row) for row in result.scalars().all()]


RetrievalIndexJobs = RetrievalIndexJobTable()
RetrievalIndexStates = RetrievalIndexStateTable()
