"""Idempotency behavior for agent_service endpoints.

Phase 1 of streaming-text rollout: event.append is relaxed so that re-using an
idempotency key with a different payload returns the originally stored event
instead of failing the run with a 409. state.transition remains strict.
"""

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.internal.db import Base
from open_webui.models.agent_runs import (
    AgentArtifact,
    AgentRun,
    AgentRunEvent,
    AgentRunOperation,
    AgentRuns,
)
from open_webui.routers import agent_runs, agent_service
from open_webui.utils.auth import get_verified_user
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _service_headers(idempotency_key: str) -> dict[str, str]:
    return {
        'Authorization': 'Bearer service-token',
        'X-Agent-Idempotency-Key': idempotency_key,
    }


@pytest_asyncio.fixture
async def agent_run_db(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[
                    AgentRun.__table__,
                    AgentRunEvent.__table__,
                    AgentArtifact.__table__,
                    AgentRunOperation.__table__,
                ],
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

    monkeypatch.setattr('open_webui.models.agent_runs.get_async_db_context', session_context)

    yield session_factory

    await engine.dispose()


@pytest.fixture
def app_without_fake_event_store():
    app = FastAPI()
    app.state.config = SimpleNamespace(AGENT_RUNTIME_SERVICE_TOKEN='service-token')
    app.state.AGENT_TOOL_REGISTRY = {}
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='user-1')
    app.include_router(agent_runs.router, prefix='/api/agent/runs')
    app.include_router(agent_service.router, prefix='/api/agent/service')
    return app


async def _create_running_run() -> str:
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    await AgentRuns.transition_state(
        run.id,
        from_states=['queued'],
        to_state='running',
        reason='runtime accepted',
    )
    return run.id


@pytest.mark.asyncio
async def test_event_append_conflicting_payload_returns_existing_event(
    agent_run_db,
    app_without_fake_event_store,
):
    run_id = await _create_running_run()
    body = {
        'run_id': run_id,
        'event_type': 'subagent.completed',
        'phase': 'running',
        'summary': 'Subagent finished.',
        'payload': {'participant_id': 'sub-1', 'content': 'original'},
        'idempotency_key': 'evt:session:sub-1:completed',
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run_id}/events',
            json=body,
            headers=_service_headers('evt:session:sub-1:completed'),
        )
        # Same key, different payload — should still return the stored event.
        conflict = client.post(
            f'/api/agent/service/runs/{run_id}/events',
            json={
                **body,
                'summary': 'Subagent finished (retry).',
                'payload': {'participant_id': 'sub-1', 'content': 'different'},
            },
            headers=_service_headers('evt:session:sub-1:completed'),
        )

    assert first.status_code == 200
    assert conflict.status_code == 200
    assert conflict.json()['seq'] == first.json()['seq']
    assert conflict.json()['summary'] == 'Subagent finished.'
    assert conflict.json()['payload']['content'] == 'original'

    events = await AgentRuns.list_events(run_id)
    assert len(events) == 1
    assert events[0].payload['content'] == 'original'


@pytest.mark.asyncio
async def test_event_append_duplicate_payload_returns_existing_event(
    agent_run_db,
    app_without_fake_event_store,
):
    run_id = await _create_running_run()
    body = {
        'run_id': run_id,
        'event_type': 'run.running',
        'phase': 'running',
        'summary': 'Runtime accepted',
        'idempotency_key': 'evt:session:run-running',
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run_id}/events',
            json=body,
            headers=_service_headers('evt:session:run-running'),
        )
        duplicate = client.post(
            f'/api/agent/service/runs/{run_id}/events',
            json=body,
            headers=_service_headers('evt:session:run-running'),
        )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()


@pytest.mark.asyncio
async def test_state_transition_remains_strict_on_hash_conflict(
    agent_run_db,
    app_without_fake_event_store,
):
    run_id = await _create_running_run()
    body = {
        'run_id': run_id,
        'from_states': ['running'],
        'to_state': 'finalizing',
        'reason': 'runtime closing',
        'idempotency_key': 'state:run-1:finalizing',
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run_id}/state-transition',
            json=body,
            headers=_service_headers('state:run-1:finalizing'),
        )
        # Same key, different reason — state.transition must still 409 because
        # the run's state machine correctness depends on a single canonical
        # transition per idempotency_key.
        conflict = client.post(
            f'/api/agent/service/runs/{run_id}/state-transition',
            json={**body, 'reason': 'retry with different reason'},
            headers=_service_headers('state:run-1:finalizing'),
        )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()['detail'] == 'idempotency_conflict'


