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
from open_webui.agent.artifacts import AgentRunArtifactRegistrar
from open_webui.agent.tool_authority import build_tool_access_envelope
from open_webui.internal.db import Base
from open_webui.models.agent_runs import AgentArtifact, AgentRun, AgentRunEvent, AgentRunOperation, AgentRuns
from open_webui.routers import agent_runs, agent_service
from open_webui.routers import agent_service as agent_service_router
from open_webui.utils.auth import get_verified_user
from sqlalchemy import select
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


@pytest.mark.asyncio
async def test_agent_run_routes_use_agent_runs_db_when_no_fake_event_store(
    agent_run_db,
    app_without_fake_event_store,
):
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
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        summary='Runtime accepted',
    )

    with TestClient(app_without_fake_event_store) as client:
        detail = client.get(f'/api/agent/runs/{run.id}')
        events = client.get(f'/api/agent/runs/{run.id}/events/list')
        stream = client.get(f'/api/agent/runs/{run.id}/events')

    assert detail.status_code == 200
    assert detail.json()['state'] == 'running'
    assert detail.json()['state_version'] == 1
    assert events.status_code == 200
    assert events.json()['last_seq'] == 1
    assert events.json()['events'][0]['event_type'] == 'run.running'
    assert stream.status_code == 200
    assert 'event: run.running' in stream.text


@pytest.mark.asyncio
async def test_agent_run_cancel_marks_cancelled_and_rejects_late_completion(
    monkeypatch,
    agent_run_db,
    app_without_fake_event_store,
):
    runtime_cancels = []

    class RuntimeClient:
        def __init__(self, base_url, service_token=None, timeout=None):
            self.base_url = base_url
            self.service_token = service_token
            self.timeout = timeout

        async def cancel_run(self, run_id):
            runtime_cancels.append(
                {
                    'run_id': run_id,
                    'base_url': self.base_url,
                    'service_token': self.service_token,
                }
            )
            return {'run_id': run_id, 'state': 'cancelled', 'cancel_requested': True}

    monkeypatch.setattr(agent_runs, 'AgentRuntimeClient', RuntimeClient, raising=False)
    app_without_fake_event_store.state.config.AGENT_RUNTIME_BASE_URL = 'http://agent-runtime.test'
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
        payload={'runtime_session_id': 'runtime-session-1'},
    )

    with TestClient(app_without_fake_event_store) as client:
        cancel = client.post(f'/api/agent/runs/{run.id}/cancel')
        late_complete = client.post(
            f'/api/agent/service/runs/{run.id}/state-transition',
            json={
                'run_id': run.id,
                'from_states': ['finalizing'],
                'to_state': 'completed',
                'reason': 'runtime final answer completed',
                'payload': {},
                'idempotency_key': f'state:{run.id}:completed',
            },
            headers=_service_headers(f'state:{run.id}:completed'),
        )

    assert cancel.status_code == 200
    assert cancel.json()['state'] == 'cancelled'
    assert runtime_cancels == [
        {
            'run_id': run.id,
            'base_url': 'http://agent-runtime.test',
            'service_token': 'service-token',
        }
    ]
    assert late_complete.status_code == 409

    updated = await AgentRuns.get_run(run.id)
    assert updated is not None
    assert updated.state == 'cancelled'
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == ['run.cancelled']


@pytest.mark.asyncio
async def test_agent_service_event_callbacks_use_agent_runs_db_without_fake_event_store(
    agent_run_db,
    app_without_fake_event_store,
):
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )

    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json={
                'run_id': run.id,
                'event_type': 'run.running',
                'phase': 'running',
                'summary': 'Runtime accepted',
                'idempotency_key': 'evt:runtime:event-1',
            },
            headers=_service_headers('evt:runtime:event-1'),
        )

    assert response.status_code == 200
    assert response.json()['seq'] == 1
    assert response.json()['event_type'] == 'run.running'

    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == ['run.running']


@pytest.mark.asyncio
async def test_agent_service_event_callback_retries_are_idempotent_and_conflicting_bodies_fail(
    agent_run_db,
    app_without_fake_event_store,
):
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    body = {
        'run_id': run.id,
        'event_type': 'run.running',
        'phase': 'running',
        'summary': 'Runtime accepted',
        'idempotency_key': 'evt:runtime:event-1',
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json=body,
            headers=_service_headers('evt:runtime:event-1'),
        )
        duplicate = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json=body,
            headers=_service_headers('evt:runtime:event-1'),
        )
        conflict = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json={**body, 'summary': 'Changed body'},
            headers=_service_headers('evt:runtime:event-1'),
        )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()['detail'] == 'idempotency_conflict'

    events = await AgentRuns.list_events(run.id)
    assert len(events) == 1
    assert events[0].summary == 'Runtime accepted'


