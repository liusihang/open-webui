import importlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from open_webui.agent.artifacts import AgentRunArtifactRegistrar
from open_webui.agent.model_authority import ModelCallRequest, _model_call_form_data
from open_webui.agent.resources import AgentRunResourceManager
from open_webui.internal.db import Base
from open_webui.models.agent_runs import AgentRuns
from open_webui.routers import agent_runs as agent_runs_router
from open_webui.routers.openai import convert_to_responses_payload
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

main = importlib.import_module('open_webui.main')


OPEN_WEBUI_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = OPEN_WEBUI_DIR / 'config.py'
MAIN_PATH = OPEN_WEBUI_DIR / 'main.py'


def test_agent_context_replay_json_sanitizes_json_encoded_private_fields():
    encoded = main._agent_context_replay_json(
        '{"stdout":"ok","raw_reasoning":"secret","nested":{"Thought":"hidden"}}'
    )

    assert json.loads(encoded) == {'nested': {}, 'stdout': 'ok'}


def test_agent_context_replay_trim_items_drops_orphaned_tool_items(monkeypatch):
    message = {
        'type': 'message',
        'role': 'assistant',
        'content': 'Continue from the verified result.',
        'phase': 'commentary',
    }
    unmatched = main._agent_context_replay_trim_items(
        [
            {
                'type': 'function_call',
                'call_id': 'orphan-call',
                'name': 'read_file',
                'arguments': '{}',
            },
            message,
        ]
    )
    assert unmatched == [message]

    monkeypatch.setattr(main, '_AGENT_CONTEXT_REPLAY_MAX_CHARS', 240)
    trimmed = main._agent_context_replay_trim_items(
        [
            {
                'type': 'function_call',
                'call_id': 'large-call',
                'name': 'run_command',
                'arguments': json.dumps({'command': 'x' * 500}),
            },
            {
                'type': 'function_call_output',
                'call_id': 'large-call',
                'output': '{"status":"success"}',
            },
            message,
        ]
    )
    call_ids = {
        item['call_id'] for item in trimmed if item.get('type') == 'function_call'
    }
    output_ids = {
        item['call_id'] for item in trimmed if item.get('type') == 'function_call_output'
    }
    assert call_ids == output_ids


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
    calls = SimpleNamespace(provider_calls=[], runtime_calls=[], upserts=[], process_payload_calls=[])
    _patch_model_and_chat_boundaries(monkeypatch, calls)
    _patch_legacy_chat_pipeline(monkeypatch, calls)
    _patch_runtime_client(monkeypatch, calls)
    return calls


def _patch_model_and_chat_boundaries(monkeypatch, calls):
    async def fake_get_model_by_id(model_id):
        return None

    async def fake_config_get(key, default=None):
        return default

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
    monkeypatch.setattr(main.Config, 'get', fake_config_get)
    monkeypatch.setattr(main.Chats, 'is_chat_owner', fake_is_chat_owner)
    monkeypatch.setattr(main.Chats, 'get_message_by_id_and_message_id', fake_get_message)
    monkeypatch.setattr(main.Chats, 'upsert_message_to_chat_by_id_and_message_id', fake_upsert)


def _patch_legacy_chat_pipeline(monkeypatch, calls):
    async def fake_process_payload(request, form_data, user, metadata, model):
        calls.process_payload_calls.append(
            {
                'form_data': dict(form_data),
                'metadata': dict(metadata),
                'model': model,
            }
        )
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

    expected = {
        'ENABLE_AGENT_MODE': 'agent.mode.enable',
        'AGENT_RUNTIME_BASE_URL': 'agent.runtime.base_url',
        'AGENT_RUNTIME_SERVICE_TOKEN': 'agent.runtime.service_token',
        'AGENT_RUN_DEFAULT_TIMEOUT_SECONDS': 'agent.run.default_timeout_seconds',
        'AGENT_RUN_MAX_MODEL_CALLS': 'agent.run.max_model_calls',
        'AGENT_RUN_MAX_TOOL_CALLS': 'agent.run.max_tool_calls',
        'AGENT_TEAM_MAX_SUBAGENTS': 'agent.team.max_subagents',
        'AGENT_SUBAGENT_DEFAULT_BUDGET': 'agent.subagent.default_budget',
    }
    for symbol, config_key in expected.items():
        assert f'{symbol} =' in config_text
        assert f"'{config_key}': {symbol}" in config_text
        assert f"'{symbol}': '{config_key}'" in main_text

    assert "'agent.mode.enable'" in main_text
    assert "'enable_agent_mode': config.get('agent.mode.enable')" in main_text


