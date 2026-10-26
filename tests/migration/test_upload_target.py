"""Production upload target validation; no Live requests or document parsing."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

SOURCE = Path(__file__).resolve().parents[2] / 'backend/open_webui/routers/files.py'


class UploadTargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.knowledge = SimpleNamespace(user_id='owner')
        self.knowledges = SimpleNamespace(
            get_knowledge_by_id=AsyncMock(return_value=self.knowledge),
            get_directory_by_id=AsyncMock(return_value=SimpleNamespace(knowledge_id='kb')),
        )
        self.grants = SimpleNamespace(has_access=AsyncMock(return_value=False))
        scope = dict(Knowledges=self.knowledges, AccessGrants=self.grants, HTTPException=HTTPException)
        node = next(
            n
            for n in ast.parse(SOURCE.read_text()).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == 'validate_knowledge_upload_target'
        )
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), scope)
        self.validate = scope['validate_knowledge_upload_target']
        self.user = SimpleNamespace(id='owner', role='user')

    async def test_deleted_knowledge_has_explicit_error_even_for_admin(self):
        self.knowledges.get_knowledge_by_id.return_value = None
        with self.assertRaises(HTTPException) as caught:
            await self.validate({'knowledge_id': 'kb'}, SimpleNamespace(id='admin', role='admin'))
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn('知识库已删除', caught.exception.detail)

    async def test_read_only_user_cannot_receive_into_someone_elses_knowledge(self):
        with self.assertRaises(HTTPException) as caught:
            await self.validate({'knowledge_id': 'kb'}, SimpleNamespace(id='reader', role='user'))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn('写入权限', caught.exception.detail)

    async def test_foreign_directory_is_rejected(self):
        self.knowledges.get_directory_by_id.return_value = SimpleNamespace(knowledge_id='another-kb')
        with self.assertRaises(HTTPException) as caught:
            await self.validate({'knowledge_id': 'kb', 'directory_id': 'foreign'}, self.user)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn('文件夹', caught.exception.detail)

    async def test_owner_and_granted_writer_are_accepted(self):
        await self.validate({'knowledge_id': 'kb', 'directory_id': 'dir'}, self.user)
        self.grants.has_access.return_value = True
        await self.validate({'knowledge_id': 'kb'}, SimpleNamespace(id='writer', role='user'))

    async def test_chat_upload_does_not_require_knowledge(self):
        await self.validate({}, self.user)
        self.knowledges.get_knowledge_by_id.assert_not_awaited()

    async def test_directory_sync_does_not_repeat_old_durable_receipts_or_skip_failures(self):
        source = SOURCE.with_name('knowledge.py')
        node = next(
            n
            for n in ast.walk(ast.parse(source.read_text()))
            if isinstance(n, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'pending_files' for target in n.targets)
        )

        function = ast.parse('async def receipts():\n pass').body[0]
        function.body = [node, ast.Return(value=ast.Name(id='pending_files', ctx=ast.Load()))]
        meta = SimpleNamespace(data={}, model_dump=lambda: {'file_hash': 'hash'})
        rows = [
            SimpleNamespace(
                filename='durable.txt', created_at=1, meta=meta, data={'status': 'pending', 'ingestion_job_id': 'job'}
            ),
            SimpleNamespace(
                filename='failed.txt',
                created_at=99999,
                meta=meta,
                data={'status': 'failed', 'ingestion_job_id': 'failed'},
            ),
            SimpleNamespace(filename='old-legacy.txt', created_at=1, meta=meta, data={'status': 'pending'}),
        ]
        scope = dict(
            Files=SimpleNamespace(get_pending_files_for_knowledge=AsyncMock(return_value=rows)),
            id='kb',
            db=None,
            directory_path_by_id={},
            time=SimpleNamespace(time=lambda: 100000),
        )
        tree = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        exec(compile(tree, str(source), 'exec'), scope)
        self.assertEqual(await scope['receipts'](), {('', 'durable.txt', 'hash')})


if __name__ == '__main__':
    unittest.main()
