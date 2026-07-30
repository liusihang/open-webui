from collections.abc import Callable

import pytest
from open_webui.socket import utils as socket_utils
from open_webui.utils.json_codec import JSONCodec
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


class FlakyRedisRead:
    def __init__(self, method_name: str, result, error_type: type[Exception]):
        self.method_name = method_name
        self.result = result
        self.error_type = error_type
        self.calls = 0

    def __getattr__(self, name: str):
        if name != self.method_name:
            raise AttributeError(name)

        def execute(*_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise self.error_type('stale pooled connection')
            return self.result

        return execute


class InMemoryRedisHash:
    def __init__(self):
        self.hashes = {}

    def hkeys(self, name):
        return list(self.hashes.get(name, {}))

    def hset(self, name, key=None, value=None, mapping=None):
        values = self.hashes.setdefault(name, {})
        if mapping is not None:
            values.update(mapping)
        else:
            values[key] = value

    def hdel(self, name, *keys):
        values = self.hashes.get(name, {})
        removed = sum(key in values for key in keys)
        for key in keys:
            values.pop(key, None)
        return removed

    def hget(self, name, key):
        return self.hashes.get(name, {}).get(key)


@pytest.mark.parametrize('error_type', [RedisConnectionError, RedisTimeoutError])
@pytest.mark.parametrize(
    ('method_name', 'result', 'operation', 'expected'),
    [
        ('hget', JSONCodec.dumps({'id': 'model-1'}), lambda value: value['model-1'], {'id': 'model-1'}),
        ('hexists', True, lambda value: 'model-1' in value, True),
        ('hlen', 1, len, 1),
        ('hkeys', ['model-1'], lambda value: value.keys(), ['model-1']),
        (
            'hvals',
            [JSONCodec.dumps({'id': 'model-1'})],
            lambda value: value.values(),
            [{'id': 'model-1'}],
        ),
        (
            'hgetall',
            {'model-1': JSONCodec.dumps({'id': 'model-1'})},
            lambda value: value.items(),
            [('model-1', {'id': 'model-1'})],
        ),
    ],
)
def test_redis_dict_retries_transient_failures_for_idempotent_reads(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    method_name: str,
    result,
    operation: Callable,
    expected,
):
    redis = FlakyRedisRead(method_name, result, error_type)
    monkeypatch.setattr(socket_utils, 'get_redis_connection', lambda *_args, **_kwargs: redis)
    value = socket_utils.RedisDict('open-webui:models', 'redis://redis:6379/0')

    assert operation(value) == expected
    assert redis.calls == 2


def test_redis_dict_retries_transient_reads_only_once(monkeypatch: pytest.MonkeyPatch):
    class UnavailableRedis:
        def __init__(self):
            self.calls = 0

        def hlen(self, _name):
            self.calls += 1
            raise RedisConnectionError('redis unavailable')

    redis = UnavailableRedis()
    monkeypatch.setattr(socket_utils, 'get_redis_connection', lambda *_args, **_kwargs: redis)
    value = socket_utils.RedisDict('open-webui:models', 'redis://redis:6379/0')

    with pytest.raises(RedisConnectionError, match='redis unavailable'):
        len(value)

    assert redis.calls == 2


def test_redis_dict_set_keeps_models_published_by_another_worker(monkeypatch: pytest.MonkeyPatch):
    redis = InMemoryRedisHash()
    monkeypatch.setattr(socket_utils, 'get_redis_connection', lambda *_args, **_kwargs: redis)

    worker_one = socket_utils.RedisDict('open-webui:models', 'redis://redis:6379/0')
    worker_two = socket_utils.RedisDict('open-webui:models', 'redis://redis:6379/0')

    worker_one.set(
        {
            'shared-model': {'id': 'shared-model'},
            'bifrostapi.Cliproxy/gpt-5.5': {'id': 'bifrostapi.Cliproxy/gpt-5.5'},
        }
    )
    worker_two.set({'shared-model': {'id': 'shared-model'}})

    assert worker_two['bifrostapi.Cliproxy/gpt-5.5'] == {'id': 'bifrostapi.Cliproxy/gpt-5.5'}
