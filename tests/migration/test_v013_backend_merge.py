"""Real offline migration upgrades and provider-facing compatibility seams.

No application bootstrap, production configuration, live service or model call.
"""

import ast
import asyncio
import copy
import importlib.util
import json
import logging
from numbers import Number
from pathlib import Path
import re
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend/open_webui'


def functions(path, names, scope):
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if getattr(node, 'name', '') in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope


class UpgradeTests(unittest.TestCase):
    def test_empty_official_and_custom_database_upgrade_without_replaying_records(self):
        import sqlalchemy as sa
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.runtime.environment import EnvironmentContext

        # Replace only bootstrap imports. Execute the production JSON storage
        # type and every actual Alembic upgrade through Alembic's revision runner.
        db = ModuleType('open_webui.internal.db')
        scope = {
            'types': sa.types,
            'Dialect': sa.Dialect,
            '_T': object,
            'Any': object,
            'Self': object,
            'JSONCodec': json,
        }
        functions(BACKEND / 'internal/db.py', {'JSONField'}, scope)
        db.JSONField = scope['JSONField']
        packages = {}
        for name, path in [
            ('open_webui', BACKEND),
            ('open_webui.internal', BACKEND / 'internal'),
            ('open_webui.migrations', BACKEND / 'migrations'),
        ]:
            package = ModuleType(name)
            package.__path__ = [str(path)]
            packages[name] = package
        packages['open_webui.internal.db'] = db
        packages['open_webui'].internal = packages['open_webui.internal']
        packages['open_webui.internal'].db = db
        with patch.dict(sys.modules, packages):
            config = Config()
            config.set_main_option('script_location', str(BACKEND / 'migrations'))
            script = ScriptDirectory.from_config(config)
            self.assertEqual(script.get_heads(), ['a13f0d42b9ce'])
            for start in (None, '0bd252b206fb', 'f64b7a2810ce'):
                with self.subTest(start=start), tempfile.TemporaryDirectory() as directory:
                    engine = sa.create_engine('sqlite:///' + directory + '/migration.db')

                    def upgrade(target):
                        with engine.begin() as connection:
                            with EnvironmentContext(
                                config, script, fn=lambda current, context: script._upgrade_revs(target, current)
                            ) as environment:
                                environment.configure(connection=connection)
                                environment.run_migrations()

                    if start:
                        upgrade(start)
                        with engine.begin() as connection:
                            connection.execute(
                                sa.text(
                                    "INSERT INTO chat (id,user_id,title,chat,created_at,updated_at) VALUES ('fixture-chat','fixture-owner','Keep this record','{}',1,1)"
                                )
                            )
                    upgrade('head')
                    with engine.connect() as connection:
                        inspector = sa.inspect(connection)
                        tables = set(inspector.get_table_names())
                        self.assertTrue(
                            {'knowledge_evidence', 'retrieval_chunk', 'ingestion_job', 'comment', 'notification'}
                            <= tables,
                            tables,
                        )
                        self.assertIn('tool_pruning', {column['name'] for column in inspector.get_columns('chat')})
                        self.assertIn('is_active', {column['name'] for column in inspector.get_columns('tool')})
                        self.assertEqual(
                            connection.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one(),
                            'a13f0d42b9ce',
                        )
                        if start:
                            self.assertEqual(
                                connection.execute(
                                    sa.text("SELECT title FROM chat WHERE id='fixture-chat'")
                                ).scalar_one(),
                                'Keep this record',
                            )
                            self.assertEqual(connection.execute(sa.text('SELECT count(*) FROM chat')).scalar_one(), 1)
                    engine.dispose()


class ToolAndUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolved_staged_dispatch_approval_and_provider_replay(self):
        scope = {
            'JSONCodec': json,
            'CALL_TOOL_NAME': 'call_tool',
            'SEARCH_TOOL_NAME': 'search_tools',
            'ast': ast,
            're': re,
            'time': time,
            'uuid4': uuid4,
            'log': logging.getLogger(__name__),
            'is_saved_chat_id': lambda value: False,
        }
        functions(BACKEND / 'utils/tool_search.py', {'resolve_tool_call'}, scope)
        functions(BACKEND / 'utils/middleware.py', {'execute_tool_call'}, scope)

        async def updated(function, extra_params):
            return function

        scope['get_updated_tool_function'] = updated
        callable = AsyncMock(return_value='authorized result')
        tools = {
            'call_tool': {'tool_id': 'builtin:call_tool'},
            'lookup': {
                'spec': {'parameters': {'properties': {'query': {'type': 'string'}}}},
                'callable': callable,
                'type': 'builtin',
            },
        }
        raw = {
            'id': 'provider-call',
            'function': {
                'name': 'call_tool',
                'arguments': json.dumps({'tool_name': 'lookup', 'params': '{"query":"needle"}'}),
            },
        }
        self.assertIsNone(scope['resolve_tool_call'](raw, tools))
        staged = {
            'type': 'function_call',
            'call_id': raw['id'],
            **raw['function'],
            'provider_function': raw['provider_function'],
        }
        caller = AsyncMock(return_value=False)
        metadata = {'tools': tools, 'params': {'tool_approval_mode': 'ask'}}
        result = await scope['execute_tool_call']({}, metadata, caller, staged)
        self.assertEqual(result[1], 'Error: tool call was not approved.')
        callable.assert_not_awaited()
        caller.return_value = True
        result = await scope['execute_tool_call']({}, metadata, caller, staged)
        self.assertEqual(result[1], 'authorized result')
        callable.assert_awaited_once_with(query='needle')
        self.assertEqual(caller.await_args.args[0]['data']['tool_call']['name'], 'lookup')
        self.assertEqual(staged['provider_function']['name'], 'call_tool')

    async def test_uploaded_display_uses_file_reader_with_permission_check_even_with_terminal_selected(self):
        identity = '11111111-1111-4111-8111-111111111111'
        record = SimpleNamespace(id=identity, filename='report.pdf', meta={'content_type': 'application/pdf'})
        access = AsyncMock(return_value=True)
        scope = {
            'JSONCodec': json,
            'Request': object,
            'log': logging.getLogger(__name__),
            '_has_read_access_to_file': access,
        }
        functions(BACKEND / 'tools/builtin.py', {'display_file'}, scope)

        async def uploaded(path, page=None):
            return await scope['display_file'](path, page, __request__=SimpleNamespace(), __user__={'id': 'owner'})

        file_reader = AsyncMock(side_effect=uploaded)
        terminal_reader = AsyncMock(return_value={'exists': True, 'path': '/terminal/private.pdf'})
        properties = {'path': {'type': 'string'}, 'page': {'type': 'integer'}}
        tools = {
            'display_file': {
                'type': 'terminal',
                'callable': terminal_reader,
                'spec': {'parameters': {'properties': properties}},
                'file_tool': {
                    'type': 'builtin',
                    'callable': file_reader,
                    'spec': {'parameters': {'properties': properties}},
                },
            }
        }
        execute_scope = {
            'JSONCodec': json,
            'ast': ast,
            're': re,
            'time': time,
            'uuid4': uuid4,
            'log': logging.getLogger(__name__),
            'is_saved_chat_id': lambda value: True,
        }

        async def updated(function, extra_params):
            return function

        execute_scope['get_updated_tool_function'] = updated
        functions(BACKEND / 'utils/middleware.py', {'execute_tool_call'}, execute_scope)
        call = {'call_id': 'display', 'name': 'display_file', 'arguments': json.dumps({'path': identity, 'page': 2})}
        with patch.dict(
            sys.modules,
            {
                'open_webui.models.files': SimpleNamespace(
                    Files=SimpleNamespace(get_file_by_id=AsyncMock(return_value=record))
                )
            },
        ):
            result = await execute_scope['execute_tool_call'](
                {}, {'tools': tools, 'terminal_id': 'selected'}, None, call
            )
            displayed = json.loads(result[1])
            self.assertEqual(displayed['source'], 'open_webui')
            self.assertTrue(displayed['displayed'])
            self.assertEqual(displayed['page'], 2)
            terminal_reader.assert_not_awaited()
            access.assert_awaited_once()
            access.return_value = False
            result = await execute_scope['execute_tool_call'](
                {}, {'tools': tools, 'terminal_id': 'selected'}, None, call
            )
            self.assertFalse(json.loads(result[1])['exists'])
            terminal_reader.assert_not_awaited()

    async def test_disabled_cached_callable_remains_blocked_after_extra_params_refresh(self):
        from functools import wraps, update_wrapper
        from fastapi import HTTPException

        active = AsyncMock(return_value=True)
        target = AsyncMock(return_value='result')
        scope = {
            'Callable': object,
            'wraps': wraps,
            'HTTPException': HTTPException,
            'Tools': SimpleNamespace(is_tool_active=active),
        }
        functions(BACKEND / 'utils/tools.py', {'guard_tool_enabled', 'get_updated_tool_function'}, scope)
        guarded = scope['guard_tool_enabled']('tool', target)
        self.assertEqual(await guarded(), 'result')
        active.return_value = False
        with self.assertRaises(HTTPException):
            await (await scope['get_updated_tool_function'](guarded, {}))()
        target.assert_awaited_once()

    async def test_nonstream_responses_refusal_incomplete_and_usage_mapping(self):
        scope = {'JSONCodec': json, 'time': time}
        functions(
            BACKEND / 'utils/response.py',
            {
                'usage_counter',
                'normalize_usage',
                'convert_responses_usage_to_chat_completions',
                'get_responses_finish_reason',
            },
            scope,
        )
        functions(BACKEND / 'routers/openai.py', {'convert_responses_result'}, scope)
        response = {
            'id': 'response',
            'created_at': 42,
            'status': 'incomplete',
            'incomplete_details': {'reason': 'content_filter'},
            'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'Declined'}]}],
            'usage': {'input_tokens': 0, 'output_tokens': 2},
        }
        result = scope['convert_responses_result'](response)
        self.assertEqual(result['choices'][0]['message']['refusal'], 'Declined')
        self.assertEqual(result['choices'][0]['finish_reason'], 'content_filter')
        self.assertEqual(result['created'], 42)
        self.assertEqual(result['usage']['prompt_tokens'], 0)
        self.assertEqual(result['usage']['completion_tokens'], 2)
        response['incomplete_details']['reason'] = 'max_output_tokens'
        self.assertEqual(scope['convert_responses_result'](response)['choices'][0]['finish_reason'], 'length')

    async def test_repeated_usage_snapshots_count_one_call_and_preserve_zero(self):
        scope = {'Number': Number}
        tree = ast.parse((BACKEND / 'utils/response.py').read_text())
        names = {
            'usage_counter',
            'normalize_usage',
            '_is_numeric_usage_value',
            '_merge_numeric_usage_map',
            'merge_usage',
            'update_usage_snapshot',
        }
        nodes = [
            n
            for n in tree.body
            if getattr(n, 'name', '') in names
            or isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id.startswith('USAGE_') for t in n.targets)
        ]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<production-usage>', 'exec'), scope)
        snapshot = scope['update_usage_snapshot']({}, {'input_tokens': 100, 'output_tokens': 2})
        snapshot = scope['update_usage_snapshot'](snapshot, {'input_tokens': 100, 'output_tokens': 5})
        self.assertEqual((snapshot['input_tokens'], snapshot['output_tokens']), (100, 5))
        cumulative = scope['merge_usage']({'input_tokens': 20, 'output_tokens': 3}, snapshot)
        self.assertEqual(cumulative['input_tokens'], 120)
        self.assertEqual(cumulative['prompt_tokens'], 100)
        self.assertEqual(scope['normalize_usage']({'prompt_tokens': 0})['input_tokens'], 0)
        self.assertNotIn('input_tokens', scope['normalize_usage']({'output_tokens': 0}))


