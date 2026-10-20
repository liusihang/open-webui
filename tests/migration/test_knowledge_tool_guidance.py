"""Offline tests of actual knowledge tool functions; storage and RE2 are mocked.

AST loading avoids backend startup/database dependencies. The matcher double
uses Python re only for the simple RE2-compatible patterns exercised here.
"""

import ast
import asyncio
import importlib.util
import json
import logging
import re
import sys
import types
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, patch


SOURCE = Path(__file__).resolve().parents[2] / 'backend/open_webui/tools/builtin.py'
TREE = ast.parse(SOURCE.read_text())
FUNCTIONS = {node.name: node for node in TREE.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def file(content, file_id='file-1', filename='contract.pdf'):
    return types.SimpleNamespace(id=file_id, filename=filename, data={'content': content})


def build_matcher(pattern, case_insensitive=False):
    # Mirror the production auto-detection for these compatible test patterns.
    regex = any(token in pattern for token in ('|', '.*', '.+', '.?', r'\d', r'\w', r'\s', '['))
    if regex:
        try:
            compiled = re.compile(pattern, re.I if case_insensitive else 0)
        except re.error:
            return None, 'Invalid or unsupported regex (RE2 syntax)'
        return lambda line: bool(compiled.search(line)), None
    needle = pattern.lower() if case_insensitive else pattern
    return lambda line: needle in (line.lower() if case_insensitive else line), None


def load_scope():
    spec = importlib.util.spec_from_file_location('knowledge_helpers', SOURCE.with_name('knowledge_tools.py'))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    scope = {
        'Optional': Optional,
        'Request': object,
        'JSONCodec': json,
        'asyncio': asyncio,
        'log': logging.getLogger(__name__),
        'Groups': types.SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
        '_has_read_access_to_file': AsyncMock(return_value=True),
        '_get_accessible_chat_files': AsyncMock(return_value=[]),
        'KNOWLEDGE_GREP_MAX_MATCHES': 50,
        'VIEW_FILE_DEFAULT_MAX_CHARS': 10_000,
        'VIEW_FILE_MAX_CHARS': 100_000,
        'knowledge_entries': helpers.knowledge_entries,
        'is_native_pdf_attachment': lambda *args: False,
    }
    module = ast.Module(
        body=[
            FUNCTIONS['_grep_file_models'],
            FUNCTIONS['_knowledge_source_files'],
            FUNCTIONS['grep_knowledge_files'],
            FUNCTIONS['grep_chat_files'],
        ],
        type_ignores=[],
    )
    exec(compile(module, str(SOURCE), 'exec'), scope)
    return scope


class KnowledgeGrepTests(unittest.TestCase):
    def setUp(self):
        self.scope = load_scope()
        self.matcher_module = types.ModuleType('open_webui.tools.knowledge_fs')
        self.matcher_module.build_matcher = build_matcher
        self.modules = patch.dict(sys.modules, {'open_webui.tools.knowledge_fs': self.matcher_module})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def grep(self, content, pattern='PC57', **kwargs):
        files = content if isinstance(content, list) else [file(content)]
        return self.scope['_grep_file_models'](files, pattern, **kwargs)

    def test_two_context_lines_on_each_side_and_original_match_format(self):
        output = self.grep('outside\nbefore two\nbefore one\nPC57 duty\nafter one\nafter two\noutside')
        self.assertIn('file-1  contract.pdf:4: PC57 duty', output)
        for line, text in ((2, 'before two'), (3, 'before one'), (5, 'after one'), (6, 'after two')):
            self.assertIn(f'file-1  contract.pdf-{line}- {text}', output)
        self.assertNotIn('outside', output)

    def test_overlapping_context_is_not_repeated_and_matches_stay_marked(self):
        output = self.grep('one\nPC57 first\nthree\nPC57 second\nfive\nsix')
        self.assertEqual(output.count('contract.pdf-3- three'), 1)
        self.assertIn('contract.pdf:2: PC57 first', output)
        self.assertIn('contract.pdf:4: PC57 second', output)

    def test_file_boundaries_and_real_line_numbers(self):
        output = self.grep([file('PC57\nlast'), file('first\nPC57', 'file-2', 'other.pdf')])
        self.assertIn('file-1  contract.pdf:1: PC57', output)
        self.assertIn('file-2  other.pdf:2: PC57', output)
        self.assertIn('file-2  other.pdf-1- first', output)

    def test_counts_do_not_include_context_and_count_only_stays_unchanged(self):
        files = [file('PC57\nnear\nPC57'), file('near\nPC57', 'file-2', 'other.pdf')]
        self.assertEqual(
            self.grep(files, count_only=True),
            'file-1  contract.pdf: 2\nfile-2  other.pdf: 1\n[3 total matches]',
        )

    def test_original_match_limit_is_preserved_with_honest_notice(self):
        output = self.grep('\n'.join(f'PC57 clause {i}' for i in range(60)))
        self.assertLessEqual(len(re.findall(r'contract.pdf:\d+:', output)), 50)
        self.assertIn('60 matches', output)
        self.assertIn('view_knowledge_file', output)

    def test_many_matches_have_a_total_character_budget(self):
        output = self.grep('\n'.join('PC57 ' + 'x' * 4_000 for _ in range(60)))
        self.assertLessEqual(len(output), 10_000)
        self.assertIn('truncated', output.lower())
        self.assertIn('view_knowledge_file', output)

    def test_single_long_line_is_clipped_but_still_identifiable(self):
        output = self.grep('PC57 ' + 'x' * 30_000)
        self.assertLessEqual(len(output), 10_000)
        self.assertIn('file-1  contract.pdf:1:', output)
        self.assertIn('line truncated', output)

    def test_server_character_cap_is_respected_including_notice(self):
        self.scope['VIEW_FILE_MAX_CHARS'] = 300
        output = self.grep('PC57 ' + 'x' * 30_000)
        self.assertLessEqual(len(output), 300)
        self.assertIn('truncated', output.lower())

    def test_context_has_a_line_budget_even_when_match_limit_is_large(self):
        self.scope['KNOWLEDGE_GREP_MAX_MATCHES'] = 10_000
        output = self.grep('\n'.join('PC57' if i % 5 == 2 else '.' for i in range(500)))
        self.assertLessEqual(len(output.splitlines()), 251)  # 250 source rows + notice.
        self.assertIn('truncated', output.lower())

    def test_no_matches_and_empty_files(self):
        self.assertEqual(self.grep('nothing'), 'No matches for "PC57"')
        self.assertEqual(self.grep([file(''), types.SimpleNamespace(data=None)]), 'No matches for "PC57"')

    def test_case_insensitive_and_literal_clause_number(self):
        self.assertIn('contract.pdf:1: pc57 A6.3', self.grep('pc57 A6.3', case_insensitive=True))
        self.assertIn('contract.pdf:1: A6.3', self.grep('A6.3\nA6x3', pattern='A6.3'))
        self.assertNotIn('contract.pdf:2:', self.grep('A6.3\nA6x3', pattern='A6.3'))

    def test_regex_is_not_second_unescaped(self):
        self.assertIn('contract.pdf:1: A6.3', self.grep('A6.3', pattern=r'A6[.]3|A6[.]4'))
        self.assertEqual(self.grep('A6.3', pattern=r'A6\\.3|A6\\.4'), 'No matches for "A6\\\\.3|A6\\\\.4"')

    def test_invalid_regex_error_passes_through(self):
        self.assertIn('error', json.loads(self.grep('PC57', pattern='[bad|')))

    def test_public_grep_executes_real_renderer_after_access_check(self):
        file_store = types.ModuleType('open_webui.models.files')
        file_store.Files = types.SimpleNamespace(get_file_by_id=AsyncMock(return_value=file('before\nPC57\nafter')))
        knowledge = types.ModuleType('open_webui.models.knowledge')
        knowledge.Knowledges = object()
        with patch.dict(sys.modules, {'open_webui.models.files': file_store, 'open_webui.models.knowledge': knowledge}):
            output = asyncio.run(
                self.scope['grep_knowledge_files'](
                    'PC57', file_id='file-1', __request__=object(), __user__={'id': 'owner'}
                )
            )
            self.assertIn('contract.pdf-1- before', output)
            self.scope['_has_read_access_to_file'].assert_awaited_once()
            self.scope['_has_read_access_to_file'].return_value = False
            denied = asyncio.run(
                self.scope['grep_knowledge_files'](
                    'PC57', file_id='file-1', __request__=object(), __user__={'id': 'owner'}
                )
            )
            self.assertEqual(json.loads(denied), {'error': 'File not found'})

    def test_missing_context_and_blank_pattern_remain_rejected(self):
        invoke = self.scope['grep_knowledge_files']
        for kwargs, expected in (
            ({}, 'Request context not available'),
            ({'__request__': object()}, 'User context not available'),
            ({'__request__': object(), '__user__': {'id': 'owner'}}, 'Pattern is required'),
        ):
            self.assertEqual(json.loads(asyncio.run(invoke(' ', **kwargs))), {'error': expected})

    def test_chat_files_shared_renderer_points_to_the_chat_reader(self):
        self.scope['_get_accessible_chat_files'].return_value = [({}, file('PC57 ' + 'x' * 30_000))]
        output = asyncio.run(
            self.scope['grep_chat_files'](
                'PC57',
                file_id='file-1',
                __request__=object(),
                __user__={'id': 'owner'},
                __files__=[{'id': 'file-1', 'type': 'file'}],
            )
        )
        self.assertIn('view_file', output)
        self.assertNotIn('view_knowledge_file', output)
        self.assertLessEqual(len(output), 10_000)
        denied = asyncio.run(
            self.scope['grep_chat_files'](
                'PC57',
                file_id='unattached',
                __request__=object(),
                __user__={'id': 'owner'},
                __files__=[{'id': 'file-1', 'type': 'file'}],
            )
        )
        self.assertEqual(json.loads(denied), {'error': 'File not found'})


class KnowledgeGuidanceTests(unittest.TestCase):
    def doc(self, name):
        return ast.get_docstring(FUNCTIONS[name]).lower()

    def test_directory_discovery_is_conditional_not_mandatory_first_step(self):
        doc = self.doc('list_knowledge')
        self.assertNotIn('use this first', doc)
        self.assertIn('unknown', doc)
        self.assertIn('already', doc)

    def test_tools_distinguish_filename_exact_semantic_and_direct_reads(self):
        self.assertIn('filename', self.doc('search_knowledge_files'))
        self.assertIn('grep_knowledge_files', self.doc('search_knowledge_files'))
        self.assertIn('nearby', self.doc('grep_knowledge_files'))
        self.assertIn('plain text', self.doc('grep_knowledge_files'))
        self.assertIn('double-escap', self.doc('grep_knowledge_files'))
        self.assertIn('cross-document', self.doc('query_knowledge_files'))
        self.assertIn('already', self.doc('query_knowledge_files'))
        self.assertIn('reread', self.doc('view_knowledge_file'))
        self.assertIn('start_line', self.doc('view_knowledge_file'))


if __name__ == '__main__':
    unittest.main()
