import asyncio
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tools.search import gpt_web_search as src


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tool = src.Tools()
        self.tool.valves.API_KEY = 'test-secret-not-real'

    def test_legacy_defaults(self):
        body = self.tool._request(' example ')
        self.assertEqual(body['commands'], {'search_query': [{'q': 'example'}]})
        self.assertEqual(body['input'], 'example')
        self.assertEqual(body['model'], 'gpt-6-luna')
        self.assertEqual(body['max_output_tokens'], 2000)
        self.assertEqual(
            body['settings'], {'search_context_size': 'low', 'allowed_callers': ['direct'], 'external_web_access': True}
        )
        self.assertEqual(self.tool.valves.TIMEOUT_SECONDS, 60)
        self.assertEqual(self.tool.valves.MAX_OUTPUT_CHARS, 16000)

    def test_advanced_mapping(self):
        body = self.tool._request(
            commands={
                'sports': [{'fn': 'standings', 'league': 'nba'}],
                'search_query': [{'q': 'test', 'domains': ['iana.org'], 'recency': 30}],
            },
            input='context',
            settings={
                'search_context_size': 'medium',
                'user_location': {'country': 'US'},
                'image_settings': {'caption': True},
            },
        )
        self.assertEqual(body['commands']['sports'][0]['tool'], 'sports')
        self.assertEqual(body['settings']['search_context_size'], 'medium')
        self.assertEqual(body['settings']['allowed_callers'], ['direct'])
        self.assertEqual(body['input'], 'context')

    def test_reject_management_and_unknown(self):
        for setting in [
            'model',
            'API_KEY',
            'endpoint',
            'allowed_callers',
            'external_web_access',
            'max_output_tokens',
            'encrypted_output',
            'requestId',
            'unknown',
        ]:
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                self.tool._request('test', settings={setting: 'x'})
        for command in ['unknown', 'model', 'reasoning', 'id']:
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.tool._request(commands={command: []})
        with self.assertRaises(TypeError):
            asyncio.run(self.tool.search_web(query='test', model='override'))

    def test_budget(self):
        with self.assertRaises(ValueError):
            self.tool._request(
                commands={'time': [{'utc_offset': '+08:00'}] * 6, 'open': [{'ref_id': 'https://example.com'}]}
            )
        with self.assertRaises(ValueError):
            self.tool._request(commands={'search_query': [{'q': 'x'}] * 5})
        with self.assertRaises(ValueError):
            self.tool._request(commands={'image_query': [{'q': 'x'}] * 3})

    def test_empty_and_conflict(self):
        for kwargs in [
            {},
            {'commands': {}},
            {'commands': {'response_length': 'short'}},
            {'query': ''},
            {'query': 'x' * 2001},
            {'query': 'x', 'commands': {'time': []}},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.tool._request(**kwargs)

    def test_input_string_only(self):
        for context in [[], {}, 1, 'x' * 4001]:
            with self.subTest(type=type(context)), self.assertRaises(ValueError):
                self.tool._request('test', input=context)

    def test_enums_types(self):
        for commands in [
            {'response_length': 'huge'},
            {'sports': [{'fn': 'wrong', 'league': 'nba'}]},
            {'finance': [{'ticker': 'x', 'type': 'coin'}]},
            {'screenshot': [{'ref_id': 'https://example.com/a.pdf', 'pageno': -1}]},
            {'click': [{'ref_id': 'https://example.com', 'id': '2'}]},
            {'search_query': [{'q': 'x', 'recency': True}]},
        ]:
            with self.subTest(commands=commands), self.assertRaises(ValueError):
                self.tool._request(commands=commands)

    def test_full_urls_only_no_shared_state(self):
        for ref in ['turn0view0', 'file:///etc/passwd', 'https://user:pass@example.com', 'https:///bad']:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                self.tool._request(commands={'open': [{'ref_id': ref}]})
        self.assertFalse(hasattr(self.tool, '_sessions'))
        for key, extra in [
            ('open', {}),
            ('click', {'id': 0}),
            ('find', {'pattern': 'a'}),
            ('screenshot', {'pageno': 0}),
        ]:
            body = self.tool._request(commands={key: [{'ref_id': 'https://example.com/a.pdf', **extra}]})
            self.assertEqual(body['commands'][key][0]['ref_id'], 'https://example.com/a.pdf')

    def test_scoped_reference_isolation(self):
        command = {'click': [{'ref_id': 'turn0view0', 'id': 0}]}
        a = self.tool._request(commands=command, user={'id': 'user-a'}, metadata={'chat_id': 'chat-a'})
        a2 = self.tool._request(commands=command, user={'id': 'user-a'}, metadata={'chat_id': 'chat-a'})
        b = self.tool._request(commands=command, user={'id': 'user-b'}, metadata={'chat_id': 'chat-a'})
        c = self.tool._request(commands=command, user={'id': 'user-a'}, metadata={'chat_id': 'chat-b'})
        self.assertEqual(a['id'], a2['id'])
        self.assertNotEqual(a['id'], b['id'])
        self.assertNotEqual(a['id'], c['id'])
        self.assertNotIn('user-a', a['id'])
        self.assertNotIn('chat-a', a['id'])

    def test_no_chat_request_scope(self):
        command = {'click': [{'ref_id': 'turn0view0', 'id': 0}]}
        one = SimpleNamespace(state=SimpleNamespace())
        two = SimpleNamespace(state=SimpleNamespace())
        a = self.tool._request(commands=command, user={'id': 'user-a'}, request=one)
        a2 = self.tool._request(commands=command, user={'id': 'user-a'}, request=one)
        b = self.tool._request(commands=command, user={'id': 'user-a'}, request=two)
        self.assertEqual(a['id'], a2['id'])
        self.assertNotEqual(a['id'], b['id'])

    def test_images_in_output(self):
        result = self.tool._parse({'output': 'Image URL: https://example.com/a.jpg?q=1# Heading', 'results': []}, 3)
        self.assertEqual(result['media'][0]['url'], 'https://example.com/a.jpg?q=1')

    def test_empty_image_operation_is_failed(self):
        result = self.tool._parse({'output': '', 'results': []}, 3, {'image_query': [{'q': 'test'}]})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errors'][0]['code'], 'images_not_returned')

    def test_structured_image_url_preserved(self):
        result = self.tool._parse(
            {
                'output': 'image',
                'results': [
                    {'type': 'image_result', 'image_url': 'https://example.com/image.png', 'ref_id': 'turn0image0'}
                ],
            },
            3,
            {'image_query': [{'q': 'test'}]},
        )
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['media'][0]['url'], 'https://example.com/image.png')

    def test_pdf_text_is_not_screenshot(self):
        result = self.tool._parse(
            {'output': 'text only', 'results': []},
            3,
            {'screenshot': [{'ref_id': 'https://example.com/a.pdf', 'pageno': 0}]},
        )
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errors'][0]['code'], 'screenshot_not_returned')

    def test_utility_empty_results_valid(self):
        result = self.tool._parse({'output': 'Time: 2026-10-18 12:00 UTC', 'results': []}, 3)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['result_count'], 0)
        self.assertIn('2026', result['content'])

    def test_source_count_dedup_text_budget(self):
        rows = [{'url': 'https://example.com/' + str(i), 'title': 't', 'snippet': 's'} for i in range(8)]
        result = self.tool._parse({'output': 'x' * 20000, 'results': rows[:1] + rows}, 3)
        self.assertEqual(result['result_count'], 3)
        self.assertEqual(len(result['content']), 16000)
        self.assertTrue(result['truncated'])

    def test_actual_sources_only(self):
        self.assertNotIn('citations', self.tool._parse({'output': 'plain'}, 3))
        parsed = self.tool._parse({'output': 'plain', 'sources': ['a', 'b'], 'citations': ['c']}, 1)
        self.assertEqual(parsed['sources'], ['a'])
        self.assertEqual(parsed['citations'], ['c'])

    def test_media_not_truncated_or_text(self):
        uri = 'data:image/png;base64,' + 'A' * 20000
        result = self.tool._parse(
            {
                'output': 'screenshot ' + uri,
                'results': [{'type': 'image_result', 'url': 'https://example.com/image.jpg', 'ref_id': 'turn0image0'}],
            },
            3,
        )
        self.assertNotIn('base64', result['content'])
        self.assertIn(uri, [m['url'] for m in result['media']])
        self.assertEqual(result['results'][0]['type'], 'image_result')

    def test_media_blocks(self):
        result = self.tool._parse(
            {'output': 'screenshot', 'content': [{'type': 'image', 'mimeType': 'image/png', 'data': 'AAAA'}]}, 3
        )
        self.assertEqual(result['media'][0]['url'], 'data:image/png;base64,AAAA')

    def test_bare_base64_not_returned_as_text(self):
        data = 'A' * 20000
        result = self.tool._parse(
            {
                'output': 'image',
                'results': [
                    {'type': 'image_result', 'mime_type': 'image/png', 'blob': data, 'encrypted_content': 'opaque'}
                ],
            },
            3,
        )
        self.assertEqual(result['media'][0]['url'], 'data:image/png;base64,' + data)
        self.assertNotIn(data, json.dumps(result['results']))
        self.assertNotIn('opaque', json.dumps(result))

    def test_operation_errors(self):
        for output, code in [
            (
                'Internal Error: content type is not application/pdf and web screenshot is not enabled',
                'upstream_operation_error',
            ),
            ('Empty search results', 'empty_results'),
            ('Unable to resolve ref_id', 'upstream_operation_error'),
        ]:
            result = self.tool._parse({'output': output, 'results': []}, 3)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['errors'][0]['code'], code)

    def test_bad_responses(self):
        for payload in [None, [], 'text', {}, {'output': []}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.tool._parse(payload, 3)

    def test_top_level_upstream_error(self):
        result = self.tool._parse({'error': {'message': 'operation failed'}}, 3)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errors'][0]['code'], 'upstream_error')

    def test_partial_success_is_preserved(self):
        result = self.tool._parse(
            {'output': 'Internal Error for one operation; turn0time0 UTC time 12:00', 'results': []}, 3
        )
        self.assertEqual(result['status'], 'partial')
        self.assertIn('UTC time', result['content'])

    def test_no_opaque_or_secret(self):
        result = self.tool._parse(
            {
                'output': 'test-secret-not-real',
                'results': [{'url': 'https://example.com', 'encrypted_output': 'opaque'}],
                'encrypted_output': 'opaque',
            },
            3,
        )
        self.assertNotIn('opaque', json.dumps(result))
        self.assertNotIn('test-secret-not-real', json.dumps(result))

    def test_network_responses(self):
        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def json(self, **kwargs):
                return {'output': 'valid', 'results': []}

        class Session:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def post(self, *args, **kwargs):
                return Response()

        with patch.object(src.aiohttp, 'ClientSession', Session):
            result = asyncio.run(self.tool.search_web('test'))
            self.assertEqual(result['status'], 'ok')
            Response.status = 502
            result = asyncio.run(self.tool.search_web('test'))
            self.assertEqual(result['error']['http_status'], 502)

    def test_invalid_arguments_no_network(self):
        result = asyncio.run(self.tool.search_web(commands={'image_query': [{'q': 'x', 'admin': True}]}))
        self.assertEqual(result['error']['code'], 'invalid_arguments')


if __name__ == '__main__':
    unittest.main(verbosity=2)
