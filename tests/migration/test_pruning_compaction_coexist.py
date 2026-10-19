"""Pruned replay must still reach text/native compaction and reuse safe windows.

Runs production functions, mocking only storage, configuration and upstream I/O.
No application startup, live DB, credential or paid model request is required.
"""

import asyncio
import copy
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = load_module('coexist_native_fixture', ROOT / 'tests/migration/test_native_compaction.py')
idle = load_module('coexist_idle', ROOT / 'backend/open_webui/utils/idle_tool_pruning.py')


def tool_message(text='large original result ' * 100):
    return {
        'id': 'old-assistant',
        'role': 'assistant',
        'done': True,
        'content': 'Original assistant prose',
        'output': [
            {'type': 'function_call', 'call_id': 'c1', 'name': 'lookup', 'arguments': '{}', 'status': 'completed'},
            {'type': 'function_call_output', 'call_id': 'c1', 'output': text},
        ],
    }


class PruningNativeCoexistTests(unittest.TestCase):
    def setUp(self):
        self.f = native.NativeCompactionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.metadata['idle_tool_output_pruning'] = True

    def test_no_tool_results_still_trigger_120000_native_threshold(self):
        self.f.config.update(token_threshold=120000, token_cap=200000)
        self.f.payload['input'][0] = native.item('token ' * 120100)
        result = self.f.run_request()
        self.f.scope['_compact_upstream'].assert_awaited_once()
        self.assertEqual(result['model'], self.f.payload['model'])
        self.assertIn('compaction', str(result['input']))

    def replay_payload(self, chars):
        original = tool_message()
        masks = {idle.result_key(original, original['output'][1], 1): {'chars': chars}}
        replay = idle.apply_masks([original], masks)[0]
        result = copy.deepcopy(self.f.payload)
        result['input'] = [native.item('old question'), *replay['output'], native.item('current question')]
        return result

    def test_masked_results_compact_then_unchanged_masks_reuse_checkpoint(self):
        payload = self.replay_payload(4)
        first = self.f.run_request(payload)
        self.assertEqual(
            self.f.scope['_compact_upstream'].await_args.args[0]['input'][2]['output'][0]['text'], 'larg\n[truncated]'
        )
        second = self.f.run_request(payload)
        self.assertEqual(first, second)
        self.assertEqual(self.f.scope['_compact_upstream'].await_count, 1)

    def test_mask_change_rebuilds_instead_of_restoring_old_opaque_state(self):
        self.f.run_request(self.replay_payload(20))
        self.f.compacted[-1]['encrypted_content'] = 'new-opaque'
        result = self.f.run_request(self.replay_payload(0))
        self.assertEqual(self.f.scope['_compact_upstream'].await_count, 2)
        self.assertEqual(result['input'][-1]['encrypted_content'], 'new-opaque')
        self.assertEqual(self.f.scope['_compact_upstream'].await_args.args[0]['input'][2]['output'][0]['text'], '')

    def test_pending_batch_is_preserved_then_completed_continue_can_compact(self):
        payload = copy.deepcopy(self.f.payload)
        call = {'type': 'function_call', 'call_id': 'pending', 'name': 'approved', 'arguments': '{}'}
        payload['input'].append(call)
        self.assertEqual(self.f.run_request(payload)['input'], payload['input'])
        self.f.scope['_compact_upstream'].assert_not_awaited()
        payload['input'].append({'type': 'function_call_output', 'call_id': 'pending', 'output': 'approved result'})
        self.f.run_request(payload)
        self.f.scope['_compact_upstream'].assert_awaited_once()

    def test_tool_loop_new_results_follow_reused_checkpoint(self):
        payload = self.replay_payload(0)
        first = self.f.run_request(payload)
        self.f.config['token_threshold'] = 80000
        batch = [
            {'type': 'function_call', 'call_id': 'fresh', 'name': 'lookup', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'fresh', 'output': 'new result remains'},
        ]
        payload['input'].extend(batch)
        self.assertEqual(self.f.run_request(payload)['input'], first['input'] + batch)
        self.assertEqual(self.f.scope['_compact_upstream'].await_count, 1)

    def test_codex_v2_masked_history_uses_same_model_and_reuses_window(self):
        self.f.kwargs['api_config']['native_compaction_protocol'] = 'codex_v2'
        self.f.compacted = [{'type': 'compaction', 'encrypted_content': 'same-model-opaque'}]
        payload = self.replay_payload(0)
        first = self.f.run_request(payload)
        self.assertEqual(first['input'], self.f.compacted + payload['input'][-1:])
        call = self.f.scope['_compact_upstream'].await_args
        self.assertEqual(call.args[0]['model'], payload['model'])
        self.assertEqual(call.kwargs['protocol'], 'codex_v2')
        self.assertEqual(call.args[0]['input'], payload['input'][:-1])
        self.assertEqual(self.f.run_request(payload), first)
        self.assertEqual(self.f.scope['_compact_upstream'].await_count, 1)


