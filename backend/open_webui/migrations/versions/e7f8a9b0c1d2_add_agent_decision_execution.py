"""Add durable Agent decision execution outbox.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e7f8a9b0c1d2'
down_revision: str | None = 'd6e7f8a9b0c1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = 'agent_run_decision_execution'
ID_LENGTH = 128
KEY_LENGTH = 512
STATUS_LENGTH = 64
TYPE_LENGTH = 64


def _index_names(inspector: sa.Inspector) -> set[str]:
    if TABLE_NAME not in inspector.get_table_names():
        return set()
    return {index['name'] for index in inspector.get_indexes(TABLE_NAME)}


def _ensure_index(
    inspector: sa.Inspector,
    name: str,
    columns: list[str],
) -> None:
    if name not in _index_names(inspector):
        op.create_index(name, TABLE_NAME, columns)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        op.create_table(
            TABLE_NAME,
            sa.Column('id', sa.String(length=ID_LENGTH), nullable=False),
            sa.Column('run_id', sa.String(length=ID_LENGTH), nullable=False),
            sa.Column('resource_type', sa.String(length=TYPE_LENGTH), nullable=False),
            sa.Column('resource_id', sa.String(length=KEY_LENGTH), nullable=False),
            sa.Column('decision', sa.String(length=STATUS_LENGTH), nullable=False),
            sa.Column('command_type', sa.String(length=TYPE_LENGTH), nullable=False),
            sa.Column('command_payload', sa.JSON(), nullable=False),
            sa.Column('fingerprint', sa.String(length=64), nullable=False),
            sa.Column('runtime_session_id', sa.String(length=ID_LENGTH), nullable=False),
            sa.Column('expected_checkpoint_version', sa.Integer(), nullable=False),
            sa.Column('expected_run_state_version', sa.Integer(), nullable=False),
            sa.Column('request_event_seq', sa.Integer(), nullable=False),
            sa.Column('tool_arguments_fingerprint', sa.String(length=64), nullable=True),
            sa.Column('tool_call_idempotency_key', sa.String(length=KEY_LENGTH), nullable=True),
            sa.Column('status', sa.String(length=STATUS_LENGTH), nullable=False),
            sa.Column('claim_owner', sa.String(length=ID_LENGTH), nullable=True),
            sa.Column('claim_token', sa.String(length=ID_LENGTH), nullable=True),
            sa.Column('claimed_at', sa.BigInteger(), nullable=True),
            sa.Column('claim_expires_at', sa.BigInteger(), nullable=True),
            sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('next_attempt_at', sa.BigInteger(), nullable=True),
            sa.Column('prepare_response', sa.JSON(), nullable=True),
            sa.Column('prepared_at', sa.BigInteger(), nullable=True),
            sa.Column('backend_committed_at', sa.BigInteger(), nullable=True),
            sa.Column('completion_event_id', sa.String(length=ID_LENGTH), nullable=True),
            sa.Column('completion_event_seq', sa.Integer(), nullable=True),
            sa.Column('activate_response', sa.JSON(), nullable=True),
            sa.Column('activated_at', sa.BigInteger(), nullable=True),
            sa.Column('runtime_outcome', sa.JSON(), nullable=True),
            sa.Column('outcome_at', sa.BigInteger(), nullable=True),
            sa.Column('last_error', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint('id', name='pk_agent_run_decision_execution'),
            sa.UniqueConstraint(
                'run_id',
                'resource_type',
                'resource_id',
                name='uq_agent_run_decision_resource',
            ),
        )

    inspector = sa.inspect(bind)
    _ensure_index(inspector, 'ix_agent_run_decision_execution_run_id', ['run_id'])
    _ensure_index(inspector, 'ix_agent_run_decision_execution_status', ['status'])
    _ensure_index(inspector, 'ix_agent_run_decision_status_retry', ['status', 'next_attempt_at'])
    _ensure_index(inspector, 'ix_agent_run_decision_run_status', ['run_id', 'status'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing = _index_names(inspector)
    for name in (
        'ix_agent_run_decision_run_status',
        'ix_agent_run_decision_status_retry',
        'ix_agent_run_decision_execution_status',
        'ix_agent_run_decision_execution_run_id',
    ):
        if name in existing:
            op.drop_index(name, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
