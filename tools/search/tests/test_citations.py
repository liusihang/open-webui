import unittest
from tools.search import gpt_web_search as src


class Citations(unittest.TestCase):
    def setUp(self):
        self.tool = src.Tools()

    def test_actual_failure_multiple_refs_and_turns(self):
        rows = [
            {'ref_id': ref, 'url': url, 'title': ref}
            for ref, url in [
                ('turn1search0', 'https://example.org/a'),
                ('turn2search0', 'https://example.org/b'),
                ('turn2search1', 'https://example.org/c'),
            ]
        ]
        out = self.tool._parse(
            {'output': '事实citeturn2search1turn2search0turn2search1；旧事实citeturn1search0', 'results': rows},
            1,
        )
        self.assertNotIn('', out['content'])
        for url in [r['url'] for r in rows]:
            self.assertIn(url, out['content'])
        self.assertEqual(len(out['results']), 1)
        self.assertEqual({r['ref_id'] for r in out['citation_sources']}, {r['ref_id'] for r in rows})

    def test_unknown_is_not_invented(self):
        out = self.tool._parse({'output': '中文citeturn9search0；broken citeturn9search1', 'results': []}, 3)
        self.assertNotIn('', out['content'])
        self.assertIn('source unavailable', out['content'])
        self.assertNotIn('https://', out['content'])
        self.assertTrue(out['citation_warnings'])

    def test_header_mapping_without_results_and_click_labels(self):
        text = 'Example (https://www.iana.org/domains)\nciteturn1view0 Source: click({"ref_id":"turn0view0","id":0})\nL0: cite0†Domains'
        out = self.tool._parse({'output': text, 'results': []}, 1)
        self.assertIn('https://www.iana.org/domains', out['content'])
        self.assertIn('link id: 0', out['content'])
        self.assertIn('turn1view0', out['content'])
        self.assertNotIn('', out['content'])
        self.assertEqual(out['citation_sources'][0]['ref_id'], 'turn1view0')

    def test_utility_widgets_and_unicode_survive(self):
        text = 'financeturn0finance0 AAPL 100 美元\nciteturn0time0 UTC 12:00\nforecastturn0forecast0 晴'
        out = self.tool._parse({'output': text, 'results': []}, 1)
        self.assertNotIn('', out['content'])
        self.assertIn('AAPL 100 美元', out['content'])
        self.assertIn('UTC 12:00', out['content'])
        self.assertNotIn('citation_sources', out)

    def test_code_literals_and_markdown_unchanged(self):
        text = '中文 `citeturn0search0`\n```text\nciteturn0search1\n```\n[正常](https://example.org/)'
        out = self.tool._parse({'output': text}, 3)
        self.assertEqual(out['content'], text)

    def test_nested_source_markers_and_unsafe_url(self):
        text = 'Source (https://example.org/a_(b))\nciteturn0search0 More navlistturn0search0†Sourceturn0search1†Other'
        out = self.tool._parse(
            {'output': text, 'results': [{'ref_id': 'turn0search1', 'url': 'javascript:bad', 'title': 'x'}]}, 3
        )
        self.assertNotIn('', out['content'])
        self.assertIn('https://example.org/a_%28b%29', out['content'])
        self.assertNotIn('javascript:', out['content'])

    def test_conflicting_ref_does_not_silently_choose_url(self):
        out = self.tool._parse(
            {
                'output': 'citeturn0search0',
                'results': [
                    {'ref_id': 'turn0search0', 'url': 'https://example.org/a'},
                    {'ref_id': 'turn0search0', 'url': 'https://example.org/b'},
                ],
            },
            3,
        )
        self.assertNotIn('](https://', out['content'])
        self.assertIn('source unavailable', out['content'])

    def test_literal_code_header_cannot_supply_a_citation(self):
        out = self.tool._parse(
            {'output': '```text\nFake (https://example.org/fake)\nciteturn0search0\n```\nClaim citeturn0search0'},
            3,
        )
        self.assertIn('Claim [source unavailable', out['content'])


if __name__ == '__main__':
    unittest.main()
