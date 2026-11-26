"""Connection-scoped Responses tool choices; no application or database imports."""

import ast
import copy
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'open_webui/routers/openai.py'


def apply(payload, config=None):
    tree = ast.parse(SOURCE.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'apply_declared_tools_only')
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['apply_declared_tools_only'](payload, config or {})


def test_enabled_connection_allows_every_declared_function_but_not_proxy_additions():
    names = ['run_command', 'read_file', 'search_web', 'search_tools', 'call_tool', 'image_studio']
    tools = [{'type': 'function', 'name': name, 'description': name, 'parameters': {'type': 'object'}} for name in names]
    original = copy.deepcopy(tools)
    payload = apply({'tools': tools}, {'declared_tools_only': True})
    assert payload['tools'] == original
    assert payload['tool_choice'] == {
        'type': 'allowed_tools', 'mode': 'auto',
        'tools': [{'type': 'function', 'name': name} for name in names],
    }
    payload['tools'].append({'type': 'image_generation'})
    assert {'type': 'image_generation'} not in payload['tool_choice']['tools']


def test_disabled_connections_remain_unchanged():
    for config in ({}, {'declared_tools_only': False}):
        payload = {'tools': [{'type': 'function', 'name': 'call_tool'}]}
        original = copy.deepcopy(payload)
        assert apply(payload, config) == original


def test_explicit_choices_are_preserved():
    for choice in ('none', {'type': 'function', 'name': 'call_tool'}, {'type': 'allowed_tools', 'mode': 'auto', 'tools': []}):
        payload = {'tools': [{'type': 'function', 'name': 'call_tool'}], 'tool_choice': choice}
        assert apply(copy.deepcopy(payload), {'declared_tools_only': True}) == payload


def test_required_and_client_declared_hosted_tools_are_preserved():
    tools = [{'type': 'function', 'name': 'call_tool'}, {'type': 'image_generation'}, {'type': 'mcp', 'server_label': 'configured-server'}]
    payload = apply({'tools': tools, 'tool_choice': 'required'}, {'declared_tools_only': True})
    assert payload['tools'] == tools
    assert payload['tool_choice'] == {'type': 'allowed_tools', 'mode': 'required', 'tools': tools}


def test_no_declared_tools_prevents_proxy_tools_from_being_selected():
    for tools in (None, []):
        assert apply({'tools': tools}, {'declared_tools_only': True})['tool_choice'] == 'none'


def test_both_responses_entrypoints_apply_the_connection_policy():
    tree = ast.parse(SOURCE.read_text())
    for name in ('generate_chat_completion', 'responses'):
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'apply_declared_tools_only']
        assert len(calls) == 1
        assert [ast.unparse(arg) for arg in calls[0].args] == ['payload', 'api_config']
