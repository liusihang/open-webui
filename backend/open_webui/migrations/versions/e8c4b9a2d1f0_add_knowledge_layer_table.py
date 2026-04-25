"""add knowledge layer table

Revision ID: e8c4b9a2d1f0
Revises: b2c3d4e5f6a7, 56359461a091
Create Date: 2026-04-25 22:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8c4b9a2d1f0"
down_revision: Union[str, Sequence[str], None] = ("b2c3d4e5f6a7", "56359461a091")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_file_layer",
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
        sa.Column("part_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("part_total", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("display_title", sa.Text(), nullable=True),
        sa.Column("embedding_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("embedding_error", sa.Text(), nullable=True),
        sa.Column("embedding_updated_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["file.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_id"], ["knowledge.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "knowledge_id",
            "file_id",
            "layer_type",
            "part_index",
            name="uq_knowledge_file_layer_identity",
        ),
    )
    op.create_index(
        "idx_knowledge_file_layer_knowledge_id",
        "knowledge_file_layer",
        ["knowledge_id"],
        unique=False,
    )
    op.create_index(
        "idx_knowledge_file_layer_file_id",
        "knowledge_file_layer",
        ["file_id"],
        unique=False,
    )
    op.create_index(
        "idx_knowledge_file_layer_status",
        "knowledge_file_layer",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_knowledge_file_layer_status", table_name="knowledge_file_layer")
    op.drop_index("idx_knowledge_file_layer_file_id", table_name="knowledge_file_layer")
    op.drop_index("idx_knowledge_file_layer_knowledge_id", table_name="knowledge_file_layer")
    op.drop_table("knowledge_file_layer")
