"""Add knowledge layer embedding state

Revision ID: 20260327_add_knowledge_layer_embedding_state
Revises: d4e5f6a7b8c9
Create Date: 2026-03-27 01:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260327_add_knowledge_layer_embedding_state"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_file_layer", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "embedding_status",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'pending'"),
            )
        )
        batch_op.add_column(sa.Column("embedding_error", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("embedding_updated_at", sa.BigInteger(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_file_layer", schema=None) as batch_op:
        batch_op.drop_column("embedding_updated_at")
        batch_op.drop_column("embedding_error")
        batch_op.drop_column("embedding_status")
