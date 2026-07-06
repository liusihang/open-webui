from types import SimpleNamespace

import pytest


class FakeRedis:
    def __init__(self, values=None):
        self.values = values or {}
        self.published = []

    async def get(self, key):
        return self.values.get(key)

    async def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    async def publish(self, channel, payload):
        self.published.append((channel, payload))


class FakeFunctions:
    def __init__(self):
        self.calls = []

    async def update_function_by_id(self, function_id, updated, db=None):
        self.calls.append((function_id, updated))
        return SimpleNamespace(id=function_id)


@pytest.mark.asyncio
async def test_config_event_refreshes_runtime_config_and_model_cache():
    from open_webui.utils.cache_invalidation import apply_cache_invalidation_event

    refresh_calls = []
    app = _app_state(cache_versions={'config:ui.default_models': '1'})

    async def refresh_runtime_config(target_app):
        refresh_calls.append(target_app)
        target_app.state.config.DEFAULT_MODELS = 'fresh'

    app.state.refresh_runtime_config = refresh_runtime_config
    app.state.BASE_MODELS = [{'id': 'stale-base'}]
    app.state.MODELS = {'stale-base': {'id': 'stale-base'}}

    await apply_cache_invalidation_event(
        app,
        {
            'namespace': 'config',
            'key': 'ui.default_models',
            'version': '2',
        },
    )

    assert refresh_calls == [app]
    assert app.state.config.DEFAULT_MODELS == 'fresh'
    assert app.state.BASE_MODELS == []
    assert app.state.MODELS == {}
    assert app.state.CACHE_VERSIONS['config:ui.default_models'] == '2'


@pytest.mark.asyncio
async def test_function_model_hook_publishes_and_clears_registered_caches():
    from open_webui.utils.cache_invalidation import (
        CACHE_INVALIDATION_CHANNEL,
        install_model_cache_invalidation_hooks,
        register_cache_invalidation_app,
    )

    fake_functions = FakeFunctions()
    redis = FakeRedis()
    app = _app_state(redis=redis)
    app.state.FUNCTIONS['demo'] = object()
    app.state.FUNCTION_CONTENTS['demo'] = 'old'
    app.state.BASE_MODELS = [{'id': 'stale-pipe-model'}]

    register_cache_invalidation_app(app)
    install_model_cache_invalidation_hooks(functions=fake_functions)

    result = await fake_functions.update_function_by_id('demo', {'content': 'new'})

    assert result.id == 'demo'
    assert fake_functions.calls == [('demo', {'content': 'new'})]
    assert 'demo' not in app.state.FUNCTIONS
    assert 'demo' not in app.state.FUNCTION_CONTENTS
    assert app.state.BASE_MODELS == []
    assert redis.published
    assert redis.published[0][0] == CACHE_INVALIDATION_CHANNEL


@pytest.mark.asyncio
async def test_ensure_cache_fresh_applies_remote_version_once():
    from open_webui.utils.cache_invalidation import ensure_cache_fresh

    app = _app_state(
        redis=FakeRedis({'open-webui:cache:functions:demo:version': '2'}),
        cache_versions={'functions:demo': '1'},
    )
    app.state.FUNCTIONS['demo'] = object()
    app.state.FUNCTION_CONTENTS['demo'] = 'old'

    first = await ensure_cache_fresh(app, 'functions', 'demo')
    second = await ensure_cache_fresh(app, 'functions', 'demo')

    assert first is True
    assert second is False
    assert 'demo' not in app.state.FUNCTIONS
    assert 'demo' not in app.state.FUNCTION_CONTENTS


@pytest.mark.asyncio
async def test_config_hook_preserves_staticmethod_call_signature():
    from open_webui.utils.cache_invalidation import install_config_cache_invalidation_hooks

    calls = []

    class FakeConfig:
        @staticmethod
        async def upsert(updates):
            calls.append(updates)

        @staticmethod
        async def delete(key):
            calls.append(key)
            return True

        @staticmethod
        async def clear():
            calls.append('clear')

    install_config_cache_invalidation_hooks(FakeConfig)

    await FakeConfig.upsert({'ui.default_models': 'demo'})
    assert calls == [{'ui.default_models': 'demo'}]


def test_cache_invalidation_source_does_not_reintroduce_appconfig_storage():
    from pathlib import Path

    source = Path('backend/open_webui/utils/cache_invalidation.py').read_text()

    assert 'AppConfig' not in source
    assert 'open_webui.internal.config' not in source


def _app_state(redis=None, cache_versions=None):
    return SimpleNamespace(
        state=SimpleNamespace(
            redis=redis,
            CACHE_VERSIONS=cache_versions or {},
            FUNCTIONS={},
            FUNCTION_CONTENTS={},
            TOOLS={},
            TOOL_CONTENTS={},
            MODELS={},
            BASE_MODELS=[],
            config=SimpleNamespace(),
        )
    )
