import os
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import pytest

from open_webui.agent.tool_authority import build_tool_access_envelope
from open_webui.routers.agent_service import (
    _external_tool_source_id_from_snapshot,
    _rebuild_agent_tool_registry,
    _rebuild_external_tools,
    _registry_from_snapshot,
    _snapshot_tools,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class FakeRunStore:
    """Stores a single run that get_run() returns."""

    def __init__(self, run):
        self.run = run

    async def get_run(self, run_id):
        if self.run.id == run_id:
            return self.run
        return None


class FakeUserStore:
    """Returns a fixed user model."""

    def __init__(self, user):
        self.user = user

    async def get_user_by_id(self, user_id):
        if self.user.id == user_id:
            return self.user
        return None


def _fake_run(**kw):
    return SimpleNamespace(id='run-1', **kw)


def _fake_user(**kw):
    defaults = {'id': 'user-1', 'name': 'Test User', 'email': 'test@test.com', 'role': 'user'}
    defaults.update(kw)
    u = SimpleNamespace(**defaults)

    def _model_dump(mode='json'):
        return defaults

    u.model_dump = _model_dump
    return u


def _snapshot_with_external_tool(
    name='external_search',
    service_part='openapi:abc123',
):
    """Build a snapshot entry for a single external tool."""
    source_id = f'server:{service_part}'
    opaque_id = f'tool:server:{service_part}:{name}'
    return {
        'id': opaque_id,
        'name': name,
        'type': 'external',
        'schema': {
            'name': name,
            'description': 'Search external.',
            'parameters': {'type': 'object', 'properties': {}},
        },
    }


def _snapshot_with_builtin_tool(name='write_note'):
    return {
        'id': f'tool:builtin:{name}:{name}',
        'name': name,
        'type': 'builtin',
        'schema': {
            'name': name,
            'description': 'Write a note.',
            'parameters': {'type': 'object', 'properties': {}},
        },
    }


def _snapshot_envelope(tools):
    return {'tools': tools}


# ---------------------------------------------------------------------------
# _external_tool_source_id_from_snapshot
# ---------------------------------------------------------------------------


def test_external_tool_source_id_openapi():
    tool = _snapshot_with_external_tool('search', 'openapi:abc123')
    assert _external_tool_source_id_from_snapshot(tool) == 'server:openapi:abc123'


def test_external_tool_source_id_plain():
    tool = _snapshot_with_external_tool('search', 'abc')
    assert _external_tool_source_id_from_snapshot(tool) == 'server:abc'


def test_external_tool_source_id_missing_fields():
    assert _external_tool_source_id_from_snapshot({}) is None
    assert _external_tool_source_id_from_snapshot({'id': 'x'}) is None
    assert _external_tool_source_id_from_snapshot({'name': 'x'}) is None


def test_external_tool_source_id_wrong_type():
    tool = {'id': 'tool:terminal:xyz:search', 'name': 'search', 'type': 'terminal'}
    assert _external_tool_source_id_from_snapshot(tool) is None


def test_external_tool_source_id_non_string_fields():
    assert _external_tool_source_id_from_snapshot({'id': None, 'name': 'x'}) is None
    assert _external_tool_source_id_from_snapshot({'id': 'x', 'name': 123}) is None


# ---------------------------------------------------------------------------
# _snapshot_tools
# ---------------------------------------------------------------------------


def test_snapshot_tools_none():
    assert _snapshot_tools(None) == []


def test_snapshot_tools_empty():
    assert _snapshot_tools({}) == []


def test_snapshot_tools_no_tools_key():
    assert _snapshot_tools({'other': 1}) == []


def test_snapshot_tools_filters_non_dicts():
    assert _snapshot_tools({'tools': [1, 'str', None, {}]}) == [{}]


# ---------------------------------------------------------------------------
# _registry_from_snapshot
# ---------------------------------------------------------------------------


def test_registry_from_snapshot_preserves_opaque_ids():
    snapshot = [
        {'id': 'tool:server:openapi:x:search', 'name': 'search', 'type': 'external'},
        {'id': 'tool:server:openapi:x:fetch', 'name': 'fetch', 'type': 'external'},
    ]
    _, current_registry = build_tool_access_envelope(
        {
            'search': {
                'tool_id': 'server:openapi:x',
                'callable': None,
                'spec': {'name': 'search'},
                'type': 'external',
            },
            'fetch': {
                'tool_id': 'server:openapi:x',
                'callable': None,
                'spec': {'name': 'fetch'},
                'type': 'external',
            },
        }
    )
    result = _registry_from_snapshot(snapshot, current_registry)
    assert 'tool:server:openapi:x:search' in result
    assert 'tool:server:openapi:x:fetch' in result
    assert result['tool:server:openapi:x:search']['name'] == 'search'
    assert result['tool:server:openapi:x:fetch']['name'] == 'fetch'


def test_registry_from_snapshot_missing_in_current_is_skipped():
    snapshot = [
        {'id': 'tool:server:openapi:x:search', 'name': 'search', 'type': 'external'},
    ]
    _, current_registry = build_tool_access_envelope(
        {
            'other_tool': {
                'tool_id': 'server:openapi:x',
                'callable': None,
                'spec': {'name': 'other_tool'},
                'type': 'external',
            },
        }
    )
    result = _registry_from_snapshot(snapshot, current_registry)
    assert result == {}


# ---------------------------------------------------------------------------
# _rebuild_external_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_external_tools_empty_when_no_external(monkeypatch):
    run = _fake_run(
        tool_access_snapshot=_snapshot_envelope(
            [_snapshot_with_builtin_tool('write_note')]
        ),
        user_id='user-1',
        chat_id='chat-1',
    )
    user = _fake_user()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    result = await _rebuild_external_tools(
        request, run, user, _snapshot_tools(run.tool_access_snapshot)
    )
    assert result == {}


@pytest.mark.asyncio
async def test_rebuild_external_tools_single_server(monkeypatch):
    calls = []

    async def write_artifact(title: str, content: str):
        calls.append((title, content))
        return {'title': title, 'content': content}

    async def fake_get_tools(request, tool_ids, user, extra_params):
        assert tool_ids == ['server:openapi:abc']
        return {
            'write_artifact': {
                'tool_id': 'server:openapi:abc',
                'callable': write_artifact,
                'spec': {
                    'name': 'write_artifact',
                    'description': 'Write an artifact.',
                    'parameters': {'type': 'object'},
                },
                'type': 'external',
            }
        }

    monkeypatch.setattr(
        'open_webui.routers.agent_service.get_tools', fake_get_tools
    )

    external_tool = _snapshot_with_external_tool('write_artifact', 'openapi:abc')
    run = _fake_run(
        tool_access_snapshot=_snapshot_envelope([external_tool]),
        user_id='user-1',
        chat_id='chat-1',
        assistant_message_id='msg-1',
    )
    user = _fake_user()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    result = await _rebuild_external_tools(
        request, run, user, _snapshot_tools(run.tool_access_snapshot)
    )

    opaque_id = 'tool:server:openapi:abc:write_artifact'
    assert opaque_id in result
    assert result[opaque_id]['name'] == 'write_artifact'
    assert result[opaque_id]['type'] == 'external'
    assert result[opaque_id]['callable'] is write_artifact


@pytest.mark.asyncio
async def test_rebuild_external_tools_multiple_functions_same_server(monkeypatch):
    async def search():
        return 'search result'

    async def fetch():
        return 'fetch result'

    async def fake_get_tools(request, tool_ids, user, extra_params):
        return {
            'search': {
                'tool_id': 'server:openapi:xyz',
                'callable': search,
                'spec': {'name': 'search'},
                'type': 'external',
            },
            'fetch': {
                'tool_id': 'server:openapi:xyz',
                'callable': fetch,
                'spec': {'name': 'fetch'},
                'type': 'external',
            },
        }

    monkeypatch.setattr(
        'open_webui.routers.agent_service.get_tools', fake_get_tools
    )

    tools = [
        _snapshot_with_external_tool('search', 'openapi:xyz'),
        _snapshot_with_external_tool('fetch', 'openapi:xyz'),
    ]
    run = _fake_run(
        tool_access_snapshot=_snapshot_envelope(tools),
        user_id='user-1',
        chat_id='chat-1',
    )
    user = _fake_user()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    result = await _rebuild_external_tools(
        request, run, user, _snapshot_tools(run.tool_access_snapshot)
    )

    assert 'tool:server:openapi:xyz:search' in result
    assert 'tool:server:openapi:xyz:fetch' in result


@pytest.mark.asyncio
async def test_rebuild_external_tools_missing_tool_graceful(monkeypatch):
    """When one tool is missing but another is available, rebuild the available one."""
    async def search():
        return 'search result'

    async def fake_get_tools(request, tool_ids, user, extra_params):
        # Only return 'search' — 'fetch' is missing
        return {
            'search': {
                'tool_id': 'server:openapi:xyz',
                'callable': search,
                'spec': {'name': 'search'},
                'type': 'external',
            },
        }

    monkeypatch.setattr(
        'open_webui.routers.agent_service.get_tools', fake_get_tools
    )

    tools = [
        _snapshot_with_external_tool('search', 'openapi:xyz'),
        _snapshot_with_external_tool('fetch', 'openapi:xyz'),
    ]
    run = _fake_run(
        tool_access_snapshot=_snapshot_envelope(tools),
        user_id='user-1',
        chat_id='chat-1',
    )
    user = _fake_user()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    result = await _rebuild_external_tools(
        request, run, user, _snapshot_tools(run.tool_access_snapshot)
    )

    # Only the available tool should be rebuilt
    assert 'tool:server:openapi:xyz:search' in result
    assert 'tool:server:openapi:xyz:fetch' not in result
    assert len(result) == 1


@pytest.mark.asyncio
async def test_rebuild_external_tools_all_missing_raises(monkeypatch):
    """When no external tools are available, raise 503."""
    from fastapi import HTTPException

    async def fake_get_tools(request, tool_ids, user, extra_params):
        return {}  # Nothing available

    monkeypatch.setattr(
        'open_webui.routers.agent_service.get_tools', fake_get_tools
    )

    tools = [_snapshot_with_external_tool('search', 'openapi:xyz')]
    run = _fake_run(
        tool_access_snapshot=_snapshot_envelope(tools),
        user_id='user-1',
        chat_id='chat-1',
    )
    user = _fake_user()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(HTTPException) as exc_info:
        await _rebuild_external_tools(
            request, run, user, _snapshot_tools(run.tool_access_snapshot)
        )
    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail
    assert detail['code'] == 'agent_tool_registry_rebuild_failed'
    assert detail['tool_source_id'] == 'server:openapi:xyz'
    assert 'search' in detail['tools']


@pytest.mark.asyncio
async def test_rebuild_external_tools_defensive_bad_snapshot_fields(monkeypatch):
    """Snapshot fields that are unparseable should cause graceful skip."""
    async def search():
        return 'result'

    async def fake_get_tools(request, tool_ids, user, extra_params):
        return {
            'search': {
                'tool_id': 'server:openapi:good',
                'callable': search,
                'spec': {'name': 'search'},
                'type': 'external',
            },
        }

    monkeypatch.setattr(
        'open_webui.routers.agent_service.get_tools', fake_get_tools
    )

    good_tool = _snapshot_with_external_tool('search', 'openapi:good')
    # Malformed tool: id is missing, name is int
    bad_tool = {'id': None, 'name': 123, 'type': 'external', 'schema': {}}
    # Tool with wrong prefix (won't be identified as external)
    non_external = {'id': 'tool:builtin:x:x', 'name': 'x', 'type': 'external'}

    tools = [good_tool, bad_tool, non_external]
    run = _fake_run(
        tool_access_snapshot=_snapshot_envelope(tools),
        user_id='user-1',
        chat_id='chat-1',
    )
    user = _fake_user()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    result = await _rebuild_external_tools(
        request, run, user, _snapshot_tools(run.tool_access_snapshot)
    )

    # Only the good tool should be rebuilt
    assert 'tool:server:openapi:good:search' in result
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _rebuild_agent_tool_registry integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_agent_tool_registry_mixed_builtin_external(monkeypatch):
    """End-to-end: rebuild a registry containing both builtin and external tools."""
    async def write_note(title: str, content: str):
        return {'title': title, 'content': content}

    async def external_search(query: str):
        return f'search: {query}'

    async def fake_get_builtin_tools(request, extra_params, features=None, model=None):
        return {
            'write_note': {
                'tool_id': 'builtin:write_note',
                'callable': write_note,
                'spec': {'name': 'write_note', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            },
        }

    async def fake_get_tools(request, tool_ids, user, extra_params):
        return {
            'external_search': {
                'tool_id': 'server:openapi:ext',
                'callable': external_search,
                'spec': {'name': 'external_search', 'parameters': {'type': 'object'}},
                'type': 'external',
            },
        }

    monkeypatch.setattr(
        'open_webui.routers.agent_service.get_builtin_tools', fake_get_builtin_tools
    )
    monkeypatch.setattr(
        'open_webui.routers.agent_service.get_tools', fake_get_tools
    )
    monkeypatch.setattr(
        'open_webui.routers.agent_service.Users', FakeUserStore(_fake_user())
    )

    tools = [
        _snapshot_with_builtin_tool('write_note'),
        _snapshot_with_external_tool('external_search', 'openapi:ext'),
    ]
    run = _fake_run(
        tool_access_snapshot=_snapshot_envelope(tools),
        user_id='user-1',
        chat_id='chat-1',
        assistant_message_id='msg-1',
        leader_model_id='model-1',
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                AGENT_EVENT_STORE=FakeRunStore(run),
                AGENT_TOOL_REGISTRIES={},
                AGENT_TOOL_REGISTRY={},
            )
        )
    )

    result = await _rebuild_agent_tool_registry(request, 'run-1')

    # Both builtin and external should be rebuilt
    assert 'tool:builtin:write_note:write_note' in result
    assert 'tool:server:openapi:ext:external_search' in result
    assert result['tool:builtin:write_note:write_note']['callable'] is write_note
    assert result['tool:server:openapi:ext:external_search']['callable'] is external_search