def test_agent_mode_routers_are_mounted_on_main_app():
    paths = {getattr(route, 'path', '') for route in main.app.routes}

    assert '/api/agent/runs/{run_id}/events' in paths
    assert '/api/agent/service/runs/{run_id}/events' in paths


def test_main_app_initializes_agent_run_app_state_helpers():
    resource_manager = getattr(main.app.state, 'AGENT_RUN_RESOURCE_MANAGER', None)
    artifact_registrar = getattr(main.app.state, 'AGENT_RUN_ARTIFACT_REGISTRAR', None)

    assert isinstance(resource_manager, AgentRunResourceManager)
    assert isinstance(artifact_registrar, AgentRunArtifactRegistrar)
    assert artifact_registrar.artifact_store is AgentRuns


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
    runtime_payload = chat_entry_patches.runtime_calls[0]
    assert runtime_payload['run_id'] == run.id
    assert runtime_payload['team_cap'] == 5
    assert runtime_payload['model_catalog'] == [
        {
            'id': 'model-a',
            'role': 'leader',
            'meta': {},
        }
    ]
    assert any(
        message_id == 'assistant-msg' and message.get('agent_run_id') == run.id
        for _chat_id, message_id, message in chat_entry_patches.upserts
    )


@pytest.mark.asyncio
async def test_agent_mode_marks_run_running_before_runtime_start(
    monkeypatch,
    agent_run_db,
    chat_entry_patches,
):
    observed_states = []

    class InspectingRuntimeClient:
        def __init__(self, base_url, service_token=None, timeout=None):
            pass

        async def start_run(self, payload):
            run = await AgentRuns.get_run(payload['run_id'])
            observed_states.append(run.state)
            return {'accepted': True, 'runtime_session_id': 'runtime-session-1'}

    monkeypatch.setattr(main, 'AgentRuntimeClient', InspectingRuntimeClient, raising=False)
    request = _request(enable_agent_mode=True)

    await main.chat_completion(request, _chat_form(), _user())

    assert observed_states == ['running']


@pytest.mark.asyncio
async def test_agent_mode_runtime_payload_preserves_chat_completion_context(
    agent_run_db,
    chat_entry_patches,
):
    request = _request(enable_agent_mode=True)
    messages = [
        {'role': 'user', 'content': 'Remember this code: ORCHID-42.'},
        {'role': 'assistant', 'content': 'I will remember ORCHID-42.'},
        {'role': 'user', 'content': 'What code did I ask you to remember?'},
    ]
    form = _chat_form()
    form['id'] = 'assistant-current'
    form['parent_id'] = 'assistant-prev'
    form['messages'] = messages
    form['user_message'] = {
        'id': 'user-msg',
        'parentId': 'assistant-prev',
        'childrenIds': [],
        'role': 'user',
        'content': 'What code did I ask you to remember?',
    }

    await main.chat_completion(request, form, _user())

    runtime_payload = chat_entry_patches.runtime_calls[0]
    assert runtime_payload['messages'] == messages


@pytest.mark.asyncio
async def test_agent_mode_runtime_payload_strips_tool_loop_messages(
    agent_run_db,
    chat_entry_patches,
):
    request = _request(enable_agent_mode=True)
    form = _chat_form()
    form['messages'] = [
        {'role': 'system', 'content': 'System instructions.'},
        {'role': 'user', 'content': 'Run a command.'},
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call-1',
                    'type': 'function',
                    'function': {'name': 'run_command', 'arguments': '{}'},
                }
            ],
        },
        {
            'role': 'tool',
            'tool_call_id': 'call-1',
            'content': '{"stdout":"Python 3.12.13"}',
        },
        {
            'role': 'assistant',
            'content': 'Python is available.',
            'output': [{'type': 'function_call_output', 'call_id': 'call-1', 'output': []}],
            'metadata': {'agent_run_id': 'previous-run'},
        },
        {'role': 'user', 'content': 'Continue.'},
    ]

    await main.chat_completion(request, form, _user())

    runtime_payload = chat_entry_patches.runtime_calls[0]
    assert runtime_payload['messages'] == [
        {'role': 'system', 'content': 'System instructions.'},
        {'role': 'user', 'content': 'Run a command.'},
        {'role': 'assistant', 'content': 'Python is available.'},
        {'role': 'user', 'content': 'Continue.'},
    ]
    assert not any('tool_calls' in message for message in runtime_payload['messages'])
    assert not any(message.get('role') == 'tool' for message in runtime_payload['messages'])


