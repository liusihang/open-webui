import asyncio
import os
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.agent.approval import AgentApprovalCoordinator
from open_webui.agent.artifacts import AgentRunArtifactRegistrar
from open_webui.agent.tool_authority import (
    AgentToolAuthority,
    ToolCallRequest,
    build_tool_access_envelope,
)
from open_webui.internal.db import Base
from open_webui.models import agent_runs as agent_run_models
from open_webui.models.agent_runs import (
    AgentArtifact,
    AgentRun,
    AgentRunDecisionExecution,
    AgentRunEvent,
    AgentRunOperation,
    AgentRuns,
    AgentRunStateError,
)
from open_webui.routers import agent_runs, agent_service
from open_webui.routers import agent_service as agent_service_router
from open_webui.utils.auth import get_verified_user
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _service_headers(idempotency_key: str) -> dict[str, str]:
    return {
        'Authorization': 'Bearer service-token',
        'X-Agent-Idempotency-Key': idempotency_key,
    }


@asynccontextmanager
async def _serve_over_tcp(app):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', 0))
    sock.listen(2048)
    host, port = sock.getsockname()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            lifespan='off',
            log_level='warning',
            timeout_graceful_shutdown=1,
            timeout_keep_alive=1,
        )
    )
    server.install_signal_handlers = lambda: None
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            if server_task.done():
                await server_task
            await asyncio.sleep(0.01)
        assert server.started
        yield f'http://{host}:{port}'
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)


@pytest.mark.asyncio
async def test_tool_callback_rejects_nan_before_sqlite_operation_insert(
    agent_run_db,
    app_without_fake_event_store,
    monkeypatch,
):
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    authority = AgentToolAuthority(operation_store=AgentRuns, registry={})

    async def get_authority(_request, *, run_id):
        return authority

    monkeypatch.setattr(agent_service, 'get_agent_tool_authority', get_authority)
    body = (
        '{"run_id":"'
        + run.id
        + '","participant_id":"leader","tool_call_id":"call-nan",'
        '"tool_id":"tool:missing","arguments":{"value":NaN},'
        '"idempotency_key":"tool:leader:call-nan:1"}'
    )

    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/service/runs/{run.id}/tool-call',
            content=body,
            headers={
                **_service_headers('tool:leader:call-nan:1'),
                'Content-Type': 'application/json',
            },
        )

    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'invalid_tool_payload'
    async with agent_run_db() as session:
        operations = (
            await session.execute(
                select(AgentRunOperation).filter_by(
                    run_id=run.id,
                    operation_type='tool.call',
                )
            )
        ).scalars().all()
    assert operations == []


@pytest.mark.asyncio
async def test_tool_operation_success_appends_one_terminal_event_in_sse_order(
    agent_run_db,
    app_without_fake_event_store,
):
    calls = []

    async def read_file(path: str):
        calls.append(path)
        return {'content': 'hello'}

    _envelope, registry = build_tool_access_envelope(
        {
            'read_file': {
                'tool_id': 'builtin:read_file',
                'callable': read_file,
                'spec': {'name': 'read_file', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            }
        }
    )
    app_without_fake_event_store.state.AGENT_TOOL_REGISTRY = registry
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='tool.requested',
        participant_id='leader',
        phase='running',
        payload={
            'tool_call_id': 'call-read-1',
            'tool_id': 'tool:builtin:read_file:read_file',
        },
    )
    body = {
        'run_id': run.id,
        'participant_id': 'leader',
        'tool_call_id': 'call-read-1',
        'tool_id': 'tool:builtin:read_file:read_file',
        'arguments': {'path': '/workspace/report.txt'},
        'checkpoint_version': 4,
        'idempotency_key': 'tool:leader:call-read-1:1',
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run.id}/tool-call',
            json=body,
            headers=_service_headers('tool:leader:call-read-1:1'),
        )
        replay = client.post(
            f'/api/agent/service/runs/{run.id}/tool-call',
            json=body,
            headers=_service_headers('tool:leader:call-read-1:1'),
        )

    assert first.status_code == replay.status_code == 200
    assert calls == ['/workspace/report.txt']
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == [
        'run.running',
        'tool.requested',
        'tool.completed',
    ]
    assert events[-1].payload['tool_call_id'] == 'call-read-1'
    assert events[-1].payload['status'] == 'success'


