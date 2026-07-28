import asyncio
import os
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from open_webui.internal.db import Base
from open_webui.models import agent_runs as agent_run_models
from open_webui.models.agent_runs import (
    AgentArtifact,
    AgentRun,
    AgentRunEvent,
    AgentRunOperation,
    AgentRuns,
)
from sqlalchemy import select, text
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.dialects.mysql.mariadb import MariaDBDialect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

RUNTIME_PACKAGE_ROOT = Path(__file__).parents[4] / 'services' / 'agentscope-runtime'
if str(RUNTIME_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))

from agentscope_runtime.schemas import (  # noqa: E402
    RuntimeExecutionPrepareRequest,
    RuntimeExecutionResponse,
)


@pytest_asyncio.fixture
async def decision_db(monkeypatch, tmp_path):
    engine = create_async_engine(
        f'sqlite+aiosqlite:///{tmp_path / "decision-execution.sqlite3"}'
    )
    tables = [
        AgentRun.__table__,
        AgentRunEvent.__table__,
        AgentArtifact.__table__,
        AgentRunOperation.__table__,
    ]
    decision_table = Base.metadata.tables.get('agent_run_decision_execution')
    if decision_table is not None:
        tables.append(decision_table)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context(db=None):
        if db is not None:
            yield db
            return
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(agent_run_models, 'get_async_db_context', session_context)
    yield session_factory
    await engine.dispose()


async def _waiting_resource(
    resource_type: str,
    *,
    timeout_seconds: float | None = None,
) -> tuple[str, str]:
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='user-message-1',
        assistant_message_id='assistant-message-1',
        leader_model_id='model-1',
    )
    await AgentRuns.attach_runtime_session(run.id, 'runtime-session-1')
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        summary='Runtime accepted.',
        payload={'runtime_session_id': 'runtime-session-1'},
    )
    if resource_type == 'approval':
        resource_id = f'approval:{run.id}:tool-call-1'
        await AgentRuns.append_event(
            run.id,
            event_type='approval.requested',
            participant_id='leader',
            phase='waiting_approval',
            summary='Approval requested.',
            payload={
                'approval_id': resource_id,
                'tool_call_id': 'tool-call-1',
                'tool_id': 'tool-1',
                'tool_arguments_fingerprint': agent_run_models._decision_payload_fingerprint(
                    {'path': '/workspace/report.txt'}
                ),
                'tool_call_idempotency_key': 'tool:leader:tool-call-1:1',
                'checkpoint_version': 7,
            },
        )
    else:
        resource_id = f'user-input:{run.id}:tool-call-1'
        payload = {
            'user_input_id': resource_id,
            'tool_call_id': 'tool-call-1',
            'checkpoint_version': 7,
            'allow_cancel': True,
        }
        if timeout_seconds is not None:
            payload['timeout_seconds'] = timeout_seconds
        await AgentRuns.append_event(
            run.id,
            event_type='user_input.requested',
            participant_id='leader',
            phase='waiting_user_input',
            summary='User input requested.',
            payload=payload,
        )
    return run.id, resource_id


async def _insert_legacy_waiting_user_input(
    session_factory,
    *,
    suffix: str,
    request_payloads: list[dict],
) -> tuple[str, str]:
    run_id = f'legacy-run-{suffix}'
    async with session_factory() as session:
        session.add(
            AgentRun(
                id=run_id,
                user_id='user-1',
                chat_id='chat-1',
                user_message_id=f'user-message-{suffix}',
                assistant_message_id=f'assistant-message-{suffix}',
                state='waiting_user_input',
                state_version=2,
                leader_model_id='model-1',
                runtime_session_id='runtime-session-1',
                final_text='',
                pending_user_input_id=None,
                pending_user_input_expires_at=None,
                created_at=1,
                updated_at=1,
            )
        )
        for seq, payload in enumerate(request_payloads, start=1):
            session.add(
                AgentRunEvent(
                    id=f'legacy-event-{suffix}-{seq}',
                    run_id=run_id,
                    seq=seq,
                    event_type='user_input.requested',
                    participant_id='leader',
                    phase='waiting_user_input',
                    summary='User input requested.',
                    payload=payload,
                    created_at=seq,
                )
            )
        await session.commit()
    return run_id, str(request_payloads[-1]['user_input_id'])


async def _record(
    run_id: str,
    resource_type: str,
    resource_id: str,
    decision: str,
    key: str,
    *,
    payload=None,
):
    return await AgentRuns.record_decision_execution(
        run_id,
        resource_type=resource_type,
        resource_id=resource_id,
        decision=decision,
        payload=payload or {},
        operation_type=f'{resource_type}.result',
        idempotency_key=key,
        request_hash=f'hash:{resource_type}:{resource_id}:{decision}:{payload!r}',
    )


def _minimal_decision_values() -> dict:
    return {
        'id': 'candidate-execution',
        'run_id': 'run-1',
        'resource_type': 'user_input',
        'resource_id': 'input-1',
        'decision': 'accepted',
        'command_type': 'resume_user_input',
        'command_payload': {'status': 'accepted', 'content': 'yes'},
        'fingerprint': 'fingerprint-1',
        'runtime_session_id': 'runtime-session-1',
        'expected_checkpoint_version': 1,
        'expected_run_state_version': 2,
        'request_event_seq': 3,
        'status': 'pending',
        'attempt_count': 0,
        'created_at': 1,
        'updated_at': 1,
    }


@pytest.mark.parametrize('dialect', [mysql.dialect(), MariaDBDialect()])
def test_mysql_family_decision_insert_is_atomic_noop_on_duplicate(dialect):
    statement = agent_run_models._decision_execution_insert_statement(
        dialect.name,
        _minimal_decision_values(),
    )

    compiled = str(statement.compile(dialect=dialect)).upper()

    assert 'ON DUPLICATE KEY UPDATE' in compiled
    assert 'ID = AGENT_RUN_DECISION_EXECUTION.ID' in compiled
    assert 'RETURNING' not in compiled


@pytest.mark.parametrize('dialect', [mysql.dialect(), MariaDBDialect()])
def test_mysql_family_receipt_insert_is_atomic_noop_on_duplicate(dialect):
    statement = agent_run_models._decision_receipt_insert_statement(
        dialect.name,
        {
            'id': 'candidate-receipt',
            'run_id': 'run-1',
            'operation_type': 'user_input.result',
            'idempotency_key': 'caller-1',
            'request_hash': 'hash-1',
            'status': 'succeeded',
            'created_at': 1,
            'updated_at': 1,
        },
    )

    compiled = str(statement.compile(dialect=dialect)).upper()

    assert 'ON DUPLICATE KEY UPDATE' in compiled
    assert 'ID = AGENT_RUN_OPERATION.ID' in compiled


def test_mysql_canonical_decision_lookup_is_a_current_locking_read():
    statement = agent_run_models._canonical_decision_execution_statement(
        run_id='run-1',
        resource_type='user_input',
        resource_id='input-1',
    )

    compiled = str(statement.compile(dialect=mysql.dialect())).upper()

    assert 'FOR UPDATE' in compiled


@pytest.mark.asyncio
async def test_different_caller_keys_converge_on_one_resource_execution(decision_db):
    run_id, approval_id = await _waiting_resource('approval')

    first = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    second = await _record(run_id, 'approval', approval_id, 'approved', 'caller-2')

    assert first.execution.id == second.execution.id
    assert first.created is True
    assert second.created is False
    assert first.execution.expected_checkpoint_version == 7
    async with decision_db() as session:
        rows = (await session.execute(select(agent_run_models.AgentRunDecisionExecution))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_concurrent_caller_keys_return_one_canonical_resource_execution(
    decision_db,
):
    run_id, approval_id = await _waiting_resource('approval')

    first, second = await asyncio.gather(
        _record(run_id, 'approval', approval_id, 'approved', 'caller-1'),
        _record(run_id, 'approval', approval_id, 'approved', 'caller-2'),
    )

    assert first.execution.id == second.execution.id
    assert {first.created, second.created} == {False, True}
    async with decision_db() as session:
        rows = (
            await session.execute(
                select(agent_run_models.AgentRunDecisionExecution).where(
                    agent_run_models.AgentRunDecisionExecution.run_id == run_id
                )
            )
        ).scalars().all()
    assert [row.id for row in rows] == [first.execution.id]


@pytest.mark.asyncio
async def test_decision_execution_rejects_requested_event_without_checkpoint(
    decision_db,
):
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='user-message-1',
        assistant_message_id='assistant-message-1',
        leader_model_id='model-1',
    )
    await AgentRuns.attach_runtime_session(run.id, 'runtime-session-1')
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
    )
    approval_id = f'approval:{run.id}:tool-call-1'
    await AgentRuns.append_event(
        run.id,
        event_type='approval.requested',
        participant_id='leader',
        phase='waiting_approval',
        payload={
            'approval_id': approval_id,
            'tool_call_id': 'tool-call-1',
            'tool_id': 'tool-1',
            'tool_arguments_fingerprint': agent_run_models._decision_payload_fingerprint(
                {'path': '/workspace/report.txt'}
            ),
            'tool_call_idempotency_key': 'tool:leader:tool-call-1:1',
        },
    )

    with pytest.raises(
        agent_run_models.AgentRunDecisionConflict,
        match='checkpoint_version',
    ):
        await _record(run.id, 'approval', approval_id, 'approved', 'caller-1')


