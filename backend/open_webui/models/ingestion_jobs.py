"""Durable full-ingestion jobs, separate from historical evidence backfills."""

from open_webui.internal.db import Base, JSONField
from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, Integer, Text


class IngestionJob(Base):
    __tablename__ = 'ingestion_job'
    job_id = Column(Text, primary_key=True)
    file_id = Column(Text, nullable=False)
    user_id = Column(Text, nullable=False)
    knowledge_id = Column(Text)
    source_hash = Column(Text, nullable=False)
    config_hash = Column(Text, nullable=False)
    # NULL at terminal states; uniqueness applies to concurrent work only.
    active_key = Column(Text, unique=True)
    active_file = Column(Text, unique=True)
    worker_slot = Column(Integer, unique=True)
    lease_token = Column(Text)
    lease_until = Column(BigInteger)
    status = Column(Text, nullable=False)
    payload = Column(JSONField, nullable=False)
    result = Column(JSONField)
    error = Column(Text)
    created_at = Column(BigInteger, nullable=False)
    started_at = Column(BigInteger)
    finished_at = Column(BigInteger)
    cleaned_at = Column(BigInteger)
    __table_args__ = (
        CheckConstraint('worker_slot IS NULL OR worker_slot IN (0, 1)', name='ck_ingestion_two_slots'),
        Index('ix_ingestion_status_created', 'status', 'created_at'),
        Index('ix_ingestion_file_status', 'file_id', 'status'),
        Index('ix_ingestion_cleanup', 'cleaned_at', 'status', 'finished_at'),
    )


class IngestionCollection(Base):
    """Owner-bound logical collection -> last successfully published source."""

    __tablename__ = 'ingestion_collection'
    user_id = Column(Text, primary_key=True)
    collection_name = Column(Text, primary_key=True)
    file_id = Column(Text, ForeignKey('file.id', ondelete='CASCADE'), nullable=False)