@pytest.mark.asyncio
async def test_approved_tool_replay_appends_one_terminal_event(
    agent_run_db,
    app_without_fake_event_store,
):
    calls = []
    async def write_file(path: str, content: str):
        calls.append((path, content))
        return {'written': path}

    _envelope, registry = build_tool_access_envelope(
        {
            'write_file': {
                'tool_id': 'terminal:main',
                'callable': write_file,
                'spec': {'name': 'write_file', 'parameters': {'type': 'object'}},
                'type': 'terminal',
            }
        }
    )
    app_without_fake_event_store.state.AGENT_TOOL_REGISTRY = registry
    app_without_fake_event_store.state.AGENT_APPROVAL_COORDINATOR = (
        AgentApprovalCoordinator(AgentRuns)
    )
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        payload={'runtime_session_id': 'runtime-session-1'},
    )
    await AgentRuns.append_event(
        run.id,
        event_type='tool.requested',
        participant_id='leader',
        phase='running',
        payload={'tool_call_id': 'call-write-1'},
    )
    await AgentRuns.append_event(
        run.id,
        event_type='approval.requested',
        participant_id='leader',
        phase='waiting_approval',
        payload={
            'approval_id': f'approval:{run.id}:call-write-1',
            'tool_call_id': 'call-write-1',
            'tool_id': 'tool:terminal:main:write_file',
            'tool_arguments_fingerprint': agent_run_models._decision_payload_fingerprint(
                {'path': '/workspace/report.txt', 'content': 'replacement'}
            ),
            'tool_call_idempotency_key': 'tool:leader:call-write-1:1',
            'checkpoint_version': 5,
        },
    )
    recorded = await AgentRuns.record_decision_execution(
        run.id,
        resource_type='approval',
        resource_id=f'approval:{run.id}:call-write-1',
        decision='approved',
        payload={},
        operation_type='approval.result',
        idempotency_key='approval:call-write-1:approved',
        request_hash='approval:call-write-1:approved',
    )
    claim = await AgentRuns.claim_decision_execution(
        recorded.execution.id,
        worker_id='worker-1',
        lease_seconds=30,
    )
    assert claim is not None
    prepared = {
        'execution_id': recorded.execution.id,
        'run_id': run.id,
        'runtime_session_id': 'runtime-session-1',
        'subject_id': f'approval:{run.id}:call-write-1',
        'command_type': 'resume_approval',
        'state': 'prepared',
        'fingerprint': recorded.execution.fingerprint,
        'checkpoint_version': 5,
    }
    await AgentRuns.mark_decision_execution_prepared(
        recorded.execution.id,
        prepared,
        claim_token=claim.execution.claim_token,
    )
    await AgentRuns.commit_prepared_decision_execution(
        recorded.execution.id,
        claim_token=claim.execution.claim_token,
    )
    await AgentRuns.begin_decision_activation(
        recorded.execution.id,
        claim_token=claim.execution.claim_token,
    )
    body = {
        'run_id': run.id,
        'participant_id': 'leader',
        'tool_call_id': 'call-write-1',
        'tool_id': 'tool:terminal:main:write_file',
        'arguments': {'path': '/workspace/report.txt', 'content': 'replacement'},
        'checkpoint_version': 5,
        'idempotency_key': 'tool:leader:call-write-1:1',
    }
    headers = {
        **_service_headers('tool:leader:call-write-1:1'),
        'X-Agent-Decision-Execution-ID': recorded.execution.id,
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run.id}/tool-call',
            json=body,
            headers=headers,
        )
        replay = client.post(
            f'/api/agent/service/runs/{run.id}/tool-call',
            json=body,
            headers=headers,
        )

    assert first.status_code == replay.status_code == 200
    assert calls == [('/workspace/report.txt', 'replacement')]
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == [
        'run.running',
        'tool.requested',
        'approval.requested',
        'approval.completed',
        'tool.completed',
    ]


@pytest.mark.asyncio
async def test_tool_operation_error_appends_tool_failed(
    agent_run_db,
    app_without_fake_event_store,
):
    async def failing_tool():
        raise RuntimeError('tool exploded')

    _envelope, registry = build_tool_access_envelope(
        {
            'failing_tool': {
                'tool_id': 'builtin:failing_tool',
                'callable': failing_tool,
                'spec': {'name': 'failing_tool', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            }
        }
    )
    app_without_fake_event_store.state.AGENT_TOOL_REGISTRY = registry
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='tool.requested',
        participant_id='leader',
        phase='running',
        payload={'tool_call_id': 'call-fail-1'},
    )

    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/service/runs/{run.id}/tool-call',
            json={
                'run_id': run.id,
                'participant_id': 'leader',
                'tool_call_id': 'call-fail-1',
                'tool_id': 'tool:builtin:failing_tool:failing_tool',
                'arguments': {},
                'checkpoint_version': 2,
                'idempotency_key': 'tool:leader:call-fail-1:1',
            },
            headers=_service_headers('tool:leader:call-fail-1:1'),
        )

    assert response.status_code == 200
    assert response.json()['status'] == 'error'
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == [
        'run.running',
        'tool.requested',
        'tool.failed',
    ]
    assert events[-1].payload['structured_error']['code'] == 'tool_execution_error'


@pytest_asyncio.fixture
async def agent_run_db(monkeypatch, tmp_path):
    engine = create_async_engine(
        f'sqlite+aiosqlite:///{tmp_path / "agent-runs.sqlite3"}'
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[
                    AgentRun.__table__,
                    AgentRunEvent.__table__,
                    AgentArtifact.__table__,
                    AgentRunOperation.__table__,
                    AgentRunDecisionExecution.__table__,
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

    class FakeRequest:
        app = app_without_fake_event_store

        async def is_disconnected(self):
            return False

    stream = await agent_runs.stream_agent_run_events(
        run.id,
        FakeRequest(),
        after_seq=0,
        last_event_id=None,
        user=SimpleNamespace(id='user-1'),
    )
    iterator = stream.body_iterator
    stream_text = await asyncio.wait_for(anext(iterator), timeout=1)
    await iterator.aclose()

    assert detail.status_code == 200
    assert detail.json()['state'] == 'running'
    assert detail.json()['state_version'] == 1
    assert events.status_code == 200
    assert events.json()['last_seq'] == 1
    assert events.json()['events'][0]['event_type'] == 'run.running'
    assert stream.status_code == 200
    assert 'event: run.running' in stream_text


@pytest.mark.asyncio
async def test_agent_run_events_stream_tails_new_events_until_terminal(
    monkeypatch,
    agent_run_db,
    app_without_fake_event_store,
):
    monkeypatch.setattr(agent_runs, 'AGENT_RUN_EVENTS_POLL_SECONDS', 0.01, raising=False)
    monkeypatch.setattr(agent_runs, 'AGENT_RUN_EVENTS_HEARTBEAT_SECONDS', 60.0, raising=False)
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

    class FakeRequest:
        app = app_without_fake_event_store

        async def is_disconnected(self):
            return False

    response = await agent_runs.stream_agent_run_events(
        run.id,
        FakeRequest(),
        after_seq=1,
        last_event_id=None,
        user=SimpleNamespace(id='user-1'),
    )
    iterator = response.body_iterator
    next_chunk = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)

    await AgentRuns.append_event(
        run.id,
        event_type='action.summary',
        participant_id='leader',
        phase='running',
        summary='Still working',
    )

    assert 'event: action.summary' in await asyncio.wait_for(next_chunk, timeout=1)

    terminal_chunk = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)
    await AgentRuns.transition_state(
        run.id,
        from_states=['running'],
        to_state='finalizing',
        reason='runtime closed work',
    )
    await AgentRuns.transition_state(
        run.id,
        from_states=['finalizing'],
        to_state='completed',
        reason='runtime final answer completed',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='run.completed',
        participant_id='leader',
        phase='completed',
        summary='Agent run completed.',
    )

    assert 'event: run.completed' in await asyncio.wait_for(terminal_chunk, timeout=1)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(iterator), timeout=1)


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
async def test_repeated_cancel_retries_failed_runtime_notification(
    monkeypatch,
    agent_run_db,
    app_without_fake_event_store,
):
    from open_webui.agent.runtime_client import AgentRuntimeUnavailable

    runtime_attempts = []

    class RuntimeClient:
        def __init__(self, base_url, service_token=None, timeout=None):
            pass

        async def cancel_run(self, run_id):
            runtime_attempts.append(run_id)
            if len(runtime_attempts) == 1:
                raise AgentRuntimeUnavailable('runtime temporarily unavailable')
            return {'run_id': run_id, 'state': 'cancelled'}

    monkeypatch.setattr(agent_runs, 'AgentRuntimeClient', RuntimeClient)
    app_without_fake_event_store.state.config.AGENT_RUNTIME_BASE_URL = (
        'http://agent-runtime.test'
    )
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(f'/api/agent/runs/{run.id}/cancel')
        second = client.post(f'/api/agent/runs/{run.id}/cancel')

    assert first.status_code == second.status_code == 200
    assert runtime_attempts == [run.id, run.id]
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == ['run.cancelled']


