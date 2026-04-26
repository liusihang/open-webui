"""Add tasks and summary columns to chat table

Revision ID: a3dd5bedd151
Revises: 20260327_add_knowledge_layer_embedding_state
Create Date: 2026-03-29 22:15:00.000000

This backport wires the afc44c174-era chat branch after the legacy
knowledge-layer chain so databases already stamped at
20260327_add_knowledge_layer_embedding_state remain upgradeable.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3dd5bedd151"
down_revision: Union[str, None] = "20260327_add_knowledge_layer_embedding_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat", sa.Column("tasks", sa.JSON(), nullable=True))
    op.add_column("chat", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat", "summary")
    op.drop_column("chat", "tasks")
