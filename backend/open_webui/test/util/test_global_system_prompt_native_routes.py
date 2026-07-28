import json
from types import SimpleNamespace

import pytest
from open_webui.routers import ollama, openai


class Params:
    def model_dump(self):
        return {'system': 'Model policy.'}


def model_info():
    return SimpleNamespace(base_model_id=None, params=Params())


def user():
    return SimpleNamespace(id='user-1', role='admin', name='Admin', email='admin@example.com')


@pytest.mark.asyncio
async def test_ollama_responses_route_composes_global_before_model_and_request(monkeypatch):
    captured = {}

    async def config_get(key, default=None):
        if key == 'ollama.enable':
            return True
        if key == 'ollama.api_configs':
            return {'0': {}}
        if key == 'chat.global_system_prompt':
            return 'Administrator policy.'
        return default

    async def send_request(url, **kwargs):
        captured['payload'] = json.loads(kwargs['payload'])
        return {'ok': True}

    monkeypatch.setattr(ollama.Config, 'get', config_get)

    async def get_model(model_id):
        return model_info()

    monkeypatch.setattr(ollama.Models, 'get_model_by_id', get_model)

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(ollama, 'check_model_access', allow_access)

    async def get_url(request, model, url_idx, user):
        return 'http://ollama', 0

    monkeypatch.setattr(ollama, 'get_ollama_url', get_url)
    monkeypatch.setattr(ollama, 'get_api_key', lambda *args, **kwargs: '')
    monkeypatch.setattr(ollama, 'send_request', send_request)
    request = SimpleNamespace(state=SimpleNamespace())

    result = await ollama.generate_responses(
        request,
        ollama.ResponsesForm(
            model='model-a',
            instructions='Request policy.',
            input='Hello',
        ),
        user=user(),
    )

    assert result == {'ok': True}
    assert captured['payload']['instructions'] == (
        '[ADMINISTRATOR INSTRUCTIONS]\nAdministrator policy.\n\n[MODEL INSTRUCTIONS]\nModel policy.\nRequest policy.'
    )


@pytest.mark.asyncio
async def test_ollama_anthropic_route_preserves_existing_system_blocks(monkeypatch):
    captured = {}

    async def config_get(key, default=None):
        if key == 'ollama.enable':
            return True
        if key == 'ollama.api_configs':
            return {'0': {}}
        if key == 'chat.global_system_prompt':
            return 'Administrator policy.'
        return default

    async def get_model(model_id):
        return model_info()

    async def allow_access(*args, **kwargs):
        return None

    async def get_url(request, model, url_idx, user):
        return 'http://ollama', 0

    async def send_request(url, **kwargs):
        captured['payload'] = json.loads(kwargs['payload'])
        return {'ok': True}

    monkeypatch.setattr(ollama.Config, 'get', config_get)
    monkeypatch.setattr(ollama.Models, 'get_model_by_id', get_model)
    monkeypatch.setattr(ollama, 'check_model_access', allow_access)
    monkeypatch.setattr(ollama, 'get_ollama_url', get_url)
    monkeypatch.setattr(ollama, 'get_api_key', lambda *args, **kwargs: '')
    monkeypatch.setattr(ollama, 'send_request', send_request)
    cached_block = {
        'type': 'text',
        'text': 'Request policy.',
        'cache_control': {'type': 'ephemeral'},
    }

    result = await ollama.generate_anthropic_messages(
        SimpleNamespace(state=SimpleNamespace()),
        {
            'model': 'model-a',
            'system': [cached_block],
            'messages': [{'role': 'user', 'content': 'Hello'}],
        },
        url_idx=0,
        user=user(),
    )

    assert result == {'ok': True}
    assert captured['payload']['system'][1] == cached_block
    assert captured['payload']['system'][0]['text'].startswith('[ADMINISTRATOR INSTRUCTIONS]\nAdministrator policy.')


@pytest.mark.asyncio
async def test_openai_responses_route_composes_global_instructions(monkeypatch):
    captured = {}

    async def config_get(key, default=None):
        if key == 'chat.global_system_prompt':
            return 'Administrator policy.'
        return default

    async def get_model(model_id):
        return model_info()

    async def allow_access(*args, **kwargs):
        return None

    async def get_connection(idx):
        return 'http://openai', 'key', {}

    async def get_headers(*args, **kwargs):
        return {}, {}

    class Response:
        status = 200
        headers = {'Content-Type': 'application/json'}

        async def json(self, loads=None):
            return {'ok': True}

        async def text(self):
            return 'ok'

        async def release(self):
            return None

    class Session:
        async def request(self, **kwargs):
            captured['payload'] = json.loads(kwargs['data'])
            return Response()

    async def get_session():
        return Session()

    async def cleanup_response(response):
        return None

    monkeypatch.setattr(openai.Config, 'get', config_get)
    monkeypatch.setattr(openai.Models, 'get_model_by_id', get_model)
    monkeypatch.setattr(openai, 'check_model_access', allow_access)
    monkeypatch.setattr(openai, 'get_openai_connection', get_connection)
    monkeypatch.setattr(openai, 'get_headers_and_cookies', get_headers)
    monkeypatch.setattr(openai, 'get_session', get_session)
    monkeypatch.setattr(openai, 'cleanup_response', cleanup_response)
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={'model-a': {'urlIdx': 0}})),
    )

    result = await openai.responses(
        request,
        openai.ResponsesForm(
            model='model-a',
            instructions='Request policy.',
            input='Hello',
        ),
        user=user(),
    )

    assert result == {'ok': True}
    assert captured['payload']['instructions'] == (
        '[ADMINISTRATOR INSTRUCTIONS]\nAdministrator policy.\n\n[MODEL INSTRUCTIONS]\nModel policy.\nRequest policy.'
    )
