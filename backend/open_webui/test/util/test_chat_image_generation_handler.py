import types

import pytest
from starlette.responses import JSONResponse

from open_webui.utils import middleware


def _request(*, prompt_generation: bool = False):
    config = types.SimpleNamespace(
        ENABLE_IMAGE_EDIT=False,
        ENABLE_IMAGE_PROMPT_GENERATION=prompt_generation,
    )
    return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(config=config)))


def _user():
    return types.SimpleNamespace(id='user-1')


def _extra_params():
    events = []

    async def event_emitter(event):
        events.append(event)

    return {
        '__metadata__': {'chat_id': 'chat-1', 'message_id': 'assistant-1'},
        '__event_emitter__': event_emitter,
        '__events__': events,
    }


@pytest.mark.asyncio
async def test_chat_image_generation_uses_request_messages_when_chat_row_is_missing(monkeypatch):
    async def missing_chat(chat_id, user_id):
        return None

    captured = {}

    async def fake_image_generations(request, form_data, metadata=None, user=None):
        captured['prompt'] = form_data.prompt
        captured['metadata'] = metadata
        return [{'url': '/api/v1/files/image-1/content'}]

    monkeypatch.setattr(middleware.Chats, 'get_chat_by_id_and_user_id', missing_chat)
    monkeypatch.setattr(middleware, 'image_generations', fake_image_generations)

    form_data = {
        'model': 'model-1',
        'messages': [{'role': 'user', 'content': 'draw a calm orange cat'}],
    }
    extra_params = _extra_params()

    result = await middleware.chat_image_generation_handler(
        _request(),
        form_data,
        extra_params,
        _user(),
    )

    assert captured == {
        'prompt': 'draw a calm orange cat',
        'metadata': {'chat_id': 'chat-1', 'message_id': 'assistant-1'},
    }
    assert extra_params['__events__'][-1] == {
        'type': 'files',
        'data': {'files': [{'type': 'image', 'url': '/api/v1/files/image-1/content'}]},
    }
    assert 'requested image has been created' in result['messages'][0]['content']


@pytest.mark.asyncio
async def test_chat_image_generation_downgrades_jsonresponse_prompt_generation_without_exception_log(monkeypatch):
    chat = types.SimpleNamespace(
        chat={
            'history': {
                'currentId': 'assistant-1',
                'messages': {
                    'user-1': {
                        'id': 'user-1',
                        'parentId': None,
                        'childrenIds': ['assistant-1'],
                        'role': 'user',
                        'content': 'draw a blue cup',
                    },
                    'assistant-1': {
                        'id': 'assistant-1',
                        'parentId': 'user-1',
                        'childrenIds': [],
                        'role': 'assistant',
                        'content': '',
                    },
                },
            }
        }
    )

    async def get_chat(chat_id, user_id):
        return chat

    async def bad_prompt_response(request, payload, user):
        return JSONResponse({'detail': 'planning failed'}, status_code=400)

    captured = {}

    async def fake_image_generations(request, form_data, metadata=None, user=None):
        captured['prompt'] = form_data.prompt
        return [{'url': '/api/v1/files/image-2/content'}]

    exception_logs = []

    def fake_exception(*args, **kwargs):
        exception_logs.append((args, kwargs))

    monkeypatch.setattr(middleware.Chats, 'get_chat_by_id_and_user_id', get_chat)
    monkeypatch.setattr(middleware, 'generate_image_prompt', bad_prompt_response)
    monkeypatch.setattr(middleware, 'image_generations', fake_image_generations)
    monkeypatch.setattr(middleware.log, 'exception', fake_exception)

    await middleware.chat_image_generation_handler(
        _request(prompt_generation=True),
        {
            'model': 'model-1',
            'messages': [{'role': 'user', 'content': 'draw a blue cup'}],
        },
        _extra_params(),
        _user(),
    )

    assert captured == {'prompt': 'draw a blue cup'}
    assert exception_logs == []