class PruningTextCoexistTests(unittest.TestCase):
    def setUp(self):
        self.f = native.TextCompactionRoutingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.metadata = {'idle_tool_output_pruning': True}

    def run_text(self, messages=None):
        return asyncio.run(
            self.f.scope['compact_messages_for_request'](
                SimpleNamespace(), None, messages or self.f.messages, self.metadata, 'non-gpt', {}
            )
        )

    def test_non_gpt_above_threshold_still_generates_text_summary(self):
        messages, summary, changed = self.run_text()
        self.assertTrue(changed)
        self.assertEqual(summary, 'facts preserved')
        self.assertLess(len(messages), len(self.f.messages))

    def test_new_text_checkpoint_persists_mask_binding_and_reopens_without_summary_call(self):
        original = copy.deepcopy(self.f.messages)
        original[2] = tool_message()
        masks = {idle.result_key(original[2], original[2]['output'][1], 1): {'chars': 0}}
        signature = idle.pruning_signature(original, masks)
        self.metadata.update(chat_id='chat', message_id='a-new', _idle_tool_pruning_signature=signature)
        self.f.scope['is_saved_chat_id'] = lambda cid: cid == 'chat'
        saved = AsyncMock()
        self.f.scope['Chats'] = SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=saved)
        # Disable only UI events in this unit test, not saved-chat persistence.
        self.metadata.pop('message_id')
        messages, summary, changed = self.run_text(idle.apply_masks(original, masks))
        self.assertTrue(changed)
        call = saved.await_args
        self.assertEqual(call.args[2]['meta']['context_summary_pruning'], signature)
        anchor = call.args[1]
        next(message for message in original if message.get('id') == anchor).update(copy.deepcopy(call.args[2]))
        self.f.config['token_threshold'] = 80000
        self.f.scope['_generate_summary'].reset_mock()
        second, reopened_summary, changed = self.run_text(idle.apply_masks(original, masks))
        self.assertEqual(reopened_summary, summary)
        self.assertFalse(changed)
        self.assertEqual(len(second), len(messages))
        self.f.scope['_generate_summary'].assert_not_awaited()

    def test_manual_text_compaction_uses_masked_replay_instead_of_short_circuit(self):
        original = copy.deepcopy(self.f.messages)
        self.f.scope['Config'] = SimpleNamespace(get=AsyncMock(return_value=True))
        self.f.scope['get_message_list'] = lambda mapping, current: list(mapping.values())
        self.f.scope['Chats'] = SimpleNamespace(
            get_messages_map_by_chat_id=AsyncMock(return_value={str(i): message for i, message in enumerate(original)}),
            upsert_message_to_chat_by_id_and_message_id=AsyncMock(),
        )
        replay = AsyncMock(return_value=original)
        with native.patch.dict(
            'sys.modules', {'open_webui.utils.idle_tool_pruning': SimpleNamespace(replay_messages=replay)}
        ):
            result = asyncio.run(
                self.f.scope['compact_chat_branch'](
                    SimpleNamespace(), None, SimpleNamespace(id='chat', chat={}, current_message_id='7'), 'non-gpt', {}
                )
            )
        self.assertTrue(result['compacted'])
        replay.assert_awaited_once()
        self.f.scope['_generate_summary'].assert_awaited_once()

    def test_no_masks_preserve_legacy_text_and_native_checkpoints(self):
        messages = copy.deepcopy(self.f.messages)
        messages[3]['contextSummary'] = 'safe old summary'
        messages[3]['meta'] = {'native_compaction': {'output': 'validated separately'}}
        replay = idle.apply_masks(messages, {})
        self.f.config['token_threshold'] = 80000
        result, summary, changed = self.run_text(replay)
        self.assertEqual(summary, 'safe old summary')
        self.assertFalse(changed)
        self.assertEqual(result, [replay[0], *replay[3:]])
        self.assertEqual(replay[3]['meta']['native_compaction'], messages[3]['meta']['native_compaction'])

    def test_changed_masks_invalidate_old_text_summary_without_mutating_history(self):
        old = tool_message()
        old['contextSummary'] = 'obsolete summary containing full old result'
        old['output'].append({'type': 'open_webui:context_compaction', 'summary': 'obsolete', 'call_id': 'c1'})
        original = copy.deepcopy(old)
        masks = {idle.result_key(old, old['output'][1], 1): {'chars': 0}}
        replay = idle.apply_masks([old], masks)
        self.assertNotIn('contextSummary', replay[0])
        self.assertFalse(any(item.get('type') == 'open_webui:context_compaction' for item in replay[0]['output']))
        self.assertEqual(old, original)

    def test_related_mask_signature_stable_and_ignores_sibling_masks(self):
        old = tool_message()
        masks = {idle.result_key(old, old['output'][1], 1): {'chars': 4}}
        signature = idle.pruning_signature([old], masks)
        old['contextSummary'] = 'safe masked summary'
        old['meta'] = {'context_summary_pruning': signature}
        replay = idle.apply_masks([old], {**masks, 'unrelated-sibling-result': {'chars': 0}})
        self.assertEqual(replay[0]['contextSummary'], 'safe masked summary')
        shrunk = {next(iter(masks)): {'chars': 0}}
        self.assertNotIn('contextSummary', idle.apply_masks([old], shrunk)[0])

    def test_masked_replay_does_not_use_old_unpruned_usage_to_trigger_summary(self):
        old = tool_message()
        messages = [self.f.messages[0], self.f.messages[1], old, *self.f.messages[-2:]]
        old['usage'] = {'prompt_tokens': 300000, 'completion_tokens': 100}
        masks = {idle.result_key(old, old['output'][1], 1): {'chars': 0}}
        self.f.config['token_threshold'] = 10000
        _, summary, changed = self.run_text(idle.apply_masks(messages, masks))
        self.assertIsNone(summary)
        self.assertFalse(changed)
        self.f.scope['_generate_summary'].assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