@pytest.mark.asyncio
async def test_agent_mode_runtime_payload_replays_previous_public_agent_items(
    agent_run_db,
    chat_entry_patches,
):
    previous_run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='user-prev',
        assistant_message_id='assistant-prev',
        leader_model_id='model-a',
    )
    await AgentRuns.transition_state(
        previous_run.id,
        from_states=['queued'],
        to_state='running',
        reason='test previous run started',
    )
    await AgentRuns.append_event(
        previous_run.id,
        event_type='text.delta',
        participant_id='leader',
        phase='running',
        payload={
            'block_id': 'note-1',
            'block_kind': 'assistant_note',
            'delta_index': 0,
            'delta': 'I inspected the migration plan.',
            'raw_reasoning': 'SECRET_PRIVATE_THOUGHT',
        },
    )
    await AgentRuns.append_event(
        previous_run.id,
        event_type='tool.requested',
        participant_id='leader',
        phase='running',
        summary='Run command requested',
        payload={
            'tool_call_id': 'call-1',
            'tool_name': 'run_command',
            'arguments': {'command': 'python3 --version'},
        },
    )
    await AgentRuns.append_event(
        previous_run.id,
        event_type='text.delta',
        participant_id='leader',
        phase='running',
        payload={
            'block_id': 'summary-1',
            'block_kind': 'action_summary',
            'delta_index': 0,
            'delta': 'The runtime image was built successfully.',
            'reasoning': {'hidden': 'SECRET_REASONING_OBJECT'},
        },
    )
    await AgentRuns.append_event(
        previous_run.id,
        event_type='tool.completed',
        participant_id='leader',
        phase='running',
        summary='Run command completed',
        payload={
            'tool_call_id': 'call-1',
            'tool_name': 'run_command',
            'status': 'success',
            'result': {
                'stdout': 'Python 3.12.13',
                'raw_reasoning': 'SECRET_TOOL_PRIVATE_REASONING',
            },
        },
    )
    await AgentRuns.transition_state(
        previous_run.id,
        from_states=['running'],
        to_state='finalizing',
        reason='test previous run finalizing',
    )
    await AgentRuns.append_event(
        previous_run.id,
        event_type='final.delta',
        participant_id='leader',
        phase='finalizing',
        payload={
            'final_stream_id': 'final-1',
            'delta_index': 0,
            'delta': 'Previous final answer.',
            'text': 'Previous final answer.',
        },
    )
    await AgentRuns.transition_state(
        previous_run.id,
        from_states=['finalizing'],
        to_state='completed',
        reason='test previous run completed',
        payload={'final_text': 'Previous final answer.'},
    )

    request = _request(enable_agent_mode=True)
    form = _chat_form()
    form['id'] = 'assistant-current'
    form['parent_id'] = 'assistant-prev'
    form['messages'] = [
        {'role': 'user', 'content': 'Build the image.'},
        {'role': 'assistant', 'content': 'Previous final answer.'},
        {'role': 'user', 'content': 'Continue from there.'},
    ]
    form['user_message'] = {
        'id': 'user-msg',
        'parentId': 'assistant-prev',
        'childrenIds': [],
        'role': 'user',
        'content': 'Continue from there.',
    }

    await main.chat_completion(request, form, _user())

    runtime_payload = chat_entry_patches.runtime_calls[0]
    assert runtime_payload['messages'] == [
        {'role': 'user', 'content': 'Build the image.'},
        {'role': 'assistant', 'content': 'Previous final answer.'},
        {'role': 'user', 'content': 'Continue from there.'},
    ]
    replay_items = runtime_payload['metadata']['agent_context_replay']
    assert len(replay_items) == 1
    assert replay_items[0]['agent_run_id'] == previous_run.id
    assert replay_items[0]['assistant_message_id'] == 'assistant-prev'
    assert replay_items[0]['state'] == 'completed'
    assert replay_items[0]['messages'] == [
        {
            'role': 'assistant',
            'content': 'I inspected the migration plan.',
            'phase': 'commentary',
        },
        {
            'role': 'assistant',
            'content': 'The runtime image was built successfully.',
            'phase': 'commentary',
        },
        {
            'role': 'assistant',
            'content': 'Previous final answer.',
            'phase': 'final_answer',
        },
    ]
    namespaced_call_id = next(
        item['call_id']
        for item in replay_items[0]['items']
        if item.get('type') == 'function_call'
    )
    assert namespaced_call_id != 'call-1'
    assert len(namespaced_call_id) <= 64
    assert replay_items[0]['items'] == [
        {
            'type': 'message',
            'role': 'assistant',
            'content': 'I inspected the migration plan.',
            'phase': 'commentary',
        },
        {
            'type': 'function_call',
            'call_id': namespaced_call_id,
            'name': 'run_command',
            'arguments': '{"command": "python3 --version"}',
        },
        {
            'type': 'function_call_output',
            'call_id': namespaced_call_id,
            'output': '{"stdout": "Python 3.12.13"}',
        },
        {
            'type': 'message',
            'role': 'assistant',
            'content': 'The runtime image was built successfully.',
            'phase': 'commentary',
        },
        {
            'type': 'message',
            'role': 'assistant',
            'content': 'Previous final answer.',
            'phase': 'final_answer',
        },
    ]
    replay_text = str(replay_items[0])
    assert '[Previous Agent Mode assistant context]' not in replay_text
    assert 'phase:running' not in replay_text
    assert 'phase:finalizing' not in replay_text
    assert 'SECRET_PRIVATE_THOUGHT' not in replay_text
    assert 'SECRET_REASONING_OBJECT' not in replay_text
    assert 'raw_reasoning' not in replay_text
    assert 'reasoning' not in replay_text

    provider_form = _model_call_form_data(
        ModelCallRequest(
            run_id='current-run',
            participant_id='leader',
            model_call_id='model-call-1',
            model='model-a',
            messages=replay_items[0]['items'],
            stream=True,
            idempotency_key='model:leader:model-call-1:1',
        )
    )
    provider_payload = convert_to_responses_payload(provider_form)
    provider_input = provider_payload['input']
    call_index = next(
        index
        for index, item in enumerate(provider_input)
        if item.get('type') == 'function_call'
        and item.get('call_id') == namespaced_call_id
    )
    assert provider_input[call_index + 1] == {
        'type': 'function_call_output',
        'call_id': namespaced_call_id,
        'output': '{"stdout": "Python 3.12.13"}',
    }
    summary_index = next(
        index
        for index, item in enumerate(provider_input)
        if item.get('type') == 'message'
        and item.get('phase') == 'commentary'
        and item.get('content') == [
            {
                'type': 'output_text',
                'text': 'The runtime image was built successfully.',
            }
        ]
    )
    assert summary_index > call_index + 1


