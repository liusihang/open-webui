"""Four evidence tables. Assets are physical media, references are citeable identities."""

from open_webui.internal.db import Base, JSONField
from open_webui.models.retrieval_chunks import canonical_hash
from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Index, Integer, Text, UniqueConstraint


def stable_id(prefix, **parts):
    return f'{prefix}_' + canonical_hash(parts)


class KnowledgeEvidenceAsset(Base):
    __tablename__ = 'knowledge_evidence_asset'
    id = Column(Text, primary_key=True)
    asset_ref = Column(Text, nullable=False, unique=True)
    file_id = Column(Text, nullable=False)
    file_version = Column(Integer, nullable=False)
    asset_kind = Column(Text, nullable=False)
    storage_uri = Column(Text, nullable=False)
    sha256 = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=False)
    width = Column(Integer)
    height = Column(Integer)
    page_index = Column(Integer)
    anchor_json = Column(JSONField)
    caption = Column(Text)
    ocr_text = Column(Text)
    surrounding_text = Column(Text)
    status = Column(Text, nullable=False)
    error = Column(Text)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    __table_args__ = (Index('ix_knowledge_evidence_asset_file_version', 'file_id', 'file_version'),)


class KnowledgeEvidence(Base):
    __tablename__ = 'knowledge_evidence'
    id = Column(Text, primary_key=True)
    evidence_ref = Column(Text, nullable=False, unique=True)
    knowledge_id = Column(Text)
    file_id = Column(Text, nullable=False)
    file_version = Column(Integer, nullable=False)
    asset_id = Column(Text, ForeignKey('knowledge_evidence_asset.id'))
    retrieval_chunk_uid = Column(Text)
    modality = Column(Text, nullable=False)
    evidence_kind = Column(Text, nullable=False)
    source_name = Column(Text, nullable=False)
    page_index = Column(Integer)
    anchor_json = Column(JSONField)
    content_hash = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(BigInteger)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    __table_args__ = (
        Index('ix_knowledge_evidence_scope_active', 'knowledge_id', 'is_active'),
        Index('ix_knowledge_evidence_file_version', 'file_id', 'file_version', 'is_active'),
    )


class KnowledgeVectorSpace(Base):
    __tablename__ = 'knowledge_vector_space'
    id = Column(Text, primary_key=True)
    embedding_model = Column(Text, nullable=False)
    model_revision = Column(Text)
    embedding_dim = Column(Integer)
    distance_metric = Column(Text, nullable=False)
    config_fingerprint = Column(Text, nullable=False)
    encoding_config = Column(JSONField, nullable=False)
    verification_status = Column(Text, nullable=False, default='quarantined')
    supports_text_query = Column(Boolean, nullable=False, default=True)
    supports_image_query = Column(Boolean, nullable=False, default=False)
    supports_text_evidence = Column(Boolean, nullable=False, default=True)
    supports_image_evidence = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    __table_args__ = (Index('ix_knowledge_vector_space_verification', 'verification_status', 'active'),)


class KnowledgeEvidenceEmbedding(Base):
    __tablename__ = 'knowledge_evidence_embedding'
    id = Column(Text, primary_key=True)
    evidence_id = Column(Text, ForeignKey('knowledge_evidence.id'), nullable=False)
    evidence_ref = Column(Text, nullable=False)
    vector_space_id = Column(Text, ForeignKey('knowledge_vector_space.id'), nullable=False)
    vector_backend_collection = Column(Text, nullable=False)
    vector_backend_id = Column(Text, nullable=False)
    content_hash = Column(Text, nullable=False)
    config_fingerprint = Column(Text, nullable=False)
    embedding_status = Column(Text, nullable=False)
    embedding_error = Column(Text)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    __table_args__ = (
        UniqueConstraint('evidence_ref', 'vector_space_id', name='uq_evidence_embedding_space'),
        Index('ix_evidence_embedding_vector', 'vector_backend_collection', 'vector_backend_id'),
        Index('ix_evidence_embedding_space_status', 'vector_space_id', 'embedding_status'),
    )
