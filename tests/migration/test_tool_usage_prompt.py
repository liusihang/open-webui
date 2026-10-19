"""Actual payload assembly and continuations; no DB, model or network requests."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'tool_prompt_fixture', ROOT / 'tests/migration/test_system_prompt_order.py'
)
PROMPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMPT)
MARKER = 'TOOL_USAGE_TOKEN'


class ToolUsagePromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = PROMPT.PromptOrderTests('test_full_actual_payload_and_all_provider_role_paths')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    async def test_native_resolved_tools_get_one_policy_in_tool_section(self):
        body, metadata = await self.case.assemble()
        text = body['messages'][0]['content']
        self.case.assert_order(
            text,
            [
                'BASE_TOKEN',
                'UI_TOKEN',
                'SUMMARY_TOKEN',
                'MEMORY_TOKEN',
                'TOOLS_TOKEN',
                MARKER,
                'KNOWLEDGE_TOKEN',
                'RAG_TOKEN',
            ],
        )
        self.assertEqual(metadata['base_system_prompt'].count(MARKER), 1)
        self.assertNotIn(MARKER, metadata['_context_summary_prompt']['prefix'])
        self.assertNotIn(MARKER, str(self.case.db_messages))

    async def test_chat_and_responses_providers_keep_policy_and_tool_order(self):
        for responses, model in [(True, 'gpt-6.1-sol'), (True, 'gpt-4.1'), (False, 'non-gpt-chat')]:
            with self.subTest(responses=responses, model=model):
                self.case.s['uses_responses_api'].return_value = responses
                body, _ = await self.case.assemble()
                payload = self.case.serialize(body, model=model, responses=responses)
                self.assertEqual(json.dumps(payload).count(MARKER), 1)
                self.assertEqual(
                    [t.get('name') or t['function']['name'] for t in payload['tools']],
                    ['z_tool', 'a_tool'],
                )

    async def test_no_resolved_tools_does_not_advertise_tool_use(self):
        body, _ = await self.case.assemble(tools=False, knowledge=False)
        self.assertNotIn(MARKER, json.dumps(body['messages']))

    async def test_explicit_api_tools_opt_out_of_builtin_policy(self):
        for tools in [[], [{'type': 'function', 'function': {'name': 'caller_tool'}}]]:
            with self.subTest(tools=tools):
                body, _, _, _ = await self.case.s['process_chat_payload'](
                    self.case.request,
                    {
                        'model': 'm',
                        'messages': [{'role': 'user', 'content': 'API request'}],
                        'tools': copy.deepcopy(tools),
                    },
                    self.case.user,
                    {'chat_id': '', 'session_id': 'session'},
                    self.case.model,
                )
                self.assertEqual(body['tools'], tools)
                self.assertNotIn(MARKER, json.dumps(body['messages']))

    async def test_legacy_tool_selector_is_not_given_native_policy(self):
        self.case.s['chat_completion_tools_handler'] = AsyncMock(side_effect=lambda request, body, *args: (body, {}))
        body, _, _, _ = await self.case.s['process_chat_payload'](
            self.case.request,
            {'model': 'm', 'messages': [{'role': 'user', 'content': 'Legacy request'}]},
            self.case.user,
            {
                'chat_id': '',
                'session_id': 'session',
                'params': {'function_calling': 'legacy'},
                'tool_servers': [{'specs': [{'name': 'legacy_tool', 'parameters': {'type': 'object'}}]}],
            },
            self.case.model,
        )
        self.assertNotIn(MARKER, json.dumps(body['messages']))
        self.case.s['chat_completion_tools_handler'].assert_awaited_once()

    async def test_builtin_tools_without_attached_knowledge_get_policy(self):
        self.case.s['get_builtin_tools'].return_value = {
            'get_current_timestamp': {'spec': {'name': 'get_current_timestamp', 'parameters': {'type': 'object'}}}
        }
        body, _ = await self.case.assemble(tools=False, knowledge=False, rag=False)
        self.assertEqual(body['messages'][0]['content'].count(MARKER), 1)
        self.assertNotIn('KNOWLEDGE_TOKEN', body['messages'][0]['content'])

    async def test_tool_source_rebuild_and_summary_updates_do_not_repeat_policy(self):
        for responses in [True, False]:
            with self.subTest(responses=responses):
                self.case.s['uses_responses_api'].return_value = responses
                body, metadata = await self.case.assemble()
                scope = self.case.continuation(body, metadata, responses=responses)
                for iteration in range(2):
                    scope['latest_summary'] = f'SUMMARY_{iteration}'
                    scope['tool_call_sources'].append(
                        {
                            'source': {'id': f'tool{iteration}', 'name': f'SOURCE_{iteration}'},
                            'document': ['evidence'],
                            'metadata': [{}],
                        }
                    )
                    result = await scope['run']()
                    self.assertEqual(result['messages'][0]['content'].count(MARKER), 1)
                    self.assertLess(
                        result['messages'][0]['content'].index(MARKER),
                        result['messages'][0]['content'].index('KNOWLEDGE_TOKEN'),
                    )

    async def test_static_guidance_and_definitions_do_not_change_with_user_text(self):
        first, _ = await self.case.assemble(
            history=[{'id': 'u1', 'role': 'user', 'content': 'First question'}], rag=False
        )
        second, _ = await self.case.assemble(
            history=[{'id': 'u1', 'role': 'user', 'content': 'Change the author name'}], rag=False
        )
        self.assertEqual(first['messages'][0]['content'], second['messages'][0]['content'])
        self.assertEqual(first['tools'], second['tools'])
        self.assertEqual(first['messages'][0]['content'].count(MARKER), 1)

    async def test_real_policy_is_short_static_and_allows_required_rereads(self):
        tree = ast.parse((ROOT / 'backend/open_webui/utils/tools.py').read_text())
        policy = next(
            (
                ast.literal_eval(n.value)
                for n in tree.body
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'TOOL_USAGE_PROMPT' for t in n.targets)
            ),
            None,
        )
        self.assertIsInstance(policy, str)
        self.assertLess(len(policy), 1400)
        for phrase in ('current context', 'wording', 'semantic search', 'compaction', 'untrusted'):
            self.assertIn(phrase, policy)
        self.assertNotIn('file_id', policy)
        self.case.s['TOOL_USAGE_PROMPT'] = policy
        body, _ = await self.case.assemble()
        self.assertEqual(body['messages'][0]['content'].count(policy), 1)
        payload = self.case.serialize(body)
        self.assertEqual(payload['input'][0]['content'][0]['text'].count(policy), 1)

    async def test_progress_guidance_is_selective_public_and_uses_native_phases(self):
        tree = ast.parse((ROOT / 'backend/open_webui/utils/tools.py').read_text())
        policy = next(
            ast.literal_eval(n.value)
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'TOOL_USAGE_PROMPT' for t in n.targets)
        )
        for phrase in (
            'think privately',
            'brief public plan before acting',
            'multi-step or long tool work',
            'meaningful findings',
            'skip routine calls',
            'group parallel work',
            'not private reasoning or sensitive data',
            'Where supported',
            'native commentary',
            'final_answer',
            'never print phase markers',
            'otherwise use brief text',
            'Answer simple questions directly',
        ):
            self.assertIn(phrase, policy)
        self.assertNotIn('before each', policy)
        self.case.s['TOOL_USAGE_PROMPT'] = policy
        for responses, model in ((True, 'gpt-6-luna'), (False, 'non-gpt-chat')):
            with self.subTest(responses=responses):
                body, _ = await self.case.assemble()
                payload = self.case.serialize(body, model=model, responses=responses)
                self.assertEqual(json.dumps(payload).count(json.dumps(policy)[1:-1]), 1)
                # A static instruction is not an unsupported Chat API parameter,
                # nor a fabricated phase on historical assistant messages.
                self.assertNotIn('phase', payload)
                for item in payload.get('input', payload.get('messages', [])):
                    self.assertNotIn('phase', item)

    def test_deployed_default_prompt_requires_private_planning_then_public_plan(self):
        prompt = (ROOT / 'docs/default-system-prompt.md').read_text().strip()
        self.assertTrue(prompt.startswith('你是与我平等协作的资深科学家'))
        for phrase in (
            '必须先在内部思考',
            '在首次执行或调用工具之前',
            '用 commentary 简要说明目标和计划，再开始执行',
            '不展示私有推理或敏感信息',
            '其他模型用一小段普通文字说明计划',
            '简单问题直接回答',
        ):
            self.assertIn(phrase, prompt)
        self.assertEqual(prompt.count('必须先在内部思考'), 1)


if __name__ == '__main__':
    unittest.main()
