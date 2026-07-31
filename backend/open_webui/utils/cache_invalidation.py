from __future__ import annotations

import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from open_webui.env import REDIS_KEY_PREFIX
from open_webui.socket.utils import RedisDict

log = logging.getLogger(__name__)

CACHE_NAMESPACE_CONFIG = 'config'
CACHE_NAMESPACE_CONVERSATION_MODE_PROFILE_HEADS = 'conversation_mode_profile_heads'
CACHE_NAMESPACE_FUNCTIONS = 'functions'
CACHE_NAMESPACE_MODELS = 'models'
CACHE_NAMESPACE_TOOLS = 'tools'

CACHE_INVALIDATION_CHANNEL = f'{REDIS_KEY_PREFIX}:cache:invalidate'

_REGISTERED_APPS: list[Any] = []


def register_cache_invalidation_app(app) -> None:
    if not any(existing is app for existing in _REGISTERED_APPS):
        _REGISTERED_APPS.append(app)


def get_cache_version_slot(namespace: str, key: str | None = None) -> str:
    return f'{namespace}:{key}' if key else namespace


def get_cache_version_key(namespace: str, key: str | None = None) -> str:
    parts = [REDIS_KEY_PREFIX, 'cache', namespace]
    if key:
        parts.append(key)
    parts.append('version')
    return ':'.join(parts)


def get_cache_versions(app) -> dict[str, str]:
    if not hasattr(app.state, 'CACHE_VERSIONS'):
        app.state.CACHE_VERSIONS = {}
    return app.state.CACHE_VERSIONS


async def apply_cache_invalidation_event(app, event: dict[str, Any]) -> bool:
    namespace = event.get('namespace')
    key = event.get('key')
    version = _coerce_version(event.get('version'))

    if namespace == CACHE_NAMESPACE_FUNCTIONS:
        _clear_function_cache(app, key)
        _clear_model_derived_cache(app)
    elif namespace == CACHE_NAMESPACE_TOOLS:
        _clear_tool_cache(app, key)
    elif namespace == CACHE_NAMESPACE_MODELS:
        _clear_model_derived_cache(app)
    elif namespace == CACHE_NAMESPACE_CONFIG:
        await _refresh_runtime_config(app)
        _clear_model_derived_cache(app)
    elif namespace == CACHE_NAMESPACE_CONVERSATION_MODE_PROFILE_HEADS:
        _clear_mode_profile_head_cache(app, key)
    else:
        log.debug('Ignoring unknown cache invalidation namespace: %s', namespace)
        return False

    if version is not None:
        get_cache_versions(app)[get_cache_version_slot(namespace, key)] = version
    return True


async def ensure_cache_fresh(app, namespace: str, key: str | None = None) -> bool:
    redis = getattr(app.state, 'redis', None)
    if redis is None:
        return False

    version = await get_remote_cache_version(redis, namespace, key)
    if version is None:
        return False

    slot = get_cache_version_slot(namespace, key)
    if get_cache_versions(app).get(slot) == version:
        return False

    return await apply_cache_invalidation_event(
        app,
        {
            'namespace': namespace,
            'key': key,
            'version': version,
        },
    )


async def get_remote_cache_version(redis, namespace: str, key: str | None = None) -> str | None:
    try:
        raw = await _maybe_await(redis.get(get_cache_version_key(namespace, key)))
    except Exception:
        log.debug('Failed to read cache version for %s', get_cache_version_slot(namespace, key), exc_info=True)
        return None
    return _coerce_version(raw)


async def invalidate_config_cache(app=None, key: str | None = None) -> None:
    await _broadcast_cache_invalidation(CACHE_NAMESPACE_CONFIG, key, app=app)
    await _broadcast_cache_invalidation(CACHE_NAMESPACE_MODELS, app=app)


async def invalidate_conversation_mode_profile_head(app, mode: str) -> None:
    await _broadcast_cache_invalidation(
        CACHE_NAMESPACE_CONVERSATION_MODE_PROFILE_HEADS,
        mode,
        app=app,
    )


async def invalidate_function_cache(app=None, function_id: str | None = None) -> None:
    await _broadcast_cache_invalidation(CACHE_NAMESPACE_FUNCTIONS, function_id, app=app)
    await _broadcast_cache_invalidation(CACHE_NAMESPACE_MODELS, app=app)


async def invalidate_tool_cache(app=None, tool_id: str | None = None) -> None:
    await _broadcast_cache_invalidation(CACHE_NAMESPACE_TOOLS, tool_id, app=app)


async def invalidate_model_cache(app=None) -> None:
    await _broadcast_cache_invalidation(CACHE_NAMESPACE_MODELS, app=app)


async def redis_cache_invalidation_listener(app) -> None:
    redis = app.state.redis
    pubsub = redis.pubsub()
    await pubsub.subscribe(CACHE_INVALIDATION_CHANNEL)

    try:
        async for message in pubsub.listen():
            if message.get('type') != 'message':
                continue
            try:
                event = json.loads(_decode_message_data(message.get('data')))
                await apply_cache_invalidation_event(app, event)
            except Exception:
                log.exception('Error handling cache invalidation event')
    finally:
        try:
            await _maybe_await(pubsub.unsubscribe(CACHE_INVALIDATION_CHANNEL))
            await _maybe_await(pubsub.close())
        except Exception:
            log.debug('Failed to close cache invalidation pubsub', exc_info=True)


