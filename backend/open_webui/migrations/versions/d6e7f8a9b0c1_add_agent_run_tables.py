"""Add agent run tables

Revision ID: d6e7f8a9b0c1
Revises: f3a4b5c7d8e9
Create Date: 2026-06-18 01:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd6e7f8a9b0c1'
down_revision: str | None = 'f3a4b5c7d8e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = {
    'agent_run',
    'agent_run_event',
    'agent_artifact',
    'agent_run_operation',
}


def _tables(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    if table_name not in _tables(inspector):
        return set()
    return {index['name'] for index in inspector.get_indexes(table_name)}


def _ensure_index(inspector: sa.Inspector, name: str, table_name: str, columns: list[str]) -> None:
    if name not in _index_names(inspector, table_name):
        op.create_index(name, table_name, columns)


def _drop_table_with_indexes(bind: sa.engine.Connection, table_name: str, index_names: Sequence[str]) -> None:
    inspector = sa.inspect(bind)
    if table_name not in _tables(inspector):
        return

    existing_indexes = _index_names(inspector, table_name)
    for index_name in index_names:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=table_name)
    op.drop_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _tables(inspector)

    if 'agent_run' not in existing_tables:
        op.create_table(
            'agent_run',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('chat_id', sa.Text(), nullable=False),
            sa.Column('user_message_id', sa.Text(), nullable=False),
            sa.Column('assistant_message_id', sa.Text(), nullable=False),
            sa.Column('state', sa.Text(), nullable=False),
            sa.Column('state_version', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('leader_model_id', sa.Text(), nullable=False),
            sa.Column('runtime_session_id', sa.Text(), nullable=True),
            sa.Column('budget', sa.JSON(), nullable=True),
            sa.Column('participants', sa.JSON(), nullable=True),
            sa.Column('tool_access_snapshot', sa.JSON(), nullable=True),
            sa.Column('model_catalog_snapshot', sa.JSON(), nullable=True),
            sa.Column('process_refs', sa.JSON(), nullable=True),
            sa.Column('summary', sa.JSON(), nullable=True),
            sa.Column('error', sa.JSON(), nullable=True),
            sa.Column('final_text', sa.Text(), nullable=False, server_default=''),
            sa.Column('final_delta_state', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
            sa.Column('started_at', sa.BigInteger(), nullable=True),
            sa.Column('ended_at', sa.BigInteger(), nullable=True),
            sa.PrimaryKeyConstraint('id', name='pk_agent_run'),
        )

    inspector = sa.inspect(bind)
    _ensure_index(inspector, 'ix_agent_run_chat_id', 'agent_run', ['chat_id'])
    _ensure_index(inspector, 'ix_agent_run_state', 'agent_run', ['state'])
    _ensure_index(inspector, 'ix_agent_run_user_id', 'agent_run', ['user_id'])
    _ensure_index(inspector, 'ix_agent_run_chat_created', 'agent_run', ['chat_id', 'created_at'])
    _ensure_index(inspector, 'ix_agent_run_user_created', 'agent_run', ['user_id', 'created_at'])
    _ensure_index(inspector, 'ix_agent_run_state_updated', 'agent_run', ['state', 'updated_at'])

    existing_tables = _tables(sa.inspect(bind))
    if 'agent_run_event' not in existing_tables:
        op.create_table(
            'agent_run_event',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('run_id', sa.Text(), nullable=False),
            sa.Column('seq', sa.Integer(), nullable=False),
            sa.Column('event_type', sa.Text(), nullable=False),
            sa.Column('participant_id', sa.Text(), nullable=True),
            sa.Column('phase', sa.Text(), nullable=True),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('payload', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint('id', name='pk_agent_run_event'),
            sa.UniqueConstraint('run_id', 'seq', name='uq_agent_run_event_run_seq'),
        )

    inspector = sa.inspect(bind)
    _ensure_index(inspector, 'ix_agent_run_event_run_id', 'agent_run_event', ['run_id'])
    _ensure_index(inspector, 'ix_agent_run_event_run_seq', 'agent_run_event', ['run_id', 'seq'])
    _ensure_index(inspector, 'ix_agent_run_event_type', 'agent_run_event', ['event_type'])

    existing_tables = _tables(sa.inspect(bind))
    if 'agent_artifact' not in existing_tables:
        op.create_table(
            'agent_artifact',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('run_id', sa.Text(), nullable=False),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('kind', sa.Text(), nullable=False),
            sa.Column('terminal_server_id', sa.Text(), nullable=True),
            sa.Column('path', sa.Text(), nullable=False),
            sa.Column('url', sa.Text(), nullable=True),
            sa.Column('mime_type', sa.Text(), nullable=True),
            sa.Column('size', sa.BigInteger(), nullable=True),
            sa.Column('metadata', sa.JSON(), nullable=True),
            sa.Column('idempotency_key', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint('id', name='pk_agent_artifact'),
            sa.UniqueConstraint('run_id', 'path', 'kind', name='uq_agent_artifact_run_path_kind'),
            sa.UniqueConstraint('run_id', 'idempotency_key', name='uq_agent_artifact_run_idempotency'),
        )

    inspector = sa.inspect(bind)
    _ensure_index(inspector, 'ix_agent_artifact_run_id', 'agent_artifact', ['run_id'])
    _ensure_index(inspector, 'ix_agent_artifact_user_id', 'agent_artifact', ['user_id'])
    _ensure_index(inspector, 'ix_agent_artifact_run_path_kind', 'agent_artifact', ['run_id', 'path', 'kind'])

    existing_tables = _tables(sa.inspect(bind))
    if 'agent_run_operation' not in existing_tables:
        op.create_table(
            'agent_run_operation',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('run_id', sa.Text(), nullable=False),
            sa.Column('operation_type', sa.Text(), nullable=False),
            sa.Column('idempotency_key', sa.Text(), nullable=False),
            sa.Column('request_hash', sa.Text(), nullable=False),
            sa.Column('status', sa.Text(), nullable=False),
            sa.Column('response', sa.JSON(), nullable=True),
            sa.Column('error', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint('id', name='pk_agent_run_operation'),
            sa.UniqueConstraint('run_id', 'operation_type', 'idempotency_key', name='uq_agent_run_operation_key'),
        )

    inspector = sa.inspect(bind)
    _ensure_index(inspector, 'ix_agent_run_operation_run_id', 'agent_run_operation', ['run_id'])
    _ensure_index(inspector, 'ix_agent_run_operation_operation_type', 'agent_run_operation', ['operation_type'])
    _ensure_index(inspector, 'ix_agent_run_operation_run_type', 'agent_run_operation', ['run_id', 'operation_type'])


def downgrade() -> None:
    bind = op.get_bind()
    _drop_table_with_indexes(
        bind,
        'agent_run_operation',
        (
            'ix_agent_run_operation_run_type',
            'ix_agent_run_operation_operation_type',
            'ix_agent_run_operation_run_id',
        ),
    )
    _drop_table_with_indexes(
        bind,
        'agent_artifact',
        (
            'ix_agent_artifact_run_path_kind',
            'ix_agent_artifact_user_id',
            'ix_agent_artifact_run_id',
        ),
    )
    _drop_table_with_indexes(
        bind,
        'agent_run_event',
        (
            'ix_agent_run_event_type',
            'ix_agent_run_event_run_seq',
            'ix_agent_run_event_run_id',
        ),
    )
    _drop_table_with_indexes(
        bind,
        'agent_run',
        (
            'ix_agent_run_state_updated',
            'ix_agent_run_user_created',
            'ix_agent_run_chat_created',
            'ix_agent_run_user_id',
            'ix_agent_run_state',
            'ix_agent_run_chat_id',
        ),
    )
