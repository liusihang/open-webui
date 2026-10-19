"""Exercise actual OpenAPI discovery conversion without startup, DB or network."""

import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'backend/open_webui/utils/tools.py'


def convert(spec):
    tree = ast.parse(SOURCE.read_text())
    nodes = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name in {'resolve_schema', 'convert_openapi_to_tool_payload'}
        or isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'OPENAPI_HTTP_METHODS' for t in n.targets)
    ]
    scope = {'copy': copy}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['convert_openapi_to_tool_payload'](spec)


def fixture():
    return {
        'paths': {
            '/fixture/{context}': {
                'parameters': [{'name': 'context', 'in': 'path', 'required': True}],
                'post': {
                    'operationId': 'fixture_tool',
                    'requestBody': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Body'}}}},
                },
            }
        },
        'components': {
            'schemas': {
                'Body': {
                    'type': 'object',
                    'properties': {name: {'type': 'string'} for name in ('command', 'context', 'mode')},
                    'required': ['command', 'context', 'mode'],
                }
            }
        },
    }


def wire_schema():
    """Continue through actual cleaning and Responses serialization, as discovery does."""
    scope = {'copy': copy}
    tree = ast.parse(SOURCE.read_text())
    nodes = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in {'clean_properties', 'clean_openai_tool_schema'}
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
    router = SOURCE.parents[1] / 'routers/openai.py'
    node = next(
        n
        for n in ast.parse(router.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == 'convert_to_responses_payload'
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(router), 'exec'), scope)
    tools = [{'type': 'function', 'function': scope['clean_openai_tool_schema'](spec)} for spec in convert(fixture())]
    return scope['convert_to_responses_payload']({'messages': [], 'tools': tools})['tools']


class ToolSchemaStabilityTests(unittest.TestCase):
    def test_required_merge_keeps_first_seen_order_and_membership(self):
        original = fixture()
        frozen = copy.deepcopy(original)
        result = convert(original)
        required = result[0]['parameters']['required']
        self.assertEqual(required, ['context', 'command', 'mode'])
        self.assertEqual(set(required), {'command', 'context', 'mode'})
        self.assertEqual(original, frozen)

    def test_same_openapi_has_identical_wire_schema_across_hash_seeds(self):
        results = [
            subprocess.check_output(
                [sys.executable, str(Path(__file__).resolve()), '--emit'],
                env={**os.environ, 'PYTHONHASHSEED': str(seed)},
                text=True,
            )
            for seed in (1, 2, 3, 4)
        ]
        self.assertEqual(len(set(results)), 1, 'Identical OpenAPI must produce identical provider schema')

    def test_schema_property_and_tool_order_are_not_sorted(self):
        spec = fixture()
        spec['paths']['/second'] = {'get': {'operationId': 'a_tool'}}
        result = convert(spec)
        self.assertEqual([tool['name'] for tool in result], ['fixture_tool', 'a_tool'])
        self.assertEqual(list(result[0]['parameters']['properties']), ['context', 'command', 'mode'])


if __name__ == '__main__':
    if '--emit' in sys.argv:
        print(json.dumps(wire_schema(), ensure_ascii=False, separators=(',', ':')))
    else:
        unittest.main()
