"""Pure presentation-contract tests; no application, database or model calls."""

import importlib.util
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

PATH = Path(__file__).resolve().parents[2] / 'backend/open_webui/utils/intelligent_ui.py'
SPEC = importlib.util.spec_from_file_location('intelligent_ui_prompt', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IntelligentUIPromptTests(unittest.TestCase):
    def test_user_experience_is_an_explicit_presentation_reason(self):
        opening = MODULE.INTELLIGENT_UI_PROMPT.split('\nOUTPUT\n')[0]
        self.assertIn('materially enhances the user experience', opening)
        self.assertIn('comparison, editing or exploration; otherwise use Markdown.', opening)
        self.assertIn("Honor the user's\nrequested format.", opening)

    def test_selection_starts_from_the_users_task_without_waiting_for_component_requests(self):
        policy = ' '.join(MODULE.INTELLIGENT_UI_PROMPT.split('\nOUTPUT\n')[0].split())
        self.assertIn("Choose by the user's task", policy)
        self.assertIn('Users need not request a component', policy)
        for task in (
            'trends, distributions or numeric relationships',
            'steps, structure or relationships',
            'Compare options',
            'complete draft',
            'Explore parameter changes',
            'Find, sort or filter data',
        ):
            self.assertIn(task, policy)

    def test_finished_writing_and_edit_explanations_have_separate_places(self):
        policy = ' '.join(MODULE.INTELLIGENT_UI_PROMPT.split('\nOUTPUT\n')[0].split())
        self.assertIn('complete draft to copy, edit or send', policy)
        self.assertIn('paper rewrite', policy)
        self.assertIn('WritingBlock for the draft', policy)
        self.assertIn('explanations, edit rationale and discussion outside', policy)

    def test_controls_need_a_real_effect_and_static_views_do_not_need_buttons(self):
        policy = ' '.join(MODULE.INTELLIGENT_UI_PROMPT.split('\nOUTPUT\n')[0].split())
        self.assertIn('controls that actually change the result', policy)
        self.assertIn('Do not add purposeless controls', policy)
        self.assertIn('Static charts and diagrams need no controls', policy)
        self.assertIn('Short facts, brief explanations and confirmations', policy)

    def test_comparison_data_lookup_and_useful_prose_are_not_duplicated(self):
        policy = ' '.join(MODULE.INTELLIGENT_UI_PROMPT.split('\nOUTPUT\n')[0].split())
        self.assertIn('tables for exact attributes', policy)
        self.assertIn('visual differences', policy)
        self.assertIn('Find, sort or filter data: DataTable', policy)
        self.assertIn('small simple tables use Markdown', policy)
        self.assertIn('Give the useful answer directly', policy)
        self.assertIn('not a second copy of the content', policy)

    def test_free_native_color_contract_is_explicit_and_bounded(self):
        prompt = MODULE.INTELLIGENT_UI_PROMPT
        for text in ('Choose colors freely', 'optional starting points', 'color must not be the only meaning',
                     'Color is "#RGB" or "#RRGGBB"', 'appearance?', 'spans?', 'categoryColors:',
                     'point > categoryColors > series', 'cell > row > column > table',
                     'never displayed row indexes', 'Invalid dataset colors are ignored'):
            self.assertIn(text, prompt)

    def test_requires_administrator_boolean_not_request_features(self):
        for value in [
            None,
            [],
            False,
            {},
            {'intelligent_ui': False},
            {'intelligent_ui': 'true'},
            {'intelligent_ui': 'false'},
            {'intelligent_ui': 1},
            {'intelligent_ui': True},
            'true',
            1,
        ]:
            with self.subTest(value=value):
                self.assertIsNone(MODULE.get_intelligent_ui_prompt(value))
        self.assertEqual(MODULE.get_intelligent_ui_prompt(True), MODULE.INTELLIGENT_UI_PROMPT)

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

    def test_personal_setting_and_request_flag_removed(self):
        source = (PATH.parents[2] / 'open_webui/models/users.py').read_text()
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'InterfaceSettings')
        fields = [
            n
            for n in cls.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == 'intelligentUI'
        ]
        self.assertEqual(fields, [])
        root = PATH.parents[3]
        for name in ['src/lib/components/common/InterfaceSettings.svelte', 'src/lib/components/chat/Chat.svelte']:
            self.assertNotIn('intelligentUI', (root / name).read_text())
        self.assertNotIn('intelligentUI?:', (root / 'src/lib/stores/index.ts').read_text())
        admin_source = (root / 'src/lib/components/admin/Settings/General.svelte').read_text()
        self.assertIn('bind:state={adminConfig.ENABLE_INTELLIGENT_UI}', admin_source)
        self.assertIn("label={$i18n.t('Intelligent UI')}", admin_source)
        self.assertIn('config.set(await getBackendConfig())', admin_source)
        source = (root / 'src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte').read_text()
        self.assertIn('$config?.features?.enable_intelligent_ui === true', source)
        self.assertNotIn('$settings?.intelligentUI', source)