@pytest.mark.asyncio
async def test_agent_context_replay_canonicalizes_parallel_and_incomplete_tool_batches(
    monkeypatch,
):
    events = [
        SimpleNamespace(
            event_type='text.delta',
            payload={
                'block_kind': 'assistant_note',
                'delta': 'I will use tool one.',
                'tool_call_id': 'call-1',
            },
        ),
        SimpleNamespace(
            event_type='tool.requested',
            payload={
                'tool_call_id': 'call-1',
                'tool_name': 'tool_one',
                'arguments': {'value': 1},
            },
        ),
        SimpleNamespace(
            event_type='text.delta',
            payload={
                'block_kind': 'assistant_note',
                'delta': 'I will use tool two.',
                'tool_call_id': 'call-2',
            },
        ),
        SimpleNamespace(
            event_type='tool.requested',
            payload={
                'tool_call_id': 'call-2',
                'tool_name': 'tool_two',
                'arguments': {'value': 2},
            },
        ),
        SimpleNamespace(
            event_type='text.delta',
            payload={
                'block_kind': 'action_summary',
                'delta': 'Tool two failed.',
                'tool_call_id': 'call-2',
            },
        ),
        SimpleNamespace(
            event_type='tool.failed',
            payload={
                'tool_call_id': 'call-2',
                'tool_name': 'tool_two',
                'status': 'failed',
            },
        ),
        SimpleNamespace(
            event_type='text.delta',
            payload={
                'block_kind': 'action_summary',
                'delta': 'Tool one completed.',
            },
        ),
        SimpleNamespace(
            event_type='tool.completed',
            payload={
                'tool_call_id': 'call-1',
                'tool_name': 'tool_one',
                'status': 'success',
                'result': {'value': 'one'},
            },
        ),
        SimpleNamespace(
            event_type='text.delta',
            payload={
                'block_kind': 'assistant_note',
                'delta': 'I will use an unfinished tool.',
                'tool_call_id': 'call-3',
            },
        ),
        SimpleNamespace(
            event_type='tool.requested',
            payload={
                'tool_call_id': 'call-3',
                'tool_name': 'tool_three',
                'arguments': {},
            },
        ),
        SimpleNamespace(
            event_type='text.delta',
            payload={
                'block_kind': 'action_summary',
                'delta': 'The unfinished tool completed.',
                'tool_call_id': 'call-3',
            },
        ),
    ]

    async def list_events(_run_id):
        return events

    monkeypatch.setattr(main.AgentRuns, 'list_events', list_events)
    run = SimpleNamespace(id='previous-run', final_text='Previous final answer.')

    messages, replay_items = await main._agent_context_replay_messages_and_items(run)

    assert replay_items == [
        {
            'type': 'message',
            'role': 'assistant',
            'content': 'I will use tool one.',
            'phase': 'commentary',
        },
        {
            'type': 'message',
            'role': 'assistant',
            'content': 'I will use tool two.',
            'phase': 'commentary',
        },
        {
            'type': 'function_call',
            'call_id': 'call-1',
            'name': 'tool_one',
            'arguments': '{"value": 1}',
        },
        {
            'type': 'function_call',
            'call_id': 'call-2',
            'name': 'tool_two',
            'arguments': '{"value": 2}',
        },
        {
            'type': 'function_call_output',
            'call_id': 'call-2',
            'output': '{"status": "failed"}',
        },
        {
            'type': 'function_call_output',
            'call_id': 'call-1',
            'output': '{"value": "one"}',
        },
        {
            'type': 'message',
            'role': 'assistant',
            'content': 'Tool two failed.',
            'phase': 'commentary',
        },
        {
            'type': 'message',
            'role': 'assistant',
            'content': 'Tool one completed.',
            'phase': 'commentary',
        },
        {
            'type': 'message',
            'role': 'assistant',
            'content': 'Previous final answer.',
            'phase': 'final_answer',
        },
    ]
    assert messages == [
        {
            key: item[key]
            for key in ('role', 'content', 'phase')
        }
        for item in replay_items
        if item.get('role') == 'assistant'
    ]
    replay_text = str(replay_items)
    assert 'call-3' not in replay_text
    assert 'unfinished tool' not in replay_text

    provider_form = _model_call_form_data(
        ModelCallRequest(
            run_id='current-run',
            participant_id='leader',
            model_call_id='model-call-parallel',
            model='model-a',
            messages=replay_items,
            stream=True,
            idempotency_key='model:leader:model-call-parallel:1',
        )
    )
    provider_messages = provider_form['messages']
    tool_batch_index = next(
        index
        for index, message in enumerate(provider_messages)
        if message.get('tool_calls')
    )
    assert [
        tool_call['id']
        for tool_call in provider_messages[tool_batch_index]['tool_calls']
    ] == ['call-1', 'call-2']
    assert [
        message.get('tool_call_id')
        for message in provider_messages[tool_batch_index + 1 : tool_batch_index + 3]
    ] == ['call-2', 'call-1']
    assert all(
        message.get('role') == 'tool'
        for message in provider_messages[tool_batch_index + 1 : tool_batch_index + 3]
    )

    responses_input = convert_to_responses_payload(provider_form)['input']
    first_call_index = next(
        index
        for index, item in enumerate(responses_input)
        if item.get('type') == 'function_call'
    )
    assert [
        item.get('type')
        for item in responses_input[first_call_index : first_call_index + 4]
    ] == [
        'function_call',
        'function_call',
        'function_call_output',
        'function_call_output',
    ]
    assert [
        item.get('call_id')
        for item in responses_input[first_call_index : first_call_index + 4]
    ] == ['call-1', 'call-2', 'call-2', 'call-1']


