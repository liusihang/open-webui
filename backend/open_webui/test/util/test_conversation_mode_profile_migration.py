from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from open_webui.agent.conversation_mode_profiles import ConversationModeProfile
from open_webui.migrations.versions import (
    c0d3b4a5e6f7_add_conversation_mode_profiles as profile_migration,
)


def _run_migration(engine: sa.Engine, direction: str) -> None:
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(profile_migration, 'op', operations):
            getattr(profile_migration, direction)()


def _create_pre_profile_schema(engine: sa.Engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        'chat',
        metadata,
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('title', sa.Text(), nullable=False),
    )
    sa.Table(
        'unrelated_legacy_table',
        metadata,
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('value', sa.Text(), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO chat (id, title) VALUES ('chat-1', 'Legacy')"))
        connection.execute(sa.text("INSERT INTO unrelated_legacy_table (id, value) VALUES (1, 'keep-me')"))


def _decode_json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def test_profile_migration_adds_hash_valid_baselines_heads_and_bindings() -> None:
    engine = sa.create_engine('sqlite:///:memory:')
    _create_pre_profile_schema(engine)

    assert profile_migration.down_revision == 'f8a9b0c1d2e3'
    _run_migration(engine, 'upgrade')

    inspector = sa.inspect(engine)
    assert {
        'conversation_mode_profile_head',
        'conversation_mode_profile_revision',
        'conversation_mode_profile_temporary_binding',
    }.issubset(inspector.get_table_names())

    chat_columns = {column['name']: column for column in inspector.get_columns('chat')}
    assert chat_columns['mode_profile_revision_id']['nullable'] is True
    assert {index['name']: index['column_names'] for index in inspector.get_indexes('chat')}[
        'ix_chat_mode_profile_revision_id'
    ] == ['mode_profile_revision_id']
    chat_foreign_keys = {foreign_key['name']: foreign_key for foreign_key in inspector.get_foreign_keys('chat')}
    assert chat_foreign_keys['fk_chat_mode_profile_revision_id']['referred_table'] == (
        'conversation_mode_profile_revision'
    )

    with engine.connect() as connection:
        revisions = (
            connection.execute(
                sa.text(
                    'SELECT id, mode, revision_number, schema_version, system_prompt, defaults, '
                    'content_hash, restored_from_revision_id '
                    'FROM conversation_mode_profile_revision ORDER BY mode'
                )
            )
            .mappings()
            .all()
        )
        heads = (
            connection.execute(
                sa.text(
                    'SELECT mode, current_revision_id, baseline_revision_id, cutover_at, updated_at '
                    'FROM conversation_mode_profile_head ORDER BY mode'
                )
            )
            .mappings()
            .all()
        )

    assert [row['mode'] for row in revisions] == ['agent', 'chat']
    assert {row['id'] for row in revisions} == {
        profile_migration.AGENT_BASELINE_REVISION_ID,
        profile_migration.CHAT_BASELINE_REVISION_ID,
    }
    assert len({row['content_hash'] for row in revisions}) == 1
    for row in revisions:
        content = {
            'schema_version': row['schema_version'],
            'system_prompt': row['system_prompt'],
            'defaults': _decode_json(row['defaults']),
        }
        profile = ConversationModeProfile.from_mapping(row['mode'], content)
        assert profile.system_prompt == ''
        assert profile.defaults.to_dict() == {}
        assert profile.content_hash == row['content_hash']
        assert row['revision_number'] == 1
        assert row['restored_from_revision_id'] is None

    revisions_by_mode = {row['mode']: row for row in revisions}
    assert [row['mode'] for row in heads] == ['agent', 'chat']
    assert len({row['cutover_at'] for row in heads}) == 1
    for head in heads:
        baseline = revisions_by_mode[head['mode']]
        assert head['current_revision_id'] == baseline['id']
        assert head['baseline_revision_id'] == baseline['id']
        assert head['cutover_at'] > 0
        assert head['updated_at'] == head['cutover_at']

    temporary_indexes = {
        index['name']: index['column_names']
        for index in inspector.get_indexes('conversation_mode_profile_temporary_binding')
    }
    assert temporary_indexes['ix_conversation_mode_profile_temporary_binding_expires_at'] == ['expires_at']
    temporary_uniques = {
        constraint['name']: constraint['column_names']
        for constraint in inspector.get_unique_constraints('conversation_mode_profile_temporary_binding')
    }
    assert temporary_uniques['uq_conversation_mode_profile_temporary_binding_user_conversation'] == [
        'user_id',
        'temporary_conversation_id',
    ]

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                'INSERT INTO conversation_mode_profile_temporary_binding '
                '(id, user_id, temporary_conversation_id, mode, mode_profile_revision_id, '
                'created_at, updated_at, expires_at) '
                'VALUES (:id, :user_id, :conversation_id, :mode, :revision_id, 1, 1, 10)'
            ),
            {
                'id': 'binding-1',
                'user_id': 'user-1',
                'conversation_id': 'temporary-1',
                'mode': 'chat',
                'revision_id': profile_migration.CHAT_BASELINE_REVISION_ID,
            },
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    'INSERT INTO conversation_mode_profile_temporary_binding '
                    '(id, user_id, temporary_conversation_id, mode, mode_profile_revision_id, '
                    'created_at, updated_at, expires_at) '
                    'VALUES (:id, :user_id, :conversation_id, :mode, :revision_id, 1, 1, 10)'
                ),
                {
                    'id': 'binding-2',
                    'user_id': 'user-1',
                    'conversation_id': 'temporary-1',
                    'mode': 'chat',
                    'revision_id': profile_migration.CHAT_BASELINE_REVISION_ID,
                },
            )

    engine.dispose()


def test_profile_migration_downgrade_removes_only_new_schema_and_round_trips() -> None:
    engine = sa.create_engine('sqlite:///:memory:')
    _create_pre_profile_schema(engine)

    _run_migration(engine, 'upgrade')
    _run_migration(engine, 'downgrade')

    inspector = sa.inspect(engine)
    assert 'conversation_mode_profile_head' not in inspector.get_table_names()
    assert 'conversation_mode_profile_revision' not in inspector.get_table_names()
    assert 'conversation_mode_profile_temporary_binding' not in inspector.get_table_names()
    assert 'mode_profile_revision_id' not in {column['name'] for column in inspector.get_columns('chat')}
    assert {column['name'] for column in inspector.get_columns('chat')} == {'id', 'title'}
    with engine.connect() as connection:
        assert connection.execute(sa.text('SELECT title FROM chat WHERE id = :id'), {'id': 'chat-1'}).scalar_one() == (
            'Legacy'
        )
        assert connection.execute(sa.text('SELECT value FROM unrelated_legacy_table WHERE id = 1')).scalar_one() == (
            'keep-me'
        )

    _run_migration(engine, 'upgrade')
    assert 'conversation_mode_profile_head' in sa.inspect(engine).get_table_names()
    engine.dispose()
