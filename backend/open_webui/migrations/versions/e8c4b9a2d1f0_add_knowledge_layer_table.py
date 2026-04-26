"""Add knowledge layer table compatibility head

Revision ID: e8c4b9a2d1f0
Revises: 56359461a091
Create Date: 2026-04-25 22:10:00.000000

Fresh installs that start from this repo already create knowledge_file_layer
through the older d4e5/20260327 chain. This head keeps afc44c174-era
databases upgradeable by treating e8c4b9a2d1f0 as the converged, idempotent
target schema for knowledge_file_layer.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e8c4b9a2d1f0"
down_revision: Union[str, None] = "56359461a091"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "knowledge_file_layer"
UNIQUE_NAME = "uq_knowledge_file_layer_identity"
EXPECTED_UNIQUE_COLUMNS = ["knowledge_id", "file_id", "layer_type", "part_index"]
EXPECTED_INDEXES = {
    "idx_knowledge_file_layer_knowledge_id": ["knowledge_id"],
    "idx_knowledge_file_layer_file_id": ["file_id"],
    "idx_knowledge_file_layer_status": ["status"],
}


def _create_full_table() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("knowledge_id", sa.Text(), nullable=False),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("layer_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=True),
        sa.Column("source_ref_id", sa.Text(), nullable=True),
        sa.Column("transformation_ref_id", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "part_index",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "part_total",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("display_title", sa.Text(), nullable=True),
        sa.Column(
            "embedding_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("embedding_error", sa.Text(), nullable=True),
        sa.Column("embedding_updated_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["file.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_id"], ["knowledge.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(*EXPECTED_UNIQUE_COLUMNS, name=UNIQUE_NAME),
    )

    for name, columns in EXPECTED_INDEXES.items():
        op.create_index(name, TABLE_NAME, columns, unique=False)


def _ensure_columns(existing_columns: set[str]) -> None:
    with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
        if "part_index" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "part_index",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("1"),
                )
            )
        if "part_total" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "part_total",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("1"),
                )
            )
        if "display_title" not in existing_columns:
            batch_op.add_column(sa.Column("display_title", sa.Text(), nullable=True))
        if "embedding_status" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "embedding_status",
                    sa.Text(),
                    nullable=False,
                    server_default=sa.text("'pending'"),
                )
            )
        if "embedding_error" not in existing_columns:
            batch_op.add_column(sa.Column("embedding_error", sa.Text(), nullable=True))
        if "embedding_updated_at" not in existing_columns:
            batch_op.add_column(
                sa.Column("embedding_updated_at", sa.BigInteger(), nullable=True)
            )


def _ensure_indexes(existing_indexes: dict[str, list[str]]) -> None:
    for name, columns in EXPECTED_INDEXES.items():
        if existing_indexes.get(name) != columns:
            if name in existing_indexes:
                op.drop_index(name, table_name=TABLE_NAME)
            op.create_index(name, TABLE_NAME, columns, unique=False)


def _ensure_unique_constraint(existing_unique_columns: list[str] | None) -> None:
    if existing_unique_columns == EXPECTED_UNIQUE_COLUMNS:
        return

    with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
        if existing_unique_columns is not None:
            batch_op.drop_constraint(UNIQUE_NAME, type_="unique")
        batch_op.create_unique_constraint(UNIQUE_NAME, EXPECTED_UNIQUE_COLUMNS)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_tables = set(inspector.get_table_names())
    if TABLE_NAME not in existing_tables:
        _create_full_table()
        return

    existing_columns = {col["name"] for col in inspector.get_columns(TABLE_NAME)}
    _ensure_columns(existing_columns)

    inspector = sa.inspect(bind)
    existing_indexes = {
        idx["name"]: list(idx.get("column_names") or [])
        for idx in inspector.get_indexes(TABLE_NAME)
    }
    _ensure_indexes(existing_indexes)

    existing_unique_columns = None
    for unique in inspector.get_unique_constraints(TABLE_NAME):
        if unique.get("name") == UNIQUE_NAME:
            existing_unique_columns = list(unique.get("column_names") or [])
            break
    _ensure_unique_constraint(existing_unique_columns)


def downgrade() -> None:
    op.drop_index("idx_knowledge_file_layer_status", table_name=TABLE_NAME)
    op.drop_index("idx_knowledge_file_layer_file_id", table_name=TABLE_NAME)
    op.drop_index("idx_knowledge_file_layer_knowledge_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