class ConfigurationAndPrefixTests(unittest.IsolatedAsyncioTestCase):
    async def test_subagent_missing_null_zero_defaults_and_explicit_unlimited(self):
        from pydantic import BaseModel, Field, field_validator, ValidationError

        scope = {'BaseModel': BaseModel, 'Field': Field, 'field_validator': field_validator, '__name__': __name__}
        functions(BACKEND / 'routers/configs.py', {'SubagentsConfigForm'}, scope)
        model = scope['SubagentsConfigForm']
        base = {'ENABLE_SUBAGENTS': True, 'SUBAGENTS_BACKGROUND_ENABLED': False, 'SUBAGENTS_SYSTEM_PROMPT': ''}
        defaults = [20, 20, 30, 30000]
        names = ['SUBAGENTS_MAX_' + name for name in ('CONCURRENT', 'ASYNC', 'ITERATIONS', 'OUTPUT')]
        for value in (None, '', 0, '0'):
            form = model(**base, **dict.fromkeys(names, value))
            self.assertEqual([getattr(form, key) for key in names], defaults)
        self.assertEqual([getattr(model(**base), key) for key in names], defaults)
        form = model(
            **base,
            SUBAGENTS_MAX_CONCURRENT=-1,
            SUBAGENTS_MAX_ASYNC=-1,
            SUBAGENTS_MAX_ITERATIONS=7,
            SUBAGENTS_MAX_OUTPUT=999,
        )
        self.assertEqual([getattr(form, key) for key in names], [-1, -1, 7, 999])
        for key in names[2:]:
            with self.assertRaises(ValidationError):
                model(**base, **{key: -1})
        # Execute the production runtime's resolution statements too; persisted
        # historical nulls must retain the same finite policy as the API.
        delegate = next(
            n
            for n in ast.parse((BACKEND / 'utils/subagents.py').read_text()).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == 'delegate'
        )
        start = next(
            i
            for i, n in enumerate(delegate.body)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'max_concurrent' for t in n.targets)
        )
        end = next(
            i
            for i in range(start, len(delegate.body))
            if isinstance(delegate.body[i], ast.If)
            and ast.unparse(delegate.body[i].test) == "background and (not config.get('subagents.background_enabled'))"
        )
        for value in (None, 0, ''):
            local = {
                'config': dict.fromkeys(
                    ['subagents.max_' + name for name in ('concurrent', 'async', 'iterations', 'output')], value
                )
            }
            exec(
                compile(
                    ast.Module(body=delegate.body[start:end], type_ignores=[]), '<production-subagent-limits>', 'exec'
                ),
                local,
            )
            self.assertEqual(
                [local['max_' + name] for name in ('concurrent', 'async', 'iterations', 'output')], defaults
            )

    async def test_prefix_change_blocks_old_space_and_new_space_uses_registered_query_prefix(self):
        from contextlib import asynccontextmanager

        specification = importlib.util.spec_from_file_location(
            'v013_adapter', BACKEND / 'retrieval/vector/embedding_adapter.py'
        )
        adapter = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(adapter)
        config = {
            'rag.evidence_vector_space_id': 'space',
            'rag.embedding_engine': 'openai',
            'rag.embedding_model': 'fixture',
            'rag.openai.api_base_url': 'http://fixture.invalid/v1',
            'rag.embedding_query_prefix': 'query-v1',
            'rag.embedding_content_prefix': 'document-v1',
        }
        space = SimpleNamespace(
            id='space',
            active=True,
            verification_status='verified',
            embedding_dim=2048,
            embedding_model='fixture',
            config_fingerprint='fingerprint',
            distance_metric='cosine',
            supports_text_query=True,
            supports_text_evidence=True,
            encoding_config={
                'text_api_base_url': config['rag.openai.api_base_url'],
                'query_prefix': 'query-v1',
                'document_prefix': 'document-v1',
            },
        )

        @asynccontextmanager
        async def session():
            yield SimpleNamespace(get=AsyncMock(return_value=space))

        embedding = AsyncMock(return_value=[1.0] * 2048)
        scope = {
            'Config': SimpleNamespace(get_many=AsyncMock(side_effect=lambda *keys: dict(config))),
            'SERVICE_CONFIG_KEYS': tuple(config),
            'get_async_db': session,
            'Space': object,
            'asset_root': lambda config: Path('/tmp'),
            'ASYNC_VECTOR_DB_CLIENT': object(),
            'EvidenceEmbeddingAdapter': adapter.EvidenceEmbeddingAdapter,
            'IndexingService': SimpleNamespace,
            'EvidenceService': SimpleNamespace,
            'QueryEmbeddingRouter': lambda adapters: adapters,
            '_legacy_query_adapters': AsyncMock(return_value={}),
        }
        functions(BACKEND / 'retrieval/integration.py', {'services'}, scope)
        modules = {
            'open_webui.config': SimpleNamespace(PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH=2048),
            'open_webui.retrieval.utils': SimpleNamespace(get_embedding_function=lambda *a, **k: embedding),
        }
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=True, ef=None)))
        with patch.dict(sys.modules, modules):
            indexer, _, selected, _ = await scope['services'](request)
            self.assertEqual(selected, 'space')
            await indexer.embedding.encode(space=space, query_text='needle')
            self.assertEqual(embedding.await_args.kwargs['prefix'], 'query-v1')
            await indexer.embedding.encode(space=space, query_text='body', document=True)
            self.assertEqual(embedding.await_args.kwargs['prefix'], 'document-v1')
            config['rag.embedding_query_prefix'], config['rag.embedding_content_prefix'] = 'query-v2', 'document-v2'
            _, _, selected, status = await scope['services'](request)
            self.assertIsNone(selected)
            self.assertEqual(status['status'], 'incompatible')
            space.encoding_config.update(query_prefix='query-v2', document_prefix='document-v2')
            indexer, _, selected, _ = await scope['services'](request)
            self.assertEqual(selected, 'space')
            await indexer.embedding.encode(space=space, query_text='needle')
            self.assertEqual(embedding.await_args.kwargs['prefix'], 'query-v2')


class MediaUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_transcript_enters_queue_once_and_unparsed_video_uses_atomic_completion(self):
        from contextlib import asynccontextmanager
        from typing import Optional

        @asynccontextmanager
        async def session(db=None):
            yield SimpleNamespace()

        transcribe = AsyncMock(return_value={'text': 'transcribed speech'})
        submit, link = AsyncMock(), AsyncMock()

        async def config(key, default=None):
            return ['audio/wav'] if key == 'audio.stt.supported_content_types' else default

        scope = {
            'Optional': Optional,
            'AsyncSession': object,
            'asyncio': asyncio,
            'get_async_db_context': session,
            'Config': SimpleNamespace(get=config),
            'validate_knowledge_upload_target': AsyncMock(),
            '_is_text_file': lambda path: False,
            '_media_supported_for_extraction': lambda *args: False,
            'strict_match_mime_type': lambda supported, content_type: content_type in (supported or []),
            'Storage': SimpleNamespace(get_file=lambda path: path),
            'transcribe': transcribe,
            'ProcessFileForm': SimpleNamespace,
            '_cleanup_local_cache': lambda path: None,
            'Files': SimpleNamespace(update_file_data_by_id=AsyncMock()),
            'log': logging.getLogger(__name__),
        }
        functions(BACKEND / 'routers/files.py', {'process_uploaded_file'}, scope)
        metadata = {'knowledge_id': 'kb', 'directory_id': 'directory'}
        user, item = SimpleNamespace(id='owner'), SimpleNamespace(id='file')
        with patch.dict(
            sys.modules,
            {'open_webui.retrieval.ingestion': SimpleNamespace(submit_file=submit, link_stored_upload=link)},
        ):
            await scope['process_uploaded_file'](
                SimpleNamespace(), SimpleNamespace(content_type='audio/wav'), '/tmp/synthetic.wav', item, metadata, user
            )
            self.assertEqual(submit.await_count, 1)
            form = submit.await_args.args[1]
            self.assertEqual((form.content, form.collection_name), ('transcribed speech', 'kb'))
            self.assertEqual(submit.await_args.kwargs['auto_link'], {'directory_id': 'directory'})
            link.assert_not_awaited()
            submit.reset_mock()
            await scope['process_uploaded_file'](
                SimpleNamespace(), SimpleNamespace(content_type='video/mp4'), '/tmp/synthetic.mp4', item, metadata, user
            )
            submit.assert_not_awaited()
            link.assert_awaited_once_with('file', user, metadata)
            scope['Files'].update_file_data_by_id.assert_not_awaited()


