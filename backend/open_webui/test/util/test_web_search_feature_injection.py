"""Unit tests for web_search feature toggle → tool injection in native FC mode."""

import types

import pytest
from open_webui.utils import middleware
from open_webui.utils import tools as tools_mod


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

async def _convert_images_passthrough(form_data, *args, **kwargs):
    return form_data


async def _pipeline_passthrough(request, form_data, *args, **kwargs):
    return form_data


async def _passthrough_messages(messages, *args, **kwargs):
    return messages


async def _no_event(*args, **kwargs):
    async def emit(event):
        return None

    return emit


async def _no_oauth(*args, **kwargs):
    return None


async def _no_folder(*args, **kwargs):
    return None


async def _empty_filter_ids(*args, **kwargs):
    return []


async def _empty_functions(*args, **kwargs):
    return []


async def _empty_filter_result(*args, form_data=None, **kwargs):
    return form_data, {}


async def _no_terminal_tools(*args, **kwargs):
    return ({}, None)


async def _no_legacy_files(**kwargs):
    return kwargs['form_data'], []


async def _fake_get_tools(request, tool_ids, user, extra_params):
    return {}


async def _admin_all_permission(user_id, permission, config, **kwargs):
    # Mirror access_control.has_permission for admin: always allow
    return True


async def _config_driven_permission(user_id, permission, user_permissions, **kwargs):
    # Mirror access_control.has_permission semantics without DB access.
    # Real has_permission signature is (user_id, permission, user_permissions_dict)
    # where user_permissions_dict is config.USER_PERMISSIONS (a dict, not the config object).
    if isinstance(user_permissions, dict) and permission in user_permissions:
        return bool(user_permissions[permission])
    return True


# ---------------------------------------------------------------------------
# Fixtures — minimal request / user / model
# ---------------------------------------------------------------------------


def _make_request(**overrides):
    cfg = types.SimpleNamespace(
        TASK_MODEL='',
        TASK_MODEL_EXTERNAL='',
        ENABLE_WEB_SEARCH=False,
        ENABLE_IMAGE_GENERATION=False,
        ENABLE_IMAGE_EDIT=False,
        ENABLE_CODE_INTERPRETER=False,
        ENABLE_NOTES=False,
        ENABLE_CHANNELS=False,
        ENABLE_AUTOMATIONS=False,
        ENABLE_CALENDAR=False,
        USER_PERMISSIONS={},
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)

    model = {
        'id': 'model-1',
        'owned_by': 'openai',
        'info': {
            'meta': {
                'builtinTools': {
                    'time': False,
                    'knowledge': False,
                    'chats': False,
                    'memory': False,
                    'web_search': False,
                    'image_generation': False,
                    'code_interpreter': False,
                    'notes': False,
                    'channels': False,
                    'tasks': False,
                    'automations': False,
                    'calendar': False,
                    'skills': False,
                },
                'capabilities': {'builtin_tools': True},
            }
        },
    }

    return types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=cfg,
                MODELS={model['id']: model},
            )
        ),
        state=types.SimpleNamespace(direct=False),
    )


def _user():
    return types.SimpleNamespace(id='user-1', role='admin')


