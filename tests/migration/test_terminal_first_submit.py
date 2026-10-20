"""First-submit regressions through the actual payload assembly, without a live DB or provider."""

import asyncio
import copy
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'terminal_submit_fixture', ROOT / 'tests/migration/test_system_prompt_order.py'
)
PROMPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMPT)


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class TerminalFirstSubmitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = PROMPT.PromptOrderTests('test_full_actual_payload_and_all_provider_role_paths')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.s['HTTPException'] = HTTPException
        self.case.s['log'].warning = Mock()
        self.case.values['terminal_server.connections'] = [{'id': 'managed', 'policy_id': 'private'}]
        self.case.model['info']['meta']['builtinTools']['skills'] = True
        self.case.sources = []
        self.ready = False
        self.sequence = []

        async def prepare(*args):
            self.sequence.append('prepare')
            self.ready = True

        async def read(*args, **kwargs):
            self.sequence.append('skills')
            if not self.ready:
                raise TimeoutError()
            return []

        self.terminal = PROMPT.module(
            'open_webui.utils.terminals',
            prepare_terminal=AsyncMock(side_effect=prepare),
            get_terminal_json=AsyncMock(side_effect=read),
            get_terminal_skill=AsyncMock(),
            format_terminal_skill_context=lambda *args: 'SELECTED_SKILL',
            format_terminal_skill_manifest_entry=lambda *args: 'SKILL_MANIFEST',
            get_terminal_agents_md=AsyncMock(return_value=None),
            add_terminal_agents_md=lambda messages, *args: messages,
        )
        self.modules = patch.dict(
            sys.modules,
            {
                'open_webui.utils.terminals': self.terminal,
                'open_webui.models.skills': PROMPT.module(
                    'open_webui.models.skills', Skills=SimpleNamespace(get_skills=AsyncMock(return_value=[]))
                ),
                'open_webui.utils.skill_files': PROMPT.module(
                    'open_webui.utils.skill_files', format_skill_content=lambda *args: '', skill_overview=AsyncMock()
                ),
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.case.s['get_terminal_tools'] = AsyncMock(
            return_value=(
                {
                    'run_command': {
                        'type': 'terminal',
                        'spec': {'name': 'run_command', 'parameters': {'type': 'object'}},
                    }
                },
                'STATIC_TERMINAL_PROMPT',
            )
        )

    async def assemble(self, *, terminal='managed', skills=None, tools=None):
        body = {'model': 'm', 'messages': [{'role': 'user', 'content': 'Hello'}]}
        if terminal:
            body['terminal_id'] = terminal
        if skills:
            body['skill_ids'] = skills
        if tools is not None:
            body['tools'] = copy.deepcopy(tools)
        return await self.case.s['process_chat_payload'](
            self.case.request,
            body,
            self.case.user,
            {'chat_id': '', 'session_id': 'browser', 'params': {}},
            self.case.model,
        )

    async def test_first_submit_waits_for_terminal_before_skill_discovery(self):
        body, _, _, _ = await self.assemble()
        self.assertEqual(self.sequence, ['prepare', 'skills'])
        self.assertEqual([tool['function']['name'] for tool in body['tools']], ['run_command'])
        self.assertIn('STATIC_TERMINAL_PROMPT', body['messages'][0]['content'])

    async def test_optional_skill_timeout_does_not_abort_chat_or_remove_terminal_tools(self):
        self.terminal.get_terminal_json.side_effect = TimeoutError()
        body, _, _, _ = await self.assemble()
        self.assertEqual([tool['function']['name'] for tool in body['tools']], ['run_command'])
        self.assertIn('STATIC_TERMINAL_PROMPT', body['messages'][0]['content'])

    async def test_selected_skill_timeout_is_explicit_and_stops_model_dispatch(self):
        self.terminal.get_terminal_json.side_effect = TimeoutError()
        with self.assertRaises(HTTPException) as caught:
            await self.assemble(skills=['terminal:selected'])
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('skill', caught.exception.detail.lower())
        self.assertTrue(caught.exception.detail.strip())
        self.case.s['get_terminal_tools'].assert_not_awaited()

    async def test_missing_selected_skill_is_not_silently_omitted(self):
        with self.assertRaises(HTTPException) as caught:
            await self.assemble(skills=['terminal:missing'])
        self.assertEqual(caught.exception.status_code, 503)
        self.terminal.get_terminal_skill.assert_not_awaited()

    async def test_cold_and_warm_submissions_keep_identical_prompt_and_tools(self):
        cold, _, _, _ = await self.assemble()
        warm, _, _, _ = await self.assemble()
        self.assertEqual(cold['messages'], warm['messages'])
        self.assertEqual(cold['tools'], warm['tools'])
        self.assertNotIn('terminal_startup', str(cold['messages']))

    async def test_no_terminal_does_not_prepare_one(self):
        await self.assemble(terminal=None)
        self.terminal.prepare_terminal.assert_not_awaited()
        self.terminal.get_terminal_json.assert_not_awaited()

    async def test_explicit_api_tools_do_not_start_terminal(self):
        body, _, _, _ = await self.assemble(tools=[])
        self.assertEqual(body['tools'], [])
        self.terminal.prepare_terminal.assert_not_awaited()
        self.terminal.get_terminal_json.assert_not_awaited()

    async def test_disabled_terminal_capability_does_not_start_terminal(self):
        self.case.model['info']['meta']['capabilities'] = {'terminal': False}
        await self.assemble()
        self.terminal.prepare_terminal.assert_not_awaited()
        self.terminal.get_terminal_json.assert_not_awaited()

    async def test_terminal_is_prepared_even_when_skills_are_disabled(self):
        self.case.model['info']['meta']['builtinTools']['skills'] = False
        body, _, _, _ = await self.assemble()
        self.terminal.prepare_terminal.assert_awaited_once()
        self.terminal.get_terminal_json.assert_not_awaited()
        self.assertEqual([tool['function']['name'] for tool in body['tools']], ['run_command'])

    async def test_terminal_readiness_failure_is_not_silently_ignored(self):
        self.terminal.prepare_terminal.side_effect = HTTPException(503, 'Terminal preparation timed out.')
        with self.assertRaisesRegex(HTTPException, 'Terminal preparation timed out'):
            await self.assemble()
        self.terminal.get_terminal_json.assert_not_awaited()
        self.case.s['get_terminal_tools'].assert_not_awaited()

    async def test_skill_discovery_cancellation_is_not_swallowed(self):
        self.terminal.get_terminal_json.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.assemble()


if __name__ == '__main__':
    unittest.main()