@pytest.mark.asyncio
async def test_network_cancel_interrupts_only_target_run_tool_and_cleans_registry(
    monkeypatch,
    agent_run_db,
    app_without_fake_event_store,
):
    started = {'target': asyncio.Event(), 'other': asyncio.Event()}
    released = {'target': asyncio.Event(), 'other': asyncio.Event()}
    cancelled = {'target': asyncio.Event(), 'other': asyncio.Event()}
    runtime_cancels = []

    async def blocking_tool(label: str):
        started[label].set()
        try:
            await released[label].wait()
        except asyncio.CancelledError:
            cancelled[label].set()
            raise
        return {'label': label}

    class RuntimeClient:
        def __init__(self, base_url, service_token=None, timeout=None):
            pass

        async def cancel_run(self, run_id):
            runtime_cancels.append(run_id)
            return {'run_id': run_id, 'state': 'cancelled'}

    monkeypatch.setattr(agent_runs, 'AgentRuntimeClient', RuntimeClient)
    _envelope, registry = build_tool_access_envelope(
        {
            'blocking_tool': {
                'tool_id': 'builtin:blocking_tool',
                'callable': blocking_tool,
                'spec': {
                    'name': 'blocking_tool',
                    'parameters': {'type': 'object'},
                },
                'type': 'builtin',
            }
        }
    )
    app_without_fake_event_store.state.AGENT_TOOL_REGISTRY = registry

    runs = {}
    for label in ('target', 'other'):
        run = await AgentRuns.create_run(
            user_id='user-1',
            chat_id=f'chat-{label}',
            user_message_id=f'msg-user-{label}',
            assistant_message_id=f'msg-assistant-{label}',
            leader_model_id='model-a',
        )
        await AgentRuns.append_event(
            run.id,
            event_type='run.running',
            participant_id='leader',
            phase='running',
        )
        await AgentRuns.append_event(
            run.id,
            event_type='tool.requested',
            participant_id='leader',
            phase='running',
            payload={
                'tool_call_id': f'call-{label}',
                'tool_id': 'tool:builtin:blocking_tool:blocking_tool',
            },
        )
        runs[label] = run

    async with _serve_over_tcp(app_without_fake_event_store) as base_url:
        async with httpx.AsyncClient(base_url=base_url, timeout=2) as client:
            tool_tasks = {
                label: asyncio.create_task(
                    client.post(
                        f'/api/agent/service/runs/{run.id}/tool-call',
                        json={
                            'run_id': run.id,
                            'participant_id': 'leader',
                            'tool_call_id': f'call-{label}',
                            'tool_id': 'tool:builtin:blocking_tool:blocking_tool',
                            'arguments': {'label': label},
                            'checkpoint_version': 1,
                            'idempotency_key': f'tool:leader:call-{label}:1',
                        },
                        headers=_service_headers(f'tool:leader:call-{label}:1'),
                    )
                )
                for label, run in runs.items()
            }
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started.values())),
                timeout=2,
            )
            try:
                first_cancel = await client.post(
                    f'/api/agent/runs/{runs["target"].id}/cancel'
                )
                assert first_cancel.status_code == 200
                await asyncio.wait_for(cancelled['target'].wait(), timeout=1)
                assert not cancelled['other'].is_set()
                assert not tool_tasks['other'].done()

                released['other'].set()
                other_response = await tool_tasks['other']
                assert other_response.status_code == 200
                assert other_response.json()['status'] == 'success'
                await asyncio.gather(tool_tasks['target'], return_exceptions=True)

                second_cancel = await client.post(
                    f'/api/agent/runs/{runs["target"].id}/cancel'
                )
                assert second_cancel.status_code == 200
            finally:
                released['target'].set()
                released['other'].set()
                await asyncio.gather(*tool_tasks.values(), return_exceptions=True)

    target_operation = await AgentRuns.find_operation_by_idempotency_key(
        runs['target'].id,
        operation_type='tool.call',
        idempotency_key='tool:leader:call-target:1',
    )
    assert target_operation is not None
    assert target_operation.status == 'failed'
    assert target_operation.error['code'] == 'tool_cancelled'
    target_events = await AgentRuns.list_events(runs['target'].id)
    assert [event.event_type for event in target_events] == [
        'run.running',
        'tool.requested',
        'tool.failed',
        'run.cancelled',
    ]
    assert target_events[-2].payload['status'] == 'cancelled'
    other_events = await AgentRuns.list_events(runs['other'].id)
    assert [event.event_type for event in other_events] == [
        'run.running',
        'tool.requested',
        'tool.completed',
    ]
    execution_registry = app_without_fake_event_store.state.AGENT_TOOL_EXECUTION_REGISTRY
    assert execution_registry.active_count(runs['target'].id) == 0
    assert execution_registry.active_count(runs['other'].id) == 0
    assert runtime_cancels == [runs['target'].id, runs['target'].id]