# ---------------------------------------------------------------------------
# Test 1 — Native FC: tools injected when features.web_search=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_native_fc_injects_tools(monkeypatch):
    """features.web_search=true + function_calling=native + ENABLE_WEB_SEARCH=True
    + user has permission → web_search_research and search_web auto-injected."""

    request = _make_request(ENABLE_WEB_SEARCH=True)
    user = _user()
    model = {
        'id': 'model-1',
        'owned_by': 'openai',
        'info': {
            'meta': {
                'builtinTools': {
                    'time': False,
                    'knowledge': False,
                    'chats': False,
                    'memory': False,
                    'web_search': False,
                    'image_generation': False,
                    'code_interpreter': False,
                    'notes': False,
                    'channels': False,
                    'tasks': False,
                    'automations': False,
                    'calendar': False,
                    'skills': False,
                },
                'capabilities': {'builtin_tools': True},
            }
        },
    }

    # Let get_builtin_tools run for real to verify the force path.
    monkeypatch.setattr(middleware, 'convert_url_images_to_base64', _convert_images_passthrough)
    monkeypatch.setattr(middleware, 'get_event_emitter', _no_event)
    monkeypatch.setattr(middleware, 'get_event_call', _no_event)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', _no_oauth)
    monkeypatch.setattr(middleware.Chats, 'get_chat_folder_id', _no_folder)
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', _pipeline_passthrough)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', _empty_filter_ids)
    monkeypatch.setattr(middleware.Functions, 'get_functions_by_ids', _empty_functions)
    monkeypatch.setattr(middleware, 'process_filter_functions', _empty_filter_result)
    monkeypatch.setattr(middleware, 'add_file_context', _passthrough_messages)
    monkeypatch.setattr(middleware, 'get_terminal_tools', _no_terminal_tools)
    monkeypatch.setattr(middleware, 'apply_legacy_file_retrieval_if_needed', _no_legacy_files)
    monkeypatch.setattr(tools_mod, 'get_tools', _fake_get_tools)
    monkeypatch.setattr(tools_mod, 'has_permission', _admin_all_permission)
    monkeypatch.setattr(middleware, 'has_permission', _admin_all_permission)

    form_data, metadata, events = await middleware.process_chat_payload(
        request,
        {
            'model': model['id'],
            'messages': [{'role': 'user', 'content': 'Search for latest AI news.'}],
            'features': {'web_search': True},
        },
        user,
        {
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'params': {'function_calling': 'native'},
        },
        model,
    )

    tools = metadata.get('tools', {})
    assert 'web_search_research' in tools, (
        'web_search_research should be force-injected when features.web_search=true and function_calling=native'
    )
    assert 'search_web' in tools, (
        'search_web should be force-injected when features.web_search=true and function_calling=native'
    )

    # Verify the tools have the expected structure
    for name in ('web_search_research', 'search_web'):
        t = tools[name]
        assert t.get('tool_id') == f'builtin:{name}', f'{name} should have tool_id builtin:{name}'
        assert t.get('type') == 'builtin', f'{name} should have type builtin'
        assert 'spec' in t, f'{name} should have a spec'
        assert callable(t.get('callable')), f'{name} should have a callable'


# ---------------------------------------------------------------------------
# Test 2 — Legacy path: chat_web_search_handler called, not bypassed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_legacy_calls_handler(monkeypatch):
    """features.web_search=true + function_calling NOT native → chat_web_search_handler
    is called (legacy behaviour preserved)."""

    handler_called = False

    async def fake_web_search_handler(request, form_data, extra_params, user):
        nonlocal handler_called
        handler_called = True
        return form_data

    request = _make_request()
    user = _user()
    model = {
        'id': 'model-1',
        'owned_by': 'openai',
        'info': {'meta': {'capabilities': {'builtin_tools': True}}},
    }

    monkeypatch.setattr(middleware, 'convert_url_images_to_base64', _convert_images_passthrough)
    monkeypatch.setattr(middleware, 'get_event_emitter', _no_event)
    monkeypatch.setattr(middleware, 'get_event_call', _no_event)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', _no_oauth)
    monkeypatch.setattr(middleware.Chats, 'get_chat_folder_id', _no_folder)
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', _pipeline_passthrough)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', _empty_filter_ids)
    monkeypatch.setattr(middleware.Functions, 'get_functions_by_ids', _empty_functions)
    monkeypatch.setattr(middleware, 'process_filter_functions', _empty_filter_result)
    monkeypatch.setattr(middleware, 'chat_web_search_handler', fake_web_search_handler)
    monkeypatch.setattr(middleware, 'apply_legacy_file_retrieval_if_needed', _no_legacy_files)
    monkeypatch.setattr(middleware, 'get_builtin_tools', lambda *a, **kw: {})
    monkeypatch.setattr(middleware, 'get_terminal_tools', _no_terminal_tools)
    monkeypatch.setattr(middleware, 'add_file_context', _passthrough_messages)
    monkeypatch.setattr(tools_mod, 'get_tools', _fake_get_tools)

    await middleware.process_chat_payload(
        request,
        {
            'model': model['id'],
            'messages': [{'role': 'user', 'content': 'Tell me about Paris.'}],
            'features': {'web_search': True},
        },
        user,
        {
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'params': {'function_calling': ''},  # not native
        },
        model,
    )

    assert handler_called, (
        'chat_web_search_handler should be called for legacy (non-native) paths'
    )