@pytest.mark.asyncio
async def test_agent_context_replay_namespaces_tool_call_ids_across_runs(monkeypatch):
    runs = [
        SimpleNamespace(
            id='run-newer',
            state='completed',
            assistant_message_id='assistant-newer',
        ),
        SimpleNamespace(
            id='run-older',
            state='completed',
            assistant_message_id='assistant-older',
        ),
    ]

    async def list_runs_by_chat(_chat_id, _user_id):
        return runs

    async def replay_messages_and_items(_run):
        return [], [
            {
                'type': 'function_call',
                'call_id': 'tool-call-1',
                'name': 'run_command',
                'arguments': '{}',
            },
            {
                'type': 'function_call_output',
                'call_id': 'tool-call-1',
                'output': '{}',
            },
        ]

    monkeypatch.setattr(main.AgentRuns, 'list_runs_by_chat', list_runs_by_chat)
    monkeypatch.setattr(
        main,
        '_agent_context_replay_messages_and_items',
        replay_messages_and_items,
    )

    replay = await main._agent_context_replay_items(
        chat_id='chat-1',
        user_id='user-1',
        exclude_run_id='current-run',
        anchor_message_ids={'assistant-older', 'assistant-newer'},
    )

    call_ids = [
        item['call_id']
        for entry in replay
        for item in entry['items']
        if item.get('type') == 'function_call'
    ]
    output_ids = [
        item['call_id']
        for entry in replay
        for item in entry['items']
        if item.get('type') == 'function_call_output'
    ]
    assert len(call_ids) == len(set(call_ids)) == 2
    assert output_ids == call_ids
    assert all(len(call_id) <= 64 for call_id in call_ids)