class RequestGatingTests(unittest.IsolatedAsyncioTestCase):
    """Execute the actual feature-stage body, without initializing the application."""

    def setUp(self):
        root = PATH.parents[2]
        tree = ast.parse((root / 'open_webui/utils/middleware.py').read_text())
        method = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'process_chat_payload')
        start = next(i for i, n in enumerate(method.body) if ast.unparse(n).startswith('features = form_data.pop('))
        end = next(i for i, n in enumerate(method.body) if ast.unparse(n).startswith('tool_ids = form_data.pop('))
        wrapper = ast.parse('async def stage(form_data):\n    pass').body[0]
        wrapper.body = method.body[start:end]
        helper_tree = ast.parse((root / 'open_webui/utils/misc.py').read_text())
        helpers = [
            n
            for n in helper_tree.body
            if isinstance(n, ast.FunctionDef)
            and n.name
            in {
                'add_or_update_system_message',
                'update_message_content',
                'get_system_message',
                'get_content_from_message',
                'merge_system_messages',
            }
        ]
        self.config = AsyncMock(return_value=True)
        self.scope = {
            'Config': SimpleNamespace(get=self.config),
            'extra_params': {},
            'get_intelligent_ui_prompt': MODULE.get_intelligent_ui_prompt,
            'metadata': {},
            'context_summary': None,
        }
        helpers.extend(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in {'context_summary_message', 'apply_context_summary'}
        )
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=helpers + [wrapper], type_ignores=[])), str(PATH), 'exec'
            ),
            self.scope,
        )

    async def test_administrator_setting_controls_all_requests_including_old_clients(self):
        for enabled in [False, True]:
            for request_features in [
                None,
                {},
                {'intelligent_ui': False},
                {'intelligent_ui': True},
                {'intelligent_ui': 'true'},
            ]:
                for personal_value in [False, True]:
                    with self.subTest(enabled=enabled, request=request_features, personal=personal_value):
                        self.config.reset_mock()
                        self.config.return_value = enabled
                        payload = {
                            'messages': [
                                {'role': 'system', 'content': 'Keep the original instruction.'},
                                {'role': 'user', 'content': 'Explain.'},
                            ],
                            'ui': {'intelligentUI': personal_value},
                        }
                        if request_features is not None:
                            payload['features'] = deepcopy(request_features)
                        await self.scope['stage'](payload)
                        self.config.assert_awaited_once_with('ui.enable_intelligent_ui')
                        content = payload['messages'][0]['content']
                        self.assertTrue(content.startswith('Keep the original instruction.'))
                        self.assertEqual(MODULE.INTELLIGENT_UI_PROMPT in content, enabled)
                        self.assertEqual(payload['messages'][1], {'role': 'user', 'content': 'Explain.'})

    async def test_enabled_stage_creates_system_message_when_absent(self):
        payload = {'messages': [{'role': 'user', 'content': 'Explain.'}]}
        await self.scope['stage'](payload)
        self.assertEqual(payload['messages'][0], {'role': 'system', 'content': MODULE.INTELLIGENT_UI_PROMPT})


if __name__ == '__main__':
    unittest.main()