# ---------------------------------------------------------------------------
# Test 3 — Native FC: chat_web_search_handler is NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_native_fc_skips_handler(monkeypatch):
    """features.web_search=true + function_calling=native → chat_web_search_handler
    is NOT called (tools are injected instead)."""

    handler_called = False

    async def fake_web_search_handler(request, form_data, extra_params, user):
        nonlocal handler_called
        handler_called = True
        return form_data

    request = _make_request(ENABLE_WEB_SEARCH=True)
    user = _user()
    model = {
        'id': 'model-1',
        'owned_by': 'openai',
        'info': {
            'meta': {
                'builtinTools': {
                    'time': False,
                    'knowledge': False,
                    'chats': False,
                    'memory': False,
                    'web_search': False,
                    'image_generation': False,
                    'code_interpreter': False,
                    'notes': False,
                    'channels': False,
                    'tasks': False,
                    'automations': False,
                    'calendar': False,
                    'skills': False,
                },
                'capabilities': {'builtin_tools': True},
            }
        },
    }

    monkeypatch.setattr(middleware, 'convert_url_images_to_base64', _convert_images_passthrough)
    monkeypatch.setattr(middleware, 'get_event_emitter', _no_event)
    monkeypatch.setattr(middleware, 'get_event_call', _no_event)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', _no_oauth)
    monkeypatch.setattr(middleware.Chats, 'get_chat_folder_id', _no_folder)
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', _pipeline_passthrough)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', _empty_filter_ids)
    monkeypatch.setattr(middleware.Functions, 'get_functions_by_ids', _empty_functions)
    monkeypatch.setattr(middleware, 'process_filter_functions', _empty_filter_result)
    monkeypatch.setattr(middleware, 'chat_web_search_handler', fake_web_search_handler)
    monkeypatch.setattr(middleware, 'apply_legacy_file_retrieval_if_needed', _no_legacy_files)
    monkeypatch.setattr(middleware, 'get_terminal_tools', _no_terminal_tools)
    monkeypatch.setattr(middleware, 'add_file_context', _passthrough_messages)
    monkeypatch.setattr(tools_mod, 'get_tools', _fake_get_tools)
    monkeypatch.setattr(tools_mod, 'has_permission', _admin_all_permission)
    monkeypatch.setattr(middleware, 'has_permission', _admin_all_permission)

    await middleware.process_chat_payload(
        request,
        {
            'model': model['id'],
            'messages': [{'role': 'user', 'content': 'Search for latest AI news.'}],
            'features': {'web_search': True},
        },
        user,
        {
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'params': {'function_calling': 'native'},
        },
        model,
    )

    assert not handler_called, (
        'chat_web_search_handler should NOT be called in native FC mode'
    )


