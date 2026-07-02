from __future__ import annotations

import inspect
import json
import logging
import time
from typing import Any

from open_webui.env import REDIS_KEY_PREFIX

log = logging.getLogger(__name__)

CACHE_NAMESPACE_CONFIG = 'config'
CACHE_NAMESPACE_FUNCTIONS = 'functions'
CACHE_NAMESPACE_MODELS = 'models'

CACHE_INVALIDATION_CHANNEL = f'{REDIS_KEY_PREFIX}:cache:invalidate'


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


def apply_cache_invalidation_event(app, event: dict[str, Any]) -> None:
    namespace = event.get('namespace')
    key = event.get('key')
    version = _coerce_version(event.get('version'))

    if namespace == CACHE_NAMESPACE_FUNCTIONS:
        _clear_function_cache(app, key)
        _clear_model_derived_cache(app)
    elif namespace == CACHE_NAMESPACE_MODELS:
        _clear_model_derived_cache(app)
    elif namespace == CACHE_NAMESPACE_CONFIG:
        _refresh_config_from_redis(app, key)
        _clear_model_derived_cache(app)
    else:
        log.debug('Ignoring unknown cache invalidation namespace: %s', namespace)
        return

    if version is not None:
        get_cache_versions(app)[get_cache_version_slot(namespace, key)] = version


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

    apply_cache_invalidation_event(
        app,
        {
            'namespace': namespace,
            'key': key,
            'version': version,
        },
    )
    return True


async def get_remote_cache_version(redis, namespace: str, key: str | None = None) -> str | None:
    try:
        raw = await _maybe_await(redis.get(get_cache_version_key(namespace, key)))
    except Exception:
        log.debug('Failed to read cache version for %s', get_cache_version_slot(namespace, key), exc_info=True)
        return None
    return _coerce_version(raw)


async def invalidate_config_cache(app, key: str | None = None) -> None:
    await _invalidate_cache(app, CACHE_NAMESPACE_CONFIG, key)
    await _invalidate_cache(app, CACHE_NAMESPACE_MODELS)


async def invalidate_function_cache(app, function_id: str | None = None) -> None:
    await _invalidate_cache(app, CACHE_NAMESPACE_FUNCTIONS, function_id)
    await _invalidate_cache(app, CACHE_NAMESPACE_MODELS)


async def invalidate_model_cache(app) -> None:
    await _invalidate_cache(app, CACHE_NAMESPACE_MODELS)


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
                apply_cache_invalidation_event(app, event)
            except Exception:
                log.exception('Error handling cache invalidation event')
    finally:
        try:
            await _maybe_await(pubsub.unsubscribe(CACHE_INVALIDATION_CHANNEL))
            await _maybe_await(pubsub.close())
        except Exception:
            log.debug('Failed to close cache invalidation pubsub', exc_info=True)


def publish_cache_invalidation_sync(redis, namespace: str, key: str | None = None) -> dict[str, Any] | None:
    if redis is None:
        return None

    try:
        version = _coerce_version(_redis_incr_sync(redis, get_cache_version_key(namespace, key)))
        event = _build_event(namespace, key, version)
        _redis_publish_sync(redis, event)
        return event
    except Exception:
        log.debug('Failed to publish cache invalidation for %s', get_cache_version_slot(namespace, key), exc_info=True)
        return None


async def _invalidate_cache(app, namespace: str, key: str | None = None) -> None:
    redis = getattr(app.state, 'redis', None)
    event = await _publish_cache_invalidation(redis, namespace, key)
    if event is None:
        event = _build_event(namespace, key, str(time.time_ns()))
    apply_cache_invalidation_event(app, event)


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


def _redis_incr_sync(redis, key: str):
    if hasattr(redis, 'nodes_manager'):
        return redis.execute_command('INCR', key)
    return redis.incr(key)


async def _redis_publish(redis, event: dict[str, Any]) -> None:
    payload = json.dumps(event)
    if hasattr(redis, 'nodes_manager'):
        await _maybe_await(redis.execute_command('PUBLISH', CACHE_INVALIDATION_CHANNEL, payload))
    else:
        await _maybe_await(redis.publish(CACHE_INVALIDATION_CHANNEL, payload))


def _redis_publish_sync(redis, event: dict[str, Any]) -> None:
    payload = json.dumps(event)
    if hasattr(redis, 'nodes_manager'):
        redis.execute_command('PUBLISH', CACHE_INVALIDATION_CHANNEL, payload)
    else:
        redis.publish(CACHE_INVALIDATION_CHANNEL, payload)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _clear_function_cache(app, function_id: str | None) -> None:
    functions = getattr(app.state, 'FUNCTIONS', None)
    contents = getattr(app.state, 'FUNCTION_CONTENTS', None)

    if function_id:
        if isinstance(functions, dict):
            functions.pop(function_id, None)
        if isinstance(contents, dict):
            contents.pop(function_id, None)
    else:
        if isinstance(functions, dict):
            functions.clear()
        if isinstance(contents, dict):
            contents.clear()


def _clear_model_derived_cache(app) -> None:
    if hasattr(app.state, 'BASE_MODELS'):
        app.state.BASE_MODELS = []


def _refresh_config_from_redis(app, key: str | None) -> None:
    config = getattr(app.state, 'config', None)
    if config is None or not key:
        return

    loader = getattr(config, '_load_from_redis', None)
    if loader:
        loader(key)


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
