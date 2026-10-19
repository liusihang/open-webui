"""Execute built-in payload assembly, continuation and provider serialization with fixtures.

Only DB, retrieval, permissions and provider/task boundaries are replaced. No app
startup, persistent DB, real model request or stream transport is used.
"""

import ast
import collections.abc
import copy
import html
import importlib.util
import json
import re
import sys
import types
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
UTILS = ROOT / 'backend/open_webui/utils'
SPEC = importlib.util.spec_from_file_location(
    'native_fixture', ROOT / 'tests/migration/test_responses_native_history.py'
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


async def identity_body(body, *args, **kwargs):
    return body


class User:
    id = 'fixture-user'
    role = 'admin'
    variables = {}

    def model_dump(self):
        return {'id': self.id, 'role': self.role}


def module(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    return result


class PromptOrderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.s = FIXTURE.scope()
        self.values = {
            # These fixtures exercise the legacy compaction and prompt pipeline.
            # Enabled idle-tool pruning has its own integration fixtures.
            'chat.idle_tool_pruning.enable': False,
            'ui.enable_intelligent_ui': True,
            'task.voice.prompt.enable': True,
            'task.voice.prompt_template': 'VOICE_TOKEN',
            'memories.enable': True,
            'memories.system_context.enable': True,
            'models.default_params': {'system': 'GLOBAL_TOKEN'},
        }
        self.model_params = {'system': 'MODEL_TOKEN'}
        self.memory = 'MEMORY_TOKEN'
        self.sources = [
            {'source': {'id': 'source', 'name': 'file'}, 'document': ['RAG_TOKEN'], 'metadata': [{'source': 'source'}]}
        ]
        self.db_messages = [{'id': 'u1', 'role': 'user', 'content': 'USER_TOKEN', 'contextSummary': 'SUMMARY_TOKEN'}]
        self.errors = Mock()
        self.emitter = AsyncMock()
        self.s.update(
            Any=Any,
            Request=object,
            UserModel=User,
            html=html,
            re=re,
            collections=collections,
            Callable=Callable,
            log=SimpleNamespace(debug=Mock(), info=Mock(), exception=self.errors),
            JSONCodec=SimpleNamespace(dumps=json.dumps, loads=json.loads, JSONDecodeError=json.JSONDecodeError),
            Config=SimpleNamespace(
                get=AsyncMock(side_effect=lambda key, default=None: self.values.get(key, default)),
                get_many=AsyncMock(return_value={}),
            ),
            Models=SimpleNamespace(
                get_model_by_id=AsyncMock(
                    side_effect=lambda mid: SimpleNamespace(
                        params=SimpleNamespace(model_dump=lambda: self.model_params)
                    )
                )
            ),
            Chats=SimpleNamespace(
                get_chat_by_id=AsyncMock(return_value=SimpleNamespace(user_id=User.id, meta={})),
                get_chat_folder_id=AsyncMock(return_value='folder'),
                upsert_message_to_chat_by_id_and_message_id=AsyncMock(),
            ),
            Folders=SimpleNamespace(
                get_folder_by_id=AsyncMock(
                    return_value=SimpleNamespace(id='folder', data={'system_prompt': 'FOLDER_TOKEN'})
                )
            ),
            get_owner_accessible_folder_files=AsyncMock(return_value=[]),
            is_saved_chat_id=lambda cid: cid == 'saved',
            load_messages_from_db=AsyncMock(side_effect=lambda *args: copy.deepcopy(self.db_messages)),
            uses_responses_api=AsyncMock(return_value=True),
            prepare_native_pdf_attachments=AsyncMock(return_value=[]),
            convert_url_images_to_base64=identity_body,
            get_event_emitter=AsyncMock(return_value=self.emitter),
            get_event_call=AsyncMock(return_value=None),
            get_system_oauth_token=AsyncMock(return_value=None),
            get_task_model_id=lambda mid, *args: mid,
            process_pipeline_inlet_filter=AsyncMock(side_effect=lambda request, body, *args: body),
            ENABLE_FUNCTIONS=False,
            ENABLE_TOOL_SERVERS=True,
            DEFAULT_VOICE_MODE_PROMPT_TEMPLATE='DEFAULT_VOICE_TOKEN',
            TOOL_USAGE_PROMPT='TOOL_USAGE_TOKEN',
            get_intelligent_ui_prompt=lambda enabled: 'UI_TOKEN' if enabled is True else None,
            apply_skills_create_prompt=lambda *args, **kwargs: False,
            has_prior_user_message=lambda msgs: True,
            extract_skill_ids_from_messages=lambda msgs: set(),
            get_attached_knowledge=lambda *args: [{'id': 'KNOWLEDGE_TOKEN', 'type': 'collection'}],
            get_builtin_tools=AsyncMock(return_value={}),
            apply_tool_search=AsyncMock(return_value=set()),
            chat_completion_files_handler=AsyncMock(
                side_effect=lambda req, body, *args: (body, {'sources': self.sources})
            ),
            filter_source_metadata=lambda meta: {},
            RAG_SYSTEM_CONTEXT=True,
            rag_template=AsyncMock(side_effect=lambda template, context, prompt: 'RAG_TEMPLATE\n' + context),
            render_chat_variables=lambda text, *args, **kwargs: text,
            render_user_variables=lambda text, *args, **kwargs: text,
            prompt_variables_template=lambda text, *args: text,
            prompt_template=AsyncMock(side_effect=lambda text, *args: text),
            Memories=SimpleNamespace(
                get_memories_by_user_id=AsyncMock(
                    side_effect=lambda uid: [
                        SimpleNamespace(id='mem', type='user', path='', updated_at=1, content=self.memory)
                    ]
                )
            ),
            memory_path_hints=lambda *args: [],
            MEMORY_CONTEXT_OPEN='<memory_context>',
            MEMORY_CONTEXT_CLOSE='</memory_context>',
        )
        for name in (
            'get_content_from_message',
            'get_system_message',
            'get_last_user_message',
            'get_last_user_message_item',
            'set_last_user_message_content',
            'update_message_content',
            'add_or_update_system_message',
            'add_or_update_user_message',
            'replace_system_message_content',
            'deep_update',
        ):
            FIXTURE.load_function(FIXTURE.MISC, name, self.s)
        for name in (
            'resolve_system_prompt',
            'apply_system_prompt_to_body',
            'apply_params_to_form_data',
            'remove_open_webui_params',
            'apply_model_params_to_body',
            'apply_model_params_to_body_openai',
        ):
            FIXTURE.load_function(UTILS / 'payload.py', name, self.s)
        for name in ('model_allows_memory', 'memory_label', 'add_memory_context'):
            FIXTURE.load_function(UTILS / 'memory.py', name, self.s)
        FIXTURE.load_function(UTILS / 'skills.py', 'strip_skill_mentions', self.s)
        skills_tree = ast.parse((UTILS / 'skills.py').read_text())
        regexes = [
            n
            for n in skills_tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in {'SKILL_ID_RE', 'SKILL_MENTION_STRIP_RE'} for t in n.targets)
        ]
        exec(compile(ast.Module(body=regexes, type_ignores=[]), str(UTILS / 'skills.py'), 'exec'), self.s)
        for name in (
            'context_summary_message',
            'apply_context_summary',
            'prepare_responses_system_prompt',
            'process_chat_payload',
            'get_source_context',
            'apply_source_context_to_messages',
            'get_tool_loop_output_messages',
            'get_reasoning_format',
            'add_file_context',
        ):
            FIXTURE.load_function(FIXTURE.MIDDLEWARE, name, self.s)
        for name in ('is_openai_new_model', 'openai_reasoning_model_handler'):
            FIXTURE.load_function(FIXTURE.ROUTER, name, self.s)
        self.s.update(
            _load_config=AsyncMock(return_value={'enable': True, 'token_threshold': 80000, 'token_cap': 80000}),
            _exceeds_token_threshold=lambda *args: False,
        )
        for name in (
            '_apply_latest_summary_checkpoint',
            '_parse_positive_int',
            '_resolve_token_threshold',
            'compact_messages_for_request',
        ):
            FIXTURE.load_function(UTILS / 'context_compaction.py', name, self.s)
        self.native = AsyncMock(return_value=False)
        self.modules = patch.dict(
            sys.modules,
            {
                'open_webui.utils.native_compaction': module(
                    'open_webui.utils.native_compaction', uses_native_compaction=self.native
                ),
                'open_webui.retrieval.integration': module(
                    'open_webui.retrieval.integration', authorized_query_images=AsyncMock(return_value=[])
                ),
                'open_webui.routers.memories': module(
                    'open_webui.routers.memories',
                    QueryMemoryForm=lambda **kwargs: kwargs,
                    query_memory=AsyncMock(return_value=None),
                ),
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.model = {'id': 'm', 'info': {'meta': {'builtinTools': {'skills': False}}}}
        self.request = SimpleNamespace(
            state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={'m': self.model}))
        )
        self.user = User()

    async def assemble(
        self,
        *,
        base='BASE_TOKEN',
        summary=True,
        folder=True,
        voice=True,
        memory=True,
        tools=True,
        knowledge=True,
        rag=True,
        params=None,
        saved=True,
        history=None,
    ):
        self.sources = self.sources if rag else []
        self.model['info']['meta']['builtinTools']['knowledge'] = knowledge
        self.s['Folders'].get_folder_by_id.return_value = SimpleNamespace(
            id='folder', data={'system_prompt': 'FOLDER_TOKEN'} if folder else {}
        )
        self.db_messages = [
            {
                'id': 'u1',
                'role': 'user',
                'content': 'USER_TOKEN',
                **({'contextSummary': 'SUMMARY_TOKEN'} if summary else {}),
            }
        ]
        if history is not None:
            self.db_messages = copy.deepcopy(history)
        messages = ([] if base is None else [{'role': 'system', 'content': copy.deepcopy(base)}]) + [
            {'role': 'user', 'content': 'USER_TOKEN'}
        ]
        metadata = {'chat_id': 'saved' if saved else '', 'user_message_id': 'u1', 'session_id': 'session', 'params': {}}
        if tools:
            metadata['tool_servers'] = [
                {
                    'system_prompt': 'TOOLS_TOKEN',
                    'specs': [
                        {'name': 'z_tool', 'parameters': {'type': 'object'}},
                        {'name': 'a_tool', 'parameters': {'type': 'object'}},
                    ],
                }
            ]
        body = {
            'model': 'm',
            'messages': messages,
            'features': {'voice': voice, 'memory': memory},
            'params': params or {},
        }
        frozen = copy.deepcopy(body)
        result, metadata, _, done = await self.s['process_chat_payload'](
            self.request, body, self.user, metadata, self.model
        )
        self.assertFalse(done)
        self.errors.assert_not_called()
        # The fixture DB and caller-owned initial message list are not used as injected prompt storage.
        self.assertNotIn('UI_TOKEN', str(self.db_messages))
        self.assertNotIn('_context_summary_prompt', str(frozen))
        self.assertEqual(messages, frozen['messages'])
        return result, metadata

    def serialize(self, body, model='gpt-6.1-sol', responses=True):
        body = copy.deepcopy(body)
        body.pop('metadata', None)  # The actual router removes server-only metadata before conversion.
        body['model'] = model
        if self.s['is_openai_new_model'](model):
            body = self.s['openai_reasoning_model_handler'](body)
        return self.s['convert_to_responses_payload'](body) if responses else body

    def assert_order(self, text, tokens):
        positions = [text.index(token) for token in tokens]
        self.assertEqual(positions, sorted(positions), text)
        for token in tokens:
            self.assertEqual(text.count(token), 1, (token, text))

    async def test_full_actual_payload_and_all_provider_role_paths(self):
        body, metadata = await self.assemble()
        tokens = [
            'BASE_TOKEN',
            'FOLDER_TOKEN',
            'VOICE_TOKEN',
            'UI_TOKEN',
            'SUMMARY_TOKEN',
            'MEMORY_TOKEN',
            'TOOLS_TOKEN',
            'KNOWLEDGE_TOKEN',
            'RAG_TOKEN',
        ]
        self.assert_order(body['messages'][0]['content'], tokens)
        for model, responses, role in [
            ('gpt-6.1-sol', True, 'developer'),
            ('gpt-4.1', True, None),
            ('gpt-6.1-sol', False, 'developer'),
            ('gpt-4.1', False, 'system'),
        ]:
            with self.subTest(model=model, responses=responses):
                payload = self.serialize(body, model, responses)
                if responses and role:
                    self.assertEqual(payload['input'][0]['role'], role)
                    text = payload['input'][0]['content'][0]['text']
                    self.assertNotIn('instructions', payload)
                elif responses:
                    text = payload['instructions']
                else:
                    self.assertEqual(payload['messages'][0]['role'], role)
                    text = payload['messages'][0]['content']
                self.assert_order(text, tokens)
                self.assertNotIn('_context_summary_prompt', json.dumps(payload))
                self.assertEqual(
                    [tool.get('name') or tool['function']['name'] for tool in payload['tools']], ['z_tool', 'a_tool']
                )
        self.assertEqual(
            metadata['_context_summary_prompt']['prefix'], 'BASE_TOKEN\nFOLDER_TOKEN\nVOICE_TOKEN\nUI_TOKEN'
        )

    async def test_actual_router_base_injection_skip_and_metadata_removal(self):
        tree = ast.parse(FIXTURE.ROUTER.read_text())
        route = next(
            n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'generate_chat_completion'
        )
        start = next(i for i, n in enumerate(route.body) if ast.unparse(n).startswith('payload = {**form_data}'))
        end = next(
            i for i, n in enumerate(route.body) if ast.unparse(n).startswith('models = request.app.state.OPENAI_MODELS')
        )
        run = ast.parse('async def router_base(form_data, request, user, bypass_system_prompt):\n    pass').body[0]
        run.body = copy.deepcopy(route.body[start:end]) + [ast.Return(value=ast.Name(id='payload', ctx=ast.Load()))]
        scope = dict(self.s, bypass_filter=False, check_model_access=AsyncMock())
        scope['Models'] = SimpleNamespace(
            get_model_by_id=AsyncMock(
                return_value=SimpleNamespace(
                    base_model_id='', params=SimpleNamespace(model_dump=lambda: {'system': 'LATE_MODEL_TOKEN'})
                )
            )
        )
        exec(
            compile(ast.fix_missing_locations(ast.Module(body=[run], type_ignores=[])), str(FIXTURE.ROUTER), 'exec'),
            scope,
        )
        for responses in [True, False]:
            self.s['uses_responses_api'].return_value = responses
            body, metadata = await self.assemble()
            initial = copy.deepcopy(body)
            if not responses:
                self.request.state.responses_system_prompt_messages = None
            payload = await scope['router_base'](body, self.request, self.user, False)
            self.assertNotIn('metadata', payload)
            self.assertEqual('LATE_MODEL_TOKEN' in payload['messages'][0]['content'], not responses)
            text = payload['messages'][0]['content']
            if not responses:
                self.assertTrue(text.startswith('LATE_MODEL_TOKEN\nBASE_TOKEN'))
            self.assert_order(
                text,
                [
                    'BASE_TOKEN',
                    'FOLDER_TOKEN',
                    'VOICE_TOKEN',
                    'UI_TOKEN',
                    'SUMMARY_TOKEN',
                    'MEMORY_TOKEN',
                    'TOOLS_TOKEN',
                    'RAG_TOKEN',
                ],
            )
            self.assertNotIn('_context_summary_prompt', json.dumps(self.serialize(payload, responses=responses)))
            followup = await scope['router_base'](initial, self.request, self.user, True)
            self.assertNotIn('LATE_MODEL_TOKEN', followup['messages'][0]['content'])
            if not responses:
                # The real legacy prelude mutates its shared message; no-source
                # continuations must still replace the original summary once.
                continuation = self.continuation(body, metadata, responses=False, rebuild=False)
                followup = await continuation['run']()
                self.assert_order(
                    followup['messages'][0]['content'],
                    [
                        'LATE_MODEL_TOKEN',
                        'BASE_TOKEN',
                        'UI_TOKEN',
                        'SUMMARY_UPDATED',
                        'MEMORY_TOKEN',
                        'TOOLS_TOKEN',
                        'RAG_TOKEN',
                    ],
                )
                self.assertEqual(followup['messages'][0]['content'].count('[CONVERSATION SUMMARY]'), 1)

    async def test_optional_blocks_and_disabled_ui(self):
        for absent in ['summary', 'folder', 'voice', 'memory', 'tools', 'knowledge', 'rag', 'ui']:
            with self.subTest(absent=absent):
                self.setUp_sources()
                self.values['ui.enable_intelligent_ui'] = absent != 'ui'
                body, _ = await self.assemble(**({absent: False} if absent != 'ui' else {}))
                token = f'{absent.upper()}_TOKEN'
                text = body['messages'][0]['content']
                self.assertNotIn(token, text)
                ordered = [
                    t
                    for t in [
                        'BASE_TOKEN',
                        'FOLDER_TOKEN',
                        'VOICE_TOKEN',
                        'UI_TOKEN',
                        'SUMMARY_TOKEN',
                        'MEMORY_TOKEN',
                        'TOOLS_TOKEN',
                        'KNOWLEDGE_TOKEN',
                        'RAG_TOKEN',
                    ]
                    if t != token
                ]
                self.assert_order(text, ordered)

    def setUp_sources(self):
        self.sources = [
            {'source': {'id': 'source', 'name': 'file'}, 'document': ['RAG_TOKEN'], 'metadata': [{'source': 'source'}]}
        ]

    async def test_all_optional_blocks_absent_and_empty_system(self):
        self.values['ui.enable_intelligent_ui'] = False
        for base in ['', None]:
            body, _ = await self.assemble(
                base=base, saved=False, folder=False, voice=False, memory=False, tools=False, knowledge=False, rag=False
            )
            if base == '':
                self.assertEqual(body['messages'], [{'role': 'user', 'content': 'USER_TOKEN'}])
            else:
                self.assertEqual(body['messages'][0]['content'], 'MODEL_TOKEN')

    async def test_base_selection_overrides_and_multi_part_preservation(self):
        for base, params, model_params, expected, excluded in [
            ('PERSONAL_TOKEN', {}, {'system': 'MODEL_TOKEN'}, 'PERSONAL_TOKEN', ['MODEL_TOKEN', 'GLOBAL_TOKEN']),
            (
                'PERSONAL_TOKEN',
                {'system': 'CONTROL_TOKEN'},
                {'system': 'MODEL_TOKEN'},
                'CONTROL_TOKEN',
                ['PERSONAL_TOKEN', 'MODEL_TOKEN'],
            ),
            (None, {}, {'system': 'MODEL_TOKEN'}, 'MODEL_TOKEN', ['GLOBAL_TOKEN']),
            (None, {}, {}, 'GLOBAL_TOKEN', ['MODEL_TOKEN']),
            ('PERSONAL_TOKEN', {'system': ''}, {'system': 'MODEL_TOKEN'}, None, ['PERSONAL_TOKEN', 'MODEL_TOKEN']),
        ]:
            with self.subTest(base=base, params=params, model=model_params):
                self.model_params = model_params
                body, _ = await self.assemble(base=base, params=params)
                text = body['messages'][0]['content']
                if expected:
                    self.assertTrue(text.startswith(expected + '\n'))
                for token in excluded:
                    self.assertNotIn(token, text)
        for responses in [True, False]:
            self.s['uses_responses_api'].return_value = responses
            body, _ = await self.assemble(
                base=[{'type': 'text', 'text': 'BASE_PART1'}, {'type': 'text', 'text': 'BASE_PART2'}]
            )
            self.assert_order(
                body['messages'][0]['content'],
                ['BASE_PART1', 'BASE_PART2', 'FOLDER_TOKEN', 'VOICE_TOKEN', 'UI_TOKEN', 'SUMMARY_TOKEN'],
            )

    async def test_static_prefix_is_byte_stable_and_explicit_cache_fields_survive(self):
        params = {
            'custom_params': {'prompt_cache_key': 'fixture-isolated-key', 'prompt_cache_retention': '24h'},
            'temperature': 0.2,
        }
        first, metadata = await self.assemble(params=copy.deepcopy(params))
        second, _ = await self.assemble(params=copy.deepcopy(params))
        self.assertEqual(self.serialize(first), self.serialize(second))
        static = first['messages'][0]['content'].split('[CONVERSATION SUMMARY]')[0]
        summary_only = copy.deepcopy(first)
        summary_only['messages'] = self.s['apply_context_summary'](
            summary_only['messages'], 'SUMMARY_CHANGED', metadata
        )
        self.assertTrue(summary_only['messages'][0]['content'].encode().startswith(static.encode()))
        self.memory = 'MEMORY_CHANGED'
        memory_only, _ = await self.assemble(params=copy.deepcopy(params))
        self.assertTrue(memory_only['messages'][0]['content'].encode().startswith(static.encode()))
        self.memory = 'MEMORY_TOKEN'
        self.sources[0]['document'] = ['RAG_CHANGED']
        changed, _ = await self.assemble(params=copy.deepcopy(params))
        self.assertTrue(changed['messages'][0]['content'].startswith(static))
        for responses in [True, False]:
            changed['previous_response_id'] = 'resp_fixture'
            payload = self.serialize(changed, responses=responses)
            self.assertEqual(payload['prompt_cache_key'], 'fixture-isolated-key')
            self.assertEqual(payload['prompt_cache_retention'], '24h')
            self.assertEqual(payload['previous_response_id'], 'resp_fixture')
            self.assertEqual(
                [tool.get('name') or tool['function']['name'] for tool in payload['tools']], ['z_tool', 'a_tool']
            )

    async def test_multi_system_and_terminal_user_message_preserve_history_order(self):
        FIXTURE.load_function(UTILS / 'terminals.py', 'add_terminal_agents_md', self.s)
        self.sources = []
        history = [
            {
                'role': 'system',
                'content': [{'type': 'text', 'text': 'BASE_FIRST'}, {'type': 'text', 'text': 'BASE_SECOND'}],
            },
            {'role': 'user', 'content': 'USER_OLD'},
            {'role': 'assistant', 'content': 'ASSISTANT_OLD'},
            {'role': 'system', 'content': 'BASE_THIRD'},
            {'role': 'user', 'content': 'USER_CURRENT'},
        ]
        messages = self.s['add_terminal_agents_md'](history, 'TERMINAL_AGENTS_TOKEN')
        non_system = copy.deepcopy([m for m in messages if m['role'] != 'system'])
        result, _, _, _ = await self.s['process_chat_payload'](
            self.request, {'model': 'm', 'messages': messages, 'tools': []}, self.user, {'chat_id': ''}, self.model
        )
        self.assert_order(result['messages'][0]['content'], ['BASE_FIRST', 'BASE_SECOND', 'BASE_THIRD', 'UI_TOKEN'])
        self.assertNotIn('TERMINAL_AGENTS_TOKEN', result['messages'][0]['content'])
        self.assertEqual([m for m in result['messages'] if m['role'] != 'system'], non_system)

    async def test_native_mode_does_not_turn_opaque_history_into_text_summary(self):
        self.native.return_value = True
        body, metadata = await self.assemble()
        self.assertNotIn('SUMMARY_TOKEN', body['messages'][0]['content'])
        self.assertIsNone(metadata['_context_summary_prompt']['summary'])
        body['messages'].append(
            {'role': 'assistant', 'output': [{'type': 'compaction', 'encrypted_content': 'OPAQUE_FIXTURE'}]}
        )
        payload = self.serialize(body)
        self.assertEqual(payload['input'][-1], {'type': 'compaction', 'encrypted_content': 'OPAQUE_FIXTURE'})

    async def test_new_text_summary_is_generated_before_assembly_not_appended_after_tools(self):
        for name in ('_find_compaction_boundary', '_clamp_retention_percentage'):
            FIXTURE.load_function(UTILS / 'context_compaction.py', name, self.s)
        self.s['_load_config'].return_value.update(retention_percentage=40, prompt_template='')
        self.s['_exceeds_token_threshold'] = lambda *args: True
        self.s['_generate_summary'] = AsyncMock(return_value='GENERATED_SUMMARY')
        for previous in [False, True]:
            with self.subTest(previous=previous):
                history = [
                    {'id': f'h{idx}', 'role': 'user' if idx % 2 == 0 else 'assistant', 'content': f'HISTORY_{idx}'}
                    for idx in range(6)
                ]
                if previous:
                    history[0]['contextSummary'] = 'OLDER_SUMMARY'
                body, _ = await self.assemble(history=history)
                self.assert_order(
                    body['messages'][0]['content'],
                    ['BASE_TOKEN', 'UI_TOKEN', 'GENERATED_SUMMARY', 'MEMORY_TOKEN', 'TOOLS_TOKEN', 'RAG_TOKEN'],
                )
                self.assertNotIn('OLDER_SUMMARY', body['messages'][0]['content'])
                self.assertEqual(body['messages'][0]['content'].count('[CONVERSATION SUMMARY]'), 1)
                self.assertEqual(
                    [m['content'] for m in body['messages'] if m['role'] != 'system'], ['HISTORY_4', 'HISTORY_5']
                )
                call = self.s['_generate_summary'].await_args.args
                self.assertEqual(call[6], 'OLDER_SUMMARY' if previous else None)
                stored = self.s['Chats'].upsert_message_to_chat_by_id_and_message_id.await_args
                self.assertEqual(
                    stored.args[2],
                    {
                        'contextSummary': 'GENERATED_SUMMARY',
                        'meta': {'context_summary_pruning': None},
                    },
                )

    async def test_rag_user_side_contract(self):
        self.s['RAG_SYSTEM_CONTEXT'] = False
        body, _ = await self.assemble()
        self.assertNotIn('RAG_TOKEN', body['messages'][0]['content'])
        self.assertIn('RAG_TOKEN', body['messages'][-1]['content'])
        self.assertTrue(body['messages'][-1]['content'].endswith('USER_TOKEN'))

    async def test_legacy_image_outcome_appends_without_invalidating_summary_boundary(self):
        sources = copy.deepcopy(self.sources)
        body, metadata = await self.assemble(tools=False, knowledge=False, rag=False)
        tree = ast.parse(FIXTURE.MIDDLEWARE.read_text())
        handler = next(
            n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'chat_image_generation_handler'
        )
        insertion = next(
            n for n in handler.body if isinstance(n, ast.If) and ast.unparse(n.test) == 'system_message_content'
        )
        scope = dict(self.s, form_data=body, system_message_content='IMAGE_OUTCOME_TOKEN')
        exec(compile(ast.Module(body=[insertion], type_ignores=[]), str(FIXTURE.MIDDLEWARE), 'exec'), scope)
        body['messages'] = await self.s['apply_source_context_to_messages'](
            self.request, body['messages'], sources, 'USER_TOKEN'
        )
        body['messages'] = self.s['apply_context_summary'](body['messages'], 'SUMMARY_UPDATED', metadata)
        self.assert_order(
            body['messages'][0]['content'],
            ['BASE_TOKEN', 'UI_TOKEN', 'SUMMARY_UPDATED', 'MEMORY_TOKEN', 'IMAGE_OUTCOME_TOKEN', 'RAG_TOKEN'],
        )
        self.assertEqual(body['messages'][0]['content'].count('[CONVERSATION SUMMARY]'), 1)

    def continuation(self, body, metadata, *, responses=True, stateful=False, rebuild=True):
        """Compile the real local-tool source rebuild and request-copy branches."""
        scope = dict(self.s)
        tree = ast.parse(FIXTURE.MIDDLEWARE.read_text())
        citation = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.If)
            and ast.unparse(n.test) == 'citations_enabled'
            and 'all_tool_call_sources.extend' in ast.unparse(n)
        )
        branch = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.If) and ast.unparse(n.test) == 'ENABLE_RESPONSES_API_STATEFUL and last_response_id'
        )
        scope.update(
            form_data=copy.deepcopy(body),
            metadata=copy.deepcopy(metadata),
            messages=copy.deepcopy(body['messages']),
            output=FIXTURE.history(),
            full_output=lambda: scope['output'],
            user_message='USER_TOKEN',
            citations_enabled=rebuild,
            tool_call_sources=[],
            all_tool_call_sources=[],
            base_system_prompt=metadata['base_system_prompt'],
            event_emitter=self.emitter,
            new_form_data={},
            responses_api=responses,
            response_usage={},
            model=self.model,
            request=self.request,
            user=self.user,
            model_id='m',
            output_id=lambda prefix: 'fixture-checkpoint',
            emit_output=AsyncMock(),
            latest_summary='SUMMARY_UPDATED',
            ENABLE_RESPONSES_API_STATEFUL=stateful,
            last_response_id='resp_fixture' if stateful else None,
        )
        scope['compact_messages_for_request'] = AsyncMock(
            side_effect=lambda req, user, messages, *args: (messages, scope['latest_summary'], True)
        )
        run = ast.parse('async def run():\n    pass').body[0]
        run.body = [ast.Global(names=['messages', 'new_form_data'])] + copy.deepcopy([citation, branch])
        run.body.append(ast.Return(value=ast.Name(id='new_form_data', ctx=ast.Load())))
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[run], type_ignores=[])), str(FIXTURE.MIDDLEWARE), 'exec'
            ),
            scope,
        )
        return scope

    async def test_summary_update_and_repeated_tool_source_rebuilds(self):
        for responses in [True, False]:
            for initial_summary in [True, False]:
                for rebuild in [True, False]:
                    with self.subTest(responses=responses, initial_summary=initial_summary, rebuild=rebuild):
                        self.setUp_sources()
                        self.s['uses_responses_api'].return_value = responses
                        body, metadata = await self.assemble(summary=initial_summary)
                        frozen = copy.deepcopy(body)
                        scope = self.continuation(body, metadata, responses=responses, rebuild=rebuild)
                        for iteration in range(2):
                            scope['latest_summary'] = f'SUMMARY_UPDATE_{iteration}'
                            scope['tool_call_sources'].append(
                                {
                                    'source': {'id': f'tool{iteration}', 'name': f'TOOL_SOURCE_{iteration}'},
                                    'document': ['not-repeated'],
                                    'metadata': [{}],
                                }
                            )
                            followup = await scope['run']()
                            text = followup['messages'][0]['content']
                            self.assert_order(
                                text,
                                [
                                    'BASE_TOKEN',
                                    'FOLDER_TOKEN',
                                    'VOICE_TOKEN',
                                    'UI_TOKEN',
                                    f'SUMMARY_UPDATE_{iteration}',
                                    'MEMORY_TOKEN',
                                    'TOOLS_TOKEN',
                                    'KNOWLEDGE_TOKEN',
                                    'RAG_TOKEN',
                                ],
                            )
                            self.assertEqual(text.count('[CONVERSATION SUMMARY]'), 1)
                            self.assertNotIn('SUMMARY_TOKEN', text)
                            self.assertEqual(text.count('RAG_TEMPLATE'), 1)
                            for earlier in range(iteration):
                                self.assertNotIn(f'SUMMARY_UPDATE_{earlier}', text)
                            if rebuild:
                                for source in range(iteration + 1):
                                    self.assertEqual(text.count(f'TOOL_SOURCE_{source}'), 1)
                            serialized = self.serialize(followup, responses=responses)
                            self.assertNotIn('_context_summary_prompt', json.dumps(serialized))
                            tool_items = serialized['input'] if responses else serialized['messages']
                            if responses:
                                calls = [item for item in tool_items if item.get('type') == 'function_call']
                                results = [item for item in tool_items if item.get('type') == 'function_call_output']
                                self.assertEqual(calls[0]['call_id'], results[0]['call_id'])
                            else:
                                calls = [item for item in tool_items if item.get('tool_calls')]
                                results = [item for item in tool_items if item.get('role') == 'tool']
                                self.assertEqual(calls[0]['tool_calls'][0]['id'], results[0]['tool_call_id'])
                        self.assertEqual(body, frozen)
                        self.errors.assert_not_called()

    async def test_stateful_continuation_keeps_summary_in_place(self):
        body, metadata = await self.assemble()
        scope = self.continuation(body, metadata, stateful=True)
        scope['tool_call_sources'].append(
            {'source': {'id': 'tool', 'name': 'TOOL_SOURCE'}, 'document': [''], 'metadata': [{}]}
        )
        followup = await scope['run']()
        self.assert_order(
            followup['messages'][0]['content'],
            ['BASE_TOKEN', 'UI_TOKEN', 'SUMMARY_TOKEN', 'MEMORY_TOKEN', 'TOOLS_TOKEN', 'RAG_TOKEN'],
        )
        payload = self.serialize(followup)
        self.assertEqual(payload['previous_response_id'], 'resp_fixture')
        scope['compact_messages_for_request'].assert_not_awaited()

    async def test_changed_plugin_prefix_is_not_removed_by_stale_boundary(self):
        body, metadata = await self.assemble()
        original = body['messages'][0]['content']
        body['messages'][0]['content'] = 'PLUGIN_REWRITE\n' + original
        updated = self.s['apply_context_summary'](body['messages'], 'FRESH_SUMMARY', metadata)
        self.assertTrue(updated[0]['content'].startswith('PLUGIN_REWRITE\n' + original))
        self.assertTrue(updated[0]['content'].endswith('FRESH_SUMMARY'))

    async def test_ui_disabled_continuation_keeps_slot_and_unknown_summary_like_text(self):
        self.values['ui.enable_intelligent_ui'] = False
        base = 'BASE_TOKEN\n[CONVERSATION SUMMARY]\nLITERAL_BASE_EXAMPLE'
        body, metadata = await self.assemble(base=base)
        scope = self.continuation(body, metadata)
        followup = await scope['run']()
        text = followup['messages'][0]['content']
        self.assertNotIn('UI_TOKEN', text)
        self.assertTrue(text.startswith(base))
        self.assert_order(
            text,
            [
                'BASE_TOKEN',
                'LITERAL_BASE_EXAMPLE',
                'FOLDER_TOKEN',
                'VOICE_TOKEN',
                'SUMMARY_UPDATED',
                'MEMORY_TOKEN',
                'TOOLS_TOKEN',
                'RAG_TOKEN',
            ],
        )
        self.assertNotIn('SUMMARY_TOKEN', text)

    async def test_empty_prefix_can_gain_summary_on_tool_round_without_leading_blank(self):
        self.values['ui.enable_intelligent_ui'] = False
        body, metadata = await self.assemble(base='', summary=False, folder=False, voice=False)
        scope = self.continuation(body, metadata)
        followup = await scope['run']()
        text = followup['messages'][0]['content']
        self.assertTrue(text.startswith('[CONVERSATION SUMMARY]\nSUMMARY_UPDATED\n<memory_context>'))
        self.assert_order(text, ['SUMMARY_UPDATED', 'MEMORY_TOKEN', 'TOOLS_TOKEN', 'RAG_TOKEN'])

    async def test_legacy_model_snapshot_prefix_and_user_side_rag_continuation(self):
        self.s['uses_responses_api'].return_value = False
        body, metadata = await self.assemble(params={'system': 'LEGACY_MODEL_TOKEN'})
        scope = self.continuation(body, metadata, responses=False)
        scope['tool_call_sources'].append(
            {'source': {'id': 'tool', 'name': 'TOOL_SOURCE'}, 'document': [''], 'metadata': [{}]}
        )
        followup = await scope['run']()
        self.assert_order(
            followup['messages'][0]['content'],
            [
                'LEGACY_MODEL_TOKEN',
                'BASE_TOKEN',
                'UI_TOKEN',
                'SUMMARY_UPDATED',
                'MEMORY_TOKEN',
                'TOOLS_TOKEN',
                'RAG_TOKEN',
            ],
        )
        self.assertEqual(followup['messages'][0]['content'].count('[CONVERSATION SUMMARY]'), 1)
        self.s['RAG_SYSTEM_CONTEXT'] = False
        self.setUp_sources()
        body, metadata = await self.assemble()
        scope = self.continuation(body, metadata, responses=False)
        for iteration in range(2):
            scope['tool_call_sources'].append(
                {
                    'source': {'id': f'tool{iteration}', 'name': f'TOOL_SOURCE_{iteration}'},
                    'document': [''],
                    'metadata': [{}],
                }
            )
            followup = await scope['run']()
            self.assertNotIn('RAG_TOKEN', followup['messages'][0]['content'])
            user = next(m for m in followup['messages'] if m['role'] == 'user')
            self.assertEqual(user['content'].count('RAG_TOKEN'), 1)
            self.assertEqual(user['content'].count('RAG_TEMPLATE'), 1)


if __name__ == '__main__':
    unittest.main()
