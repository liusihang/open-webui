"""Run the actual tool-loop branch for text-compacted Responses and Chat routes."""

import ast
import asyncio
import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'native_fixture', ROOT / 'tests/migration/test_responses_native_history.py'
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
IDLE_SPEC = importlib.util.spec_from_file_location(
    'text_continuation_pruning', ROOT / 'backend/open_webui/utils/idle_tool_pruning.py'
)
IDLE = importlib.util.module_from_spec(IDLE_SPEC)
IDLE_SPEC.loader.exec_module(IDLE)


class TextCompactionContinuationTests(unittest.TestCase):
    def exercise(self, responses_api, pruning=False):
        scope = FIXTURE.scope()
        for name in ('get_content_from_message', 'update_message_content', 'add_or_update_system_message'):
            FIXTURE.load_function(FIXTURE.MISC, name, scope)
        FIXTURE.load_function(FIXTURE.MIDDLEWARE, 'get_tool_loop_output_messages', scope)
        FIXTURE.load_function(FIXTURE.MISC, 'get_system_message', scope)
        FIXTURE.load_function(FIXTURE.MIDDLEWARE, 'context_summary_message', scope)
        FIXTURE.load_function(FIXTURE.MIDDLEWARE, 'apply_context_summary', scope)
        system = {'role': 'system', 'content': 'BASE INSTRUCTIONS\nINTELLIGENT UI INSTRUCTIONS'}
        original_system = copy.deepcopy(system)
        output = FIXTURE.history()
        metadata = {}
        if pruning:
            metadata = {
                'idle_tool_output_pruning': True,
                'assistant_message_id': 'current',
                '_idle_tool_pruning_signature': 'existing-branch-signature',
                '_idle_tool_pruning_masks': {},
            }
        errors = Mock()
        emitted = AsyncMock()
        scope.update(
            form_data={'messages': [system]},
            messages=[system],
            output=output,
            full_output=lambda: output,
            responses_api=responses_api,
            response_usage={},
            model={},
            metadata=metadata,
            new_form_data={},
            user=None,
            model_id='m',
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={}))),
            log=SimpleNamespace(exception=errors),
            output_id=lambda prefix: 'cc-id',
            emit_output=emitted,
            compact_messages_for_request=AsyncMock(
                side_effect=lambda req, user, messages, *args: (messages, 'Retained summary', True)
            ),
            get_reasoning_format=lambda model: None,
            get_last_user_message_item=lambda messages: None,
        )
        tree = ast.parse(FIXTURE.MIDDLEWARE.read_text())
        branch = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == 'ENABLE_RESPONSES_API_STATEFUL and last_response_id'
        )
        run = ast.AsyncFunctionDef(
            name='run',
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Global(names=['messages', 'new_form_data'])] + copy.deepcopy(branch.orelse),
            decorator_list=[],
        )
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[run], type_ignores=[])), str(FIXTURE.MIDDLEWARE), 'exec'
            ),
            scope,
        )
        with patch.dict(sys.modules, {'open_webui.utils.idle_tool_pruning': IDLE}):
            asyncio.run(scope['run']())
        errors.assert_not_called()
        emitted.assert_awaited_once()
        marker = output[-1]
        self.assertEqual(marker['type'], 'open_webui:context_compaction')
        self.assertEqual(marker['call_id'], 'call_real')
        self.assertEqual(marker['summary'], 'Retained summary')
        self.assertEqual(marker['pruning'], metadata.get('_idle_tool_pruning_signature'))
        self.assertEqual(system, original_system)
        request_messages = scope['new_form_data']['messages']
        systems = [message for message in request_messages if message['role'] == 'system']
        self.assertEqual(len(systems), 1)
        for text in ('BASE INSTRUCTIONS', 'INTELLIGENT UI INSTRUCTIONS', 'Retained summary'):
            self.assertIn(text, systems[0]['content'])
        if responses_api:
            payload = scope['convert_to_responses_payload'](copy.deepcopy(scope['new_form_data']))
            self.assertEqual(payload['instructions'], systems[0]['content'])
            self.assertNotIn('open_webui:context_compaction', str(payload))
            self.assertEqual(payload['input'][0]['encrypted_content'], 'opaque')

        if pruning:
            replayed = IDLE.mask_current_output(output, metadata)
            self.assertEqual(replayed[-1], marker)
            metadata['_idle_tool_pruning_signature'] = 'changed-branch-signature'
            self.assertNotIn(marker, IDLE.mask_current_output(output, metadata))

    def test_responses_marker_and_base_instructions_survive_text_compaction(self):
        self.exercise(True)

    def test_chat_marker_and_base_instructions_still_survive_text_compaction(self):
        self.exercise(False)

    def test_responses_text_marker_binding_survives_masked_tool_loop_and_continue(self):
        self.exercise(True, pruning=True)

    def test_chat_text_marker_binding_survives_masked_tool_loop_and_continue(self):
        self.exercise(False, pruning=True)


if __name__ == '__main__':
    unittest.main()
