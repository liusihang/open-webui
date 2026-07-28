import inspect
from types import SimpleNamespace

import pytest
from open_webui import functions
from open_webui.routers import ollama, openai
from open_webui.utils import chat


def test_openai_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(openai.generate_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source


def test_openai_responses_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(openai.responses)

    assert 'apply_model_system_prompt_to_responses_body' in source


def test_ollama_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(ollama.generate_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source


def test_ollama_openai_compatible_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(ollama.generate_openai_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source
    assert source.index('system = None') < source.index('if model_info is not None:')


def test_ollama_native_protocol_dispatches_use_global_model_system_prompt_helpers():
    responses_source = inspect.getsource(ollama.generate_responses)
    anthropic_source = inspect.getsource(ollama.generate_anthropic_messages)

    assert 'apply_model_system_prompt_to_responses_body' in responses_source
    assert 'apply_model_system_prompt_to_anthropic_body' in anthropic_source


def test_function_pipe_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(functions.generate_function_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source


def test_direct_connection_dispatch_uses_global_model_system_prompt_helper():
    source = inspect.getsource(chat.generate_direct_chat_completion)

    assert 'apply_model_system_prompt_to_body' in source


@pytest.mark.asyncio
async def test_direct_connection_continuation_respects_bypass_system_prompt(monkeypatch):
    calls = []

    async def fake_apply_model_system_prompt(*args, **kwargs):
        calls.append((args, kwargs))
        return args[1]

    async def fake_get_event_call(metadata):
        return None

    monkeypatch.setattr(chat, 'apply_model_system_prompt_to_body', fake_apply_model_system_prompt)
    monkeypatch.setattr(chat, 'get_event_call', fake_get_event_call)
    request = SimpleNamespace(
        state=SimpleNamespace(bypass_system_prompt=True),
    )

    with pytest.raises(Exception, match='active WebSocket session'):
        await chat.generate_direct_chat_completion(
            request,
            {
                'model': 'direct-model',
                'messages': [{'role': 'system', 'content': 'Already composed.'}],
                'metadata': {'user_id': 'user-1', 'session_id': 'session-1'},
            },
            SimpleNamespace(),
            {'direct-model': {'id': 'direct-model'}},
        )

    assert calls == []


@pytest.mark.asyncio
async def test_external_dispatch_does_not_trust_task_metadata_for_global_prompt_bypass(monkeypatch):
    captured = {}

    async def fake_openai_dispatch(request, form_data, user):
        captured['bypass_global_system_prompt'] = request.state.bypass_global_system_prompt
        return {'ok': True}

    monkeypatch.setattr(chat, 'generate_openai_chat_completion', fake_openai_dispatch)
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={
                    'model-a': {
                        'id': 'model-a',
                        'owned_by': 'openai',
                    }
                }
            )
        ),
    )

    result = await chat.generate_chat_completion(
        request,
        {
            'model': 'model-a',
            'messages': [{'role': 'user', 'content': 'Generate a title'}],
            'metadata': {'task': 'title_generation'},
        },
        SimpleNamespace(role='admin'),
    )

    assert result == {'ok': True}
    assert captured['bypass_global_system_prompt'] is False


@pytest.mark.asyncio
async def test_internal_task_dispatch_sets_explicit_global_prompt_bypass(monkeypatch):
    captured = {}

    async def fake_openai_dispatch(request, form_data, user):
        captured['bypass_global_system_prompt'] = request.state.bypass_global_system_prompt
        return {'ok': True}

    monkeypatch.setattr(chat, 'generate_openai_chat_completion', fake_openai_dispatch)
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={
                    'model-a': {
                        'id': 'model-a',
                        'owned_by': 'openai',
                    }
                }
            )
        ),
    )

    result = await chat.generate_chat_completion(
        request,
        {
            'model': 'model-a',
            'messages': [{'role': 'user', 'content': 'Generate a title'}],
            'metadata': {'task': 'title_generation'},
        },
        SimpleNamespace(role='admin'),
        bypass_global_system_prompt=True,
    )

    assert result == {'ok': True}
    assert captured['bypass_global_system_prompt'] is True