def install_model_cache_invalidation_hooks(
    *,
    functions=None,
    models=None,
    tools=None,
) -> None:
    if functions is None:
        from open_webui.models.functions import Functions as functions
    if models is None:
        from open_webui.models.models import Models as models
    if tools is None:
        from open_webui.models.tools import Tools as tools

    _wrap_async_cache_method(
        functions,
        'insert_new_function',
        lambda args, kwargs, result: invalidate_function_cache(function_id=_result_id(result) or _form_id(args, 2)),
    )
    _wrap_async_cache_method(
        functions,
        'sync_functions',
        lambda args, kwargs, result: invalidate_function_cache(),
    )
    _wrap_async_cache_method(
        functions,
        'update_function_by_id',
        lambda args, kwargs, result: invalidate_function_cache(function_id=_arg(args, 0)),
    )
    _wrap_async_cache_method(
        functions,
        'update_function_metadata_by_id',
        lambda args, kwargs, result: invalidate_function_cache(function_id=_arg(args, 0)),
    )
    _wrap_async_cache_method(
        functions,
        'update_function_valves_by_id',
        lambda args, kwargs, result: invalidate_function_cache(function_id=_arg(args, 0)),
    )
    _wrap_async_cache_method(
        functions,
        'deactivate_all_functions',
        lambda args, kwargs, result: invalidate_function_cache(),
    )
    _wrap_async_cache_method(
        functions,
        'delete_function_by_id',
        lambda args, kwargs, result: invalidate_function_cache(function_id=_arg(args, 0)),
    )

    _wrap_async_cache_method(
        models,
        'insert_new_model',
        lambda args, kwargs, result: invalidate_model_cache(),
    )
    _wrap_async_cache_method(
        models,
        'toggle_model_by_id',
        lambda args, kwargs, result: invalidate_model_cache(),
    )
    _wrap_async_cache_method(
        models,
        'update_model_by_id',
        lambda args, kwargs, result: invalidate_model_cache(),
    )
    _wrap_async_cache_method(
        models,
        'update_model_updated_at_by_id',
        lambda args, kwargs, result: invalidate_model_cache(),
    )
    _wrap_async_cache_method(
        models,
        'delete_model_by_id',
        lambda args, kwargs, result: invalidate_model_cache(),
    )
    _wrap_async_cache_method(
        models,
        'delete_all_models',
        lambda args, kwargs, result: invalidate_model_cache(),
    )
    _wrap_async_cache_method(
        models,
        'sync_models',
        lambda args, kwargs, result: invalidate_model_cache(),
    )

    _wrap_async_cache_method(
        tools,
        'insert_new_tool',
        lambda args, kwargs, result: invalidate_tool_cache(tool_id=_result_id(result) or _form_id(args, 1)),
    )
    _wrap_async_cache_method(
        tools,
        'update_tool_by_id',
        lambda args, kwargs, result: invalidate_tool_cache(tool_id=_arg(args, 0)),
    )
    _wrap_async_cache_method(
        tools,
        'update_tool_valves_by_id',
        lambda args, kwargs, result: invalidate_tool_cache(tool_id=_arg(args, 0)),
    )
    _wrap_async_cache_method(
        tools,
        'delete_tool_by_id',
        lambda args, kwargs, result: invalidate_tool_cache(tool_id=_arg(args, 0)),
    )


def install_config_cache_invalidation_hooks(config_cls=None) -> None:
    if config_cls is None:
        from open_webui.models.config import Config as config_cls

    _wrap_async_cache_method(
        config_cls,
        'upsert',
        lambda args, kwargs, result: _invalidate_config_updates(_arg(args, 0) or kwargs.get('updates')),
        success=lambda result: True,
    )
    _wrap_async_cache_method(
        config_cls,
        'delete',
        lambda args, kwargs, result: invalidate_config_cache(key=_arg(args, 0)),
    )
    _wrap_async_cache_method(
        config_cls,
        'clear',
        lambda args, kwargs, result: invalidate_config_cache(),
        success=lambda result: True,
    )


def _wrap_async_cache_method(
    target,
    method_name: str,
    invalidator: Callable[[tuple[Any, ...], dict[str, Any], Any], Awaitable[None]],
    *,
    success: Callable[[Any], bool] | None = None,
) -> None:
    original = getattr(target, method_name, None)
    if original is None or getattr(original, '_cache_invalidation_wrapped', False):
        return

    success = success or (lambda result: result is not None and result is not False)

    @wraps(original)
    async def wrapped(*args, **kwargs):
        result = await original(*args, **kwargs)
        if success(result):
            await invalidator(args, kwargs, result)
        return result

    wrapped._cache_invalidation_wrapped = True
    setattr(target, method_name, wrapped)