@pytest.mark.asyncio
async def test_agent_run_cancel_rolls_back_state_when_cancel_event_insert_fails(
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
    running = await AgentRuns.transition_state(
        run.id,
        from_states=['queued'],
        to_state='running',
        reason='runtime accepted',
    )
    async with agent_run_db() as session:
        await session.execute(
            text(
                """
                CREATE TRIGGER reject_agent_run_cancel_event
                BEFORE INSERT ON agent_run_event
                WHEN NEW.event_type = 'run.cancelled'
                BEGIN
                    SELECT RAISE(ABORT, 'forced cancel event insert failure');
                END
                """
            )
        )
        await session.commit()

    with pytest.raises(IntegrityError, match='forced cancel event insert failure'):
        await agent_runs.cancel_agent_run(
            SimpleNamespace(app=app_without_fake_event_store),
            run.id,
            user=SimpleNamespace(id='user-1'),
        )

    updated = await AgentRuns.get_run(run.id)
    events = await AgentRuns.list_events(run.id)
    assert updated is not None
    assert updated.state == 'running'
    assert updated.state_version == running.state_version
    assert updated.ended_at is None
    assert events == []


@pytest.mark.asyncio
async def test_concurrent_agent_run_cancel_persists_once_and_notifies_idempotently(
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
            runtime_cancels.append(run_id)
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

    original_cancel = AgentRuns.cancel_run_with_decision_executions
    append_entries = 0
    both_entered_append = asyncio.Event()
    append_lock = asyncio.Lock()

    async def synchronized_cancel(*args, **kwargs):
        nonlocal append_entries
        append_entries += 1
        if append_entries == 2:
            both_entered_append.set()
        await both_entered_append.wait()
        async with append_lock:
            return await original_cancel(*args, **kwargs)

    monkeypatch.setattr(
        AgentRuns,
        'cancel_run_with_decision_executions',
        synchronized_cancel,
    )
    request = SimpleNamespace(app=app_without_fake_event_store)
    user = SimpleNamespace(id='user-1')
    await asyncio.gather(
        agent_runs.cancel_agent_run(request, run.id, user=user),
        agent_runs.cancel_agent_run(request, run.id, user=user),
    )

    updated = await AgentRuns.get_run(run.id)
    assert updated is not None
    assert updated.state == 'cancelled'
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == ['run.cancelled']
    assert runtime_cancels == [run.id, run.id]


@pytest.mark.asyncio
async def test_agent_run_public_approval_decision_records_user_authorized_decision(
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
    await AgentRuns.attach_runtime_session(run.id, 'runtime-session-1')
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        payload={'runtime_session_id': 'runtime-session-1'},
    )
    coordinator = AgentApprovalCoordinator(AgentRuns)
    app_without_fake_event_store.state.AGENT_APPROVAL_COORDINATOR = coordinator
    approval = await coordinator.request_tool_approval(
        ToolCallRequest(
            run_id=run.id,
            participant_id='leader',
            tool_call_id='call-approval-1',
            tool_id='tool:terminal:main:write_file',
            arguments={'path': '/workspace/report.txt', 'content': 'replacement'},
            checkpoint_version=7,
            idempotency_key='tool:leader:call-approval-1:1',
        ),
        {
            'name': 'write_file',
            'tool_id': 'terminal:main',
            'type': 'terminal',
        },
    )
    assert approval is not None

    approval_id = f'approval:{run.id}:call-approval-1'
    idempotency_key = f'approval:{run.id}:call-approval-1:approved'
    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/runs/{run.id}/approvals/{approval_id}/decision',
            json={
                'run_id': run.id,
                'approval_id': approval_id,
                'decision': 'approved',
                'idempotency_key': idempotency_key,
            },
            headers={'X-Agent-Idempotency-Key': idempotency_key},
        )

    assert response.status_code == 202
    assert response.json()['status'] == 'approval_recorded'
    assert response.json()['execution_status'] == 'pending'
    assert response.json()['execution_id']

    updated = await AgentRuns.get_run(run.id)
    assert updated is not None
    assert updated.state == 'waiting_approval'
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == ['run.running', 'approval.requested']


@pytest.mark.asyncio
async def test_agent_run_public_user_input_records_outbox_without_completion_event(
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
    await AgentRuns.attach_runtime_session(run.id, 'runtime-session-1')
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        payload={'runtime_session_id': 'runtime-session-1'},
    )
    user_input_id = f'user-input:{run.id}:call-input-1'
    await AgentRuns.append_event(
        run.id,
        event_type='user_input.requested',
        participant_id='leader',
        phase='waiting_user_input',
        payload={
            'user_input_id': user_input_id,
            'tool_call_id': 'call-input-1',
            'checkpoint_version': 3,
            'allow_cancel': True,
        },
    )
    key = f'user-input:{user_input_id}:accepted'

    with TestClient(app_without_fake_event_store) as client:
        response = client.post(
            f'/api/agent/runs/{run.id}/user-input/{user_input_id}',
            json={
                'run_id': run.id,
                'user_input_id': user_input_id,
                'status': 'accepted',
                'content': {'answer': 'A'},
                'idempotency_key': key,
            },
            headers={'X-Agent-Idempotency-Key': key},
        )

    assert response.status_code == 202
    assert response.json()['execution_status'] == 'pending'
    updated = await AgentRuns.get_run(run.id)
    assert updated.state == 'waiting_user_input'
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == [
        'run.running',
        'user_input.requested',
    ]


@pytest.mark.asyncio
async def test_agent_run_public_user_input_maps_idempotency_conflict_to_409(
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
    await AgentRuns.attach_runtime_session(run.id, 'runtime-session-1')
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        summary='Runtime accepted',
    )
    user_input_id = f'user-input:{run.id}:call-input-1'
    await AgentRuns.append_event(
        run.id,
        event_type='user_input.requested',
        participant_id='leader',
        phase='waiting_user_input',
        summary='Needs your input',
        payload={
            'user_input_id': user_input_id,
            'tool_call_id': 'call-input-1',
            'checkpoint_version': 7,
            'allow_cancel': True,
        },
    )
    key = f'user-input:{user_input_id}:accepted'

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/runs/{run.id}/user-input/{user_input_id}',
            json={
                'run_id': run.id,
                'user_input_id': user_input_id,
                'status': 'accepted',
                'content': {'answer': 'A'},
                'idempotency_key': key,
            },
            headers={'X-Agent-Idempotency-Key': key},
        )
        conflict = client.post(
            f'/api/agent/runs/{run.id}/user-input/{user_input_id}',
            json={
                'run_id': run.id,
                'user_input_id': user_input_id,
                'status': 'accepted',
                'content': {'answer': 'B'},
                'idempotency_key': key,
            },
            headers={'X-Agent-Idempotency-Key': key},
        )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()['detail'] == 'idempotency_conflict'


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
@pytest.mark.parametrize(
    ('initial_state', 'event_type', 'expected_state', 'event_payload'),
    [
        ('queued', 'run.running', 'running', {}),
        (
            'running',
            'approval.requested',
            'waiting_approval',
            {'approval_id': 'approval-1'},
        ),
        (
            'running',
            'user_input.requested',
            'waiting_user_input',
            {'user_input_id': 'input-1'},
        ),
        ('running', 'final.started', 'finalizing', {}),
        ('running', 'run.failed', 'failed', {'error': {'code': 'test_failure'}}),
        ('running', 'run.cancelled', 'cancelled', {}),
        ('finalizing', 'run.completed', 'completed', {}),
    ],
)
async def test_agent_run_lifecycle_event_append_commits_corresponding_state(
    agent_run_db,
    initial_state,
    event_type,
    expected_state,
    event_payload,
):
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    if initial_state == 'queued':
        current = run
    else:
        running = await AgentRuns.transition_state(
            run.id,
            from_states=['queued'],
            to_state='running',
            reason='runtime accepted',
        )
        current = running
    if initial_state not in {'queued', 'running'}:
        current = await AgentRuns.transition_state(
            run.id,
            from_states=['running'],
            to_state=initial_state,
            reason=f'prepare {initial_state}',
        )

    event = await AgentRuns.append_event(
        run.id,
        event_type=event_type,
        participant_id='leader',
        phase=expected_state,
        summary=f'{event_type} persisted',
        payload=event_payload,
    )

    updated = await AgentRuns.get_run(run.id)
    assert event.event_type == event_type
    assert updated is not None
    assert updated.state == expected_state
    assert updated.state_version == current.state_version + 1
    if expected_state in {'failed', 'cancelled', 'completed'}:
        assert updated.ended_at is not None
        assert updated.summary is not None
        assert updated.summary['audit']['last_seq'] == event.seq


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('events', 'expected_state'),
    [
        (
            [
                ('final.started', {'runtime_session_id': 'runtime-1'}),
                ('run.completed', {'runtime_session_id': 'runtime-1'}),
            ],
            'completed',
        ),
    ],
)
async def test_canonical_lifecycle_replay_does_not_reapply_old_transition(
    agent_run_db,
    events,
    expected_state,
):
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        summary='Runtime accepted',
    )
    first_event = None
    for event_type, payload in events:
        stored = await AgentRuns.append_event(
            run.id,
            event_type=event_type,
            participant_id='leader',
            phase=expected_state,
            summary=f'{event_type} persisted',
            payload=payload,
        )
        if first_event is None:
            first_event = stored

    before_replay = await AgentRuns.get_run(run.id)
    assert before_replay is not None
    assert before_replay.state == expected_state

    replayed = await AgentRuns.append_event(
        run.id,
        event_type=events[0][0],
        participant_id='leader',
        phase='stale-retry',
        summary='Late retry of an older lifecycle event',
        payload=events[0][1],
    )

    after_replay = await AgentRuns.get_run(run.id)
    assert first_event is not None
    assert replayed.seq == first_event.seq
    assert after_replay is not None
    assert after_replay.state == expected_state
    assert after_replay.state_version == before_replay.state_version


