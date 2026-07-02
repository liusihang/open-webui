from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from open_webui.internal.db import Base, JSONField, get_async_db_context
from sqlalchemy import BigInteger, Boolean, Column, Index, Integer, Text, UniqueConstraint
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_content_hash(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string; pass an explicit empty string for empty content")
    return _sha256_hex(text)


def compute_chunker_config_hash(config: dict[str, Any] | None) -> str:
    return _sha256_hex(_canonical_json(config or {}))


def compute_chunk_uid(
    *,
    collection_id: str | None,
    file_id: str | None,
    file_version: int,
    chunker_config_hash: str,
    chunk_index: int | None,
    content_hash: str,
    knowledge_id: str | None = None,
    collection_name: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "collection_id": collection_id,
        "file_id": file_id,
        "file_version": file_version,
        "chunker_config_hash": chunker_config_hash,
        "chunk_index": chunk_index,
        "content_hash": content_hash,
    }
    if not collection_id:
        payload["knowledge_id"] = knowledge_id
        payload["collection_name"] = collection_name

    return f"chunk_{_sha256_hex(_canonical_json(payload))}"


class RetrievalChunk(Base):
    __tablename__ = "retrieval_chunk"

    row_id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_uid = Column(Text, nullable=False)

    collection_id = Column(Text, nullable=True)
    knowledge_id = Column(Text, nullable=True)
    collection_name = Column(Text, nullable=True)
    file_id = Column(Text, nullable=True)

    file_version = Column(Integer, nullable=False, default=1)
    chunk_version = Column(Integer, nullable=False, default=1)
    chunk_index = Column(Integer, nullable=True)
    start_index = Column(Integer, nullable=True)

    content_hash = Column(Text, nullable=False)
    chunker_config_hash = Column(Text, nullable=False)
    text = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONField, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    deleted_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("chunk_uid", name="uq_retrieval_chunk_chunk_uid"),
        Index("ix_retrieval_chunk_collection_id", "collection_id"),
        Index("ix_retrieval_chunk_knowledge_id", "knowledge_id"),
        Index("ix_retrieval_chunk_file_id", "file_id"),
        Index("ix_retrieval_chunk_collection_active", "collection_id", "is_active"),
    )


async def fetch_active_chunks_by_chunk_uid(
    chunk_uids: list[str],
    *,
    db: AsyncSession | None = None,
) -> list[RetrievalChunk]:
    """Fetch active manifest chunks in the caller's chunk_uid order."""
    if not chunk_uids:
        return []

    requested_order = {chunk_uid: index for index, chunk_uid in enumerate(dict.fromkeys(chunk_uids))}
    async with get_async_db_context(db) as session:
        result = await session.execute(
            select(RetrievalChunk).where(
                RetrievalChunk.chunk_uid.in_(requested_order.keys()),
                RetrievalChunk.is_active.is_(True),
            )
        )
        rows = result.scalars().all()

    by_uid = {
        row.chunk_uid: row
        for row in rows
        if row.chunk_uid in requested_order and row.is_active
    }
    return [by_uid[chunk_uid] for chunk_uid in requested_order if chunk_uid in by_uid]


def _scope_conditions(
    *,
    collection_id: str | None = None,
    collection_name: str | None = None,
    file_id: str | None = None,
) -> list[Any]:
    if not any((collection_id, collection_name, file_id)):
        raise ValueError("retrieval chunk scope requires collection_id, collection_name, or file_id")

    conditions: list[Any] = []
    collection_conditions = []
    if collection_id:
        collection_conditions.append(RetrievalChunk.collection_id == collection_id)
    if collection_name:
        collection_conditions.append(RetrievalChunk.collection_name == collection_name)
    if collection_conditions:
        conditions.append(or_(*collection_conditions))
    if file_id:
        conditions.append(RetrievalChunk.file_id == file_id)
    return conditions


async def fetch_chunk_uids_for_scope(
    *,
    collection_id: str | None = None,
    collection_name: str | None = None,
    file_id: str | None = None,
    active_only: bool = True,
    db: AsyncSession | None = None,
) -> list[str]:
    conditions = _scope_conditions(
        collection_id=collection_id,
        collection_name=collection_name,
        file_id=file_id,
    )
    if active_only:
        conditions.append(RetrievalChunk.is_active.is_(True))

    async with get_async_db_context(db) as session:
        result = await session.execute(
            select(RetrievalChunk.chunk_uid)
            .where(*conditions)
            .order_by(RetrievalChunk.row_id.asc())
        )
        return list(dict.fromkeys(result.scalars().all()))


async def fetch_all_active_chunk_uids_for_reset(
    *,
    db: AsyncSession | None = None,
) -> list[str]:
    async with get_async_db_context(db) as session:
        result = await session.execute(
            select(RetrievalChunk.chunk_uid)
            .where(RetrievalChunk.is_active.is_(True))
            .order_by(RetrievalChunk.row_id.asc())
        )
        return list(dict.fromkeys(result.scalars().all()))


async def deactivate_all_active_chunks_for_reset(
    *,
    db: AsyncSession | None = None,
    deleted_at: int | None = None,
) -> int:
    deleted_at = int(deleted_at or time.time())
    async with get_async_db_context(db) as session:
        result = await session.execute(
            update(RetrievalChunk)
            .where(RetrievalChunk.is_active.is_(True))
            .values(
                is_active=False,
                deleted_at=deleted_at,
                updated_at=deleted_at,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


async def deactivate_active_chunks(
    *,
    collection_id: str | None = None,
    collection_name: str | None = None,
    file_id: str | None = None,
    db: AsyncSession | None = None,
    deleted_at: int | None = None,
) -> int:
    conditions = [
        RetrievalChunk.is_active.is_(True),
        *_scope_conditions(
            collection_id=collection_id,
            collection_name=collection_name,
            file_id=file_id,
        ),
    ]

    deleted_at = int(deleted_at or time.time())
    async with get_async_db_context(db) as session:
        result = await session.execute(
            update(RetrievalChunk)
            .where(*conditions)
            .values(
                is_active=False,
                deleted_at=deleted_at,
                updated_at=deleted_at,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)
