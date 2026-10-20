"""Fixed provider schemas, deferred discovery and dispatch approvals; no provider calls."""

import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from open_webui.tools import builtin
from open_webui.utils import middleware, tool_search

PROFILE = Path(__file__).resolve().parents[2] / 'docs/config/knowledge-tool-search.json'
RESIDENT_KNOWLEDGE = {'search_knowledge_files', 'grep_knowledge_files', 'query_knowledge_files', 'view_knowledge_file'}


def tools_fixture():
    async def result():
        return 'done'

    tools = {
        name: {
            'tool_id': 'builtin:' + name,
            'type': 'builtin',
            'spec': {
                'name': name,
                'description': 'Knowledge discovery' if name == 'search_knowledge_bases' else name,
                'parameters': {'type': 'object', 'properties': {}},
            },
            'callable': AsyncMock(spec=result, return_value='done'),
        }
        for name in (*sorted(RESIDENT_KNOWLEDGE), 'search_knowledge_bases', 'view_file')
    }
    tools['view_file']['hidden'] = True
    return tools


@pytest.mark.asyncio
async def test_discovery_hides_aliases_and_does_not_change_provider_definitions(monkeypatch):
    profile = json.loads(PROFILE.read_text())
    config = {key.rsplit('.', 1)[-1]: value for key, value in profile.items() if not key.endswith('.enable')}
    monkeypatch.setattr(tool_search, 'get_tool_search_config', AsyncMock(return_value=config))
    tools = tools_fixture()
    metadata = {'tools': tools}
    body = {'messages': [{'role': 'system', 'content': 'Stable base'}, {'role': 'user', 'content': 'Find a library'}]}
    deferred = await tool_search.apply_tool_search(body, metadata, tools)
    assert deferred == {'search_knowledge_bases'}
    assert 'view_file' not in body['messages'][0]['content']

    def provider_specs():
        return [tool['spec'] for name, tool in tools.items() if name not in deferred and not tool.get('hidden')]

    original_specs = copy.deepcopy(provider_specs())
    discovered = json.loads(await tools['search_tools']['callable'](query='search_knowledge_bases'))
    assert [tool['name'] for tool in discovered['tools']] == ['search_knowledge_bases']
    assert provider_specs() == original_specs
    assert {spec['name'] for spec in original_specs} == RESIDENT_KNOWLEDGE | {'search_tools', 'call_tool'}
    assert 'callable' not in tools['call_tool']  # Only target resolution may execute a tool.

    for approved in (False, True):
        tool_call = {
            'id': 'call',
            'function': {
                'name': 'call_tool',
                'arguments': json.dumps({'tool_name': 'search_knowledge_bases', 'params': '{}'}),
            },
        }
        caller = AsyncMock(return_value=approved)

        async def updated(function, extra_params):
            return function

        monkeypatch.setattr(middleware, 'get_updated_tool_function', updated)
        result = await middleware.execute_tool_call(
            {'messages': []}, {**metadata, 'params': {'tool_approval_mode': 'ask'}}, caller, tool_call
        )
        assert result[1] == ('done' if approved else 'Error: tool call was not approved.')
        assert caller.await_args.args[0]['data']['tool_call']['name'] == 'search_knowledge_bases'
        assert tool_call['provider_function']['name'] == 'call_tool'
    tools['search_knowledge_bases']['callable'].assert_awaited_once()


@pytest.mark.parametrize('params', ['[]', 'null', '{invalid-json'])
def test_dispatch_rejects_non_object_or_invalid_arguments(params):
    tools = tools_fixture()
    tools['call_tool'] = {'tool_id': 'builtin:call_tool'}
    call = {
        'function': {
            'name': 'call_tool',
            'arguments': json.dumps({'tool_name': 'search_knowledge_bases', 'params': params}),
        }
    }
    assert tool_search.resolve_tool_call(call, tools).startswith('Error:')
    tools['search_knowledge_bases']['callable'].assert_not_called()


@pytest.mark.asyncio
async def test_legacy_hidden_call_still_executes_through_normal_dispatch():
    tools = tools_fixture()
    call = {'function': {'name': 'view_file', 'arguments': '{}'}}
    result = await middleware.execute_tool_call({'messages': []}, {'tools': tools}, None, call)
    assert result[1] == 'done'
    tools['view_file']['callable'].assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_tool_search_keeps_canonical_schemas_without_dispatchers(monkeypatch):
    tools = tools_fixture()
    monkeypatch.setattr(tool_search, 'get_tool_search_config', AsyncMock(return_value=None))
    body = {'messages': [{'role': 'user', 'content': 'Question'}]}
    assert await tool_search.apply_tool_search(body, {'tools': tools}, tools) == set()
    assert set(tools) - {'view_file'} == RESIDENT_KNOWLEDGE | {'search_knowledge_bases'}
    assert 'search_tools' not in tools and 'call_tool' not in tools
