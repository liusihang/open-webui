"""Model-facing evidence contracts; all transport and retrieval are mocked."""

import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from tools.search import elsevier_article_search as articles
from tools.search import gpt_web_search as web


class ArticleEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tool = articles.Tools()
        self.tool.valves.ELSEVIER_API_KEY = 'test-not-a-key'
        self.assertEqual(self.tool.valves.RAG_CHUNK_WORDS, 500)
        self.assertEqual(self.tool.valves.RAG_TOP_K, 5)

    def test_short_evidence_and_source_fields_survive(self):
        evidence = {
            'rank': 1,
            'chunk_id': 'c1',
            'text': 'Measured evidence.',
            'char_start': 40,
            'char_end': 58,
            'doi': '10.1016/test',
            'section_title': 'Results',
            'rrf_score': 0.01,
            'bm25_rank': 1,
        }
        result = self.tool._compact_rag_evidence([evidence])
        self.assertEqual(result[0]['text'], evidence['text'])
        self.assertEqual(result[0]['doi'], evidence['doi'])
        self.assertEqual(result[0]['char_start'], 40)
        self.assertNotIn('rrf_score', result[0])
        self.assertNotIn('bm25_rank', result[0])
        self.assertNotIn('truncated', result[0])

    def test_projection_does_not_hide_a_second_post_retrieval_cut(self):
        text = (
            'Background context with no relevant measurement. ' * 100
            + 'Dynamic exclusion duration was 20 seconds; precision improved by 12 percent. '
            + 'Unrelated discussion follows. ' * 100
        )
        result = self.tool._compact_rag_evidence([{'chunk_id': 'a1', 'text': text, 'char_start': 100}])[0]
        self.assertIn('20 seconds', result['text'])
        self.assertEqual(result['text'], text)
        self.assertEqual(result['char_start'], 100)
        self.assertNotIn('truncated', result)

    def test_unicode_offsets_and_word_budget(self):
        for text in ['研究蛋白质结构与测量误差。🧬 ' * 700, 'ACDEFGHIKLMNPQRSTVWY ' * 700]:
            with self.subTest(text=text[:30]):
                parts = self.tool._split_text_for_rag(text, 500, 50)
                part = parts[-1]
                result = self.tool._compact_rag_evidence(
                    [{'text': part['text'], 'char_start': part['start'], 'char_end': part['end']}]
                )[0]
                self.assertNotIn('\ufffd', result['text'])
                self.assertLessEqual(len(result['text'].split()), 500)
                self.assertEqual(text[result['char_start'] : result['char_end']], result['text'])

    def test_short_abstract_remains_a_single_unpadded_chunk(self):
        abstract = 'Measured proteomics evidence. ' * 100
        chunks = self.tool._build_search_article_rag_chunks(
            [{'title': 'Paper A', 'abstract': abstract, 'doi': '10.1016/test'}]
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(abstract.strip(), chunks[0]['text'])

    def test_article_title_is_not_repeated_as_a_section_title(self):
        result = self.tool._compact_rag_evidence(
            [{'title': 'Paper A', 'section_title': 'Paper A', 'text': 'Evidence.'}]
        )[0]
        self.assertEqual(result['title'], 'Paper A')
        self.assertNotIn('section_title', result)

    def test_public_search_returns_only_one_copy_of_selected_evidence(self):
        rows = [
            {
                'title': 'Paper A',
                'abstract': 'Dynamic exclusion reduces repeated selection. ' * 100,
                'doi': '10.1016/test',
                'url': 'https://doi.org/10.1016/test',
                'year': '2026',
            }
        ]
        response = {'results': rows, 'result_count': 1, 'database': 'scopus'}
        events = []

        async def emit(event):
            events.append(event)

        with (
            patch.object(self.tool, '_search_articles_structured', new=AsyncMock(return_value=response)),
            patch.object(self.tool, '_embed_texts', side_effect=RuntimeError('offline fixture')),
        ):
            result = json.loads(
                asyncio.run(self.tool.search_articles(query='dynamic exclusion', __event_emitter__=emit))
            )
        for field in (
            'answer_context',
            'candidate_articles',
            'index_stats',
            'search_rerank',
            'source_queries',
            'source_query_variants',
            'results',
        ):
            self.assertNotIn(field, result)
        evidence = result['evidence_chunks'][0]
        self.assertLessEqual(len(evidence['text'].split()), 500)
        self.assertNotIn('Title:', evidence['text'])
        self.assertEqual(evidence['doi'], '10.1016/test')
        self.assertTrue(any(event['type'] == 'citation' for event in events))
        self.assertTrue(result['warnings'])  # Offline dense-retrieval warning stays visible.

    def test_public_fulltext_rag_no_duplicate_context_or_automatic_fulltext(self):
        body = 'Measured body evidence with uncertainty. ' * 100
        parsed = {
            'title': 'Paper',
            'doi': '10.1016/test',
            'body_text': body,
            'full_text': body,
            'sections': [{'title': 'Results', 'text': body}],
        }
        kwargs = dict(
            parsed=parsed,
            resolved_mode='rag',
            identifier='10.1016/test',
            query='body evidence',
            top_k=2,
            bm25_candidates=4,
            dense_candidates=4,
            return_full_text=False,
            source='elsevier_api',
            content_prefix='article',
            article_url='https://doi.org/10.1016/test',
            __event_emitter__=None,
        )
        with patch.object(self.tool, '_embed_texts', side_effect=RuntimeError('offline fixture')):
            result = asyncio.run(self.tool._build_fulltext_result(**kwargs))
        self.assertNotIn('answer_context', result)
        self.assertNotIn('full_text', result)
        self.assertNotIn('abstract', result)
        self.assertNotIn('index_stats', result)
        self.assertTrue(result['evidence_chunks'])
        for evidence in result['evidence_chunks']:
            self.assertLessEqual(len(evidence['text'].split()), 500)
        kwargs['return_full_text'] = True
        result = asyncio.run(self.tool._build_fulltext_result(**kwargs))
        self.assertEqual(result['full_text'], body.strip())

    def test_fulltext_frontend_excerpt_does_not_need_duplicate_model_snippet(self):
        xml = (
            '<full-text-retrieval-response><coredata><title>Example article</title>'
            '<doi>10.1016/test</doi></coredata><originalText><section><section-title>Results</section-title>'
            '<para>Measured body evidence with uncertainty.</para></section></originalText>'
            '</full-text-retrieval-response>'
        )
        events = []

        async def emit(event):
            events.append(event)

        with (
            patch.object(self.tool, '_fetch_text', return_value=xml),
            patch.object(self.tool, '_embed_texts', side_effect=RuntimeError('offline fixture')),
        ):
            result = json.loads(
                asyncio.run(
                    self.tool.get_article_fulltext(
                        doi='10.1016/test', query='measured evidence', __event_emitter__=emit
                    )
                )
            )
        self.assertNotIn('error', result)
        self.assertNotIn('snippet', result)
        citations = [event for event in events if event['type'] == 'citation']
        self.assertEqual(len(citations), 1)
        self.assertIn('Measured body evidence', citations[0]['data']['document'][0])


class WebEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tool = web.Tools()
        self.tool.valves.API_KEY = 'test-not-a-key'

    def test_model_copy_omits_duplicate_sources_without_mutating_display_copy(self):
        source = {
            'title': 'Paper',
            'url': 'https://example.org/paper',
            'ref_id': 'turn0search0',
            'snippet': 'Measured evidence.',
        }
        original = {
            'status': 'ok',
            'content': 'Paper https://example.org/paper (ref_id: turn0search0)\nMeasured evidence.',
            'results': [source],
            'result_count': 1,
            'citation_sources': [source],
            'sources': [source],
            'citations': [source],
            'truncated': False,
        }
        frozen = copy.deepcopy(original)
        result = self.tool._model_result(original)
        self.assertEqual(original, frozen)
        self.assertEqual(result['content'], original['content'])
        for field in ('results', 'result_count', 'citation_sources', 'sources', 'citations'):
            self.assertNotIn(field, result)

    def test_missing_refs_and_sources_are_retained_once(self):
        source = {
            'title': 'Paper',
            'url': 'https://example.org/paper',
            'ref_id': 'turn0view0',
            'snippet': 'Missing from the body.',
        }
        result = self.tool._model_result(
            {'status': 'ok', 'content': 'A short answer.', 'results': [source], 'citation_sources': [source]}
        )
        self.assertEqual(result['results'], [source])
        self.assertEqual(result['result_count'], 1)
        self.assertNotIn('citation_sources', result)

    def test_navigation_ref_not_discarded_just_because_url_is_present(self):
        source = {'title': 'Paper', 'url': 'https://example.org/paper', 'ref_id': 'turn0view0'}
        result = self.tool._model_result({'content': 'https://example.org/paper', 'citation_sources': [source]})
        self.assertEqual(result['results'][0]['ref_id'], 'turn0view0')

    def test_source_url_alone_does_not_prove_its_evidence_is_duplicated(self):
        source = {
            'url': 'https://example.org/paper',
            'ref_id': 'turn0view0',
            'snippet': 'A measurement missing from the text.',
            'published_at': '2026-10-20',
        }
        result = self.tool._model_result(
            {'content': 'https://example.org/paper turn0view0', 'citation_sources': [source]}
        )
        self.assertEqual(result['results'], [source])

    def test_page_lines_links_errors_and_media_remain_unchanged(self):
        source = {
            'status': 'partial',
            'content': 'turn0view0 L18: evidence\n【7†Read more】',
            'errors': [{'code': 'upstream_operation_error'}],
            'truncated': True,
            'media': [{'type': 'image', 'url': 'https://example.org/figure.png'}],
        }
        self.assertEqual(self.tool._model_result(source), source)

    def test_nonstructured_sources_are_not_silently_discarded(self):
        result = self.tool._model_result({'content': '', 'sources': ['source text'], 'citations': ['citation text']})
        self.assertEqual(result['sources'], ['source text'])
        self.assertEqual(result['citations'], ['citation text'])

    def test_unrecognized_source_shapes_do_not_break_a_successful_search(self):
        sources = [{'url': 42}, {'url': 'https://example.org/paper', 'ref_id': 7}]
        result = self.tool._model_result({'status': 'ok', 'content': 'Evidence.', 'sources': sources})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['sources'], sources)

    def test_public_function_emits_frontend_citations_but_returns_compact_evidence(self):
        source = {
            'title': 'Paper',
            'url': 'https://example.org/paper',
            'ref_id': 'turn0search0',
            'snippet': 'Source excerpt.',
        }
        parsed = {
            'status': 'ok',
            'content': 'https://example.org/paper (ref_id: turn0search0) Source excerpt.',
            'results': [source],
            'result_count': 1,
            'citation_sources': [source],
        }

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def json(self, **kwargs):
                return {'output': 'fixture'}

        class Session(Response):
            def __init__(self, *args, **kwargs):
                pass

            def post(self, *args, **kwargs):
                return Response()

        events = []

        async def emit(event):
            events.append(event)

        with (
            patch.object(web.aiohttp, 'ClientSession', Session),
            patch.object(self.tool, '_parse', return_value=parsed),
        ):
            result = asyncio.run(self.tool.search_web('fixture', __event_emitter__=emit))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['data']['document'], ['Source excerpt.'])
        self.assertEqual(events[0]['data']['metadata'][0]['ref_id'], 'turn0search0')
        self.assertNotIn('citation_sources', result)
        self.assertNotIn('results', result)
        self.assertIn('turn0search0', result['content'])


if __name__ == '__main__':
    unittest.main()