# ---------------------------------------------------------------------------
# Test 4 — Idempotency: forced tools do not duplicate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_native_fc_idempotent(monkeypatch):
    """When web_search_research is already in tools_dict, the force-injection
    does not create a duplicate."""

    request = _make_request(ENABLE_WEB_SEARCH=True)
    user = _user()
    model = {
        'id': 'model-1',
        'owned_by': 'openai',
        'info': {
            'meta': {
                'builtinTools': {
                    'time': False,
                    'knowledge': False,
                    'chats': False,
                    'memory': False,
                    'web_search': False,
                    'image_generation': False,
                    'code_interpreter': False,
                    'notes': False,
                    'channels': False,
                    'tasks': False,
                    'automations': False,
                    'calendar': False,
                    'skills': False,
                },
                'capabilities': {'builtin_tools': True},
            }
        },
    }

    monkeypatch.setattr(middleware, 'convert_url_images_to_base64', _convert_images_passthrough)
    monkeypatch.setattr(middleware, 'get_event_emitter', _no_event)
    monkeypatch.setattr(middleware, 'get_event_call', _no_event)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', _no_oauth)
    monkeypatch.setattr(middleware.Chats, 'get_chat_folder_id', _no_folder)
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', _pipeline_passthrough)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', _empty_filter_ids)
    monkeypatch.setattr(middleware.Functions, 'get_functions_by_ids', _empty_functions)
    monkeypatch.setattr(middleware, 'process_filter_functions', _empty_filter_result)
    monkeypatch.setattr(middleware, 'add_file_context', _passthrough_messages)
    monkeypatch.setattr(middleware, 'get_terminal_tools', _no_terminal_tools)
    monkeypatch.setattr(middleware, 'apply_legacy_file_retrieval_if_needed', _no_legacy_files)
    monkeypatch.setattr(tools_mod, 'get_tools', _fake_get_tools)
    monkeypatch.setattr(tools_mod, 'has_permission', _admin_all_permission)
    monkeypatch.setattr(middleware, 'has_permission', _admin_all_permission)

    # Use the real get_builtin_tools (which will force-inject via the elif path)
    form_data, metadata, events = await middleware.process_chat_payload(
        request,
        {
            'model': model['id'],
            'messages': [{'role': 'user', 'content': 'Search for latest AI news.'}],
            'features': {'web_search': True},
        },
        user,
        {
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'params': {'function_calling': 'native'},
        },
        model,
    )

    tools = metadata.get('tools', {})
    assert 'web_search_research' in tools
    assert 'search_web' in tools

    # Count occurrences — should be exactly one each
    tool_names = list(tools.keys())
    assert tool_names.count('web_search_research') == 1, (
        'web_search_research should appear exactly once (idempotent)'
    )
    assert tool_names.count('search_web') == 1, (
        'search_web should appear exactly once (idempotent)'
    )


# ---------------------------------------------------------------------------
# Test 5 — Global ENABLE_WEB_SEARCH=False blocks injection even in native FC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_native_fc_blocked_when_globally_disabled(monkeypatch):
    """features.web_search=true + function_calling=native but
    ENABLE_WEB_SEARCH=False → tools must NOT be injected (global switch
    is a hard security boundary, mirroring process_web_search:2519)."""

    request = _make_request(ENABLE_WEB_SEARCH=False)
    user = _user()
    model = {
        'id': 'model-1',
        'owned_by': 'openai',
        'info': {
            'meta': {
                'builtinTools': {'web_search': False},
                'capabilities': {'builtin_tools': True},
            }
        },
    }

    monkeypatch.setattr(middleware, 'convert_url_images_to_base64', _convert_images_passthrough)
    monkeypatch.setattr(middleware, 'get_event_emitter', _no_event)
    monkeypatch.setattr(middleware, 'get_event_call', _no_event)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', _no_oauth)
    monkeypatch.setattr(middleware.Chats, 'get_chat_folder_id', _no_folder)
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', _pipeline_passthrough)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', _empty_filter_ids)
    monkeypatch.setattr(middleware.Functions, 'get_functions_by_ids', _empty_functions)
    monkeypatch.setattr(middleware, 'process_filter_functions', _empty_filter_result)
    monkeypatch.setattr(middleware, 'add_file_context', _passthrough_messages)
    monkeypatch.setattr(middleware, 'get_terminal_tools', _no_terminal_tools)
    monkeypatch.setattr(middleware, 'apply_legacy_file_retrieval_if_needed', _no_legacy_files)
    monkeypatch.setattr(tools_mod, 'get_tools', _fake_get_tools)
    monkeypatch.setattr(tools_mod, 'has_permission', _admin_all_permission)
    monkeypatch.setattr(middleware, 'has_permission', _admin_all_permission)

    form_data, metadata, events = await middleware.process_chat_payload(
        request,
        {
            'model': model['id'],
            'messages': [{'role': 'user', 'content': 'Search for latest AI news.'}],
            'features': {'web_search': True},
        },
        user,
        {
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'params': {'function_calling': 'native'},
        },
        model,
    )

    tools = metadata.get('tools', {})
    assert 'web_search_research' not in tools, (
        'web_search_research must NOT be injected when ENABLE_WEB_SEARCH=False'
    )
    assert 'search_web' not in tools, (
        'search_web must NOT be injected when ENABLE_WEB_SEARCH=False'
    )


