from __future__ import annotations

import importlib
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from open_webui.agent.conversation_mode_profile_service import (
    ModeProfileCapabilityRequestError,
    ModeProfileCapabilityResolution,
    ModeProfileRuntimeWarning,
)
from open_webui.agent.conversation_mode_profiles import (
    ConversationModeProfile,
    ProfileDefaults,
)
from open_webui.internal.db import Base
from open_webui.models.agent_runs import AgentRuns
from open_webui.models.conversation_mode_profiles import (
    ConversationModeProfileIntegrityError,
    ConversationModeProfileRevisionModel,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

main = importlib.import_module('open_webui.main')


def _revision(
    revision_id: str,
    mode: str,
    *,
    administrator_prompt: str = '',
    defaults: ProfileDefaults | None = None,
) -> ConversationModeProfileRevisionModel:
    profile = ConversationModeProfile(
        mode=mode,
        schema_version=1,
        system_prompt=administrator_prompt,
        defaults=defaults or ProfileDefaults(),
    )
    return ConversationModeProfileRevisionModel(
        id=revision_id,
        mode=mode,
        revision_number=1,
        schema_version=1,
        system_prompt=administrator_prompt,
        defaults=profile.defaults,
        content_hash=profile.content_hash,
        created_at=1,
        created_by='admin-1',
        restored_from_revision_id=None,
    )


@pytest_asyncio.fixture
async def agent_run_db(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

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
def profile_entry(monkeypatch):  # noqa: C901
    calls = SimpleNamespace(
        events=[],
        provider_calls=[],
        runtime_calls=[],
        upserts=[],
        process_payload_calls=[],
        chat_inserts=[],
        stored_chats={
            'chat-1': SimpleNamespace(
                id='chat-1',
                user_id='user-1',
                mode_profile_revision_id='agent-old',
                chat={
                    'id': 'chat-1',
                    'mode': 'agent',
                    'history': {'currentId': None, 'messages': {}},
                },
            )
        },
        current={
            'chat': _revision('chat-current', 'chat'),
            'agent': _revision('agent-current', 'agent'),
        },
        revisions={
            'chat-current': _revision('chat-current', 'chat'),
            'chat-old': _revision('chat-old', 'chat'),
            'agent-current': _revision('agent-current', 'agent'),
            'agent-old': _revision('agent-old', 'agent'),
        },
        config_values={
            'models.default_params': {},
            'chat.global_system_prompt': '',
        },
        model_info=None,
        capability_resolution=None,
    )

    async def get_model_by_id(model_id):
        return calls.model_info

    async def config_get(key, default=None):
        return calls.config_values.get(key, default)

    async def get_current(app, mode):
        calls.events.append(('current_revision', str(mode)))
        return calls.current[str(mode)]

    async def get_revision(app, revision_id, *, expected_mode=None):
        calls.events.append(('bound_revision', revision_id, str(expected_mode)))
        return calls.revisions.get(revision_id)

    async def resolve_capabilities(
        app,
        *,
        profile_defaults,
        model,
        user,
        request_values,
    ):
        calls.events.append(('capability_resolution', dict(request_values)))
        if isinstance(calls.capability_resolution, Exception):
            raise calls.capability_resolution
        if calls.capability_resolution is not None:
            return calls.capability_resolution
        features = request_values.get('features')
        return ModeProfileCapabilityResolution(
            terminal_id=request_values.get('terminal_id'),
            tool_ids=list(request_values.get('tool_ids') or []),
            skill_ids=list(request_values.get('skill_ids') or []),
            filter_ids=list(request_values.get('filter_ids') or []),
            feature_ids=[
                feature_id
                for feature_id in ('web_search', 'code_interpreter', 'image_generation')
                if isinstance(features, dict) and features.get(feature_id) is True
            ],
        )

    async def is_owner(chat_id, user_id):
        return True

    async def get_chat(chat_id):
        return calls.stored_chats.get(chat_id)

    async def claim_mode(chat_id, *, requested, user_id, has_agent_run, db=None):
        stored = calls.stored_chats.get(chat_id)
        resolution = main.resolve_conversation_mode(
            requested=requested,
            persisted=stored.chat.get('mode'),
            is_new=False,
            has_agent_run=has_agent_run,
        )
        calls.events.append(('mode_claim', resolution.mode.value))
        return stored, resolution

    async def insert_chat(
        chat_id,
        user_id,
        form_data,
        db=None,
        *,
        mode_profile_revision_id=None,
    ):
        calls.events.append(('chat_insert', mode_profile_revision_id))
        stored = SimpleNamespace(
            id=chat_id,
            user_id=user_id,
            mode_profile_revision_id=mode_profile_revision_id,
            chat=form_data.chat,
        )
        calls.chat_inserts.append(stored)
        calls.stored_chats[chat_id] = stored
        return stored

    async def get_message(chat_id, message_id):
        if message_id == 'user-msg':
            return {'id': message_id, 'childrenIds': []}
        return None

    async def upsert(chat_id, message_id, message):
        calls.events.append(('message_write', message_id))
        calls.upserts.append((chat_id, message_id, message))
        return message

    async def publish(*args, **kwargs):
        return None

    async def process_payload(request, form_data, user, metadata, model):
        calls.process_payload_calls.append(
            {
                'form_data': dict(form_data),
                'metadata': dict(metadata),
            }
        )
        return form_data, metadata, []

    async def provider(request, form_data, user, **kwargs):
        calls.events.append(('provider', None))
        calls.provider_calls.append((form_data, kwargs))
        return {'provider': True}

    async def build_context(request, form_data, user, model, metadata, tasks, events):
        return {'metadata': metadata}

    async def process_response(response, ctx):
        return {'legacy': True, 'provider_response': response}

    original_create_run = AgentRuns.create_run

    async def create_run(*args, **kwargs):
        calls.events.append(('agent_run', kwargs.get('chat_id')))
        return await original_create_run(*args, **kwargs)

    class RuntimeClient:
        def __init__(self, base_url, service_token=None, timeout=None):
            pass

        async def start_run(self, payload):
            calls.events.append(('runtime', payload['chat_id']))
            calls.runtime_calls.append(payload)
            await AgentRuns.append_event(
                payload['run_id'],
                event_type='run.running',
                participant_id='leader',
                phase='running',
                summary='accepted',
                payload={'runtime_session_id': 'runtime-session-1'},
            )
            return {'accepted': True, 'runtime_session_id': 'runtime-session-1'}

    monkeypatch.setattr(main.Models, 'get_model_by_id', get_model_by_id)
    monkeypatch.setattr(main.Config, 'get', config_get)
    monkeypatch.setattr(main, 'get_cached_current_revision', get_current, raising=False)
    monkeypatch.setattr(main, 'get_cached_revision', get_revision, raising=False)
    monkeypatch.setattr(
        main,
        'resolve_mode_profile_capabilities',
        resolve_capabilities,
        raising=False,
    )
    monkeypatch.setattr(main.Chats, 'is_chat_owner', is_owner)
    monkeypatch.setattr(main.Chats, 'get_chat_by_id', get_chat)
    monkeypatch.setattr(main.Chats, 'claim_conversation_mode', claim_mode)
    monkeypatch.setattr(main.Chats, 'insert_new_chat', insert_chat)
    monkeypatch.setattr(main.Chats, 'get_message_by_id_and_message_id', get_message)
    monkeypatch.setattr(main.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
    monkeypatch.setattr(main, 'publish_event', publish, raising=False)
    monkeypatch.setattr(main, 'process_chat_payload', process_payload)
    monkeypatch.setattr(main, 'chat_completion_handler', provider)
    monkeypatch.setattr(main, 'build_chat_response_context', build_context)
    monkeypatch.setattr(main, 'process_chat_response', process_response)
    monkeypatch.setattr(main.AgentRuns, 'create_run', create_run)
    monkeypatch.setattr(main, 'AgentRuntimeClient', RuntimeClient, raising=False)
    return calls


@pytest.mark.asyncio
async def test_new_chat_binds_current_chat_revision_before_dispatch(
    agent_run_db,
    profile_entry,
):
    response = await main.chat_completion(
        _request(enable_agent_mode=True),
        _new_chat_form(mode='chat'),
        _user(),
    )

    assert response['legacy'] is True
    assert profile_entry.chat_inserts[0].mode_profile_revision_id == 'chat-current'
    assert profile_entry.events.index(('chat_insert', 'chat-current')) < profile_entry.events.index(('provider', None))


@pytest.mark.asyncio
async def test_new_agent_binds_current_agent_revision_before_run_and_runtime(
    agent_run_db,
    profile_entry,
):
    response = await main.chat_completion(
        _request(enable_agent_mode=True),
        _new_chat_form(mode='agent'),
        _user(),
    )

    assert response['status'] is True
    assert profile_entry.chat_inserts[0].mode_profile_revision_id == 'agent-current'
    insert_index = profile_entry.events.index(('chat_insert', 'agent-current'))
    assert insert_index < profile_entry.events.index(('agent_run', profile_entry.chat_inserts[0].id))
    assert insert_index < profile_entry.events.index(('runtime', profile_entry.chat_inserts[0].id))


@pytest.mark.asyncio
async def test_stale_new_chat_revision_hint_cannot_select_old_revision(
    agent_run_db,
    profile_entry,
):
    form = _new_chat_form(mode='chat')
    form['mode_profile_revision_id'] = 'chat-old'

    with pytest.raises(main.HTTPException) as exc_info:
        await main.chat_completion(_request(enable_agent_mode=True), form, _user())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail['code'] == 'mode_profile_revision_conflict'
    assert exc_info.value.detail['current_revision_id'] == 'chat-current'
    assert profile_entry.chat_inserts == []
    assert profile_entry.provider_calls == []


@pytest.mark.asyncio
async def test_missing_current_revision_is_mode_profile_unavailable_before_writes(
    monkeypatch,
    agent_run_db,
    profile_entry,
):
    async def missing_current(app, mode):
        return None

    monkeypatch.setattr(main, 'get_cached_current_revision', missing_current)

    with pytest.raises(main.HTTPException) as exc_info:
        await main.chat_completion(
            _request(enable_agent_mode=True),
            _new_chat_form(mode='chat'),
            _user(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail['code'] == 'mode_profile_unavailable'
    assert profile_entry.chat_inserts == []
    assert profile_entry.provider_calls == []


@pytest.mark.asyncio
async def test_existing_chat_keeps_bound_old_revision_after_head_switch(
    agent_run_db,
    profile_entry,
):
    profile_entry.stored_chats['chat-1'] = SimpleNamespace(
        id='chat-1',
        user_id='user-1',
        mode_profile_revision_id='chat-old',
        chat={'id': 'chat-1', 'mode': 'chat', 'history': {'currentId': None, 'messages': {}}},
    )
    profile_entry.current['chat'] = _revision('chat-current', 'chat')

    response = await main.chat_completion(
        _request(enable_agent_mode=True),
        _existing_chat_form(mode='chat'),
        _user(),
    )

    assert response['legacy'] is True
    assert ('bound_revision', 'chat-old', 'chat') in profile_entry.events
    assert ('current_revision', 'chat') not in profile_entry.events


@pytest.mark.asyncio
async def test_existing_binding_rejects_request_hint_mismatch_before_writes(
    agent_run_db,
    profile_entry,
):
    form = _existing_chat_form(mode='agent')
    form['mode_profile_revision_id'] = 'agent-current'

    with pytest.raises(main.HTTPException) as exc_info:
        await main.chat_completion(_request(enable_agent_mode=True), form, _user())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail['code'] == 'mode_profile_binding_mismatch'
    assert profile_entry.upserts == []
    assert profile_entry.runtime_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'mode_mismatch'])
async def test_bound_revision_integrity_failure_precedes_messages_runs_and_calls(
    monkeypatch,
    agent_run_db,
    profile_entry,
    failure,
):
    async def broken_revision(app, revision_id, *, expected_mode=None):
        if failure == 'missing':
            return None
        if failure == 'mode_mismatch':
            return _revision(revision_id, 'chat')
        raise ConversationModeProfileIntegrityError(revision_id, 'corrupt persisted revision')

    monkeypatch.setattr(main, 'get_cached_revision', broken_revision, raising=False)

    with pytest.raises(main.HTTPException) as exc_info:
        await main.chat_completion(
            _request(enable_agent_mode=True),
            _existing_chat_form(mode='agent'),
            _user(),
        )

    assert exc_info.value.detail['code'] == 'mode_profile_integrity_error'
    assert profile_entry.upserts == []
    assert not any(event[0] in {'agent_run', 'runtime', 'provider'} for event in profile_entry.events)


@pytest.mark.asyncio
async def test_chat_prompt_order_is_administrator_model_global_then_user(
    agent_run_db,
    profile_entry,
):
    _configure_prompt_layers(profile_entry, mode='chat', administrator='Administrator prompt.')
    form = _existing_chat_form(mode='chat')
    form['params'] = {'system': 'User parameter prompt.', 'temperature': 0.2}
    form['messages'] = [
        {'role': 'system', 'content': 'User message prompt.'},
        {'role': 'user', 'content': 'hello'},
    ]

    await main.chat_completion(_request(enable_agent_mode=True), form, _user())

    provider_form, provider_kwargs = profile_entry.provider_calls[0]
    assert _system_content(provider_form['messages']) == (
        'Administrator prompt.\n\nGlobal prompt.\n\nModel prompt.\n\nUser parameter prompt.\n\nUser message prompt.'
    )
    assert provider_form['params'] == {
        'temperature': 0.2,
        'top_p': 0.8,
        'max_tokens': 100,
    }
    assert provider_kwargs == {
        'bypass_system_prompt': True,
        'bypass_global_system_prompt': True,
    }
    assert 'Administrator prompt.' not in repr(provider_form.get('metadata'))


@pytest.mark.asyncio
async def test_agent_prompt_order_matches_chat_and_preserves_non_system_precedence(
    agent_run_db,
    profile_entry,
):
    _configure_prompt_layers(profile_entry, mode='agent', administrator='Administrator prompt.')
    form = _existing_chat_form(mode='agent')
    form['params'] = {'system': 'User parameter prompt.', 'temperature': 0.2}
    form['messages'] = [
        {'role': 'system', 'content': 'User message prompt.'},
        {'role': 'user', 'content': 'hello'},
    ]

    await main.chat_completion(_request(enable_agent_mode=True), form, _user())

    runtime_payload = profile_entry.runtime_calls[0]
    assert _system_content(runtime_payload['messages']) == (
        'Administrator prompt.\n\nGlobal prompt.\n\nModel prompt.\n\nUser parameter prompt.\n\nUser message prompt.'
    )
    assert runtime_payload['metadata']['model_params'] == {
        'temperature': 0.2,
        'top_p': 0.8,
        'max_tokens': 100,
    }
    assert 'Administrator prompt.' not in repr(runtime_payload['metadata'])


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['chat', 'agent'])
async def test_empty_administrator_prompt_preserves_previous_payload_behavior(
    agent_run_db,
    profile_entry,
    mode,
):
    _configure_prompt_layers(profile_entry, mode=mode, administrator='')
    form = _existing_chat_form(mode=mode)
    form['params'] = {'system': 'User parameter prompt.', 'temperature': 0.2}
    form['messages'] = [
        {'role': 'system', 'content': 'User message prompt.'},
        {'role': 'user', 'content': 'hello'},
    ]

    await main.chat_completion(_request(enable_agent_mode=True), form, _user())

    if mode == 'chat':
        provider_form, provider_kwargs = profile_entry.provider_calls[0]
        assert provider_form['messages'] == form['messages']
        assert provider_form['params']['system'] == 'User parameter prompt.'
        assert provider_kwargs == {}
    else:
        runtime_payload = profile_entry.runtime_calls[0]
        assert runtime_payload['messages'] == form['messages']
        assert runtime_payload['metadata']['model_params']['system'] == 'User parameter prompt.'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['chat', 'agent'])
async def test_profile_hint_and_control_fields_are_stripped_before_dispatch(
    agent_run_db,
    profile_entry,
    mode,
):
    _configure_prompt_layers(profile_entry, mode=mode, administrator='Administrator prompt.')
    form = _existing_chat_form(mode=mode)
    form['mode_profile_revision_id'] = f'{mode}-old'
    form['mode_profile_system_prompt'] = 'Client administrator replacement.'
    form['params'] = {
        'temperature': 0.2,
        'mode_profile_revision_id': f'{mode}-old',
        'mode_profile_system_prompt': 'Client nested replacement.',
    }

    await main.chat_completion(_request(enable_agent_mode=True), form, _user())

    dispatched = profile_entry.provider_calls[0][0] if mode == 'chat' else profile_entry.runtime_calls[0]
    assert not _contains_profile_control(dispatched)
    assert 'Client administrator replacement.' not in repr(dispatched)
    assert 'Client nested replacement.' not in repr(dispatched)
    messages = dispatched['messages']
    assert _system_content(messages).startswith('Administrator prompt.')


@pytest.mark.asyncio
async def test_new_chat_does_not_persist_administrator_prompt(
    agent_run_db,
    profile_entry,
):
    profile_entry.current['chat'] = _revision(
        'chat-current',
        'chat',
        administrator_prompt='Administrator prompt.',
    )
    profile_entry.revisions['chat-current'] = profile_entry.current['chat']

    await main.chat_completion(
        _request(enable_agent_mode=True),
        _new_chat_form(mode='chat'),
        _user(),
    )

    assert 'Administrator prompt.' not in repr(profile_entry.chat_inserts[0].chat)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['chat', 'agent'])