@pytest.mark.asyncio
async def test_agent_mode_runtime_payload_preserves_reasoning_model_params(
    agent_run_db,
    chat_entry_patches,
):
    request = _request(enable_agent_mode=True)
    form = _chat_form()
    form['params'] = {'temperature': 0.2}
    form['reasoning'] = {
        'enabled': True,
        'effort': 'xhigh',
        'max_tokens': 12400,
    }

    await main.chat_completion(request, form, _user())

    runtime_payload = chat_entry_patches.runtime_calls[0]
    assert runtime_payload['metadata']['model_params'] == {
        'temperature': 0.2,
        'reasoning': {
            'enabled': True,
            'effort': 'xhigh',
            'max_tokens': 12400,
        },
    }


@pytest.mark.asyncio
async def test_agent_mode_runtime_payload_preserves_reasoning_before_payload_processing(
    monkeypatch,
    agent_run_db,
    chat_entry_patches,
):
    async def fake_process_payload(request, form_data, user, metadata, model):
        form_data = dict(form_data)
        form_data.pop('reasoning', None)
        form_data.pop('params', None)
        return form_data, metadata, []

    monkeypatch.setattr(main, 'process_chat_payload', fake_process_payload)
    request = _request(enable_agent_mode=True)
    form = _chat_form()
    form['params'] = {'temperature': 0.2}
    form['reasoning'] = {
        'enabled': True,
        'effort': 'high',
        'max_tokens': 8126,
    }

    await main.chat_completion(request, form, _user())

    runtime_payload = chat_entry_patches.runtime_calls[0]
    assert runtime_payload['metadata']['model_params'] == {
        'temperature': 0.2,
        'reasoning': {
            'enabled': True,
            'effort': 'high',
            'max_tokens': 8126,
        },
    }


@pytest.mark.asyncio
async def test_agent_mode_runtime_payload_omits_empty_reasoning_effort(
    agent_run_db,
    chat_entry_patches,
):
    request = _request(enable_agent_mode=True)
    form = _chat_form()
    form['reasoning'] = {
        'enabled': True,
        'effort': '',
        'max_tokens': 8126,
    }

    await main.chat_completion(request, form, _user())

    runtime_payload = chat_entry_patches.runtime_calls[0]
    assert runtime_payload['metadata']['model_params']['reasoning'] == {
        'enabled': True,
        'max_tokens': 8126,
    }


