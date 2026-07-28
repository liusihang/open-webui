from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from open_webui.migrations.versions import (
    e7f8a9b0c1d2_add_agent_decision_execution as decision_migration,
)
from sqlalchemy import create_engine, inspect


def _run_migration(engine, direction: str) -> None:
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(decision_migration, 'op', operations):
            getattr(decision_migration, direction)()


def test_agent_decision_execution_migration_round_trips_and_is_idempotent():
    engine = create_engine('sqlite:///:memory:')

    _run_migration(engine, 'upgrade')
    _run_migration(engine, 'upgrade')

    inspector = inspect(engine)
    assert 'agent_run_decision_execution' in inspector.get_table_names()
    assert {column['name'] for column in inspector.get_columns('agent_run_decision_execution')} >= {
        'id',
        'run_id',
        'resource_type',
        'resource_id',
        'decision',
        'fingerprint',
        'runtime_session_id',
        'expected_checkpoint_version',
        'expected_run_state_version',
        'request_event_seq',
        'tool_arguments_fingerprint',
        'tool_call_idempotency_key',
        'status',
        'prepare_response',
        'completion_event_id',
        'activate_response',
        'runtime_outcome',
    }
    assert {constraint['name'] for constraint in inspector.get_unique_constraints(
        'agent_run_decision_execution'
    )} == {'uq_agent_run_decision_resource'}
    assert {index['name'] for index in inspector.get_indexes(
        'agent_run_decision_execution'
    )} >= {
        'ix_agent_run_decision_execution_run_id',
        'ix_agent_run_decision_execution_status',
        'ix_agent_run_decision_status_retry',
        'ix_agent_run_decision_run_status',
    }

    _run_migration(engine, 'downgrade')
    _run_migration(engine, 'downgrade')

    assert 'agent_run_decision_execution' not in inspect(engine).get_table_names()
