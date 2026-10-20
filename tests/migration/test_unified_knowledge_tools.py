"""Exercise public knowledge interfaces with mocked storage, never a Live DB."""

import ast
import asyncio
import copy
import importlib.util
import json
import logging
import sys
import types
import unittest
from pathlib import Path
from typing import Literal, Optional
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'backend/open_webui/tools/builtin.py'


def model_file(identity='file', text='first\nPC57 requirement\nlast', owner='owner'):
    return types.SimpleNamespace(
        id=identity,
        filename=identity + '.txt',
        user_id=owner,
        data={'content': text},
        meta={},
        created_at=1,
        updated_at=2,
    )


def load_scope():
    scope = dict(
        Optional=Optional,
        Literal=Literal,
        Request=object,
        asyncio=asyncio,
        JSONCodec=json,
        log=logging.getLogger(__name__),
        Groups=types.SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
        _has_read_access_to_file=AsyncMock(return_value=True),
        Config=types.SimpleNamespace(get=AsyncMock(return_value=5)),
        VIEW_FILE_DEFAULT_MAX_CHARS=10000,
        VIEW_FILE_MAX_CHARS=100000,
        KNOWLEDGE_GREP_MAX_MATCHES=50,
        is_native_pdf_attachment=lambda item, metadata: item.get('native_model_file') is True,
    )
    helper = ROOT / 'backend/open_webui/tools/knowledge_tools.py'
    if helper.exists():
        spec = importlib.util.spec_from_file_location('knowledge_helpers_fixture', helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scope.update(
            knowledge_entries=module.knowledge_entries,
            file_content_page=module.file_content_page,
            model_evidence_result=module.model_evidence_result,
        )
    nodes = [
        node
        for node in ast.parse(SOURCE.read_text()).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (
            node.name.startswith('_knowledge_')
            or node.name
            in {
                'search_knowledge_bases',
                'list_knowledge_bases',
                'search_knowledge_files',
                '_get_accessible_chat_files',
                '_grep_file_models',
                'grep_knowledge_files',
                'view_file',
                'view_knowledge_file',
                'view_knowledge_evidence',
                'query_chat_files',
                'query_knowledge_files',
            }
        )
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope


class UnifiedKnowledgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = load_scope()
        self.files = {
            'file': model_file(),
            'second': model_file('second'),
            'foreign': model_file('foreign', owner='other'),
        }
        self.kbs = {
            'kb': types.SimpleNamespace(id='kb', user_id='owner', name='Library', description='Demo', updated_at=2)
        }
        self.members = {'kb': ['file', 'second']}
        self.file_store = types.SimpleNamespace(
            get_file_by_id=AsyncMock(side_effect=lambda identity: self.files.get(identity))
        )

        async def search_files(knowledge_id, user_id, filter, skip=0, limit=5):
            matches = [
                self.files[identity]
                for identity in self.members.get(knowledge_id, [])
                if filter.get('query', '').casefold() in self.files[identity].filename.casefold()
            ]
            return types.SimpleNamespace(items=matches[skip : skip + limit])

        self.knowledge = types.SimpleNamespace(
            get_knowledge_by_id=AsyncMock(side_effect=lambda identity: self.kbs.get(identity)),
            get_files_by_id=AsyncMock(
                side_effect=lambda identity: [self.files[i] for i in self.members.get(identity, [])]
            ),
            get_file_metadatas_by_id=AsyncMock(
                side_effect=lambda identity: [self.files[i] for i in self.members.get(identity, [])]
            ),
            has_file=AsyncMock(side_effect=lambda kb, identity: identity in self.members.get(kb, [])),
            get_knowledges_by_file_id=AsyncMock(return_value=[]),
            search_files_by_id=AsyncMock(side_effect=search_files),
            search_knowledge_files=AsyncMock(return_value=types.SimpleNamespace(items=[])),
            search_knowledge_bases=AsyncMock(return_value=types.SimpleNamespace(items=list(self.kbs.values()))),
        )
        self.grants = types.SimpleNamespace(has_access=AsyncMock(return_value=False))
        modules = {}
        for name, key, value in [
            ('open_webui.models.files', 'Files', self.file_store),
            ('open_webui.models.knowledge', 'Knowledges', self.knowledge),
            ('open_webui.models.access_grants', 'AccessGrants', self.grants),
        ]:
            module = types.ModuleType(name)
            setattr(module, key, value)
            modules[name] = module
        matcher = types.ModuleType('open_webui.tools.knowledge_fs')
        matcher.build_matcher = lambda pattern, case_insensitive: (
            lambda line: pattern.casefold() in line.casefold() if case_insensitive else pattern in line,
            None,
        )
        modules['open_webui.tools.knowledge_fs'] = matcher
        self.patch = patch.dict(sys.modules, modules)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.context = {
            '__request__': types.SimpleNamespace(state=types.SimpleNamespace()),
            '__user__': {'id': 'owner', 'role': 'user'},
        }

    async def invoke(self, name, *args, **kwargs):
        value = await self.scope[name](*args, **self.context, **kwargs)
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    async def test_empty_query_lists_knowledge_bases(self):
        result = await self.invoke('search_knowledge_bases')
        self.assertEqual(result[0]['id'], 'kb')
        self.assertEqual(self.knowledge.search_knowledge_bases.await_args.kwargs['filter']['query'], '')

    async def test_legacy_kb_list_reuses_canonical_search(self):
        self.scope['search_knowledge_bases'] = AsyncMock(return_value='[]')
        self.assertEqual(await self.invoke('list_knowledge_bases', count=7, skip=2), [])
        self.scope['search_knowledge_bases'].assert_awaited_once()
        self.assertEqual(self.scope['search_knowledge_bases'].await_args.kwargs['count'], 7)

    async def test_file_catalog_lists_only_chat_attachments_when_no_kb_selected(self):
        result = await self.invoke('search_knowledge_files', __metadata__={'files': [{'id': 'file', 'type': 'file'}]})
        self.assertEqual([row['id'] for row in result], ['file'])
        self.knowledge.search_knowledge_files.assert_not_awaited()

    async def test_file_catalog_deduplicates_attachment_and_kb_membership(self):
        result = await self.invoke(
            'search_knowledge_files',
            query='',
            __model_knowledge__=[{'id': 'kb', 'type': 'collection'}, {'id': 'file', 'type': 'file'}],
        )
        self.assertEqual(sorted(row['id'] for row in result), ['file', 'second'])
        self.assertNotIn('content', result[0])

    async def test_file_catalog_denied_direct_attachment_is_not_exposed(self):
        self.scope['_has_read_access_to_file'].return_value = False
        result = await self.invoke(
            'search_knowledge_files', query='', __metadata__={'files': [{'id': 'foreign', 'type': 'file'}]}
        )
        self.assertEqual(result, [])

    async def test_explicit_kb_restriction_drops_chat_attachments(self):
        result = await self.invoke(
            'search_knowledge_files',
            query='',
            knowledge_id='kb',
            __model_knowledge__=[{'id': 'kb', 'type': 'collection'}],
            __metadata__={'files': [{'id': 'foreign', 'type': 'file'}]},
        )
        self.assertEqual(sorted(row['id'] for row in result), ['file', 'second'])

    async def test_bound_scope_cannot_expand_to_another_kb(self):
        result = await self.invoke(
            'search_knowledge_files',
            query='',
            knowledge_id='unattached',
            __model_knowledge__=[{'id': 'kb', 'type': 'collection'}],
        )
        self.assertIn('error', result)
        self.knowledge.search_files_by_id.assert_not_awaited()

    async def test_exact_search_includes_chat_attachment_without_global_scan(self):
        result = await self.invoke(
            'grep_knowledge_files', 'PC57', __metadata__={'files': [{'id': 'file', 'type': 'file'}]}
        )
        self.assertIn('PC57 requirement', result)
        self.knowledge.search_knowledge_bases.assert_not_awaited()

    async def test_exact_search_with_no_scope_does_not_scan_all_knowledge(self):
        result = await self.invoke('grep_knowledge_files', 'PC57')
        self.assertIn('error', result)
        self.knowledge.search_knowledge_bases.assert_not_awaited()

    async def test_explicit_exact_search_still_checks_file_access(self):
        self.scope['_has_read_access_to_file'].return_value = False
        result = await self.invoke('grep_knowledge_files', 'PC57', file_id='foreign')
        self.assertIn('error', result)

    async def test_native_pdf_is_not_added_as_an_indexed_search_target(self):
        result = await self.invoke(
            'search_knowledge_files',
            query='',
            __knowledge_enabled__=False,
            __metadata__={'files': [{'id': 'file', 'type': 'file', 'native_model_file': True}]},
        )
        self.assertEqual(result, [])

    async def test_line_based_read_is_bounded_even_for_one_huge_line(self):
        self.files['file'].data['content'] = 'x' * 5000 + '\nsecond'
        result = await self.invoke('view_knowledge_file', 'file', start_line=1, end_line=2, max_chars=100)
        self.assertLessEqual(len(result['content']), 100)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['next_offset'], result['returned_chars'])

    async def test_legacy_file_reader_and_canonical_reader_share_text_and_offsets(self):
        legacy = await self.invoke('view_file', 'file', offset=3, max_chars=9)
        canonical = await self.invoke('view_knowledge_file', 'file', offset=3, max_chars=9)
        for key in ['content', 'offset', 'next_offset']:
            self.assertEqual(legacy[key], canonical[key])

    async def test_read_rejects_file_and_evidence_refs_together(self):
        result = await self.invoke('view_knowledge_file', 'file', evidence_refs=['evidence'])
        self.assertIn('error', result)

    async def test_evidence_read_delegates_with_scope_and_batch(self):
        self.scope['view_knowledge_evidence'] = AsyncMock(return_value='{"items":[],"warnings":[]}')
        result = await self.invoke(
            'view_knowledge_file', evidence_refs=['a', 'b'], __metadata__={'files': [{'id': 'file', 'type': 'file'}]}
        )
        self.assertEqual(result['items'], [])
        self.assertEqual(self.scope['view_knowledge_evidence'].await_args.args[0], ['a', 'b'])

    async def test_chat_semantic_compatibility_uses_unified_search_not_legacy_rag(self):
        self.scope['query_knowledge_files'] = AsyncMock(return_value='{"items":[]}')
        result = await self.invoke(
            'query_chat_files', 'temperature', file_id='file', count=3, __files__=[{'id': 'file', 'type': 'file'}]
        )
        self.assertEqual(result, {'items': []})
        args = self.scope['query_knowledge_files'].await_args
        self.assertEqual(args.kwargs['file_ids'], ['file'])
        self.assertFalse(args.kwargs['__knowledge_enabled__'])

    async def test_kb_discovery_does_not_load_file_bodies_to_count_them(self):
        await self.invoke('search_knowledge_bases')
        self.knowledge.get_files_by_id.assert_not_awaited()
        self.knowledge.get_file_metadatas_by_id.assert_awaited_once_with('kb')

    async def test_bound_file_read_and_exact_search_cannot_expand_to_another_file(self):
        for name, args in [('view_knowledge_file', ('second',)), ('grep_knowledge_files', ('PC57',))]:
            extra = {'file_id': 'second'} if name.startswith('grep') else {}
            result = await self.invoke(name, *args, **extra, __metadata__={'files': [{'id': 'file', 'type': 'file'}]})
            self.assertIn('error', result)

    async def test_legacy_reader_obeys_files_only_scope(self):
        for identity, allowed in [('file', True), ('second', False)]:
            result = await self.invoke(
                'view_file',
                identity,
                __knowledge_enabled__=False,
                __model_knowledge__=[{'id': 'kb', 'type': 'collection'}],
                __metadata__={'files': [{'id': 'file', 'type': 'file'}]},
            )
            self.assertEqual('error' not in result, allowed)

    async def test_file_read_can_narrow_to_a_member_of_the_selected_kb(self):
        result = await self.invoke(
            'view_knowledge_file', 'second', __model_knowledge__=[{'id': 'kb', 'type': 'collection'}]
        )
        self.assertEqual(result['id'], 'second')


class EvidencePresentationTests(unittest.TestCase):
    def setUp(self):
        self.scope = load_scope()

    def test_body_budget_is_shared_and_projection_does_not_mutate_stored_evidence(self):
        original = {
            'encoding_space': {'private': 'details'},
            'items': [
                {
                    'evidence_ref': 'a',
                    'content': 'a' * 8,
                    'preview': 'duplicate',
                    'score': 0.7,
                    'source': {'file_id': 'file', 'file_version': 2},
                    'modality': 'image',
                    'asset_id': 'asset',
                    'image_available': True,
                },
                {'evidence_ref': 'b', 'content': 'b' * 8},
            ],
            'legacy_items': [{'content': 'c' * 8, 'source': 'old-note'}],
            'warnings': [],
        }
        saved = copy.deepcopy(original)
        result = self.scope['model_evidence_result'](original, max_chars=10)
        self.assertEqual(sum(len(item['content']) for item in result['items'] + result['legacy_items']), 10)
        self.assertNotIn('preview', result['items'][0])
        self.assertNotIn('score', result['items'][0])
        self.assertNotIn('encoding_space', result)
        self.assertEqual(result['items'][0]['source'], original['items'][0]['source'])
        self.assertEqual(result['items'][0]['asset_id'], 'asset')
        self.assertEqual(result['items'][1]['next_offset'], 2)
        self.assertEqual(result['legacy_items'][0]['next_offset'], 0)
        self.assertEqual(original, saved)

    def test_evidence_offset_continues_without_repeating_previous_content(self):
        result = self.scope['model_evidence_result'](
            {'items': [{'content': '0123456789', 'evidence_ref': 'a'}]}, offset=4, max_chars=3
        )
        self.assertEqual(result['items'][0]['content'], '456')
        self.assertEqual(result['items'][0]['next_offset'], 7)

    def test_numbered_file_pages_preserve_all_original_characters_and_progress(self):
        content = 'first\n' + 'very long line ' * 20 + '\nlast'
        file = model_file(text=content)
        offset, chunks = 0, []
        for _ in range(100):
            page = self.scope['file_content_page'](file, offset=offset, max_chars=25, line_numbers=True)
            self.assertLessEqual(len(page['content']), 25)
            consumed = page.get('returned_chars', len(content) - offset)
            chunks.append(content[offset : offset + consumed])
            if not page.get('truncated'):
                break
            self.assertGreater(page['next_offset'], offset)
            offset = page['next_offset']
        self.assertEqual(''.join(chunks), content)

    def test_line_range_reports_actual_lines_not_requested_unreturned_lines(self):
        page = self.scope['file_content_page'](
            model_file(text='x' * 100 + '\nsecond\nthird'), start_line=1, end_line=3, max_chars=20
        )
        self.assertEqual(page['showing_lines'], '1-1')


if __name__ == '__main__':
    unittest.main()
