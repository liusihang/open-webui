"""Exercise real metadata-copy, router Response and middleware callback seams."""

import ast
import asyncio
import copy
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2] / 'backend/open_webui'


def tree(name):
    return ast.parse((ROOT / name).read_text())


def async_block(body, scope, name='run'):
    node = ast.parse('async def ' + name + '():\n pass').body[0]
    node.body = body
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<real-dispatch-seam>', 'exec'),
        scope,
    )
    return asyncio.run(scope[name]())


class DispatchBindingTests(unittest.TestCase):
    def test_actual_metadata_copy_and_each_stream_response_keep_their_own_stamp(self):
        metadata = {'chat_id': 'chat', 'message_id': 'assistant', 'user_message_id': 'user'}
        request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
        merge = next(
            n
            for n in ast.walk(tree('utils/chat.py'))
            if isinstance(n, ast.If) and ast.unparse(n.test) == "hasattr(request.state, 'metadata')"
        )
        upstream = []
        for i in range(2):
            form = {'metadata': metadata}
            scope = {'request': request, 'form_data': form}
            exec(compile(ast.Module(body=[merge], type_ignores=[]), '<actual-metadata-copy>', 'exec'), scope)
            routed = form['metadata']
            self.assertIsNot(routed, metadata)
            routed['_native_token_usage'] = {'request': i}
            branch = next(
                n
                for n in ast.walk(tree('routers/openai.py'))
                if isinstance(n, ast.If)
                and ast.unparse(n.test) == "'text/event-stream' in r.headers.get('Content-Type', '')"
            )
            start = next(
                j
                for j, n in enumerate(branch.body)
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'streaming' for t in n.targets)
            )
            response = async_block(
                branch.body[start:],
                {
                    'metadata': routed,
                    'is_responses': True,
                    'r': SimpleNamespace(status=200, headers={}),
                    'StreamingResponse': lambda *a, **k: SimpleNamespace(),
                    'stream_wrapper': lambda r: None,
                    '_clean_proxy_headers': lambda h: h,
                },
            )
            upstream.append((response, routed))
        module = types.ModuleType('open_webui.utils.native_compaction')
        module.save_native_token_usage = AsyncMock()
        callback = next(
            n
            for n in ast.walk(tree('utils/middleware.py'))
            if isinstance(n, ast.If) and ast.unparse(n.test) == "response_data_type == 'response.completed'"
        )
        for response, routed in upstream:
            with patch.dict(sys.modules, {'open_webui.utils.native_compaction': module}):
                async_block(
                    [callback],
                    {
                        'response_data_type': 'response.completed',
                        'data': {'response': {'model': 'model', 'usage': {'input_tokens': 10}}},
                        'user': 'owner',
                        'metadata': metadata,
                        'response': response,
                    },
                )
            self.assertIs(module.save_native_token_usage.await_args.args[1], routed)
        self.assertNotIn('_native_token_usage', metadata)
        self.assertIsNot(upstream[0][1], upstream[1][1])

    def test_cold_history_mode_uses_same_server_model_connection_as_warm_mode(self):
        node = next(
            n
            for n in tree('utils/middleware.py').body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == 'uses_responses_api'
        )
        connection = AsyncMock(return_value=('private', 'private', {'api_type': 'responses'}))
        module = SimpleNamespace(get_openai_connection=connection)
        scope = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual-route-helper>', 'exec'), scope)
        req = SimpleNamespace(
            state=SimpleNamespace(direct=False), app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={}))
        )
        model = {'id': 'gpt-6-luna', 'urlIdx': 8, 'owned_by': 'openai'}
        with patch.dict(sys.modules, {'open_webui.routers.openai': module}):
            self.assertTrue(asyncio.run(scope['uses_responses_api'](req, model)))
            req.app.state.OPENAI_MODELS = {'gpt-6-luna': {'urlIdx': 8}}
            self.assertTrue(asyncio.run(scope['uses_responses_api'](req, model)))
            self.assertFalse(
                asyncio.run(
                    scope['uses_responses_api'](
                        req, {'id': 'custom', 'urlIdx': 8, 'info': {'base_model_id': 'missing'}}
                    )
                )
            )
        self.assertEqual(connection.await_count, 2)


if __name__ == '__main__':
    unittest.main()