@pytest.mark.asyncio
async def test_concurrent_same_user_input_receipt_key_inserts_once_on_sqlite(
    decision_db,
):
    run_id, input_id = await _waiting_resource('user_input')

    first, second = await asyncio.gather(
        _record(
            run_id,
            'user_input',
            input_id,
            'accepted',
            'same-key',
            payload={'content': {'answer': 'A'}},
        ),
        _record(
            run_id,
            'user_input',
            input_id,
            'accepted',
            'same-key',
            payload={'content': {'answer': 'A'}},
        ),
    )

    assert first.execution is not None
    assert second.execution is not None
    assert first.execution.id == second.execution.id
    async with decision_db() as session:
        receipts = (
            await session.execute(
                select(AgentRunOperation).filter_by(
                    run_id=run_id,
                    operation_type='user_input.result',
                    idempotency_key='same-key',
                )
            )
        ).scalars().all()
    assert len(receipts) == 1
    assert receipts[0].status == 'succeeded'


@pytest.mark.asyncio
async def test_concurrent_opposite_decisions_choose_one_canonical_resource_owner(
    decision_db,
):
    run_id, approval_id = await _waiting_resource('approval')

    results = await asyncio.gather(
        _record(run_id, 'approval', approval_id, 'approved', 'caller-approved'),
        _record(run_id, 'approval', approval_id, 'rejected', 'caller-rejected'),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, Exception)]
    conflicts = [
        result
        for result in results
        if isinstance(result, agent_run_models.AgentRunDecisionConflict)
    ]
    async with decision_db() as session:
        executions = (
            await session.execute(
                select(agent_run_models.AgentRunDecisionExecution).where(
                    agent_run_models.AgentRunDecisionExecution.run_id == run_id
                )
            )
        ).scalars().all()

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert len(executions) == 1
    assert successes[0].execution.id == executions[0].id
    assert successes[0].execution.decision == executions[0].decision


@pytest.mark.asyncio
async def test_user_input_deadline_uses_database_clock_not_event_clock(
    decision_db,
    monkeypatch,
):
    application_now = 9_000_000_000_000_000_000
    database_now = 4_000_000_000

    monkeypatch.setattr(agent_run_models, '_now_ns', lambda: application_now)

    async def fake_database_now(_db):
        return database_now

    monkeypatch.setattr(agent_run_models, '_database_now_ns', fake_database_now)

    run_id, input_id = await _waiting_resource(
        'user_input',
        timeout_seconds=2.5,
    )

    requested = (await AgentRuns.list_events(run_id))[-1]
    async with decision_db() as session:
        run = await session.get(AgentRun, run_id)

    assert requested.created_at == application_now
    assert run.pending_user_input_id == input_id
    assert run.pending_user_input_expires_at == database_now + 2_500_000_000


@pytest.mark.parametrize(
    'dialect',
    [sqlite.dialect(), postgresql.dialect(), mysql.dialect(), MariaDBDialect()],
)
def test_due_user_input_query_is_indexable_and_bounded(dialect):
    statement = agent_run_models._due_user_input_statement(
        now_ns=123_000_000_000,
        limit=17,
    )

    compiled = str(
        statement.compile(
            dialect=dialect,
            compile_kwargs={'literal_binds': True},
        )
    ).upper()

    assert 'FROM AGENT_RUN' in compiled
    assert 'AGENT_RUN_EVENT' not in compiled
    assert 'STATE =' in compiled
    assert 'PENDING_USER_INPUT_EXPIRES_AT <=' in compiled
    assert 'ORDER BY AGENT_RUN.PENDING_USER_INPUT_EXPIRES_AT' in compiled
    assert 'LIMIT 17' in compiled


@pytest.mark.parametrize(
    'dialect',
    [sqlite.dialect(), postgresql.dialect(), mysql.dialect(), MariaDBDialect()],
)
def test_legacy_user_input_reconciliation_query_is_sql_limited(dialect):
    statement = agent_run_models._legacy_user_input_request_statement(limit=19)

    compiled = str(
        statement.compile(
            dialect=dialect,
            compile_kwargs={'literal_binds': True},
        )
    ).upper()

    assert 'WAITING_USER_INPUT' in compiled
    assert 'USER_INPUT.REQUESTED' in compiled
    assert 'PENDING_USER_INPUT_ID IS NULL' in compiled
    assert 'LIMIT 19' in compiled


@pytest.mark.asyncio
async def test_legacy_timed_user_input_uses_latest_request_and_database_now(
    decision_db,
    monkeypatch,
):
    database_now = 7_000_000_000

    async def fake_database_now(_db):
        return database_now

    monkeypatch.setattr(agent_run_models, '_database_now_ns', fake_database_now)
    run_id, latest_input_id = await _insert_legacy_waiting_user_input(
        decision_db,
        suffix='timed',
        request_payloads=[
            {
                'user_input_id': 'legacy-input-old',
                'timeout_seconds': 1,
            },
            {
                'user_input_id': 'legacy-input-latest',
                'timeout_seconds': 30,
            },
        ],
    )

    reconciled = await AgentRuns.reconcile_legacy_user_inputs(limit=10)

    async with decision_db() as session:
        run = await session.get(AgentRun, run_id)
    assert reconciled == 1
    assert run.pending_user_input_id == latest_input_id
    assert run.pending_user_input_expires_at == database_now + 30_000_000_000


@pytest.mark.asyncio
async def test_legacy_untimed_user_input_reconciles_once_without_deadline(
    decision_db,
    monkeypatch,
):
    database_now_calls = 0

    async def fake_database_now(_db):
        nonlocal database_now_calls
        database_now_calls += 1
        return 11_000_000_000

    monkeypatch.setattr(agent_run_models, '_database_now_ns', fake_database_now)
    run_id, input_id = await _insert_legacy_waiting_user_input(
        decision_db,
        suffix='untimed',
        request_payloads=[{'user_input_id': 'legacy-input-untimed'}],
    )

    first = await AgentRuns.reconcile_legacy_user_inputs(limit=10)
    second = await AgentRuns.reconcile_legacy_user_inputs(limit=10)

    async with decision_db() as session:
        run = await session.get(AgentRun, run_id)
    assert first == 1
    assert second == 0
    assert database_now_calls == 2
    assert run.pending_user_input_id == input_id
    assert run.pending_user_input_expires_at is None


@pytest.mark.asyncio
async def test_legacy_user_input_reconciliation_applies_limit(decision_db):
    for index in range(7):
        await _insert_legacy_waiting_user_input(
            decision_db,
            suffix=f'limit-{index}',
            request_payloads=[
                {'user_input_id': f'legacy-input-limit-{index}'},
            ],
        )

    reconciled = await AgentRuns.reconcile_legacy_user_inputs(limit=3)

    async with decision_db() as session:
        populated = (
            await session.execute(
                select(AgentRun).where(
                    AgentRun.pending_user_input_id.is_not(None)
                )
            )
        ).scalars().all()
    assert reconciled == 3
    assert len(populated) == 3


@pytest.mark.asyncio
async def test_legacy_user_input_reconciliation_is_multi_worker_safe(decision_db):
    run_id, input_id = await _insert_legacy_waiting_user_input(
        decision_db,
        suffix='multi-worker',
        request_payloads=[
            {
                'user_input_id': 'legacy-input-multi-worker',
                'timeout_seconds': 60,
            }
        ],
    )

    results = await asyncio.gather(
        AgentRuns.reconcile_legacy_user_inputs(limit=10),
        AgentRuns.reconcile_legacy_user_inputs(limit=10),
    )

    async with decision_db() as session:
        run = await session.get(AgentRun, run_id)
    assert sum(results) == 1
    assert run.pending_user_input_id == input_id
    assert run.pending_user_input_expires_at is not None


