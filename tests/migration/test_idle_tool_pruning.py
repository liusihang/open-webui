"""Actual token budgets and provider replay, without app startup or an LLM."""

import ast
import asyncio
import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pruning = load_module('idle_tool_pruning', ROOT / 'backend/open_webui/utils/idle_tool_pruning.py')
native = load_module('native_history_fixture', ROOT / 'tests/migration/test_responses_native_history.py')


def assistant(label, text, *, done=True):
    output = copy.deepcopy(native.history())
    for item in output:
        if item.get('id'):
            item['id'] += label
        if item.get('call_id'):
            item['call_id'] += label
    output[3]['output'][0]['text'] = text
    return {'id': label, 'role': 'assistant', 'content': 'rendered prose', 'done': done, 'output': output}


def visible_content(message):
    return pruning.result_text(message['output'][3])[0].removesuffix(pruning.TRUNCATED).replace(pruning.PLACEHOLDER, '')


class PruningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoding = pruning.resolve_encoding('gpt-4')

    def plan(self, messages, budget=4000, masks=None):
        return pruning.plan_masks(messages, masks or {}, budget, 'gpt-4')[0]

    def test_4000_aggregate_tokens_not_characters_or_per_result(self):
        history = [assistant('old', ' old' * 3500), assistant('new', ' new' * 3000)]
        original = copy.deepcopy(history)
        masks = self.plan(history)
        result = pruning.apply_masks(history, masks)
        lengths = [len(self.encoding.encode(visible_content(message))) for message in result]
        self.assertEqual(lengths, [1000, 3000])
        self.assertEqual(sum(lengths), 4000)
        self.assertGreater(sum(len(visible_content(message)) for message in result), 4000)
        self.assertEqual(history, original)
        self.assertTrue(result[0]['output'][3]['output'][0]['text'].endswith(pruning.TRUNCATED))

    def test_boundary_result_prefix_and_older_placeholders(self):
        history = [assistant('old', ' older' * 100), assistant('new', ' newest' * 100)]
        result = pruning.apply_masks(history, self.plan(history, 20))
        self.assertEqual(visible_content(result[0]), '')
        self.assertEqual(visible_content(result[1]), ' newest' * 20)
        self.assertEqual(result[0]['output'][2], history[0]['output'][2])
        self.assertEqual(result[0]['output'][3]['call_id'], result[0]['output'][2]['call_id'])

    def test_unicode_special_tokens_and_zero_budget(self):
        history = [assistant('a', '数据🙂🔬<|endoftext|>' * 100)]
        for budget in (0, 1, 2, 17, 200):
            result = pruning.apply_masks(history, self.plan(history, budget))
            text = visible_content(result[0])
            self.assertTrue(history[0]['output'][3]['output'][0]['text'].startswith(text))
            self.assertNotIn('\ufffd', text)
            self.assertLessEqual(len(self.encoding.encode(text, disallowed_special=())), budget)

    def test_new_results_remain_until_next_episode_and_masks_only_shrink(self):
        old = assistant('old', ' old' * 100)
        masks = self.plan([old], 10)
        fresh = assistant('fresh', ' fresh' * 100)
        result = pruning.apply_masks([old, fresh], masks)
        self.assertEqual(result[1], fresh)
        next_masks = self.plan([old, fresh], 20, masks)
        result = pruning.apply_masks([old, fresh], next_masks)
        self.assertEqual(visible_content(result[0]), '')
        self.assertEqual(visible_content(result[1]), ' fresh' * 20)
        # Removing the newer item / changing the model never refills old output.
        self.assertEqual(visible_content(pruning.apply_masks([old], next_masks)[0]), '')
        self.assertEqual(visible_content(pruning.apply_masks([old], self.plan([old], 1000, masks))[0]), ' old' * 10)

    def test_stable_id_survives_commentary_insertion_and_result_edit_is_new(self):
        old = assistant('old', ' old' * 100)
        masks = self.plan([old], 0)
        edited = copy.deepcopy(old)
        edited['output'].insert(0, {'type': 'message', 'content': [{'type': 'output_text', 'text': 'More prose.'}]})
        result = pruning.apply_masks([edited], masks)[0]
        self.assertEqual(result['output'][4]['output'][0]['text'], pruning.PLACEHOLDER)
        edited['output'][4]['output'][0]['text'] = 'new evidence'
        self.assertEqual(pruning.apply_masks([edited], masks)[0], edited)

    def test_pending_unfinished_and_unpaired_results_untouched(self):
        for status in ('pending', 'queued', 'in_progress', 'requires_approval'):
            message = assistant(status, ' keep' * 100)
            message['output'][2]['status'] = status
            self.assertEqual(self.plan([message], 0), {})
        for done in (False, None):
            self.assertEqual(self.plan([assistant('unfinished', ' keep', done=done)], 0), {})
        message = assistant('missing', ' keep')
        del message['output'][2]
        self.assertEqual(self.plan([message], 0), {})
        message = assistant('partial', ' keep')
        message['output'][3]['status'] = 'incomplete'
        self.assertEqual(self.plan([message], 0), {})

    def test_result_images_removed_before_flattening_user_images_preserved(self):
        message = assistant('image', 'caption')
        message['output'][3]['output'].append({'type': 'input_image', 'image_url': 'data:image/png;base64,TOOL'})
        user = {'id': 'user', 'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'USER_IMAGE'}}]}
        result = pruning.apply_masks([user, message], self.plan([user, message]))
        self.assertEqual(result[0], user)
        self.assertNotIn('TOOL', json.dumps(result))
        scope = native.scope()
        replay = scope['process_messages_with_output'](result)
        self.assertEqual(
            [item for item in replay if item['role'] == 'user'], [{'role': 'user', 'content': user['content']}]
        )
        self.assertIn('caption', json.dumps(replay))

    def test_native_responses_and_chat_preserve_prose_and_pairs(self):
        history = [assistant('a', 'SECRET_OLD_RESULT ' * 100)]
        result = pruning.apply_masks(history, self.plan(history, 0))
        scope = native.scope()
        for responses in (False, True):
            replay = scope['process_messages_with_output'](result, responses_api=responses)
            replay = scope['sanitize_tool_pairs'](replay)
            if responses:
                replay = scope['convert_to_responses_payload']({'messages': replay})['input']
                self.assertEqual(
                    [item['phase'] for item in replay if item['type'] == 'message'], ['commentary', 'final_answer']
                )
                call, output = [item for item in replay if item['type'].startswith('function_call')]
                self.assertEqual(call['call_id'], output['call_id'])
            else:
                calls = [item for item in replay if item.get('tool_calls')]
                outputs = [item for item in replay if item['role'] == 'tool']
                self.assertEqual(calls[0]['tool_calls'][0]['id'], outputs[0]['tool_call_id'])
            text = json.dumps(replay)
            self.assertNotIn('SECRET_OLD_RESULT', text)
            self.assertIn('Checking.', text)
            self.assertIn('42', text)

    def test_continue_tool_loop_masks_canonical_prefix_again_on_every_round(self):
        old = assistant('old', 'SECRET_OLD ' * 100)
        masks = self.plan([old], 0)
        metadata = {
            'idle_tool_output_pruning': True,
            'assistant_message_id': old['id'],
            '_idle_tool_pruning_masks': masks,
        }
        canonical = copy.deepcopy(old['output'])
        scope = native.scope()
        tool_messages = native.load_function(native.MIDDLEWARE, 'get_tool_loop_output_messages', scope)
        with patch.dict(sys.modules, {'open_webui.utils.idle_tool_pruning': pruning}):
            for round_number in range(3):
                canonical.extend(assistant(f'new-{round_number}', f'FRESH_RESULT_{round_number}')['output'])
                snapshot = copy.deepcopy(canonical)
                for responses in (False, True):
                    replay = tool_messages(canonical, responses_api=responses, metadata=metadata)
                    if responses:
                        replay = scope['convert_to_responses_payload']({'messages': replay})['input']
                    self.assertNotIn('SECRET_OLD', json.dumps(replay))
                    self.assertIn(f'FRESH_RESULT_{round_number}', json.dumps(replay))
                    self.assertIn('Checking.', json.dumps(replay))
                    self.assertEqual(canonical, snapshot)
        # Exercise the production caller's wiring, not just the helper in isolation.
        tree = ast.parse(native.MIDDLEWARE.read_text())
        loop_call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == 'get_tool_loop_output_messages'
            and 'full_output()' in ast.unparse(node)
        )
        self.assertIn('metadata', [keyword.arg for keyword in loop_call.keywords])

    def test_zero_budget_does_not_tokenize_discarded_megabytes(self):
        encoding = type(
            'NeverTokenize', (), {'encode': lambda *args, **kwargs: self.fail('encoded discarded source')}
        )()
        self.assertEqual(pruning.text_prefix('x' * 1000000, 0, encoding), ('', 0))

    def test_legacy_code_preserved_only_execution_output_pruned(self):
        item = {
            'type': 'open_webui:code_interpreter',
            'id': 'code1',
            'status': 'completed',
            'code': 'print(42)',
            'output': {'stdout': ' legacy' * 100},
        }
        message = {
            'id': 'a',
            'role': 'assistant',
            'done': True,
            'content': 'Prose stays',
            'output': [item, native.history()[1]],
        }
        result = pruning.apply_masks([message], self.plan([message], 4))
        self.assertEqual(result[0]['content'], message['content'])
        self.assertEqual(result[0]['output'][0]['code'], 'print(42)')
        replay = native.scope()['process_messages_with_output'](result)
        self.assertIn('print(42)', json.dumps(replay))
        self.assertIn('Checking.', json.dumps(replay))
        self.assertNotIn(' legacy' * 5, json.dumps(replay))

    def test_compaction_checkpoints_removed_only_on_request_copy(self):
        message = assistant('a', ' result')
        message.update(
            contextSummary='OLD_SUMMARY',
            context_summary='OLD_SUMMARY',
            meta={'native_compaction': {'output': 'OLD_PAYLOAD'}, 'other': 'keep'},
        )
        message['output'].append({'type': 'open_webui:context_compaction', 'summary': 'OLD_SUMMARY'})
        original = copy.deepcopy(message)
        result = pruning.apply_masks([message], {})[0]
        self.assertEqual(message, original)
        self.assertNotIn('OLD_', json.dumps(result))
        self.assertEqual(result['meta'], {'other': 'keep'})

    def test_unknown_model_uses_real_fallback_with_warning(self):
        pruning.resolve_encoding.cache_clear()
        with self.assertLogs(pruning.log, level='WARNING') as captured:
            encoder = pruning.resolve_encoding('unknown-provider/custom-model')
        self.assertEqual(encoder.name, 'cl100k_base')
        self.assertIn('not an exact provider token count', captured.output[0])
        self.assertEqual(
            self.plan([assistant('a', ' hi' * 50)], 10)[
                pruning.result_key(assistant('a', ' hi' * 50), assistant('a', ' hi' * 50)['output'][3], 3)
            ]['chars'],
            30,
        )

    def test_unavailable_tokenizer_drops_only_eligible_outputs_without_character_estimate(self):
        old, pending = assistant('old', ' old'), assistant('pending', ' pending', done=False)
        with patch.object(pruning, 'resolve_encoding', side_effect=OSError('offline')):
            with self.assertLogs(pruning.log, level='ERROR'):
                masks, tokenizer = pruning.plan_masks([old, pending], {}, 4000, 'gpt-4')
        self.assertEqual(tokenizer, 'unavailable:omit-results')
        result = pruning.apply_masks([old, pending], masks)
        self.assertEqual(visible_content(result[0]), '')
        self.assertEqual(result[1], pending)

    def test_text_compaction_and_native_checkpoint_bypass_before_external_work(self):
        scope = {'Any': object, '_load_config': AsyncMock(side_effect=AssertionError('should not load summaries'))}
        compact = native.load_function(
            ROOT / 'backend/open_webui/utils/context_compaction.py', 'compact_messages_for_request', scope
        )
        messages = [assistant('a', ' keep')]
        returned = asyncio.run(compact(None, None, messages, {'idle_tool_output_pruning': True}, 'model', {}))
        self.assertEqual(returned, (messages, None, False))
        compaction = load_module('native_compaction_fixture', ROOT / 'tests/migration/test_native_compaction.py')
        fixture = compaction.NativeCompactionTests()
        fixture.setUp()
        try:
            fixture.metadata['idle_tool_output_pruning'] = True
            fixture.map['u1']['meta']['native_compaction'] = {'output': 'old opaque tool payload'}
            self.assertEqual(fixture.run_request(), fixture.payload)
            fixture.scope['_compact_upstream'].assert_not_awaited()
            fixture.chats.get_messages_map_by_chat_id.assert_not_awaited()
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
