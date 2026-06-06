"""Add retrieval index jobs and state

Revision ID: c7d8e9f0a1b2
Revises: b6f7c8d9e0a1
Create Date: 2026-06-06 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from open_webui.internal.db import JSONField

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b6f7c8d9e0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JOB_TABLE = "retrieval_index_job"
STATE_TABLE = "retrieval_index_state"
JOB_INDEXES = {
    "ix_retrieval_index_job_status": (JOB_TABLE, ["status"]),
    "ix_retrieval_index_job_kind_status": (JOB_TABLE, ["index_kind", "status"]),
    "ix_retrieval_index_job_collection_id": (JOB_TABLE, ["collection_id"]),
    "ix_retrieval_index_job_file_id": (JOB_TABLE, ["file_id"]),
}
STATE_INDEXES = {
    "ix_retrieval_index_state_kind_status": (STATE_TABLE, ["index_kind", "status"]),
    "ix_retrieval_index_state_collection_id": (STATE_TABLE, ["collection_id"]),
    "ix_retrieval_index_state_file_id": (STATE_TABLE, ["file_id"]),
}


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _existing_index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    if not _table_exists(inspector, table_name):
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, JOB_TABLE):
        _create_job_table()
    if not _table_exists(inspector, STATE_TABLE):
        _create_state_table()

    inspector = sa.inspect(bind)
    for index_name, (table_name, columns) in {**JOB_INDEXES, **STATE_INDEXES}.items():
        if index_name not in _existing_index_names(inspector, table_name):
            op.create_index(index_name, table_name, columns)


def _create_job_table() -> None:
    op.create_table(
        JOB_TABLE,
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("index_kind", sa.Text(), nullable=False),
        sa.Column("collection_id", sa.Text(), nullable=True),
        sa.Column("knowledge_id", sa.Text(), nullable=True),
        sa.Column("collection_name", sa.Text(), nullable=True),
        sa.Column("file_id", sa.Text(), nullable=True),
        sa.Column("chunker_config_hash", sa.Text(), nullable=True),
        sa.Column("target_config_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload", JSONField(), nullable=True),
        sa.Column("result", JSONField(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=True),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("job_id", name="pk_retrieval_index_job"),
        sa.UniqueConstraint("job_id", name="uq_retrieval_index_job_job_id"),
    )


def _create_state_table() -> None:
    op.create_table(
        STATE_TABLE,
        sa.Column("state_id", sa.Text(), nullable=False),
        sa.Column("index_kind", sa.Text(), nullable=False),
        sa.Column("collection_id", sa.Text(), nullable=True),
        sa.Column("knowledge_id", sa.Text(), nullable=True),
        sa.Column("collection_name", sa.Text(), nullable=True),
        sa.Column("file_id", sa.Text(), nullable=True),
        sa.Column("chunker_config_hash", sa.Text(), nullable=True),
        sa.Column("target_config_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("active_chunk_count", sa.Integer(), nullable=True),
        sa.Column("indexed_chunk_count", sa.Integer(), nullable=True),
        sa.Column("last_job_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("state_id", name="pk_retrieval_index_state"),
        sa.UniqueConstraint("state_id", name="uq_retrieval_index_state_state_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for index_name, (table_name, _columns) in reversed(list({**JOB_INDEXES, **STATE_INDEXES}.items())):
        if index_name in _existing_index_names(inspector, table_name):
            op.drop_index(index_name, table_name=table_name)

    if _table_exists(inspector, STATE_TABLE):
        op.drop_table(STATE_TABLE)
    if _table_exists(inspector, JOB_TABLE):
        op.drop_table(JOB_TABLE)
