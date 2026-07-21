import os
from unittest.mock import patch

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from open_webui.migrations.versions import (
    d6e7f8a9b0c1_add_agent_run_tables as run_migration,
)
from open_webui.migrations.versions import (
    e7f8a9b0c1d2_add_agent_decision_execution as decision_migration,
)
from open_webui.migrations.versions import (
    f8a9b0c1d2e3_add_agent_user_input_deadline as deadline_migration,
)
from open_webui.models import agent_runs
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.dialects.mysql.mariadb import MariaDBDialect
from sqlalchemy.schema import CreateIndex, CreateTable

AGENT_TABLE_NAMES = {
    'agent_run',
    'agent_run_event',
    'agent_artifact',
    'agent_run_operation',
    'agent_run_decision_execution',
}


def _run_migration(engine, migration) -> None:
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(migration, 'op', operations):
            migration.upgrade()


def _fresh_migration_metadata() -> sa.MetaData:
    engine = sa.create_engine('sqlite:///:memory:')
    for migration in (
        run_migration,
        decision_migration,
        deadline_migration,
    ):
        _run_migration(engine, migration)
    metadata = sa.MetaData()
    metadata.reflect(engine, only=AGENT_TABLE_NAMES)
    engine.dispose()
    return metadata


def _model_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    source_tables = {
        *(
            model.__table__
            for model in (
                agent_runs.AgentRun,
                agent_runs.AgentRunEvent,
                agent_runs.AgentArtifact,
                agent_runs.AgentRunOperation,
                agent_runs.AgentRunDecisionExecution,
            )
        ),
    }
    for table in source_tables:
        table.to_metadata(metadata)
    return metadata


def _key_column_sets(table: sa.Table) -> list[tuple[str, list[sa.Column]]]:
    keyed = [('primary key', list(table.primary_key.columns))]
    keyed.extend(
        (constraint.name or 'unique', list(constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    )
    keyed.extend((index.name or 'index', list(index.columns)) for index in table.indexes)
    return [(name, columns) for name, columns in keyed if columns]


@pytest.mark.parametrize(
    'metadata_factory',
    [_fresh_migration_metadata, _model_metadata],
)
@pytest.mark.parametrize(
    'dialect',
    [sqlite.dialect(), postgresql.dialect(), mysql.dialect(), MariaDBDialect()],
)
def test_agent_schema_ddl_compiles_for_supported_dialects(
    metadata_factory,
    dialect,
):
    metadata = metadata_factory()

    assert set(metadata.tables) == AGENT_TABLE_NAMES
    for table in metadata.sorted_tables:
        create_table_sql = str(CreateTable(table).compile(dialect=dialect)).upper()
        if dialect.name in {'mysql', 'mariadb'}:
            assert 'TEXT NOT NULL DEFAULT' not in create_table_sql
        for index in table.indexes:
            str(CreateIndex(index).compile(dialect=dialect))


@pytest.mark.parametrize(
    'metadata_factory',
    [_fresh_migration_metadata, _model_metadata],
)
@pytest.mark.parametrize('dialect', [mysql.dialect(), MariaDBDialect()])
def test_agent_schema_compiles_with_bounded_mysql_key_columns(
    metadata_factory,
    dialect,
):
    metadata = metadata_factory()

    assert set(metadata.tables) == AGENT_TABLE_NAMES
    for table in metadata.sorted_tables:
        for key_name, columns in _key_column_sets(table):
            string_key_chars = 0
            for column in columns:
                if not isinstance(column.type, sa.String):
                    continue
                assert column.type.length is not None, (
                    f'{table.name}.{column.name} uses unbounded '
                    f'{column.type!r} in {key_name}'
                )
                compiled_type = dialect.type_compiler.process(column.type).upper()
                assert 'TEXT' not in compiled_type
                string_key_chars += column.type.length

            assert string_key_chars <= 768, (
                f'{table.name} {key_name} can exceed the 3072-byte '
                'utf8mb4 key budget'
            )