@pytest.mark.asyncio
async def test_decision_record_clears_pending_user_input_deadline(decision_db):
    run_id, input_id = await _waiting_resource(
        'user_input',
        timeout_seconds=30,
    )

    await _record(
        run_id,
        'user_input',
        input_id,
        'accepted',
        'user-submit',
        payload={'content': {'answer': 'A'}},
    )

    async with decision_db() as session:
        run = await session.get(AgentRun, run_id)
    assert run.pending_user_input_id is None
    assert run.pending_user_input_expires_at is None


@pytest.mark.asyncio
async def test_terminal_transition_clears_pending_user_input_deadline(decision_db):
    run_id, _input_id = await _waiting_resource(
        'user_input',
        timeout_seconds=30,
    )

    await AgentRuns.append_event(
        run_id,
        event_type='run.cancelled',
        phase='cancelled',
        payload={'runtime_session_id': 'runtime-session-1'},
    )

    async with decision_db() as session:
        run = await session.get(AgentRun, run_id)
    assert run.pending_user_input_id is None
    assert run.pending_user_input_expires_at is None


@pytest.mark.asyncio
async def test_due_user_input_timeout_persists_expired_event_and_resumes_run(
    decision_db,
):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, input_id = await _waiting_resource(
        'user_input',
        timeout_seconds=0.01,
    )
    requested = (await AgentRuns.list_events(run_id))[-1]

    expired = await AgentRuns.expire_due_user_inputs(
        now_ns=requested.created_at + 10_000_001,
    )
    assert len(expired) == 1
    assert expired[0].decision == 'timeout'

    result = await AgentDecisionExecutionDispatcher(
        AgentRuns,
        StubRuntimeClient(activate_state='applied'),
        worker_id='timeout-worker',
    ).dispatch_execution(expired[0].id)

    run = await AgentRuns.get_run(run_id)
    events = await AgentRuns.list_events(run_id)
    assert result.status == 'succeeded'
    assert run is not None
    assert run.state == 'running'
    assert [event.event_type for event in events].count('user_input.expired') == 1
    expired_event = next(
        event for event in events if event.event_type == 'user_input.expired'
    )
    assert expired_event.payload['user_input_id'] == input_id
    assert expired_event.payload['status'] == 'timeout'


@pytest.mark.asyncio
async def test_restart_scanner_discovers_durable_due_user_input(decision_db):
    run_id, _input_id = await _waiting_resource(
        'user_input',
        timeout_seconds=1,
    )
    async with decision_db() as session:
        run = await session.get(AgentRun, run_id)
        persisted_deadline = run.pending_user_input_expires_at
    restarted_store = agent_run_models.AgentRunTable()

    expired = await restarted_store.expire_due_user_inputs(
        now_ns=persisted_deadline + 1,
    )

    assert len(expired) == 1
    assert expired[0].decision == 'timeout'


@pytest.mark.asyncio
async def test_user_submission_and_timeout_race_create_exactly_one_decision(
    decision_db,
):
    run_id, input_id = await _waiting_resource(
        'user_input',
        timeout_seconds=0.01,
    )
    requested = (await AgentRuns.list_events(run_id))[-1]

    timeout_result, user_result = await asyncio.gather(
        AgentRuns.expire_due_user_inputs(
            now_ns=requested.created_at + 10_000_001,
        ),
        _record(
            run_id,
            'user_input',
            input_id,
            'accepted',
            'user-submit-race',
            payload={'content': {'answer': 'A'}},
        ),
        return_exceptions=True,
    )

    async with decision_db() as session:
        rows = (
            await session.execute(
                select(agent_run_models.AgentRunDecisionExecution).where(
                    agent_run_models.AgentRunDecisionExecution.run_id == run_id
                )
            )
        ).scalars().all()
        run = await session.get(AgentRun, run_id)
    assert len(rows) == 1
    assert rows[0].decision in {'accepted', 'timeout'}
    assert run.pending_user_input_id is None
    assert run.pending_user_input_expires_at is None
    assert not (
        isinstance(timeout_result, Exception)
        and isinstance(user_result, Exception)
    )


@pytest.mark.asyncio
async def test_repeated_timeout_scan_is_idempotent(decision_db):
    run_id, _input_id = await _waiting_resource(
        'user_input',
        timeout_seconds=0.01,
    )
    requested = (await AgentRuns.list_events(run_id))[-1]
    due_at = requested.created_at + 10_000_001

    first = await AgentRuns.expire_due_user_inputs(now_ns=due_at)
    second = await AgentRuns.expire_due_user_inputs(now_ns=due_at)

    async with decision_db() as session:
        rows = (
            await session.execute(
                select(agent_run_models.AgentRunDecisionExecution).where(
                    agent_run_models.AgentRunDecisionExecution.run_id == run_id
                )
            )
        ).scalars().all()
    assert len(first) == 1
    assert second == []
    assert len(rows) == 1
    assert rows[0].decision == 'timeout'


@pytest.mark.asyncio
async def test_timeout_scan_applies_sql_limit_with_many_waiting_runs(decision_db):
    database_now = 50_000_000_000
    run_count = 125
    async with decision_db() as session:
        for index in range(run_count):
            run_id = f'high-cardinality-run-{index:03d}'
            input_id = f'user-input:{run_id}:tool-call-1'
            session.add(
                AgentRun(
                    id=run_id,
                    user_id='user-1',
                    chat_id='chat-1',
                    user_message_id=f'user-message-{index}',
                    assistant_message_id=f'assistant-message-{index}',
                    state='waiting_user_input',
                    state_version=2,
                    leader_model_id='model-1',
                    runtime_session_id='runtime-session-1',
                    final_text='',
                    pending_user_input_id=input_id,
                    pending_user_input_expires_at=database_now - run_count + index,
                    created_at=index + 1,
                    updated_at=index + 1,
                )
            )
            session.add(
                AgentRunEvent(
                    id=f'event-{index}',
                    run_id=run_id,
                    seq=1,
                    event_type='user_input.requested',
                    participant_id='leader',
                    phase='waiting_user_input',
                    summary='User input requested.',
                    payload={
                        'user_input_id': input_id,
                        'tool_call_id': 'tool-call-1',
                        'checkpoint_version': 7,
                        'allow_cancel': True,
                        'timeout_seconds': 1,
                    },
                    created_at=1,
                )
            )
        await session.commit()

    first = await AgentRuns.expire_due_user_inputs(
        now_ns=database_now,
        limit=11,
    )

    async with decision_db() as session:
        executions = (
            await session.execute(
                select(agent_run_models.AgentRunDecisionExecution)
            )
        ).scalars().all()
        pending = (
            await session.execute(
                select(AgentRun).where(
                    AgentRun.pending_user_input_expires_at.is_not(None)
                )
            )
        ).scalars().all()

    assert len(first) == 11
    assert len(executions) == 11
    assert len(pending) == run_count - 11


@pytest.mark.asyncio
async def test_timeout_scanner_exception_does_not_starve_dispatcher(
    monkeypatch,
):
    from open_webui.agent import decision_execution as decision_module

    scan_calls = 0
    claim_calls = 0
    scan_order = []
    dispatcher_reached = asyncio.Event()

    async def reconcile_legacy():
        scan_order.append('reconcile')
        return 0

    async def failing_scan():
        nonlocal scan_calls
        scan_calls += 1
        scan_order.append('expire')
        raise RuntimeError('forced timeout scan failure')

    async def claim_next(**_kwargs):
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls >= 3:
            dispatcher_reached.set()
        return None

    monkeypatch.setattr(
        AgentRuns,
        'reconcile_legacy_user_inputs',
        reconcile_legacy,
    )
    monkeypatch.setattr(AgentRuns, 'expire_due_user_inputs', failing_scan)
    monkeypatch.setattr(AgentRuns, 'claim_next_decision_execution', claim_next)
    app = SimpleNamespace(
        state=SimpleNamespace(
            config=SimpleNamespace(
                AGENT_RUNTIME_BASE_URL='',
                AGENT_RUNTIME_SERVICE_TOKEN='',
                AGENT_RUN_DEFAULT_TIMEOUT_SECONDS=1,
            )
        )
    )

    task = asyncio.create_task(
        decision_module.agent_decision_dispatcher_loop(app, poll_seconds=0.001)
    )
    try:
        await asyncio.wait_for(dispatcher_reached.wait(), timeout=0.2)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert scan_calls == 1
    assert scan_order == ['reconcile', 'expire']
    assert claim_calls >= 3


