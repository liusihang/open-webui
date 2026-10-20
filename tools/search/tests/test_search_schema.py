"""Exercise Open WebUI's real tool-schema builder without app/DB startup."""

import ast
import copy
import inspect
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_type_hints
from unittest.mock import AsyncMock, patch

from langchain_core.utils.function_calling import convert_to_openai_function
from pydantic import BaseModel, Field, create_model

from tools.search import gpt_web_search as src

ROOT = Path(__file__).resolve().parents[3]


def schema_helpers():
    source = ROOT / 'backend/open_webui/utils/tools.py'
    names = {
        'parse_description',
        'parse_docstring',
        'convert_function_to_pydantic_model',
        'clean_properties',
        'clean_openai_tool_schema',
        'get_functions_from_tool',
        'get_tool_specs',
    }
    nodes = [
        node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    scope = {
        '__name__': __name__,
        'Any': Any,
        'Callable': Any,
        'BaseModel': BaseModel,
        'Field': Field,
        'create_model': create_model,
        'get_type_hints': get_type_hints,
        'inspect': inspect,
        'copy': copy,
        're': re,
        'convert_pydantic_model_to_openai_function_spec': convert_to_openai_function,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
    return scope


def model_spec():
    return schema_helpers()['get_tool_specs'](src.Tools())[0]


def legacy_spec():
    # Same public function contract and documentation, but the full legacy
    # nested types. This isolates the parameter-definition size reduction.
    async def search_web(
        query: src.Text | None = None,
        count: int = 3,
        commands: src.Commands | None = None,
        input: str | None = None,
        settings: src.Settings | None = None,
    ) -> dict:
        pass

    search_web.__doc__ = src.Tools.search_web.__doc__
    helpers = schema_helpers()
    model = helpers['convert_function_to_pydantic_model'](search_web)
    return helpers['clean_openai_tool_schema'](convert_to_openai_function(model))


class SearchSchemaTests(unittest.TestCase):
    def test_same_function_and_top_level_parameters(self):
        specs = schema_helpers()['get_tool_specs'](src.Tools())
        self.assertEqual([spec['name'] for spec in specs], ['search_web'])
        self.assertEqual(
            list(specs[0]['parameters']['properties']), ['query', 'count', 'commands', 'input', 'settings']
        )

    def test_only_common_commands_are_advertised(self):
        commands = model_spec()['parameters']['properties']['commands']
        self.assertEqual(commands['type'], 'object')
        self.assertEqual(
            set(commands['properties']),
            {
                'search_query',
                'image_query',
                'open',
                'click',
                'find',
                'screenshot',
                'response_length',
            },
        )
        self.assertIs(commands['additionalProperties'], False)

    def test_only_search_scope_settings_are_advertised(self):
        settings = model_spec()['parameters']['properties']['settings']
        self.assertEqual(set(settings['properties']), {'search_context_size', 'filters'})
        self.assertIs(settings['additionalProperties'], False)
        self.assertEqual(set(settings['properties']['filters']['properties']), {'allowed_domains', 'blocked_domains'})

    def test_search_and_media_parameter_types_and_limits_are_preserved(self):
        commands = model_spec()['parameters']['properties']['commands']['properties']
        for key, limit in [('search_query', 4), ('image_query', 2), ('screenshot', 2)]:
            self.assertEqual(commands[key]['type'], 'array')
            self.assertEqual(commands[key]['maxItems'], limit)
        query = commands['search_query']['items']
        self.assertEqual(query['required'], ['q'])
        self.assertEqual(query['properties']['q']['maxLength'], 2000)
        self.assertEqual(query['properties']['recency']['type'], 'integer')
        self.assertEqual(query['properties']['domains']['maxItems'], 10)
        self.assertIn('id', commands['click']['items']['required'])
        self.assertEqual(commands['click']['items']['properties']['id']['type'], 'integer')
        self.assertEqual(commands['screenshot']['items']['properties']['pageno']['minimum'], 0)
        self.assertEqual(commands['response_length']['enum'], ['short', 'medium', 'long'])

    def test_model_schema_is_at_least_a_quarter_smaller(self):
        current = len(json.dumps(model_spec(), ensure_ascii=False))
        previous = len(json.dumps(legacy_spec(), ensure_ascii=False))
        self.assertLessEqual(current, previous * 0.75, (current, previous))

    def test_schema_does_not_depend_on_instance_valves(self):
        first = src.Tools()
        second = src.Tools()
        second.valves.API_KEY = 'fixture-private-never-exposed'
        second.valves.MODEL = 'fixture-other-backend'
        helpers = schema_helpers()
        self.assertEqual(helpers['get_tool_specs'](first), helpers['get_tool_specs'](second))


class LegacyDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatcher_preserves_old_nested_arguments_with_smaller_schema(self):
        source = ROOT / 'backend/open_webui/utils/middleware.py'
        node = next(
            node
            for node in ast.parse(source.read_text()).body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == 'execute_tool_call'
        )
        scope = {
            'ast': ast,
            'JSONCodec': SimpleNamespace(loads=json.loads, dumps=json.dumps),
            'resolve_tool_call': lambda *_: None,
            'is_saved_chat_id': lambda _: True,
            'get_updated_tool_function': AsyncMock(side_effect=lambda function, extra_params: function),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), scope)
        tool = src.Tools()
        tool.valves.API_KEY = 'fixture-not-real'
        arguments = {
            'commands': {'finance': [{'ticker': 'MSFT', 'type': 'equity'}]},
            'settings': {'user_location': {'country': 'CN'}, 'image_settings': {'caption': True}},
            'input': 'Legacy context',
        }
        metadata = {
            'chat_id': 'saved-chat',
            'tools': {
                'search_web': {'spec': model_spec(), 'callable': tool.search_web, 'type': 'local'},
            },
        }
        with (
            patch.object(tool, '_request', wraps=tool._request) as request_builder,
            patch.object(
                src.aiohttp, 'ClientSession', side_effect=src.aiohttp.ClientError('offline fixture')
            ) as session,
        ):
            params, result, *_ = await scope['execute_tool_call'](
                {},
                metadata,
                None,
                {'function': {'name': 'search_web', 'arguments': json.dumps(arguments)}},
            )
        self.assertEqual(params, arguments)
        self.assertEqual(request_builder.call_args.args[1], arguments['commands'])
        self.assertEqual(request_builder.call_args.args[3], arguments['settings'])
        session.assert_called_once()
        self.assertEqual(result['error']['code'], 'transport_error')


if __name__ == '__main__':
    unittest.main(verbosity=2)
