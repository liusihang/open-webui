"""Add agent memory foundation tables

Revision ID: f3a4b5c7d8e9
Revises: e2f3a4b5c7
Create Date: 2026-06-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c7d8e9"
down_revision: str | None = "e2f3a4b5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = {
    "agent_memory_extraction_cache": [
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.Text(), nullable=False),
        sa.Column("source_updated_at", sa.BigInteger(), nullable=False),
        sa.Column("raw_memory", sa.Text(), nullable=False),
        sa.Column("rollout_summary", sa.Text(), nullable=False),
        sa.Column("rollout_slug", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "chat_id", name="pk_agent_memory_extraction_cache"),
    ],
    "agent_memory_extraction_job": [
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("lease_until", sa.BigInteger(), nullable=True),
        sa.Column("retry_at", sa.BigInteger(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "chat_id", name="pk_agent_memory_extraction_job"),
    ],
    "agent_memory_consolidation_job": [
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("lease_until", sa.BigInteger(), nullable=True),
        sa.Column("retry_at", sa.BigInteger(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "user_id",
            "scope_type",
            "scope_id",
            name="pk_agent_memory_consolidation_job",
        ),
    ],
    "agent_memory_artifact": [
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("note_id", sa.Text(), nullable=True),
        sa.Column("note_content_hash", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "user_id",
            "scope_type",
            "scope_id",
            "path",
            name="pk_agent_memory_artifact",
        ),
    ],
}

INDEXES = {
    "ix_agent_memory_extraction_job_claim": (
        "agent_memory_extraction_job",
        ["status", "retry_at", "lease_until", "updated_at", "chat_id"],
    ),
    "ix_agent_memory_consolidation_job_claim": (
        "agent_memory_consolidation_job",
        ["status", "retry_at", "lease_until", "updated_at", "scope_type", "scope_id"],
    ),
}


def _existing_table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _existing_index_names(table_name: str) -> set[str]:
    existing_tables = _existing_table_names()
    if table_name not in existing_tables:
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    existing_tables = _existing_table_names()
    for table_name, columns in TABLES.items():
        if table_name not in existing_tables:
            op.create_table(table_name, *columns)

    for index_name, (table_name, columns) in INDEXES.items():
        if index_name not in _existing_index_names(table_name):
            op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    existing_tables = _existing_table_names()
    for index_name, (table_name, _columns) in reversed(list(INDEXES.items())):
        if table_name in existing_tables and index_name in _existing_index_names(table_name):
            op.drop_index(index_name, table_name=table_name)

    for table_name in reversed(TABLES):
        if table_name in existing_tables:
            op.drop_table(table_name)
