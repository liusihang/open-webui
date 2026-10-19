"""Add server-only idle tool output pruning state.

Revision ID: 72c3a9d1e5b0
Revises: d41e823f01ac
"""

import sqlalchemy as sa
from alembic import op

revision = '72c3a9d1e5b0'
down_revision = 'd41e823f01ac'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chat', sa.Column('tool_pruning', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('chat', 'tool_pruning')
