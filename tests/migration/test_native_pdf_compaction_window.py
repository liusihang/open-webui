"""A native checkpoint replays complete provider output, not old PDF input."""

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'pdf_window_native_fixture', ROOT / 'tests/migration/test_native_compaction.py'
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def pdf(name):
    return {'type': 'input_file', 'filename': name, 'file_data': 'data:application/pdf;base64,c3ludGhldGlj'}


class NativePdfCompactionWindowTests(unittest.TestCase):
    def setUp(self):
        self.f = FIXTURE.NativeCompactionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.kwargs['api_config']['native_compaction_protocol'] = 'codex_v2'
        self.f.compacted = [{'type': 'compaction', 'encrypted_content': 'opaque-document-state'}]
        self.old_pdf = FIXTURE.item('Read this document')
        self.old_pdf['content'].append(pdf('old.pdf'))
        self.f.payload['input'] = [
            self.old_pdf,
            FIXTURE.item('READY', 'assistant'),
            FIXTURE.item('What was the result?'),
        ]

    def test_complete_provider_window_replaces_old_pdf_and_messages(self):
        original = copy.deepcopy(self.f.payload)
        result = self.f.run_request()
        self.assertEqual(result['input'], self.f.compacted + original['input'][-1:])
        self.assertEqual(self.f.payload, original, 'The original transcript and PDF are retained')
        self.assertEqual(self.f.scope['_compact_upstream'].await_args.args[0]['input'], original['input'][:-1])
        self.assertEqual(self.f.map['a1']['meta']['native_compaction']['output'], self.f.compacted)

    def test_provider_retained_files_and_all_ordered_items_are_not_pruned(self):
        retained = FIXTURE.item('Provider explicitly retained this part')
        retained['content'].append(pdf('provider-retained.pdf'))
        self.f.compacted.insert(0, retained)
        self.assertEqual(self.f.run_request()['input'], self.f.compacted + self.f.payload['input'][-1:])

    def test_new_current_pdf_is_not_part_of_old_compaction(self):
        self.f.payload['input'][-1]['content'].append(pdf('new-current.pdf'))
        result = self.f.run_request()
        self.assertEqual(result['input'], self.f.compacted + self.f.payload['input'][-1:])
        self.assertNotIn('new-current.pdf', str(self.f.scope['_compact_upstream'].await_args.args[0]['input']))
        self.assertIn('new-current.pdf', str(result['input']))
        self.assertNotIn('old.pdf', str(result['input']))

    def test_reopen_reuses_window_without_old_pdf_or_second_compaction(self):
        self.f.run_request()
        self.f.config['token_threshold'] = 80000
        follow = copy.deepcopy(self.f.payload)
        follow['input'].extend([FIXTURE.item('Saved reply', 'assistant'), FIXTURE.item('Follow-up')])
        result = self.f.run_request(follow)
        self.assertEqual(result['input'], self.f.compacted + follow['input'][2:])
        self.f.scope['_compact_upstream'].assert_awaited_once()
        self.assertNotIn('old.pdf', str(result['input']))

    def test_tool_loop_replay_keeps_current_results_without_reintroducing_old_pdf(self):
        self.f.run_request()
        batch = [
            {'type': 'function_call', 'call_id': 'new', 'name': 'lookup', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'new', 'output': 'New evidence'},
        ]
        replay = copy.deepcopy(self.f.payload)
        replay['input'].extend(batch)
        self.assertEqual(self.f.run_request(replay)['input'], self.f.compacted + replay['input'][2:])
        self.f.scope['_compact_upstream'].assert_awaited_once()

    def test_failed_compaction_keeps_original_pdf_and_transcript(self):
        original = copy.deepcopy(self.f.payload)
        self.f.scope['_compact_upstream'].side_effect = RuntimeError('Synthetic provider failure')
        result = self.f.run_request()
        self.assertEqual(result['input'], original['input'])
        self.assertEqual(self.f.payload, original)
        self.assertIn('old.pdf', str(result['input']))

    def test_legacy_v2_window_binding_cannot_restore_full_pdf(self):
        self.f.run_request()
        state = self.f.map['a1']['meta']['native_compaction']
        # Recreate the deployed legacy binding and its client-added PDF window.
        state['binding'] = self.f.scope['_digest'](
            [
                'codex_v2',
                self.f.kwargs['connection_index'],
                self.f.kwargs['request_url'],
                self.f.scope['_digest'](self.f.kwargs['api_key']),
                self.f.payload['model'],
                self.f.payload['instructions'],
            ]
        )
        state['output'] = [copy.deepcopy(self.old_pdf), *self.f.compacted]
        self.f.run_request()
        self.assertEqual(self.f.scope['_compact_upstream'].await_count, 2)
        self.assertEqual(self.f.map['a1']['meta']['native_compaction']['output'], self.f.compacted)


if __name__ == '__main__':
    unittest.main()
