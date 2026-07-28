"""Add retrieval chunk manifest

Revision ID: b6f7c8d9e0a1
Revises: 461111b60977
Create Date: 2026-06-05 16:24:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from open_webui.internal.db import JSONField

revision: str = "b6f7c8d9e0a1"
down_revision: Union[str, None] = "461111b60977"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "retrieval_chunk"
INDEXES = {
    "ix_retrieval_chunk_collection_id": ["collection_id"],
    "ix_retrieval_chunk_knowledge_id": ["knowledge_id"],
    "ix_retrieval_chunk_file_id": ["file_id"],
    "ix_retrieval_chunk_collection_active": ["collection_id", "is_active"],
}


def _table_exists(inspector: sa.Inspector) -> bool:
    return TABLE_NAME in inspector.get_table_names()


def _existing_index_names(inspector: sa.Inspector) -> set[str]:
    if not _table_exists(inspector):
        return set()
    return {index["name"] for index in inspector.get_indexes(TABLE_NAME)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector):
        _create_retrieval_chunk_table()

    inspector = sa.inspect(bind)
    existing_indexes = _existing_index_names(inspector)
    for index_name, columns in INDEXES.items():
        if index_name not in existing_indexes:
            op.create_index(index_name, TABLE_NAME, columns)


def _create_retrieval_chunk_table() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("row_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chunk_uid", sa.Text(), nullable=False),
        sa.Column("collection_id", sa.Text(), nullable=True),
        sa.Column("knowledge_id", sa.Text(), nullable=True),
        sa.Column("collection_name", sa.Text(), nullable=True),
        sa.Column("file_id", sa.Text(), nullable=True),
        sa.Column("file_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("chunk_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("start_index", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("chunker_config_hash", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("metadata", JSONField(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.sql.expression.true(), nullable=False),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("row_id", name="pk_retrieval_chunk"),
        sa.UniqueConstraint("chunk_uid", name="uq_retrieval_chunk_chunk_uid"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _table_exists(inspector):
        return

    existing_indexes = _existing_index_names(inspector)
    for index_name in reversed(INDEXES):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=TABLE_NAME)

    op.drop_table(TABLE_NAME)
