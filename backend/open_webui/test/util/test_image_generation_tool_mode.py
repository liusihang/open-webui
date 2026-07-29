import types

import pytest

from open_webui.utils import middleware


def _request(model):
    config = types.SimpleNamespace(
        TASK_MODEL='',
        TASK_MODEL_EXTERNAL='',
    )
    return types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=config,
                MODELS={model['id']: model},
            )
        ),
        state=types.SimpleNamespace(direct=False),
    )


def _user():
    return types.SimpleNamespace(id='user-1')


@pytest.mark.asyncio
async def test_image_generation_feature_enables_native_tool_without_forced_generation(monkeypatch):
    model = {
        'id': 'model-1',
        'owned_by': 'openai',
        'info': {
            'meta': {
                'capabilities': {
                    'builtin_tools': True,
                    'image_generation': True,
                }
            }
        },
    }

    async def convert_images_passthrough(form_data, *args, **kwargs):
        return form_data

    async def pipeline_passthrough(request, form_data, *args, **kwargs):
        return form_data

    async def passthrough_messages(messages, *args, **kwargs):
        return messages

    async def no_event(*args, **kwargs):
        async def emit(event):
            return None

        return emit

    async def empty_filter_ids(*args, **kwargs):
        return []

    async def empty_filter_result(*args, form_data=None, **kwargs):
        return form_data, {}

    async def no_folder(*args, **kwargs):
        return None

    async def no_oauth(*args, **kwargs):
        return None

    async def no_legacy_files(**kwargs):
        return kwargs['form_data'], []

    async def config_get(key, default=None):
        values = {
            'task.model.default': '',
            'task.model.external': '',
            'user.permissions': {},
        }
        assert key in values
        return values[key]

    async def allow_permission(*args, **kwargs):
        return True

    forced_calls = []

    async def forced_image_handler(*args, **kwargs):
        forced_calls.append((args, kwargs))
        return args[1]

    builtin_feature_snapshots = []

    async def builtin_tools(request, extra_params, features, current_model):
        builtin_feature_snapshots.append(dict(features))
        if features.get('image_generation'):
            return {
                'generate_image': {
                    'spec': {
                        'name': 'generate_image',
                        'description': 'Generate an image',
                    }
                }
            }
        return {}

    monkeypatch.setattr(middleware, 'convert_url_images_to_base64', convert_images_passthrough)
    monkeypatch.setattr(middleware, 'get_event_emitter', no_event)
    monkeypatch.setattr(middleware, 'get_event_call', no_event)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', no_oauth)
    monkeypatch.setattr(middleware.Chats, 'get_chat_folder_id', no_folder)
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', pipeline_passthrough)
    monkeypatch.setattr(middleware, 'get_filter_functions', empty_filter_ids)
    monkeypatch.setattr(middleware, 'process_filter_functions', empty_filter_result)
    monkeypatch.setattr(middleware, 'chat_image_generation_handler', forced_image_handler)
    monkeypatch.setattr(middleware, 'add_file_context', passthrough_messages)
    monkeypatch.setattr(middleware, 'get_builtin_tools', builtin_tools)
    monkeypatch.setattr(middleware, 'apply_legacy_file_retrieval_if_needed', no_legacy_files)
    monkeypatch.setattr(middleware.Config, 'get', config_get)
    monkeypatch.setattr(middleware, 'has_permission', allow_permission)

    form_data, metadata, events = await middleware.process_chat_payload(
        _request(model),
        {
            'model': model['id'],
            'messages': [{'role': 'user', 'content': 'What is the weather?'}],
            'features': {'image_generation': True},
        },
        _user(),
        {
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'session_id': 'session-1',
            'params': {'function_calling': 'native'},
        },
        model,
    )

    assert forced_calls == []
    assert metadata['features']['image_generation'] is True
    assert 'native_image_generation_forced' not in metadata
    assert builtin_feature_snapshots == [{'image_generation': True, 'attached_knowledge_query': False}]
    assert form_data['tools'] == [
        {
            'type': 'function',
            'function': {
                'name': 'generate_image',
                'description': 'Generate an image',
            },
        }
    ]
    assert events == []
