"""Offline production-helper/router/compaction seams; no app, DB or provider startup."""

import ast
import collections.abc
import copy
import importlib.util
import json
import logging
import re
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, patch
from urllib.parse import quote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
UTILS = ROOT / 'backend/open_webui/utils'
ROUTER = ROOT / 'backend/open_webui/routers/openai.py'
SPEC = importlib.util.spec_from_file_location(
    'native_fixture', ROOT / 'tests/migration/test_responses_native_history.py'
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def cache_scope():
    scope = {}
    exec(compile((UTILS / 'chat_id.py').read_text(), str(UTILS / 'chat_id.py'), 'exec'), scope)
    tree = ast.parse((UTILS / 'prompt_cache.py').read_text())
    tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and n.module == 'open_webui.utils.chat_id')]
    exec(compile(tree, str(UTILS / 'prompt_cache.py'), 'exec'), scope)
    return scope


CACHE = cache_scope()['apply_default_prompt_cache_key']


class PromptCacheTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'model': 'gpt-6.1-sol', 'input': [{'role': 'user', 'content': 'first'}]}
        self.metadata = {'chat_id': 'saved-chat', 'message_id': 'a1', 'session_id': 'socket1'}
        self.kwargs = dict(
            api_config={'api_type': 'responses'},
            user_id='verified-user',
            connection_index=2,
            base_url='https://example.invalid/v1',
        )

    def apply(self, payload=None, metadata=None, **kwargs):
        return CACHE(
            self.payload if payload is None else payload,
            self.metadata if metadata is None else metadata,
            **(self.kwargs | kwargs),
        )

    def test_deterministic_versioned_ascii_key_and_no_caller_mutation(self):
        frozen = copy.deepcopy(self.payload)
        result = self.apply()
        key = result['prompt_cache_key']
        self.assertEqual(result, self.apply())
        self.assertRegex(key, r'^owui:pc:v1:[a-f0-9]+$')
        self.assertEqual(len(key), 64)
        self.assertTrue(key.isascii())
        for private in ['verified-user', 'saved-chat', 'example.invalid', 'first']:
            self.assertNotIn(private, key)
        self.assertEqual(self.payload, frozen)

    def test_round_tool_summary_memory_rag_and_socket_changes_do_not_change_key(self):
        first = self.apply()['prompt_cache_key']
        payload = self.payload | {
            'instructions': 'new summary / memory / RAG',
            'input': self.payload['input'] + [{'type': 'function_call_output', 'call_id': 'c', 'output': 'new'}],
            'tools': [{'type': 'function', 'name': 'new_tool'}],
            'previous_response_id': 'resp-new',
        }
        metadata = self.metadata | {
            'message_id': 'a2',
            'user_message_id': 'u2',
            'session_id': 'socket2',
            'user_id': 'untrusted-user',
        }
        self.assertEqual(first, self.apply(payload, metadata)['prompt_cache_key'])

    def test_user_chat_model_and_connection_are_separate_scopes(self):
        keys = {self.apply()['prompt_cache_key']}
        for payload, metadata, kwargs in [
            (self.payload, self.metadata, {'user_id': 'other-user'}),
            (self.payload, self.metadata | {'chat_id': 'other-chat'}, {}),
            (self.payload | {'model': 'gpt-6-astra'}, self.metadata, {}),
            (self.payload, self.metadata, {'connection_index': 3}),
            (self.payload, self.metadata, {'base_url': 'https://other.invalid/v1'}),
            (self.payload, self.metadata, {'base_url': 'https://example.invalid/other'}),
        ]:
            key = self.apply(payload, metadata, **kwargs)['prompt_cache_key']
            self.assertNotIn(key, keys)
            keys.add(key)

    def test_url_case_trailing_slash_and_fragment_are_normalized(self):
        self.assertEqual(
            self.apply()['prompt_cache_key'],
            self.apply(base_url='HTTPS://EXAMPLE.INVALID/v1/#ignored')['prompt_cache_key'],
        )

    def test_explicit_key_null_and_empty_always_win_even_on_unsupported_paths(self):
        for value in ['caller-choice', '', None, False]:
            payload = self.payload | {'prompt_cache_key': value}
            with self.subTest(value=value):
                self.assertIs(self.apply(payload), payload)
                self.assertIs(self.apply(payload, {}, api_config={'api_type': 'chat'}, user_id=None), payload)

    def test_no_fallback_for_tasks_non_saved_chats_or_missing_verified_identity(self):
        for metadata in [
            None,
            {},
            {'chat_id': ''},
            {'chat_id': 'temporary:abc'},
            {'chat_id': 'local:abc'},
            {'chat_id': 'channel:abc'},
            {'chat_id': 42},
            {'chat_id': '  '},
            self.metadata | {'task': 'title_generation'},
            self.metadata | {'internal': True},
            self.metadata | {'direct': True},
        ]:
            with self.subTest(metadata=metadata):
                self.assertIs(CACHE(self.payload, metadata, **self.kwargs), self.payload)
        for user_id in [None, '', '  ']:
            self.assertIs(self.apply(user_id=user_id), self.payload)

    def test_only_actual_gpt_responses_and_supported_connection_schema(self):
        for model in ['claude-4', 'o3', 'alias', 'prefix/gpt-6-astra', 'openai.gpt-6-astra', 'gpt-3.5-turbo', '', None]:
            payload = self.payload | {'model': model}
            self.assertIs(self.apply(payload), payload)
        for api_config in [
            {},
            {'api_type': 'chat'},
            {'api_type': 'responses', 'azure': True},
            {'api_type': 'responses', 'provider': 'azure'},
        ]:
            self.assertIs(self.apply(api_config=api_config), self.payload)
        for model in ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-luna', 'gpt-5.4', 'gpt-4.1', 'gpt-4o']:
            self.assertIn('prompt_cache_key', self.apply(self.payload | {'model': model}))
        self.assertIn(
            'prompt_cache_key',
            self.apply(
                api_config={'api_type': 'responses', 'provider': 'azure'}, base_url='https://example.invalid/openai/v1'
            ),
        )
        self.assertIs(self.apply(connection_index=-1), self.payload)
        self.assertIs(self.apply(base_url=''), self.payload)


class RouterPromptCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = {'api_type': 'responses', 'prefix_id': 'conn'}
        self.url = 'https://example.invalid/v1'
        self.model_params = {}
        self.provider = {'urlIdx': 2}
        self.user = SimpleNamespace(id='verified-user', name='fixture', email='fixture@example.invalid', role='user')
        self.request = SimpleNamespace(
            state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={}))
        )
        self.compaction = AsyncMock(side_effect=lambda request, user, payload, metadata, **kwargs: payload)
        self.scope = FIXTURE.scope()
        self.scope.update(
            re=re,
            quote=quote,
            collections=collections,
            log=logging.getLogger(__name__),
            Request=object,
            Callable=Callable,
            Optional=Optional,
            BYPASS_MODEL_ACCESS_CONTROL=False,
            JSONCodec=SimpleNamespace(dumps=json.dumps, loads=json.loads, JSONDecodeError=json.JSONDecodeError),
            Config=SimpleNamespace(get=AsyncMock(return_value=True)),
            Models=SimpleNamespace(
                get_model_by_id=AsyncMock(
                    side_effect=lambda mid: SimpleNamespace(
                        base_model_id='conn.gpt-6.1-sol',
                        params=SimpleNamespace(model_dump=lambda: copy.deepcopy(self.model_params)),
                    )
                )
            ),
            check_model_access=AsyncMock(),
            get_all_models=AsyncMock(),
            get_openai_connection=AsyncMock(side_effect=lambda idx: (self.url, 'synthetic-key', self.config)),
            get_headers_and_cookies=AsyncMock(return_value=({'Custom-Header': 'unchanged'}, None)),
            apply_system_prompt_to_body=AsyncMock(side_effect=lambda system, payload, *args: payload),
            apply_default_prompt_cache_key=CACHE,
        )
        for name in (
            'is_openai_new_model',
            'openai_reasoning_model_handler',
            '_sanitize_model_for_url',
            'get_azure_allowed_params',
            'convert_to_azure_payload',
        ):
            FIXTURE.load_function(ROUTER, name, self.scope)
        FIXTURE.load_function(UTILS / 'model_ids.py', 'strip_provider_model_prefix', self.scope)
        FIXTURE.load_function(UTILS / 'memory.py', 'apply_dynamic_memory_context', self.scope)
        FIXTURE.load_function(UTILS / 'misc.py', 'deep_update', self.scope)
        for name in ('remove_open_webui_params', 'apply_model_params_to_body', 'apply_model_params_to_body_openai'):
            FIXTURE.load_function(UTILS / 'payload.py', name, self.scope)
        tree = ast.parse(ROUTER.read_text())
        route = next(
            n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'generate_chat_completion'
        )
        # Execute the complete actual route through final serialization; stop before network I/O.
        end = next(i for i, n in enumerate(route.body) if ast.unparse(n) == 'payload = JSONCodec.dumps(payload)')
        route.body = route.body[: end + 1] + [ast.Return(value=ast.Name(id='payload', ctx=ast.Load()))]
        route.decorator_list = []
        route.args.defaults = []
        exec(
            compile(ast.fix_missing_locations(ast.Module(body=[route], type_ignores=[])), str(ROUTER), 'exec'),
            self.scope,
        )
        self.modules = patch.dict(
            'sys.modules',
            {
                'open_webui.utils.native_compaction': SimpleNamespace(
                    compact_responses_payload=self.compaction, native_compaction_headers=lambda headers, *args: headers
                )
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)

    async def send(self, body=None, **metadata):
        model = 'conn.gpt-6.1-sol'
        self.request.app.state.OPENAI_MODELS = {model: self.provider}
        body = {
            'model': 'display-alias',
            'messages': [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'first'}],
            'metadata': {'chat_id': 'saved-chat', 'user_id': 'spoofed', **metadata},
            'stream': False,
            **(body or {}),
        }
        return json.loads(await self.scope['generate_chat_completion'](self.request, body, self.user))

    async def test_actual_hook_uses_verified_user_final_model_and_runs_before_compaction(self):
        result = await self.send(
            {
                'user': {'id': 'spoofed'},
                'tools': [
                    {'type': 'function', 'function': {'name': 'z', 'parameters': {}}},
                    {'type': 'function', 'function': {'name': 'a', 'parameters': {}}},
                ],
                'safety_identifier': 'explicit-safety',
            }
        )
        expected = CACHE(
            {'model': 'gpt-6.1-sol'},
            {'chat_id': 'saved-chat'},
            api_config=self.config,
            user_id=self.user.id,
            connection_index=2,
            base_url=self.url,
        )['prompt_cache_key']
        self.assertEqual(result['prompt_cache_key'], expected)
        self.assertEqual(result['model'], 'gpt-6.1-sol')
        self.assertIn('input', result)
        self.assertNotIn('messages', result)
        self.assertNotIn('metadata', result)
        self.assertNotIn('prompt_cache_key', str(result['input']))
        self.assertEqual([tool['name'] for tool in result['tools']], ['z', 'a'])
        self.assertEqual(result['user'], {'id': 'spoofed'})  # Existing explicit user semantics are unchanged.
        self.assertEqual(result['safety_identifier'], 'explicit-safety')
        compact_payload = self.compaction.await_args.args[2]
        self.assertEqual(compact_payload['prompt_cache_key'], expected)
        self.assertIn('input', compact_payload)
        self.assertEqual(self.compaction.await_args.kwargs['headers'], {'Custom-Header': 'unchanged'})

    async def test_actual_router_tool_followup_and_dynamic_instructions_keep_key(self):
        first = await self.send()
        follow = await self.send(
            {
                'messages': [
                    {'role': 'system', 'content': 'new summary memory RAG'},
                    {
                        'role': 'assistant',
                        'tool_calls': [
                            {'id': 'c', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}
                        ],
                    },
                    {'role': 'tool', 'tool_call_id': 'c', 'content': 'result'},
                ]
            },
            message_id='new-message',
            session_id='new-socket',
        )
        self.assertEqual(first['prompt_cache_key'], follow['prompt_cache_key'])
        self.assertEqual(follow['input'][-1]['type'], 'function_call_output')

    async def test_actual_router_preserves_explicit_and_model_custom_parameter_keys(self):
        self.model_params = {'custom_params': {'prompt_cache_key': 'model-choice', 'prompt_cache_retention': '24h'}}
        self.assertEqual((await self.send())['prompt_cache_key'], 'model-choice')
        for value in ['caller-choice', '', None]:
            result = await self.send({'prompt_cache_key': value})
            self.assertEqual(result['prompt_cache_key'], value)
            self.assertEqual(result['prompt_cache_retention'], '24h')

    async def test_actual_router_skips_chat_non_gpt_and_legacy_azure(self):
        self.config = {'api_type': 'chat', 'prefix_id': 'conn'}
        result = await self.send()
        self.assertNotIn('prompt_cache_key', result)
        self.assertIn('messages', result)
        self.compaction.assert_not_awaited()
        self.config = {'api_type': 'responses', 'prefix_id': 'conn', 'azure': True}
        self.assertNotIn('prompt_cache_key', await self.send())
        self.config = {
            'api_type': 'responses',
            'prefix_id': 'other',
        }  # Provider ID remains conn.gpt-*; no alias guessing.
        self.assertNotIn('prompt_cache_key', await self.send())

    async def test_actual_router_skips_tasks_unsaved_direct_internal_and_pipeline(self):
        for metadata in [{'task': 'title_generation'}, {'chat_id': None}, {'chat_id': 'temporary:fixture'}]:
            self.assertNotIn('prompt_cache_key', await self.send(**metadata))
        for flag in ['direct', 'internal']:
            setattr(self.request.state, flag, True)
            self.assertNotIn('prompt_cache_key', await self.send())
            setattr(self.request.state, flag, False)
        self.provider['pipeline'] = True
        self.assertNotIn('prompt_cache_key', await self.send())

    def test_responses_passthrough_route_does_not_create_chat_key(self):
        tree = ast.parse(ROUTER.read_text())
        route = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'responses')
        self.assertNotIn('apply_default_prompt_cache_key', ast.unparse(route))


class CompactionPromptCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = cache_scope()
        self.scope.update(copy=copy, logging=logging, time=time, urlsplit=urlsplit, urlunsplit=urlunsplit)
        path = UTILS / 'native_compaction.py'
        tree = ast.parse(path.read_text())
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        exec(compile(tree, str(path), 'exec'), self.scope)
        self.output = [{'type': 'compaction', 'encrypted_content': 'opaque-fixture'}]
        self.scope['_read_v2_output'] = AsyncMock(return_value=self.output)  # No stream transport test.
        self.payload = CACHE(
            {
                'model': 'gpt-6.1-sol',
                'input': [
                    {'role': 'user', 'content': 'prior'},
                    {'role': 'assistant', 'content': 'reply'},
                    {'role': 'user', 'content': 'now'},
                ],
                'tools': [{'type': 'function', 'name': 'z'}, {'type': 'function', 'name': 'a'}],
                'instructions': 'unchanged',
                'tool_choice': 'none',
            },
            {'chat_id': 'saved-chat'},
            api_config={'api_type': 'responses'},
            user_id='verified-user',
            connection_index=2,
            base_url='https://example.invalid/v1',
        )
        self.response = SimpleNamespace(
            status=200, release=lambda: None, json=AsyncMock(return_value={'output': self.output})
        )
        self.session = SimpleNamespace(request=AsyncMock(return_value=self.response))
        self.modules = patch.dict(
            'sys.modules',
            {
                'open_webui.env': SimpleNamespace(AIOHTTP_CLIENT_SESSION_SSL=True),
                'open_webui.utils.json_codec': SimpleNamespace(
                    JSONCodec=SimpleNamespace(dumps=json.dumps, loads=json.loads)
                ),
                'open_webui.utils.session_pool': SimpleNamespace(
                    get_client_timeout=lambda **kwargs: 60, get_session=AsyncMock(return_value=self.session)
                ),
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)

    async def test_actual_v2_request_carries_same_key_without_mutating_payload_or_headers(self):
        for value in [self.payload['prompt_cache_key'], 'explicit-key', '', None]:
            payload = self.payload | {'prompt_cache_key': value}
            frozen = copy.deepcopy(payload)
            headers = {'Custom-Header': 'unchanged'}
            await self.scope['_compact_upstream'](
                payload, 'https://example.invalid/v1/responses', headers, None, protocol='codex_v2'
            )
            body = json.loads(self.session.request.await_args.kwargs['data'])
            self.assertEqual(body['prompt_cache_key'], value)
            self.assertEqual(body['input'][-1], {'type': 'compaction_trigger'})
            self.assertEqual(body['tools'], payload['tools'])
            self.assertNotIn('tool_choice', body)
            self.assertEqual(payload, frozen)
            self.assertEqual(headers, {'Custom-Header': 'unchanged'})

    async def test_actual_legacy_compact_schema_does_not_gain_cache_key(self):
        await self.scope['_compact_upstream'](self.payload, 'https://example.invalid/v1/responses', {}, None)
        call = self.session.request.await_args.kwargs
        self.assertEqual(call['url'], 'https://example.invalid/v1/responses/compact')
        self.assertEqual(set(json.loads(call['data'])), {'model', 'input', 'instructions'})

    async def test_actual_checkpoint_generation_and_reopen_keep_the_key(self):
        messages = {
            'u': {'id': 'u', 'role': 'user', 'meta': {}},
            'a': {'id': 'a', 'parentId': 'u', 'role': 'assistant', 'meta': {}},
        }

        async def save(chat_id, message_id, data, **kwargs):
            messages[message_id].update(copy.deepcopy(data))
            return SimpleNamespace(id=chat_id)

        self.scope.update(
            Chats=SimpleNamespace(
                get_chat_by_id=AsyncMock(return_value=SimpleNamespace(user_id='verified-user')),
                get_messages_map_by_chat_id=AsyncMock(side_effect=lambda cid: copy.deepcopy(messages)),
                upsert_message_to_chat_by_id_and_message_id=AsyncMock(side_effect=save),
            ),
            get_message_list=lambda mapping, current: [mapping['u']],
            _emit=AsyncMock(),
        )
        context = SimpleNamespace(
            _load_config=AsyncMock(return_value={'enable': True, 'token_threshold': 1, 'token_cap': 80000}),
            _estimate_tokens=lambda value: 100,
            _resolve_token_threshold=lambda *args: 1,
        )
        metadata = {'chat_id': 'saved-chat', 'user_message_id': 'u', 'message_id': 'a'}
        kwargs = dict(
            api_config={
                'api_type': 'responses',
                'native_compaction_models': ['gpt-6.1-sol'],
                'native_compaction_protocol': 'codex_v2',
            },
            request_url='https://example.invalid/v1/responses',
            connection_index=2,
            api_key='synthetic-key',
            headers={},
        )
        frozen = copy.deepcopy(self.payload)
        with patch.dict('sys.modules', {'open_webui.utils.context_compaction': context}):
            first = await self.scope['compact_responses_payload'](
                SimpleNamespace(), SimpleNamespace(id='verified-user'), self.payload, metadata, **kwargs
            )
            second = await self.scope['compact_responses_payload'](
                SimpleNamespace(), SimpleNamespace(id='verified-user'), self.payload, metadata, **kwargs
            )
        self.assertEqual(first['prompt_cache_key'], self.payload['prompt_cache_key'])
        self.assertEqual(second, first)
        self.assertEqual(self.session.request.await_count, 1)
        self.assertEqual(
            json.loads(self.session.request.await_args.kwargs['data'])['prompt_cache_key'], first['prompt_cache_key']
        )
        self.assertEqual(self.payload, frozen)
        self.assertNotIn('compaction_trigger', str(first))


if __name__ == '__main__':
    unittest.main()
