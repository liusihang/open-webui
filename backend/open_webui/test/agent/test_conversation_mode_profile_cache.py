from __future__ import annotations

from types import SimpleNamespace

import pytest


class FakeRedis:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.values: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def get(self, key):
        if self.fail:
            raise RuntimeError('redis unavailable')
        return self.values.get(key)

    async def incr(self, key):
        if self.fail:
            raise RuntimeError('redis unavailable')
        value = int(self.values.get(key, '0')) + 1
        self.values[key] = str(value)
        return value

    async def publish(self, channel, payload):
        if self.fail:
            raise RuntimeError('redis unavailable')
        self.published.append((channel, payload))


def _app(redis):
    return SimpleNamespace(
        state=SimpleNamespace(
            redis=redis,
            CACHE_VERSIONS={},
            CONVERSATION_MODE_PROFILE_HEADS={},
            CONVERSATION_MODE_PROFILE_REVISIONS={},
        )
    )


@pytest.mark.asyncio
async def test_two_workers_converge_on_changed_mode_without_flushing_old_revision(monkeypatch):
    from open_webui.agent import conversation_mode_profile_service as service
    from open_webui.utils import cache_invalidation

    redis = FakeRedis()
    worker_one = _app(redis)
    worker_two = _app(redis)
    monkeypatch.setattr(cache_invalidation, '_REGISTERED_APPS', [])
    cache_invalidation.register_cache_invalidation_app(worker_one)
    cache_invalidation.register_cache_invalidation_app(worker_two)

    chat_v1 = SimpleNamespace(id='chat-v1', mode='chat')
    chat_v2 = SimpleNamespace(id='chat-v2', mode='chat')
    agent_v1 = SimpleNamespace(id='agent-v1', mode='agent')
    current = {'chat': chat_v1, 'agent': agent_v1}
    revisions = {revision.id: revision for revision in (chat_v1, chat_v2, agent_v1)}
    revision_reads: list[str] = []

    async def get_current_revision(mode, db=None):
        return current[str(mode)]

    async def get_revision(revision_id, expected_mode=None, db=None):
        revision_reads.append(revision_id)
        return revisions.get(revision_id)

    monkeypatch.setattr(service.ConversationModeProfiles, 'get_current_revision', get_current_revision)
    monkeypatch.setattr(service.ConversationModeProfiles, 'get_revision', get_revision)

    for worker in (worker_one, worker_two):
        assert (await service.get_cached_current_revision(worker, 'chat')).id == 'chat-v1'
        assert (await service.get_cached_current_revision(worker, 'agent')).id == 'agent-v1'
        assert (await service.get_cached_revision(worker, 'chat-v1', expected_mode='chat')).id == 'chat-v1'

    revision_reads.clear()
    current['chat'] = chat_v2
    await cache_invalidation.invalidate_conversation_mode_profile_head(worker_one, 'chat')

    assert 'chat' not in worker_one.state.CONVERSATION_MODE_PROFILE_HEADS
    assert 'chat' not in worker_two.state.CONVERSATION_MODE_PROFILE_HEADS
    assert worker_one.state.CONVERSATION_MODE_PROFILE_HEADS['agent'].id == 'agent-v1'
    assert worker_two.state.CONVERSATION_MODE_PROFILE_HEADS['agent'].id == 'agent-v1'
    assert worker_one.state.CONVERSATION_MODE_PROFILE_REVISIONS['chat-v1'].id == 'chat-v1'
    assert worker_two.state.CONVERSATION_MODE_PROFILE_REVISIONS['chat-v1'].id == 'chat-v1'

    assert (await service.get_cached_current_revision(worker_one, 'chat')).id == 'chat-v2'
    assert (await service.get_cached_current_revision(worker_two, 'chat')).id == 'chat-v2'
    assert (await service.get_cached_revision(worker_one, 'chat-v1', expected_mode='chat')).id == 'chat-v1'
    assert (await service.get_cached_revision(worker_two, 'chat-v1', expected_mode='chat')).id == 'chat-v1'
    assert revision_reads == []

    assert redis.published
    assert '"namespace": "conversation_mode_profile_heads"' in redis.published[-1][1]
    assert '"key": "chat"' in redis.published[-1][1]


@pytest.mark.asyncio
async def test_publish_failure_still_clears_local_head_and_preserves_revision_cache(monkeypatch):
    from open_webui.utils import cache_invalidation

    app = _app(FakeRedis(fail=True))
    app.state.CONVERSATION_MODE_PROFILE_HEADS['chat'] = SimpleNamespace(id='chat-v1')
    app.state.CONVERSATION_MODE_PROFILE_REVISIONS['chat-v1'] = SimpleNamespace(id='chat-v1')
    monkeypatch.setattr(cache_invalidation, '_REGISTERED_APPS', [])

    await cache_invalidation.invalidate_conversation_mode_profile_head(app, 'chat')

    assert 'chat' not in app.state.CONVERSATION_MODE_PROFILE_HEADS
    assert app.state.CONVERSATION_MODE_PROFILE_REVISIONS['chat-v1'].id == 'chat-v1'
