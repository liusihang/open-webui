"""Run shipped terminal resolution functions with only network/DB seams replaced."""

import ast
import asyncio
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(os.environ.get('OWUI_TEST_ROOT', Path(__file__).resolve().parents[2]))
SOURCE = ROOT / 'backend/open_webui/utils/tools.py'


class TerminalSessionDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = {
            'id': 'session',
            'url': 'http://terminals.internal',
            'auth_type': 'session',
            'policy_id': 'private-policy',
            'name': 'Session Terminal',
            'enabled': True,
        }
        self.direct = {
            'id': 'direct',
            'url': 'http://direct.internal',
            'auth_type': 'bearer',
            'key': 'static-direct-key',
            'enabled': True,
        }
        self.connections = [self.session, self.direct]
        self.network = []
        self.calls = []

        async def discover(configs):
            result = []
            for config in configs:
                self.network.append(copy.deepcopy(config))
                credential = config.get('key')
                identity = {'session-JWT-A': 'user-a', 'session-JWT-B': 'user-b', 'static-direct-key': 'direct'}.get(
                    credential, 'anonymous'
                )
                result.append(
                    {
                        'id': config['info']['id'],
                        'url': config['url'],
                        'openapi': {'paths': {}},
                        'specs': [
                            {
                                'name': 'run_command',
                                'description': 'schema-' + identity,
                                'parameters': {'type': 'object', 'properties': {}},
                            }
                        ],
                        'info': {'title': 'Terminal'},
                    }
                )
            return result

        async def execute(**kwargs):
            self.calls.append(kwargs)
            return {'ok': True}, None

        async def wrap(function, extra):
            return function

        def identity(headers):
            return {'Bearer session-JWT-A': 'user-a', 'Bearer session-JWT-B': 'user-b'}.get(
                headers.get('Authorization'), 'direct'
            )

        async def cwd(url, headers, cookies):
            return '/home/' + identity(headers)

        async def prompt(url, headers, cookies=None):
            return 'prompt-' + identity(headers)

        self.cache = AsyncMock(
            return_value=[
                {
                    'id': 'direct',
                    'url': self.direct['url'],
                    'openapi': {},
                    'specs': [
                        {
                            'name': 'run_command',
                            'description': 'cached-direct',
                            'parameters': {'type': 'object', 'properties': {}},
                        }
                    ],
                }
            ]
        )
        self.state = SimpleNamespace(
            TERMINAL_SERVERS=[{'id': 'unchanged-shared-cache'}], redis=SimpleNamespace(set=AsyncMock())
        )
        self.scope = dict(
            Any=Any,
            Request=object,
            UserModel=object,
            asyncio=asyncio,
            Config=SimpleNamespace(get=AsyncMock(side_effect=lambda key, default=None: self.connections)),
            Groups=SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
            has_connection_access=AsyncMock(return_value=True),
            ENABLE_TOOL_SERVERS=True,
            get_terminal_servers=self.cache,
            get_tool_servers_data=AsyncMock(side_effect=discover),
            get_terminal_server_url=lambda conn: (
                conn['url'] + ('/p/' + conn['policy_id'] if conn.get('policy_id') else '')
            ),
            normalize_bearer_token=lambda token: str(token or '').removeprefix('Bearer ').strip(),
            bearer_auth_header=lambda token: (
                {'Authorization': 'Bearer ' + str(token).removeprefix('Bearer ').strip()} if token else {}
            ),
            terminal_context_available=lambda conn, context: True,
            terminal_context_id=lambda conn, meta, context: None,
            terminal_context_config=lambda conn, context: {},
            TERMINAL_CONTEXT_HEADER='X-Terminal-Context-Id',
            get_terminal_cwd=AsyncMock(side_effect=cwd),
            get_terminal_system_prompt=AsyncMock(side_effect=prompt),
            add_terminal_display_file_inline_param=lambda spec: copy.deepcopy(spec),
            clean_openai_tool_schema=lambda spec: spec,
            get_async_tool_function_and_apply_extra_params=AsyncMock(side_effect=wrap),
            execute_tool_server=AsyncMock(side_effect=execute),
            JSONCodec=SimpleNamespace(dumps=json.dumps, loads=json.loads),
            REDIS_KEY_PREFIX='test',
            log=SimpleNamespace(error=Mock()),
        )
        tree = ast.parse(SOURCE.read_text())
        tree.body = [
            x
            for x in tree.body
            if isinstance(x, ast.AsyncFunctionDef) and x.name in ('get_terminal_tools', 'set_terminal_servers')
        ]
        exec(compile(tree, str(SOURCE), 'exec'), self.scope)

    def request(self, credential='session-JWT-A'):
        token = SimpleNamespace(credentials=credential) if credential is not None else None
        return SimpleNamespace(state=SimpleNamespace(token=token), app=SimpleNamespace(state=self.state), cookies={})

    async def resolve(self, user='user-a', credential='session-JWT-A', terminal='session'):
        return await self.scope['get_terminal_tools'](
            self.request(credential), terminal, SimpleNamespace(id=user), {'__metadata__': {'chat_id': 'chat-' + user}}
        )

    async def test_two_users_fetch_separate_schema_prompt_and_command_credentials(self):
        frozen = copy.deepcopy(self.state.TERMINAL_SERVERS)
        first, prompt_a = await self.resolve()
        second, prompt_b = await self.resolve('user-b', 'session-JWT-B')
        self.assertEqual((prompt_a, prompt_b), ('prompt-user-a', 'prompt-user-b'))
        self.assertIn('schema-user-a', first['run_command']['spec']['description'])
        self.assertIn('/home/user-a', first['run_command']['spec']['description'])
        self.assertIn('schema-user-b', second['run_command']['spec']['description'])
        self.assertEqual([x['key'] for x in self.network], ['session-JWT-A', 'session-JWT-B'])
        self.assertEqual(self.state.TERMINAL_SERVERS, frozen)
        self.cache.assert_not_awaited()
        self.state.redis.set.assert_not_awaited()
        await first['run_command']['callable'](command='true')
        await second['run_command']['callable'](command='true')
        self.assertEqual(
            [x['headers']['Authorization'] for x in self.calls], ['Bearer session-JWT-A', 'Bearer session-JWT-B']
        )
        self.assertEqual([x['headers']['X-User-Id'] for x in self.calls], ['user-a', 'user-b'])
        self.assertEqual([x['headers']['X-Session-Id'] for x in self.calls], ['chat-user-a', 'chat-user-b'])
        for resolved in (first, second):
            schema = json.dumps([x['spec'] for x in resolved.values()])
            self.assertNotIn('session-JWT-A', schema)
            self.assertNotIn('session-JWT-B', schema)

    async def test_missing_credentials_rejected_before_discovery_or_cache(self):
        for credential in (None, '', '   '):
            with self.assertRaisesRegex(RuntimeError, 'session credentials'):
                await self.resolve(credential=credential)
        self.scope['get_tool_servers_data'].assert_not_awaited()
        self.cache.assert_not_awaited()

    async def test_startup_skips_session_and_never_caches_private_prompt(self):
        result = await self.scope['set_terminal_servers'](self.request(None))
        self.assertEqual([x['id'] for x in result], ['direct'])
        self.assertEqual([x['info']['id'] for x in self.network], ['direct'])
        self.state.redis.set.assert_awaited_once()
        serialized = self.state.redis.set.await_args.args[1]
        self.assertNotIn('session-JWT', serialized)
        self.assertNotIn('prompt-user-', serialized)
        self.assertNotIn('static-direct-key', serialized)

    async def test_bearer_direct_still_uses_existing_shared_schema(self):
        tools, prompt = await self.resolve(terminal='direct')
        self.cache.assert_awaited_once()
        self.scope['get_tool_servers_data'].assert_not_awaited()
        self.assertIn('cached-direct', tools['run_command']['spec']['description'])
        await tools['run_command']['callable'](command='true')
        self.assertEqual(self.calls[0]['headers']['Authorization'], 'Bearer static-direct-key')

    async def test_permissions_win_before_network(self):
        self.scope['has_connection_access'].return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Access denied'):
            await self.resolve()
        self.scope['get_tool_servers_data'].assert_not_awaited()
        self.cache.assert_not_awaited()

    async def test_disabled_server_still_refuses(self):
        self.session['enabled'] = False
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            await self.resolve()
        self.scope['get_tool_servers_data'].assert_not_awaited()

    async def test_discovery_failure_is_not_replaced_by_another_user_cache(self):
        self.scope['get_tool_servers_data'].return_value = []
        self.scope['get_tool_servers_data'].side_effect = None
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            await self.resolve()
        self.cache.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
