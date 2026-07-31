from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from open_webui import events
from open_webui.routers import ollama, openai
from open_webui.utils import chat as chat_utils
from open_webui.utils import middleware


class _EventSink:
    def __init__(self):
        self.events = []

    async def handle_event(self, app, event, request=None):
        self.events.append(event)


@pytest.mark.asyncio
async def test_build_chat_response_context_passes_request_scoped_secrets_to_socket(
    monkeypatch,
):
    secret = 'request-scoped-socket-secret'
    received = {'emitter': None, 'caller': None}

    async def get_emitter(metadata, update_db=True, redaction_secrets=()):
        received['emitter'] = tuple(redaction_secrets)
        return None

    async def get_call(metadata, redaction_secrets=()):
        received['caller'] = tuple(redaction_secrets)
        return None

    monkeypatch.setattr(middleware, 'get_event_emitter', get_emitter)
    monkeypatch.setattr(middleware, 'get_event_call', get_call)
    request = SimpleNamespace(state=SimpleNamespace(prompt_redaction_secrets=(secret,)))
    metadata = {
        'user_id': 'user-1',
        'chat_id': 'chat-1',
        'message_id': 'message-1',
        'session_id': 'session-1',
    }

    await middleware.build_chat_response_context(
        request,
        {},
        SimpleNamespace(),
        {},
        metadata,
        None,
        [],
    )

    assert received == {'emitter': (secret,), 'caller': (secret,)}


@pytest.mark.asyncio
async def test_utils_chat_debug_form_sink_redacts_request_scoped_prompt(caplog):
    secret = 'resolved-debug-prompt-value'
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(MODELS={})),
        state=SimpleNamespace(prompt_redaction_secrets=(secret,)),
    )
    caplog.set_level('DEBUG', logger=chat_utils.__name__)

    with pytest.raises(Exception, match='Model not found'):
        await chat_utils.generate_chat_completion(
            request,
            {
                'model': 'missing-model',
                'messages': [{'role': 'system', 'content': secret}],
            },
            SimpleNamespace(id='user-1', role='admin'),
        )

    assert secret not in caplog.text
    assert '[administrator prompt redacted]' in caplog.text


@pytest.mark.asyncio
async def test_openai_provider_error_log_response_and_event_are_request_redacted(
    monkeypatch,
    caplog,
):
    secret = 'resolved-openai-provider-value\nsecond-line'
    response = SimpleNamespace(
        status=400,
        headers={'Content-Type': 'text/event-stream'},
        text=lambda: None,
    )

    async def response_text():
        return json.dumps({'error': {'message': json.dumps(secret)[1:-1]}})

    response.text = response_text

    class Session:
        async def request(self, **kwargs):
            return response

    async def no_model(model_id):
        return None

    async def no_access(*args, **kwargs):
        return None

    async def connection(idx):
        return 'http://provider.test', 'provider-key', {}

    async def headers(*args, **kwargs):
        return {}, {}

    async def session():
        return Session()

    async def cleanup(value):
        return None

    async def config_get(key, default=None):
        assert key == 'openai.enable'
        return True

    sink = _EventSink()
    monkeypatch.setattr(events, 'EVENT_SINKS', [sink])
    monkeypatch.setattr(openai.Models, 'get_model_by_id', no_model)
    monkeypatch.setattr(openai, 'check_model_access', no_access)
    monkeypatch.setattr(openai, 'get_openai_connection', connection)
    monkeypatch.setattr(openai, 'get_headers_and_cookies', headers)
    monkeypatch.setattr(openai, 'get_session', session)
    monkeypatch.setattr(openai, 'cleanup_response', cleanup)
    monkeypatch.setattr(openai.Config, 'get', config_get)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                OPENAI_MODELS={'model-a': {'urlIdx': 0}},
            )
        ),
        state=SimpleNamespace(
            bypass_filter=True,
            bypass_system_prompt=True,
            prompt_redaction_secrets=(secret,),
        ),
    )
    user = SimpleNamespace(id='user-1', role='admin')
    caplog.set_level('ERROR', logger=openai.__name__)

    result = await openai.generate_chat_completion(
        request,
        {
            'model': 'model-a',
            'messages': [{'role': 'system', 'content': secret}],
        },
        user,
    )

    failure_state = {
        'response': result.body.decode(),
        'events': sink.events,
        'logs': caplog.text,
    }
    assert 'resolved-openai-provider-value' not in repr(failure_state)
    assert '[administrator prompt redacted]' in repr(failure_state)


@pytest.mark.asyncio
async def test_ollama_provider_error_and_event_are_request_redacted(
    monkeypatch,
):
    secret = 'resolved-ollama-provider-value'
    response = SimpleNamespace(
        ok=False,
        status=400,
        headers={},
    )

    async def response_json():
        return {'error': f'provider rejected {secret}'}

    response.json = response_json

    class Session:
        async def request(self, *args, **kwargs):
            return response

    async def session():
        return Session()

    async def cleanup(value):
        return None

    sink = _EventSink()
    monkeypatch.setattr(events, 'EVENT_SINKS', [sink])
    monkeypatch.setattr(ollama, 'get_session', session)
    monkeypatch.setattr(ollama, 'cleanup_response', cleanup)

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace()),
        state=SimpleNamespace(prompt_redaction_secrets=(secret,)),
    )

    with pytest.raises(ollama.HTTPException) as exc_info:
        await ollama.send_request(
            'http://ollama.test/api/chat',
            payload='{}',
            user=SimpleNamespace(id='user-1', role='admin'),
            request=request,
        )

    failure_state = {
        'detail': exc_info.value.detail,
        'events': sink.events,
    }
    assert secret not in repr(failure_state)
    assert '[administrator prompt redacted]' in repr(failure_state)
