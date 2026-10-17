"""Execute the real history/router/tool-loop seams, without app startup or a provider."""

import ast
import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
MIDDLEWARE = ROOT / 'backend/open_webui/utils/middleware.py'
MISC = ROOT / 'backend/open_webui/utils/misc.py'
ROUTER = ROOT / 'backend/open_webui/routers/openai.py'


def load_function(path, name, scope):
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope[name]


def history():
    return [
        {'type': 'reasoning', 'id': 'rs_real', 'summary': [], 'encrypted_content': 'opaque', 'started_at': 1},
        {
            'type': 'message',
            'id': 'msg_comment',
            'role': 'assistant',
            'phase': 'commentary',
            'content': [{'type': 'output_text', 'text': 'Checking.', 'annotations': [], 'local': True}],
            'status': 'completed',
            'duration': 1,
        },
        {
            'type': 'function_call',
            'id': 'fc_real',
            'call_id': 'call_real',
            'name': 'time',
            'arguments': '{}',
            'status': 'completed',
            'tool_type': 'builtin',
        },
        {
            'type': 'function_call_output',
            'call_id': 'call_real',
            'output': [{'type': 'input_text', 'text': '{"timestamp": 42}'}],
            'id': 'webui-result',
            'status': 'completed',
        },
        {
            'type': 'message',
            'id': 'msg_final',
            'role': 'assistant',
            'phase': 'final_answer',
            'content': [{'type': 'output_text', 'text': '42'}],
            'status': 'completed',
        },
    ]


def scope():
    result = {'copy': copy, 'Optional': Optional, 'JSONCodec': SimpleNamespace(dumps=json.dumps)}
    for name in (
        'get_provider_function',
        'get_paired_tool_call_ids',
        'reconcile_tool_pairs',
        'convert_output_to_messages',
        'strip_empty_content_blocks',
        'merge_system_messages',
        'get_output_text',
    ):
        load_function(MISC, name, result)
    for name in ('process_messages_with_output', 'sanitize_tool_pairs', 'normalize_messages_for_model'):
        load_function(MIDDLEWARE, name, result)
    tree = ast.parse(ROUTER.read_text())
    assignment = next(
        n
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == 'RESPONSES_ALLOWED_FIELDS'
    )
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), str(ROUTER), 'exec'), result)
    for name in ('_normalize_stored_item', 'convert_to_responses_payload'):
        load_function(ROUTER, name, result)
    return result


class NativeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.s = scope()

    def replay(self, output):
        messages = self.s['process_messages_with_output'](
            [
                {
                    'id': 'db-assistant',
                    'role': 'assistant',
                    'content': 'duplicate rendered text',
                    'model': 'model',
                    'output': output,
                }
            ],
            responses_api=True,
        )
        messages = self.s['sanitize_tool_pairs'](messages)
        body = self.s['normalize_messages_for_model']({'messages': messages})
        return self.s['convert_to_responses_payload'](body)['input']

    def test_router_stored_item_keeps_phase(self):
        items = self.s['convert_to_responses_payload'](
            {'messages': [{'role': 'assistant', 'output': history()}]},
        )['input']
        self.assertEqual(items[1].get('phase'), 'commentary')

    def test_replay_restores_provider_tool_search_call_and_keeps_native_id(self):
        output = history()
        output[2]['provider_function'] = {'name': 'tool_search', 'arguments': {'query': 'time'}}
        items = self.replay(output)
        call = next(item for item in items if item['type'] == 'function_call')
        self.assertEqual(call['name'], 'tool_search')
        self.assertEqual(json.loads(call['arguments']), {'query': 'time'})
        self.assertEqual(call['id'], 'fc_real')
        self.assertNotIn('provider_function', call)

    def test_saved_output_preserves_native_items_order_phase_ids_and_encrypted(self):
        original = history()
        frozen = copy.deepcopy(original)
        items = self.replay(original)
        self.assertEqual([i['type'] for i in items], [i['type'] for i in original])
        self.assertEqual(items[0]['encrypted_content'], 'opaque')
        self.assertEqual(items[0]['id'], 'rs_real')
        self.assertEqual(items[1]['phase'], 'commentary')
        self.assertEqual(items[-1]['phase'], 'final_answer')
        self.assertEqual(items[2]['id'], 'fc_real')
        self.assertEqual(items[2]['call_id'], items[3]['call_id'])
        self.assertNotIn('duplicate rendered text', json.dumps(items))
        self.assertEqual(original, frozen)

    def test_local_bookkeeping_removed_without_dropping_protocol_fields(self):
        items = self.replay(history())
        rendered = json.dumps(items)
        for key in ('started_at', 'duration', 'tool_type', 'webui-result', 'local'):
            self.assertNotIn(key, rendered)
        self.assertEqual(items[1]['content'][0]['annotations'], [])

    def test_local_extension_not_forwarded_as_responses_item(self):
        items = self.replay(history() + [{'type': 'open_webui:status', 'content': 'local status'}])
        self.assertNotIn('open_webui:', json.dumps(items))

    def test_chat_converter_remains_chat_and_retains_provider_reasoning_details(self):
        output = history()
        output[0]['reasoning_details'] = [{'format': 'bifrost', 'data': 'opaque-provider'}]
        result = self.s['process_messages_with_output'](
            [{'role': 'assistant', 'output': output}],
            responses_api=False,
        )
        self.assertTrue(any(m.get('tool_calls') for m in result))
        self.assertTrue(any(m.get('role') == 'tool' for m in result))
        self.assertTrue(any(m.get('reasoning_details') for m in result))
        self.assertNotIn('encrypted_content', json.dumps(result))
        self.assertNotIn('phase', json.dumps(result))

    def test_next_user_turn_and_authorized_pdf_stay_in_history(self):
        file_part = {
            'type': 'file',
            'file': {'filename': 'fixture.pdf', 'file_data': 'data:application/pdf;base64,eA=='},
        }
        messages = [
            {'role': 'user', 'content': [{'type': 'text', 'text': 'secret fixture context'}, file_part]},
            {'role': 'assistant', 'output': history()},
            {'role': 'user', 'content': 'Recall and use the tool'},
        ]
        cleaned = self.s['process_messages_with_output'](messages, responses_api=True)
        items = self.s['convert_to_responses_payload']({'messages': cleaned})['input']
        self.assertEqual(items[0]['content'][1]['type'], 'input_file')
        self.assertEqual(items[-1]['content'][0]['text'], 'Recall and use the tool')
        self.assertEqual(sum(i['type'] == 'function_call_output' for i in items), 1)

    def test_native_reasoning_fields_absent_upstream_are_not_invented(self):
        output = [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'OK'}]}]
        items = self.replay(output)
        self.assertNotIn('phase', items[0])
        self.assertNotIn('encrypted_content', json.dumps(items))

    def test_actual_saved_chat_loader_then_replay_keeps_full_output(self):
        messages = [
            {'id': 'u1', 'role': 'user', 'content': 'original context'},
            {'id': 'a1', 'role': 'assistant', 'content': '42', 'model': 'model', 'output': history()},
            {'id': 'u2', 'role': 'user', 'content': 'next question'},
        ]
        tree = ast.parse(MIDDLEWARE.read_text())
        assignment = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'MESSAGE_REPLAY_KEYS' for t in n.targets)
        )
        exec(compile(ast.Module(body=[assignment], type_ignores=[]), str(MIDDLEWARE), 'exec'), self.s)
        self.s.update(
            {
                'Chats': SimpleNamespace(get_messages_map_by_chat_id=AsyncMock(return_value={'fixture': True})),
                'get_message_list': lambda mapping, message_id: messages,
                'get_content_from_message': lambda msg: msg.get('content', ''),
            }
        )
        load = load_function(MIDDLEWARE, 'load_messages_from_db', self.s)
        saved = asyncio.run(load('chat', 'u2'))
        cleaned = self.s['process_messages_with_output'](saved, responses_api=True)
        items = self.s['convert_to_responses_payload']({'messages': cleaned})['input']
        self.assertEqual(items[1]['encrypted_content'], 'opaque')
        self.assertEqual(items[2]['phase'], 'commentary')
        self.assertEqual(items[-1]['content'][0]['text'], 'next question')

    def test_saved_payload_calls_pdf_authorization_before_response_reconstruction(self):
        tree = ast.parse(MIDDLEWARE.read_text())
        payload = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'process_chat_payload')
        calls = [n for n in ast.walk(payload) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        pdf = next(n for n in calls if n.func.id == 'prepare_native_pdf_attachments')
        cleanup = next(n for n in calls if n.func.id == 'process_messages_with_output')
        self.assertLess(pdf.lineno, cleanup.lineno)
        self.assertIn('responses_api', [keyword.arg for keyword in cleanup.keywords])
        self.assertIn('await uses_responses_api(request, model)', ast.unparse(cleanup))
        # Actual authorization denial/owner cases are covered by test_native_pdf.py;
        # this locks their position before the new native history branch.

    def test_connection_type_not_owner_selects_native_and_resolves_custom_base_model(self):
        target = load_function(MIDDLEWARE, 'uses_responses_api', self.s)
        get_connection = AsyncMock(
            side_effect=lambda idx: ('private', 'secret', {'api_type': 'responses' if idx == 9 else 'chat'})
        )
        module = SimpleNamespace(get_openai_connection=get_connection)
        request = SimpleNamespace(
            state=SimpleNamespace(direct=False),
            app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={'native': {'urlIdx': 9}, 'chat': {'urlIdx': 1}})),
        )
        with patch.dict(sys.modules, {'open_webui.routers.openai': module}):
            self.assertTrue(
                asyncio.run(
                    target(request, {'id': 'custom', 'owned_by': 'openai', 'info': {'base_model_id': 'native'}})
                )
            )
            self.assertFalse(asyncio.run(target(request, {'id': 'chat', 'owned_by': 'openai'})))
            self.assertFalse(asyncio.run(target(request, {'id': 'unknown', 'owned_by': 'openai'})))
            self.assertFalse(asyncio.run(target(request, {'id': 'native', 'pipe': {'id': 'pipe'}})))
            self.assertFalse(asyncio.run(target(request, {'id': 'native', 'owned_by': 'ollama'})))
            request.state.direct = True
            self.assertFalse(asyncio.run(target(request, {'id': 'native'})))
        self.assertEqual(get_connection.await_count, 2)

    def test_multiple_tool_iterations_replace_cumulative_output_once(self):
        output = history()
        get_messages = load_function(MIDDLEWARE, 'get_tool_loop_output_messages', self.s)
        messages = [{'role': 'user', 'content': 'original context'}, *get_messages(output, responses_api=True)]
        output += [
            {'type': 'function_call', 'id': 'fc_next', 'call_id': 'call_next', 'name': 'time', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'call_next', 'output': '43'},
        ]
        # Execute the actual stateless continuation branch from the stream handler.
        tree = ast.parse(MIDDLEWARE.read_text())
        branch = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.If) and ast.unparse(n.test) == 'ENABLE_RESPONSES_API_STATEFUL and last_response_id'
        )
        self.s.update(
            {
                'form_data': {'messages': [{'role': 'user', 'content': 'original context'}]},
                'messages': messages,
                'output': output,
                'full_output': lambda: output,
                'responses_api': True,
                'response_usage': {},
                'model': {},
                'metadata': {},
                'new_form_data': {},
                'request': SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={}))),
                'user': None,
                'model_id': 'm',
                'log': SimpleNamespace(exception=lambda *a: None),
                'compact_messages_for_request': AsyncMock(side_effect=lambda req, user, msgs, *a: (msgs, None, False)),
                'get_reasoning_format': lambda model: None,
                'get_last_user_message_item': lambda msgs: msgs[0],
            }
        )
        async_node = ast.AsyncFunctionDef(
            name='run',
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=branch.orelse + [ast.Return(value=ast.Name(id='new_form_data', ctx=ast.Load()))],
            decorator_list=[],
        )
        # Branch assignments are local, so bind fixture state as defaults through globals.
        async_node.body.insert(0, ast.Global(names=['messages', 'new_form_data']))
        exec(
            compile(ast.fix_missing_locations(ast.Module(body=[async_node], type_ignores=[])), str(MIDDLEWARE), 'exec'),
            self.s,
        )
        result = asyncio.run(self.s['run']())
        items = self.s['convert_to_responses_payload'](result)['input']
        self.assertEqual([i['call_id'] for i in items if i['type'] == 'function_call'], ['call_real', 'call_next'])
        self.assertEqual(
            [i['call_id'] for i in items if i['type'] == 'function_call_output'], ['call_real', 'call_next']
        )
        self.assertNotIn('_responses_tool_turn', json.dumps(items))
        self.assertEqual(items[0]['content'][0]['text'], 'original context')
        self.assertEqual(items[1]['encrypted_content'], 'opaque')


if __name__ == '__main__':
    unittest.main()
