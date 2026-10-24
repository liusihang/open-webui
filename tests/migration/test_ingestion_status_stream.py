"""Production status route, without DB/bootstrap; assert valid SSE and no body read."""

import ast
import asyncio
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import AsyncMock, patch

SOURCE = Path(__file__).resolve().parents[2] / 'backend/open_webui/routers/files.py'


class StatusStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.expire = AsyncMock(return_value=0)
        module = ModuleType('open_webui.retrieval.ingestion_queue')
        module.expire_jobs = self.expire
        modules = patch.dict(sys.modules, {module.__name__: module})
        modules.start()
        self.addCleanup(modules.stop)
        self.files = SimpleNamespace(
            get_file_processing_state=AsyncMock(),
            get_file_by_id=AsyncMock(side_effect=AssertionError('must not fetch full body')),
        )
        self.scope = dict(
            Files=self.files,
            JSONCodec=json,
            Query=lambda value: value,
            Depends=lambda fn: None,
            get_verified_user=lambda: None,
            router=SimpleNamespace(get=lambda *_: lambda fn: fn),
            asyncio=SimpleNamespace(sleep=AsyncMock()),
            has_access_to_file=AsyncMock(return_value=False),
            HTTPException=RuntimeError,
            ERROR_MESSAGES=SimpleNamespace(NOT_FOUND='not found'),
            StreamingResponse=lambda iterator, **_: SimpleNamespace(body_iterator=iterator),
        )
        node = next(
            n
            for n in ast.parse(SOURCE.read_text()).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_file_process_status'
        )
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), self.scope)

    async def run_stream(self):
        result = await self.scope['get_file_process_status'](
            'file', stream=True, user=SimpleNamespace(id='owner', role='user')
        )
        return [chunk async for chunk in result.body_iterator]

    async def test_completed_event_has_real_sse_delimiters(self):
        self.files.get_file_processing_state.return_value = {'id': 'file', 'user_id': 'owner', 'status': 'completed'}
        self.assertEqual(await self.run_stream(), ['data: {"status": "completed"}\n\n'])
        self.files.get_file_by_id.assert_not_awaited()

    async def test_pending_to_completed_never_selects_full_body(self):
        base = {'id': 'file', 'user_id': 'owner', 'ingestion_job_id': 'job'}
        self.files.get_file_processing_state.side_effect = [
            {**base, 'status': 'pending'},
            {**base, 'status': 'processing'},
            {**base, 'status': 'completed'},
        ]
        events = await self.run_stream()
        self.assertEqual([json.loads(chunk[6:])['status'] for chunk in events], ['processing', 'completed'])
        self.assertTrue(all(chunk.endswith('\n\n') for chunk in events))
        self.files.get_file_by_id.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
