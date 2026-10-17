"""Offline upgrade graph and integration seam checks, without app startup."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class UpgradeMergeTests(unittest.TestCase):
    def test_migrations_join_without_rewriting_existing_fork_revision(self):
        revisions = {}
        for path in (ROOT / 'backend/open_webui/migrations/versions').glob('*.py'):
            values = {}
            for node in ast.parse(path.read_text()).body:
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                    if isinstance(node, ast.AnnAssign)
                    else []
                )
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in ('revision', 'down_revision'):
                        values[target.id] = ast.literal_eval(node.value)
            if 'revision' in values:
                revisions[values['revision']] = values['down_revision']
        parents = {
            parent
            for value in revisions.values()
            for parent in (value if isinstance(value, tuple) else [value])
            if parent
        }
        self.assertEqual(set(revisions) - parents, {'d41e823f01ac'})
        self.assertEqual(revisions['c92e41b7d630'], 'b8f2c6d91a04')
        self.assertEqual(set(revisions['d41e823f01ac']), {'0bd252b206fb', 'c92e41b7d630'})
        self.assertTrue(parents <= set(revisions))

    def test_evidence_citations_in_shared_tool_allowlist(self):
        path = ROOT / 'backend/open_webui/utils/middleware.py'
        tree = ast.parse(path.read_text())
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'CITATION_TOOL_NAMES' for target in node.targets)
        )
        self.assertIn('view_knowledge_evidence', ast.literal_eval(assignment.value))


if __name__ == '__main__':
    unittest.main()