@pytest.mark.asyncio
async def test_agent_run_lifecycle_event_rolls_back_state_when_event_insert_fails(
    agent_run_db,
):
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )
    running = await AgentRuns.transition_state(
        run.id,
        from_states=['queued'],
        to_state='running',
        reason='runtime accepted',
    )
    async with agent_run_db() as session:
        await session.execute(
            text(
                """
                CREATE TRIGGER reject_agent_run_events
                BEFORE INSERT ON agent_run_event
                BEGIN
                    SELECT RAISE(ABORT, 'forced agent event insert failure');
                END
                """
            )
        )
        await session.commit()

    with pytest.raises(IntegrityError, match='forced agent event insert failure'):
        await AgentRuns.append_event(
            run.id,
            event_type='run.failed',
            participant_id='leader',
            phase='failed',
            payload={'error': {'code': 'forced_failure'}},
        )

    updated = await AgentRuns.get_run(run.id)
    events = await AgentRuns.list_events(run.id)
    assert updated is not None
    assert updated.state == 'running'
    assert updated.state_version == running.state_version
    assert updated.ended_at is None
    assert events == []


@pytest.mark.asyncio
async def test_agent_run_lifecycle_event_rejection_rolls_back_shared_session(
    agent_run_db,
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
        reason='runtime closing',
    )
    completed = await AgentRuns.transition_state(
        run.id,
        from_states=['finalizing'],
        to_state='completed',
        reason='runtime completed',
    )

    async with agent_run_db() as session:
        with pytest.raises(AgentRunStateError, match='run.failed event persistence'):
            await AgentRuns.append_event(
                run.id,
                event_type='run.failed',
                participant_id='leader',
                phase='failed',
                db=session,
            )
        await session.commit()

    updated = await AgentRuns.get_run(run.id)
    events = await AgentRuns.list_events(run.id)
    assert updated is not None
    assert updated.state == 'completed'
    assert updated.state_version == completed.state_version
    assert events == []