@pytest.mark.asyncio
async def test_agent_mode_multimodel_binds_current_model_assistant_message(
    agent_run_db,
    chat_entry_patches,
):
    request = _request(enable_agent_mode=True)
    form = _chat_form()
    form['message_ids'] = [
        {'model_id': 'comparison-model', 'message_id': 'assistant-comparison'},
        {'model_id': 'model-a', 'message_id': 'assistant-current'},
    ]

    response = await main.chat_completion(request, form, _user())

    runs = await AgentRuns.list_runs_by_chat('chat-1', 'user-1')
    assert len(runs) == 1
    run = runs[0]
    assert run.assistant_message_id == 'assistant-current'
    assert run.leader_model_id == 'model-a'
    assert run.participants == [
        {
            'id': 'leader',
            'role': 'leader',
            'model_id': 'model-a',
        }
    ]
    assert response['agent_run_id'] == run.id
    runtime_payload = chat_entry_patches.runtime_calls[0]
    assert runtime_payload['assistant_message_id'] == 'assistant-current'
    assert runtime_payload['leader_model_id'] == 'model-a'
    assert runtime_payload['model_catalog'] == [
        {
            'id': 'model-a',
            'role': 'leader',
            'meta': {},
        }
    ]
    assert any(
        message_id == 'assistant-current' and message.get('agent_run_id') == run.id
        for _chat_id, message_id, message in chat_entry_patches.upserts
    )
    assert all(
        message_id != 'assistant-comparison' or message.get('agent_run_id') != run.id
        for _chat_id, message_id, message in chat_entry_patches.upserts
    )