@pytest.mark.asyncio
async def test_agent_service_final_delta_uses_agent_runs_db_without_fake_event_store(
    agent_run_db,
    app_without_fake_event_store,
):
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
    await AgentRuns.transition_state(
        run.id,
        from_states=['running'],
        to_state='finalizing',
        reason='runtime closed work',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='final.started',
        participant_id='leader',
        phase='finalizing',
        summary='Final answer phase',
    )

    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/service/runs/{run.id}/final-delta',
            json={
                'run_id': run.id,
                'final_stream_id': 'answer',
                'delta_index': 0,
                'delta': 'hello',
                'idempotency_key': f'final:{run.id}:answer:0',
            },
            headers=_service_headers(f'final:{run.id}:answer:0'),
        )

    assert response.status_code == 200
    assert response.json()['event_type'] == 'final.delta'
    assert response.json()['payload']['text'] == 'hello'

    updated = await AgentRuns.get_run(run.id)
    assert updated is not None
    assert updated.final_text == 'hello'


@pytest.mark.asyncio
async def test_agent_service_final_delta_retries_are_idempotent_and_conflicting_bodies_fail(
    agent_run_db,
    app_without_fake_event_store,
):
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
    await AgentRuns.transition_state(
        run.id,
        from_states=['running'],
        to_state='finalizing',
        reason='runtime closed work',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='final.started',
        participant_id='leader',
        phase='finalizing',
        summary='Final answer phase',
    )
    key = f'final:{run.id}:answer:0'
    body = {
        'run_id': run.id,
        'final_stream_id': 'answer',
        'delta_index': 0,
        'delta': 'hello',
        'idempotency_key': key,
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run.id}/final-delta',
            json=body,
            headers=_service_headers(key),
        )
        duplicate = client.post(
            f'/api/agent/service/runs/{run.id}/final-delta',
            json=body,
            headers=_service_headers(key),
        )
        conflict = client.post(
            f'/api/agent/service/runs/{run.id}/final-delta',
            json={**body, 'delta': 'changed'},
            headers=_service_headers(key),
        )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()['detail'] == 'idempotency_conflict'

    updated = await AgentRuns.get_run(run.id)
    assert updated is not None
    assert updated.final_text == 'hello'
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == ['final.started', 'final.delta']


@pytest.mark.asyncio
async def test_agent_service_state_transition_completed_writes_final_text_to_chat(
    monkeypatch,
    agent_run_db,
    app_without_fake_event_store,
):
    chat_updates = []

    async def fake_upsert_message(chat_id, message_id, message):
        chat_updates.append(
            {
                'chat_id': chat_id,
                'message_id': message_id,
                'message': message,
            }
        )
        return message

    monkeypatch.setattr(
        agent_service_router,
        'Chats',
        SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=fake_upsert_message),
        raising=False,
    )
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
    await AgentRuns.transition_state(
        run.id,
        from_states=['running'],
        to_state='finalizing',
        reason='runtime closed work',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='final.started',
        participant_id='leader',
        phase='finalizing',
        summary='Final answer phase',
    )

    with TestClient(app_without_fake_event_store) as client:
        delta = client.post(
            f'/api/agent/service/runs/{run.id}/final-delta',
            json={
                'run_id': run.id,
                'final_stream_id': 'answer',
                'delta_index': 0,
                'delta': 'hello from agent mode',
                'idempotency_key': f'final:{run.id}:answer:0',
            },
            headers=_service_headers(f'final:{run.id}:answer:0'),
        )
        complete = client.post(
            f'/api/agent/service/runs/{run.id}/state-transition',
            json={
                'run_id': run.id,
                'from_states': ['finalizing'],
                'to_state': 'completed',
                'reason': 'runtime final answer completed',
                'payload': {},
                'idempotency_key': f'state:{run.id}:completed',
            },
            headers=_service_headers(f'state:{run.id}:completed'),
        )

    assert delta.status_code == 200
    assert complete.status_code == 200
    assert complete.json()['state'] == 'completed'

    updated = await AgentRuns.get_run(run.id)
    assert updated is not None
    assert updated.state == 'completed'
    assert updated.final_text == 'hello from agent mode'
    assert chat_updates == [
        {
            'chat_id': 'chat-1',
            'message_id': 'msg-assistant',
            'message': {
                'agent_run_id': run.id,
                'content': 'hello from agent mode',
                'done': True,
            },
        }
    ]


@pytest.mark.asyncio
async def test_agent_service_callbacks_require_service_credential(
    agent_run_db,
    app_without_fake_event_store,
):
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )

    with TestClient(app_without_fake_event_store) as client:
        event_response = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json={
                'run_id': run.id,
                'event_type': 'run.running',
                'idempotency_key': 'evt:runtime:event-1',
            },
            headers={'X-Agent-Idempotency-Key': 'evt:runtime:event-1'},
        )
        tool_response = client.post(
            f'/api/agent/service/runs/{run.id}/tool-call',
            json={
                'run_id': run.id,
                'participant_id': 'leader',
                'tool_call_id': 'call-1',
                'tool_id': 'tool:builtin:read_file:read_file',
                'arguments': {},
                'idempotency_key': 'tool:leader:call-1:1',
            },
            headers={'X-Agent-Idempotency-Key': 'tool:leader:call-1:1'},
        )

    assert event_response.status_code == 401
    assert event_response.json()['detail'] == 'service token required'
    assert tool_response.status_code == 401
    assert tool_response.json()['detail'] == 'service token required'