# ---------------------------------------------------------------------------
# Test 6 — User without web_search permission is blocked in native FC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_native_fc_blocked_without_user_permission(monkeypatch):
    """features.web_search=true + function_calling=native + ENABLE_WEB_SEARCH=True
    but non-admin user with USER_PERMISSIONS['features.web_search']=False →
    tools must NOT be injected."""

    # Non-admin user, web_search explicitly denied in USER_PERMISSIONS
    request = _make_request(ENABLE_WEB_SEARCH=True, USER_PERMISSIONS={'features.web_search': False})
    user = types.SimpleNamespace(id='user-no-perm', role='user')
    model = {
        'id': 'model-1',
        'owned_by': 'openai',
        'info': {
            'meta': {
                'builtinTools': {'web_search': False},
                'capabilities': {'builtin_tools': True},
            }
        },
    }

    monkeypatch.setattr(middleware, 'convert_url_images_to_base64', _convert_images_passthrough)
    monkeypatch.setattr(middleware, 'get_event_emitter', _no_event)
    monkeypatch.setattr(middleware, 'get_event_call', _no_event)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', _no_oauth)
    monkeypatch.setattr(middleware.Chats, 'get_chat_folder_id', _no_folder)
    monkeypatch.setattr(middleware, 'process_pipeline_inlet_filter', _pipeline_passthrough)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', _empty_filter_ids)
    monkeypatch.setattr(middleware.Functions, 'get_functions_by_ids', _empty_functions)
    monkeypatch.setattr(middleware, 'process_filter_functions', _empty_filter_result)
    monkeypatch.setattr(middleware, 'add_file_context', _passthrough_messages)
    monkeypatch.setattr(middleware, 'get_terminal_tools', _no_terminal_tools)
    monkeypatch.setattr(middleware, 'apply_legacy_file_retrieval_if_needed', _no_legacy_files)
    monkeypatch.setattr(tools_mod, 'get_tools', _fake_get_tools)
    monkeypatch.setattr(tools_mod, 'has_permission', _config_driven_permission)
    monkeypatch.setattr(middleware, 'has_permission', _config_driven_permission)

    form_data, metadata, events = await middleware.process_chat_payload(
        request,
        {
            'model': model['id'],
            'messages': [{'role': 'user', 'content': 'Search for latest AI news.'}],
            'features': {'web_search': True},
        },
        user,
        {
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'params': {'function_calling': 'native'},
        },
        model,
    )

    tools = metadata.get('tools', {})
    assert 'web_search_research' not in tools, (
        'web_search_research must NOT be injected when non-admin user lacks web_search permission'
    )
    assert 'search_web' not in tools, (
        'search_web must NOT be injected when non-admin user lacks web_search permission'
    )