@pytest.mark.asyncio
async def test_agent_mode_product_chat_auto_attaches_accessible_system_terminal(
    monkeypatch,
    agent_run_db,
    chat_entry_patches,
):
    async def fake_terminal_tool(command: str):
        return {'output': command}

    async def fake_has_connection_access(user, connection, user_group_ids=None):
        return connection.get('id') == 'terminal-system-2'

    async def fake_process_payload(request, form_data, user, metadata, model):
        terminal_id = form_data.get('terminal_id')
        chat_entry_patches.process_payload_calls.append(
            {
                'terminal_id': terminal_id,
                'form_data': dict(form_data),
                'metadata': dict(metadata),
            }
        )
        metadata['terminal_id'] = terminal_id
        if terminal_id:
            metadata['tools'] = {
                'run_command': {
                    'tool_id': f'terminal:{terminal_id}',
                    'callable': fake_terminal_tool,
                    'spec': {
                        'name': 'run_command',
                        'parameters': {
                            'type': 'object',
                            'properties': {'command': {'type': 'string'}},
                        },
                    },
                    'type': 'terminal',
                }
            }
        return form_data, metadata, []

    monkeypatch.setattr(main, 'has_connection_access', fake_has_connection_access, raising=False)
    monkeypatch.setattr(main, 'process_chat_payload', fake_process_payload)
    request = _request(enable_agent_mode=True)
    request.app.state.config.TERMINAL_SERVER_CONNECTIONS = [
        {'id': 'terminal-disabled', 'enabled': False, 'url': 'http://terminal-disabled.test'},
        {'id': 'terminal-system-1', 'enabled': True, 'url': 'http://terminal-one.test'},
        {'id': 'terminal-system-2', 'enabled': True, 'url': 'http://terminal-two.test'},
    ]

    response = await main.chat_completion(request, _chat_form(), _user())

    runtime_payload = chat_entry_patches.runtime_calls[0]
    assert response['status'] is True
    assert chat_entry_patches.process_payload_calls[0]['terminal_id'] == 'terminal-system-2'
    assert runtime_payload['tool_access_envelope']['metadata']['terminal_id'] == 'terminal-system-2'
    assert runtime_payload['tool_access_envelope']['tools'] == [
        {
            'id': 'tool:terminal:terminal-system-2:run_command',
            'name': 'run_command',
            'type': 'terminal',
            'schema': {
                'name': 'run_command',
                'parameters': {
                    'type': 'object',
                    'properties': {'command': {'type': 'string'}},
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_agent_mode_product_chat_populates_tool_envelope_and_callback_registry(
    monkeypatch,
    agent_run_db,
    chat_entry_patches,
):
    async def fake_tool(query: str):
        return {'answer': query}

    async def fake_process_payload(request, form_data, user, metadata, model):
        metadata['tools'] = {
            'lookup_fact': {
                'tool_id': 'builtin:lookup_fact',
                'callable': fake_tool,
                'spec': {
                    'name': 'lookup_fact',
                    'parameters': {
                        'type': 'object',
                        'properties': {'query': {'type': 'string'}},
                    },
                },
                'type': 'builtin',
            }
        }
        return form_data, metadata, []

    monkeypatch.setattr(main, 'process_chat_payload', fake_process_payload)
    request = _request(enable_agent_mode=True)

    response = await main.chat_completion(request, _chat_form(), _user())

    runs = await AgentRuns.list_runs_by_chat('chat-1', 'user-1')
    run = runs[0]
    runtime_payload = chat_entry_patches.runtime_calls[0]
    envelope_tools = runtime_payload['tool_access_envelope']['tools']
    assert response['agent_run_id'] == run.id
    assert envelope_tools == [
        {
            'id': 'tool:builtin:lookup_fact:lookup_fact',
            'name': 'lookup_fact',
            'type': 'builtin',
            'schema': {
                'name': 'lookup_fact',
                'parameters': {
                    'type': 'object',
                    'properties': {'query': {'type': 'string'}},
                },
            },
        }
    ]
    assert run.tool_access_snapshot == runtime_payload['tool_access_envelope']
    assert run.tool_access_snapshot['metadata'] == {'session_id': 'session-1', 'files': []}
    registry = request.app.state.AGENT_TOOL_REGISTRIES[run.id]
    assert registry['tool:builtin:lookup_fact:lookup_fact']['callable'] is fake_tool


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
    events = await AgentRuns.list_events(run.id)
    assert [(event.event_type, event.phase, event.payload) for event in events] == [
        (
            'run.failed',
            'failed',
            {
                'error': {
                    'code': 'agent_runtime_unavailable',
                    'message': 'agent runtime unavailable',
                }
            },
        )
    ]
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
async def test_agent_mode_runtime_unavailable_removes_run_tool_registry(
    monkeypatch,
    agent_run_db,
    chat_entry_patches,
):
    async def fake_tool(query: str):
        return {'answer': query}

    async def fake_process_payload(request, form_data, user, metadata, model):
        metadata['tools'] = {
            'lookup_fact': {
                'tool_id': 'builtin:lookup_fact',
                'callable': fake_tool,
                'spec': {'name': 'lookup_fact'},
                'type': 'builtin',
            }
        }
        return form_data, metadata, []

    class UnavailableRuntimeClient:
        def __init__(self, base_url, service_token=None, timeout=None):
            pass

        async def start_run(self, payload):
            raise main.AgentRuntimeUnavailable('agent runtime unavailable')

    monkeypatch.setattr(main, 'process_chat_payload', fake_process_payload)
    monkeypatch.setattr(main, 'AgentRuntimeClient', UnavailableRuntimeClient, raising=False)
    request = _request(enable_agent_mode=True)

    response = await main.chat_completion(request, _chat_form(), _user())

    assert response['status'] is False
    assert request.app.state.AGENT_TOOL_REGISTRIES == {}


@pytest.mark.asyncio
async def test_chat_stop_endpoint_cancels_active_agent_runs(
    monkeypatch,
    agent_run_db,
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

    async def fake_get_chat_by_id(chat_id):
        return SimpleNamespace(id=chat_id, user_id='user-1')

    async def fake_stop_item_tasks(redis, chat_id):
        return {'status': True, 'message': f'All tasks for item {chat_id} stopped.'}

    monkeypatch.setattr(agent_runs_router, 'AgentRuntimeClient', RuntimeClient, raising=False)
    monkeypatch.setattr(main.Chats, 'get_chat_by_id', fake_get_chat_by_id)
    monkeypatch.setattr(main, 'stop_item_tasks', fake_stop_item_tasks)
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='assistant-msg',
        leader_model_id='model-a',
    )
    await AgentRuns.transition_state(
        run.id,
        from_states=['queued'],
        to_state='running',
        reason='runtime accepted',
        payload={'runtime_session_id': 'runtime-session-1'},
    )

    response = await main.stop_tasks_by_chat_id_endpoint(_request(enable_agent_mode=True), 'chat-1', _user())

    assert response['status'] is True
    assert response['agent_run_ids'] == [run.id]
    assert runtime_cancels == [run.id]
    updated = await AgentRuns.get_run(run.id)
    assert updated is not None
    assert updated.state == 'cancelled'
    events = await AgentRuns.list_events(run.id)
    assert [event.event_type for event in events] == ['run.cancelled']


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
