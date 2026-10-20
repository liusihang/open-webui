"""Check nonempty errors at the shipped chat-task persistence/event boundary."""

import ast
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
MISC = ROOT / 'backend/open_webui/utils/misc.py'
MAIN = ROOT / 'backend/open_webui/main.py'


def load_error_detail():
    tree = ast.parse(MISC.read_text())
    node = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'get_exception_error_detail'
    )
    scope = {'JSONCodec': SimpleNamespace(dumps=json.dumps)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(MISC), 'exec'), scope)
    return scope['get_exception_error_detail']


class ErrorDetailTests(unittest.TestCase):
    def test_empty_timeout_and_other_empty_exceptions_have_useful_fallbacks(self):
        detail = load_error_detail()
        self.assertIn('timed out', detail(TimeoutError()))
        self.assertIn('RuntimeError', detail(RuntimeError()))
        self.assertIn('RuntimeError', detail(RuntimeError('   ')))

    def test_existing_messages_and_structured_http_details_are_preserved(self):
        detail = load_error_detail()
        self.assertEqual(detail(ValueError('Specific error')), 'Specific error')
        error = Exception()
        error.detail = 'Specific HTTP error'
        self.assertEqual(detail(error), 'Specific HTTP error')
        error.detail = {'message': 'Specific HTTP error'}
        self.assertEqual(json.loads(detail(error)), error.detail)


class ChatTaskErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_timeout_is_saved_and_emitted_with_same_nonempty_message(self):
        tree = ast.parse(MAIN.read_text())
        task = next(
            node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat'
        )
        emitter = AsyncMock()
        save = AsyncMock()
        log = SimpleNamespace(info=Mock(), error=Mock(), exception=Mock(), debug=Mock())
        scope = {
            'asyncio': asyncio,
            'copy': copy,
            'model_id': 'fixture-model',
            'model_info': None,
            'model_controls': {},
            'apply_model_controls': lambda params, *args: params,
            'get_exception_error_detail': load_error_detail(),
            'process_chat_payload': AsyncMock(side_effect=TimeoutError()),
            'is_saved_chat_id': lambda chat_id: chat_id == 'saved',
            'get_event_emitter': AsyncMock(return_value=emitter),
            'Chats': SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=save),
            'publish_chat_failed_event': AsyncMock(),
            'log': log,
        }
        exec(compile(ast.Module(body=[task], type_ignores=[]), str(MAIN), 'exec'), scope)
        with patch.dict(
            sys.modules,
            {'open_webui.utils.idle_tool_pruning': SimpleNamespace(finish_request=AsyncMock())},
        ):
            await scope['process_chat'](
                SimpleNamespace(state=SimpleNamespace(internal=True)),
                {'model': 'fixture-model'},
                SimpleNamespace(id='fixture-user'),
                {'chat_id': 'saved', 'session_id': 'browser', 'message_id': 'failed', 'user_message_id': 'user'},
                {'id': 'fixture-model'},
            )
        stored = save.await_args.args[2]
        self.assertTrue(stored['done'])
        self.assertIn('timed out', stored['error']['content'])
        self.assertEqual(emitter.await_args.args[0]['data']['error'], stored['error'])
        self.assertIn('TimeoutError', log.error.call_args.args)
        self.assertIn('saved', log.error.call_args.args)
        log.exception.assert_not_called()


if __name__ == '__main__':
    unittest.main()
