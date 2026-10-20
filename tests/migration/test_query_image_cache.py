"""Real tool specs and message/provider assembly; no app, database or provider calls."""

import copy
import importlib.util
import inspect
import json
import re
import sys
import types
import unittest
from functools import cache, partial, update_wrapper
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Literal, Optional, get_args, get_type_hints
from unittest.mock import AsyncMock, patch

from langchain_core.utils.function_calling import (
    convert_to_openai_function as convert_pydantic_model_to_openai_function_spec,
)
from pydantic import BaseModel, Field, create_model

ROOT = Path(__file__).resolve().parents[2]
UTILS = ROOT / 'backend/open_webui/utils'
BUILTIN = ROOT / 'backend/open_webui/tools/builtin.py'
SPEC = importlib.util.spec_from_file_location(
    'query_image_prompt_fixture', Path(__file__).with_name('test_system_prompt_order.py')
)
PROMPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMPT)
LOAD = PROMPT.FIXTURE.load_function
IMAGE_A = '11111111-1111-4111-8111-111111111111'
IMAGE_B = '22222222-2222-4222-8222-222222222222'


def tool_scope():
    scope = dict(
        __name__=__name__,
        Any=Any,
        Awaitable=Awaitable,
        Callable=Callable,
        Literal=Literal,
        Optional=Optional,
        Request=Any,
        BaseModel=BaseModel,
        Field=Field,
        create_model=create_model,
        VIEW_FILE_DEFAULT_MAX_CHARS=10_000,
        VIEW_FILE_MAX_CHARS=100_000,
        copy=copy,
        inspect=inspect,
        re=re,
        cache=cache,
        partial=partial,
        update_wrapper=update_wrapper,
        get_args=get_args,
        get_type_hints=get_type_hints,
        JSONCodec=SimpleNamespace(dumps=json.dumps, loads=json.loads),
        convert_pydantic_model_to_openai_function_spec=convert_pydantic_model_to_openai_function_spec,
        Config=SimpleNamespace(get_many=AsyncMock(return_value={}), get=AsyncMock(return_value={})),
        is_native_pdf_attachment=lambda *args: False,
    )
    helper_spec = importlib.util.spec_from_file_location(
        'query_cache_knowledge_helpers', BUILTIN.with_name('knowledge_tools.py')
    )
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    scope.update(knowledge_entries=helper.knowledge_entries, model_evidence_result=helper.model_evidence_result)
    for name in (
        'list_knowledge',
        'search_knowledge_files',
        'grep_knowledge_files',
        'query_knowledge_files',
        'view_file',
        'view_knowledge_file',
        'view_knowledge_evidence',
        'list_knowledge_bases',
        'search_knowledge_bases',
        'query_knowledge_bases',
        'list_chat_files',
        'grep_chat_files',
        'query_chat_files',
    ):
        LOAD(BUILTIN, name, scope)
    for name in (
        'parse_description',
        'parse_docstring',
        'convert_function_to_pydantic_model',
        'clean_properties',
        'clean_openai_tool_schema',
        'get_builtin_function_introspection',
        'build_builtin_tool_spec_json',
        'get_builtin_tool_spec',
        'get_async_tool_function_and_apply_extra_params',
        'get_attached_knowledge',
        'get_builtin_tools',
    ):
        LOAD(UTILS / 'tools.py', name, scope)
    return scope


class QueryImageCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = tool_scope()
        self.case = PROMPT.PromptOrderTests('test_full_actual_payload_and_all_provider_role_paths')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        LOAD(UTILS / 'misc.py', 'is_raster_image_content_type', self.case.s)
        self.case.s['get_builtin_tools'] = self.scope['get_builtin_tools']
        self.case.model['info']['meta']['builtinTools'] = {
            name: False
            for name in (
                'time',
                'user_input',
                'files',
                'chats',
                'subagents',
                'memory',
                'web_search',
                'image_generation',
                'code_interpreter',
                'notes',
                'channels',
                'skills',
                'tasks',
                'automations',
                'calendar',
                'notifications',
            )
        }
        self.case.model['info']['meta']['builtinTools']['knowledge'] = True
        self.case.model['info']['meta']['knowledge'] = [{'id': 'kb', 'type': 'collection'}]
        env = types.ModuleType('open_webui.env')
        env.ENABLE_KB_EXEC = False
        self.env_patch = patch.dict(sys.modules, {'open_webui.env': env})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    async def resolve(self, refs):
        return await self.scope['get_builtin_tools'](
            self.case.request,
            {'__user__': {'id': 'owner', 'role': 'admin'}, '__metadata__': {'rag_query_image_refs': refs}},
            model=self.case.model,
        )

    async def test_image_a_image_b_and_no_image_keep_every_tool_spec_identical(self):
        specs = []
        for refs in ([IMAGE_A], [IMAGE_B], []):
            tools = await self.resolve(refs)
            specs.append(json.dumps([tool['spec'] for tool in tools.values()], separators=(',', ':')))
        self.assertEqual(specs[0], specs[1])
        self.assertEqual(specs[1], specs[2])
        self.assertNotIn(IMAGE_A, specs[0])
        self.assertNotIn(IMAGE_B, specs[1])

    async def test_only_five_canonical_knowledge_interfaces_are_advertised(self):
        tools = await self.resolve([])
        visible = {name for name, tool in tools.items() if not tool.get('hidden')}
        self.assertEqual(
            visible,
            {
                'search_knowledge_bases',
                'search_knowledge_files',
                'grep_knowledge_files',
                'query_knowledge_files',
                'view_knowledge_file',
            },
        )
        for alias in (
            'list_knowledge',
            'list_knowledge_bases',
            'query_knowledge_bases',
            'view_file',
            'view_knowledge_evidence',
        ):
            self.assertTrue(tools[alias]['hidden'])
            self.assertTrue(callable(tools[alias]['callable']))
        for tool in tools.values():
            self.assertFalse(any(name.startswith('__') for name in tool['spec']['parameters']['properties']))

    async def test_selected_sources_do_not_change_visible_knowledge_definitions(self):
        snapshots = []
        for sources in ([], [{'type': 'collection', 'id': 'kb'}], [{'type': 'file', 'id': 'file'}]):
            self.case.model['info']['meta']['knowledge'] = sources
            tools = await self.resolve([])
            snapshots.append(json.dumps([tool['spec'] for tool in tools.values() if not tool.get('hidden')]))
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])

    async def test_file_only_tools_keep_chat_scope_and_hide_old_names(self):
        meta = self.case.model['info']['meta']
        meta['builtinTools'].update(knowledge=False, files=True)
        meta['capabilities'] = {'file_context': False, 'file_upload': True}
        meta['knowledge'] = [{'type': 'collection', 'id': 'not-available'}]
        tools = await self.scope['get_builtin_tools'](
            self.case.request,
            {'__user__': {'id': 'owner', 'role': 'admin'}, '__metadata__': {'files': [{'id': 'file', 'type': 'file'}]}},
            model=self.case.model,
        )
        self.assertEqual(
            {name for name, tool in tools.items() if not tool.get('hidden')},
            {'search_knowledge_files', 'grep_knowledge_files', 'query_knowledge_files', 'view_knowledge_file'},
        )
        for name in ('query_knowledge_files', 'view_knowledge_file', 'view_file'):
            self.assertFalse(tools[name]['callable'].__extra_params__['__knowledge_enabled__'])
        self.assertTrue(tools['query_chat_files']['hidden'])
        self.assertNotIn('search_knowledge_bases', tools)

    async def test_both_feature_categories_disabled_expose_no_knowledge_tools(self):
        self.case.model['info']['meta']['builtinTools']['knowledge'] = False
        self.assertEqual(await self.resolve([]), {})

    async def test_provider_payloads_advertise_only_canonical_knowledge_names(self):
        body, metadata = await self.case.assemble(
            tools=False, folder=False, voice=False, summary=False, memory=False, rag=False
        )
        expected = {
            'search_knowledge_bases',
            'search_knowledge_files',
            'grep_knowledge_files',
            'query_knowledge_files',
            'view_knowledge_file',
        }
        self.assertEqual({tool['function']['name'] for tool in body['tools']}, expected)
        self.assertTrue(metadata['tools']['view_file']['hidden'])
        for responses in (False, True):
            payload = self.case.serialize(body, responses=responses)
            self.assertEqual({tool.get('name') or tool['function']['name'] for tool in payload['tools']}, expected)

    async def test_attachment_hint_is_static_and_query_argument_remains_explicit(self):
        tools = await self.resolve([IMAGE_A])
        spec = tools['query_knowledge_files']['spec']
        param = spec['parameters']['properties']['query_image_refs']
        self.assertIn('attached_files', param['description'])
        self.assertIn('image-based', param['description'])
        self.assertIn('Never a URL, path or base64', param['description'])
        self.assertNotIn('Authorized uploaded query image file IDs for this turn', spec['description'])
        self.assertIsNone(
            inspect.signature(tools['query_knowledge_files']['callable']).parameters['query_image_refs'].default
        )

    async def test_chat_and_responses_reuse_attachment_tags_without_changing_static_prefix(self):
        old_history = [
            {'id': 'u0', 'role': 'user', 'content': 'Earlier question'},
            {'id': 'a0', 'role': 'assistant', 'content': 'Earlier answer'},
        ]
        for responses in (True, False):
            with self.subTest(responses=responses):
                self.case.s['uses_responses_api'].return_value = responses
                payloads = []
                for refs in ([IMAGE_A], [IMAGE_B], []):
                    history = old_history + [
                        {
                            'id': 'u1',
                            'role': 'user',
                            'content': [{'type': 'text', 'text': 'Search for a related figure'}],
                            'files': [
                                {
                                    'id': ref,
                                    'type': 'file',
                                    'content_type': 'image/png',
                                    'url': f'/api/v1/files/{ref}/content',
                                }
                                for ref in refs
                            ],
                        }
                    ]
                    body, _ = await self.case.assemble(history=history, tools=False, rag=False)
                    payload = self.case.serialize(body, responses=responses)
                    payloads.append(payload)
                    items = payload['input'] if responses else payload['messages']
                    last = next(item for item in reversed(items) if item.get('role') == 'user')
                    rendered = json.dumps(last)
                    for ref in refs:
                        self.assertEqual(rendered.count(ref), 2)  # id plus existing file URL, not a new hint
                        self.assertIn('<attached_files>', rendered)
                        self.assertNotIn(ref, json.dumps(payload['tools']))
                        self.assertNotIn(ref, json.dumps(items[:-1]))
                    self.assertEqual(self.case.db_messages, history)
                self.assertEqual(payloads[0]['tools'], payloads[1]['tools'])
                self.assertEqual(payloads[1]['tools'], payloads[2]['tools'])
                item_key = 'input' if responses else 'messages'
                self.assertEqual(payloads[0][item_key][:-1], payloads[1][item_key][:-1])
                self.assertEqual(payloads[1][item_key][:-1], payloads[2][item_key][:-1])

    async def test_later_turn_keeps_original_attachment_history_and_caller_data(self):
        history = [
            {
                'id': 'u0',
                'role': 'user',
                'content': 'Find related diagrams',
                'files': [
                    {
                        'id': IMAGE_A,
                        'type': 'file',
                        'content_type': 'image/png',
                        'url': f'/api/v1/files/{IMAGE_A}/content',
                    }
                ],
            },
            {'id': 'a0', 'role': 'assistant', 'content': 'First result'},
            {'id': 'u1', 'role': 'user', 'content': 'Now explain the result'},
        ]
        frozen = copy.deepcopy(history)
        first, _ = await self.case.assemble(history=history[:1], tools=False, rag=False)
        later, _ = await self.case.assemble(history=history, tools=False, rag=False)
        first_items = self.case.serialize(first)['input']
        later_items = self.case.serialize(later)['input']
        self.assertEqual(first_items, later_items[: len(first_items)])
        self.assertEqual(history, frozen)
        self.assertNotIn(IMAGE_A, json.dumps(later_items[-1]))

    async def test_bound_query_passes_only_explicit_image_references_to_authorized_search(self):
        selected = SimpleNamespace(knowledge_ids=('kb',), file_ids=())
        evidence = SimpleNamespace(search=AsyncMock(return_value={'items': [], 'warnings': []}))
        integration = types.ModuleType('open_webui.retrieval.integration')
        integration.request_scope = AsyncMock(return_value=selected)
        integration.services = AsyncMock(return_value=(None, evidence, None, {'status': 'ready'}))
        integration.split_external_scope = AsyncMock(return_value=(selected, []))
        integration.model_images = AsyncMock(return_value=[])
        self.scope.update(UserModel=SimpleNamespace, log=SimpleNamespace(warning=lambda *args: None))
        tools = await self.resolve([IMAGE_B])
        query = tools['query_knowledge_files']['callable']
        with patch.dict(sys.modules, {'open_webui.retrieval.integration': integration}):
            result = json.loads(await query('text-only lookup'))
            self.assertNotIn('error', result)
            self.assertEqual(evidence.search.await_args.kwargs['query_image_refs'], ())
            await query('', query_image_refs=[IMAGE_A])
            self.assertEqual(evidence.search.await_args.kwargs['query_image_refs'], [IMAGE_A])
            self.assertIs(evidence.search.await_args.kwargs['resolved_scope'], selected)
            self.assertEqual(evidence.search.await_args.kwargs['user'].id, 'owner')
            evidence.search.side_effect = PermissionError('Image is not authorized')
            result = json.loads(await query('', query_image_refs=['forbidden-image']))
            self.assertEqual(result['error_type'], 'PermissionError')
            self.assertNotIn('items', result)


if __name__ == '__main__':
    unittest.main()
