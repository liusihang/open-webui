"""Exercise real native-window/checkpoint logic with network and DB boundaries mocked.

No application startup, secrets, real model calls, or persistent database.
"""

import ast
import asyncio
import copy
import hashlib
import json
import logging
import re
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'backend/open_webui/utils/native_compaction.py'
TEXT = ROOT / 'backend/open_webui/utils/context_compaction.py'


def load(path, scope, name=None):
    nodes = ast.parse(path.read_text()).body
    if name:
        nodes = [
            node for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ]
    else:
        nodes = [node for node in nodes if not isinstance(node, (ast.Import, ast.ImportFrom))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)


def item(text, role='user'):
    return {'type': 'message', 'role': role, 'content': [{'type': 'input_text', 'text': text}]}


class NativeCompactionTests(unittest.TestCase):
    def setUp(self):
        self.map = {
            'u1': {'id': 'u1', 'role': 'user', 'content': 'retained original', 'meta': {'other': True}},
            'a1': {'id': 'a1', 'role': 'assistant', 'parentId': 'u1', 'content': ''},
        }
        self.config = {'enable': True, 'token_threshold': 10, 'token_cap': 80000}
        self.compacted = [
            item('retained original'),
            {'type': 'compaction', 'id': 'cmp1', 'encrypted_content': 'opaque'},
        ]
        self.scope = dict(
            copy=copy,
            hashlib=hashlib,
            json=json,
            logging=logging,
            time=time,
            urlsplit=urlsplit,
            urlunsplit=urlunsplit,
            is_saved_chat_id=lambda value: value == 'chat',
            get_message_list=lambda mapping, current: [mapping['u1']] if current == 'u1' else list(mapping.values()),
        )
        self.chats = SimpleNamespace(
            get_chat_by_id=AsyncMock(return_value=SimpleNamespace(user_id='owner')),
            get_messages_map_by_chat_id=AsyncMock(side_effect=lambda cid: copy.deepcopy(self.map)),
            upsert_message_to_chat_by_id_and_message_id=AsyncMock(side_effect=self.save),
        )
        self.scope['Chats'] = self.chats
        load(MODULE, self.scope)
        self.scope['_compact_upstream'] = AsyncMock(side_effect=lambda *args, **kwargs: copy.deepcopy(self.compacted))
        self.scope['_emit'] = AsyncMock()
        self.metadata = {'chat_id': 'chat', 'user_message_id': 'u1', 'message_id': 'a1', 'params': {}}
        self.payload = {
            'model': 'gpt-6.1-sol',
            'instructions': 'immutable system',
            'store': False,
            'input': [item('old context ' * 50), item('next')],
        }
        self.kwargs = dict(
            api_config={'api_type': 'responses', 'native_compaction_models': ['gpt-6.1-sol']},
            request_url='https://example.invalid/v1/responses',
            connection_index=2,
            api_key='synthetic-key',
            headers={'Authorization': 'synthetic'},
        )
        module = types.ModuleType('open_webui.utils.context_compaction')
        module._load_config = AsyncMock(side_effect=lambda: self.config)
        module._resolve_token_threshold = lambda threshold, cap, metadata: min(
            metadata.get('params', {}).get('compact_token_threshold', threshold), cap
        )
        estimator_scope = {'Any': Any, 'JSONCodec': SimpleNamespace(dumps=json.dumps)}
        load(TEXT, estimator_scope, '_estimate_tokens')
        module._estimate_tokens = estimator_scope['_estimate_tokens']
        self.patch = patch.dict('sys.modules', {'open_webui.utils.context_compaction': module})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def save(self, chat_id, message_id, data, **kwargs):
        self.map[message_id].update(copy.deepcopy(data))
        return SimpleNamespace(id=chat_id)

    def run_request(self, payload=None, metadata=None, **kwargs):
        return asyncio.run(
            self.scope['compact_responses_payload'](
                SimpleNamespace(),
                SimpleNamespace(id='owner'),
                copy.deepcopy(payload or self.payload),
                metadata if metadata is not None else self.metadata,
                **(self.kwargs | kwargs),
            )
        )

    def test_exact_allowlist_not_gpt_name_or_chat_connection(self):
        fn = self.scope['native_compaction_enabled']
        self.assertFalse(fn({'api_type': 'responses'}, 'gpt-6.1-sol'))
        self.assertFalse(fn({'api_type': 'chat', 'native_compaction_models': ['gpt-6.1-sol']}, 'gpt-6.1-sol'))
        self.assertFalse(fn({'api_type': 'responses', 'native_compaction_models': ['gpt-*']}, 'gpt-6.1-sol'))
        self.assertTrue(fn(self.kwargs['api_config'], 'gpt-6.1-sol'))

    def test_success_preserves_full_output_window_and_original_history(self):
        original = copy.deepcopy(self.payload)
        result = self.run_request()
        self.assertEqual(result['input'], self.compacted)
        self.assertEqual(self.payload, original)
        self.assertFalse(result['store'])
        state = self.map['a1']['meta']['native_compaction']
        self.assertEqual(state['output'], self.compacted)
        self.assertEqual(self.map['u1']['content'], 'retained original')
        self.assertNotIn('contextSummary', self.map['a1'])

    def test_reopen_replays_whole_window_then_only_new_items(self):
        self.run_request()
        self.config['token_threshold'] = 80000
        followup = copy.deepcopy(self.payload)
        followup['input'] += [item('saved assistant answer', 'assistant'), item('a new question')]
        result = self.run_request(followup)
        self.assertEqual(result['input'], self.compacted + followup['input'][2:])
        self.assertEqual(self.scope['_compact_upstream'].await_count, 1)

    def test_same_input_does_not_recompact_opaque_window(self):
        first = self.run_request()
        second = self.run_request()
        self.assertEqual(first, second)
        self.assertEqual(self.scope['_compact_upstream'].await_count, 1)

    def test_tool_call_and_result_replay_in_order(self):
        self.run_request()
        self.config['token_threshold'] = 80000
        batch = [
            {'type': 'function_call', 'id': 'fc1', 'call_id': 'c1', 'name': 'lookup', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'c1', 'output': 'answer'},
        ]
        self.assertEqual(
            self.run_request(self.payload | {'input': self.payload['input'] + batch})['input'], self.compacted + batch
        )

    def test_pending_approval_does_not_compact_incomplete_call(self):
        call = {'type': 'function_call', 'call_id': 'c1', 'name': 'guarded', 'arguments': '{}'}
        result = self.run_request(self.payload | {'input': self.payload['input'] + [call]})
        self.assertEqual(result['input'][-1], call)
        self.scope['_compact_upstream'].assert_not_awaited()

    def test_parallel_calls_wait_for_all_outputs(self):
        calls = [{'type': 'function_call', 'call_id': key} for key in ['a', 'b']]
        calls += [{'type': 'function_call_output', 'call_id': 'b', 'output': 'B'}]
        self.run_request(self.payload | {'input': self.payload['input'] + calls})
        self.scope['_compact_upstream'].assert_not_awaited()
        calls += [{'type': 'function_call_output', 'call_id': 'a', 'output': 'A'}]
        self.run_request(self.payload | {'input': self.payload['input'] + calls})
        self.scope['_compact_upstream'].assert_awaited_once()

    def test_approval_resume_current_assistant_checkpoint(self):
        self.run_request()
        self.config['token_threshold'] = 80000
        metadata = self.metadata | {'message_id': 'resumed-id', 'assistant_message_id': 'a1'}
        self.assertEqual(self.run_request(metadata=metadata)['input'], self.compacted)

    def test_edit_invalidates_prefix_and_uses_full_edited_history(self):
        self.run_request()
        self.config['token_threshold'] = 80000
        changed = copy.deepcopy(self.payload)
        changed['input'][0] = item('edited original')
        self.assertEqual(self.run_request(changed)['input'], changed['input'])

    def test_sibling_branch_cannot_reuse_checkpoint(self):
        self.run_request()
        self.config['token_threshold'] = 80000
        self.map['a1']['parentId'] = 'another-user'
        self.assertEqual(self.run_request()['input'], self.payload['input'])

    def test_model_route_key_or_system_change_never_replays_opaque_state(self):
        self.run_request()
        self.config['token_threshold'] = 80000
        cases = [
            ({'instructions': 'new system'}, {}),
            (
                {'model': 'gpt-6-astra'},
                {'api_config': {'api_type': 'responses', 'native_compaction_models': ['gpt-6.1-sol', 'gpt-6-astra']}},
            ),
            ({}, {'connection_index': 3}),
            ({}, {'api_key': 'changed-key'}),
            ({}, {'request_url': 'https://other.invalid/v1/responses'}),
        ]
        for change, kwargs in cases:
            with self.subTest(change=change, kwargs=kwargs):
                self.assertEqual(self.run_request(self.payload | change, **kwargs)['input'], self.payload['input'])

    def test_repeated_compaction_receives_prior_window_not_full_transcript(self):
        self.run_request()
        new = self.payload | {'input': self.payload['input'] + [item('more facts ' * 100)]}
        expected = self.compacted + new['input'][2:]
        self.run_request(new)
        self.assertEqual(self.scope['_compact_upstream'].await_args.args[0]['input'], expected)
        self.assertEqual(self.map['a1']['meta']['native_compaction']['prefix_count'], 3)

    def test_failure_retains_full_history_and_suppresses_same_branch_retry(self):
        self.scope['_compact_upstream'].side_effect = RuntimeError('HTTP400')
        self.assertEqual(self.run_request()['input'], self.payload['input'])
        self.assertEqual(self.map['a1']['meta']['native_compaction']['status'], 'failed')
        self.run_request(self.payload | {'input': self.payload['input'] + [item('later')]})
        self.assertEqual(self.scope['_compact_upstream'].await_count, 1)
        self.assertTrue(self.scope['_emit'].await_args.kwargs['error'])

    def test_failed_recompact_retains_last_successful_window(self):
        self.run_request()
        self.scope['_compact_upstream'].side_effect = RuntimeError('HTTP500')
        new = self.payload | {'input': self.payload['input'] + [item('more facts ' * 100)]}
        self.assertEqual(self.run_request(new)['input'], self.compacted + new['input'][2:])
        self.assertEqual(self.map['a1']['meta']['native_compaction']['output'], self.compacted)
        self.assertEqual(self.run_request(new)['input'], self.compacted + new['input'][2:])
        self.assertEqual(self.scope['_compact_upstream'].await_count, 2)

    def test_save_failure_does_not_use_unrecoverable_window(self):
        self.chats.upsert_message_to_chat_by_id_and_message_id.side_effect = None
        self.chats.upsert_message_to_chat_by_id_and_message_id.return_value = None
        self.assertEqual(self.run_request()['input'], self.payload['input'])
        self.assertTrue(self.scope['_emit'].await_args.kwargs['error'])

    def test_failed_checkpoint_save_does_not_smuggle_new_output_into_failure_record(self):
        count = 0

        async def fail_first_save(*args, **kwargs):
            nonlocal count
            count += 1
            return None if count == 1 else await self.save(*args, **kwargs)

        self.chats.upsert_message_to_chat_by_id_and_message_id.side_effect = fail_first_save
        self.assertEqual(self.run_request()['input'], self.payload['input'])
        state = self.map['a1']['meta']['native_compaction']
        self.assertEqual(state['status'], 'failed')
        self.assertNotIn('output', state)
        self.assertEqual(self.run_request()['input'], self.payload['input'])

    def test_disabled_background_stateful_unsaved_or_other_owner_skip(self):
        for metadata, payload in [
            (self.metadata | {'task': 'title'}, self.payload),
            ({}, self.payload),
            (self.metadata, self.payload | {'previous_response_id': 'resp1'}),
        ]:
            self.run_request(payload, metadata)
        self.config['enable'] = False
        self.run_request()
        self.config['enable'] = True
        self.chats.get_chat_by_id.return_value = SimpleNamespace(user_id='stranger')
        self.run_request()
        self.scope['_compact_upstream'].assert_not_awaited()

    def test_output_validation_requires_opaque_compaction_and_keeps_retained_items(self):
        valid = self.scope['_valid_output']
        self.assertTrue(valid(self.compacted))
        for output in (
            None,
            [],
            [{'type': 'message'}],
            [{'type': 'compaction', 'encrypted_content': ''}],
            [{'type': 'compaction', 'encrypted_content': 42}],
        ):
            self.assertFalse(valid(output))

    def test_upstream_protocol_schema_url_and_exact_output(self):
        load(MODULE, self.scope, '_compact_upstream')
        response = SimpleNamespace(
            status=200, json=AsyncMock(return_value={'output': self.compacted}), release=lambda: None
        )
        session = SimpleNamespace(request=AsyncMock(return_value=response))
        modules = {
            'open_webui.env': SimpleNamespace(AIOHTTP_CLIENT_SESSION_SSL=True),
            'open_webui.utils.json_codec': SimpleNamespace(
                JSONCodec=SimpleNamespace(dumps=json.dumps, loads=json.loads)
            ),
            'open_webui.utils.session_pool': SimpleNamespace(
                get_client_timeout=lambda **kwargs: 60, get_session=AsyncMock(return_value=session)
            ),
        }
        with patch.dict('sys.modules', modules):
            output = asyncio.run(
                self.scope['_compact_upstream'](
                    self.payload | {'tools': [{'name': 'synthetic'}], 'stream': True, 'max_output_tokens': 100},
                    'https://example.invalid/openai/v1/responses?api-version=test',
                    {'Authorization': 'synthetic'},
                    None,
                )
            )
        body = json.loads(session.request.await_args.kwargs['data'])
        self.assertEqual(set(body), {'model', 'input', 'instructions'})
        self.assertEqual(body['model'], self.payload['model'])
        self.assertEqual(
            session.request.await_args.kwargs['url'],
            'https://example.invalid/openai/v1/responses/compact?api-version=test',
        )
        self.assertEqual(output, self.compacted)


class TextCompactionRoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            'enable': True,
            'token_threshold': 10,
            'token_cap': 80000,
            'retention_percentage': 40,
            'prompt_template': '',
            'summary_excluded_models': ['known-gpt'],
        }
        self.scope = dict(
            logging=logging,
            re=re,
            Any=Any,
            JSONCodec=SimpleNamespace(dumps=json.dumps),
            JSONResponse=type('JSONResponse', (), {}),
            is_saved_chat_id=lambda cid: False,
            get_content_from_message=lambda m: m.get('content', ''),
        )
        load(TEXT, self.scope)
        self.scope['_load_config'] = AsyncMock(side_effect=lambda: self.config)
        self.scope['_generate_summary'] = AsyncMock(return_value='facts preserved')
        self.native = AsyncMock(return_value=False)
        self.patch = patch.dict(
            'sys.modules', {'open_webui.utils.native_compaction': SimpleNamespace(uses_native_compaction=self.native)}
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.messages = [{'role': 'system', 'content': 'system'}] + [
            {'id': str(idx), 'role': 'user' if idx % 2 == 0 else 'assistant', 'content': 'synthetic noise ' * 20}
            for idx in range(8)
        ]

    def run_text(self, model='non-gpt'):
        return asyncio.run(
            self.scope['compact_messages_for_request'](SimpleNamespace(), None, self.messages, {}, model, {})
        )

    def test_non_gpt_keeps_existing_summary_pipeline_and_system(self):
        result, summary, changed = self.run_text()
        self.assertTrue(changed)
        self.assertEqual(summary, 'facts preserved')
        self.assertEqual(result[0], self.messages[0])
        self.assertLess(len(result), len(self.messages))
        self.scope['_generate_summary'].assert_awaited_once()

    def test_known_gpt_pending_native_never_silently_summarizes(self):
        self.messages[3]['contextSummary'] = 'obsolete text checkpoint'
        result, summary, changed = self.run_text('known-gpt')
        self.assertFalse(changed)
        self.assertIsNone(summary)
        self.assertEqual(len(result), len(self.messages))
        self.assertFalse(any(m.get('contextSummary') for m in result))
        self.scope['_generate_summary'].assert_not_awaited()

    def test_explicit_native_and_text_modes_cannot_double_compact(self):
        self.native.return_value = True
        self.run_text('enabled-native')
        self.scope['_generate_summary'].assert_not_awaited()

    def test_disabled_global_keeps_existing_history(self):
        self.config['enable'] = False
        self.assertEqual(self.run_text(), (self.messages, None, False))
        self.scope['_generate_summary'].assert_not_awaited()


class CodexV2Tests(unittest.TestCase):
    # Exercise the real checkpoint path with the protocol's different window
    # contract; parent tests still exercise the original standalone protocol.
    setUp = NativeCompactionTests.setUp
    run_request = NativeCompactionTests.run_request
    save = NativeCompactionTests.save

    def v2(self, original=None):
        self.kwargs['api_config']['native_compaction_protocol'] = 'codex_v2'
        self.compacted = [{'type': 'compaction', 'id': 'cmp-v2', 'encrypted_content': 'opaque-v2'}]
        self.payload['input'] = original or [
            item('client system', 'system'),
            item('lookup the fact'),
            {'type': 'function_call', 'call_id': 'c1', 'name': 'lookup', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'c1', 'output': 'SECRET-FACT ' * 100},
            item('retained', 'assistant'),
            item('what was the fact?'),
        ]

    def test_v2_uses_complete_provider_window_then_current_input(self):
        self.v2()
        original = copy.deepcopy(self.payload['input'])
        result = self.run_request()
        self.assertEqual(result['input'], self.compacted + original[-1:])
        state = self.map['a1']['meta']['native_compaction']
        self.assertEqual(state['protocol'], 'codex_v2')
        self.assertEqual(state['version'], 2)
        self.assertEqual(state['prefix_count'], 5)
        self.assertEqual(state['output'], self.compacted)
        self.assertEqual(self.scope['_compact_upstream'].await_args.args[0]['input'], original[:-1])
        self.assertEqual(self.scope['_compact_upstream'].await_args.kwargs['protocol'], 'codex_v2')

    def test_v2_reopen_and_repeat_use_persisted_window_without_recompressing_same_prefix(self):
        self.v2()
        first = self.run_request()
        self.assertEqual(self.run_request(), first)
        self.assertEqual(self.scope['_compact_upstream'].await_count, 1)
        self.config['token_threshold'] = 80000
        follow = self.payload | {'input': self.payload['input'] + [item('answer', 'assistant'), item('again')]}
        saved = self.map['a1']['meta']['native_compaction']['output']
        self.assertEqual(self.run_request(follow)['input'], saved + follow['input'][5:])

    def test_v2_no_completed_prior_turn_does_not_pay_for_empty_compaction(self):
        self.v2([item('new user ' * 200)])
        self.assertEqual(self.run_request()['input'], self.payload['input'])
        self.scope['_compact_upstream'].assert_not_awaited()

    def test_cjk_history_above_threshold_reaches_native_dispatch(self):
        # Real reachable tool output: char/4 stays below 120K, o200k exceeds it.
        # The current user and opaque/history content must not be truncated.
        self.v2()
        self.payload['input'][3]['output'] = '病毒衣壳蛋白消毒耐受性' * 11000
        self.config.update(token_threshold=120000, token_cap=120000)
        self.assertLess(len(json.dumps(self.payload['input'], ensure_ascii=False)) // 4, 120000)
        self.assertGreater(self.scope['_estimate_native_tokens'](self.payload['input']), 120000)
        original = copy.deepcopy(self.payload)
        self.run_request()
        self.scope['_compact_upstream'].assert_awaited_once()
        self.assertEqual(self.payload, original)

    def test_tool_continuation_dispatch_checks_new_completed_prior_history(self):
        self.v2()
        self.config.update(token_threshold=120000, token_cap=120000)
        self.run_request()
        self.scope['_compact_upstream'].assert_not_awaited()
        # Same user turn, complete native function batch, dispatched again at
        # the Responses seam. The old completed turns can now be compacted.
        batch = [
            {'type': 'function_call', 'call_id': 'new', 'name': 'lookup', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'new', 'output': '病毒衣壳蛋白消毒耐受性' * 11000},
        ]
        result = self.run_request(self.payload | {'input': self.payload['input'] + batch})
        self.scope['_compact_upstream'].assert_awaited_once()
        self.assertEqual(result['input'][-3:], self.payload['input'][-1:] + batch)

    def test_usage_cached_tokens_are_not_subtracted(self):
        scope = {}
        load(TEXT, scope, '_usage_token_count')
        self.assertEqual(
            scope['_usage_token_count'](
                {'input_tokens': 120001, 'output_tokens': 12, 'input_tokens_details': {'cached_tokens': 119000}}
            ),
            120013,
        )

    def test_native_tokenizer_work_is_bounded_for_unbroken_text(self):
        fixtures = [
            '病毒衣壳蛋白消毒耐受性' * 11000,
            'x' * 180000,
            'AbCdEf0123456789+/' * 12000,
        ]
        for text in fixtures:
            with self.subTest(length=len(text)):
                parts = []

                def encode(chunk):
                    self.assertLessEqual(len(chunk), 4096)
                    parts.append(chunk)
                    return [0] * len(chunk)

                with patch('tiktoken.get_encoding', return_value=SimpleNamespace(encode_ordinary=encode)):
                    self.assertEqual(self.scope['_estimate_native_tokens'](text), len(text))
                self.assertEqual(''.join(parts), text, 'Every character is counted without truncating opaque content')

    def test_chunked_native_estimate_retains_character_floor(self):
        with patch('tiktoken.get_encoding', return_value=SimpleNamespace(encode_ordinary=lambda text: [0])):
            self.assertEqual(self.scope['_estimate_native_tokens']('x' * 180000), 45000)

    def test_switching_protocol_does_not_replay_standalone_checkpoint(self):
        self.run_request()
        self.config['token_threshold'] = 80000
        self.kwargs['api_config']['native_compaction_protocol'] = 'codex_v2'
        self.assertEqual(self.run_request()['input'], self.payload['input'])

    def test_unknown_protocol_never_enables_native(self):
        config = self.kwargs['api_config'] | {'native_compaction_protocol': 'other'}
        self.assertFalse(self.scope['native_compaction_enabled'](config, self.payload['model']))

    def test_v2_compacts_old_client_images_and_preserves_all_ordered_provider_output(self):
        image = {'type': 'input_image', 'image_url': 'data:image/png;base64,synthetic'}
        self.v2(
            [
                item('developer instruction', 'developer'),
                item('look') | {'content': [image]},
                item('old result ' * 100, 'assistant'),
                item('new question'),
            ]
        )
        extra = item('provider retained output', 'assistant')
        self.compacted.append(extra)
        self.assertEqual(self.run_request()['input'], self.compacted + self.payload['input'][-1:])
        self.assertEqual(self.scope['_compact_upstream'].await_args.args[0]['input'], self.payload['input'][:-1])

    def test_v2_failed_recompact_retains_accepted_persisted_window(self):
        self.v2()
        first = self.run_request()['input']
        saved = copy.deepcopy(self.map['a1']['meta']['native_compaction']['output'])
        self.scope['_compact_upstream'].side_effect = RuntimeError('HTTP 500')
        later = self.payload | {'input': self.payload['input'] + [item('reply', 'assistant'), item('next')]}
        result = self.run_request(later)
        self.assertEqual(result['input'], first + later['input'][6:])
        self.assertEqual(self.map['a1']['meta']['native_compaction']['output'], saved)
        self.assertEqual(self.run_request(later), result)
        self.assertEqual(self.scope['_compact_upstream'].await_count, 2)

    def test_v2_stream_request_and_done_collector(self):
        load(MODULE, self.scope, '_compact_upstream')
        compact = {'type': 'compaction', 'id': 'cmp', 'encrypted_content': 'full-opaque'}
        events = [
            {
                'type': 'response.output_item.added',
                'output_index': 0,
                'item': compact | {'encrypted_content': 'partial'},
            },
            {'type': 'response.output_item.done', 'output_index': 0, 'item': compact},
            {'type': 'response.completed', 'response': {'model': 'gpt-6.1-sol', 'status': 'completed'}},
        ]

        class Lines:
            def __init__(self, values):
                self.values = values

            def __aiter__(self):
                async def generate():
                    for event in self.values:
                        yield ('data: ' + json.dumps(event) + '\n').encode()
                        yield b'\n'

                return generate()

        response = SimpleNamespace(status=200, content=Lines(events), release=lambda: None)
        session = SimpleNamespace(request=AsyncMock(return_value=response))
        modules = {
            'open_webui.env': SimpleNamespace(AIOHTTP_CLIENT_SESSION_SSL=True),
            'open_webui.utils.json_codec': SimpleNamespace(
                JSONCodec=SimpleNamespace(dumps=json.dumps, loads=json.loads)
            ),
            'open_webui.utils.session_pool': SimpleNamespace(
                get_client_timeout=lambda **kwargs: 60, get_session=AsyncMock(return_value=session)
            ),
        }
        payload = self.payload | {
            'tools': [{'type': 'function', 'name': 'lookup'}],
            'stream': False,
            'tool_choice': 'none',
        }
        with patch.dict('sys.modules', modules):
            output = asyncio.run(
                self.scope['_compact_upstream'](
                    payload,
                    self.kwargs['request_url'],
                    {'Authorization': 'synthetic', 'X-Codex-Beta-Features': 'other_beta'},
                    None,
                    protocol='codex_v2',
                )
            )
        self.assertEqual(output, [compact])
        call = session.request.await_args.kwargs
        body = json.loads(call['data'])
        self.assertEqual(call['url'], self.kwargs['request_url'])
        self.assertEqual(body['model'], self.payload['model'])
        self.assertTrue(body['stream'])
        self.assertFalse(body['store'])
        self.assertEqual(body['tools'], payload['tools'])
        self.assertNotIn('tool_choice', body)
        self.assertEqual(body['input'][-1], {'type': 'compaction_trigger'})
        self.assertEqual(call['headers']['x-codex-beta-features'], 'other_beta,remote_compaction_v2')
        for invalid in [
            events[:-1],
            events[:1] + events[-1:],
            events + [{'type': 'response.failed'}],
            events[:-1]
            + [{'type': 'response.output_item.done', 'output_index': 1, 'item': compact | {'id': 'other'}}]
            + events[-1:],
            events[:-1]
            + [
                {
                    'type': 'response.output_item.done',
                    'output_index': 0,
                    'item': compact | {'encrypted_content': 'conflict'},
                }
            ]
            + events[-1:],
        ]:
            response.content = Lines(invalid)
            with patch.dict('sys.modules', modules), self.assertRaises(RuntimeError):
                asyncio.run(
                    self.scope['_compact_upstream'](payload, self.kwargs['request_url'], {}, None, protocol='codex_v2')
                )


if __name__ == '__main__':
    unittest.main()
