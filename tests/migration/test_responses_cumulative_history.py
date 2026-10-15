"""Exercise actual stream prefix/reset and stateless continuation, not a cumulative fixture."""

import ast
import asyncio
import copy
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'native_fixture', ROOT / 'tests/migration/test_responses_native_history.py'
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def batch(label):
    result = FIXTURE.history()
    for item in result:
        if 'id' in item:
            item['id'] += label
        if 'call_id' in item:
            item['call_id'] += label
        if 'encrypted_content' in item:
            item['encrypted_content'] += label
    return result[:-1]


class CumulativeHistoryTests(unittest.TestCase):
    def exercise(self, responses_api):
        s = FIXTURE.scope()
        FIXTURE.load_function(FIXTURE.MIDDLEWARE, 'get_tool_loop_output_messages', s)
        tree = ast.parse(FIXTURE.MIDDLEWARE.read_text())
        full = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'full_output')
        branch = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.If) and ast.unparse(n.test) == 'ENABLE_RESPONSES_API_STATEFUL and last_response_id'
        )
        reset = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.If)
            and ast.unparse(n.test) == 'isinstance(res, StreamingResponse)'
            and any(
                isinstance(x, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'prior_output' for t in x.targets)
                for x in n.body
            )
        )
        s.update(
            output=batch('A'),
            prior_output=[],
            continuing=False,
            messages=[{'role': 'user', 'content': 'original context'}],
            form_data={'messages': [{'role': 'user', 'content': 'original context'}]},
            responses_api=responses_api,
            model={},
            metadata={},
            new_form_data={},
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={}))),
            user=None,
            model_id='m',
            log=SimpleNamespace(exception=lambda *a: None),
            compact_messages_for_request=AsyncMock(side_effect=lambda req, user, msgs, *a: (msgs, None, False)),
            get_reasoning_format=lambda model: None,
            get_last_user_message_item=lambda msgs: msgs[0],
        )
        exec(compile(ast.Module(body=[full], type_ignores=[]), str(FIXTURE.MIDDLEWARE), 'exec'), s)
        run = ast.AsyncFunctionDef(
            name='run',
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Global(names=['messages', 'new_form_data'])]
            + copy.deepcopy(branch.orelse)
            + [ast.Return(value=ast.Name(id='new_form_data', ctx=ast.Load()))],
            decorator_list=[],
        )
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[run], type_ignores=[])), str(FIXTURE.MIDDLEWARE), 'exec'
            ),
            s,
        )
        first = copy.deepcopy(asyncio.run(s['run']()))
        # Real streaming-response lifecycle: prior_output <- full_output; output <- [].
        reset_statements = reset.body[:-1]  # do not invoke the actual network stream handler
        exec(compile(ast.Module(body=reset_statements, type_ignores=[]), str(FIXTURE.MIDDLEWARE), 'exec'), s)
        self.assertEqual(s['output'], [])
        self.assertEqual(s['prior_output'], batch('A'))
        s['output'] = batch('B')  # new provider output_index is zero-relative
        second = copy.deepcopy(asyncio.run(s['run']()))
        exec(compile(ast.Module(body=reset_statements, type_ignores=[]), str(FIXTURE.MIDDLEWARE), 'exec'), s)
        s['output'] = batch('C')
        third = copy.deepcopy(asyncio.run(s['run']()))
        return s, first, second, third

    def test_responses_retains_prior_native_items_after_real_reset(self):
        s, first, second, third = self.exercise(True)
        for body, labels in ((first, 'A'), (second, 'AB'), (third, 'ABC')):
            self.assertEqual(sum(bool(m.get('output')) for m in body['messages']), 1)
            items = s['convert_to_responses_payload'](body)['input']
            self.assertEqual(
                [i['call_id'] for i in items if i.get('type') == 'function_call'], ['call_real' + x for x in labels]
            )
            self.assertEqual(
                [i['call_id'] for i in items if i.get('type') == 'function_call_output'],
                ['call_real' + x for x in labels],
            )
            self.assertEqual(
                [i['encrypted_content'] for i in items if i.get('type') == 'reasoning'], ['opaque' + x for x in labels]
            )
            self.assertEqual(
                [i['phase'] for i in items if i.get('type') == 'message' and 'phase' in i], ['commentary'] * len(labels)
            )

    def test_chat_retains_current_batch_conversion_and_appends_once(self):
        s, first, second, third = self.exercise(False)
        for body, count in ((first, 1), (second, 2), (third, 3)):
            self.assertEqual(sum(m.get('role') == 'tool' for m in body['messages']), count)
            self.assertFalse(any(m.get('_responses_tool_turn') for m in body['messages']))
            self.assertNotIn('encrypted_content', repr(body))


if __name__ == '__main__':
    unittest.main()
