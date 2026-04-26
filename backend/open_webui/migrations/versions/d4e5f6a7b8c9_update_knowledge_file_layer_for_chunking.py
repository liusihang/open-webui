"""Update knowledge_file_layer table for chunked rows

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-25 21:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_file_layer", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "part_index",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "part_total",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.add_column(sa.Column("display_title", sa.Text(), nullable=True))
        batch_op.drop_constraint(
            "uq_knowledge_file_layer_identity",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_knowledge_file_layer_identity",
            ["knowledge_id", "file_id", "layer_type", "part_index"],
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_file_layer", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_knowledge_file_layer_identity",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_knowledge_file_layer_identity",
            ["knowledge_id", "file_id", "layer_type"],
        )
        batch_op.drop_column("display_title")
        batch_op.drop_column("part_total")
        batch_op.drop_column("part_index")
