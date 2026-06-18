import os
from contextlib import asynccontextmanager
from unittest.mock import patch

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from open_webui.internal.db import Base
from open_webui.models.agent_runs import (
    AgentArtifact,
    AgentRun,
    AgentRunEvent,
    AgentRunOperation,
    AgentRunOperationConflict,
    AgentRunState,
    AgentRunStateError,
    AgentRunTable,
)
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

AGENT_RUN_TABLES = [
    AgentRun.__table__,
    AgentRunEvent.__table__,
    AgentArtifact.__table__,
    AgentRunOperation.__table__,
]


@pytest_asyncio.fixture
async def agent_db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=AGENT_RUN_TABLES,
            )
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context(db=None):
        if db is not None:
            yield db
            return

        async with session_factory() as session:
            yield session

    table = AgentRunTable()
    with patch('open_webui.models.agent_runs.get_async_db_context', session_context):
        yield table, session_factory

    await engine.dispose()


def test_agent_run_metadata_declares_contract_tables_indexes_and_constraints():
    assert AgentRun.__tablename__ == 'agent_run'
    assert AgentRunEvent.__tablename__ == 'agent_run_event'
    assert AgentArtifact.__tablename__ == 'agent_artifact'
    assert AgentRunOperation.__tablename__ == 'agent_run_operation'

    run_columns = set(AgentRun.__table__.columns.keys())
    assert {
        'id',
        'user_id',
        'chat_id',
        'user_message_id',
        'assistant_message_id',
        'state',
        'state_version',
        'leader_model_id',
        'runtime_session_id',
        'budget',
        'participants',
        'tool_access_snapshot',
        'model_catalog_snapshot',
        'process_refs',
        'summary',
        'error',
        'created_at',
        'updated_at',
        'started_at',
        'ended_at',
    } <= run_columns

    run_indexes = {index.name: tuple(index.columns.keys()) for index in AgentRun.__table__.indexes}
    assert run_indexes['ix_agent_run_chat_created'] == ('chat_id', 'created_at')
    assert run_indexes['ix_agent_run_user_created'] == ('user_id', 'created_at')
    assert run_indexes['ix_agent_run_state_updated'] == ('state', 'updated_at')

    event_indexes = {index.name: tuple(index.columns.keys()) for index in AgentRunEvent.__table__.indexes}
    assert event_indexes['ix_agent_run_event_run_seq'] == ('run_id', 'seq')
    assert event_indexes['ix_agent_run_event_type'] == ('event_type',)

    artifact_indexes = {index.name: tuple(index.columns.keys()) for index in AgentArtifact.__table__.indexes}
    assert artifact_indexes['ix_agent_artifact_run_path_kind'] == ('run_id', 'path', 'kind')

    operation_indexes = {index.name: tuple(index.columns.keys()) for index in AgentRunOperation.__table__.indexes}
    assert operation_indexes['ix_agent_run_operation_run_type'] == ('run_id', 'operation_type')


@pytest.mark.asyncio
async def test_create_get_and_list_runs_by_chat_and_user(agent_db):
    table, _session_factory = agent_db

    first = await table.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user-1',
        assistant_message_id='msg-assistant-1',
        leader_model_id='model-a',
        budget={'max_tool_calls': 3},
        participants=[{'id': 'leader', 'role': 'leader'}],
        tool_access_snapshot={'tools': ['read_file']},
        model_catalog_snapshot={'models': ['model-a']},
    )
    second = await table.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user-2',
        assistant_message_id='msg-assistant-2',
        leader_model_id='model-b',
    )

    assert first.state == 'queued'
    assert first.state_version == 0
    assert first.budget == {'max_tool_calls': 3}
    assert first.participants == [{'id': 'leader', 'role': 'leader'}]

    loaded = await table.get_run(first.id)
    assert loaded is not None
    assert loaded.id == first.id
    assert loaded.tool_access_snapshot == {'tools': ['read_file']}

    by_chat = await table.list_runs_by_chat('chat-1', 'user-1')
    assert [run.id for run in by_chat] == [second.id, first.id]

    by_user = await table.list_runs_by_user('user-1')
    assert [run.id for run in by_user] == [second.id, first.id]