@pytest.mark.asyncio
async def test_agent_service_duplicate_terminal_event_with_new_key_returns_canonical_event(
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
    error = {
        'code': 'runtime_failed',
        'message': 'Runtime callback failed.',
        'summary': 'Agent run failed.',
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json={
                'run_id': run.id,
                'event_type': 'run.failed',
                'phase': 'failed',
                'summary': 'Agent run failed.',
                'payload': {'error': error},
                'idempotency_key': 'evt:runtime:failed:first',
            },
            headers=_service_headers('evt:runtime:failed:first'),
        )
        duplicate = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json={
                'run_id': run.id,
                'event_type': 'run.failed',
                'phase': 'failed',
                'summary': 'Agent run failed.',
                'payload': {'error': error},
                'idempotency_key': 'evt:runtime:failed:second',
            },
            headers=_service_headers('evt:runtime:failed:second'),
        )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    updated = await AgentRuns.get_run(run.id)
    events = await AgentRuns.list_events(run.id)
    assert updated is not None
    assert updated.state == 'failed'
    assert updated.error == error
    assert updated.summary is not None
    assert updated.summary['audit']['last_seq'] == first.json()['seq']
    assert [event.event_type for event in events] == ['run.failed']


@pytest.mark.asyncio
async def test_agent_service_lifecycle_event_recovers_committed_event_operation_crash(
    monkeypatch,
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
    key = 'evt:runtime:failed:crash-window'
    body = {
        'run_id': run.id,
        'event_type': 'run.failed',
        'phase': 'failed',
        'summary': 'Agent run failed.',
        'payload': {'error': {'code': 'runtime_failed'}},
        'idempotency_key': key,
    }
    original_finish = AgentRuns.finish_operation_success

    async def crash_after_event_commit(*args, **kwargs):
        raise RuntimeError('simulated crash after lifecycle event commit')

    monkeypatch.setattr(AgentRuns, 'finish_operation_success', crash_after_event_commit)
    with TestClient(app_without_fake_event_store, raise_server_exceptions=False) as client:
        first = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json=body,
            headers=_service_headers(key),
        )

    monkeypatch.setattr(AgentRuns, 'finish_operation_success', original_finish)
    with TestClient(app_without_fake_event_store) as client:
        retry = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json=body,
            headers=_service_headers(key),
        )

    assert first.status_code in {200, 500}
    assert retry.status_code == 200
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == ['run.failed']
    async with agent_run_db() as session:
        operation = (
            await session.execute(
                select(AgentRunOperation).filter_by(
                    run_id=run.id,
                    operation_type='event.append',
                    idempotency_key=key,
                )
            )
        ).scalars().one()
    assert operation.status == 'succeeded'
    assert operation.response == retry.json()