@pytest.mark.asyncio
async def test_resource_specific_runtime_command_payloads(decision_db):
    approval_run_id, approval_id = await _waiting_resource('approval')
    approval = await _record(
        approval_run_id,
        'approval',
        approval_id,
        'approved',
        'approval-key',
    )
    input_run_id, input_id = await _waiting_resource('user_input')
    user_input = await _record(
        input_run_id,
        'user_input',
        input_id,
        'accepted',
        'input-key',
        payload={'content': {'answer': 'A'}},
    )

    assert approval.execution.command_payload == {'decision': 'approved'}
    assert user_input.execution.command_payload == {
        'status': 'accepted',
        'content': {'answer': 'A'},
    }
    assert 'decision' not in user_input.execution.command_payload


@pytest.mark.asyncio
async def test_opposite_decisions_with_different_keys_conflict_at_resource_owner(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')

    with pytest.raises(agent_run_models.AgentRunDecisionConflict):
        await _record(run_id, 'approval', approval_id, 'rejected', 'caller-2')


@pytest.mark.asyncio
async def test_decision_and_caller_receipt_roll_back_together(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    async with decision_db() as session:
        await session.execute(
            text(
                """
                CREATE TRIGGER reject_decision_receipt
                BEFORE INSERT ON agent_run_operation
                BEGIN
                    SELECT RAISE(ABORT, 'forced receipt failure');
                END
                """
            )
        )
        await session.commit()

    with pytest.raises(Exception, match='forced receipt failure'):
        await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')

    async with decision_db() as session:
        executions = (await session.execute(select(agent_run_models.AgentRunDecisionExecution))).scalars().all()
        receipts = (await session.execute(select(AgentRunOperation))).scalars().all()
    assert executions == []
    assert receipts == []


@pytest.mark.asyncio
async def test_record_decision_respects_external_session_rollback(decision_db):
    run_id, approval_id = await _waiting_resource('approval')

    async with decision_db() as session:
        recorded = await AgentRuns.record_decision_execution(
            run_id,
            resource_type='approval',
            resource_id=approval_id,
            decision='approved',
            payload={},
            operation_type='approval.result',
            idempotency_key='external-record',
            request_hash='external-record-hash',
            db=session,
        )
        assert recorded.execution is not None
        await session.rollback()

    async with decision_db() as session:
        executions = (
            await session.execute(
                select(agent_run_models.AgentRunDecisionExecution).filter_by(
                    run_id=run_id
                )
            )
        ).scalars().all()
        receipts = (
            await session.execute(
                select(AgentRunOperation).filter_by(
                    run_id=run_id,
                    operation_type='approval.result',
                    idempotency_key='external-record',
                )
            )
        ).scalars().all()
    assert executions == []
    assert receipts == []


@pytest.mark.asyncio
async def test_claim_prepare_commit_respects_external_session_rollback(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')

    async with decision_db() as session:
        claim = await AgentRuns.claim_decision_execution(
            recorded.execution.id,
            worker_id='worker-1',
            lease_seconds=30,
            db=session,
        )
        assert claim is not None
        prepared = _runtime_response(
            {
                'execution_id': recorded.execution.id,
                'runtime_session_id': 'runtime-session-1',
                'subject_id': approval_id,
                'command_type': 'resume_approval',
                'expected_checkpoint_version': 7,
                'fingerprint': recorded.execution.fingerprint,
            },
            run_id,
            state='prepared',
        )
        await AgentRuns.mark_decision_execution_prepared(
            recorded.execution.id,
            prepared,
            claim_token=claim.execution.claim_token,
            db=session,
        )
        committed = await AgentRuns.commit_prepared_decision_execution(
            recorded.execution.id,
            claim_token=claim.execution.claim_token,
            db=session,
        )
        assert committed.status == 'backend_committed'
        await session.rollback()

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    run = await AgentRuns.get_run(run_id)
    events = await AgentRuns.list_events(run_id)
    assert execution is not None
    assert execution.status == 'pending'
    assert run is not None
    assert run.state == 'waiting_approval'
    assert 'approval.completed' not in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_fail_decision_respects_external_session_rollback(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    claim = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert claim is not None

    async with decision_db() as session:
        failed = await AgentRuns.fail_decision_execution(
            recorded.execution.id,
            claim_token=claim.execution.claim_token,
            error={'code': 'runtime_rejected'},
            db=session,
        )
        assert failed.status == 'failed'
        await session.rollback()

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    run = await AgentRuns.get_run(run_id)
    events = await AgentRuns.list_events(run_id)
    assert execution is not None
    assert execution.status == 'claimed'
    assert run is not None
    assert run.state == 'waiting_approval'
    assert 'run.failed' not in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_claim_next_and_cancel_pending_respect_external_session_rollback(
    decision_db,
):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')

    async with decision_db() as session:
        claimed = await AgentRuns.claim_next_decision_execution(
            worker_id='worker-1',
            lease_seconds=30,
            db=session,
        )
        assert claimed is not None
        assert claimed.id == recorded.execution.id
        assert claimed.status == 'claimed'
        await session.rollback()

        cancelled = await AgentRuns.cancel_pending_decision_executions(
            run_id,
            db=session,
        )
        assert cancelled == 1
        await session.rollback()

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    assert execution is not None
    assert execution.status == 'pending'


@pytest.mark.asyncio
async def test_two_dispatchers_cannot_claim_the_same_pending_execution(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')

    first, second = await asyncio.gather(
        AgentRuns.claim_decision_execution(recorded.execution.id, worker_id='worker-1', lease_seconds=30),
        AgentRuns.claim_decision_execution(recorded.execution.id, worker_id='worker-2', lease_seconds=30),
    )

    claimed = [item for item in (first, second) if item is not None]
    assert len(claimed) == 1
    assert claimed[0].execution.status == 'claimed'
    assert claimed[0].execution.claim_owner in {'worker-1', 'worker-2'}


@pytest.mark.asyncio
async def test_release_uses_exponential_backoff_and_skips_poison_row(
    decision_db,
):
    poison_run_id, poison_approval_id = await _waiting_resource('approval')
    poison = await _record(
        poison_run_id,
        'approval',
        poison_approval_id,
        'approved',
        'poison-key',
    )
    first_claim = await AgentRuns.claim_decision_execution(
        poison.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert first_claim is not None
    now = agent_run_models._now_ns()
    released = await AgentRuns.release_decision_execution(
        poison.execution.id,
        {'code': 'transient'},
        claim_token=first_claim.execution.claim_token,
        now_ns=now,
        jitter_fraction=0.5,
    )
    assert released.status == 'pending'
    assert released.next_attempt_at == now + 1_000_000_000

    healthy_run_id, healthy_approval_id = await _waiting_resource('approval')
    healthy = await _record(
        healthy_run_id,
        'approval',
        healthy_approval_id,
        'approved',
        'healthy-key',
    )
    selection_now = agent_run_models._now_ns()
    selected = await AgentRuns.claim_next_decision_execution(
        worker_id='worker-2',
        lease_seconds=30,
        now_ns=selection_now,
    )

    assert selected is not None
    assert selected.id == healthy.execution.id

    second_claim = await AgentRuns.claim_decision_execution(
        poison.execution.id,
        worker_id='worker-3',
        lease_seconds=30,
        now_ns=released.next_attempt_at,
    )
    assert second_claim is not None
    second_release = await AgentRuns.release_decision_execution(
        poison.execution.id,
        {'code': 'transient-again'},
        claim_token=second_claim.execution.claim_token,
        now_ns=now,
        jitter_fraction=0.5,
    )
    assert second_release.next_attempt_at == now + 2_000_000_000


@pytest.mark.asyncio
async def test_release_default_schedule_uses_database_clock_under_worker_skew(
    decision_db,
    monkeypatch,
):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    claim = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert claim is not None
    async with decision_db() as session:
        database_now = await agent_run_models._database_now_ns(session)
    monkeypatch.setattr(
        agent_run_models,
        '_now_ns',
        lambda: 9_000_000_000_000_000_000,
    )

    released = await AgentRuns.release_decision_execution(
        recorded.execution.id,
        {'code': 'transient'},
        claim_token=claim.execution.claim_token,
        jitter_fraction=0.5,
    )

    assert database_now + 900_000_000 <= released.next_attempt_at
    assert released.next_attempt_at <= database_now + 1_200_000_000


@pytest.mark.asyncio
async def test_expired_prepared_and_backend_committed_executions_are_reclaimed(
    decision_db,
):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    first = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert first is not None
    await AgentRuns.mark_decision_execution_prepared(
        recorded.execution.id,
        _runtime_response(
            {
                'execution_id': recorded.execution.id,
                'runtime_session_id': 'runtime-session-1',
                'subject_id': approval_id,
                'command_type': 'resume_approval',
                'expected_checkpoint_version': 7,
            },
            run_id,
            state='prepared',
        ),
        claim_token=first.execution.claim_token,
    )
    async with decision_db() as session:
        await session.execute(
            agent_run_models.AgentRunDecisionExecution.__table__.update()
            .where(
                agent_run_models.AgentRunDecisionExecution.id
                == recorded.execution.id
            )
            .values(claim_expires_at=0)
        )
        await session.commit()

    prepared = await AgentRuns.claim_next_decision_execution(
        worker_id='worker-2',
        lease_seconds=30,
    )

    assert prepared is not None
    assert prepared.status == 'prepared'
    assert prepared.claim_owner == 'worker-2'

    await AgentRuns.commit_prepared_decision_execution(
        recorded.execution.id,
        claim_token=prepared.claim_token,
    )
    async with decision_db() as session:
        await session.execute(
            agent_run_models.AgentRunDecisionExecution.__table__.update()
            .where(
                agent_run_models.AgentRunDecisionExecution.id
                == recorded.execution.id
            )
            .values(claim_expires_at=0)
        )
        await session.commit()

    backend_committed = await AgentRuns.claim_next_decision_execution(
        worker_id='worker-3',
        lease_seconds=30,
    )

    assert backend_committed is not None
    assert backend_committed.status == 'backend_committed'
    assert backend_committed.claim_owner == 'worker-3'


@pytest.mark.asyncio
async def test_stale_claim_token_cannot_write_after_lease_takeover(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    first = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert first is not None
    first_token = first.execution.claim_token
    async with decision_db() as session:
        await session.execute(
            agent_run_models.AgentRunDecisionExecution.__table__.update()
            .where(
                agent_run_models.AgentRunDecisionExecution.id
                == recorded.execution.id
            )
            .values(claim_expires_at=0)
        )
        await session.commit()
    second = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-2',
        lease_seconds=30,
    )
    assert second is not None
    second_token = second.execution.claim_token
    prepared_response = _runtime_response(
        {
            'execution_id': recorded.execution.id,
            'runtime_session_id': 'runtime-session-1',
            'subject_id': approval_id,
            'command_type': 'resume_approval',
            'expected_checkpoint_version': 7,
        },
        run_id,
        state='prepared',
    )
    prepared_response['fingerprint'] = recorded.execution.fingerprint
    await AgentRuns.mark_decision_execution_prepared(
        recorded.execution.id,
        prepared_response,
        claim_token=second_token,
    )
    await AgentRuns.commit_prepared_decision_execution(
        recorded.execution.id,
        claim_token=second_token,
    )
    await AgentRuns.begin_decision_activation(
        recorded.execution.id,
        claim_token=second_token,
    )
    activated_response = {
        **prepared_response,
        'state': 'activated',
        'checkpoint_version': 8,
    }
    await AgentRuns.record_decision_runtime_state(
        recorded.execution.id,
        activated_response,
        claim_token=second_token,
    )

    with pytest.raises(agent_run_models.AgentRunDecisionConflict, match='claim'):
        await AgentRuns.mark_decision_execution_prepared(
            recorded.execution.id,
            prepared_response,
            claim_token=first_token,
        )
    with pytest.raises(agent_run_models.AgentRunDecisionConflict, match='claim'):
        await AgentRuns.commit_prepared_decision_execution(
            recorded.execution.id,
            claim_token=first_token,
        )
    with pytest.raises(agent_run_models.AgentRunDecisionConflict, match='claim'):
        await AgentRuns.record_decision_runtime_state(
            recorded.execution.id,
            activated_response,
            claim_token=first_token,
        )

    current = await AgentRuns.get_decision_execution(recorded.execution.id)
    assert current is not None
    assert current.status == 'activated'
    assert current.claim_token == second_token


@pytest.mark.asyncio
async def test_stale_session_identity_cannot_commit_after_worker_takeover(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    first = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert first is not None
    await AgentRuns.mark_decision_execution_prepared(
        recorded.execution.id,
        _runtime_response(
            {
                'execution_id': recorded.execution.id,
                'runtime_session_id': 'runtime-session-1',
                'subject_id': approval_id,
                'command_type': 'resume_approval',
                'expected_checkpoint_version': 7,
            },
            run_id,
            state='prepared',
        ),
        claim_token=first.execution.claim_token,
    )

    async with decision_db() as stale_session:
        stale = await stale_session.get(
            agent_run_models.AgentRunDecisionExecution,
            recorded.execution.id,
        )
        assert stale is not None
        assert stale.claim_token == first.execution.claim_token
        await stale_session.commit()

        async with decision_db() as takeover_session:
            await takeover_session.execute(
                agent_run_models.AgentRunDecisionExecution.__table__.update()
                .where(
                    agent_run_models.AgentRunDecisionExecution.id
                    == recorded.execution.id
                )
                .values(claim_expires_at=0)
            )
            await takeover_session.commit()
        second = await AgentRuns.claim_decision_execution(
            recorded.execution.id,
            worker_id='worker-2',
            lease_seconds=30,
        )
        assert second is not None
        await AgentRuns.commit_prepared_decision_execution(
            recorded.execution.id,
            claim_token=second.execution.claim_token,
        )

        with pytest.raises(
            agent_run_models.AgentRunDecisionConflict,
            match='claim',
        ):
            await AgentRuns.commit_prepared_decision_execution(
                recorded.execution.id,
                claim_token=first.execution.claim_token,
                db=stale_session,
            )

    current = await AgentRuns.get_decision_execution(recorded.execution.id)
    assert current is not None
    assert current.status == 'backend_committed'
    assert current.claim_token == second.execution.claim_token


@pytest.mark.asyncio
async def test_stale_worker_cannot_fail_execution_after_takeover(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    first = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert first is not None

    async with decision_db() as stale_session:
        stale = await stale_session.get(
            agent_run_models.AgentRunDecisionExecution,
            recorded.execution.id,
        )
        assert stale is not None
        assert stale.claim_token == first.execution.claim_token
        await stale_session.commit()

        async with decision_db() as takeover_session:
            await takeover_session.execute(
                agent_run_models.AgentRunDecisionExecution.__table__.update()
                .where(
                    agent_run_models.AgentRunDecisionExecution.id
                    == recorded.execution.id
                )
                .values(claim_expires_at=0)
            )
            await takeover_session.commit()

        second = await AgentRuns.claim_decision_execution(
            recorded.execution.id,
            worker_id='worker-2',
            lease_seconds=30,
        )
        assert second is not None

        with pytest.raises(agent_run_models.AgentRunDecisionConflict, match='claim'):
            await AgentRuns.fail_decision_execution(
                recorded.execution.id,
                claim_token=first.execution.claim_token,
                error={'code': 'stale_worker_failure'},
                db=stale_session,
            )

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    run = await AgentRuns.get_run(run_id)
    assert execution is not None
    assert execution.status == 'claimed'
    assert execution.claim_owner == 'worker-2'
    assert execution.claim_token == second.execution.claim_token
    assert run is not None
    assert run.state == 'waiting_approval'


class StubRuntimeClient:
    def __init__(
        self,
        *,
        prepare_error=None,
        prepare_state='prepared',
        query_state='prepared',
        query_error=None,
        activate_error=None,
        subject_id=None,
        command_type=None,
        fingerprint=None,
        prepare_response_updates=None,
        activate_state='activated',
    ):
        self.prepare_error = prepare_error
        self.prepare_state = prepare_state
        self.query_state = query_state
        self.query_error = query_error
        self.activate_error = activate_error
        self.subject_id = subject_id
        self.command_type = command_type
        self.fingerprint = fingerprint
        self.prepare_response_updates = prepare_response_updates or {}
        self.activate_state = activate_state
        self.calls = []
        self.prepared_payload = None

    async def prepare_decision_execution(self, run_id, execution_id, payload):
        self.calls.append(('prepare', run_id, execution_id, payload))
        self.prepared_payload = payload
        if self.prepare_error is not None:
            raise self.prepare_error
        return {
            **_runtime_response(payload, run_id, state=self.prepare_state),
            **self.prepare_response_updates,
        }

    async def activate_decision_execution(self, run_id, execution_id):
        self.calls.append(('activate', run_id, execution_id, None))
        if self.activate_error is not None:
            raise self.activate_error
        payload = self.prepared_payload or {}
        return {
            'execution_id': execution_id,
            'run_id': run_id,
            'runtime_session_id': 'runtime-session-1',
            'subject_id': payload.get('subject_id') or self.subject_id,
            'command_type': payload.get('command_type') or self.command_type,
            'state': self.activate_state,
            'fingerprint': payload.get('fingerprint') or self.fingerprint,
            'checkpoint_version': 8,
            'duplicate': False,
            'outcome': (
                {'status': 'success'} if self.activate_state == 'applied' else None
            ),
            'error': (
                {
                    'code': self.activate_state,
                    'message': f'Runtime execution {self.activate_state}',
                }
                if self.activate_state in {'failed', 'indeterminate', 'unrecoverable'}
                else None
            ),
        }

    async def get_decision_execution(self, run_id, execution_id):
        self.calls.append(('query', run_id, execution_id, None))
        if self.query_error is not None:
            raise self.query_error
        payload = self.prepared_payload or {}
        return {
            'execution_id': execution_id,
            'run_id': run_id,
            'runtime_session_id': 'runtime-session-1',
            'subject_id': payload.get('subject_id') or self.subject_id,
            'command_type': payload.get('command_type') or self.command_type,
            'state': self.query_state,
            'fingerprint': payload.get('fingerprint') or self.fingerprint,
            'checkpoint_version': (
                payload.get('expected_checkpoint_version', 0)
                if self.query_state == 'prepared'
                else 8
            ),
            'duplicate': True,
            'outcome': {'status': 'success'} if self.query_state == 'applied' else None,
            'error': None,
        }


class SlowPrepareRuntimeClient(StubRuntimeClient):
    def __init__(self, delay_seconds: float):
        super().__init__()
        self.delay_seconds = delay_seconds
        self.prepare_started = asyncio.Event()
        self.prepare_cancelled = asyncio.Event()

    async def prepare_decision_execution(self, run_id, execution_id, payload):
        self.calls.append(('prepare', run_id, execution_id, payload))
        self.prepared_payload = payload
        self.prepare_started.set()
        try:
            await asyncio.sleep(self.delay_seconds)
        except asyncio.CancelledError:
            self.prepare_cancelled.set()
            raise
        return _runtime_response(payload, run_id, state='prepared')


def _runtime_response(payload, run_id, *, state):
    return {
        'execution_id': payload['execution_id'],
        'run_id': run_id,
        'runtime_session_id': payload['runtime_session_id'],
        'subject_id': payload['subject_id'],
        'command_type': payload['command_type'],
        'state': state,
        'fingerprint': payload.get('fingerprint'),
        'checkpoint_version': payload['expected_checkpoint_version'],
        'duplicate': False,
        'outcome': None,
        'error': None,
    }


@pytest.mark.asyncio
async def test_prepare_wire_uses_runtime_expected_checkpoint_version_field(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    runtime = StubRuntimeClient()

    await AgentDecisionExecutionDispatcher(
        AgentRuns,
        runtime,
        worker_id='worker-1',
    ).dispatch_execution(recorded.execution.id)

    assert runtime.prepared_payload['expected_checkpoint_version'] == 7
    assert 'checkpoint_version' not in runtime.prepared_payload


@pytest.mark.asyncio
async def test_backend_prepare_body_is_accepted_by_runtime_schema_over_asgi(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    runtime_app = FastAPI()

    @runtime_app.put(
        '/v1/openwebui/runs/{run_id}/executions/{execution_id}',
        response_model=RuntimeExecutionResponse,
    )
    async def prepare_execution(
        run_id: str,
        execution_id: str,
        body: RuntimeExecutionPrepareRequest,
    ):
        return {
            'execution_id': execution_id,
            'run_id': run_id,
            'runtime_session_id': body.runtime_session_id,
            'subject_id': body.subject_id,
            'command_type': body.command_type,
            'fingerprint': body.fingerprint,
            'state': 'prepared',
            'checkpoint_version': body.expected_checkpoint_version,
        }

    class RuntimeSchemaClient(StubRuntimeClient):
        status_code = None

        async def prepare_decision_execution(self, run_id, execution_id, payload):
            self.calls.append(('prepare', run_id, execution_id, payload))
            self.prepared_payload = payload
            async with AsyncClient(
                transport=ASGITransport(app=runtime_app),
                base_url='http://runtime.test',
            ) as client:
                response = await client.put(
                    f'/v1/openwebui/runs/{run_id}/executions/{execution_id}',
                    json=payload,
                )
            self.status_code = response.status_code
            response.raise_for_status()
            return response.json()

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    runtime = RuntimeSchemaClient()

    await AgentDecisionExecutionDispatcher(
        AgentRuns,
        runtime,
        worker_id='worker-1',
    ).dispatch_execution(recorded.execution.id)

    assert runtime.status_code == 200


@pytest.mark.asyncio
async def test_claim_expiry_uses_database_clock_not_worker_clock_skew(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    first = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert first is not None

    skewed = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-skewed',
        lease_seconds=30,
        now_ns=9_000_000_000_000_000_000,
    )

    assert skewed is None
    current = await AgentRuns.get_decision_execution(recorded.execution.id)
    assert current is not None
    assert current.claim_owner == 'worker-1'
    assert current.claim_token == first.execution.claim_token


@pytest.mark.asyncio
async def test_slow_prepare_renews_claim_beyond_original_lease(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    runtime = SlowPrepareRuntimeClient(delay_seconds=0.24)
    dispatcher = AgentDecisionExecutionDispatcher(
        AgentRuns,
        runtime,
        worker_id='worker-1',
        lease_seconds=0.09,
        heartbeat_seconds=0.02,
    )

    task = asyncio.create_task(dispatcher.dispatch_execution(recorded.execution.id))
    await asyncio.wait_for(runtime.prepare_started.wait(), timeout=1)
    await asyncio.sleep(0.13)
    takeover = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-2',
        lease_seconds=0.09,
    )
    result = await asyncio.wait_for(task, timeout=1)

    assert takeover is None
    assert result.status == 'activated'
    assert [call[0] for call in runtime.calls] == ['prepare', 'activate']


@pytest.mark.asyncio
async def test_two_workers_never_duplicate_send_during_slow_prepare(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    first_runtime = SlowPrepareRuntimeClient(delay_seconds=0.45)
    first_dispatcher = AgentDecisionExecutionDispatcher(
        AgentRuns,
        first_runtime,
        worker_id='worker-1',
        lease_seconds=0.2,
        heartbeat_seconds=0.04,
    )
    first_task = asyncio.create_task(
        first_dispatcher.dispatch_execution(recorded.execution.id)
    )
    await asyncio.wait_for(first_runtime.prepare_started.wait(), timeout=1)
    await asyncio.sleep(0.27)

    second_runtime = StubRuntimeClient()
    second_result = await AgentDecisionExecutionDispatcher(
        AgentRuns,
        second_runtime,
        worker_id='worker-2',
        lease_seconds=0.2,
        heartbeat_seconds=0.04,
    ).dispatch_execution(recorded.execution.id)
    first_result = await asyncio.wait_for(first_task, timeout=1)

    assert second_runtime.calls == []
    assert second_result.claim_owner == 'worker-1'
    assert first_result.status == 'activated'


@pytest.mark.asyncio
async def test_lost_lease_cancels_inflight_runtime_call_before_state_write(decision_db):
    from open_webui.agent.decision_execution import (
        AgentDecisionExecutionDispatcher,
        AgentDecisionExecutionLeaseLost,
    )

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    runtime = SlowPrepareRuntimeClient(delay_seconds=10)
    task = asyncio.create_task(
        AgentDecisionExecutionDispatcher(
            AgentRuns,
            runtime,
            worker_id='worker-1',
            lease_seconds=0.2,
            heartbeat_seconds=0.03,
        ).dispatch_execution(recorded.execution.id)
    )
    await asyncio.wait_for(runtime.prepare_started.wait(), timeout=1)

    async with decision_db() as session:
        await session.execute(
            agent_run_models.AgentRunDecisionExecution.__table__.update()
            .where(
                agent_run_models.AgentRunDecisionExecution.id
                == recorded.execution.id
            )
            .values(
                claim_owner='worker-2',
                claim_token='takeover-token',
                claim_expires_at=9_000_000_000_000_000_000,
            )
        )
        await session.commit()

    with pytest.raises(AgentDecisionExecutionLeaseLost):
        await asyncio.wait_for(task, timeout=1)

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    events = await AgentRuns.list_events(run_id)
    assert runtime.prepare_cancelled.is_set()
    assert execution is not None
    assert execution.status == 'claimed'
    assert execution.claim_token == 'takeover-token'
    assert 'approval.completed' not in [event.event_type for event in events]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'updates',
    [
        {'state': 'failed'},
        {'state': 'unrecoverable'},
        {'state': 'indeterminate'},
        {'fingerprint': 'wrong-fingerprint'},
        {'checkpoint_version': 8},
    ],
)
async def test_prepare_rejects_invalid_wire_ack_without_committing_completion(
    decision_db,
    updates,
):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    dispatcher = AgentDecisionExecutionDispatcher(
        AgentRuns,
        StubRuntimeClient(prepare_response_updates=updates),
        worker_id='worker-1',
    )

    result = await dispatcher.dispatch_execution(recorded.execution.id)

    assert result.status == 'failed'
    run = await AgentRuns.get_run(run_id)
    events = await AgentRuns.list_events(run_id)
    assert run is not None
    assert run.state == 'failed'
    assert [event.event_type for event in events].count('run.failed') == 1
    assert 'approval.completed' not in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_prepare_lost_response_reconciles_by_query_and_commits_lifecycle(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher
    from open_webui.agent.runtime_client import AgentRuntimeUnavailable

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    runtime = StubRuntimeClient(
        prepare_error=AgentRuntimeUnavailable('prepare response lost'),
        query_state='prepared',
    )
    dispatcher = AgentDecisionExecutionDispatcher(AgentRuns, runtime, worker_id='worker-1')

    result = await dispatcher.dispatch_execution(recorded.execution.id)

    assert result.status == 'activated'
    run = await AgentRuns.get_run(run_id)
    events = await AgentRuns.list_events(run_id)
    assert run.state == 'running'
    assert [event.event_type for event in events][-1] == 'approval.completed'
    assert [call[0] for call in runtime.calls] == ['prepare', 'query', 'activate']


@pytest.mark.asyncio
async def test_backend_commit_rolls_back_if_completion_event_insert_fails(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    async with decision_db() as session:
        await session.execute(
            text(
                """
                CREATE TRIGGER reject_decision_completion
                BEFORE INSERT ON agent_run_event
                WHEN NEW.event_type = 'approval.completed'
                BEGIN
                    SELECT RAISE(ABORT, 'forced completion failure');
                END
                """
            )
        )
        await session.commit()

    dispatcher = AgentDecisionExecutionDispatcher(AgentRuns, StubRuntimeClient(), worker_id='worker-1')
    with pytest.raises(Exception, match='forced completion failure'):
        await dispatcher.dispatch_execution(recorded.execution.id)

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    run = await AgentRuns.get_run(run_id)
    assert execution.status == 'prepared'
    assert execution.completion_event_seq is None
    assert run.state == 'waiting_approval'


@pytest.mark.asyncio
async def test_activate_lost_response_reconciles_terminal_runtime_state(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher
    from open_webui.agent.runtime_client import AgentRuntimeUnavailable

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    runtime = StubRuntimeClient(
        activate_error=AgentRuntimeUnavailable('activate response lost'),
        query_state='applied',
    )
    dispatcher = AgentDecisionExecutionDispatcher(AgentRuns, runtime, worker_id='worker-1')

    result = await dispatcher.dispatch_execution(recorded.execution.id)

    assert result.status == 'succeeded'
    assert result.runtime_outcome == {'status': 'success'}
    assert [call[0] for call in runtime.calls] == ['prepare', 'activate', 'query']


@pytest.mark.asyncio
async def test_applied_runtime_state_completes_execution(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')

    result = await AgentDecisionExecutionDispatcher(
        AgentRuns,
        StubRuntimeClient(activate_state='applied'),
        worker_id='worker-1',
    ).dispatch_execution(recorded.execution.id)

    assert result.status == 'succeeded'
    assert result.runtime_outcome == {'status': 'success'}


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime_state', ['indeterminate', 'unrecoverable'])
async def test_unrecoverable_runtime_outcome_fails_run_once(
    decision_db,
    runtime_state,
):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    dispatcher = AgentDecisionExecutionDispatcher(
        AgentRuns,
        StubRuntimeClient(activate_state=runtime_state),
        worker_id='worker-1',
    )

    first = await dispatcher.dispatch_execution(recorded.execution.id)
    replay = await dispatcher.dispatch_execution(recorded.execution.id)

    assert first.status == replay.status == 'failed'
    run = await AgentRuns.get_run(run_id)
    events = await AgentRuns.list_events(run_id)
    assert run is not None
    assert run.state == 'failed'
    assert [event.event_type for event in events].count('run.failed') == 1


@pytest.mark.asyncio
async def test_activation_outage_retries_backend_committed_execution_after_lease(
    decision_db,
):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher
    from open_webui.agent.runtime_client import AgentRuntimeUnavailable

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    unavailable = AgentRuntimeUnavailable('runtime unavailable')
    first_dispatcher = AgentDecisionExecutionDispatcher(
        AgentRuns,
        StubRuntimeClient(
            activate_error=unavailable,
            query_error=unavailable,
        ),
        worker_id='worker-1',
        clock_ns=lambda: 10_000_000_000,
        random_fn=lambda: 0.5,
    )

    with pytest.raises(AgentRuntimeUnavailable):
        await first_dispatcher.dispatch_execution(recorded.execution.id)

    interrupted = await AgentRuns.get_decision_execution(recorded.execution.id)
    assert interrupted.status == 'backend_committed'
    assert interrupted.claim_token is None
    assert interrupted.next_attempt_at == 11_000_000_000
    async with decision_db() as session:
        await session.execute(
            agent_run_models.AgentRunDecisionExecution.__table__.update()
            .where(
                agent_run_models.AgentRunDecisionExecution.id
                == recorded.execution.id
            )
            .values(claim_expires_at=0)
        )
        await session.commit()

    resumed = await AgentDecisionExecutionDispatcher(
        AgentRuns,
        StubRuntimeClient(
            subject_id=approval_id,
            command_type='resume_approval',
            fingerprint=recorded.execution.fingerprint,
        ),
        worker_id='worker-2',
    ).dispatch_execution(recorded.execution.id)

    assert resumed.status == 'activated'
    assert resumed.claim_owner == 'worker-2'


@pytest.mark.asyncio
async def test_prepare_and_query_transients_preserve_longest_retry_after(
    decision_db,
):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher
    from open_webui.agent.runtime_client import AgentRuntimeUnavailable

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    async with decision_db() as session:
        database_now = await agent_run_models._database_now_ns(session)
    runtime = StubRuntimeClient(
        prepare_error=AgentRuntimeUnavailable(
            'HTTP 429',
            retry_after_seconds=60,
        ),
        query_error=AgentRuntimeUnavailable('HTTP 503'),
    )

    with pytest.raises(AgentRuntimeUnavailable, match='HTTP 503'):
        await AgentDecisionExecutionDispatcher(
            AgentRuns,
            runtime,
            worker_id='worker-1',
            random_fn=lambda: 0.5,
        ).dispatch_execution(recorded.execution.id)

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    assert execution is not None
    assert [call[0] for call in runtime.calls] == ['prepare', 'query']
    assert database_now + 60_000_000_000 <= execution.next_attempt_at
    assert execution.next_attempt_at <= database_now + 60_200_000_000


@pytest.mark.asyncio
async def test_permanent_prepare_protocol_error_closes_run_once(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher
    from open_webui.agent.runtime_client import AgentRuntimeRejected

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    runtime = StubRuntimeClient(
        prepare_error=AgentRuntimeRejected('execution_conflict'),
    )
    dispatcher = AgentDecisionExecutionDispatcher(AgentRuns, runtime, worker_id='worker-1')

    first = await dispatcher.dispatch_execution(recorded.execution.id)
    duplicate = await dispatcher.dispatch_execution(recorded.execution.id)

    assert first.status == duplicate.status == 'failed'
    run = await AgentRuns.get_run(run_id)
    events = await AgentRuns.list_events(run_id)
    assert run.state == 'failed'
    assert [event.event_type for event in events].count('run.failed') == 1


@pytest.mark.asyncio
async def test_runtime_auth_error_fails_execution_and_run_once(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher
    from open_webui.agent.runtime_client import AgentRuntimeAuthenticationError

    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    dispatcher = AgentDecisionExecutionDispatcher(
        AgentRuns,
        StubRuntimeClient(
            prepare_error=AgentRuntimeAuthenticationError('invalid runtime token')
        ),
        worker_id='worker-1',
        clock_ns=lambda: 20_000_000_000,
        random_fn=lambda: 0.5,
    )

    first = await dispatcher.dispatch_execution(recorded.execution.id)
    replay = await dispatcher.dispatch_execution(recorded.execution.id)

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    run = await AgentRuns.get_run(run_id)
    events = await AgentRuns.list_events(run_id)
    assert execution is not None
    assert first.status == replay.status == 'failed'
    assert execution.status == 'failed'
    assert execution.last_error['code'] == 'agent_runtime_auth_error'
    assert run is not None
    assert run.state == 'failed'
    assert [event.event_type for event in events].count('run.failed') == 1


@pytest.mark.asyncio
async def test_cancelled_execution_is_not_dispatched(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, input_id = await _waiting_resource('user_input')
    recorded = await _record(
        run_id,
        'user_input',
        input_id,
        'accepted',
        'caller-1',
        payload={'content': {'answer': 'A'}},
    )
    await AgentRuns.cancel_pending_decision_executions(run_id)
    runtime = StubRuntimeClient()
    dispatcher = AgentDecisionExecutionDispatcher(AgentRuns, runtime, worker_id='worker-1')

    result = await dispatcher.dispatch_execution(recorded.execution.id)

    assert result.status == 'cancelled'
    assert runtime.calls == []


async def _backend_committed_approval() -> tuple[str, str, str]:
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    claim = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert claim is not None
    prepared = _runtime_response(
        {
            'execution_id': recorded.execution.id,
            'runtime_session_id': 'runtime-session-1',
            'subject_id': approval_id,
            'command_type': 'resume_approval',
            'expected_checkpoint_version': 7,
            'fingerprint': recorded.execution.fingerprint,
        },
        run_id,
        state='prepared',
    )
    await AgentRuns.mark_decision_execution_prepared(
        recorded.execution.id,
        prepared,
        claim_token=claim.execution.claim_token,
    )
    await AgentRuns.commit_prepared_decision_execution(
        recorded.execution.id,
        claim_token=claim.execution.claim_token,
    )
    return run_id, recorded.execution.id, claim.execution.claim_token


@pytest.mark.asyncio
async def test_cancel_winning_before_activation_never_calls_runtime(decision_db):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher

    run_id, execution_id, _claim_token = await _backend_committed_approval()
    await AgentRuns.cancel_run_with_decision_executions(
        run_id,
        runtime_session_id='runtime-session-1',
    )
    runtime = StubRuntimeClient()

    result = await AgentDecisionExecutionDispatcher(
        AgentRuns,
        runtime,
        worker_id='worker-1',
    ).dispatch_execution(execution_id)

    assert result.status == 'cancelled'
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_activation_winning_then_cancel_keeps_cancelled_state(
    decision_db,
    monkeypatch,
):
    from open_webui.agent.decision_execution import AgentDecisionExecutionDispatcher
    from open_webui.routers import agent_runs

    run_id, execution_id, _claim_token = await _backend_committed_approval()
    execution = await AgentRuns.get_decision_execution(execution_id)
    assert execution is not None

    runtime_cancels = []

    class RuntimeCancelClient:
        def __init__(self, base_url, *, service_token=None, timeout=None):
            pass

        async def cancel_run(self, runtime_run_id):
            runtime_cancels.append(runtime_run_id)
            return {'run_id': runtime_run_id, 'state': 'cancelled'}

    monkeypatch.setattr(agent_runs, 'AgentRuntimeClient', RuntimeCancelClient)
    cancel_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    AGENT_RUNTIME_BASE_URL='http://runtime.test',
                    AGENT_RUNTIME_SERVICE_TOKEN='secret',
                    AGENT_RUN_DEFAULT_TIMEOUT_SECONDS=30,
                )
            )
        )
    )

    class CancelDuringActivateRuntime(StubRuntimeClient):
        async def activate_decision_execution(self, runtime_run_id, runtime_execution_id):
            self.calls.append(
                ('activate', runtime_run_id, runtime_execution_id, None)
            )
            await agent_runs.cancel_agent_run(
                cancel_request,
                runtime_run_id,
                user=SimpleNamespace(id='user-1'),
            )
            return {
                'execution_id': runtime_execution_id,
                'run_id': runtime_run_id,
                'runtime_session_id': 'runtime-session-1',
                'subject_id': execution.resource_id,
                'command_type': execution.command_type,
                'state': 'applied',
                'fingerprint': execution.fingerprint,
                'checkpoint_version': 8,
                'duplicate': False,
                'outcome': {'status': 'success'},
                'error': None,
            }

    runtime = CancelDuringActivateRuntime()
    result = await AgentDecisionExecutionDispatcher(
        AgentRuns,
        runtime,
        worker_id='worker-1',
    ).dispatch_execution(execution_id)

    run = await AgentRuns.get_run(run_id)
    assert [call[0] for call in runtime.calls] == ['activate']
    assert run is not None
    assert run.state == 'cancelled'
    assert result.status == 'cancelled'
    assert runtime_cancels == [run_id]


@pytest.mark.asyncio
async def test_backend_committed_execution_does_not_authorize_tool_replay(
    decision_db,
):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')
    claim = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert claim is not None
    await AgentRuns.mark_decision_execution_prepared(
        recorded.execution.id,
        _runtime_response(
            {
                'execution_id': recorded.execution.id,
                'runtime_session_id': 'runtime-session-1',
                'subject_id': approval_id,
                'command_type': 'resume_approval',
                'expected_checkpoint_version': 7,
            },
            run_id,
            state='prepared',
        ),
        claim_token=claim.execution.claim_token,
    )
    await AgentRuns.commit_prepared_decision_execution(
        recorded.execution.id,
        claim_token=claim.execution.claim_token,
    )

    authorized = await AgentRuns.validate_approved_tool_replay(
        run_id,
        execution_id=recorded.execution.id,
        tool_call_id='tool-call-1',
        tool_id='tool-1',
        arguments={'path': '/workspace/report.txt'},
        idempotency_key='tool:leader:tool-call-1:1',
    )
    wrong_call = await AgentRuns.validate_approved_tool_replay(
        run_id,
        execution_id=recorded.execution.id,
        tool_call_id='tool-call-forged',
        tool_id='tool-1',
        arguments={'path': '/workspace/report.txt'},
        idempotency_key='tool:leader:tool-call-1:1',
    )

    assert authorized is None
    assert wrong_call is None

    activating = await AgentRuns.begin_decision_activation(
        recorded.execution.id,
        claim_token=claim.execution.claim_token,
    )
    authorized_after_cas = await AgentRuns.validate_approved_tool_replay(
        run_id,
        execution_id=recorded.execution.id,
        tool_call_id='tool-call-1',
        tool_id='tool-1',
        arguments={'path': '/workspace/report.txt'},
        idempotency_key='tool:leader:tool-call-1:1',
    )

    assert activating.status == 'activating'
    assert authorized_after_cas is not None
    assert authorized_after_cas.status == 'activating'


@pytest.mark.asyncio
async def test_run_cancel_atomically_cancels_uncommitted_decision_execution(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')

    result = await AgentRuns.cancel_run_with_decision_executions(
        run_id,
        runtime_session_id='runtime-session-1',
    )

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    run = await AgentRuns.get_run(run_id)
    assert result.created is True
    assert execution.status == 'cancelled'
    assert run.state == 'cancelled'


@pytest.mark.asyncio
async def test_run_cancel_respects_external_session_rollback(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    recorded = await _record(run_id, 'approval', approval_id, 'approved', 'caller-1')

    async with decision_db() as session:
        result = await AgentRuns.cancel_run_with_decision_executions(
            run_id,
            runtime_session_id='runtime-session-1',
            db=session,
        )
        assert result.created is True
        await session.rollback()

    execution = await AgentRuns.get_decision_execution(recorded.execution.id)
    run = await AgentRuns.get_run(run_id)
    events = await AgentRuns.list_events(run_id)
    assert execution is not None
    assert execution.status == 'pending'
    assert run is not None
    assert run.state == 'waiting_approval'
    assert 'run.cancelled' not in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_historical_completion_without_execution_is_not_replayed(decision_db):
    run_id, approval_id = await _waiting_resource('approval')
    async with decision_db() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        session.add(
            AgentRunEvent(
                id='legacy-approval-completed',
                run_id=run_id,
                seq=3,
                event_type='approval.completed',
                participant_id='leader',
                phase='running',
                summary='Legacy approval approved.',
                payload={'approval_id': approval_id, 'decision': 'approved'},
                created_at=1,
            )
        )
        run.state = 'running'
        run.state_version += 1
        await session.commit()

    result = await _record(run_id, 'approval', approval_id, 'approved', 'caller-legacy')

    assert result.execution is None
    assert result.historical_event.event_type == 'approval.completed'
    async with decision_db() as session:
        rows = (await session.execute(select(agent_run_models.AgentRunDecisionExecution))).scalars().all()
    assert rows == []