@pytest.mark.asyncio
async def test_state_transition_enforces_contract_and_versions(agent_db):
    table, _session_factory = agent_db
    run = await table.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )

    running = await table.transition_state(
        run.id,
        from_states=['queued'],
        to_state='running',
        reason='runtime accepted',
        payload={'runtime_session_id': 'runtime-1'},
    )
    assert running.state == 'running'
    assert running.state_version == 1
    assert running.runtime_session_id == 'runtime-1'
    assert running.started_at is not None

    with pytest.raises(AgentRunStateError) as exc_info:
        await table.transition_state(
            run.id,
            from_states=['queued'],
            to_state='completed',
            reason='skip finalizing',
        )
    assert exc_info.value.code == 'invalid_state_transition'

    finalizing = await table.transition_state(
        run.id,
        from_states=['running'],
        to_state='finalizing',
        reason='runtime closed work',
    )
    assert finalizing.state_version == 2

    completed = await table.transition_state(
        run.id,
        from_states=['finalizing'],
        to_state='completed',
        reason='final answer done',
        payload={'summary': {'compact': True}},
    )
    assert completed.state == 'completed'
    assert completed.state_version == 3
    assert completed.ended_at is not None
    assert completed.summary == {'compact': True}

    duplicate_terminal = await table.transition_state(
        run.id,
        from_states=['finalizing'],
        to_state='completed',
        reason='duplicate terminal callback',
    )
    assert duplicate_terminal.state == 'completed'
    assert duplicate_terminal.state_version == 3

    with pytest.raises(AgentRunStateError) as terminal_exc:
        await table.transition_state(
            run.id,
            from_states=['completed'],
            to_state='running',
            reason='illegal restart',
        )
    assert terminal_exc.value.code == 'invalid_state_transition'


@pytest.mark.parametrize(
    ('terminal_state', 'from_state'),
    [
        ('completed', 'finalizing'),
        ('failed', 'running'),
        ('cancelled', 'running'),
        ('budget_exceeded', 'running'),
    ],
)
@pytest.mark.asyncio
async def test_terminal_state_transition_compacts_events_once(agent_db, terminal_state, from_state):
    table, _session_factory = agent_db
    run = await table.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
        participants=[{'id': 'leader', 'role': 'leader', 'model_id': 'model-a'}],
        budget={'max_tool_calls': 5, 'tool_calls_used': 1},
    )
    await table.transition_state(
        run.id,
        from_states=['queued'],
        to_state='running',
        reason='runtime accepted',
        payload={
            'process_refs': [
                {
                    'terminal_server_id': 'terminal-main',
                    'process_id': 'proc-live',
                    'command': 'python long_job.py',
                    'status': 'running',
                    'exit_code': None,
                }
            ]
        },
    )
    await table.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        summary='Runtime accepted',
    )
    await table.append_event(
        run.id,
        event_type='action.summary',
        participant_id='leader',
        phase='running',
        summary='Inspecting terminal output',
    )
    await table.append_event(
        run.id,
        event_type='tool.completed',
        participant_id='leader',
        phase='running',
        summary='Command finished',
        payload={
            'tool_name': 'run_command',
            'status': 'success',
            'artifacts': [
                {
                    'path': f'/workspace/agent-runs/{run.id}/outputs/report.txt',
                    'kind': 'file',
                }
            ],
            'process_refs': [{'process_id': 'proc-live', 'status': 'running'}],
        },
    )
    await table.register_artifact(
        run_id=run.id,
        user_id='user-1',
        kind='file',
        path=f'/workspace/agent-runs/{run.id}/outputs/report.txt',
        mime_type='text/plain',
        size=42,
        idempotency_key=f'artifact:{run.id}:outputs:report.txt',
    )

    if from_state == 'finalizing':
        await table.transition_state(
            run.id,
            from_states=['running'],
            to_state='finalizing',
            reason='runtime closed work',
        )

    terminal = await table.transition_state(
        run.id,
        from_states=[from_state],
        to_state=terminal_state,
        reason='terminal acceptance proof',
    )
    duplicate_terminal = await table.transition_state(
        run.id,
        from_states=[from_state],
        to_state=terminal_state,
        reason='duplicate terminal callback',
    )

    assert terminal.summary is not None
    assert terminal.summary['state'] == terminal_state
    assert terminal.summary['ui']['actions'] == [
        {'seq': 2, 'participant_id': 'leader', 'summary': 'Inspecting terminal output'}
    ]
    assert terminal.summary['ui']['tools'][0]['process_refs'] == [
        {'process_id': 'proc-live', 'status': 'running'}
    ]
    assert terminal.summary['ui']['artifacts'][0]['path'].endswith('/outputs/report.txt')
    assert terminal.summary['ui']['artifacts'][0]['metadata']['cleanup_eligible'] is False
    assert terminal.summary['ui']['process_refs'] == [
        {
            'terminal_server_id': 'terminal-main',
            'process_id': 'proc-live',
            'command': 'python long_job.py',
            'status': 'running',
            'exit_code': None,
        }
    ]
    assert duplicate_terminal.summary == terminal.summary


