"""Add automation tables

Revision ID: da5f6a7b8c90
Revises: a3dd5bedd151
Create Date: 2026-03-30 00:00:00.000000

This revision intentionally uses a new ID because afc44c174 reused
d4e5f6a7b8c9, which belongs to the legacy knowledge-layer chain.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "da5f6a7b8c90"
down_revision: Union[str, None] = "a3dd5bedd151"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("last_run_at", sa.BigInteger(), nullable=True),
        sa.Column("next_run_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_automation_next_run", "automation", ["next_run_at"])

    op.create_table(
        "automation_run",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("automation_id", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_automation_run_automation_id",
        "automation_run",
        ["automation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_automation_run_automation_id", table_name="automation_run")
    op.drop_table("automation_run")
    op.drop_index("ix_automation_next_run", table_name="automation")
    op.drop_table("automation")
