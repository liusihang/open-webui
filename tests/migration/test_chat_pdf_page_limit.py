"""Exercise real upload admission before storage/DB, with local synthetic PDFs."""

import ast
import asyncio
import errno
import hashlib
import importlib.util
import io
import json
import logging
import os
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, Mock, patch
from contextlib import asynccontextmanager

from fastapi import Depends, File, Form, HTTPException, Query, UploadFile, status
from open_webui.utils import native_pdf
from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[2]
PDF_MODEL = {'info': {'meta': {'capabilities': {'native_pdf': True}}}}
TEXT_MODEL = {'info': {'meta': {'capabilities': {'native_pdf': False}}}}


def pdf_bytes(pages, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if password is not None:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class ChatPdfUploadLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        disk_budget = patch.dict(os.environ, {'INDEXING_UPLOAD_STORAGE_MIN_FREE_BYTES': '0'})
        disk_budget.start()
        self.addCleanup(disk_budget.stop)
        self.stored = Mock(side_effect=lambda stream, *_, **__: (None, '/synthetic/paper.pdf'))
        self.insert = AsyncMock(
            side_effect=lambda _user, form, **_: SimpleNamespace(**vars(form), model_dump=lambda: vars(form))
        )
        self.process = AsyncMock()
        self.queued = AsyncMock(return_value={'ingestion_job_id': 'fixture-job'})
        queue_patch = patch('open_webui.retrieval.ingestion.submit_file', self.queued)
        queue_patch.start()
        self.addCleanup(queue_patch.stop)

        @asynccontextmanager
        async def session(_):
            yield None

        scope = {
            'Request': object,
            'UploadFile': UploadFile,
            'File': File,
            'Form': Form,
            'Query': Query,
            'Depends': Depends,
            'get_verified_user': lambda: None,
            'Optional': Optional,
            'BackgroundTasks': object,
            'AsyncSession': object,
            'asyncio': asyncio,
            'log': logging.getLogger(__name__),
            'os': os,
            'errno': errno,
            'uuid': uuid,
            'hashlib': hashlib,
            'HTTPException': HTTPException,
            'status': status,
            'ERROR_MESSAGES': SimpleNamespace(DEFAULT=lambda message: message),
            'Config': SimpleNamespace(get=AsyncMock(return_value=None)),
            'Storage': SimpleNamespace(upload_file=self.stored),
            'Files': SimpleNamespace(insert_new_file=self.insert),
            'FileForm': SimpleNamespace,
            'Channels': SimpleNamespace(get_channel_by_id_and_user_id=AsyncMock(return_value=None)),
            'process_uploaded_file': self.process,
            'get_async_db_context': session,
            'ProcessFileForm': SimpleNamespace,
            'strict_match_mime_type': lambda *_: False,
            **vars(native_pdf),
        }
        tree = ast.parse((ROOT / 'backend/open_webui/routers/files.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'upload_file_handler')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'routers/files.py', 'exec'), scope)
        self.upload = scope['upload_file_handler']

    async def call(self, contents, metadata=None, filename='paper.pdf', content_type='application/pdf', process=True):
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(MODELS={'gpt': PDF_MODEL, 'text': TEXT_MODEL}))
        )
        file = UploadFile(file=io.BytesIO(contents), filename=filename, headers={'content-type': content_type})
        return await self.upload(
            request,
            file=file,
            metadata={'upload_context': 'chat', 'model_ids': ['gpt']} if metadata is None else metadata,
            process=process,
            process_in_background=False,
            background_tasks=None,
            user=SimpleNamespace(id='owner', email='owner@example.invalid', name='Owner'),
            db=None,
        )

    def assert_no_side_effects(self):
        self.stored.assert_not_called()
        self.insert.assert_not_awaited()
        self.process.assert_not_awaited()

    async def test_nineteen_and_twenty_pages_allowed(self):
        for pages in (19, 20):
            with self.subTest(pages=pages):
                await self.call(pdf_bytes(pages))
        self.assertEqual(self.stored.call_count, 2)
        self.assertEqual(self.insert.await_count, 2)
        self.process.assert_not_awaited()

    async def test_twenty_one_pages_rejected_before_storage_db_and_parser(self):
        with self.assertRaises(HTTPException) as error:
            await self.call(pdf_bytes(21))
        self.assertEqual(error.exception.status_code, 400)
        for text in ('21', '20', '知识库', '引用'):
            self.assertIn(text, error.exception.detail)
        self.assert_no_side_effects()

    async def test_client_claimed_page_count_does_not_override_pdf_bytes(self):
        with self.assertRaises(HTTPException):
            await self.call(pdf_bytes(21), {'upload_context': 'chat', 'page_count': 1, 'pdf_page_count': 1})
        self.assert_no_side_effects()

    async def test_non_gpt_and_mixed_models_use_same_upload_limit(self):
        for ids in (['text'], ['gpt', 'text'], []):
            with self.subTest(model_ids=ids), self.assertRaises(HTTPException):
                await self.call(pdf_bytes(21), {'upload_context': 'chat', 'model_ids': ids})
        self.assert_no_side_effects()

    async def test_process_false_cannot_bypass_admission(self):
        with self.assertRaises(HTTPException):
            await self.call(pdf_bytes(21), process=False)
        self.assert_no_side_effects()

    async def test_real_pdf_with_disguised_name_and_mime_is_rejected(self):
        with self.assertRaises(HTTPException):
            await self.call(pdf_bytes(21), filename='notes.txt', content_type='text/plain')
        self.assert_no_side_effects()

    async def test_non_gpt_small_pdf_keeps_parser_path(self):
        await self.call(pdf_bytes(20), {'upload_context': 'chat', 'model_ids': ['text']})
        self.queued.assert_awaited_once()
        self.process.assert_not_awaited()
        self.insert.assert_awaited_once()

    async def test_knowledge_and_channel_uploads_are_exempt(self):
        for metadata in (
            {'knowledge_id': 'knowledge'},
            {'upload_context': 'knowledge'},
            {'channel_id': 'channel'},
            {'upload_context': 'chat', 'knowledge_id': 'knowledge'},
            {'upload_context': 'chat', 'channel_id': 'channel'},
            {},
        ):
            with self.subTest(metadata=metadata):
                await self.call(pdf_bytes(21), metadata)
        self.assertEqual(self.stored.call_count, 6)
        self.assertEqual(self.insert.await_count, 6)
        self.assertEqual(self.queued.await_count, 6)
        self.process.assert_not_awaited()

    async def test_damaged_or_disguised_non_pdf_is_explained_not_500(self):
        for contents in (b'%PDF-1.7\ninvalid', b'not a PDF'):
            with self.subTest(contents=contents), self.assertRaises(HTTPException) as error:
                await self.call(contents)
            self.assertEqual(error.exception.status_code, 400)
            self.assertIn('页数', error.exception.detail)
        self.assert_no_side_effects()

    async def test_encrypted_pdf_is_rejected_without_decryption(self):
        for password in ('secret', ''):
            contents = pdf_bytes(1, password)
            with self.subTest(password=password):
                with patch('pypdf._encryption.Encryption.verify') as decrypt:
                    with self.assertRaises(HTTPException) as error:
                        await self.call(contents)
                    decrypt.assert_not_called()
                self.assertEqual(error.exception.status_code, 400)
                self.assertIn('加密', error.exception.detail)
        self.assert_no_side_effects()

    async def test_empty_pdf_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            await self.call(pdf_bytes(0))
        self.assertEqual(error.exception.status_code, 400)
        self.assert_no_side_effects()

    async def test_non_pdf_chat_file_is_not_read_as_pdf(self):
        await self.call(b'plain text', filename='notes.txt', content_type='text/plain')
        self.stored.assert_called_once()
        self.queued.assert_awaited_once()

    async def test_validation_rewinds_stream_before_storage(self):
        contents = pdf_bytes(20)
        await self.call(contents)
        self.assertEqual(self.insert.await_args.args[1].meta['size'], len(contents))

    def test_old_history_not_rechecked_but_explicit_reattachment_is_new(self):
        old = {'role': 'user', 'files': [{'id': 'old-pdf'}]}
        current = {'role': 'user', 'files': [{'id': 'new-pdf'}]}
        selected = native_pdf.chat_pdf_admission_ids([{'id': 'old-pdf'}, {'id': 'new-pdf'}], current, [old])
        self.assertEqual(selected, {'new-pdf'})
        current['files'].append({'id': 'old-pdf'})
        self.assertEqual(native_pdf.chat_pdf_admission_ids([], current, [old]), {'new-pdf', 'old-pdf'})

    def test_unknown_file_ids_and_new_chats_are_checked(self):
        self.assertEqual(native_pdf.chat_pdf_admission_ids([{'id': 'old-pdf'}], None, []), {'old-pdf'})

    def test_knowledge_references_are_not_direct_uploads(self):
        items = [{'id': 'kb', 'type': 'collection'}, {'id': 'pdf', 'source': 'knowledge'}]
        self.assertEqual(native_pdf.chat_pdf_admission_ids(items, {'files': items}, []), set())

    def test_current_attachment_without_top_level_file_list_is_still_checked(self):
        self.assertEqual(native_pdf.chat_pdf_admission_ids([], {'files': [{'id': 'pdf'}]}, []), {'pdf'})


class ChatPdfMiddlewareAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            'pdf_prompt_fixture', ROOT / 'tests/migration/test_system_prompt_order.py'
        )
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        self.f = fixture.PromptOrderTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.s.update(
            json=json,
            is_raster_image_content_type=lambda mime: bool(mime and mime.startswith('image/')),
            MESSAGE_REPLAY_KEYS=(
                'id',
                'role',
                'content',
                'output',
                'files',
                'contextSummary',
                'usage',
                'model',
                'meta',
            ),
            pending_tool_calls=lambda output: [],
        )

    async def run_admission(self, stored, files, *, client_messages=None, metadata=None):
        self.f.db_messages = stored
        body = {
            'model': 'm',
            'messages': client_messages or [{'id': 'u1', 'role': 'user', 'content': 'Question'}],
            'files': files,
            'features': {},
            'params': {},
        }
        await self.f.s['process_chat_payload'](
            self.f.request,
            body,
            self.f.user,
            {'chat_id': 'saved', 'user_message_id': 'u1', 'session_id': 'session', 'params': {}, **(metadata or {})},
            self.f.model,
        )
        return self.f.s['prepare_native_pdf_attachments'].await_args.kwargs['new_attachment_ids']

    async def test_server_history_exemption_survives_compaction(self):
        current = {'id': 'u1', 'role': 'user', 'content': 'Follow up'}
        old = {'id': 'old', 'role': 'user', 'content': 'Old PDF', 'files': [{'id': 'old-pdf'}]}
        self.f.s['compact_messages_for_request'] = AsyncMock(return_value=([current], None, False))
        self.assertEqual(await self.run_admission([old, current], [{'id': 'old-pdf'}]), set())

    async def test_server_current_attachment_checked_even_if_previously_used(self):
        old = {'id': 'old', 'role': 'user', 'files': [{'id': 'old-pdf'}]}
        current = {'id': 'u1', 'role': 'user', 'content': 'Read again', 'files': [{'id': 'old-pdf'}]}
        self.assertEqual(await self.run_admission([old, current], []), {'old-pdf'})

    async def test_client_supplied_history_is_not_a_limit_exemption(self):
        client = [
            {'id': 'fake', 'role': 'user', 'files': [{'id': 'old-pdf'}]},
            {'id': 'u1', 'role': 'user', 'content': 'Question'},
        ]
        self.assertEqual(await self.run_admission([], [{'id': 'old-pdf'}], client_messages=client), {'old-pdf'})

    async def test_continue_and_approval_resume_keep_old_pdf(self):
        current = {'id': 'u1', 'role': 'user', 'content': 'Old PDF', 'files': [{'id': 'old-pdf'}]}
        self.f.s['Chats'].get_message_by_id_and_message_id = AsyncMock(
            return_value={'id': 'a1', 'parentId': 'u1', 'role': 'assistant', 'content': 'Existing answer'}
        )
        for resume in ({}, {'tool_approval_resume': 'model'}):
            with self.subTest(resume=resume):
                self.assertEqual(
                    await self.run_admission(
                        [current], [{'id': 'old-pdf'}], metadata={'assistant_message_id': 'a1', **resume}
                    ),
                    set(),
                )

    async def test_forged_continue_flag_without_server_response_does_not_exempt(self):
        current = {'id': 'u1', 'role': 'user', 'content': 'New PDF', 'files': [{'id': 'old-pdf'}]}
        self.f.s['Chats'].get_message_by_id_and_message_id = AsyncMock(return_value=None)
        self.assertEqual(
            await self.run_admission([current], [{'id': 'old-pdf'}], metadata={'assistant_message_id': 'fake'}),
            {'old-pdf'},
        )

    async def test_channel_conversation_has_no_chat_upload_limit(self):
        self.assertEqual(
            await self.run_admission([], [{'id': 'channel-pdf'}], metadata={'chat_id': 'channel:one'}), set()
        )

    async def test_request_without_chat_id_still_checks_new_files(self):
        self.assertEqual(
            await self.run_admission([], [{'id': 'new-pdf'}], metadata={'chat_id': None, 'user_message_id': None}),
            {'new-pdf'},
        )


if __name__ == '__main__':
    unittest.main()
