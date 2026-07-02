from types import SimpleNamespace

import pytest
from open_webui.utils import models as model_utils
from open_webui.utils import plugin as plugin_utils

FUNCTION_CONTENT_V1 = """
class Pipe:
    def pipe(self):
        return "v1"
"""

FUNCTION_CONTENT_V2 = """
class Pipe:
    def pipe(self):
        return "v2"
"""


class FakeRedis:
    def __init__(self, values=None):
        self.values = values or {}

    async def get(self, key):
        return self.values.get(key)


@pytest.mark.asyncio
async def test_cache_only_function_path_reloads_when_function_version_changes(monkeypatch):
    current = SimpleNamespace(content=FUNCTION_CONTENT_V2)

    async def fake_get_function_by_id(function_id):
        assert function_id == 'demo'
        return current

    async def fake_update_function_by_id(function_id, data):
        return None

    monkeypatch.setattr(plugin_utils.Functions, 'get_function_by_id', fake_get_function_by_id)
    monkeypatch.setattr(plugin_utils.Functions, 'update_function_by_id', fake_update_function_by_id)

    request = _request_state(
        redis=FakeRedis({'open-webui:cache:functions:demo:version': '2'}),
        cache_versions={'functions:demo': '1'},
    )
    request.app.state.FUNCTIONS['demo'] = SimpleNamespace(pipe=lambda: 'v1')
    request.app.state.FUNCTION_CONTENTS['demo'] = FUNCTION_CONTENT_V1

    module, function_type, _ = await plugin_utils.get_function_module_from_cache(
        request,
        'demo',
        load_from_db=False,
    )

    assert function_type == 'pipe'
    assert module.pipe() == 'v2'
    assert request.app.state.FUNCTION_CONTENTS['demo'] == FUNCTION_CONTENT_V2


def test_function_invalidation_event_clears_function_and_model_derived_caches():
    from open_webui.utils.cache_invalidation import apply_cache_invalidation_event

    app = _app_state(cache_versions={'functions:demo': '1'})
    app.state.FUNCTIONS['demo'] = object()
    app.state.FUNCTION_CONTENTS['demo'] = FUNCTION_CONTENT_V1
    app.state.BASE_MODELS = [{'id': 'stale-pipe-model'}]

    apply_cache_invalidation_event(
        app,
        {
            'namespace': 'functions',
            'key': 'demo',
            'version': '2',
        },
    )

    assert 'demo' not in app.state.FUNCTIONS
    assert 'demo' not in app.state.FUNCTION_CONTENTS
    assert app.state.BASE_MODELS == []
    assert app.state.CACHE_VERSIONS['functions:demo'] == '2'


@pytest.mark.asyncio
async def test_get_all_models_refreshes_base_models_when_model_cache_version_changes(monkeypatch):
    async def fake_get_all_base_models(request, user=None):
        return [
            {
                'id': 'fresh-base',
                'name': 'Fresh Base',
                'object': 'model',
                'owned_by': 'openai',
            }
        ]

    monkeypatch.setattr(model_utils, 'get_all_base_models', fake_get_all_base_models)
    monkeypatch.setattr(model_utils.Models, 'get_all_models', _empty_async)
    monkeypatch.setattr(model_utils.Functions, 'get_global_action_functions', _empty_async)
    monkeypatch.setattr(model_utils.Functions, 'get_functions_by_type', _empty_async)
    monkeypatch.setattr(model_utils.Functions, 'get_global_filter_functions', _empty_async)
    monkeypatch.setattr(model_utils.Functions, 'get_functions_by_ids', _empty_async)
    monkeypatch.setattr(model_utils.Functions, 'get_function_valves_by_ids', _empty_dict_async)

    request = _request_state(
        redis=FakeRedis({'open-webui:cache:models:version': '2'}),
        cache_versions={'models': '1'},
        enable_base_models_cache=True,
    )
    request.app.state.MODELS = {'stale-base': {'id': 'stale-base'}}
    request.app.state.BASE_MODELS = [
        {
            'id': 'stale-base',
            'name': 'Stale Base',
            'object': 'model',
            'owned_by': 'openai',
        }
    ]

    models = await model_utils.get_all_models(request)

    assert [model['id'] for model in models] == ['fresh-base']
    assert request.app.state.BASE_MODELS[0]['id'] == 'fresh-base'


async def _empty_async(*args, **kwargs):
    return []


async def _empty_dict_async(*args, **kwargs):
    return {}


def _request_state(redis=None, cache_versions=None, enable_base_models_cache=False):
    return SimpleNamespace(
        app=_app_state(
            redis=redis,
            cache_versions=cache_versions,
            enable_base_models_cache=enable_base_models_cache,
        )
    )

def _app_state(redis=None, cache_versions=None, enable_base_models_cache=False):
    return SimpleNamespace(
        state=SimpleNamespace(
            redis=redis,
            CACHE_VERSIONS=cache_versions or {},
            FUNCTIONS={},
            FUNCTION_CONTENTS={},
            MODELS={},
            BASE_MODELS=[],
            config=SimpleNamespace(
                ENABLE_BASE_MODELS_CACHE=enable_base_models_cache,
                ENABLE_EVALUATION_ARENA_MODELS=False,
                EVALUATION_ARENA_MODELS=[],
                DEFAULT_MODEL_METADATA={},
            ),
        )
    )
