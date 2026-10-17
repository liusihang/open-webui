"""Exercise the real isolated state validator and atomic persistence method."""

import ast
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ui', ROOT / 'backend/open_webui/utils/intelligent_ui.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class StateTests(unittest.TestCase):
    def test_valid_state_is_copied(self):
        state = {'version': 1, 'blocks': {'message:item:0': {'slider:value': 42, 'note:open': True}}}
        result = ui.validate_intelligent_ui_state(state)
        self.assertEqual(result, state)
        result['blocks']['message:item:0']['slider:value'] = 0
        self.assertEqual(state['blocks']['message:item:0']['slider:value'], 42)

    def test_rejects_noncontrol_payloads(self):
        invalid = [
            None,
            {},
            {'version': True, 'blocks': {}},
            {'version': 2, 'blocks': {}},
            {'version': 1, 'blocks': {}, 'spec': {}},
            {'version': 1, 'blocks': {'constructor': {}}},
        ]
        for value in ['x' * 257, 'invalid\x00value', None, [], {}, float('nan'), float('inf'), 10**1000]:
            invalid.append({'version': 1, 'blocks': {'b': {'x': value}}})
        invalid.extend(
            [
                {'version': 1, 'blocks': {str(i): {} for i in range(65)}},
                {'version': 1, 'blocks': {'b': {str(i): 1 for i in range(65)}}},
                {'version': 1, 'blocks': {'b': {'__proto__': True}}},
            ]
        )
        for state in invalid:
            with self.subTest(state=str(state)[:150]), self.assertRaises(ValueError):
                ui.validate_intelligent_ui_state(state)

    def test_bounded_selection_strings(self):
        state = {'version': 1, 'blocks': {'b': {'shared:group': 'All', 'shared:selection': ''}}}
        self.assertEqual(ui.validate_intelligent_ui_state(state), state)

    def test_encoded_byte_limit(self):
        state = {'version': 1, 'blocks': {str(i): {('界' * 500) + str(j): 1 for j in range(64)} for i in range(2)}}
        with self.assertRaises(ValueError):
            ui.validate_intelligent_ui_state(state)


class AtomicPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = ast.parse((ROOT / 'backend/open_webui/models/chats.py').read_text())
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'ChatTable')
        method = next(
            n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'update_intelligent_ui_state'
        )
        self.upsert = AsyncMock()
        scope = {
            'deepcopy': deepcopy,
            'ChatMessages': SimpleNamespace(upsert_message=self.upsert),
            'flag_modified': lambda *args: None,
            'ChatModel': type('ChatModel', (), {'model_validate': staticmethod(lambda value: value)}),
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual-state-method>', 'exec'), scope)
        self.method = scope['update_intelligent_ui_state']
        self.item = SimpleNamespace(
            user_id='owner',
            current_message_id='new-branch',
            updated_at=10,
            chat={
                'title': 'Keep title',
                'history': {
                    'currentId': 'new-branch',
                    'messages': {
                        'm': {
                            'id': 'm',
                            'role': 'assistant',
                            'done': True,
                            'content': 'Latest server edit',
                            'output': [{'type': 'message'}],
                            'meta': {'existing': 'keep'},
                        },
                        'new-branch': {'id': 'new-branch', 'role': 'user', 'content': 'New message'},
                    },
                },
            },
        )

        @asynccontextmanager
        async def transaction(_id):
            yield object(), self.item

        self.table = SimpleNamespace(_chat_transaction=transaction)
        self.state = {'version': 1, 'blocks': {'b': {'s:value': 7}}}

    async def test_changes_only_state_without_moving_branch_or_timestamp(self):
        before = deepcopy(self.item.chat)
        result = await self.method(self.table, 'chat', 'm', 'owner', self.state)
        self.assertIs(result, self.item)
        self.assertEqual(self.item.chat['history']['messages']['m']['meta']['intelligent_ui'], self.state)
        del self.item.chat['history']['messages']['m']['meta']['intelligent_ui']
        self.assertEqual(self.item.chat, before)
        self.assertEqual(self.item.current_message_id, 'new-branch')
        self.assertEqual(self.item.updated_at, 10)
        self.upsert.assert_awaited_once()

    async def test_no_deleted_message_recreation(self):
        del self.item.chat['history']['messages']['m']
        self.assertIsNone(await self.method(self.table, 'chat', 'm', 'owner', self.state))
        self.assertNotIn('m', self.item.chat['history']['messages'])
        self.upsert.assert_not_awaited()

    async def test_ownership_role_and_completion_guards(self):
        for patch, caller in [
            ({}, 'other'),
            ({'user_id': 'other'}, 'owner'),
            ({'role': 'user'}, 'owner'),
            ({'done': False}, 'owner'),
        ]:
            with self.subTest(patch=patch, caller=caller):
                original = deepcopy(self.item.chat['history']['messages']['m'])
                self.item.chat['history']['messages']['m'].update(patch)
                self.assertIsNone(await self.method(self.table, 'chat', 'm', caller, self.state))
                self.item.chat['history']['messages']['m'] = original
        self.upsert.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
