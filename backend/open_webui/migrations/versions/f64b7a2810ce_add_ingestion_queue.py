"""Bounded full-ingestion queue; no existing vectors or jobs are replayed."""

from alembic import op
import sqlalchemy as sa

revision = 'f64b7a2810ce'
down_revision = '72c3a9d1e5b0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ingestion_job',
        sa.Column('job_id', sa.Text(), primary_key=True),
        sa.Column('file_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('knowledge_id', sa.Text()),
        sa.Column('source_hash', sa.Text(), nullable=False),
        sa.Column('config_hash', sa.Text(), nullable=False),
        sa.Column('active_key', sa.Text(), unique=True),
        sa.Column('active_file', sa.Text(), unique=True),
        sa.Column('worker_slot', sa.Integer(), unique=True),
        sa.Column('lease_token', sa.Text()),
        sa.Column('lease_until', sa.BigInteger()),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('result', sa.Text()),
        sa.Column('error', sa.Text()),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('started_at', sa.BigInteger()),
        sa.Column('finished_at', sa.BigInteger()),
        sa.Column('cleaned_at', sa.BigInteger()),
        sa.CheckConstraint('worker_slot IS NULL OR worker_slot IN (0, 1)', name='ck_ingestion_two_slots'),
    )
    op.create_index('ix_ingestion_status_created', 'ingestion_job', ['status', 'created_at'])
    op.create_index('ix_ingestion_file_status', 'ingestion_job', ['file_id', 'status'])
    op.create_index('ix_ingestion_cleanup', 'ingestion_job', ['cleaned_at', 'status', 'finished_at'])
    op.create_table(
        'ingestion_collection',
        sa.Column('user_id', sa.Text(), primary_key=True),
        sa.Column('collection_name', sa.Text(), primary_key=True),
        sa.Column('file_id', sa.Text(), sa.ForeignKey('file.id', ondelete='CASCADE'), nullable=False),
    )


def downgrade():
    op.drop_table('ingestion_collection')
    op.drop_table('ingestion_job')
