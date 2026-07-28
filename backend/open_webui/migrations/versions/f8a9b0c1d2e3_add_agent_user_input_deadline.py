"""Add durable Agent user-input deadlines.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-22 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f8a9b0c1d2e3'
down_revision: str | None = 'e7f8a9b0c1d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = 'agent_run'
INDEX_NAME = 'ix_agent_run_user_input_deadline'
PENDING_USER_INPUT_ID_LENGTH = 512
STATE_LENGTH = 64


def _columns(inspector: sa.Inspector) -> dict[str, dict]:
    if TABLE_NAME not in inspector.get_table_names():
        return {}
    return {column['name']: column for column in inspector.get_columns(TABLE_NAME)}


def _index_names(inspector: sa.Inspector) -> set[str]:
    if TABLE_NAME not in inspector.get_table_names():
        return set()
    return {index['name'] for index in inspector.get_indexes(TABLE_NAME)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    columns = _columns(inspector)
    state_column = columns.get('state')
    state_type = state_column['type'] if state_column is not None else None
    bound_state_for_mysql = (
        bind.dialect.name in {'mysql', 'mariadb'}
        and isinstance(state_type, sa.String)
        and (state_type.length is None or state_type.length > STATE_LENGTH)
    )
    pending_id_column = columns.get('pending_user_input_id')
    pending_id_type = (
        pending_id_column['type'] if pending_id_column is not None else None
    )
    bound_pending_id_for_mysql = (
        bind.dialect.name in {'mysql', 'mariadb'}
        and isinstance(pending_id_type, sa.String)
        and (
            pending_id_type.length is None
            or pending_id_type.length > PENDING_USER_INPUT_ID_LENGTH
        )
    )
    missing_pending_id = 'pending_user_input_id' not in columns
    missing_deadline = 'pending_user_input_expires_at' not in columns
    if (
        bound_state_for_mysql
        or bound_pending_id_for_mysql
        or missing_pending_id
        or missing_deadline
    ):
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            if bound_state_for_mysql:
                batch_op.alter_column(
                    'state',
                    existing_type=state_type,
                    type_=sa.String(length=STATE_LENGTH),
                    existing_nullable=bool(state_column['nullable']),
                )
            if bound_pending_id_for_mysql:
                batch_op.alter_column(
                    'pending_user_input_id',
                    existing_type=pending_id_type,
                    type_=sa.String(length=PENDING_USER_INPUT_ID_LENGTH),
                    existing_nullable=bool(pending_id_column['nullable']),
                )
            if missing_pending_id:
                batch_op.add_column(
                    sa.Column(
                        'pending_user_input_id',
                        sa.String(length=PENDING_USER_INPUT_ID_LENGTH),
                        nullable=True,
                    )
                )
            if missing_deadline:
                batch_op.add_column(
                    sa.Column(
                        'pending_user_input_expires_at',
                        sa.BigInteger(),
                        nullable=True,
                    )
                )

    inspector = sa.inspect(bind)
    if INDEX_NAME not in _index_names(inspector):
        op.create_index(
            INDEX_NAME,
            TABLE_NAME,
            ['state', 'pending_user_input_expires_at'],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    if INDEX_NAME in _index_names(inspector):
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)

    columns = _columns(sa.inspect(bind))
    removable = [
        column
        for column in (
            'pending_user_input_expires_at',
            'pending_user_input_id',
        )
        if column in columns
    ]
    if not removable:
        return
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column in removable:
            batch_op.drop_column(column)
