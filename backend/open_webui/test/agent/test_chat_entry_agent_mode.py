import importlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from open_webui.internal.db import Base
from open_webui.models.agent_runs import AgentRuns
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

main = importlib.import_module('open_webui.main')


OPEN_WEBUI_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = OPEN_WEBUI_DIR / 'config.py'
MAIN_PATH = OPEN_WEBUI_DIR / 'main.py'


@pytest_asyncio.fixture
async def agent_run_db(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
def chat_entry_patches(monkeypatch):
    calls = SimpleNamespace(provider_calls=[], runtime_calls=[], upserts=[])
    _patch_model_and_chat_boundaries(monkeypatch, calls)
    _patch_legacy_chat_pipeline(monkeypatch, calls)
    _patch_runtime_client(monkeypatch, calls)
    return calls


def _patch_model_and_chat_boundaries(monkeypatch, calls):
    async def fake_get_model_by_id(model_id):
        return None

    async def fake_is_chat_owner(chat_id, user_id):
        return True

    async def fake_get_message(chat_id, message_id):
        if message_id == 'user-msg':
            return {'id': 'user-msg', 'childrenIds': []}
        return None

    async def fake_upsert(chat_id, message_id, message):
        calls.upserts.append((chat_id, message_id, message))
        return message

    monkeypatch.setattr(main.Models, 'get_model_by_id', fake_get_model_by_id)
    monkeypatch.setattr(main.Chats, 'is_chat_owner', fake_is_chat_owner)
    monkeypatch.setattr(main.Chats, 'get_message_by_id_and_message_id', fake_get_message)
    monkeypatch.setattr(main.Chats, 'upsert_message_to_chat_by_id_and_message_id', fake_upsert)


def _patch_legacy_chat_pipeline(monkeypatch, calls):
    async def fake_process_payload(request, form_data, user, metadata, model):
        return form_data, metadata, []

    async def fake_provider_handler(request, form_data, user):
        calls.provider_calls.append(form_data)
        return {'provider': True}

    async def fake_build_context(request, form_data, user, model, metadata, tasks, events):
        return {'metadata': metadata}

    async def fake_process_response(response, ctx):
        return {'legacy': True, 'provider_response': response}

    monkeypatch.setattr(main, 'process_chat_payload', fake_process_payload)
    monkeypatch.setattr(main, 'chat_completion_handler', fake_provider_handler)
    monkeypatch.setattr(main, 'build_chat_response_context', fake_build_context)
    monkeypatch.setattr(main, 'process_chat_response', fake_process_response)


def _patch_runtime_client(monkeypatch, calls):
    class RuntimeClient:
        def __init__(self, base_url, service_token=None, timeout=None):
            self.base_url = base_url
            self.service_token = service_token
            self.timeout = timeout

        async def start_run(self, payload):
            calls.runtime_calls.append(payload)
            return {'accepted': True, 'runtime_session_id': 'runtime-session-1'}

    monkeypatch.setattr(main, 'AgentRuntimeClient', RuntimeClient, raising=False)


def test_agent_mode_rollout_config_is_defined_and_assigned_to_app_config():
    config_text = CONFIG_PATH.read_text()
    main_text = MAIN_PATH.read_text()

    for symbol in [
        'ENABLE_AGENT_MODE',
        'AGENT_RUNTIME_BASE_URL',
        'AGENT_RUNTIME_SERVICE_TOKEN',
        'AGENT_RUN_DEFAULT_TIMEOUT_SECONDS',
        'AGENT_RUN_MAX_MODEL_CALLS',
        'AGENT_RUN_MAX_TOOL_CALLS',
        'AGENT_TEAM_MAX_SUBAGENTS',
        'AGENT_SUBAGENT_DEFAULT_BUDGET',
    ]:
        assert f'{symbol} = ConfigVar(' in config_text
        assert symbol in main_text
        assert f'app.state.config.{symbol} = {symbol}' in main_text


@pytest.mark.asyncio
async def test_agent_mode_disabled_uses_legacy_chat_path(agent_run_db, chat_entry_patches):
    request = _request(enable_agent_mode=False)

    response = await main.chat_completion(request, _chat_form(include_session=False), _user())

    assert response['legacy'] is True
    assert len(chat_entry_patches.provider_calls) == 1
    assert chat_entry_patches.runtime_calls == []
    assert await AgentRuns.list_runs_by_chat('chat-1', 'user-1') == []


@pytest.mark.asyncio
async def test_agent_mode_enabled_creates_run_links_message_and_starts_runtime(
    agent_run_db,
    chat_entry_patches,
):
    request = _request(enable_agent_mode=True)

    response = await main.chat_completion(request, _chat_form(), _user())

    runs = await AgentRuns.list_runs_by_chat('chat-1', 'user-1')
    assert len(runs) == 1
    run = runs[0]
    assert run.state == 'running'
    assert run.runtime_session_id == 'runtime-session-1'
    assert run.assistant_message_id == 'assistant-msg'
    assert run.leader_model_id == 'model-a'
    assert response['status'] is True
    assert response['chat_id'] == 'chat-1'
    assert response['agent_run_id'] == run.id
    assert response['task_ids'] == []
    assert chat_entry_patches.provider_calls == []
    assert chat_entry_patches.runtime_calls[0]['run_id'] == run.id
    assert any(
        message_id == 'assistant-msg' and message.get('agent_run_id') == run.id
        for _chat_id, message_id, message in chat_entry_patches.upserts
    )


@pytest.mark.asyncio
async def test_agent_mode_runtime_unavailable_marks_run_failed_and_visible(
    monkeypatch,
    agent_run_db,
    chat_entry_patches,
):
    class UnavailableRuntimeClient:
        def __init__(self, base_url, service_token=None, timeout=None):
            pass

        async def start_run(self, payload):
            raise main.AgentRuntimeUnavailable('agent runtime unavailable')

    monkeypatch.setattr(main, 'AgentRuntimeClient', UnavailableRuntimeClient, raising=False)
    request = _request(enable_agent_mode=True)

    response = await main.chat_completion(request, _chat_form(), _user())

    runs = await AgentRuns.list_runs_by_chat('chat-1', 'user-1')
    assert len(runs) == 1
    run = runs[0]
    assert run.state == 'failed'
    assert run.error == {
        'code': 'agent_runtime_unavailable',
        'message': 'agent runtime unavailable',
    }
    assert response['status'] is False
    assert response['agent_run_id'] == run.id
    assert response['error']['code'] == 'agent_runtime_unavailable'
    assert chat_entry_patches.provider_calls == []
    assert any(
        message_id == 'assistant-msg'
        and message.get('agent_run_id') == run.id
        and message.get('error', {}).get('content') == 'agent runtime unavailable'
        for _chat_id, message_id, message in chat_entry_patches.upserts
    )


@pytest.mark.asyncio
async def test_user_supplied_internal_guard_metadata_and_headers_do_not_bypass_agent_mode(
    agent_run_db,
    chat_entry_patches,
):
    request = _request(
        enable_agent_mode=True,
        headers={
            'X-OpenWebUI-Agent-Internal-Model-Call': 'true',
            'X-OpenWebUI-Agent-Run-Id': 'forged-run',
        },
    )
    form = _chat_form()
    form['metadata'] = {
        'agent_internal_model_call': True,
        'agent_run_id': 'forged-run',
    }

    response = await main.chat_completion(request, form, _user())

    runs = await AgentRuns.list_runs_by_chat('chat-1', 'user-1')
    assert len(runs) == 1
    assert response['agent_run_id'] == runs[0].id
    assert chat_entry_patches.provider_calls == []


@pytest.mark.asyncio
async def test_trusted_request_state_internal_guard_uses_legacy_model_path(
    agent_run_db,
    chat_entry_patches,
):
    request = _request(enable_agent_mode=True)
    request.state.agent_internal_model_call = True
    request.state.agent_run_id = 'trusted-run'
    request.state.agent_service_principal = 'agentscope-runtime'

    response = await main.chat_completion(request, _chat_form(include_session=False), _user())

    assert response['legacy'] is True
    assert len(chat_entry_patches.provider_calls) == 1
    assert chat_entry_patches.runtime_calls == []
    assert await AgentRuns.list_runs_by_chat('chat-1', 'user-1') == []


def _request(*, enable_agent_mode: bool, headers: dict[str, str] | None = None):
    config = SimpleNamespace(
        DEFAULT_MODEL_PARAMS={},
        ENABLE_AGENT_MODE=enable_agent_mode,
        AGENT_RUNTIME_BASE_URL='http://agent-runtime.test',
        AGENT_RUNTIME_SERVICE_TOKEN='test-service-token',
        AGENT_RUN_DEFAULT_TIMEOUT_SECONDS=30,
        AGENT_RUN_MAX_MODEL_CALLS=8,
        AGENT_RUN_MAX_TOOL_CALLS=12,
        AGENT_TEAM_MAX_SUBAGENTS=5,
        AGENT_SUBAGENT_DEFAULT_BUDGET={'max_model_calls': 2, 'max_tool_calls': 3},
        USER_PERMISSIONS={},
    )
    model = {
        'id': 'model-a',
        'name': 'Model A',
        'info': {'meta': {}},
    }
    app = SimpleNamespace(
        state=SimpleNamespace(
            MODELS={'model-a': model},
            config=config,
            redis=None,
        )
    )
    return SimpleNamespace(app=app, state=SimpleNamespace(), headers=headers or {})


def _user():
    return SimpleNamespace(id='user-1', role='admin')


def _chat_form(*, include_session: bool = True):
    form = {
        'model': 'model-a',
        'chat_id': 'chat-1',
        'id': 'assistant-msg',
        'parent_id': 'root',
        'user_message': {
            'id': 'user-msg',
            'parentId': None,
            'childrenIds': [],
            'role': 'user',
            'content': 'hello',
        },
        'stream': True,
    }
    if include_session:
        form['session_id'] = 'session-1'
    return form