@pytest.mark.asyncio
async def test_agent_service_event_callback_retries_are_idempotent_and_conflicting_bodies_return_existing(
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
    # event.append 放宽幂等冲突：payload 不同时返回已存事件，不抛 409
    assert conflict.status_code == 200
    assert conflict.json()['summary'] == 'Runtime accepted'
    assert conflict.json()['seq'] == first.json()['seq']

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
async def test_agent_service_state_transition_completed_writes_only_final_text_to_chat(
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
        transcript = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json={
                'run_id': run.id,
                'block_id': 'note-1',
                'block_kind': 'assistant_note',
                'delta_index': 0,
                'delta': 'I checked the file before answering.',
                'participant_id': 'leader',
                'phase': 'finalizing',
                'idempotency_key': f'text:{run.id}:leader:note-1:0',
            },
            headers=_service_headers(f'text:{run.id}:leader:note-1:0'),
        )
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

    assert transcript.status_code == 200
    assert transcript.json()['payload']['block_kind'] == 'assistant_note'
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
async def test_internal_service_approval_decision_requires_service_credential(
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
    await AgentRuns.attach_runtime_session(run.id, 'runtime-session-1')
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        summary='Runtime accepted',
    )
    approval_id = f'approval:{run.id}:call-1'
    await AgentRuns.append_event(
        run.id,
        event_type='approval.requested',
        participant_id='leader',
        phase='waiting_approval',
        summary='Approval requested',
        payload={
            'approval_id': approval_id,
            'tool_call_id': 'call-1',
            'tool_id': 'tool-1',
            'tool_arguments_fingerprint': 'a' * 64,
            'tool_call_idempotency_key': 'tool:leader:call-1:1',
            'checkpoint_version': 7,
        },
    )
    key = f'approval:{approval_id}:approved'
    body = {
        'run_id': run.id,
        'approval_id': approval_id,
        'decision': 'approved',
        'idempotency_key': key,
    }

    with TestClient(app_without_fake_event_store) as client:
        missing = client.post(
            f'/api/agent/service/runs/{run.id}/approvals/{approval_id}/decision',
            json=body,
            headers={'X-Agent-Idempotency-Key': key},
        )
        invalid = client.post(
            f'/api/agent/service/runs/{run.id}/approvals/{approval_id}/decision',
            json=body,
            headers={
                'Authorization': 'Bearer wrong-token',
                'X-Agent-Idempotency-Key': key,
            },
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    async with agent_run_db() as session:
        executions = (
            await session.execute(
                select(AgentRunDecisionExecution).filter_by(run_id=run.id)
            )
        ).scalars().all()
    assert executions == []


@pytest.mark.asyncio
async def test_decision_routes_return_202_for_every_nonterminal_execution_status(
    agent_run_db,
    app_without_fake_event_store,
):
    class DecisionCoordinator:
        execution_status = 'pending'

        async def decide(self, request):
            return {
                'status': 'approval_recorded',
                'execution_id': 'execution-1',
                'execution_status': self.execution_status,
            }

        async def complete(self, request):
            return {
                'status': request.status,
                'execution_id': 'execution-1',
                'execution_status': self.execution_status,
            }

    coordinator = DecisionCoordinator()
    app_without_fake_event_store.state.AGENT_APPROVAL_COORDINATOR = coordinator
    app_without_fake_event_store.state.AGENT_USER_INPUT_COORDINATOR = coordinator
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-a',
    )

    with TestClient(app_without_fake_event_store) as client:
        for index, execution_status in enumerate(
            [
                'pending',
                'claimed',
                'prepared',
                'backend_committed',
                'activating',
                'activated',
            ]
        ):
            coordinator.execution_status = execution_status
            approval_key = f'approval-status-{index}'
            input_key = f'input-status-{index}'
            public_approval = client.post(
                f'/api/agent/runs/{run.id}/approvals/approval-1/decision',
                json={
                    'run_id': run.id,
                    'approval_id': 'approval-1',
                    'decision': 'approved',
                    'idempotency_key': approval_key,
                },
                headers={'X-Agent-Idempotency-Key': approval_key},
            )
            public_input = client.post(
                f'/api/agent/runs/{run.id}/user-input/input-1',
                json={
                    'run_id': run.id,
                    'user_input_id': 'input-1',
                    'status': 'accepted',
                    'content': {'answer': 'A'},
                    'idempotency_key': input_key,
                },
                headers={'X-Agent-Idempotency-Key': input_key},
            )
            service_approval = client.post(
                f'/api/agent/service/runs/{run.id}/approvals/approval-1/decision',
                json={
                    'run_id': run.id,
                    'approval_id': 'approval-1',
                    'decision': 'approved',
                    'idempotency_key': approval_key,
                },
                headers=_service_headers(approval_key),
            )

            assert public_approval.status_code == 202
            assert public_input.status_code == 202
            assert service_approval.status_code == 202

        for index, execution_status in enumerate(
            ['succeeded', 'failed', 'cancelled', 'historical_completed']
        ):
            coordinator.execution_status = execution_status
            key = f'terminal-status-{index}'
            response = client.post(
                f'/api/agent/service/runs/{run.id}/approvals/approval-1/decision',
                json={
                    'run_id': run.id,
                    'approval_id': 'approval-1',
                    'decision': 'approved',
                    'idempotency_key': key,
                },
                headers=_service_headers(key),
            )
            assert response.status_code == 200


def test_decision_route_openapi_freezes_shared_200_and_202_envelope(
    app_without_fake_event_store,
):
    schema = app_without_fake_event_store.openapi()
    paths = [
        '/api/agent/runs/{run_id}/approvals/{approval_id}/decision',
        '/api/agent/runs/{run_id}/user-input/{user_input_id}',
        '/api/agent/service/runs/{run_id}/approvals/{approval_id}/decision',
    ]

    for path in paths:
        responses = schema['paths'][path]['post']['responses']
        for status_code in ('200', '202'):
            response_schema = responses[status_code]['content'][
                'application/json'
            ]['schema']
            assert response_schema == {
                '$ref': '#/components/schemas/DecisionExecutionResponse'
            }


@pytest.mark.asyncio
async def test_legacy_service_routes_cannot_synthesize_approval_completion(
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
    await AgentRuns.attach_runtime_session(run.id, 'runtime-session-1')
    await AgentRuns.append_event(
        run.id,
        event_type='run.running',
        participant_id='leader',
        phase='running',
        summary='Runtime accepted',
    )
    approval_id = f'approval:{run.id}:call-1'
    await AgentRuns.append_event(
        run.id,
        event_type='approval.requested',
        participant_id='leader',
        phase='waiting_approval',
        summary='Approval requested',
        payload={
            'approval_id': approval_id,
            'tool_call_id': 'call-1',
            'tool_id': 'tool-1',
            'checkpoint_version': 7,
        },
    )

    with TestClient(app_without_fake_event_store) as client:
        transition = client.post(
            f'/api/agent/service/runs/{run.id}/state-transition',
            json={
                'run_id': run.id,
                'from_states': ['waiting_approval'],
                'to_state': 'running',
                'reason': 'legacy approval resume',
                'payload': {
                    'approval_id': approval_id,
                    'decision': 'approved',
                },
                'idempotency_key': f'legacy-state:{run.id}:approval',
            },
            headers=_service_headers(f'legacy-state:{run.id}:approval'),
        )
        event = client.post(
            f'/api/agent/service/runs/{run.id}/events',
            json={
                'run_id': run.id,
                'event_type': 'approval.completed',
                'participant_id': 'leader',
                'phase': 'running',
                'payload': {
                    'approval_id': approval_id,
                    'decision': 'approved',
                },
                'idempotency_key': f'legacy-event:{run.id}:approval',
            },
            headers=_service_headers(f'legacy-event:{run.id}:approval'),
        )

    assert transition.status_code == 409
    assert event.status_code == 409
    stored_run = await AgentRuns.get_run(run.id)
    stored_events = await AgentRuns.list_events(run.id)
    executions = []
    async with agent_run_db() as session:
        executions = (
            await session.execute(
                select(AgentRunDecisionExecution).filter_by(run_id=run.id)
            )
        ).scalars().all()
    assert stored_run is not None
    assert stored_run.state == 'waiting_approval'
    assert [item.event_type for item in stored_events] == [
        'run.running',
        'approval.requested',
    ]
    assert executions == []


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

    async def no_approval(tool_request, tool):
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

    async def no_approval(tool_request, tool):
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


@pytest.mark.asyncio
async def test_agent_service_text_delta_writes_to_db_without_final_text_side_effect(
    agent_run_db,
    app_without_fake_event_store,
):
    """text.delta endpoint writes replayable public transcript events without
    accumulating into final_text. Final answer content must use final.delta.
    Run does NOT need to be finalizing — text deltas are emitted during the
    ReAct loop.
    """
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

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json={
                'run_id': run.id,
                'block_id': 'block-1',
                'block_kind': 'assistant_note',
                'delta_index': 0,
                'delta': 'Let me check',
                'participant_id': 'leader',
                'phase': 'running',
                'idempotency_key': f'text:{run.id}:leader:block-1:0',
            },
            headers=_service_headers(f'text:{run.id}:leader:block-1:0'),
        )
        second = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json={
                'run_id': run.id,
                'block_id': 'block-1',
                'block_kind': 'assistant_note',
                'delta_index': 1,
                'delta': ' the repo.',
                'participant_id': 'leader',
                'phase': 'running',
                'idempotency_key': f'text:{run.id}:leader:block-1:1',
            },
            headers=_service_headers(f'text:{run.id}:leader:block-1:1'),
        )

    assert first.status_code == 200
    assert first.json()['event_type'] == 'text.delta'
    assert first.json()['payload']['block_id'] == 'block-1'
    assert first.json()['payload']['block_kind'] == 'assistant_note'
    assert first.json()['payload']['delta_index'] == 0
    assert first.json()['payload']['text'] == 'Let me check'
    assert second.status_code == 200
    assert second.json()['payload']['delta_index'] == 1
    assert second.json()['payload']['text'] == 'Let me check the repo.'

    updated = await AgentRuns.get_run(run.id)
    assert updated is not None
    assert updated.final_text == ''
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == ['text.delta', 'text.delta']
    assert [event.payload['block_kind'] for event in events] == ['assistant_note', 'assistant_note']

    with TestClient(app_without_fake_event_store) as client:
        listed = client.get(f'/api/agent/runs/{run.id}/events/list')

    class FakeRequest:
        app = app_without_fake_event_store

        async def is_disconnected(self):
            return False

    stream = await agent_runs.stream_agent_run_events(
        run.id,
        FakeRequest(),
        after_seq=0,
        last_event_id=None,
        user=SimpleNamespace(id='user-1'),
    )
    iterator = stream.body_iterator
    stream_text = await asyncio.wait_for(anext(iterator), timeout=1)
    await iterator.aclose()

    assert listed.status_code == 200
    assert listed.json()['events'][0]['payload']['block_kind'] == 'assistant_note'
    assert stream.status_code == 200
    assert '"block_kind":"assistant_note"' in stream_text


