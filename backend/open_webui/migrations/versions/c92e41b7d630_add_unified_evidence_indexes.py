"""Add the slim evidence/manifest schema on the official v0.12 migration chain.

No Live tables are rewritten, no historical job is replayed, and no vector is calculated.
Import selected Live records separately after classifying their provenance.
"""

import sqlalchemy as sa
from alembic import op

revision = 'c92e41b7d630'
down_revision = 'b8f2c6d91a04'
branch_labels = None
depends_on = None


def _text(name, nullable=True, primary=False):
    return sa.Column(name, sa.Text(), nullable=nullable if not primary else False, primary_key=primary)


def _integer(name, nullable=True):
    return sa.Column(name, sa.Integer(), nullable=nullable)


def _times():
    return [
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    ]


def upgrade():
    # A legacy customized DB requires explicit data/schema mapping, not silent partial DDL.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    names = {
        'retrieval_chunk',
        'retrieval_index_job',
        'retrieval_index_state',
        'knowledge_evidence',
        'knowledge_evidence_asset',
        'knowledge_evidence_embedding',
        'knowledge_vector_space',
    }
    if existing & names:
        raise RuntimeError(
            'Legacy evidence schema already exists; use an isolated official database and explicit import'
        )
    op.create_table(
        'retrieval_chunk',
        sa.Column('row_id', sa.Integer(), primary_key=True, autoincrement=True),
        _text('chunk_uid', False),
        _text('collection_id'),
        _text('knowledge_id'),
        _text('collection_name', False),
        _text('file_id', False),
        _integer('file_version', False),
        _integer('chunk_version', False),
        _integer('chunk_index'),
        _integer('start_index'),
        _text('content_hash', False),
        _text('chunker_config_hash', False),
        _text('text'),
        _text('metadata'),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.BigInteger()),
        *_times(),
        sa.UniqueConstraint('chunk_uid', name='uq_retrieval_chunk_chunk_uid'),
    )
    op.create_index('ix_retrieval_chunk_file_version', 'retrieval_chunk', ['file_id', 'file_version', 'is_active'])
    op.create_index('ix_retrieval_chunk_collection_active', 'retrieval_chunk', ['collection_name', 'is_active'])
    op.create_table(
        'retrieval_index_job',
        _text('job_id', primary=True),
        _text('index_kind', False),
        _text('collection_id'),
        _text('knowledge_id'),
        _text('collection_name'),
        _text('file_id', False),
        _integer('file_version', False),
        _text('executor_version', False),
        _text('chunker_config_hash'),
        _text('target_config_hash'),
        _text('status', False),
        _text('payload'),
        _text('result'),
        _text('error'),
        _integer('retry_count', False),
        _integer('max_retries', False),
        sa.Column('started_at', sa.BigInteger()),
        sa.Column('finished_at', sa.BigInteger()),
        *_times(),
    )
    op.create_index('ix_retrieval_job_executor_status', 'retrieval_index_job', ['executor_version', 'status'])
    op.create_index('ix_retrieval_index_job_file_id', 'retrieval_index_job', ['file_id'])
    op.create_table(
        'retrieval_index_state',
        _text('state_id', primary=True),
        _text('index_kind', False),
        _text('knowledge_id'),
        _text('collection_name'),
        _text('file_id', False),
        _integer('file_version', False),
        _text('target_config_hash'),
        _text('status', False),
        _integer('active_chunk_count'),
        _integer('indexed_chunk_count'),
        _text('last_job_id'),
        _text('error'),
        *_times(),
    )
    op.create_index('ix_retrieval_index_state_file_version', 'retrieval_index_state', ['file_id', 'file_version'])
    op.create_table(
        'knowledge_evidence_asset',
        _text('id', primary=True),
        _text('asset_ref', False),
        _text('file_id', False),
        _integer('file_version', False),
        _text('asset_kind', False),
        _text('storage_uri', False),
        _text('sha256', False),
        _text('mime_type', False),
        _integer('width'),
        _integer('height'),
        _integer('page_index'),
        _text('anchor_json'),
        _text('caption'),
        _text('ocr_text'),
        _text('surrounding_text'),
        _text('status', False),
        _text('error'),
        *_times(),
        sa.UniqueConstraint('asset_ref'),
    )
    op.create_index('ix_knowledge_evidence_asset_file_version', 'knowledge_evidence_asset', ['file_id', 'file_version'])
    op.create_table(
        'knowledge_evidence',
        _text('id', primary=True),
        _text('evidence_ref', False),
        _text('knowledge_id'),
        _text('file_id', False),
        _integer('file_version', False),
        sa.Column('asset_id', sa.Text(), sa.ForeignKey('knowledge_evidence_asset.id')),
        _text('retrieval_chunk_uid'),
        _text('modality', False),
        _text('evidence_kind', False),
        _text('source_name', False),
        _integer('page_index'),
        _text('anchor_json'),
        _text('content_hash', False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.BigInteger()),
        *_times(),
        sa.UniqueConstraint('evidence_ref'),
    )
    op.create_index('ix_knowledge_evidence_scope_active', 'knowledge_evidence', ['knowledge_id', 'is_active'])
    op.create_index(
        'ix_knowledge_evidence_file_version', 'knowledge_evidence', ['file_id', 'file_version', 'is_active']
    )
    op.create_table(
        'knowledge_vector_space',
        _text('id', primary=True),
        _text('embedding_model', False),
        _text('model_revision'),
        _integer('embedding_dim'),
        _text('distance_metric', False),
        _text('config_fingerprint', False),
        _text('encoding_config', False),
        _text('verification_status', False),
        *[
            sa.Column(name, sa.Boolean(), nullable=False)
            for name in (
                'supports_text_query',
                'supports_image_query',
                'supports_text_evidence',
                'supports_image_evidence',
                'active',
            )
        ],
        *_times(),
    )
    op.create_index(
        'ix_knowledge_vector_space_verification', 'knowledge_vector_space', ['verification_status', 'active']
    )
    op.create_table(
        'knowledge_evidence_embedding',
        _text('id', primary=True),
        sa.Column('evidence_id', sa.Text(), sa.ForeignKey('knowledge_evidence.id'), nullable=False),
        _text('evidence_ref', False),
        sa.Column('vector_space_id', sa.Text(), sa.ForeignKey('knowledge_vector_space.id'), nullable=False),
        _text('vector_backend_collection', False),
        _text('vector_backend_id', False),
        _text('content_hash', False),
        _text('config_fingerprint', False),
        _text('embedding_status', False),
        _text('embedding_error'),
        *_times(),
        sa.UniqueConstraint('evidence_ref', 'vector_space_id', name='uq_evidence_embedding_space'),
    )
    op.create_index(
        'ix_evidence_embedding_vector',
        'knowledge_evidence_embedding',
        ['vector_backend_collection', 'vector_backend_id'],
    )
    op.create_index(
        'ix_evidence_embedding_space_status', 'knowledge_evidence_embedding', ['vector_space_id', 'embedding_status']
    )


def downgrade():
    for table in (
        'knowledge_evidence_embedding',
        'knowledge_vector_space',
        'knowledge_evidence',
        'knowledge_evidence_asset',
        'retrieval_index_state',
        'retrieval_index_job',
        'retrieval_chunk',
    ):
        op.drop_table(table)
