"""500-word chunks must exist before embedding, retrieval and Top-5 selection."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from tools.search import elsevier_article_search as src


def paragraph(label, count):
    return ' '.join(f'{label}{i}' for i in range(count)) + '.'


class RagChunkTests(unittest.TestCase):
    def setUp(self):
        self.tool = src.Tools()

    def test_explicit_word_units_and_default_top_five(self):
        self.assertEqual(self.tool.valves.RAG_CHUNK_WORDS, 500)
        self.assertEqual(self.tool.valves.RAG_CHUNK_OVERLAP_WORDS, 50)
        self.assertEqual(self.tool.valves.RAG_TOP_K, 5)
        self.assertNotIn('RAG_EVIDENCE_WORD_LIMIT', self.tool.Valves.model_fields)
        # Old persisted character values cannot silently become word counts.
        valves = self.tool.Valves(RAG_CHUNK_SIZE=1200, RAG_CHUNK_OVERLAP=200)
        self.assertEqual(valves.RAG_CHUNK_WORDS, 500)
        self.assertEqual(valves.RAG_CHUNK_OVERLAP_WORDS, 50)

    def test_splitter_uses_words_and_preserves_offsets_and_overlap(self):
        text = paragraph('word', 1200)
        parts = self.tool._split_text_for_rag(text, 500, 50)
        self.assertEqual([len(p['text'].split()) for p in parts], [500, 500, 300])
        for part in parts:
            self.assertEqual(text[part['start'] : part['end']], part['text'])
        for previous, current in zip(parts, parts[1:]):
            self.assertEqual(previous['text'].split()[-50:], current['text'].split()[:50])
        self.assertEqual(parts[-1]['end'], len(text))

    def test_sentences_and_unicode_do_not_create_missing_source_ranges(self):
        text = ('Measured evidence supports this result.\n\n' * 200 + '研究证据 🧬 ' * 100).strip()
        parts = self.tool._split_text_for_rag(text, 500, 50)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part['text'].split()), 500)
            self.assertEqual(text[part['start'] : part['end']], part['text'])
            self.assertNotIn('\ufffd', part['text'])
        for previous, current in zip(parts, parts[1:]):
            self.assertLessEqual(current['start'], previous['end'])
        self.assertEqual(parts[0]['start'], 0)
        self.assertEqual(parts[-1]['end'], len(text))

    def test_search_embeds_prebuilt_fragments_not_complete_long_abstracts(self):
        abstract = paragraph('abstract', 1200)
        rows = [{'title': 'Paper A', 'abstract': abstract, 'doi': '10.1016/test'}]
        with patch.object(self.tool, '_embed_texts', side_effect=RuntimeError('offline fixture')) as embed:
            index = self.tool._build_search_article_rag_index(results=rows)
        chunks = index['chunks']
        self.assertEqual(len(chunks), 3)
        self.assertEqual([len(c['text'].split()) for c in chunks], [500, 500, 300])
        self.assertEqual([c['part_index'] for c in chunks], [1, 2, 3])
        self.assertEqual(len({c['chunk_id'] for c in chunks}), 3)
        self.assertEqual(len({c['article_id'] for c in chunks}), 1)
        for chunk in chunks:
            self.assertEqual(abstract[chunk['char_start'] : chunk['char_end']], chunk['text'])
        documents = embed.call_args.args[0]
        self.assertEqual(len(documents), 3)
        self.assertTrue(all('Paper A' in text for text in documents))
        self.assertTrue(all(abstract not in text for text in documents))

    def test_fulltext_blocks_respect_sections_and_cache_version_changes(self):
        first, second = paragraph('method', 1200), paragraph('result', 1200)
        parsed = {'sections': [{'title': 'Methods', 'text': first}, {'title': 'Results', 'text': second}]}
        chunks = self.tool._build_rag_chunks(parsed)
        self.assertEqual(len(chunks), 6)
        self.assertEqual({c['section_title'] for c in chunks}, {'Methods', 'Results'})
        for chunk in chunks:
            original = first if chunk['section_title'] == 'Methods' else second
            self.assertEqual(original[chunk['char_start'] : chunk['char_end']], chunk['text'])
            self.assertLessEqual(len(chunk['text'].split()), 500)
        signature = self.tool._fulltext_rag_signature()
        self.assertIn('words', signature)
        self.tool.valves.RAG_CHUNK_WORDS = 400
        self.assertNotEqual(signature, self.tool._fulltext_rag_signature())

    def test_retrieval_selects_five_real_blocks_without_a_second_cut(self):
        rows = [
            {'title': f'Paper {i}', 'abstract': paragraph(f'evidence{i}', 1200), 'doi': f'10.1016/test{i}'}
            for i in range(3)
        ]
        with patch.object(self.tool, '_embed_texts', side_effect=RuntimeError('offline fixture')):
            index = self.tool._build_search_article_rag_index(results=rows)
        self.assertEqual(len(index['chunks']), 9)
        selected = self.tool._query_search_article_rag(
            rag_index=index,
            query='evidence10',
            top_k=self.tool.valves.RAG_TOP_K,
            bm25_candidates=24,
            dense_candidates=24,
            rrf_k=60,
        )
        self.assertEqual(len(selected['evidence_chunks']), 5)
        self.assertEqual(selected['index_stats']['article_count'], 3)
        by_id = {chunk['chunk_id']: chunk for chunk in index['chunks']}
        output = self.tool._compact_rag_evidence(selected['evidence_chunks'])
        self.assertEqual(len(output), 5)
        for evidence in output:
            original = by_id[evidence['chunk_id']]
            self.assertEqual(evidence['text'], original['text'])
            self.assertEqual(evidence['char_start'], original['char_start'])
            self.assertEqual(evidence['char_end'], original['char_end'])

    def test_public_search_defaults_to_five_but_does_not_pad_short_results(self):
        self.tool.valves.ELSEVIER_API_KEY = 'test-not-a-key'
        for count in (2, 8):
            rows = [
                {'title': f'Paper {i}', 'abstract': 'Measured evidence.', 'doi': f'10.1016/test{i}'}
                for i in range(count)
            ]
            response = {'results': rows, 'result_count': count, 'database': 'scopus'}
            with (
                patch.object(self.tool, '_search_articles_structured', new=AsyncMock(return_value=response)),
                patch.object(self.tool, '_embed_texts', side_effect=RuntimeError('offline fixture')),
            ):
                result = json.loads(asyncio.run(self.tool.search_articles(query='measured evidence')))
            self.assertEqual(result['top_k'], min(count, 5))
            self.assertEqual(len(result['evidence_chunks']), min(count, 5))

    def test_dense_and_rerank_operate_on_chunks_before_top_five(self):
        rows = [
            {
                'title': f'Paper {i}',
                'abstract': paragraph('winner' if i == 2 else f'background{i}', 1200),
                'doi': f'10.1016/test{i}',
            }
            for i in range(3)
        ]
        self.tool.valves.RERANK_ENABLED = True
        self.tool.valves.RERANK_CANDIDATES = 9

        def vectors(documents):
            return [[1.0, 0.0] for _ in documents]

        def rank(query, documents):
            self.assertEqual(len(documents), 9)
            self.assertTrue(all(len(text.split()) < 520 for text in documents))
            winner = next(i for i, text in enumerate(documents) if 'winner0 ' in text)
            order = [winner] + [i for i in range(len(documents)) if i != winner]
            return [{'index': i, 'relevance_score': 1.0 if i == winner else 0.1} for i in order]

        with (
            patch.object(self.tool, '_embed_texts', side_effect=vectors),
            patch.object(self.tool, '_rerank_documents', side_effect=rank) as rerank,
        ):
            index = self.tool._build_search_article_rag_index(results=rows)
            selected = self.tool._query_search_article_rag(
                rag_index=index, query='query', top_k=5, bm25_candidates=24, dense_candidates=24, rrf_k=60
            )
        rerank.assert_called_once()
        self.assertEqual(selected['retrieval_mode'], 'hybrid_rerank')
        self.assertEqual(len(selected['evidence_chunks']), 5)
        self.assertEqual(selected['evidence_chunks'][0]['chunk_id'], 'a3p1')

    def test_one_fulltext_article_can_return_five_distinct_chunks(self):
        body = paragraph('method', 1200) + '\n\n' + paragraph('result', 1200)
        parsed = {
            'title': 'One article',
            'doi': '10.1016/test',
            'body_text': body,
            'full_text': body,
            'sections': [{'title': 'Results', 'text': body}],
        }
        with patch.object(self.tool, '_embed_texts', side_effect=RuntimeError('offline fixture')):
            result = asyncio.run(
                self.tool._build_fulltext_result(
                    parsed=parsed,
                    resolved_mode='rag',
                    identifier='10.1016/test',
                    query='result0',
                    top_k=None,
                    bm25_candidates=None,
                    dense_candidates=None,
                    return_full_text=False,
                    source='elsevier_api',
                    content_prefix='article',
                    article_url='https://doi.org/10.1016/test',
                    __event_emitter__=None,
                )
            )
        self.assertEqual(result['top_k'], 5)
        self.assertEqual(len({c['chunk_id'] for c in result['evidence_chunks']}), 5)
        for chunk in result['evidence_chunks']:
            self.assertEqual(body[chunk['char_start'] : chunk['char_end']], chunk['text'])
            self.assertLessEqual(len(chunk['text'].split()), 500)
        events = []

        async def emit(event):
            events.append(event)

        asyncio.run(
            self.tool._emit_citations(
                emit,
                [
                    {'title': 'One article', 'url': 'https://doi.org/10.1016/test', 'snippet': c['text']}
                    for c in result['evidence_chunks']
                ],
            )
        )
        self.assertEqual(len(events), 1)  # Five evidence chunks, one frontend source.


if __name__ == '__main__':
    unittest.main()
