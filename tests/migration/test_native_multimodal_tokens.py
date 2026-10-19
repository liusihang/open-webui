"""Offline regression for native structured-media budgets and bound usage reuse."""

import asyncio
import ast
import base64
import copy
import io
import json
import unittest
import types
from unittest.mock import AsyncMock
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image, PngImagePlugin
from pypdf import PdfWriter

import test_native_compaction as native_tests

item = native_tests.item


def image_part(padding=0, detail='auto'):
    stream = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('irrelevant-transport-padding', '0123456789abcdef' * padding)
    Image.new('RGB', (512, 512), 'white').save(stream, format='PNG', pnginfo=metadata)
    return {
        'type': 'input_image',
        'detail': detail,
        'image_url': 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode(),
    }


class MultimodalTokenTests(unittest.TestCase):
    setUp = native_tests.NativeCompactionTests.setUp
    save = native_tests.NativeCompactionTests.save
    run_request = native_tests.NativeCompactionTests.run_request
    v2 = native_tests.CodexV2Tests.v2

    def estimate(self, value):
        return self.scope['_estimate_native_tokens'](value)

    def test_image_encoding_length_does_not_change_semantic_budget(self):
        small, large = image_part(), image_part(10000)
        self.assertGreater(len(large['image_url']), 200000)
        self.assertEqual(self.estimate([small]), self.estimate([large]))
        self.assertGreater(self.estimate([small]), 0)

    def test_thirty_images_no_immediate_recompact_after_json_reopen(self):
        self.v2(
            [
                item('fixed system', 'developer'),
                item('look') | {'content': [image_part(10000)] * 30},
                item('病毒衣壳蛋白消毒耐受性' * 11000, 'assistant'),
                item('next'),
            ]
        )
        self.config.update(token_threshold=120000, token_cap=120000)
        original = json.dumps(self.payload, ensure_ascii=False)
        self.run_request()
        self.scope['_compact_upstream'].assert_awaited_once()
        self.map = json.loads(json.dumps(self.map))
        follow = self.payload | {'input': self.payload['input'] + [item('reply', 'assistant'), item('again')]}
        self.run_request(follow)
        self.assertEqual(self.scope['_compact_upstream'].await_count, 1)
        self.assertEqual(json.dumps(self.payload, ensure_ascii=False), original)
        window = self.map['a1']['meta']['native_compaction']['output']
        # The provider's complete window replaces old client images too; only
        # the retained source transcript keeps the large image transport.
        self.assertEqual(len(window), len(self.compacted))
        self.assertEqual(window, self.compacted)

    def test_pdf_bytes_not_tokenized_and_pages_are_not_omitted(self):
        def pdf(pages):
            writer = PdfWriter()
            for _ in range(pages):
                writer.add_blank_page(width=612, height=792)
            stream = io.BytesIO()
            writer.write(stream)
            return {
                'type': 'input_file',
                'filename': 'scan.pdf',
                'file_data': 'data:application/pdf;base64,' + base64.b64encode(stream.getvalue()).decode(),
            }

        self.assertGreater(self.estimate(pdf(1)), 0)
        self.assertGreaterEqual(self.estimate(pdf(2)) - self.estimate(pdf(1)), 1844)

    def test_unknown_or_malformed_file_is_conservative_not_free(self):
        for part in (
            {'type': 'input_file', 'file_id': 'provider-only'},
            {'type': 'input_file', 'file_data': 'data:application/pdf;base64,broken'},
            {'type': 'input_audio', 'data': 'transport-not-text'},
        ):
            with self.subTest(part=part['type']):
                self.assertGreater(self.estimate(part), 120000)

    def test_opaque_budget_is_not_ciphertext_tokenization(self):
        budget = self.estimate({'type': 'compaction', 'encrypted_content': 'X' * 19768})
        self.assertGreater(budget, 0)
        self.assertLess(budget, 2000)
        self.assertEqual(budget, self.estimate({'type': 'compaction', 'encrypted_content': 'Y' * 19768}))

    def test_reasoning_and_generated_image_use_their_own_structured_budgets(self):
        opaque = 'X' * 19768
        self.assertGreater(
            self.estimate({'type': 'reasoning', 'encrypted_content': opaque}),
            self.estimate({'type': 'compaction', 'encrypted_content': opaque}),
        )
        self.assertEqual(
            self.estimate({'type': 'image_generation_call', 'result': 'X' * 10}),
            self.estimate({'type': 'image_generation_call', 'result': 'Y' * 200000}),
        )

    def test_tool_schema_and_dynamic_memory_still_trigger(self):
        self.v2()
        self.config.update(token_threshold=120000, token_cap=120000)
        self.payload['tools'] = [
            {'type': 'function', 'name': 'test', 'parameters': {'description': '病毒衣壳蛋白消毒耐受性' * 11000}}
        ]
        self.run_request()
        self.scope['_compact_upstream'].assert_awaited_once()

    def test_usage_anchor_is_exactly_bound_and_new_suffix_is_counted(self):
        self.v2([item('fixed'), item('answer', 'assistant'), item('look') | {'content': [image_part()] * 80}])
        self.config.update(token_threshold=200000, token_cap=200000)
        self.run_request()
        stamp = copy.deepcopy(self.metadata['_native_token_usage'])
        self.map['a1']['done'] = True
        self.map['a1']['meta'] = {'native_token_usage': stamp | {'input_tokens': 21000}}
        follow = self.payload | {'input': self.payload['input'] + [item('answer', 'assistant'), item('next')]}
        self.config.update(token_threshold=120000, token_cap=120000)
        self.run_request(follow)
        self.scope['_compact_upstream'].assert_not_awaited()
        # New real text above threshold wins even with a small observed baseline.
        follow['input'][-1] = item('病毒衣壳蛋白消毒耐受性' * 11000)
        self.run_request(follow)
        self.scope['_compact_upstream'].assert_awaited_once()

    def test_changed_tools_or_prefix_invalidates_usage_anchor(self):
        self.v2([item('fixed'), item('answer', 'assistant'), item('look') | {'content': [image_part()] * 80}])
        self.config.update(token_threshold=200000, token_cap=200000)
        self.run_request()
        self.map['a1']['done'] = True
        self.map['a1']['meta'] = {
            'native_token_usage': copy.deepcopy(self.metadata['_native_token_usage']) | {'input_tokens': 21000}
        }
        follow = self.payload | {'input': self.payload['input'] + [item('answer', 'assistant'), item('next')]}
        self.config.update(token_threshold=120000, token_cap=120000)
        follow['tools'] = [{'type': 'function', 'name': 'new-tool'}]
        self.run_request(follow)
        self.scope['_compact_upstream'].assert_awaited_once()

    def test_only_completed_raw_response_usage_is_saved(self):
        self.v2()
        self.run_request()
        fn = self.scope['save_native_token_usage']
        self.chats.upsert_message_to_chat_by_id_and_message_id.reset_mock()
        event = {
            'model': self.payload['model'],
            'usage': {'input_tokens': 21433, 'input_tokens_details': {'cached_tokens': 21248}, 'output_tokens': 66},
        }
        asyncio.run(fn(SimpleNamespace(id='owner'), self.metadata, event))
        saved = self.map['a1']['meta']['native_token_usage']
        self.assertEqual(saved['input_tokens'], 21433)
        self.assertEqual(saved['output_tokens'], 66)
        self.assertNotIn('_native_token_usage', self.metadata)
        self.assertIn('native_compaction', self.map['a1']['meta'])

    def test_invalid_observation_cannot_become_a_usage_anchor(self):
        self.v2()
        self.run_request()
        stamp = copy.deepcopy(self.metadata['_native_token_usage'])
        for change in (
            {'model': 'different'},
            {'status': 'incomplete'},
            {'usage': {'input_tokens': True}},
            {'usage': {'input_tokens': 0}},
            {'usage': {'input_tokens': -1}},
            {'usage': {}},
        ):
            with self.subTest(change=change):
                self.metadata['_native_token_usage'] = copy.deepcopy(stamp)
                self.chats.upsert_message_to_chat_by_id_and_message_id.reset_mock()
                response = {'model': self.payload['model'], 'usage': {'input_tokens': 12}} | change
                asyncio.run(self.scope['save_native_token_usage'](SimpleNamespace(id='owner'), self.metadata, response))
                self.chats.upsert_message_to_chat_by_id_and_message_id.assert_not_awaited()
        self.metadata['_native_token_usage'] = stamp
        asyncio.run(
            self.scope['save_native_token_usage'](
                SimpleNamespace(id='other-owner'),
                self.metadata,
                {'model': self.payload['model'], 'usage': {'input_tokens': 12}},
            )
        )
        self.chats.upsert_message_to_chat_by_id_and_message_id.assert_not_awaited()

    def test_anchor_reuse_rejects_each_changed_binding(self):
        original = [item('fixed'), item('reply', 'assistant'), item('look') | {'content': [image_part()] * 80}]
        for change in ('route', 'model', 'key', 'prefix', 'active', 'incomplete', 'owner'):
            with self.subTest(change=change):
                self.setUp()
                self.v2(copy.deepcopy(original))
                self.config.update(token_threshold=200000, token_cap=200000)
                self.run_request()
                self.map['a1']['done'] = True
                self.map['a1']['meta'] = {
                    'native_token_usage': copy.deepcopy(self.metadata['_native_token_usage']) | {'input_tokens': 21000}
                }
                follow = self.payload | {'input': self.payload['input'] + [item('answer', 'assistant'), item('next')]}
                self.config.update(token_threshold=120000, token_cap=120000)
                kwargs = {}
                if change == 'route':
                    kwargs['request_url'] = 'https://other.invalid/v1/responses'
                elif change == 'key':
                    kwargs['api_key'] = 'other-synthetic'
                elif change == 'model':
                    follow['model'] = 'another'
                    self.kwargs['api_config']['native_compaction_models'].append('another')
                elif change == 'prefix':
                    follow['input'][0] = item('edited')
                elif change == 'active':
                    self.map['a1']['meta']['native_token_usage']['active_hash'] = 'stale'
                elif change == 'owner':
                    self.map['a1']['meta']['native_token_usage']['binding'] = 'other-owner'
                elif change == 'incomplete':
                    self.map['a1']['done'] = False
                self.run_request(follow, **kwargs)
                self.scope['_compact_upstream'].assert_awaited_once()

    def test_memory_and_output_schema_are_not_lost_from_budget(self):
        for field in ('memory', 'schema'):
            with self.subTest(field=field):
                self.v2()
                self.scope['_compact_upstream'].reset_mock()
                self.map['a1'].pop('meta', None)
                self.config.update(token_threshold=120000, token_cap=120000)
                text = '病毒衣壳蛋白消毒耐受性' * 11000
                if field == 'memory':
                    self.metadata['_dynamic_memory_context'] = text
                else:
                    self.metadata.pop('_dynamic_memory_context', None)
                    self.payload['text'] = {'format': {'type': 'json_schema', 'schema': {'description': text}}}
                self.run_request()
                self.scope['_compact_upstream'].assert_awaited_once()

    def test_original_image_uses_dimensions_not_transport_length(self):
        small, padded = image_part(detail='original'), image_part(10000, detail='original')
        self.assertEqual(self.estimate(small), self.estimate(padded))
        self.assertLess(self.estimate(small), self.estimate(image_part()))
        self.assertGreater(self.estimate(small), 0)

    def test_provider_file_reference_remains_intact_and_can_use_observed_baseline(self):
        self.v2(
            [
                item('fixed'),
                item('reply', 'assistant'),
                item('read') | {'content': [{'type': 'input_file', 'file_id': 'provider-only'}]},
            ]
        )
        self.run_request()
        self.map['a1']['done'] = True
        self.map['a1']['meta']['native_token_usage'] = copy.deepcopy(self.metadata['_native_token_usage']) | {
            'input_tokens': 21000
        }
        follow = self.payload | {'input': self.payload['input'] + [item('reply', 'assistant'), item('next')]}
        self.config.update(token_threshold=120000, token_cap=120000)
        before = copy.deepcopy(follow)
        self.run_request(follow)
        self.assertEqual(self.scope['_compact_upstream'].await_count, 1)
        self.assertEqual(follow, before)

    def test_pdf_extracted_text_is_in_addition_to_each_page_image(self):
        part = {
            'type': 'input_file',
            'file_data': 'data:application/pdf;base64,' + base64.b64encode(b'%PDF-synthetic').decode(),
        }
        text = '病毒衣壳蛋白消毒耐受性' * 11000
        reader = SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: text)])
        with patch('pypdf.PdfReader', return_value=reader):
            self.assertGreater(self.estimate(part), 120000)

    def test_real_stream_and_nonstream_hooks_receive_single_raw_usage(self):
        module = types.ModuleType('open_webui.utils.native_compaction')
        module.save_native_token_usage = AsyncMock()
        response = {'model': 'gpt-6-luna', 'usage': {'input_tokens': 21433}}
        for filename, selector in (
            (
                'utils/middleware.py',
                lambda node: ast.unparse(node.test) == "response_data_type == 'response.completed'",
            ),
            ('routers/openai.py', lambda node: ast.unparse(node.test) == 'is_responses and isinstance(response, dict)'),
        ):
            with self.subTest(filename=filename):
                tree = ast.parse((native_tests.ROOT / 'backend/open_webui' / filename).read_text())
                block = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and selector(node))
                function = ast.parse('async def hook(response):\n    pass').body[0]
                function.body = [block]
                scope = {
                    'response_data_type': 'response.completed',
                    'data': {'response': response},
                    'user': SimpleNamespace(id='owner'),
                    'metadata': {},
                    'response': response,
                    'is_responses': True,
                    'convert_responses_result': lambda value: value,
                }
                exec(
                    compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), filename, 'exec'),
                    scope,
                )
                module.save_native_token_usage.reset_mock()
                with patch.dict('sys.modules', {'open_webui.utils.native_compaction': module}):
                    asyncio.run(scope['hook'](response))
                self.assertEqual(module.save_native_token_usage.await_args.args[2], response)

    def test_observed_output_usage_bounds_retained_reasoning_before_new_user(self):
        self.v2([item('fixed'), item('reply', 'assistant'), item('last user')])
        self.config.update(token_threshold=120000, token_cap=120000)
        self.run_request()
        self.map['a1']['done'] = True
        self.map['a1']['meta'] = {
            'native_token_usage': copy.deepcopy(self.metadata['_native_token_usage'])
            | {'input_tokens': 1000, 'output_tokens': 121000}
        }
        follow = self.payload | {
            'input': self.payload['input']
            + [{'type': 'reasoning', 'encrypted_content': 'opaque-reasoning'}, item('new user')]
        }
        self.run_request(follow)
        self.scope['_compact_upstream'].assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
