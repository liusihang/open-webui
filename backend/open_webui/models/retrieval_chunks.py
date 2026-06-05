from __future__ import annotations

import hashlib
import json
from typing import Any

from open_webui.internal.db import Base, JSONField
from sqlalchemy import BigInteger, Boolean, Column, Index, Integer, Text, UniqueConstraint


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