async def test_live_filtered_capabilities_and_warnings_reach_each_dispatch_path(
    agent_run_db,
    profile_entry,
    mode,
):
    _configure_prompt_layers(profile_entry, mode=mode, administrator='Administrator prompt.')
    profile_entry.capability_resolution = ModeProfileCapabilityResolution(
        terminal_id=None,
        tool_ids=['tool-keep'],
        skill_ids=['skill-keep'],
        filter_ids=['filter-keep'],
        feature_ids=['web_search'],
        warnings=[
            ModeProfileRuntimeWarning(
                category='tools',
                reason='unavailable',
                resource_ids=['tool-drop'],
            ),
            ModeProfileRuntimeWarning(
                category='skills',
                reason='inactive',
                resource_ids=['skill-drop'],
            ),
        ],
    )
    form = _existing_chat_form(mode=mode)
    form.update(
        {
            'tool_ids': ['tool-keep', 'tool-drop'],
            'skill_ids': ['skill-keep', 'skill-drop'],
            'filter_ids': ['filter-keep', 'filter-drop'],
            'terminal_id': 'terminal-drop',
            'features': {
                'web_search': True,
                'code_interpreter': True,
                'voice': True,
            },
        }
    )

    await main.chat_completion(_request(enable_agent_mode=True), form, _user())

    processed = profile_entry.process_payload_calls[0]
    assert processed['form_data']['tool_ids'] == ['tool-keep']
    assert processed['form_data']['skill_ids'] == ['skill-keep']
    assert processed['form_data'].get('terminal_id') is None
    assert processed['metadata']['filter_ids'] == ['filter-keep']
    assert processed['form_data']['features'] == {
        'web_search': True,
        'code_interpreter': False,
        'voice': True,
        'image_generation': False,
    }
    warnings = processed['metadata']['mode_profile_warnings']
    assert warnings == [
        {
            'code': 'mode_profile_capability_omitted',
            'category': 'tools',
            'reason': 'unavailable',
            'resource_ids': ['tool-drop'],
        },
        {
            'code': 'mode_profile_capability_omitted',
            'category': 'skills',
            'reason': 'inactive',
            'resource_ids': ['skill-drop'],
        },
    ]
    if mode == 'chat':
        assert profile_entry.provider_calls[0][0]['metadata']['mode_profile_warnings'] == warnings
    else:
        assert profile_entry.runtime_calls[0]['metadata']['mode_profile_warnings'] == warnings


