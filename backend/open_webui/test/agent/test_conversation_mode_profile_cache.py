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


def _revision(revision_id: str, mode: str, *, prompt: str = ''):
    from open_webui.agent.conversation_mode_profiles import ProfileDefaults
    from open_webui.models.conversation_mode_profiles import ConversationModeProfileRevisionModel

    return ConversationModeProfileRevisionModel(
        id=revision_id,
        mode=mode,
        revision_number=1,
        schema_version=1,
        system_prompt=prompt,
        defaults=ProfileDefaults(),
        content_hash='0' * 64,
        created_at=1,
        created_by='admin-1',
        restored_from_revision_id=None,
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

    async def get_head(mode, db=None):
        normalized_mode = str(mode)
        return SimpleNamespace(
            mode=normalized_mode,
            current_revision_id=current[normalized_mode].id,
        )

    async def get_revision(revision_id, expected_mode=None, db=None):
        revision_reads.append(revision_id)
        return revisions.get(revision_id)

    monkeypatch.setattr(service.ConversationModeProfiles, 'get_current_revision', get_current_revision)
    monkeypatch.setattr(service.ConversationModeProfiles, 'get_head', get_head)
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
    assert revision_reads == ['chat-v2', 'chat-v2']

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


@pytest.mark.asyncio
async def test_unregistered_reader_refreshes_from_database_after_writer_publish_failure(monkeypatch):
    from open_webui.agent import conversation_mode_profile_service as service
    from open_webui.utils import cache_invalidation

    writer = _app(FakeRedis(fail=True))
    reader = _app(FakeRedis(fail=True))
    monkeypatch.setattr(cache_invalidation, '_REGISTERED_APPS', [])

    chat_v1 = _revision('chat-v1', 'chat')
    chat_v2 = _revision('chat-v2', 'chat')
    revisions = {revision.id: revision for revision in (chat_v1, chat_v2)}
    head_revision_id = {'chat': chat_v1.id}

    async def get_head(mode, db=None):
        normalized_mode = str(mode)
        return SimpleNamespace(
            mode=normalized_mode,
            current_revision_id=head_revision_id[normalized_mode],
        )

    async def get_current_revision(mode, db=None):
        return revisions[head_revision_id[str(mode)]]

    async def get_revision(revision_id, expected_mode=None, db=None):
        return revisions.get(revision_id)

    monkeypatch.setattr(service.ConversationModeProfiles, 'get_head', get_head)
    monkeypatch.setattr(service.ConversationModeProfiles, 'get_current_revision', get_current_revision)
    monkeypatch.setattr(service.ConversationModeProfiles, 'get_revision', get_revision)

    assert (await service.get_cached_current_revision(reader, 'chat')).id == chat_v1.id
    head_revision_id['chat'] = chat_v2.id
    await cache_invalidation.invalidate_conversation_mode_profile_head(writer, 'chat')

    assert reader.state.CONVERSATION_MODE_PROFILE_HEADS['chat'].id == chat_v1.id
    assert (await service.get_cached_current_revision(reader, 'chat')).id == chat_v2.id


@pytest.mark.asyncio
async def test_current_read_checks_database_head_but_reuses_matching_immutable_body(monkeypatch):
    from open_webui.agent import conversation_mode_profile_service as service

    app = _app(None)
    revision = _revision('chat-v1', 'chat')
    head_reads = []
    revision_reads = []

    async def get_head(mode, db=None):
        head_reads.append(str(mode))
        return SimpleNamespace(mode='chat', current_revision_id=revision.id)

    async def get_current_revision(mode, db=None):
        return revision

    async def get_revision(revision_id, expected_mode=None, db=None):
        revision_reads.append(revision_id)
        return revision

    monkeypatch.setattr(service.ConversationModeProfiles, 'get_head', get_head)
    monkeypatch.setattr(service.ConversationModeProfiles, 'get_current_revision', get_current_revision)
    monkeypatch.setattr(service.ConversationModeProfiles, 'get_revision', get_revision)

    assert (await service.get_cached_current_revision(app, 'chat')).id == revision.id
    assert (await service.get_cached_current_revision(app, 'chat')).id == revision.id
    assert head_reads == ['chat', 'chat']
    assert revision_reads == [revision.id]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing_head', 'missing_revision', 'mode_mismatch'])
async def test_current_read_fails_closed_for_invalid_authoritative_state(monkeypatch, failure):
    from open_webui.agent import conversation_mode_profile_service as service

    app = _app(None)
    wrong_mode = _revision('chat-v1', 'agent')

    async def get_head(mode, db=None):
        if failure == 'missing_head':
            return None
        return SimpleNamespace(mode='chat', current_revision_id='chat-v1')

    async def get_current_revision(mode, db=None):
        if failure == 'mode_mismatch':
            return wrong_mode
        return None

    async def get_revision(revision_id, expected_mode=None, db=None):
        if failure == 'mode_mismatch':
            return wrong_mode
        return None

    monkeypatch.setattr(service.ConversationModeProfiles, 'get_head', get_head)
    monkeypatch.setattr(service.ConversationModeProfiles, 'get_current_revision', get_current_revision)
    monkeypatch.setattr(service.ConversationModeProfiles, 'get_revision', get_revision)

    with pytest.raises(Exception) as exc_info:
        await service.get_cached_current_revision(app, 'chat')

    assert getattr(exc_info.value, 'code', None) in {
        'mode_profile_service_unavailable',
        'mode_profile_integrity_error',
    }


@pytest.mark.asyncio
async def test_current_head_database_failure_is_typed_service_unavailable(monkeypatch):
    from open_webui.agent import conversation_mode_profile_service as service
    from sqlalchemy.exc import OperationalError

    app = _app(None)

    async def get_head(mode, db=None):
        raise OperationalError('SELECT head', {}, RuntimeError('database unavailable'))

    async def get_current_revision(mode, db=None):
        raise OperationalError('SELECT current', {}, RuntimeError('database unavailable'))

    monkeypatch.setattr(service.ConversationModeProfiles, 'get_head', get_head)
    monkeypatch.setattr(service.ConversationModeProfiles, 'get_current_revision', get_current_revision)

    with pytest.raises(Exception) as exc_info:
        await service.get_cached_current_revision(app, 'chat')

    assert getattr(exc_info.value, 'code', None) == 'mode_profile_service_unavailable'
    assert getattr(exc_info.value, 'operation', None) == 'read_current_head'


@pytest.mark.asyncio
async def test_public_projection_requires_exactly_chat_and_agent(monkeypatch):
    from open_webui.agent import conversation_mode_profile_service as service

    app = _app(None)
    agent = _revision('agent-v1', 'agent')

    async def get_current(target_app, mode):
        return agent if str(mode) == 'agent' else None

    monkeypatch.setattr(service, 'get_cached_current_revision', get_current)

    with pytest.raises(Exception) as exc_info:
        await service.get_public_conversation_mode_profiles(app)

    assert getattr(exc_info.value, 'code', None) == 'mode_profile_service_unavailable'


@pytest.mark.asyncio
async def test_revision_cache_is_lru_bounded_and_access_refreshes_recency(monkeypatch):
    from open_webui.agent import conversation_mode_profile_service as service

    app = _app(None)
    monkeypatch.setattr(service, 'PROFILE_REVISION_CACHE_MAX_SIZE', 2, raising=False)
    first = _revision('revision-1', 'chat')
    second = _revision('revision-2', 'chat')
    third = _revision('revision-3', 'chat')

    async def unexpected_read(*args, **kwargs):
        raise AssertionError('immutable cache hit should not query the repository')

    monkeypatch.setattr(service.ConversationModeProfiles, 'get_revision', unexpected_read)

    service.cache_profile_revision(app, first)
    service.cache_profile_revision(app, second)
    assert (await service.get_cached_revision(app, first.id, expected_mode='chat')).id == first.id
    service.cache_profile_revision(app, third)

    assert list(service.get_profile_revision_cache(app)) == [first.id, third.id]


def test_revision_cache_repr_does_not_leak_prompt():
    from open_webui.agent import conversation_mode_profile_service as service

    app = _app(None)
    secret = 'PRIVATE ADMINISTRATOR PROMPT'
    revision = _revision('revision-secret', 'chat', prompt=secret)

    service.cache_profile_revision(app, revision)

    assert secret not in repr(revision)
    assert secret not in repr(service.get_profile_revision_cache(app))
