"""One durable job queue; historical Live operations are deliberately not replayed."""

from open_webui.internal.db import Base, JSONField
from sqlalchemy import BigInteger, Column, Index, Integer, Text

EXECUTOR_VERSION = 'evidence-v1'
INDEX_KINDS = ('map_text', 'image', 'lexical', 'delete')


class RetrievalIndexJob(Base):
    __tablename__ = 'retrieval_index_job'
    job_id = Column(Text, primary_key=True)
    index_kind = Column(Text, nullable=False)
    collection_id = Column(Text)
    knowledge_id = Column(Text)
    collection_name = Column(Text)
    file_id = Column(Text, nullable=False)
    file_version = Column(Integer, nullable=False)
    executor_version = Column(Text, nullable=False)
    chunker_config_hash = Column(Text)
    target_config_hash = Column(Text)
    status = Column(Text, nullable=False)
    payload = Column(JSONField)
    result = Column(JSONField)
    error = Column(Text)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    created_at = Column(BigInteger, nullable=False)
    started_at = Column(BigInteger)
    finished_at = Column(BigInteger)
    updated_at = Column(BigInteger, nullable=False)
    __table_args__ = (
        Index('ix_retrieval_job_executor_status', 'executor_version', 'status'),
        Index('ix_retrieval_index_job_file_id', 'file_id'),
    )


class RetrievalIndexState(Base):
    __tablename__ = 'retrieval_index_state'
    state_id = Column(Text, primary_key=True)
    index_kind = Column(Text, nullable=False)
    knowledge_id = Column(Text)
    collection_name = Column(Text)
    file_id = Column(Text, nullable=False)
    file_version = Column(Integer, nullable=False)
    target_config_hash = Column(Text)
    status = Column(Text, nullable=False)
    active_chunk_count = Column(Integer)
    indexed_chunk_count = Column(Integer)
    last_job_id = Column(Text)
    error = Column(Text)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    __table_args__ = (Index('ix_retrieval_index_state_file_version', 'file_id', 'file_version'),)
