"""Exercise real image normalization, Responses replay and both tool result loops offline."""

import ast
import base64
import copy
import logging
import mimetypes
import re
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, Mock

from test_responses_native_history import MIDDLEWARE, load_function, scope as history_scope


FILE_URL = '/api/v1/files/11111111-1111-4111-8111-111111111111/content'
DATA = 'data:image/png;base64,aGVsbG8='


def image_result(url=FILE_URL):
    return [
        {
            'type': 'function_call',
            'call_id': 'read-image',
            'name': 'read_file',
            'arguments': '{}',
            'status': 'completed',
        },
        {
            'type': 'function_call_output',
            'call_id': 'read-image',
            'output': [
                {'type': 'input_text', 'text': 'Image read successfully.'},
                {'type': 'input_image', 'image_url': url},
            ],
            'status': 'completed',
        },
    ]


class ToolImageReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = history_scope()
        self.user = SimpleNamespace(id='owner', role='user')
        self.resolver = AsyncMock(return_value=DATA)
        self.scope.update(get_image_base64_from_url=self.resolver, log=logging.getLogger(__name__))
        self.convert = load_function(MIDDLEWARE, 'convert_url_images_to_base64', self.scope)

    async def test_native_tool_continuation_and_history_replay_send_image_bytes(self):
        original = {'role': 'assistant', 'content': '', 'output': image_result()}
        saved = copy.deepcopy(original)
        for replay in (False, True):  # Fresh tool output and later replay of the same stored turn.
            messages = (
                self.scope['process_messages_with_output']([original], responses_api=True) if replay else [original]
            )
            body = {'messages': messages}
            await self.convert(body, user=self.user)
            payload = self.scope['convert_to_responses_payload'](body)
            self.assertEqual(payload['input'][1]['output'][1]['image_url'], DATA)
            self.assertEqual(original, saved, 'Outgoing normalization must not rewrite stored output')
        self.resolver.assert_awaited_with(FILE_URL, user=self.user)

    async def test_two_tool_images_and_normal_content_share_per_request_resolution(self):
        body = {
            'messages': [
                {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': FILE_URL, 'detail': 'low'}}]},
                {'role': 'tool', 'content': [{'type': 'input_image', 'image_url': FILE_URL, 'detail': 'high'}]},
                {'role': 'assistant', 'output': image_result() + image_result()},
            ]
        }
        await self.convert(body, user=self.user)
        self.assertEqual(self.resolver.await_count, 1)
        self.assertEqual(body['messages'][0]['content'][0]['image_url'], {'url': DATA, 'detail': 'low'})
        self.assertEqual(body['messages'][1]['content'][0]['detail'], 'high')
        for item in body['messages'][2]['output']:
            if item['type'] == 'function_call_output':
                self.assertEqual(item['output'][1]['image_url'], DATA)

    async def test_failed_image_is_explicit_text_not_a_broken_provider_url(self):
        for failure in (None, RuntimeError('storage unavailable')):
            self.resolver.return_value = failure
            self.resolver.side_effect = failure if isinstance(failure, Exception) else None
            body = {'messages': [{'role': 'assistant', 'output': image_result()}]}
            await self.convert(body, user=self.user)
            parts = self.scope['convert_to_responses_payload'](body)['input'][1]['output']
            self.assertEqual(parts[0]['text'], 'Image read successfully.')
            self.assertEqual(parts[1]['type'], 'input_text')
            self.assertIn('unavailable', parts[1]['text'].lower())
            self.assertNotIn('image_url', parts[1])

    async def test_failed_chat_completion_image_uses_chat_text_type(self):
        self.resolver.return_value = None
        body = {'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': FILE_URL}}]}]}
        await self.convert(body, user=self.user)
        self.assertEqual(body['messages'][0]['content'][0]['type'], 'text')

    async def test_inline_images_provider_file_ids_and_opaque_compaction_are_unchanged(self):
        output = image_result(DATA)
        output[1]['output'].append({'type': 'input_image', 'file_id': 'file-provider-id', 'detail': 'auto'})
        output.append({'type': 'compaction', 'encrypted_content': 'opaque-native-data'})
        original = {'role': 'assistant', 'output': output}
        body = {'messages': [original]}
        await self.convert(body, user=self.user)
        self.assertEqual(body['messages'], [original])
        self.resolver.assert_not_awaited()

    async def test_remote_image_still_uses_existing_authenticated_ssrf_checked_resolver(self):
        url = 'https://example.org/preview.png'
        body = {'messages': [{'role': 'assistant', 'output': image_result(url)}]}
        await self.convert(body, user=self.user)
        self.resolver.assert_awaited_once_with(url, user=self.user)
        self.assertEqual(body['messages'][0]['output'][1]['output'][1]['image_url'], DATA)

    async def test_chat_completion_reconstruction_keeps_images_and_normalizes_them(self):
        messages = self.scope['convert_output_to_messages'](image_result(), flatten_tool_images=True)
        body = {'messages': messages}
        await self.convert(body, user=self.user)
        image = next(
            part
            for message in body['messages']
            if isinstance(message.get('content'), list)
            for part in message['content']
            if part['type'] == 'image_url'
        )
        self.assertEqual(image['image_url']['url'], DATA)

    async def test_both_tool_loops_return_ui_reference_even_without_model_vision(self):
        tree = ast.parse(MIDDLEWARE.read_text())
        loops = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == 'file_item'
            and ast.unparse(node.iter) in ('files', "result.get('files', [])")
        ]
        self.assertEqual(len(loops), 2)
        for loop in loops:
            function = ast.AsyncFunctionDef(
                name='build_result',
                args=ast.arguments(
                    posonlyargs=[],
                    args=[ast.arg(arg=n) for n in ('model', 'files', 'result')],
                    kwonlyargs=[],
                    kw_defaults=[],
                    defaults=[],
                ),
                body=ast.parse(
                    "output_parts=[{'type':'input_text','text':'Image read successfully.'}]\ndisplay_files=[]"
                ).body
                + [copy.deepcopy(loop)]
                + ast.parse('return output_parts, display_files').body,
                decorator_list=[],
            )
            store = AsyncMock(return_value=FILE_URL)
            scope = {'store_tool_result_image': store, 'request': None, 'metadata': {}, 'user': self.user}
            exec(
                compile(
                    ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(MIDDLEWARE), 'exec'
                ),
                scope,
            )
            for vision in (True, False):
                model = {'info': {'meta': {'capabilities': {'vision': vision}}}}
                file = {'type': 'image', 'url': DATA}
                parts, _ = await scope['build_result'](model, [file], {'files': [file]})
                self.assertIn(FILE_URL, parts[0]['text'])
                self.assertEqual(sum(part['type'] == 'input_image' for part in parts), int(vision))


class StoredImagePermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_file_resolver_checks_owner_admin_and_explicit_read_grants_before_storage(self):
        import asyncio

        for user, grant, allowed in [
            (SimpleNamespace(id='owner', role='user'), False, True),
            (SimpleNamespace(id='admin', role='admin'), False, True),
            (SimpleNamespace(id='other', role='user'), True, True),
            (SimpleNamespace(id='other', role='user'), False, False),
            (None, False, False),
        ]:
            with self.subTest(user=user, grant=grant):
                file = SimpleNamespace(id='file', user_id='owner', path='/fixture/chart.png', meta={})
                storage = Mock(return_value=file.path)
                read = AsyncMock(return_value=b'fixture')

                @asynccontextmanager
                async def opened(*args):
                    yield SimpleNamespace(read=read)

                scope = {
                    'Optional': Optional,
                    'asyncio': asyncio,
                    'base64': base64,
                    'mimetypes': mimetypes,
                    'Files': SimpleNamespace(get_file_by_id=AsyncMock(return_value=file)),
                    'Storage': SimpleNamespace(get_file=storage),
                    'Path': lambda _: SimpleNamespace(is_file=lambda: True, name='chart.png'),
                    'aiofiles': SimpleNamespace(open=opened),
                    'has_access_to_file': AsyncMock(return_value=grant),
                    'ENABLE_IMAGE_CONTENT_TYPE_EXTENSION_FALLBACK': False,
                    'FILE_CONTENT_URL_PATTERN': re.compile(r'^/api/v1/files/([^/?#]+)/content(?:[?#]|$)'),
                }
                source = MIDDLEWARE.parent / 'files.py'
                load_function(source, 'get_image_base64_from_file_id', scope)
                resolve = load_function(source, 'get_image_base64_from_url', scope)
                result = await resolve(FILE_URL, user=user)
                self.assertEqual(bool(result), allowed)
                self.assertEqual(storage.called, allowed)
                self.assertEqual(bool(read.await_count), allowed)


if __name__ == '__main__':
    unittest.main()
