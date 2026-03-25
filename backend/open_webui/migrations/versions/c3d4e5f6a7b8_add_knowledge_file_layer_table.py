"""Add knowledge_file_layer table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-25 17:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_file_layer",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column(
            "knowledge_id",
            sa.Text(),
            sa.ForeignKey("knowledge.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            sa.Text(),
            sa.ForeignKey("file.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("layer_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=True),
        sa.Column("source_ref_id", sa.Text(), nullable=True),
        sa.Column("transformation_ref_id", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "knowledge_id",
            "file_id",
            "layer_type",
            name="uq_knowledge_file_layer_identity",
        ),
    )
    op.create_index(
        "idx_knowledge_file_layer_knowledge_id",
        "knowledge_file_layer",
        ["knowledge_id"],
    )
    op.create_index(
        "idx_knowledge_file_layer_file_id", "knowledge_file_layer", ["file_id"]
    )
    op.create_index(
        "idx_knowledge_file_layer_status", "knowledge_file_layer", ["status"]
    )


def downgrade() -> None:
    op.drop_index("idx_knowledge_file_layer_status", table_name="knowledge_file_layer")
    op.drop_index("idx_knowledge_file_layer_file_id", table_name="knowledge_file_layer")
    op.drop_index(
        "idx_knowledge_file_layer_knowledge_id", table_name="knowledge_file_layer"
    )
    op.drop_table("knowledge_file_layer")
