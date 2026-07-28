from __future__ import annotations

from types import SimpleNamespace

import pytest
from open_webui.agent import conversation_mode_profile_service as service
from open_webui.agent.conversation_mode_profiles import ProfileDefaults


def _resolver():
    resolver = getattr(service, 'resolve_mode_profile_capabilities', None)
    assert callable(resolver), 'runtime capability resolver is not implemented'
    return resolver


def _model(*, capabilities=None, filter_ids=None, defaults=None):
    meta = {
        'capabilities': capabilities or {},
        'filterIds': filter_ids or [],
        **(defaults or {}),
    }
    return {'id': 'model-a', 'info': {'meta': meta}}


def _user(*, role='user'):
    return SimpleNamespace(id='user-1', role=role)


@pytest.fixture
def capability_boundaries(monkeypatch):  # noqa: C901
    state = SimpleNamespace(
        tools={},
        skills=[],
        functions=[],
        global_filters=[],
        config={
            'terminal_server.connections': [],
            'user.permissions': {},
        },
        feature_config={
            'web.search.enable': True,
            'code_interpreter.enable': True,
            'image_generation.enable': True,
        },
        feature_permissions={
            'features.web_search': True,
            'features.code_interpreter': True,
            'features.image_generation': True,
        },
        terminal_access={},
    )

    async def get_tools_by_ids(ids):
        return {tool_id: state.tools[tool_id] for tool_id in ids if tool_id in state.tools}

    async def get_skills_by_user_id(user_id, permission='read'):
        return list(state.skills)

    async def get_functions_by_ids(ids):
        by_id = {function.id: function for function in state.functions}
        return [by_id[function_id] for function_id in ids if function_id in by_id]

    async def get_global_filters():
        return list(state.global_filters)

    async def config_get(key, default=None):
        return state.config.get(key, default)

    async def config_get_many(*keys):
        return {key: state.feature_config.get(key) for key in keys}

    async def has_permission(user_id, key, default_permissions):
        return state.feature_permissions.get(key, False)

    async def terminal_access(user, connection, user_group_ids=None):
        connection_id = connection.get('id') or (connection.get('info') or {}).get('id')
        return state.terminal_access.get(connection_id, False)

    for dependency in ('Tools', 'Skills', 'Functions'):
        if not hasattr(service, dependency):
            monkeypatch.setattr(service, dependency, SimpleNamespace(), raising=False)
    monkeypatch.setattr(service.Tools, 'get_tools_by_ids', get_tools_by_ids, raising=False)
    monkeypatch.setattr(
        service.Skills,
        'get_skills_by_user_id',
        get_skills_by_user_id,
        raising=False,
    )
    monkeypatch.setattr(
        service.Functions,
        'get_functions_by_ids',
        get_functions_by_ids,
        raising=False,
    )
    monkeypatch.setattr(
        service.Functions,
        'get_global_filter_functions',
        get_global_filters,
        raising=False,
    )
    monkeypatch.setattr(service.Config, 'get', config_get)
    monkeypatch.setattr(service.Config, 'get_many', config_get_many)
    monkeypatch.setattr(service, 'has_permission', has_permission, raising=False)
    monkeypatch.setattr(service, 'has_connection_access', terminal_access, raising=False)
    return state


