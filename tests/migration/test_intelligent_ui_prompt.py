"""Pure presentation-contract tests; no application, database or model calls."""

import importlib.util
import ast
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / 'backend/open_webui/utils/intelligent_ui.py'
SPEC = importlib.util.spec_from_file_location('intelligent_ui_prompt', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IntelligentUIPromptTests(unittest.TestCase):
    def test_requires_explicit_boolean_opt_in(self):
        for value in [
            None,
            [],
            True,
            {},
            {'intelligent_ui': False},
            {'intelligent_ui': 'true'},
            {'intelligent_ui': 'false'},
            {'intelligent_ui': 1},
        ]:
            with self.subTest(value=value):
                self.assertIsNone(MODULE.get_intelligent_ui_prompt(value))
        self.assertEqual(MODULE.get_intelligent_ui_prompt({'intelligent_ui': True}), MODULE.INTELLIGENT_UI_PROMPT)

    def test_no_mutation_of_features(self):
        features = {'intelligent_ui': True, 'memory': False}
        original = features.copy()
        MODULE.get_intelligent_ui_prompt(features)
        self.assertEqual(features, original)

    def test_catalog_and_fallback_documented(self):
        prompt = MODULE.INTELLIGENT_UI_PROMPT
        for component in [
            'Stack',
            'Grid',
            'Tabs',
            'Chart',
            'ComparisonCards',
            'Explanation',
            'ParameterSlider',
            'Select',
            'NumberInput',
            'DataTable',
            'MetricCard',
        ]:
            self.assertIn(component, prompt)
        self.assertIn('fallback', prompt)
        self.assertIn('never calls the model', prompt)
        self.assertIn('multiple UI blocks may alternate', prompt)

    def test_request_hook_appends_without_replacing_system_instructions(self):
        root = PATH.parents[2]
        source = (root / 'open_webui/utils/middleware.py').read_text()
        tree = ast.parse(source)
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'get_intelligent_ui_prompt'
        ]
        self.assertEqual(len(calls), 1)
        additions = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == 'add_or_update_system_message'
            and n.args
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id == 'ui_prompt'
        ]
        self.assertEqual(len(additions), 1)
        self.assertTrue(
            any(
                k.arg == 'append' and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in additions[0].keywords
            )
        )

    def test_interface_setting_has_typed_storage_field(self):
        source = (PATH.parents[2] / 'open_webui/models/users.py').read_text()
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'InterfaceSettings')
        field = next(
            n
            for n in cls.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == 'intelligentUI'
        )
        self.assertEqual(ast.unparse(field.annotation), 'bool | None')


if __name__ == '__main__':
    unittest.main()
