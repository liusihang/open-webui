"""Exercise shipped terminal routing against an isolated HTTP server; never contact Live."""

import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import quote

try:
    from aiohttp import resolver, web
except ImportError:
    web = None

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'backend/open_webui/utils/terminals.py'


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@unittest.skipIf(web is None, 'Requires the runtime aiohttp dependency')
class TerminalReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Match env.py's default threaded resolver without importing app startup.
        for name in ('aiohttp.resolver.DefaultResolver', 'aiohttp.connector.DefaultResolver'):
            resolver_patch = patch(name, resolver.ThreadedResolver)
            resolver_patch.start()
            self.addCleanup(resolver_patch.stop)
        self.ready = False
        self.delay = 0.06
        self.health_status = 200
        self.calls = []

        async def handle(request):
            self.calls.append((request.path, dict(request.headers)))
            if not self.ready:
                await asyncio.sleep(self.delay)
                self.ready = True
            return web.json_response({'status': 'ok'}, status=self.health_status)

        app = web.Application()
        app.router.add_get('/{path:.*}', handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.addAsyncCleanup(self.runner.cleanup)
        self.connection = {
            'id': 'managed',
            'url': f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}',
            'policy_id': 'private',
            'config': {'contexts': {'chat': {'context_id': 'chat_id'}}},
        }
        self.config = SimpleNamespace(get=AsyncMock(return_value=[self.connection]))
        self.access = AsyncMock(return_value=True)
        self.emitter = AsyncMock()
        self.user = SimpleNamespace(id='user-a')
        self.metadata = {'terminal_id': 'managed', 'chat_id': 'chat-a'}

        async def headers(connection, request, user, **kwargs):
            return {'Authorization': f'Bearer fixture-{user.id}'}, {}

        self.modules = patch.dict(
            sys.modules,
            {
                'fastapi': SimpleNamespace(HTTPException=HTTPException),
                'open_webui.env': SimpleNamespace(
                    AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL=True, AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA=0.02
                ),
                'open_webui.models.config': SimpleNamespace(Config=self.config),
                'open_webui.models.groups': SimpleNamespace(
                    Groups=SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[]))
                ),
                'open_webui.models.users': SimpleNamespace(UserModel=SimpleNamespace),
                'open_webui.utils.access_control': SimpleNamespace(has_connection_access=self.access),
                'open_webui.utils.tools': SimpleNamespace(build_tool_server_headers=headers),
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.scope = {
            'asyncio': asyncio,
            'quote': quote,
            'ENABLE_TOOL_SERVERS': True,
            'is_saved_chat_id': lambda chat_id: bool(chat_id),
            'log': SimpleNamespace(warning=Mock()),
        }
        names = {
            'is_terminal_orchestrator',
            'get_terminal_server_url',
            'terminal_context_config',
            'terminal_context_available',
            'terminal_context_id',
            'get_terminal_json',
            'prepare_terminal',
        }
        tree = ast.parse(SOURCE.read_text())
        selected = [
            node
            for node in tree.body
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id.startswith('TERMINAL_') for target in node.targets)
            )
            or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ]
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), self.scope)
        self.assertEqual(self.scope['TERMINAL_STARTUP_TIMEOUT'], 60)
        self.assertEqual(self.scope['TERMINAL_STARTUP_NOTICE_DELAY'], 1)
        # Scale the same timeout relationship down for fast, deterministic CI.
        self.scope['TERMINAL_STARTUP_TIMEOUT'] = 0.3
        self.scope['TERMINAL_STARTUP_NOTICE_DELAY'] = 0.005

    async def prepare(self):
        return await self.scope['prepare_terminal'](
            object(), self.user, self.metadata, {'__event_emitter__': self.emitter}
        )

    async def test_cold_start_longer_than_data_timeout_succeeds_before_metadata_read(self):
        await self.prepare()
        result = await self.scope['get_terminal_json'](object(), self.user, self.metadata, '/skills')
        self.assertEqual(result, {'status': 'ok'})
        self.assertEqual([path for path, _ in self.calls], ['/p/private/health', '/p/private/skills'])
        states = [call.args[0]['data'] for call in self.emitter.await_args_list]
        self.assertEqual([state['done'] for state in states], [False, True])
        self.assertEqual([state['error'] for state in states], [False, False])

    async def test_original_short_metadata_timeout_still_reproduces_without_readiness(self):
        with self.assertRaises(TimeoutError):
            await self.scope['get_terminal_json'](object(), self.user, self.metadata, '/skills')

    async def test_warm_terminal_does_not_add_status_history(self):
        self.ready = True
        self.scope['TERMINAL_STARTUP_NOTICE_DELAY'] = 1
        await self.prepare()
        self.emitter.assert_not_awaited()

    async def test_production_budget_allows_observed_eleven_second_cold_start(self):
        self.delay = 11
        self.scope['TERMINAL_STARTUP_TIMEOUT'] = 60
        self.scope['TERMINAL_STARTUP_NOTICE_DELAY'] = 1
        sys.modules['open_webui.env'].AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = 10
        await self.prepare()
        result = await self.scope['get_terminal_json'](object(), self.user, self.metadata, '/skills')
        self.assertEqual(result, {'status': 'ok'})
        self.assertEqual([call.args[0]['data']['done'] for call in self.emitter.await_args_list], [False, True])

    async def test_startup_timeout_is_bounded_explicit_and_finishes_pending_status(self):
        self.scope['TERMINAL_STARTUP_TIMEOUT'] = 0.02
        with self.assertRaises(HTTPException) as caught:
            await self.prepare()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('timed out', caught.exception.detail)
        self.assertEqual(self.emitter.await_args_list[-1].args[0]['data']['error'], True)
        self.assertEqual(self.emitter.await_args_list[-1].args[0]['data']['done'], True)

    async def test_unhealthy_terminal_is_not_advertised_as_ready(self):
        self.health_status = 503
        with self.assertRaises(HTTPException) as caught:
            await self.prepare()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertTrue(caught.exception.detail)
        self.assertEqual(self.emitter.await_args_list[-1].args[0]['data']['error'], True)

    async def test_cancellation_propagates_and_cleans_up_pending_status_task(self):
        pending = asyncio.Event()

        async def emit(event):
            if not event['data']['done']:
                pending.set()

        self.emitter.side_effect = emit
        task = asyncio.create_task(self.prepare())
        await asyncio.wait_for(pending.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.emitter.await_args_list[-1].args[0]['data']['done'])
        self.assertTrue(self.emitter.await_args_list[-1].args[0]['data']['error'])

    async def test_status_transport_failure_does_not_abort_readiness(self):
        self.emitter.side_effect = RuntimeError('socket disconnected')
        await self.prepare()
        self.assertTrue(self.ready)
        self.scope['log'].warning.assert_called()

    async def test_permissions_and_disabled_connection_prevent_network_access(self):
        for denied in ('access', 'disabled', 'context'):
            with self.subTest(denied=denied):
                self.access.return_value = denied != 'access'
                self.connection['enabled'] = denied != 'disabled'
                if denied == 'context':
                    self.connection['config']['contexts']['chat'] = False
                with self.assertRaises(HTTPException):
                    await self.prepare()
        self.assertEqual(self.calls, [])

    async def test_direct_terminal_keeps_existing_behavior(self):
        self.connection.pop('policy_id')
        await self.prepare()
        self.assertEqual(self.calls, [])

    async def test_two_users_keep_separate_identity_and_context_without_shared_readiness_cache(self):
        self.ready = True
        await self.prepare()
        self.user = SimpleNamespace(id='user-b')
        self.metadata['chat_id'] = 'chat-b'
        await self.prepare()
        self.assertEqual([headers['X-User-Id'] for _, headers in self.calls], ['user-a', 'user-b'])
        self.assertEqual([headers['X-Session-Id'] for _, headers in self.calls], ['chat-a', 'chat-b'])
        self.assertEqual(
            [headers['X-Terminal-Context-Id'] for _, headers in self.calls], ['chat:chat-a', 'chat:chat-b']
        )
        self.assertEqual(
            [headers['Authorization'] for _, headers in self.calls], ['Bearer fixture-user-a', 'Bearer fixture-user-b']
        )


if __name__ == '__main__':
    unittest.main()