@pytest.mark.asyncio
async def test_capability_request_error_is_stable_400_before_any_write(
    agent_run_db,
    profile_entry,
):
    _configure_prompt_layers(profile_entry, mode='chat', administrator='Administrator prompt.')
    profile_entry.capability_resolution = ModeProfileCapabilityRequestError(
        reason='duplicate_identifier',
        field='tool_ids',
    )
    form = _existing_chat_form(mode='chat')
    form['tool_ids'] = ['tool-1', 'tool-1']

    with pytest.raises(main.HTTPException) as exc_info:
        await main.chat_completion(_request(enable_agent_mode=True), form, _user())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        'code': 'invalid_mode_profile_capability_request',
        'reason': 'duplicate_identifier',
        'field': 'tool_ids',
        'message': 'The requested conversation capabilities are invalid.',
    }
    assert profile_entry.upserts == []
    assert profile_entry.provider_calls == []
    assert profile_entry.runtime_calls == []


@pytest.mark.asyncio
async def test_capability_truth_failure_is_non_secret_unavailable_before_writes(
    agent_run_db,
    profile_entry,
):
    _configure_prompt_layers(
        profile_entry,
        mode='chat',
        administrator='Administrator prompt.',
    )
    profile_entry.capability_resolution = RuntimeError('private database detail')

    with pytest.raises(main.HTTPException) as exc_info:
        await main.chat_completion(
            _request(enable_agent_mode=True),
            _existing_chat_form(mode='chat'),
            _user(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        'code': 'mode_profile_unavailable',
        'message': 'Conversation capability truth is unavailable.',
    }
    assert 'private database detail' not in repr(exc_info.value.detail)
    assert profile_entry.upserts == []
    assert profile_entry.provider_calls == []


@pytest.mark.asyncio
async def test_profile_filtered_skills_are_not_readded_from_model_defaults(
    monkeypatch,
    agent_run_db,
    profile_entry,
):
    revision = _revision(
        'chat-old',
        'chat',
        administrator_prompt='Administrator prompt.',
        defaults=ProfileDefaults(skill_ids=()),
    )
    profile_entry.revisions['chat-old'] = revision
    profile_entry.stored_chats['chat-1'] = SimpleNamespace(
        id='chat-1',
        user_id='user-1',
        mode_profile_revision_id='chat-old',
        chat={
            'id': 'chat-1',
            'mode': 'chat',
            'history': {'currentId': None, 'messages': {}},
        },
    )
    profile_entry.capability_resolution = ModeProfileCapabilityResolution(skill_ids=[])
    request = _request(enable_agent_mode=True)
    request.app.state.MODELS['model-a']['info']['meta']['skillIds'] = ['model-skill']

    async def process_payload(request, form_data, user, metadata, model):
        form_data = dict(form_data)
        form_data['skill_ids'] = [
            *form_data.get('skill_ids', []),
            *model.get('info', {}).get('meta', {}).get('skillIds', []),
        ]
        return form_data, metadata, []

    monkeypatch.setattr(main, 'process_chat_payload', process_payload)

    await main.chat_completion(request, _existing_chat_form(mode='chat'), _user())

    provider_form, _kwargs = profile_entry.provider_calls[0]
    assert provider_form['skill_ids'] == []


def _configure_prompt_layers(profile_entry, *, mode: str, administrator: str) -> None:
    revision_id = f'{mode}-old'
    revision = _revision(
        revision_id,
        mode,
        administrator_prompt=administrator,
    )
    profile_entry.revisions[revision_id] = revision
    profile_entry.stored_chats['chat-1'] = SimpleNamespace(
        id='chat-1',
        user_id='user-1',
        mode_profile_revision_id=revision_id,
        chat={
            'id': 'chat-1',
            'mode': mode,
            'history': {'currentId': None, 'messages': {}},
        },
    )
    profile_entry.config_values.update(
        {
            'models.default_params': {
                'system': 'Default prompt.',
                'temperature': 0.9,
                'max_tokens': 100,
            },
            'chat.global_system_prompt': 'Global prompt.',
        }
    )
    profile_entry.model_info = SimpleNamespace(
        base_model_id=None,
        params=SimpleNamespace(
            model_dump=lambda: {
                'system': 'Model prompt.',
                'temperature': 0.4,
                'top_p': 0.8,
            }
        ),
    )


def _system_content(messages: list[dict]) -> str | None:
    return next(
        (
            message.get('content')
            for message in messages
            if isinstance(message, dict) and message.get('role') == 'system'
        ),
        None,
    )


def _contains_profile_control(value) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).startswith(('mode_profile_', 'conversation_mode_profile_')) or _contains_profile_control(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_profile_control(item) for item in value)
    return False


