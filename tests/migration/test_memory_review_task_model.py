"""Run actual memory-review functions with in-memory boundaries; no paid inference."""

import ast
import asyncio
import copy
import json
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

ROOT = Path(__file__).resolve().parents[2]
MEMORY = ROOT / 'backend/open_webui/utils/memory.py'


def load_functions(path, names, scope):
    nodes = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)


@pytest.fixture
def env(monkeypatch):
    generate = AsyncMock(return_value={'choices': [{'message': {'content': '{"operations":[]}'}}]})
    chat = ModuleType('open_webui.utils.chat')
    chat.generate_chat_completion = generate
    monkeypatch.setitem(sys.modules, 'open_webui.utils.chat', chat)
    updates = AsyncMock()
    memories = ModuleType('open_webui.routers.memories')
    memories.UpdateMemoriesForm = lambda **kwargs: SimpleNamespace(**kwargs)
    memories.update_memories = updates
    monkeypatch.setitem(sys.modules, 'open_webui.routers.memories', memories)
    values = {
        'memories.enable': True,
        'memories.background_review.enable': True,
        'memories.review_interval_turns': 1,
        'task.model.external': 'small-reviewer',
    }
    config = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: values.get(key)),
        get_many=AsyncMock(side_effect=lambda *keys: {k: values.get(k) for k in keys}),
    )
    scope = {
        'Any': Any,
        'Request': Request,
        'Config': config,
        'asyncio': asyncio,
        'Memories': SimpleNamespace(get_memories_by_user_id=AsyncMock(return_value=[])),
        'get_content_from_message': lambda m: m.get('content', ''),
        'model_allows_memory': lambda m: True,
        'has_permission': AsyncMock(return_value=True),
        'JSONCodec': SimpleNamespace(loads=json.loads),
        'log': logging.getLogger('memory-review-test'),
    }
    load_functions(MEMORY, {'_review_memory', '_generate_memory_operations', 'review_memory_after_turn'}, scope)
    model = {'id': 'large-chat', 'owned_by': 'arena', 'info': {'meta': {'model_ids': ['large-chat']}}}
    state = {
        'model': model,
        'direct': True,
        'metadata': {'selected_model_id': 'large-chat', 'model': model, 'task': 'source-task'},
        'user': 'original-user',
    }
    request = Request(
        {'type': 'http', 'headers': [], 'state': state, 'app': SimpleNamespace(state=SimpleNamespace(MODELS={}))}
    )
    user = SimpleNamespace(id='ordinary-user', role='user')
    kwargs = {
        'request': request,
        'user': user,
        'model': model,
        'metadata': {'chat_id': 'chat', 'message_id': 'msg', 'features': {'memory': True}},
        'form_data': {'model': 'large-chat', 'messages': []},
        'assistant_message': {'role': 'assistant', 'content': 'Noted.'},
        'messages': [{'role': 'user', 'content': 'Prefer concise answers.'}],
    }
    return SimpleNamespace(scope=scope, values=values, generate=generate, updates=updates, kwargs=kwargs)


@pytest.mark.parametrize(
    'configured,expected',
    [
        ('small-reviewer', 'small-reviewer'),
        (' small-reviewer \n', 'small-reviewer'),
        ('', 'large-chat'),
        (' \t', 'large-chat'),
        (None, 'large-chat'),
    ],
)
def test_review_model_selection(env, configured, expected):
    env.values['task.model.external'] = configured
    asyncio.run(env.scope['_review_memory'](**env.kwargs))
    assert env.generate.await_args.kwargs['form_data']['model'] == expected
    assert env.generate.await_args.kwargs['user'] is env.kwargs['user']
    assert env.generate.await_count == 1


def test_selected_reviewer_isolated_from_source_model_state(env):
    before = copy.deepcopy(env.kwargs['model'])
    metadata_before = copy.deepcopy(env.kwargs['request'].state.metadata)
    asyncio.run(env.scope['_review_memory'](**env.kwargs))
    review_request = env.generate.await_args.args[0]
    assert review_request is not env.kwargs['request']
    assert not hasattr(review_request.state, 'model')
    assert not hasattr(review_request.state, 'direct')
    assert not hasattr(review_request.state, 'metadata')
    assert review_request.state.user == 'original-user'
    assert env.kwargs['model'] == before
    assert env.kwargs['request'].state.metadata == metadata_before
    assert env.kwargs['request'].state.direct is True
    assert env.kwargs['form_data']['model'] == 'large-chat'
    assert env.generate.await_args.kwargs['form_data']['metadata'] == {
        'task': 'memory_review',
        'chat_id': 'chat',
        'message_id': 'msg',
    }


