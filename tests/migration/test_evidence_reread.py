"""Offline real-method regression for explicit evidence reads after search.

Execute the production service class without importing application/SQL startup.
Only authorization and stored-row boundaries are mocked; read/search/dedup use
the real implementations. SQL visibility and grants need test_rag_core.py.
"""

import ast
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

SOURCE = Path(__file__).resolve().parents[2] / 'backend/open_webui/retrieval/evidence.py'


def service_types(resolve_scope):
    tree = ast.parse(SOURCE.read_text())
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in {'ResolvedScope', 'EvidenceService'}
    ]
    namespace = {'dataclass': dataclass, 'resolve_scope': resolve_scope}
    exec(compile(ast.Module(body=classes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['EvidenceService'], namespace['ResolvedScope']


class EvidenceRereadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resolve_scope = AsyncMock()
        service, scope = service_types(self.resolve_scope)
        self.scope = scope(('kb',), ())
        self.resolve_scope.return_value = self.scope
        self.user = SimpleNamespace(id='reader', role='user')
        self.service = service(embedding=object(), vector_client=object())
        self.seen = set()
        self.rows = {
            ref: {
                'evidence_ref': ref,
                'citation_key': 'citation-' + ref,
                'content': 'stored ' + ref,
                'modality': 'text',
                'status': 'ready',
            }
            for ref in ('a', 'b')
        }

        async def rows(scope, *, refs=None, **kwargs):
            # A resolved scope is not itself an enduring authorization grant.
            return {ref: self.rows[ref] for ref in refs if ref in self.rows and scope.knowledge_ids == ('kb',)}

        self.service._rows = AsyncMock(side_effect=rows)
        self.service._item = lambda parts, scope: dict(parts)
        self.service._groups = AsyncMock(return_value=({}, ['a']))
        self.service.lexical_enabled = True
        self.service._rankings = AsyncMock(return_value=([('a', 1.0)], []))

    async def read(self, refs, **kwargs):
        return await self.service.read(refs, user=self.user, resolved_scope=self.scope, seen_refs=self.seen, **kwargs)

    async def search(self):
        return await self.service.search('stored text', user=self.user, resolved_scope=self.scope, seen_refs=self.seen)

    async def test_search_then_read_same_reference_returns_content(self):
        found = await self.search()
        self.assertEqual([item['evidence_ref'] for item in found['items']], ['a'])
        self.assertIn('a', self.seen)
        result = await self.read(['a'])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['content'], 'stored a')
        self.assertEqual(result['warnings'], [])

    async def test_repeated_explicit_read_remains_available(self):
        first = await self.read(['a'])
        self.assertEqual(await self.read(['a']), first)

    async def test_duplicate_input_references_merge_in_first_seen_order(self):
        result = await self.read(['b', 'a', 'b', 'a'])
        self.assertEqual([item['evidence_ref'] for item in result['items']], ['b', 'a'])
        self.service._rows.assert_awaited_once_with(self.scope, refs=['b', 'a'])

    async def test_read_without_shared_history_keeps_the_same_contract(self):
        result = await self.service.read(['a', 'a'], user=self.user, resolved_scope=self.scope)
        self.assertEqual(result, {'items': [self.rows['a']], 'warnings': []})

    async def test_mixed_seen_new_and_unavailable_references_keep_order(self):
        self.seen.update(('a', 'citation-b', 'old-version'))
        result = await self.read(['a', 'old-version', 'b', 'missing', 'a'])
        self.assertEqual([item['evidence_ref'] for item in result['items']], ['a', 'old-version', 'b', 'missing'])
        self.assertEqual([item['status'] for item in result['items']], ['ready', 'unavailable', 'ready', 'unavailable'])
        self.assertEqual(result['warnings'], [])

    async def test_citation_aliases_merge_within_one_read_only(self):
        self.rows['b']['citation_key'] = self.rows['a']['citation_key']
        self.seen.add(self.rows['a']['citation_key'])
        result = await self.read(['b', 'a'])
        self.assertEqual([item['evidence_ref'] for item in result['items']], ['b'])

    async def test_read_records_refs_for_later_search_dedup(self):
        await self.read(['a'])
        self.assertTrue({'a', 'citation-a'} <= self.seen)
        self.assertEqual((await self.search())['items'], [])

    async def test_repeated_search_still_deduplicates(self):
        self.assertEqual(len((await self.search())['items']), 1)
        self.assertEqual((await self.search())['items'], [])

    async def test_revalidation_can_remove_scope_after_previous_read(self):
        await self.read(['a'])
        self.resolve_scope.return_value = type(self.scope)()
        result = await self.read(['a'])
        self.assertEqual(result['items'], [{'evidence_ref': 'a', 'status': 'unavailable'}])
        self.resolve_scope.assert_awaited_with(self.user, knowledge_ids=('kb',), file_ids=(), db=None)

    async def test_authorization_failure_prevents_row_access_even_when_seen(self):
        self.seen.add('a')
        self.resolve_scope.side_effect = PermissionError('authenticated user is required')
        with self.assertRaises(PermissionError):
            await self.read(['a'])
        self.service._rows.assert_not_awaited()

    async def test_unresolved_scope_is_rejected_before_row_access(self):
        with self.assertRaises(TypeError):
            await self.service.read(['a'], user=self.user, resolved_scope='kb', seen_refs=self.seen)
        self.service._rows.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