@pytest.mark.asyncio
async def test_final_delta_remains_strict_on_hash_conflict(
    agent_run_db,
    app_without_fake_event_store,
):
    run_id = await _create_running_run()
    await AgentRuns.transition_state(
        run_id,
        from_states=['running'],
        to_state='finalizing',
        reason='runtime closing',
    )
    await AgentRuns.append_event(
        run_id,
        event_type='final.started',
        participant_id='leader',
        phase='finalizing',
        summary='Final answer phase',
    )

    body = {
        'run_id': run_id,
        'final_stream_id': 'answer',
        'delta_index': 0,
        'delta': 'Hello',
        'idempotency_key': 'final:run-1:answer:0',
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run_id}/final-delta',
            json=body,
            headers=_service_headers('final:run-1:answer:0'),
        )
        # Same key, different delta content — final.delta must still 409 because
        # it writes the canonical final_text payload for the persisted message.
        conflict = client.post(
            f'/api/agent/service/runs/{run_id}/final-delta',
            json={**body, 'delta': 'Goodbye'},
            headers=_service_headers('final:run-1:answer:0'),
        )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()['detail'] == 'idempotency_conflict'


@pytest.mark.asyncio
async def test_event_append_in_progress_operation_returns_202(
    agent_run_db,
    app_without_fake_event_store,
    monkeypatch,
):
    """If a prior attempt left the operation in 'in_progress', the conflict
    path must surface 202 (operation_in_progress) so the caller can retry,
    NOT silently return a stored event.
    """
    run_id = await _create_running_run()

    # Pre-seed an in_progress operation row with the same idempotency_key but
    # a mismatched request_hash so claim_operation will raise Conflict.
    from open_webui.models.agent_runs import AgentRunOperation
    import time

    async with agent_run_db() as session:
        session.add(
            AgentRunOperation(
                id='op-seed-1',
                run_id=run_id,
                operation_type='event.append',
                idempotency_key='evt:session:seeded',
                request_hash='preseeded-hash-not-matching',
                status='in_progress',
                created_at=time.time_ns(),
                updated_at=time.time_ns(),
            )
        )
        await session.commit()

    body = {
        'run_id': run_id,
        'event_type': 'run.running',
        'phase': 'running',
        'summary': 'Runtime accepted',
        'idempotency_key': 'evt:session:seeded',
    }

    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/service/runs/{run_id}/events',
            json=body,
            headers=_service_headers('evt:session:seeded'),
        )

    assert response.status_code == 202
    assert response.json()['detail'] == 'operation_in_progress'


@pytest.mark.asyncio
async def test_event_append_failed_operation_returns_409(
    agent_run_db,
    app_without_fake_event_store,
):
    """If a prior attempt failed (status='failed'), the conflict path must
    surface the original error as 409, not silently retry.
    """
    run_id = await _create_running_run()

    from open_webui.models.agent_runs import AgentRunOperation
    import time

    async with agent_run_db() as session:
        session.add(
            AgentRunOperation(
                id='op-seed-failed',
                run_id=run_id,
                operation_type='event.append',
                idempotency_key='evt:session:failed',
                request_hash='preseeded-hash-not-matching',
                status='failed',
                error={
                    'code': 'event_append_failed',
                    'message': 'prior attempt crashed',
                },
                created_at=time.time_ns(),
                updated_at=time.time_ns(),
            )
        )
        await session.commit()

    body = {
        'run_id': run_id,
        'event_type': 'run.running',
        'phase': 'running',
        'summary': 'Runtime accepted',
        'idempotency_key': 'evt:session:failed',
    }

    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/service/runs/{run_id}/events',
            json=body,
            headers=_service_headers('evt:session:failed'),
        )

    assert response.status_code == 409