async def _invalidate_config_updates(updates: dict | None) -> None:
    if not isinstance(updates, dict) or not updates:
        await invalidate_config_cache()
        return

    for key in updates:
        await invalidate_config_cache(key=key)


async def _broadcast_cache_invalidation(namespace: str, key: str | None = None, *, app=None) -> None:
    event = await _publish_cache_invalidation(_select_redis(app), namespace, key)
    if event is None:
        event = _build_event(namespace, key, str(time.time_ns()))

    apps = []
    if app is not None:
        apps.append(app)
    for registered_app in _REGISTERED_APPS:
        if not any(existing is registered_app for existing in apps):
            apps.append(registered_app)

    for target_app in apps:
        try:
            await apply_cache_invalidation_event(target_app, event)
        except Exception:
            log.debug('Failed to apply local cache invalidation event', exc_info=True)


async def _publish_cache_invalidation(redis, namespace: str, key: str | None = None) -> dict[str, Any] | None:
    if redis is None:
        return None

    try:
        version = _coerce_version(await _redis_incr(redis, get_cache_version_key(namespace, key)))
        event = _build_event(namespace, key, version)
        await _redis_publish(redis, event)
        return event
    except Exception:
        log.debug('Failed to publish cache invalidation for %s', get_cache_version_slot(namespace, key), exc_info=True)
        return None


def _select_redis(app=None):
    if app is not None:
        redis = getattr(getattr(app, 'state', None), 'redis', None)
        if redis is not None:
            return redis

    for registered_app in _REGISTERED_APPS:
        redis = getattr(getattr(registered_app, 'state', None), 'redis', None)
        if redis is not None:
            return redis

    try:
        from open_webui.utils.redis import get_redis_client

        return get_redis_client(async_mode=True)
    except Exception:
        return None


def _build_event(namespace: str, key: str | None, version: str | None) -> dict[str, Any]:
    return {
        'namespace': namespace,
        'key': key,
        'version': version,
    }


async def _redis_incr(redis, key: str):
    if hasattr(redis, 'nodes_manager'):
        return await _maybe_await(redis.execute_command('INCR', key))
    return await _maybe_await(redis.incr(key))


async def _redis_publish(redis, event: dict[str, Any]) -> None:
    payload = json.dumps(event)
    if hasattr(redis, 'nodes_manager'):
        await _maybe_await(redis.execute_command('PUBLISH', CACHE_INVALIDATION_CHANNEL, payload))
    else:
        await _maybe_await(redis.publish(CACHE_INVALIDATION_CHANNEL, payload))


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _refresh_runtime_config(app) -> None:
    refresh = getattr(app.state, 'refresh_runtime_config', None)
    if refresh is not None:
        await _maybe_await(refresh(app))


def _clear_function_cache(app, function_id: str | None) -> None:
    _clear_named_cache(app, 'FUNCTIONS', function_id)
    _clear_named_cache(app, 'FUNCTION_CONTENTS', function_id)


def _clear_tool_cache(app, tool_id: str | None) -> None:
    _clear_named_cache(app, 'TOOLS', tool_id)
    _clear_named_cache(app, 'TOOL_CONTENTS', tool_id)


def _clear_mode_profile_head_cache(app, mode: str | None) -> None:
    _clear_named_cache(app, 'CONVERSATION_MODE_PROFILE_HEADS', mode)


def _clear_named_cache(app, name: str, key: str | None) -> None:
    cache = getattr(app.state, name, None)
    if cache is None:
        return

    if key:
        try:
            cache.pop(key, None)
            return
        except AttributeError:
            pass
        try:
            if key in cache:
                del cache[key]
        except Exception:
            log.debug('Failed to clear %s cache key %s', name, key, exc_info=True)
        return

    try:
        cache.clear()
    except Exception:
        setattr(app.state, name, {})


def _clear_model_derived_cache(app) -> None:
    if hasattr(app.state, 'BASE_MODELS'):
        app.state.BASE_MODELS = []

    models = getattr(app.state, 'MODELS', None)
    if models is None:
        return
    if isinstance(models, RedisDict):
        # MODELS is already one shared hash. Every worker receives the same
        # invalidation event, so deleting it here lets a late worker erase a
        # catalog that another worker has just repopulated. Resetting
        # BASE_MODELS above still makes each worker rebuild its derived list;
        # RedisDict.set() publishes verified entries without making the
        # shared catalog transiently empty.
        return
    try:
        models.clear()
    except Exception:
        app.state.MODELS = {}


def _decode_message_data(data) -> str:
    if isinstance(data, bytes):
        return data.decode('utf-8')
    return data


def _coerce_version(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode('utf-8')
    return str(value)


def _arg(args: tuple[Any, ...], index: int) -> Any:
    return args[index] if len(args) > index else None


def _form_id(args: tuple[Any, ...], index: int) -> str | None:
    form = _arg(args, index)
    return getattr(form, 'id', None)


def _result_id(result) -> str | None:
    return getattr(result, 'id', None)