@pytest.mark.asyncio
async def test_agent_service_tool_callback_rebuilds_missing_registry_from_run_snapshot(
    monkeypatch,
    agent_run_db,
    app_without_fake_event_store,
):
    calls = []

    async def write_note(title: str, content: str):
        calls.append((title, content))
        return {'title': title, 'content': content}

    async def fake_get_builtin_tools(request, extra_params, features=None, model=None):
        assert extra_params['__metadata__']['chat_id'] == 'chat-1'
        return {
            'write_note': {
                'tool_id': 'builtin:write_note',
                'callable': write_note,
                'spec': {'name': 'write_note', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            }
        }

    async def no_approval(tool_request, tool, resume):
        return None

    monkeypatch.setattr(agent_service, 'get_builtin_tools', fake_get_builtin_tools)
    monkeypatch.setattr(
        agent_service.Users,
        'get_user_by_id',
        lambda user_id: SimpleNamespace(
            id=user_id,
            model_dump=lambda mode=None: {'id': user_id, 'role': 'admin', 'name': 'Test User'},
        ),
    )

    app_without_fake_event_store.state.AGENT_TOOL_REGISTRIES = {}
    app_without_fake_event_store.state.AGENT_APPROVAL_COORDINATOR = SimpleNamespace(request_tool_approval=no_approval)
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
        tool_access_snapshot={
            'tools': [
                {
                    'id': 'tool:builtin:write_note:write_note',
                    'name': 'write_note',
                    'type': 'builtin',
                    'schema': {'name': 'write_note', 'parameters': {'type': 'object'}},
                }
            ]
        },
    )

    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/service/runs/{run.id}/tool-call',
            json={
                'run_id': run.id,
                'participant_id': 'leader',
                'tool_call_id': 'call-1',
                'tool_id': 'tool:builtin:write_note:write_note',
                'arguments': {'title': 'Plan', 'content': 'Ship it'},
                'idempotency_key': 'tool:leader:call-1:1',
            },
            headers=_service_headers('tool:leader:call-1:1'),
        )

    assert response.status_code == 200
    assert response.json()['status'] == 'success'
    assert calls == [('Plan', 'Ship it')]
    assert 'tool:builtin:write_note:write_note' in app_without_fake_event_store.state.AGENT_TOOL_REGISTRIES[run.id]


@pytest.mark.asyncio
async def test_agent_service_tool_callback_uses_run_user_id_for_terminal_artifacts(
    agent_run_db,
    app_without_fake_event_store,
):
    async def run_command(command: str):
        return {
            'process_id': 'proc-report',
            'command': command,
            'status': 'completed',
            'exit_code': 0,
            'log_path': '/workspace/logs/proc-report.jsonl',
            'next_offset': 12,
        }

    async def no_approval(tool_request, tool, resume):
        return None

    _envelope, registry = build_tool_access_envelope(
        {
            'run_command': {
                'tool_id': 'terminal:main',
                'callable': run_command,
                'spec': {'name': 'run_command', 'parameters': {'type': 'object'}},
                'type': 'terminal',
            }
        }
    )
    app_without_fake_event_store.state.AGENT_TOOL_REGISTRY = registry
    app_without_fake_event_store.state.AGENT_RUN_ARTIFACT_REGISTRAR = AgentRunArtifactRegistrar(AgentRuns)
    app_without_fake_event_store.state.AGENT_APPROVAL_COORDINATOR = SimpleNamespace(request_tool_approval=no_approval)

    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    output_path = f'/workspace/agent-runs/{run.id}/outputs/final_report.md'

    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/service/runs/{run.id}/tool-call',
            json={
                'run_id': run.id,
                'participant_id': 'leader',
                'tool_call_id': 'call-1',
                'tool_id': 'tool:terminal:main:run_command',
                'arguments': {'command': f'python write_report.py > {output_path}'},
                'idempotency_key': 'tool:leader:call-1:1',
            },
            headers=_service_headers('tool:leader:call-1:1'),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'success'
    assert payload['artifacts'][0]['path'] == output_path

    async with agent_run_db() as session:
        operation = (
            await session.execute(
                select(AgentRunOperation).filter_by(
                    run_id=run.id,
                    operation_type='tool.call',
                    idempotency_key='tool:leader:call-1:1',
                )
            )
        ).scalars().one()
        artifact = (await session.execute(select(AgentArtifact).filter_by(run_id=run.id))).scalars().one()
        events = (
            await session.execute(select(AgentRunEvent).filter_by(run_id=run.id).order_by(AgentRunEvent.seq))
        ).scalars().all()

    assert operation.response['artifacts'][0]['path'] == output_path
    assert artifact.user_id == 'user-1'
    assert artifact.path == output_path
    assert events[-1].event_type == 'artifact.registered'
    assert events[-1].participant_id == 'leader'
    assert events[-1].payload['artifacts'][0]['path'] == output_path