def test_empty_external_model_uses_form_model_if_model_object_absent(env):
    env.kwargs['model'] = None
    env.values['task.model.external'] = ''
    asyncio.run(env.scope['_review_memory'](**env.kwargs))
    assert env.generate.await_args.kwargs['form_data']['model'] == 'large-chat'


def test_existing_task_api_reads_the_same_config_key(env):
    task_path = ROOT / 'backend/open_webui/routers/tasks.py'
    load_functions(task_path, {'get_config_values'}, env.scope)
    keys = next(
        node.value
        for node in ast.parse(task_path.read_text()).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'TASK_CONFIG_KEYS' for t in node.targets)
    )
    key_map = ast.literal_eval(keys)
    assert key_map['TASK_MODEL_EXTERNAL'] == 'task.model.external'
    result = asyncio.run(env.scope['get_config_values']({'TASK_MODEL_EXTERNAL': key_map['TASK_MODEL_EXTERNAL']}))
    assert result == {'TASK_MODEL_EXTERNAL': 'small-reviewer'}


def test_operations_update_original_user_memory(env):
    operations = [{'action': 'add', 'type': 'user', 'content': 'Prefer concise answers.'}]
    env.generate.return_value = {'choices': [{'message': {'content': json.dumps({'operations': operations})}}]}
    asyncio.run(env.scope['_review_memory'](**env.kwargs))
    request, form, user = env.updates.await_args.args
    assert request is env.kwargs['request'] and user is env.kwargs['user']
    assert form.operations == operations and form.source == 'background_review'


@pytest.mark.parametrize('failure', [ValueError('Model not found'), HTTPException(403, 'Access denied')])
def test_failure_does_not_retry_or_fall_back(env, failure):
    env.generate.side_effect = failure
    with pytest.raises(type(failure)):
        asyncio.run(env.scope['_review_memory'](**env.kwargs))
    assert env.generate.await_count == 1
    assert env.generate.await_args.kwargs['form_data']['model'] == 'small-reviewer'
    env.updates.assert_not_awaited()


def test_real_chat_router_rejects_inaccessible_reviewer(env):
    """Execute actual generation entry and access check before any provider code."""
    denied = AsyncMock(side_effect=HTTPException(403, 'Access denied'))
    scope = {
        'Request': Request,
        'Any': Any,
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'log': logging.getLogger('memory-review-test'),
        'check_model_access': denied,
    }
    load_functions(ROOT / 'backend/open_webui/utils/chat.py', {'generate_chat_completion'}, scope)
    sys.modules['open_webui.utils.chat'].generate_chat_completion = scope['generate_chat_completion']
    env.kwargs['request'].app.state.MODELS = {'small-reviewer': {'id': 'small-reviewer', 'owned_by': 'openai'}}
    with pytest.raises(HTTPException) as error:
        asyncio.run(env.scope['_review_memory'](**env.kwargs))
    assert error.value.status_code == 403
    denied.assert_awaited_once_with(env.kwargs['user'], {'id': 'small-reviewer', 'owned_by': 'openai'})
    env.updates.assert_not_awaited()


def test_real_chat_router_rejects_missing_reviewer(env):
    scope = {
        'Request': Request,
        'Any': Any,
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'log': logging.getLogger('memory-review-test'),
    }
    load_functions(ROOT / 'backend/open_webui/utils/chat.py', {'generate_chat_completion'}, scope)
    sys.modules['open_webui.utils.chat'].generate_chat_completion = scope['generate_chat_completion']
    with pytest.raises(Exception, match='Model not found'):
        asyncio.run(env.scope['_review_memory'](**env.kwargs))
    env.updates.assert_not_awaited()


def test_disabled_background_review_never_generates(env):
    env.values['memories.background_review.enable'] = False
    asyncio.run(env.scope['review_memory_after_turn'](**env.kwargs))
    env.generate.assert_not_awaited()
    env.scope['Config'].get.assert_not_awaited()


def test_background_failure_logged_without_private_error(env, caplog):
    env.generate.side_effect = HTTPException(403, 'private conversation or credential')

    async def run():
        await env.scope['review_memory_after_turn'](**env.kwargs)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    with caplog.at_level(logging.WARNING, logger='memory-review-test'):
        asyncio.run(run())
    assert 'Memory review failed (HTTPException)' in caplog.text
    assert 'private conversation' not in caplog.text
    assert env.generate.await_count == 1


def test_no_independent_config_or_new_ui():
    for relative in [
        'backend/open_webui/config.py',
        'backend/open_webui/routers/auths.py',
        'src/lib/components/admin/Settings/General.svelte',
    ]:
        source = (ROOT / relative).read_text()
        assert 'MEMORY_BACKGROUND_REVIEW_MODEL' not in source
        assert 'memories.background_review.model' not in source
    assert "Config.get('task.model.external')" in MEMORY.read_text()