@pytest.mark.asyncio
async def test_runtime_drift_selectively_omits_only_unavailable_capabilities(
    capability_boundaries,
):
    state = capability_boundaries
    state.tools = {
        'tool-ok': SimpleNamespace(id='tool-ok', user_id='user-1', access_grants=[]),
        'tool-denied': SimpleNamespace(id='tool-denied', user_id='user-2', access_grants=[]),
    }
    state.skills = [
        SimpleNamespace(id='skill-ok', is_active=True),
        SimpleNamespace(id='skill-inactive', is_active=False),
    ]
    state.functions = [
        SimpleNamespace(id='filter-ok', type='filter', is_active=True),
    ]
    state.config['terminal_server.connections'] = [
        {'id': 'terminal-denied', 'enabled': True, 'config': {'access_grants': []}},
    ]
    state.feature_permissions['features.image_generation'] = False

    resolution = await _resolver()(
        SimpleNamespace(state=SimpleNamespace()),
        profile_defaults=ProfileDefaults(
            tool_ids=('tool-ok', 'tool-denied'),
            skill_ids=('skill-ok', 'skill-inactive'),
            filter_ids=('filter-ok', 'filter-missing'),
            terminal_id='terminal-denied',
            feature_ids=('web_search', 'image_generation'),
        ),
        model=_model(
            capabilities={
                'function_calling': True,
                'terminal': True,
                'web_search': True,
                'image_generation': True,
            },
            filter_ids=['filter-ok', 'filter-missing'],
        ),
        user=_user(),
        request_values={},
    )

    assert resolution.tool_ids == ['tool-ok']
    assert resolution.skill_ids == ['skill-ok']
    assert resolution.filter_ids == ['filter-ok']
    assert resolution.terminal_id is None
    assert resolution.feature_ids == ['web_search']
    omitted = {(warning.category, warning.reason): warning.resource_ids for warning in resolution.warnings}
    assert omitted[('tools', 'unavailable')] == ['tool-denied']
    assert omitted[('skills', 'inactive')] == ['skill-inactive']
    assert omitted[('filters', 'unavailable')] == ['filter-missing']
    assert omitted[('terminal', 'unavailable')] == ['terminal-denied']
    assert omitted[('features', 'forbidden')] == ['image_generation']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('terminal_access', 'expected_terminal', 'expected_features'),
    [
        (False, None, ['code_interpreter']),
        (True, 'terminal-1', []),
    ],
)
async def test_terminal_code_interpreter_arbitration_runs_after_live_filtering(
    capability_boundaries,
    terminal_access,
    expected_terminal,
    expected_features,
):
    state = capability_boundaries
    state.config['terminal_server.connections'] = [
        {'id': 'terminal-1', 'enabled': True, 'config': {'access_grants': []}},
    ]
    state.terminal_access['terminal-1'] = terminal_access

    resolution = await _resolver()(
        SimpleNamespace(state=SimpleNamespace()),
        profile_defaults=ProfileDefaults(
            terminal_id='terminal-1',
        ),
        model=_model(
            capabilities={
                'function_calling': True,
                'terminal': True,
                'code_interpreter': True,
            },
            defaults={'defaultFeatureIds': ['code_interpreter']},
        ),
        user=_user(),
        request_values={},
    )

    assert resolution.terminal_id == expected_terminal
    assert resolution.feature_ids == expected_features
    arbitration_warnings = [warning for warning in resolution.warnings if warning.reason == 'terminal_conflict']
    assert bool(arbitration_warnings) is terminal_access


@pytest.mark.asyncio
async def test_profile_defaults_never_grant_tool_or_skill_access(
    capability_boundaries,
):
    state = capability_boundaries
    state.tools = {
        'private-tool': SimpleNamespace(
            id='private-tool',
            user_id='another-user',
            access_grants=[],
        )
    }
    state.skills = []

    resolution = await _resolver()(
        SimpleNamespace(state=SimpleNamespace()),
        profile_defaults=ProfileDefaults(
            tool_ids=('private-tool',),
            skill_ids=('private-skill',),
        ),
        model=_model(capabilities={'function_calling': True}),
        user=_user(),
        request_values={},
    )

    assert resolution.tool_ids == []
    assert resolution.skill_ids == []


@pytest.mark.asyncio
async def test_explicit_openapi_server_tool_is_revalidated_against_live_connection_access(
    capability_boundaries,
):
    state = capability_boundaries
    state.config['tool_server.connections'] = [
        {
            'type': 'openapi',
            'enabled': True,
            'info': {'id': 'external-1'},
            'config': {'access_grants': []},
        }
    ]
    state.terminal_access['external-1'] = True

    resolution = await _resolver()(
        SimpleNamespace(state=SimpleNamespace()),
        profile_defaults=ProfileDefaults(),
        model=_model(capabilities={'function_calling': True}),
        user=_user(),
        request_values={'tool_ids': ['server:openapi:external-1']},
    )

    assert resolution.tool_ids == ['server:openapi:external-1']
    assert resolution.warnings == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'request_values',
    [
        {'tool_ids': 'tool-1'},
        {'skill_ids': ['skill-1', 'skill-1']},
        {'filter_ids': [' padded ']},
        {'terminal_id': 7},
        {'features': {'web_search': 'yes'}},
    ],
)
async def test_malformed_client_capability_values_raise_stable_request_error(
    capability_boundaries,
    request_values,
):
    error_type = getattr(service, 'ModeProfileCapabilityRequestError', None)
    assert error_type is not None

    with pytest.raises(error_type) as exc_info:
        await _resolver()(
            SimpleNamespace(state=SimpleNamespace()),
            profile_defaults=ProfileDefaults(),
            model=_model(),
            user=_user(),
            request_values=request_values,
        )

    assert exc_info.value.code == 'invalid_mode_profile_capability_request'


@pytest.mark.asyncio
async def test_explicit_terminal_code_interpreter_combination_is_rejected(
    capability_boundaries,
):
    error_type = getattr(service, 'ModeProfileCapabilityRequestError', None)
    assert error_type is not None

    with pytest.raises(error_type) as exc_info:
        await _resolver()(
            SimpleNamespace(state=SimpleNamespace()),
            profile_defaults=ProfileDefaults(),
            model=_model(),
            user=_user(),
            request_values={
                'terminal_id': 'terminal-1',
                'features': {'code_interpreter': True},
            },
        )

    assert exc_info.value.reason == 'terminal_code_interpreter_conflict'
