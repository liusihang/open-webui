"""Versioned source manifest. Vector identity is never derived again on import."""

import hashlib
import json
from contextlib import asynccontextmanager

from open_webui.internal.db import Base, JSONField, get_async_db_context
from sqlalchemy import BigInteger, Boolean, Column, Index, Integer, Text, UniqueConstraint


@asynccontextmanager
async def core_db(db=None):
    # Explicitly injected sessions must be honored even when global session sharing is disabled.
    if db is not None:
        yield db
    else:
        async with get_async_db_context() as session:
            yield session


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    ).hexdigest()


def compute_content_hash(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError('chunk text must be a string')
    return hashlib.sha256(text.encode()).hexdigest()


def compute_chunker_config_hash(config=None):
    return canonical_hash(config or {})


def compute_chunk_uid(
    *,
    collection_id=None,
    file_id,
    file_version,
    chunker_config_hash,
    chunk_index,
    content_hash,
    knowledge_id=None,
    collection_name=None,
):
    # Exactly the Live UID algorithm; imported explicit UIDs also remain unchanged.
    payload = dict(
        collection_id=collection_id,
        file_id=file_id,
        file_version=file_version,
        chunker_config_hash=chunker_config_hash,
        chunk_index=chunk_index,
        content_hash=content_hash,
    )
    if not collection_id:
        payload.update(knowledge_id=knowledge_id, collection_name=collection_name)
    return 'chunk_' + canonical_hash(payload)


class RetrievalChunk(Base):
    __tablename__ = 'retrieval_chunk'
    row_id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_uid = Column(Text, nullable=False)
    collection_id = Column(Text)
    knowledge_id = Column(Text)
    collection_name = Column(Text, nullable=False)
    file_id = Column(Text, nullable=False)
    file_version = Column(Integer, nullable=False, default=1)
    chunk_version = Column(Integer, nullable=False, default=1)
    chunk_index = Column(Integer)
    start_index = Column(Integer)
    content_hash = Column(Text, nullable=False)
    chunker_config_hash = Column(Text, nullable=False)
    text = Column(Text)
    metadata_ = Column('metadata', JSONField)
    is_active = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(BigInteger)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    __table_args__ = (
        UniqueConstraint('chunk_uid', name='uq_retrieval_chunk_chunk_uid'),
        Index('ix_retrieval_chunk_file_version', 'file_id', 'file_version', 'is_active'),
        Index('ix_retrieval_chunk_collection_active', 'collection_name', 'is_active'),
    )