def _request(*, enable_agent_mode: bool):
    model = {'id': 'model-a', 'name': 'Model A', 'info': {'meta': {}}}
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={'model-a': model},
                config=SimpleNamespace(
                    ENABLE_AGENT_MODE=enable_agent_mode,
                    AGENT_RUNTIME_BASE_URL='http://agent-runtime.test',
                    AGENT_RUNTIME_SERVICE_TOKEN='test-service-token',
                    AGENT_RUN_DEFAULT_TIMEOUT_SECONDS=30,
                    AGENT_RUN_MAX_MODEL_CALLS=8,
                    AGENT_RUN_MAX_TOOL_CALLS=12,
                    AGENT_TEAM_MAX_SUBAGENTS=5,
                    AGENT_SUBAGENT_DEFAULT_BUDGET={
                        'max_model_calls': 2,
                        'max_tool_calls': 3,
                    },
                    USER_PERMISSIONS={},
                ),
                redis=None,
            )
        ),
        state=SimpleNamespace(),
        headers={},
    )


def _user():
    return SimpleNamespace(id='user-1', role='admin')


def _new_chat_form(*, mode: str):
    form = _existing_chat_form(mode=mode)
    form.pop('chat_id')
    form['parent_id'] = None
    return form


def _existing_chat_form(*, mode: str):
    return {
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
        'messages': [{'role': 'user', 'content': 'hello'}],
        'stream': True,
        'chat_mode': mode,
    }
