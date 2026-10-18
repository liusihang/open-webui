import asyncio
import inspect
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib import error, request

from tools.search import elsevier_article_search as module

ABSTRACT_XML = (
    '<full-text-retrieval-response><coredata><title>Example article</title><doi>10.1016/test</doi><description>'
    + 'Abstract only. ' * 12
    + '</description></coredata></full-text-retrieval-response>'
)
BODY_XML = ABSTRACT_XML.replace(
    '</full-text-retrieval-response>',
    '<originalText><section><section-title>Results</section-title><para>Measured body evidence.</para></section></originalText></full-text-retrieval-response>',
)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tool = module.Tools()
        self.tool.valves.ELSEVIER_API_KEY = 'mock-key'
        self.tool.valves.HTTP_PROXY = 'http://campus-proxy:1080'

    def test_api_proxy_scoped(self):
        with patch.object(module.request, 'build_opener') as build:
            self.tool._build_opener(use_proxy=True)
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {'http': 'http://campus-proxy:1080', 'https': 'http://campus-proxy:1080'})
        self.assertIsInstance(handlers[1], module._ElsevierRedirectHandler)

    def test_internal_no_environment_or_campus_proxy(self):
        with patch.object(module.request, 'build_opener') as build:
            self.tool._build_opener()
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertEqual(len(build.call_args.args), 1)

    def test_empty_api_proxy_is_direct(self):
        self.tool.valves.HTTP_PROXY = ''
        with patch.object(module.request, 'build_opener') as build:
            self.tool._build_opener(use_proxy=True)
        self.assertEqual(build.call_args.args[0].proxies, {})

    def test_api_rejects_unexpected_urls_before_io(self):
        with patch.object(self.tool, '_build_opener') as build:
            for url in [
                'https://api.elsevier.com.evil.test/x',
                'http://api.elsevier.com/x',
                'https://example.com/x',
                'https://api.elsevier.com:8443/x',
                'https://user@api.elsevier.com/x',
            ]:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    self.tool._request_bytes(url)
        build.assert_not_called()

    def test_cross_host_redirect_blocked(self):
        handler = module._ElsevierRedirectHandler()
        req = request.Request('https://api.elsevier.com/test', headers={'X-ELS-APIKey': 'mock-key'})
        with self.assertRaisesRegex(RuntimeError, 'redirect outside'):
            handler.redirect_request(req, None, 302, '', {}, 'https://example.com/test')
        redirected = handler.redirect_request(req, None, 302, '', {}, 'https://api.elsevier.com/next')
        self.assertEqual(redirected.full_url, 'https://api.elsevier.com/next')

    def test_http_status_and_error_bound(self):
        for status in [401, 403, 404, 429, 503]:
            opener = Mock()
            opener.open.side_effect = error.HTTPError(
                'https://api.elsevier.com/test', status, 'failed', {}, io.BytesIO(b'X' * 10000)
            )
            with self.subTest(status=status), patch.object(self.tool, '_build_opener', return_value=opener):
                with self.assertRaises(RuntimeError) as caught:
                    self.tool._request_bytes('https://api.elsevier.com/test')
                self.assertIn(f'HTTP {status}', str(caught.exception))
                self.assertLess(len(str(caught.exception)), 1300)
                opener.open.assert_called_once()

    def test_network_error_not_permission_error(self):
        opener = Mock()
        opener.open.side_effect = error.URLError('connection refused')
        with (
            patch.object(self.tool, '_build_opener', return_value=opener),
            self.assertRaisesRegex(RuntimeError, 'Unable to reach Elsevier API'),
        ):
            self.tool._request_bytes('https://api.elsevier.com/test')

    def test_doi_forms(self):
        for text in [
            'doi:10.1016/test',
            'https://doi.org/10.1016%2Ftest',
            'https://dx.doi.org/10.1016/test',
            ' 10.1016/test ',
        ]:
            self.assertEqual(self.tool._normalize_doi(text), '10.1016/test')

    def test_pii_forms(self):
        for text in [
            'S123456',
            'pii:S123456',
            'https://www.sciencedirect.com/science/article/pii/S123456?via=ihub',
            'https://www.sciencedirect.com/science/article/abs/pii/S123456',
        ]:
            self.assertEqual(self.tool._normalize_pii(text), 'S123456')

    def test_fulltext_requests_full_view_and_body_marker(self):
        with patch.object(self.tool, '_fetch_text', return_value=BODY_XML) as fetch:
            data = json.loads(
                asyncio.run(self.tool.get_article_fulltext(doi='https://doi.org/10.1016%2Ftest', mode='preview'))
            )
        self.assertNotIn('error', data)
        self.assertEqual(data['content_level'], 'fulltext')
        self.assertIn('Measured body evidence.', data['body_text'])
        self.assertEqual(fetch.call_args.args[0], 'https://api.elsevier.com/content/article/doi/10.1016/test?view=FULL')

    def test_abstract_only_not_fulltext_or_rag(self):
        for mode in ['preview', 'rag']:
            with (
                patch.object(self.tool, '_fetch_text', return_value=ABSTRACT_XML),
                patch.object(self.tool, '_build_fulltext_rag_index') as index,
            ):
                data = json.loads(
                    asyncio.run(self.tool.get_article_fulltext(doi='10.1016/test', mode=mode, query='evidence'))
                )
            self.assertIn('abstract/metadata only', data['error'])
            self.assertNotIn('content_level', data)
            index.assert_not_called()

    def test_fulltext_403_explicit(self):
        with patch.object(self.tool, '_fetch_text', side_effect=RuntimeError('Elsevier API HTTP 403: denied')):
            data = json.loads(asyncio.run(self.tool.get_article_fulltext(doi='10.1016/test', mode='preview')))
        self.assertIn('403', data['error'])
        self.assertIn('Access denied', data['tip'])

    def test_pii_reuse_and_mutual_exclusion(self):
        with patch.object(self.tool, '_fetch_text', return_value=BODY_XML) as fetch:
            data = json.loads(
                asyncio.run(
                    self.tool.get_article_fulltext(
                        pii='https://www.sciencedirect.com/science/article/pii/S123456?via=ihub', mode='preview'
                    )
                )
            )
            self.assertNotIn('error', data)
            self.assertIn('/pii/S123456?view=FULL', fetch.call_args.args[0])
            fetch.reset_mock()
            data = json.loads(
                asyncio.run(self.tool.get_article_fulltext(doi='10.1016/test', pii='S123456', mode='preview'))
            )
            self.assertIn('exactly one', data['error'])
            fetch.assert_not_called()

    def test_original_four_public_methods(self):
        public = {
            name
            for name, member in inspect.getmembers(module.Tools, inspect.iscoroutinefunction)
            if not name.startswith('_')
        }
        self.assertEqual(public, {'search_articles', 'search_authors', 'get_article_abstract', 'get_article_fulltext'})
        for method, arguments in {
            'search_articles': ['query', 'queries', 'database', 'max_results', 'mode', 'enrich_top_n'],
            'search_authors': ['name', 'query', 'orcid'],
            'get_article_abstract': ['doi', 'scopus_id', 'view'],
            'get_article_fulltext': ['doi', 'pii', 'query', 'mode', 'top_k', 'return_full_text'],
        }.items():
            signature = inspect.signature(getattr(module.Tools, method))
            for argument in arguments:
                self.assertIn(argument, signature.parameters)
        self.assertEqual(self.tool.valves.DEFAULT_DATABASE, 'both')
        self.assertEqual(self.tool.valves.FULLTEXT_DEFAULT_MODE, 'rag')

    def test_removed_non_elsevier_route_and_valves(self):
        source = Path(module.__file__).read_text(encoding='utf-8').lower()
        self.assertNotIn('crawl4ai', source)
        self.assertNotIn('fulltext_route', source)
        self.assertFalse(any(name.startswith('CRAWL4AI_') for name in self.tool.Valves.model_fields))
        with patch.object(self.tool, '_fetch_text') as fetch, patch.object(self.tool, '_build_opener') as opener:
            for identifier in ['10.1021/acs.test', '10.1038/example', '10.9999/example', 'https://example.com/article']:
                with self.subTest(identifier=identifier):
                    data = json.loads(asyncio.run(self.tool.get_article_fulltext(doi=identifier, mode='preview')))
                    self.assertEqual(data['status'], 'unsupported')
                    self.assertIn('only through the Elsevier API', data['error'])
            fetch.assert_not_called()
            opener.assert_not_called()

    def test_no_key_old_interfaces_do_not_fetch(self):
        self.tool.valves.ELSEVIER_API_KEY = ''
        calls = [
            self.tool.search_articles(query='test'),
            self.tool.search_authors(name='Test'),
            self.tool.get_article_abstract(doi='10.1016/test'),
            self.tool.get_article_fulltext(doi='10.1016/test', mode='preview'),
        ]
        for call in calls:
            self.assertIn('error', json.loads(asyncio.run(call)))

    def test_cache_remains_scoped_to_proxy_and_credentials(self):
        first = self.tool._cache_auth_fingerprint()
        self.tool.valves.HTTP_PROXY = 'http://another-proxy:1080'
        self.assertNotEqual(first, self.tool._cache_auth_fingerprint())
        second = self.tool._cache_auth_fingerprint()
        self.tool.valves.ELSEVIER_INST_TOKEN = 'new-mock-token'
        self.assertNotEqual(second, self.tool._cache_auth_fingerprint())


if __name__ == '__main__':
    unittest.main(verbosity=2)