@pytest.mark.asyncio
async def test_agent_service_text_delta_requires_block_kind(
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

    with TestClient(app_without_fake_event_store) as client:
        missing = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json={
                'run_id': run.id,
                'block_id': 'block-1',
                'delta_index': 0,
                'delta': 'hello',
                'idempotency_key': f'text:{run.id}:block-1:0',
            },
            headers=_service_headers(f'text:{run.id}:block-1:0'),
        )
        invalid = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json={
                'run_id': run.id,
                'block_id': 'block-2',
                'block_kind': 'raw_reasoning',
                'delta_index': 0,
                'delta': 'hello',
                'idempotency_key': f'text:{run.id}:block-2:0',
            },
            headers=_service_headers(f'text:{run.id}:block-2:0'),
        )

    assert missing.status_code == 422
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_agent_service_text_delta_retries_are_idempotent_and_conflicting_bodies_return_existing(
    agent_run_db,
    app_without_fake_event_store,
):
    """text.delta shares event.append's idempotency relaxation: duplicate
    payload returns the same event, conflicting payload also returns the
    previously stored event (not 409).
    """
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
    key = f'text:{run.id}:leader:block-1:0'
    body = {
        'run_id': run.id,
        'block_id': 'block-1',
        'block_kind': 'assistant_note',
        'delta_index': 0,
        'delta': 'hello',
        'participant_id': 'leader',
        'phase': 'running',
        'idempotency_key': key,
    }

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json=body,
            headers=_service_headers(key),
        )
        duplicate = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json=body,
            headers=_service_headers(key),
        )
        conflict = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json={**body, 'delta': 'changed'},
            headers=_service_headers(key),
        )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert conflict.status_code == 200
    assert conflict.json()['seq'] == first.json()['seq']
    assert conflict.json()['payload']['delta'] == 'hello'
    assert conflict.json()['payload']['block_kind'] == 'assistant_note'

    updated = await AgentRuns.get_run(run.id)
    assert updated is not None
    assert updated.final_text == ''
    events = await AgentRuns.list_events(run.id)
    assert len(events) == 1
    assert events[0].event_type == 'text.delta'


@pytest.mark.asyncio
async def test_agent_service_text_delta_gap_returns_409(
    agent_run_db,
    app_without_fake_event_store,
):
    """text.delta enforces per-block monotonic delta_index."""
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

    with TestClient(app_without_fake_event_store) as client:
        first = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json={
                'run_id': run.id,
                'block_id': 'block-1',
                'block_kind': 'assistant_note',
                'delta_index': 0,
                'delta': 'hel',
                'idempotency_key': f'text:{run.id}:block-1:0',
            },
            headers=_service_headers(f'text:{run.id}:block-1:0'),
        )
        gap = client.post(
            f'/api/agent/service/runs/{run.id}/text-delta',
            json={
                'run_id': run.id,
                'block_id': 'block-1',
                'block_kind': 'assistant_note',
                'delta_index': 2,
                'delta': 'lo',
                'idempotency_key': f'text:{run.id}:block-1:2',
            },
            headers=_service_headers(f'text:{run.id}:block-1:2'),
        )

    assert first.status_code == 200
    assert gap.status_code == 409
    assert 'gap' in gap.json()['detail']
