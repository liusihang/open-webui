"""Exercise idle-pruning lifecycle against real temporary SQLite transactions.

Only application startup and configuration are replaced. The production ORM
schemas, message reconstruction, transaction context manager, pruning module,
and selected request hooks are executed unchanged. No provider or network calls.

Run: python tests/migration/test_idle_tool_pruning_lifecycle.py
"""

import ast
import asyncio
import copy
import importlib.util
import json
import logging
import sys
import tempfile
import types
import unittest
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import JSON, BigInteger, Boolean, Column, ForeignKey, Index, String, Text, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend/open_webui'
PRUNING = BACKEND / 'utils/idle_tool_pruning.py'
CHATS = BACKEND / 'models/chats.py'
MESSAGES = BACKEND / 'models/chat_messages.py'
MAIN = BACKEND / 'main.py'
MODEL = 'gpt-4o'
BASE_TIME = 10000.0


@lru_cache(maxsize=16)
def source_tree(path):
    return ast.parse(path.read_text())


def load_node(path, name, scope, members=None):
    """Load production declarations without importing the application's startup."""
    node = copy.deepcopy(next(node for node in source_tree(path).body if getattr(node, 'name', None) == name))
    if members is not None:
        node.body = [
            child
            for child in node.body
            if getattr(child, 'name', None) in members
            or isinstance(child, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in members for target in child.targets)
        ]
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope[name]


def module_with(name, **values):
    module = types.ModuleType(name)
    module.__dict__.update(values)
    return module


def function_calls(node, name):
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and (
            isinstance(child.func, ast.Name)
            and child.func.id == name
            or isinstance(child.func, ast.Attribute)
            and child.func.attr == name
        )
    ]


def compile_hook(statements, scope, name='hook'):
    template = ast.parse(f'async def {name}():\n    pass\n').body[0]
    template.body = statements
    exec(compile(ast.fix_missing_locations(ast.Module(body=[template], type_ignores=[])), str(MAIN), 'exec'), scope)
    return scope[name]


def tool_message(mid, parent=None, value=None):
    return {
        'id': mid,
        'parentId': parent,
        'role': 'assistant',
        'content': f'Unchanged answer from {mid}',
        'done': True,
        'timestamp': -999999999,
        'output': [
            {
                'id': f'{mid}-call',
                'type': 'function_call',
                'call_id': f'{mid}-call',
                'name': 'lookup',
                'arguments': '{"query":"keep me"}',
                'status': 'completed',
            },
            {
                'id': f'{mid}-result',
                'type': 'function_call_output',
                'call_id': f'{mid}-call',
                'output': [{'type': 'input_text', 'text': value or ('tool evidence ' * 30)}],
                'status': 'completed',
            },
        ],
    }


class IdleToolPruningLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='idle-pruning-test-')
        self.addCleanup(self.directory.cleanup)
        self.url = f'sqlite+aiosqlite:///{self.directory.name}/chat.db'
        self.engine = create_async_engine(self.url, connect_args={'timeout': 10})
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        base = declarative_base()
        scope = {
            '__name__': __name__,
            'Base': base,
            'JSON': JSON,
            'BigInteger': BigInteger,
            'Boolean': Boolean,
            'Column': Column,
            'ForeignKey': ForeignKey,
            'Index': Index,
            'String': String,
            'Text': Text,
            'select': select,
            'text': text,
            'Optional': Optional,
            'AsyncSession': AsyncSession,
            'asynccontextmanager': asynccontextmanager,
        }
        self.Chat = load_node(CHATS, 'Chat', scope)
        self.ChatMessage = load_node(MESSAGES, 'ChatMessage', scope)

        @asynccontextmanager
        async def get_async_db():
            async with self.sessions() as session:
                yield session

        @asynccontextmanager
        async def get_async_db_context(db=None):
            if db is not None:
                yield db
            else:
                async with get_async_db() as session:
                    yield session

        scope.update(get_async_db=get_async_db, get_async_db_context=get_async_db_context)
        self.chats = load_node(CHATS, 'ChatTable', scope, {'_chat_transaction'})()
        self.messages = load_node(
            MESSAGES,
            'ChatMessageTable',
            scope,
            {'get_messages_map_by_chat_id', 'DB_TO_JSON_KEY_MAP', 'EXCLUDED_COLUMNS'},
        )()
        self.message_list = load_node(BACKEND / 'utils/misc.py', 'get_message_list', {})
        self.config = {
            'chat.idle_tool_pruning.enable': True,
            'chat.idle_tool_pruning.idle_seconds': 1560,
            'chat.idle_tool_pruning.token_budget': 0,
        }
        config = SimpleNamespace(
            get_many=AsyncMock(side_effect=lambda *keys: {key: self.config[key] for key in keys}),
            get=AsyncMock(side_effect=lambda key, default=None: self.config.get(key, default)),
        )
        spec = importlib.util.spec_from_file_location('idle_tool_pruning_under_test', PRUNING)
        self.pruning = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pruning)
        self.modules = {
            'open_webui.models.chats': module_with('open_webui.models.chats', Chats=self.chats, Chat=self.Chat),
            'open_webui.models.chat_messages': module_with(
                'open_webui.models.chat_messages', ChatMessages=self.messages
            ),
            'open_webui.models.config': module_with('open_webui.models.config', Config=config),
            'open_webui.internal.db': module_with('open_webui.internal.db', get_async_db=get_async_db),
            'open_webui.utils.misc': module_with('open_webui.utils.misc', get_message_list=self.message_list),
            'open_webui.utils.idle_tool_pruning': self.pruning,
        }
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        # Replace this module's clock only; do not disturb asyncio/SQLAlchemy.
        self.clock = SimpleNamespace(time=lambda: self.now)
        self.pruning.time = self.clock
        self.now = BASE_TIME
        async with self.engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def seed(self, cid='chat', messages=None, *, legacy=None, updated_at=None, state=None):
        messages = copy.deepcopy(messages if messages is not None else [tool_message('a1')])
        async with self.sessions() as session:
            session.add(
                self.Chat(
                    id=cid,
                    user_id='owner',
                    title='Original title',
                    chat={'history': {'messages': copy.deepcopy(legacy or {})}},
                    updated_at=BASE_TIME if updated_at is None else updated_at,
                    created_at=1,
                    tool_pruning=copy.deepcopy(state),
                )
            )
            reverse = {json_key: db_key for db_key, json_key in self.messages.DB_TO_JSON_KEY_MAP.items()}
            columns = set(self.ChatMessage.__table__.columns.keys())
            for message in messages:
                values = {
                    reverse.get(key, key): value
                    for key, value in message.items()
                    if reverse.get(key, key) in columns and key != 'id'
                }
                session.add(self.ChatMessage(id=f'{cid}-{message["id"]}', chat_id=cid, user_id='owner', **values))
            await session.commit()
        return messages

    async def state(self, cid='chat'):
        async with self.sessions() as session:
            return copy.deepcopy(await session.scalar(select(self.Chat.tool_pruning).where(self.Chat.id == cid)))

    async def transcript(self, cid='chat'):
        async with self.sessions() as session:
            chat = await session.get(self.Chat, cid)
            rows = (await session.scalars(select(self.ChatMessage).where(self.ChatMessage.chat_id == cid))).all()
            return (
                copy.deepcopy(chat.chat),
                [
                    {
                        column.key: copy.deepcopy(getattr(row, column.key))
                        for column in self.ChatMessage.__table__.columns
                    }
                    for row in rows
                ],
            )

    def key(self, message):
        return self.pruning.result_key(message, message['output'][1], 1)

    async def test_idle_threshold_is_strictly_greater_than_1560_seconds(self):
        for suffix, elapsed, expected in [('before', 1559.99, False), ('exact', 1560, False), ('after', 1560.01, True)]:
            with self.subTest(elapsed=elapsed):
                await self.seed(suffix)
                self.now = BASE_TIME + elapsed
                self.assertTrue(await self.pruning.begin_request(suffix, 'a1', MODEL))
                state = await self.state(suffix)
                self.assertEqual(bool(state.get('masks')), expected)
                self.assertEqual(state['last_activity'], self.now)
                self.assertEqual('last_pruned_at' in state, expected)

    async def test_begin_uses_preinsert_server_activity_and_ignores_client_timestamps(self):
        await self.seed()
        self.now = BASE_TIME + 1561
        metadata = {'user_message': {'id': 'new-user', 'parentId': 'a1', 'timestamp': 999999999999}}
        observed = []

        async def insert_chat_turn(cid, user, user_message, message_ids):
            observed.append(await self.state(cid))
            async with self.chats._chat_transaction(cid) as (_, chat):
                chat.updated_at = self.now
            return {'currentId': 'new-assistant', 'messages': {}}

        self.chats.insert_chat_turn = insert_chat_turn
        tree = ast.parse(MAIN.read_text())
        assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
        begin = next(node for node in assignments if function_calls(node, 'begin_request'))
        insert = min(
            (node for node in assignments if node.lineno > begin.lineno and function_calls(node, 'insert_chat_turn')),
            key=lambda node: node.lineno,
        )
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == 'open_webui.utils.idle_tool_pruning'
            and any(alias.name == 'begin_request' for alias in node.names)
        ]
        self.assertLess(begin.lineno, insert.lineno)
        hook = compile_hook(
            [imports[0], begin, insert],
            {
                'metadata': metadata,
                'chat_id': 'chat',
                'assistant_message_id': None,
                'user_message': metadata['user_message'],
                'model': {'info': {}},
                'model_id': MODEL,
                'request': SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))),
                'has_active_tasks': AsyncMock(return_value=False),
                'Chats': self.chats,
                'user': SimpleNamespace(id='owner'),
                'message_ids': [],
            },
        )
        await hook()
        self.assertTrue(metadata['idle_tool_output_pruning'])
        self.assertTrue(observed[0]['masks'], 'Masks must be committed before insert_chat_turn touches updated_at')
        self.assertEqual(observed[0]['last_activity'], self.now)

    async def test_finish_records_completion_instead_of_request_start(self):
        await self.seed()
        self.now = BASE_TIME + 10
        await self.pruning.begin_request('chat', 'a1', MODEL)
        self.now = BASE_TIME + 1000
        await self.pruning.finish_request('chat')
        self.assertEqual((await self.state())['last_activity'], self.now)
        self.now += 1560
        await self.pruning.begin_request('chat', 'a1', MODEL)
        self.assertNotIn('masks', await self.state())
        self.now += 1560.01
        await self.pruning.begin_request('chat', 'a1', MODEL)
        self.assertTrue((await self.state())['masks'])

    async def test_nonconversation_updates_do_not_reset_activity(self):
        await self.seed(state={'last_activity': BASE_TIME})
        self.now = BASE_TIME + 1561
        async with self.chats._chat_transaction('chat') as (_, chat):
            chat.updated_at = self.now
            chat.title = 'Changed by title generation'
            chat.last_read_at = self.now
        await self.pruning.begin_request('chat', 'a1', MODEL)
        self.assertTrue((await self.state())['masks'])

    async def test_disabled_traffic_advances_activity_before_reenable(self):
        original = await self.seed()
        self.config['chat.idle_tool_pruning.enable'] = False
        self.now += 10000
        self.assertFalse(await self.pruning.begin_request('chat', 'a1', MODEL))
        self.assertEqual(await self.state(), {'last_activity': self.now})
        self.now += 20
        await self.pruning.finish_request('chat')
        self.config['chat.idle_tool_pruning.enable'] = True
        self.now += 100
        self.assertTrue(await self.pruning.begin_request('chat', 'a1', MODEL))
        self.assertNotIn('masks', await self.state())
        self.assertEqual(await self.pruning.replay_messages('chat', original), original)
        self.now += 1561
        await self.pruning.begin_request('chat', 'a1', MODEL)
        masks = (await self.state())['masks']
        self.config['chat.idle_tool_pruning.enable'] = False
        self.now += 10000
        await self.pruning.begin_request('chat', 'a1', MODEL)
        self.assertEqual((await self.state())['masks'], masks)
        self.assertEqual((await self.state())['last_activity'], self.now)

    async def test_concurrent_begins_serialize_and_commit_one_plan(self):
        await self.seed()
        self.now += 1561
        with patch.object(self.pruning, 'plan_masks', wraps=self.pruning.plan_masks) as plan:
            results = await asyncio.gather(*[self.pruning.begin_request('chat', 'a1', MODEL) for _ in range(8)])
        self.assertEqual(results, [True] * 8)
        self.assertEqual(plan.call_count, 1, 'The second transaction must see the first transaction activity')
        state = await self.state()
        self.assertEqual(len(state['masks']), 1)
        self.assertEqual(state['last_activity'], self.now)
        self.assertEqual(state['last_pruned_at'], self.now)

    async def test_masks_persist_across_new_database_connections(self):
        original = await self.seed()
        self.now += 1561
        await self.pruning.begin_request('chat', 'a1', MODEL)
        expected = await self.state()
        await self.engine.dispose()
        self.engine = create_async_engine(self.url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.assertEqual(await self.state(), expected)
        replayed = await self.pruning.replay_messages('chat', original)
        self.assertEqual(replayed[0]['output'][1]['output'][0]['text'], self.pruning.PLACEHOLDER)

    async def test_branch_masks_affect_shared_ancestors_without_pruning_siblings(self):
        ancestor = tool_message('ancestor')
        branch_a = tool_message('branch-a', 'ancestor')
        branch_b = tool_message('branch-b', 'ancestor')
        await self.seed(messages=[ancestor, branch_a, branch_b])
        self.now += 1561
        await self.pruning.begin_request('chat', 'branch-a', MODEL)
        state = await self.state()
        self.assertEqual(set(state['masks']), {self.key(ancestor), self.key(branch_a)})
        replayed = await self.pruning.replay_messages('chat', [ancestor, branch_b])
        self.assertEqual(replayed[0]['output'][1]['output'][0]['text'], self.pruning.PLACEHOLDER)
        self.assertEqual(replayed[1], branch_b)
        self.now += 10
        await self.pruning.begin_request('chat', 'branch-b', MODEL)
        self.assertEqual((await self.state())['masks'], state['masks'])
        self.now += 1561
        await self.pruning.begin_request('chat', 'branch-b', MODEL)
        self.assertEqual(
            set((await self.state())['masks']), {self.key(ancestor), self.key(branch_a), self.key(branch_b)}
        )

    async def test_new_turn_falls_back_to_its_existing_parent(self):
        original = await self.seed()
        self.now += 1561
        await self.pruning.begin_request('chat', 'not-inserted-yet', MODEL, parent_id='a1')
        self.assertEqual(set((await self.state())['masks']), {self.key(original[0])})

    async def test_legacy_history_and_normalized_messages_merge_with_rows_authoritative(self):
        legacy_parent = tool_message('legacy-parent')
        stale = tool_message('a1', 'legacy-parent', value='old stale result')
        fresh = tool_message('a1', 'legacy-parent', value='fresh authoritative result')
        await self.seed(messages=[fresh], legacy={'legacy-parent': legacy_parent, 'a1': stale})
        self.now += 1561
        await self.pruning.begin_request('chat', 'a1', MODEL)
        self.assertEqual(set((await self.state())['masks']), {self.key(legacy_parent), self.key(fresh)})
        self.assertNotIn(self.key(stale), (await self.state())['masks'])

    async def test_active_chat_does_not_prune_despite_long_gap(self):
        await self.seed()
        self.now += 10000
        with patch.object(self.pruning, 'plan_masks', wraps=self.pruning.plan_masks) as plan:
            await self.pruning.begin_request('chat', 'a1', MODEL, active=True)
        plan.assert_not_called()
        self.assertEqual(await self.state(), {'last_activity': self.now})

    async def test_explicit_resume_does_not_create_or_change_masks(self):
        for cid, existing in [('unpruned', {}), ('pruned', {'masks': {'existing-result': {'chars': 3}}})]:
            with self.subTest(existing=bool(existing)):
                await self.seed(cid, state=existing)
                self.now = BASE_TIME + 10000
                with patch.object(self.pruning, 'plan_masks', wraps=self.pruning.plan_masks) as plan:
                    self.assertTrue(await self.pruning.begin_request(cid, 'a1', MODEL, resume=True))
                plan.assert_not_called()
                self.assertEqual(await self.state(cid), {**existing, 'last_activity': self.now})

    async def test_done_true_with_pending_item_blocks_the_entire_branch_trigger(self):
        for status in ('pending', 'queued', 'in_progress', 'requires_approval'):
            with self.subTest(status=status):
                ancestor = tool_message('completed-ancestor')
                pending = tool_message('pending', 'completed-ancestor')
                pending['output'][0]['status'] = status
                await self.seed(status, messages=[ancestor, pending])
                self.now = BASE_TIME + 10000
                with patch.object(self.pruning, 'plan_masks', wraps=self.pruning.plan_masks) as plan:
                    await self.pruning.begin_request(status, 'pending', MODEL)
                plan.assert_not_called()
                self.assertEqual(await self.state(status), {'last_activity': self.now})

    async def test_done_false_blocks_the_entire_branch_trigger(self):
        ancestor = tool_message('completed-ancestor')
        pending = tool_message('pending', 'completed-ancestor')
        pending['done'] = False
        await self.seed(messages=[ancestor, pending])
        self.now += 10000
        with patch.object(self.pruning, 'plan_masks', wraps=self.pruning.plan_masks) as plan:
            await self.pruning.begin_request('chat', 'pending', MODEL)
        plan.assert_not_called()
        self.assertEqual(await self.state(), {'last_activity': self.now})

    async def test_unfinished_sibling_does_not_block_completed_branch(self):
        ancestor = tool_message('ancestor')
        completed = tool_message('completed', 'ancestor')
        unfinished = tool_message('unfinished', 'ancestor')
        unfinished['done'] = False
        await self.seed(messages=[ancestor, completed, unfinished])
        self.now += 1561
        await self.pruning.begin_request('chat', 'completed', MODEL)
        self.assertEqual(set((await self.state())['masks']), {self.key(ancestor), self.key(completed)})

    async def test_pruning_and_replay_never_mutate_original_transcripts(self):
        message = tool_message('a1')
        message['contextSummary'] = 'Old summary, retain only in storage'
        message['meta'] = {'native_compaction': {'output': [{'encrypted_content': 'opaque'}]}, 'keep': {'x': [1, 2]}}
        message['output'].append({'type': 'open_webui:context_compaction', 'content': 'Old checkpoint'})
        original = await self.seed(messages=[message], legacy={'a1': message})
        expected_input = copy.deepcopy(original)
        expected_db = await self.transcript()
        self.now += 1561
        await self.pruning.begin_request('chat', 'a1', MODEL)
        replayed = await self.pruning.replay_messages('chat', original)
        await self.pruning.finish_request('chat')
        self.assertEqual(await self.transcript(), expected_db)
        self.assertEqual(original, expected_input)
        self.assertEqual(replayed[0]['content'], message['content'])
        self.assertEqual(replayed[0]['output'][0], message['output'][0])
        self.assertNotIn('contextSummary', replayed[0])
        # Keep the opaque checkpoint available; native dispatch validates its
        # exact masked prefix before using it, independently of text summaries.
        self.assertEqual(replayed[0]['meta']['native_compaction'], message['meta']['native_compaction'])
        replayed[0]['meta']['keep']['x'].append(3)
        self.assertEqual(original, expected_input)

    async def test_planning_error_rolls_back_without_partial_state(self):
        await self.seed(state={'last_activity': BASE_TIME, 'masks': {'existing-result': {'chars': 4}}})
        expected = await self.state()
        self.now += 1561
        with patch.object(self.pruning, 'plan_masks', side_effect=RuntimeError('tokenizer unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'tokenizer unavailable'):
                await self.pruning.begin_request('chat', 'a1', MODEL)
        self.assertEqual(await self.state(), expected)
        # A failed writer must release BEGIN IMMEDIATE's lock as well.
        await asyncio.wait_for(self.pruning.finish_request('chat'), timeout=2)
        self.assertEqual((await self.state())['masks'], expected['masks'])

    async def test_clock_regression_cannot_move_activity_backwards(self):
        await self.seed(state={'last_activity': BASE_TIME + 500})
        await self.pruning.begin_request('chat', 'a1', MODEL)
        await self.pruning.finish_request('chat')
        self.assertEqual((await self.state())['last_activity'], BASE_TIME + 500)

    async def test_missing_chats_are_safe_and_are_not_created(self):
        self.assertTrue(await self.pruning.begin_request('missing', 'a1', MODEL))
        await self.pruning.finish_request('missing')
        self.assertIsNone(await self.state('missing'))
        self.config['chat.idle_tool_pruning.enable'] = False
        self.assertFalse(await self.pruning.begin_request('missing', 'a1', MODEL))

    async def test_main_finally_records_activity_on_cancellation_and_error(self):
        await self.seed()
        tree = ast.parse(MAIN.read_text())
        final_hooks = [
            statement
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            for statement in node.finalbody
            if isinstance(statement, ast.If) and function_calls(statement, 'finish_request')
        ]
        self.assertEqual(len(final_hooks), 1)
        self.assertTrue(function_calls(final_hooks[0], 'shield'))
        for exception in (RuntimeError('request failed'), asyncio.CancelledError()):
            with self.subTest(exception=type(exception).__name__):
                self.now += 100
                wrapper = ast.parse('try:\n    raise failure\nfinally:\n    pass').body[0]
                wrapper.finalbody = final_hooks
                hook = compile_hook(
                    [wrapper],
                    {
                        'failure': exception,
                        'asyncio': asyncio,
                        'metadata': {'chat_id': 'chat'},
                        'is_saved_chat_id': lambda cid: cid == 'chat',
                        'log': logging.getLogger(__name__),
                    },
                )
                with self.assertRaises(type(exception)):
                    await hook()
                self.assertEqual((await self.state())['last_activity'], self.now)

    async def test_middleware_masks_enabled_replay_and_bypasses_disabled_mode(self):
        original = await self.seed()
        self.now += 1561
        await self.pruning.begin_request('chat', 'a1', MODEL)
        tree = source_tree(BACKEND / 'utils/middleware.py')
        # Run the actual metadata-setting assignment and following replay block.
        hook_body = None
        for parent in ast.walk(tree):
            for _, value in ast.iter_fields(parent):
                if not isinstance(value, list):
                    continue
                for index, node in enumerate(value[:-1]):
                    if (
                        isinstance(node, ast.Assign)
                        and any(
                            isinstance(child, ast.Constant) and child.value == 'idle_tool_output_pruning'
                            for child in ast.walk(node)
                        )
                        and isinstance(value[index + 1], ast.If)
                        and function_calls(value[index + 1], 'replay_messages')
                    ):
                        hook_body = [node, value[index + 1]]
        self.assertIsNotNone(hook_body)
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.config['chat.idle_tool_pruning.enable'] = enabled
                form_data = {'messages': copy.deepcopy(original)}
                before = form_data['messages']
                metadata = {}
                hook = compile_hook(
                    hook_body,
                    {
                        'metadata': metadata,
                        'form_data': form_data,
                        'chat_id': 'chat',
                        'is_saved_chat_id': lambda cid: cid == 'chat',
                        'Config': self.modules['open_webui.models.config'].Config,
                    },
                )
                await hook()
                self.assertEqual(metadata['idle_tool_output_pruning'], enabled)
                self.assertEqual(before, original)
                if enabled:
                    self.assertEqual(metadata['_idle_tool_pruning_masks'], (await self.state())['masks'])
                    self.assertIsNot(form_data['messages'], before)
                    self.assertEqual(
                        form_data['messages'][0]['output'][1]['output'][0]['text'], self.pruning.PLACEHOLDER
                    )
                else:
                    self.assertNotIn('_idle_tool_pruning_masks', metadata)
                    self.assertIs(form_data['messages'], before)

    async def test_replay_metadata_masks_resumed_raw_output_without_mutating_it(self):
        original = await self.seed()
        self.now += 1561
        await self.pruning.begin_request('chat', 'a1', MODEL)
        metadata = {'idle_tool_output_pruning': True, 'assistant_message_id': 'a1', 'message_id': 'other-id'}
        await self.pruning.replay_messages('chat', original, metadata)
        self.assertEqual(metadata['_idle_tool_pruning_masks'], (await self.state())['masks'])
        new_items = tool_message('fresh')['output']
        raw_output = copy.deepcopy(original[0]['output']) + new_items
        expected_original = copy.deepcopy(raw_output)
        request_output = self.pruning.mask_current_output(raw_output, metadata)
        self.assertEqual(request_output[1]['output'][0]['text'], self.pruning.PLACEHOLDER)
        self.assertEqual(request_output[2:], new_items)
        self.assertEqual(raw_output, expected_original)
        # A subsequent provider request re-masks the unchanged stored output.
        self.assertEqual(self.pruning.mask_current_output(raw_output, metadata), request_output)

    async def test_sqlite_replay_preserves_matching_text_checkpoint_and_rejects_changed_masks(self):
        message = tool_message('a1')
        masks = {self.key(message): {'chars': 4}}
        signature = self.pruning.pruning_signature([message], masks)
        message['contextSummary'] = 'summary of the masked transcript'
        message['meta'] = {'context_summary_pruning': signature}
        message['output'].append(
            {
                'type': 'open_webui:context_compaction',
                'call_id': 'a1-call',
                'summary': 'same safe summary',
                'pruning': signature,
            }
        )
        original = await self.seed(messages=[message], state={'last_activity': BASE_TIME, 'masks': masks})
        metadata = {}
        replay = await self.pruning.replay_messages('chat', original, metadata)
        self.assertEqual(replay[0]['contextSummary'], message['contextSummary'])
        self.assertEqual(replay[0]['output'][-1]['pruning'], signature)
        self.assertEqual(metadata['_idle_tool_pruning_signature'], signature)
        before = await self.transcript()
        self.now += 1561
        await self.pruning.begin_request('chat', 'a1', MODEL)
        changed = await self.pruning.replay_messages('chat', original, metadata)
        self.assertNotIn('contextSummary', changed[0])
        self.assertFalse(any(item['type'] == 'open_webui:context_compaction' for item in changed[0]['output']))
        self.assertNotEqual(metadata['_idle_tool_pruning_signature'], signature)
        self.assertEqual(await self.transcript(), before)


class IdleToolPruningMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_only_change_the_pruning_column(self):
        base = declarative_base()
        scope = {**globals(), 'Base': base}
        # Build the real pre-migration Chat table by excluding the new column.
        schema = copy.deepcopy(next(node for node in source_tree(CHATS).body if getattr(node, 'name', None) == 'Chat'))
        schema.body = [
            node
            for node in schema.body
            if not (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'tool_pruning' for target in node.targets)
            )
        ]
        exec(compile(ast.Module(body=[schema], type_ignores=[]), str(CHATS), 'exec'), scope)
        migration_path = BACKEND / 'migrations/versions/72c3a9d1e5b0_add_tool_pruning_state.py'
        spec = importlib.util.spec_from_file_location('idle_tool_pruning_migration_under_test', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        payload = json.dumps(
            {'history': {'messages': {'a1': tool_message('a1', value='Original evidence: 中文 😀')}}},
            ensure_ascii=False,
            indent=2,
        )
        with tempfile.TemporaryDirectory(prefix='idle-pruning-migration-') as directory:
            engine = sa.create_engine(f'sqlite:///{directory}/migration.db')
            try:
                base.metadata.create_all(engine)
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            'INSERT INTO chat (id, user_id, title, chat, created_at, updated_at) '
                            'VALUES (:id, :owner, :title, :payload, 100, 200)'
                        ),
                        {'id': 'existing-chat', 'owner': 'owner', 'title': 'Original title', 'payload': payload},
                    )
                    before_columns = sa.inspect(connection).get_columns('chat')
                    before_names = [column['name'] for column in before_columns]
                    before_indexes = json.dumps(sa.inspect(connection).get_indexes('chat'), sort_keys=True, default=str)
                    snapshot = text('SELECT ' + ', '.join(before_names) + ' FROM chat ORDER BY id')
                    original = connection.execute(snapshot).all()
                    with Operations.context(MigrationContext.configure(connection)):
                        migration.upgrade()
                        after_columns = sa.inspect(connection).get_columns('chat')
                        self.assertEqual([column['name'] for column in after_columns], [*before_names, 'tool_pruning'])
                        pruning_column = after_columns[-1]
                        self.assertTrue(pruning_column['nullable'])
                        self.assertIsInstance(pruning_column['type'], sa.JSON)
                        self.assertIsNone(connection.scalar(text('SELECT tool_pruning FROM chat')))
                        self.assertEqual(connection.execute(snapshot).all(), original)
                        self.assertEqual(
                            json.dumps(sa.inspect(connection).get_indexes('chat'), sort_keys=True, default=str),
                            before_indexes,
                        )
                        connection.execute(
                            text('UPDATE chat SET tool_pruning = :state WHERE id = :id'),
                            {'id': 'existing-chat', 'state': json.dumps({'last_activity': 999, 'masks': {}})},
                        )
                        migration.downgrade()
                    self.assertEqual(
                        [column['name'] for column in sa.inspect(connection).get_columns('chat')], before_names
                    )
                    self.assertEqual(connection.execute(snapshot).all(), original)
                    self.assertEqual(connection.scalar(text('SELECT chat FROM chat')), payload)
                    self.assertEqual(
                        json.dumps(sa.inspect(connection).get_indexes('chat'), sort_keys=True, default=str),
                        before_indexes,
                    )
            finally:
                engine.dispose()


class IdleToolPruningConfigTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = BACKEND / 'routers/chats.py'
        keys = next(
            node
            for node in source_tree(path).body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'CHAT_CONFIG_KEYS' for target in node.targets)
        )
        self.stored = {
            'chat.idle_tool_pruning.enable': False,
            'chat.idle_tool_pruning.idle_seconds': 900,
            'chat.idle_tool_pruning.token_budget': 123,
        }
        self.config = SimpleNamespace(
            upsert=AsyncMock(side_effect=lambda values: self.stored.update(copy.deepcopy(values))),
            get_many=AsyncMock(side_effect=lambda *keys: {key: self.stored[key] for key in keys if key in self.stored}),
        )
        scope = {
            '__name__': __name__,
            'BaseModel': BaseModel,
            'Field': Field,
            'Config': self.config,
            'CHAT_CONFIG_KEYS': ast.literal_eval(keys.value),
            'CONTEXT_COMPACTION_TOKEN_THRESHOLD': 80000,
            'CONTEXT_COMPACTION_RETENTION_PERCENTAGE': 40,
            'Depends': lambda dependency: None,
            'get_admin_user': lambda: None,
            'router': SimpleNamespace(post=lambda *args, **kwargs: lambda function: function),
        }
        self.Form = load_node(path, 'ChatConfigForm', scope)
        load_node(path, 'chat_config_updates', scope)
        load_node(path, 'get_chat_config_values', scope)
        self.set_config = load_node(path, 'set_chat_config', scope)
        self.legacy_payload = {
            'ENABLE_CONTEXT_COMPACTION': True,
            'CONTEXT_COMPACTION_PROMPT_TEMPLATE': 'Old client prompt',
        }

    async def test_old_client_omitting_new_fields_preserves_existing_settings(self):
        existing = copy.deepcopy(self.stored)
        form = self.Form.model_validate(self.legacy_payload)
        self.assertTrue(form.ENABLE_IDLE_TOOL_OUTPUT_PRUNING)
        self.assertEqual(form.IDLE_TOOL_OUTPUT_PRUNING_IDLE_SECONDS, 1560)
        self.assertEqual(form.IDLE_TOOL_OUTPUT_PRUNING_TOKEN_BUDGET, 4000)
        result = await self.set_config(form)
        updates = self.config.upsert.await_args.args[0]
        self.assertFalse(any(key.startswith('chat.idle_tool_pruning.') for key in updates))
        self.assertEqual({key: self.stored[key] for key in existing}, existing)
        self.assertFalse(result['ENABLE_IDLE_TOOL_OUTPUT_PRUNING'])
        self.assertEqual(result['IDLE_TOOL_OUTPUT_PRUNING_IDLE_SECONDS'], 900)
        self.assertEqual(result['IDLE_TOOL_OUTPUT_PRUNING_TOKEN_BUDGET'], 123)

    async def test_explicit_fields_update_and_zero_budget_is_valid(self):
        result = await self.set_config(
            self.Form.model_validate(
                {
                    **self.legacy_payload,
                    'ENABLE_IDLE_TOOL_OUTPUT_PRUNING': True,
                    'IDLE_TOOL_OUTPUT_PRUNING_IDLE_SECONDS': 42,
                    'IDLE_TOOL_OUTPUT_PRUNING_TOKEN_BUDGET': 0,
                }
            )
        )
        self.assertTrue(self.stored['chat.idle_tool_pruning.enable'])
        self.assertEqual(self.stored['chat.idle_tool_pruning.idle_seconds'], 42)
        self.assertEqual(self.stored['chat.idle_tool_pruning.token_budget'], 0)
        self.assertEqual(result['IDLE_TOOL_OUTPUT_PRUNING_TOKEN_BUDGET'], 0)

    async def test_partial_explicit_update_does_not_reset_other_pruning_fields(self):
        await self.set_config(
            self.Form.model_validate(
                {
                    **self.legacy_payload,
                    'IDLE_TOOL_OUTPUT_PRUNING_TOKEN_BUDGET': 321,
                }
            )
        )
        updates = self.config.upsert.await_args.args[0]
        self.assertNotIn('chat.idle_tool_pruning.enable', updates)
        self.assertNotIn('chat.idle_tool_pruning.idle_seconds', updates)
        self.assertFalse(self.stored['chat.idle_tool_pruning.enable'])
        self.assertEqual(self.stored['chat.idle_tool_pruning.idle_seconds'], 900)
        self.assertEqual(self.stored['chat.idle_tool_pruning.token_budget'], 321)

    async def test_invalid_negative_budget_and_zero_idle_are_rejected_before_write(self):
        for field, value in [
            ('IDLE_TOOL_OUTPUT_PRUNING_TOKEN_BUDGET', -1),
            ('IDLE_TOOL_OUTPUT_PRUNING_IDLE_SECONDS', 0),
        ]:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError) as error:
                    self.Form.model_validate({**self.legacy_payload, field: value})
                self.assertEqual(error.exception.errors()[0]['loc'], (field,))
        self.config.upsert.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