class SummaryCheckpointTests(unittest.TestCase):
    def test_checkpoint_native_and_excluded_models_preserve_full_original_history(self):
        specification = importlib.util.spec_from_file_location(
            'v013_native_fixture', ROOT / 'tests/migration/test_native_compaction.py'
        )
        fixture = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(fixture)
        case = fixture.TextCompactionRoutingTests()
        case.setUp()
        try:
            case.messages[3]['contextSummary'] = 'old summary'
            case.messages[4]['output'] = [{'type': 'open_webui:context_compaction', 'summary': 'old', 'call_id': 'old'}]
            original = copy.deepcopy(case.messages)
            for model, native in [('known-gpt', False), ('native', True)]:
                case.native.return_value = native
                messages, summary = asyncio.run(
                    case.scope['apply_summary_checkpoint'](
                        case.messages, request=SimpleNamespace(), model_id=model, models={}
                    )
                )
                self.assertEqual(len(messages), len(original))
                self.assertIsNone(summary)
                self.assertFalse(any(m.get('contextSummary') for m in messages))
                self.assertEqual(messages[4]['output'], [])
                self.assertEqual(case.messages, original)
                case.scope['_generate_summary'].assert_not_awaited()
        finally:
            case.doCleanups()


if __name__ == '__main__':
    unittest.main()
