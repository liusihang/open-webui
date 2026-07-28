"""add unique normalized user email index

Revision ID: f0bd01a18a3d
Revises: 959eaac8f909
Create Date: 2026-07-27 04:41:12.708743

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op


# revision identifiers, used by Alembic.
revision: str = 'f0bd01a18a3d'
down_revision: str | None = '959eaac8f909'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = 'uq_user_email_lower'
EMAIL_IS_NOT_NULL = sa.text('email IS NOT NULL')
LOWER_EMAIL = sa.func.lower(sa.column('email'))
USER_TABLE = sa.table(sa.sql.quoted_name('user', quote=True), sa.column('email'))


def _index_exists() -> bool:
    conn = op.get_bind()
    if conn.dialect.name == 'sqlite':
        return (
            conn.execute(
                sa.text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :name"),
                {'name': INDEX_NAME},
            ).first()
            is not None
        )
    inspector = sa.inspect(conn)
    return INDEX_NAME in {index['name'] for index in inspector.get_indexes('user')}


def _duplicate_email_query() -> sa.Select:
    normalized_email = sa.func.lower(USER_TABLE.c.email)
    duplicate_count = sa.func.count()
    return (
        sa.select(
            normalized_email.label('email'),
            duplicate_count.label('duplicate_count'),
        )
        .where(USER_TABLE.c.email.is_not(None))
        .group_by(normalized_email)
        .having(duplicate_count > 1)
        .order_by(normalized_email)
    )


def _duplicate_emails() -> list:
    return op.get_bind().execute(_duplicate_email_query()).fetchall()


def _create_index() -> None:
    op.create_index(
        INDEX_NAME,
        'user',
        [LOWER_EMAIL],
        unique=True,
        postgresql_where=EMAIL_IS_NOT_NULL,
        sqlite_where=EMAIL_IS_NOT_NULL,
    )


def upgrade() -> None:
    if context.is_offline_mode():
        _create_index()
        return

    if _index_exists():
        return

    duplicates = _duplicate_emails()
    if duplicates:
        details = ', '.join(f'{row.email} (x{row.duplicate_count})' for row in duplicates)
        raise RuntimeError(
            'Cannot add unique normalized user email index because duplicate emails exist: '
            f'{details}. Merge or remove the duplicate users and rerun migrations.'
        )

    _create_index()


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_index(INDEX_NAME, table_name='user')
        return

    if _index_exists():
        op.drop_index(INDEX_NAME, table_name='user')
