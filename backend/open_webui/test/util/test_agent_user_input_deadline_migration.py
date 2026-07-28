from contextlib import contextmanager
from io import StringIO
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from open_webui.migrations.versions import (
    f8a9b0c1d2e3_add_agent_user_input_deadline as deadline_migration,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql.mariadb import MariaDBDialect


def _run_migration(engine, direction: str) -> None:
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(deadline_migration, 'op', operations):
            getattr(deadline_migration, direction)()


def test_agent_user_input_deadline_migration_round_trips_and_is_idempotent():
    engine = sa.create_engine('sqlite:///:memory:')
    metadata = sa.MetaData()
    sa.Table(
        'agent_run',
        metadata,
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('state', sa.Text(), nullable=False),
    )
    metadata.create_all(engine)

    _run_migration(engine, 'upgrade')
    _run_migration(engine, 'upgrade')

    inspector = sa.inspect(engine)
    columns = {column['name'] for column in inspector.get_columns('agent_run')}
    indexes = {index['name']: index for index in inspector.get_indexes('agent_run')}
    assert columns >= {
        'pending_user_input_id',
        'pending_user_input_expires_at',
    }
    assert indexes['ix_agent_run_user_input_deadline']['column_names'] == [
        'state',
        'pending_user_input_expires_at',
    ]
    columns_by_name = {
        column['name']: column for column in inspector.get_columns('agent_run')
    }
    assert isinstance(columns_by_name['state']['type'], sa.Text)
    assert columns_by_name['pending_user_input_id']['type'].length == 512

    _run_migration(engine, 'downgrade')
    _run_migration(engine, 'downgrade')

    inspector = sa.inspect(engine)
    columns = {column['name'] for column in inspector.get_columns('agent_run')}
    assert 'pending_user_input_id' not in columns
    assert 'pending_user_input_expires_at' not in columns
    assert 'ix_agent_run_user_input_deadline' not in {
        index['name'] for index in inspector.get_indexes('agent_run')
    }


class _FakeMySQLDeadlineOperations:
    def __init__(self, *, columns, indexes):
        self.bind = type('Bind', (), {'dialect': type('Dialect', (), {'name': 'mysql'})()})()
        self.columns = columns
        self.indexes = indexes
        self.calls = []

    def get_bind(self):
        return self.bind

    @contextmanager
    def batch_alter_table(self, table_name):
        assert table_name == deadline_migration.TABLE_NAME
        yield self

    def alter_column(self, name, **kwargs):
        self.calls.append(('alter_column', name, kwargs['type_']))
        self.columns[name] = {
            **self.columns[name],
            'type': kwargs['type_'],
        }

    def add_column(self, column):
        self.calls.append(('add_column', column.name, column.type))
        self.columns[column.name] = {
            'name': column.name,
            'type': column.type,
            'nullable': column.nullable,
        }

    def create_index(self, name, table_name, columns):
        assert self.columns['state']['type'].length is not None
        self.calls.append(('create_index', name, tuple(columns)))
        self.indexes.add(name)

    def drop_index(self, name, *, table_name):
        assert table_name == deadline_migration.TABLE_NAME
        self.calls.append(('drop_index', name))
        self.indexes.remove(name)

    def drop_column(self, name):
        self.calls.append(('drop_column', name))
        self.columns.pop(name)


class _FakeMySQLDeadlineInspector:
    def __init__(self, operations):
        self.operations = operations

    def get_table_names(self):
        return [deadline_migration.TABLE_NAME]

    def get_columns(self, table_name):
        assert table_name == deadline_migration.TABLE_NAME
        return list(self.operations.columns.values())

    def get_indexes(self, table_name):
        assert table_name == deadline_migration.TABLE_NAME
        return [{'name': name} for name in self.operations.indexes]


def test_mysql_partial_upgrade_rebounds_legacy_text_state_before_index_and_retries_cleanly():
    operations = _FakeMySQLDeadlineOperations(
        columns={
            'state': {
                'name': 'state',
                'type': sa.Text(),
                'nullable': False,
            },
            'pending_user_input_id': {
                'name': 'pending_user_input_id',
                'type': sa.Text(),
                'nullable': True,
            },
            'pending_user_input_expires_at': {
                'name': 'pending_user_input_expires_at',
                'type': sa.BigInteger(),
                'nullable': True,
            },
        },
        indexes=set(),
    )

    with (
        patch.object(deadline_migration, 'op', operations),
        patch.object(
            deadline_migration.sa,
            'inspect',
            side_effect=lambda _bind: _FakeMySQLDeadlineInspector(operations),
        ),
    ):
        deadline_migration.upgrade()
        first_calls = list(operations.calls)
        deadline_migration.upgrade()

    assert [call[:2] for call in first_calls] == [
        ('alter_column', 'state'),
        ('alter_column', 'pending_user_input_id'),
        ('create_index', deadline_migration.INDEX_NAME),
    ]
    assert first_calls[0][2].length == deadline_migration.STATE_LENGTH
    assert (
        first_calls[1][2].length
        == deadline_migration.PENDING_USER_INPUT_ID_LENGTH
    )
    assert first_calls[2][2] == ('state', 'pending_user_input_expires_at')
    assert operations.calls == first_calls
    assert isinstance(operations.columns['state']['type'], sa.String)
    assert operations.columns['state']['type'].length == deadline_migration.STATE_LENGTH


def test_mysql_partial_downgrade_is_idempotent_after_index_creation():
    operations = _FakeMySQLDeadlineOperations(
        columns={
            'state': {
                'name': 'state',
                'type': sa.String(length=deadline_migration.STATE_LENGTH),
                'nullable': False,
            },
            'pending_user_input_id': {
                'name': 'pending_user_input_id',
                'type': sa.String(
                    length=deadline_migration.PENDING_USER_INPUT_ID_LENGTH
                ),
                'nullable': True,
            },
            'pending_user_input_expires_at': {
                'name': 'pending_user_input_expires_at',
                'type': sa.BigInteger(),
                'nullable': True,
            },
        },
        indexes={deadline_migration.INDEX_NAME},
    )

    with (
        patch.object(deadline_migration, 'op', operations),
        patch.object(
            deadline_migration.sa,
            'inspect',
            side_effect=lambda _bind: _FakeMySQLDeadlineInspector(operations),
        ),
    ):
        deadline_migration.downgrade()
        first_calls = list(operations.calls)
        deadline_migration.downgrade()

    assert first_calls == [
        ('drop_index', deadline_migration.INDEX_NAME),
        ('drop_column', 'pending_user_input_expires_at'),
        ('drop_column', 'pending_user_input_id'),
    ]
    assert operations.calls == first_calls
    assert set(operations.columns) == {'state'}
    assert operations.indexes == set()


@pytest.mark.parametrize('dialect', [mysql.dialect(), MariaDBDialect()])
def test_mysql_family_legacy_type_repair_ddl_compiles(dialect):
    output = StringIO()
    context = MigrationContext.configure(
        dialect=dialect,
        opts={'as_sql': True, 'output_buffer': output},
    )
    operations = Operations(context)

    with operations.batch_alter_table(deadline_migration.TABLE_NAME) as batch_op:
        batch_op.alter_column(
            'state',
            existing_type=sa.Text(),
            type_=sa.String(length=deadline_migration.STATE_LENGTH),
            existing_nullable=False,
        )
        batch_op.alter_column(
            'pending_user_input_id',
            existing_type=sa.Text(),
            type_=sa.String(
                length=deadline_migration.PENDING_USER_INPUT_ID_LENGTH
            ),
            existing_nullable=True,
        )

    compiled = output.getvalue().upper()
    assert 'ALTER TABLE AGENT_RUN MODIFY STATE VARCHAR(64) NOT NULL' in compiled
    assert (
        'ALTER TABLE AGENT_RUN MODIFY PENDING_USER_INPUT_ID VARCHAR(512) NULL'
        in compiled
    )