@pytest.mark.asyncio
async def test_append_event_assigns_run_local_monotonic_sequence(agent_db):
    table, _session_factory = agent_db
    run = await table.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )

    first = await table.append_event(
        run.id,
        event_type='run.queued',
        participant_id='leader',
        phase='queued',
        summary='Queued',
        payload={'visible': True},
    )
    second = await table.append_event(
        run.id,
        event_type='action.summary',
        participant_id='leader',
        phase='running',
        summary='Planning',
        payload={'step': 1},
    )

    assert first.seq == 1
    assert second.seq == 2
    assert second.payload == {'step': 1}

    events = await table.list_events(run.id, after_seq=0)
    assert [event.seq for event in events] == [1, 2]
    assert [event.event_type for event in events] == ['run.queued', 'action.summary']


@pytest.mark.asyncio
async def test_final_started_helpers_and_final_text_accumulation(agent_db):
    table, _session_factory = agent_db
    run = await table.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )

    assert await table.get_run_state(run.id) == AgentRunState.QUEUED
    assert await table.has_final_started(run.id) is False

    await table.transition_state(
        run.id,
        from_states=['queued'],
        to_state='running',
        reason='runtime accepted',
    )
    await table.transition_state(
        run.id,
        from_states=['running'],
        to_state='finalizing',
        reason='runtime closed work',
    )
    await table.append_event(
        run.id,
        event_type='final.started',
        participant_id='leader',
        phase='finalizing',
        summary='Final answer phase',
    )

    assert await table.get_run_state(run.id) == AgentRunState.FINALIZING
    assert await table.has_final_started(run.id) is True

    first = await table.append_final_text_delta(run.id, 'answer', 0, 'hel')
    duplicate = await table.append_final_text_delta(run.id, 'answer', 0, 'hel')
    second = await table.append_final_text_delta(run.id, 'answer', 1, 'lo')

    assert first == 'hel'
    assert duplicate == 'hel'
    assert second == 'hello'

    with pytest.raises(ValueError):
        await table.append_final_text_delta(run.id, 'answer', 3, '!')


@pytest.mark.asyncio
async def test_operation_ledger_caches_success_and_rejects_request_hash_conflict(agent_db):
    table, _session_factory = agent_db
    run = await table.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )

    claim = await table.claim_operation(
        run.id,
        operation_type='tool.call',
        idempotency_key='tool:leader:call-1:1',
        request_hash='hash-a',
    )
    assert claim.created is True
    assert claim.operation.status == 'in_progress'

    await table.finish_operation_success(claim.operation.id, {'status': 'success', 'content': 'ok'})

    cached = await table.claim_operation(
        run.id,
        operation_type='tool.call',
        idempotency_key='tool:leader:call-1:1',
        request_hash='hash-a',
    )
    assert cached.created is False
    assert cached.operation.status == 'succeeded'
    assert cached.operation.response == {'status': 'success', 'content': 'ok'}

    with pytest.raises(AgentRunOperationConflict) as exc_info:
        await table.claim_operation(
            run.id,
            operation_type='tool.call',
            idempotency_key='tool:leader:call-1:1',
            request_hash='hash-b',
        )
    assert exc_info.value.code == 'idempotency_conflict'


@pytest.mark.asyncio
async def test_register_artifact_is_idempotent_by_operation_key_and_path(agent_db):
    table, _session_factory = agent_db
    run = await table.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )

    artifact = await table.register_artifact(
        run_id=run.id,
        user_id='user-1',
        kind='file',
        path='/workspace/agent-runs/run-1/outputs/report.csv',
        idempotency_key='artifact:leader:report-csv',
        terminal_server_id='terminal-main',
        mime_type='text/csv',
        size=128,
        metadata={'cleanup': 'never'},
    )
    duplicate = await table.register_artifact(
        run_id=run.id,
        user_id='user-1',
        kind='file',
        path='/workspace/agent-runs/run-1/outputs/report.csv',
        idempotency_key='artifact:leader:report-csv',
        terminal_server_id='terminal-main',
        mime_type='text/csv',
        size=128,
        metadata={'cleanup': 'never'},
    )
    same_path = await table.register_artifact(
        run_id=run.id,
        user_id='user-1',
        kind='file',
        path='/workspace/agent-runs/run-1/outputs/report.csv',
        idempotency_key='artifact:leader:report-csv-retry-alt-key',
    )

    assert duplicate.id == artifact.id
    assert same_path.id == artifact.id
    assert same_path.metadata == {'cleanup': 'never'}


def test_agent_run_tables_create_in_sqlite_metadata():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    async def create_and_inspect():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn,
                    tables=AGENT_RUN_TABLES,
                )
            )
            return await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

    import asyncio

    table_names = asyncio.run(create_and_inspect())
    asyncio.run(engine.dispose())

    assert 'agent_run' in table_names
    assert 'agent_run_event' in table_names
    assert 'agent_artifact' in table_names
    assert 'agent_run_operation' in table_names
