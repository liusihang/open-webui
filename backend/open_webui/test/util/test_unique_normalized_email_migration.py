from io import StringIO
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from open_webui.migrations.versions import (
    f0bd01a18a3d_add_unique_normalized_user_email_index as email_migration,
)
from sqlalchemy.dialects import mysql, postgresql, sqlite


def _run_migration(engine: sa.Engine, direction: str) -> None:
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with (
            patch.object(email_migration, 'op', operations),
            patch.object(email_migration.context, 'is_offline_mode', return_value=False),
        ):
            getattr(email_migration, direction)()


def _create_user_table(engine: sa.Engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        'user',
        metadata,
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('email', sa.Text(), nullable=True),
    )
    metadata.create_all(engine)


def _sqlite_index_exists(engine: sa.Engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                sa.text("SELECT count(*) FROM sqlite_master WHERE type = 'index' AND name = :name"),
                {'name': email_migration.INDEX_NAME},
            ).scalar_one()
        )


def test_sqlite_normalized_email_index_round_trips_and_is_idempotent() -> None:
    engine = sa.create_engine('sqlite:///:memory:')
    _create_user_table(engine)

    _run_migration(engine, 'upgrade')
    _run_migration(engine, 'upgrade')
    assert _sqlite_index_exists(engine)

    _run_migration(engine, 'downgrade')
    _run_migration(engine, 'downgrade')
    assert not _sqlite_index_exists(engine)

    _run_migration(engine, 'upgrade')
    assert _sqlite_index_exists(engine)
    engine.dispose()


def test_normalized_email_index_fails_closed_on_case_insensitive_duplicates() -> None:
    engine = sa.create_engine('sqlite:///:memory:')
    _create_user_table(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text('INSERT INTO "user" (id, email) VALUES (:id, :email)'),
            [
                {'id': 'user-1', 'email': 'Test@Example.com'},
                {'id': 'user-2', 'email': 'test@example.com'},
            ],
        )

    with pytest.raises(RuntimeError, match='test@example.com \\(x2\\)'):
        _run_migration(engine, 'upgrade')

    assert not _sqlite_index_exists(engine)
    engine.dispose()


@pytest.mark.parametrize(
    ('dialect', 'expected_expression'),
    [
        (postgresql.dialect(), '(lower(email))'),
        (sqlite.dialect(), '(lower(email))'),
        (mysql.dialect(), '((lower(email)))'),
    ],
)
def test_normalized_email_index_ddl_compiles_for_supported_dialects(
    dialect: sa.engine.Dialect,
    expected_expression: str,
) -> None:
    output = StringIO()
    context = MigrationContext.configure(
        dialect=dialect,
        opts={'as_sql': True, 'output_buffer': output},
    )
    operations = Operations(context)

    with patch.object(email_migration, 'op', operations):
        email_migration._create_index()

    assert expected_expression in output.getvalue()
