"""Add skill package table

Revision ID: e2f3a4b5c7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from open_webui.internal.db import JSONField

revision: str = 'e2f3a4b5c7'
down_revision: str | None = 'd1e2f3a4b5c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = 'skill_package'
INDEXES = {
    'ix_skill_package_skill_id': ['skill_id'],
    'ix_skill_package_bundle_hash': ['bundle_hash'],
}


def _table_exists(inspector: sa.Inspector) -> bool:
    return TABLE_NAME in inspector.get_table_names()


def _existing_index_names(inspector: sa.Inspector) -> set[str]:
    if not _table_exists(inspector):
        return set()
    return {index['name'] for index in inspector.get_indexes(TABLE_NAME)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector):
        op.create_table(
            TABLE_NAME,
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('skill_id', sa.String(), sa.ForeignKey('skill.id', ondelete='CASCADE'), nullable=False),
            sa.Column('bundle_hash', sa.String(), nullable=False),
            sa.Column('manifest', JSONField(), nullable=False),
            sa.Column('storage_path', sa.Text(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint('id', name='pk_skill_package'),
            sa.UniqueConstraint('skill_id', 'bundle_hash', name='uq_skill_package_skill_bundle'),
        )

    inspector = sa.inspect(bind)
    existing_indexes = _existing_index_names(inspector)
    for index_name, columns in INDEXES.items():
        if index_name not in existing_indexes:
            op.create_index(index_name, TABLE_NAME, columns)


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