@pytest.mark.asyncio
async def test_rebuild_agent_tool_registry_external_only(monkeypatch):
    """End-to-end: rebuild a registry containing only external tools."""
    async def search(query: str):
        return f'search: {query}'

    async def fake_get_tools(request, tool_ids, user, extra_params):
        return {
            'search': {
                'tool_id': 'server:openapi:ext',
                'callable': search,
                'spec': {'name': 'search', 'parameters': {'type': 'object'}},
                'type': 'external',
            },
        }

    monkeypatch.setattr(
        'open_webui.routers.agent_service.get_tools', fake_get_tools
    )
    monkeypatch.setattr(
        'open_webui.routers.agent_service.Users', FakeUserStore(_fake_user())
    )

    tools = [_snapshot_with_external_tool('search', 'openapi:ext')]
    run = _fake_run(
        tool_access_snapshot=_snapshot_envelope(tools),
        user_id='user-1',
        chat_id='chat-1',
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                AGENT_EVENT_STORE=FakeRunStore(run),
                AGENT_TOOL_REGISTRIES={},
                AGENT_TOOL_REGISTRY={},
            )
        )
    )

    result = await _rebuild_agent_tool_registry(request, 'run-1')

    assert 'tool:server:openapi:ext:search' in result
    assert result['tool:server:openapi:ext:search']['callable'] is search
    assert len(result) == 1


@pytest.mark.asyncio
async def test_rebuild_agent_tool_registry_none_on_missing_run(monkeypatch):
    """Returns None when there is no matching run."""
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                AGENT_EVENT_STORE=FakeRunStore(None),  # no run
            )
        )
    )
    # FakeRunStore(None) means .get_run() returns None
    # We need to handle this: get_run is None when run is None
    class EmptyStore:
        async def get_run(self, run_id):
            return None

    request.app.state.AGENT_EVENT_STORE = EmptyStore()

    result = await _rebuild_agent_tool_registry(request, 'run-nonexistent')
    assert result is None
