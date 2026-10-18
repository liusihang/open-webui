"""Offline regressions for navlists, structured text and full-text provenance."""

import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from tools.search import elsevier_article_search, gpt_web_search


class SearchFormatTests(unittest.TestCase):
    def setUp(self):
        self.tool = gpt_web_search.Tools()
        self.row = {'ref_id': 'turn0search0', 'url': 'https://example.org/paper', 'title': 'Paper'}

    def test_navlist_title_ref_and_summary_are_separate(self):
        parsed = self.tool._parse(
            {
                'output': 'navlistFurther readingturn0search0A useful summary',
                'results': [self.row],
            },
            3,
        )
        self.assertIn('Further reading', parsed['content'])
        self.assertIn('A useful summary', parsed['content'])
        self.assertIn('](https://example.org/paper)', parsed['content'])
        self.assertNotIn('source unavailable', parsed['content'])
        self.assertNotIn('', parsed['content'])

    def test_unknown_conflicting_refs_and_unknown_widgets_never_invent_links(self):
        for payload in [
            {'output': 'navlistReadingturn9search0Unknown source'},
            {
                'output': 'navlistReadingturn0search0Summary',
                'results': [
                    self.row,
                    {**self.row, 'url': 'https://example.org/other'},
                ],
            },
            {'output': 'unknownwidgetturn0search0', 'results': [self.row]},
        ]:
            with self.subTest(payload=payload):
                self.assertNotIn('](http', self.tool._parse(payload, 3)['content'])

    def test_structured_text_uses_same_mapping_without_mutating_payload(self):
        literal = '`citeturn0search0`\n```text\nnavlistLiteralturn0search0\n```'
        payload = {
            'output': 'Body citeturn0search0',
            'results': [
                {
                    **self.row,
                    'title': 'Title citeturn0search0',
                    'snippet': 'Summary citeturn0search0 ' + literal,
                    'opaque': 'citeturn0search0',
                }
            ],
            'sources': [{**self.row, 'description': 'Description citeturn0search0'}],
            'citations': ['Citation citeturn0search0'],
        }
        original = copy.deepcopy(payload)
        parsed = self.tool._parse(payload, 3)
        self.assertEqual(payload, original)
        row = parsed['results'][0]
        self.assertNotIn('', row['title'])
        self.assertTrue(row['snippet'].startswith('Summary ['))
        self.assertIn(literal, row['snippet'])
        self.assertEqual(row['opaque'], payload['results'][0]['opaque'])
        self.assertEqual(row['ref_id'], self.row['ref_id'])
        self.assertEqual(row['url'], self.row['url'])
        self.assertNotIn('', parsed['sources'][0]['description'])
        self.assertNotIn('', parsed['citations'][0])
        self.assertEqual(self.tool._parse({'output': literal}, 3)['content'], literal)


class ElsevierProvenanceTests(unittest.TestCase):
    def test_xml_doi_caller_doi_and_pii_fallback_match_citation(self):
        xml = (
            '<full-text-retrieval-response><coredata><title>Fixture article</title>'
            '<pii>S1234567890123456</pii></coredata><originalText><article><body>'
            '<section><section-title>Results</section-title><para>Fixture body evidence.</para>'
            '</section></body></article></originalText></full-text-retrieval-response>'
        )
        cases = [
            ({'doi': 'https://doi.org/10.1016%2Ffixture'}, xml, 'https://doi.org/10.1016/fixture'),
            ({'pii': 'S1234567890123456'}, xml, 'https://www.sciencedirect.com/science/article/pii/S1234567890123456'),
            (
                {'doi': '10.1016/caller'},
                xml.replace('<pii>', '<doi>10.1016/xml-fixture</doi><pii>'),
                'https://doi.org/10.1016/xml-fixture',
            ),
        ]
        for kwargs, body, expected in cases:
            with self.subTest(expected=expected):
                tool = elsevier_article_search.Tools()
                tool.valves.ELSEVIER_API_KEY = 'fixture-not-used'
                events = []

                async def emit(event):
                    events.append(event)

                with patch.object(tool, '_fetch_text', return_value=body):
                    parsed = json.loads(
                        asyncio.run(
                            tool.get_article_fulltext(
                                **kwargs,
                                mode='preview',
                                __event_emitter__=emit,
                            )
                        )
                    )
                self.assertEqual(parsed['url'], expected)
                citations = [event for event in events if event['type'] == 'citation']
                self.assertTrue(citations)
                self.assertEqual(citations[0]['data']['metadata'][0]['source'], expected)


if __name__ == '__main__':
    unittest.main()
