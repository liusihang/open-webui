"""Add immutable administrator-managed conversation mode profiles.

Revision ID: c0d3b4a5e6f7
Revises: f8a9b0c1d2e3
Create Date: 2026-07-26 00:00:00.000000
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c0d3b4a5e6f7'
down_revision: str | None = 'f8a9b0c1d2e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REVISION_TABLE = 'conversation_mode_profile_revision'
HEAD_TABLE = 'conversation_mode_profile_head'
TEMPORARY_BINDING_TABLE = 'conversation_mode_profile_temporary_binding'


def _baseline_revision_id(mode: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f'open-webui:conversation-mode-profile:{mode}:baseline:v1',
        )
    )


CHAT_BASELINE_REVISION_ID = _baseline_revision_id('chat')
AGENT_BASELINE_REVISION_ID = _baseline_revision_id('agent')
BASELINE_CONTENT = {
    'schema_version': 1,
    'system_prompt': '',
    'defaults': {},
}
BASELINE_CONTENT_HASH = hashlib.sha256(
    json.dumps(
        BASELINE_CONTENT,
        allow_nan=False,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')
).hexdigest()


def upgrade() -> None:
    op.create_table(
        REVISION_TABLE,
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('revision_number', sa.Integer(), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=False),
        sa.Column('defaults', sa.JSON(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.Column('restored_from_revision_id', sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ['restored_from_revision_id'],
            [f'{REVISION_TABLE}.id'],
            name='fk_conversation_mode_profile_revision_restored_from',
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'mode',
            'revision_number',
            name='uq_conversation_mode_profile_revision_mode_number',
        ),
    )
    op.create_index(
        'ix_conversation_mode_profile_revision_mode_created_at',
        REVISION_TABLE,
        ['mode', 'created_at'],
    )

    op.create_table(
        HEAD_TABLE,
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('current_revision_id', sa.String(length=36), nullable=False),
        sa.Column('baseline_revision_id', sa.String(length=36), nullable=False),
        sa.Column('cutover_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_by', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ['baseline_revision_id'],
            [f'{REVISION_TABLE}.id'],
            name='fk_conversation_mode_profile_head_baseline_revision',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['current_revision_id'],
            [f'{REVISION_TABLE}.id'],
            name='fk_conversation_mode_profile_head_current_revision',
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('mode'),
    )

    op.create_table(
        TEMPORARY_BINDING_TABLE,
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('temporary_conversation_id', sa.Text(), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('mode_profile_revision_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ['mode_profile_revision_id'],
            [f'{REVISION_TABLE}.id'],
            name='fk_conversation_mode_profile_temporary_binding_revision',
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id',
            'temporary_conversation_id',
            name='uq_conversation_mode_profile_temporary_binding_user_conversation',
        ),
    )
    op.create_index(
        'ix_conversation_mode_profile_temporary_binding_expires_at',
        TEMPORARY_BINDING_TABLE,
        ['expires_at'],
    )

    with op.batch_alter_table('chat') as batch_op:
        batch_op.add_column(sa.Column('mode_profile_revision_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_chat_mode_profile_revision_id',
            REVISION_TABLE,
            ['mode_profile_revision_id'],
            ['id'],
            ondelete='RESTRICT',
        )
        batch_op.create_index(
            'ix_chat_mode_profile_revision_id',
            ['mode_profile_revision_id'],
            unique=False,
        )

    cutover_at = int(time.time())
    revision_rows = [
        {
            'id': CHAT_BASELINE_REVISION_ID,
            'mode': 'chat',
            'revision_number': 1,
            'schema_version': BASELINE_CONTENT['schema_version'],
            'system_prompt': BASELINE_CONTENT['system_prompt'],
            'defaults': BASELINE_CONTENT['defaults'],
            'content_hash': BASELINE_CONTENT_HASH,
            'created_at': cutover_at,
            'created_by': None,
            'restored_from_revision_id': None,
        },
        {
            'id': AGENT_BASELINE_REVISION_ID,
            'mode': 'agent',
            'revision_number': 1,
            'schema_version': BASELINE_CONTENT['schema_version'],
            'system_prompt': BASELINE_CONTENT['system_prompt'],
            'defaults': BASELINE_CONTENT['defaults'],
            'content_hash': BASELINE_CONTENT_HASH,
            'created_at': cutover_at,
            'created_by': None,
            'restored_from_revision_id': None,
        },
    ]
    revision_table = sa.table(
        REVISION_TABLE,
        sa.column('id', sa.String()),
        sa.column('mode', sa.String()),
        sa.column('revision_number', sa.Integer()),
        sa.column('schema_version', sa.Integer()),
        sa.column('system_prompt', sa.Text()),
        sa.column('defaults', sa.JSON()),
        sa.column('content_hash', sa.String()),
        sa.column('created_at', sa.BigInteger()),
        sa.column('created_by', sa.Text()),
        sa.column('restored_from_revision_id', sa.String()),
    )
    op.bulk_insert(revision_table, revision_rows)

    head_table = sa.table(
        HEAD_TABLE,
        sa.column('mode', sa.String()),
        sa.column('current_revision_id', sa.String()),
        sa.column('baseline_revision_id', sa.String()),
        sa.column('cutover_at', sa.BigInteger()),
        sa.column('updated_at', sa.BigInteger()),
        sa.column('updated_by', sa.Text()),
    )
    op.bulk_insert(
        head_table,
        [
            {
                'mode': 'chat',
                'current_revision_id': CHAT_BASELINE_REVISION_ID,
                'baseline_revision_id': CHAT_BASELINE_REVISION_ID,
                'cutover_at': cutover_at,
                'updated_at': cutover_at,
                'updated_by': None,
            },
            {
                'mode': 'agent',
                'current_revision_id': AGENT_BASELINE_REVISION_ID,
                'baseline_revision_id': AGENT_BASELINE_REVISION_ID,
                'cutover_at': cutover_at,
                'updated_at': cutover_at,
                'updated_by': None,
            },
        ],
    )


def downgrade() -> None:
    with op.batch_alter_table('chat') as batch_op:
        batch_op.drop_index('ix_chat_mode_profile_revision_id')
        batch_op.drop_constraint(
            'fk_chat_mode_profile_revision_id',
            type_='foreignkey',
        )
        batch_op.drop_column('mode_profile_revision_id')

    op.drop_index(
        'ix_conversation_mode_profile_temporary_binding_expires_at',
        table_name=TEMPORARY_BINDING_TABLE,
    )
    op.drop_table(TEMPORARY_BINDING_TABLE)
    op.drop_table(HEAD_TABLE)
    op.drop_index(
        'ix_conversation_mode_profile_revision_mode_created_at',
        table_name=REVISION_TABLE,
    )
    op.drop_table(REVISION_TABLE)
