"""Local-only PDF routing regressions; all storage/DB/parser calls are mocked."""

import ast
import asyncio
import copy
import errno
import hashlib
import io
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Depends, File, Form, HTTPException, Query, UploadFile, status
from open_webui.utils.native_pdf import (
    CHAT_NATIVE_PDF_MAX_BYTES,
    carry_native_pdf_parts,
    is_native_pdf_attachment,
    prepare_native_pdf_attachments,
    should_defer_pdf_processing,
    supports_native_pdf,
)

PDF_MODEL = {'info': {'meta': {'capabilities': {'native_pdf': True}}}}
TEXT_MODEL = {'info': {'meta': {'capabilities': {'vision': True}}}}
PDF_BYTES = b'%PDF-1.7\nlocal mock PDF\n%%EOF'
BACKEND = Path(__file__).resolve().parents[1]


def source_function(relative_path, name, namespace=None):
    """Execute an actual isolated function without importing the app/Live DB."""
    source = ast.parse((BACKEND / 'open_webui' / relative_path).read_text())
    node = next(
        node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    scope = {'Request': object, 'UserModel': object, **(namespace or {})}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(relative_path), 'exec'), scope)
    return scope[name]


@pytest.fixture
def environment(monkeypatch, tmp_path):
    path = tmp_path / 'paper.pdf'
    path.write_bytes(PDF_BYTES)
    record = SimpleNamespace(
        id='pdf-id',
        user_id='owner',
        filename='paper.pdf',
        path=str(path),
        meta={
            'content_type': 'application/pdf',
            'size': len(PDF_BYTES),
            'data': {'upload_context': 'chat', 'native_model_file': True},
        },
        data={'status': 'completed'},
    )
    record.model_dump = lambda: {key: value for key, value in vars(record).items() if key != 'model_dump'}
    records = {record.id: record}
    files = SimpleNamespace(get_file_by_id=AsyncMock(side_effect=lambda file_id: records.get(file_id)))
    knowledge = SimpleNamespace(get_knowledges_by_file_id=AsyncMock(return_value=[]))
    access = AsyncMock(return_value=False)

    async def parse(request, form, *, user, db):
        record.data = {'content': 'Extracted PDF text', 'status': 'completed'}
        record.meta['collection_name'] = 'file-pdf-id'
        return {'status': True}

    process = AsyncMock(side_effect=parse)
    parser_db = SimpleNamespace(commit=AsyncMock(), active=False, exits=0)

    @asynccontextmanager
    async def parser_session():
        parser_db.active = True
        try:
            yield parser_db
        finally:
            parser_db.active = False
            parser_db.exits += 1

    monkeypatch.setitem(sys.modules, 'open_webui.internal.db', SimpleNamespace(get_async_db=parser_session))
    storage = SimpleNamespace(get_file=lambda stored_path: stored_path)
    monkeypatch.setitem(sys.modules, 'open_webui.models.files', SimpleNamespace(Files=files))
    monkeypatch.setitem(sys.modules, 'open_webui.models.knowledge', SimpleNamespace(Knowledges=knowledge))
    monkeypatch.setitem(
        sys.modules,
        'open_webui.routers.retrieval',
        SimpleNamespace(ProcessFileForm=SimpleNamespace, process_file=process),
    )
    monkeypatch.setitem(sys.modules, 'open_webui.storage.provider', SimpleNamespace(Storage=storage))
    monkeypatch.setitem(
        sys.modules, 'open_webui.utils.access_control.files', SimpleNamespace(has_access_to_file=access)
    )
    return SimpleNamespace(
        record=record,
        records=records,
        files=files,
        knowledge=knowledge,
        access=access,
        process=process,
        parser_db=parser_db,
        storage=storage,
        user=SimpleNamespace(id='owner', role='user'),
        path=path,
    )


def attachment(**updates):
    return {'id': 'pdf-id', 'type': 'file', 'name': 'paper.pdf', 'url': 'pdf-id', **updates}


def test_pdf_capability_is_explicit_not_vision_or_truthy_string():
    assert supports_native_pdf(PDF_MODEL)
    assert not supports_native_pdf(TEXT_MODEL)
    assert not supports_native_pdf({'info': {'meta': {'capabilities': {'native_pdf': 'true'}}}})


@pytest.mark.parametrize(
    'change',
    [
        {'upload_context': 'knowledge'},
        {'knowledge_id': 'kb'},
        {'channel_id': 'channel'},
        {'model_ids': []},
        {'model_ids': ['text']},
        {'model_ids': ['pdf', 'text']},
        {'model_ids': ['unknown']},
    ],
)
def test_upload_fallback_when_not_an_explicit_all_native_chat(change):
    metadata = {'upload_context': 'chat', 'model_ids': ['pdf'], **change}
    assert not should_defer_pdf_processing(
        metadata, 'paper.pdf', 'application/pdf', PDF_BYTES, {'pdf': PDF_MODEL, 'text': TEXT_MODEL}
    )


def test_upload_pdf_size_header_and_non_pdf_rules():
    metadata = {'upload_context': 'chat', 'model_ids': ['pdf']}
    models = {'pdf': PDF_MODEL}
    assert should_defer_pdf_processing(metadata, 'paper.PDF', 'application/octet-stream', PDF_BYTES, models)
    maximum = b'%PDF-' + b' ' * (CHAT_NATIVE_PDF_MAX_BYTES - 5)
    assert should_defer_pdf_processing(metadata, 'paper.pdf', None, maximum, models)
    assert not should_defer_pdf_processing(metadata, 'paper.pdf', None, maximum + b' ', models)
    assert not should_defer_pdf_processing(metadata, 'paper.pdf', None, b'not a PDF', models)
    assert not should_defer_pdf_processing(metadata, 'text.txt', 'text/plain', PDF_BYTES, models)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'metadata, native',
    [
        ({'upload_context': 'chat', 'model_ids': ['pdf']}, True),
        ({'upload_context': 'chat', 'model_ids': ['text'], 'native_model_file': True}, False),
        ({'upload_context': 'chat', 'model_ids': ['pdf', 'text']}, False),
        ({'knowledge_id': 'kb', 'model_ids': ['pdf'], 'native_model_file': True}, False),
        ({}, False),
    ],
)
async def test_actual_upload_handler_status_and_parse_branch(environment, metadata, native):
    inserted = []

    async def insert(user_id, form, *, db):
        inserted.append(form)
        environment.record.id = form.id
        environment.record.meta = form.meta
        environment.record.data = form.data
        return environment.record

    parser = AsyncMock()
    handler = source_function(
        'routers/files.py',
        'upload_file_handler',
        {
            'File': File,
            'Form': Form,
            'Depends': Depends,
            'Query': Query,
            'Optional': Optional,
            'UploadFile': UploadFile,
            'BackgroundTasks': object,
            'AsyncSession': object,
            'get_verified_user': lambda: None,
            'get_async_session': lambda: None,
            'asyncio': __import__('asyncio'),
            'os': os,
            'errno': errno,
            'hashlib': hashlib,
            'uuid': SimpleNamespace(uuid4=uuid4),
            'HTTPException': HTTPException,
            'status': status,
            'log': SimpleNamespace(info=lambda *args: None, exception=lambda *args: None),
            'Config': SimpleNamespace(get=AsyncMock(return_value=None)),
            'Storage': SimpleNamespace(
                upload_file=lambda stream, filename, tags: (stream.read(), str(environment.path))
            ),
            'Files': SimpleNamespace(insert_new_file=insert),
            'FileForm': SimpleNamespace,
            'should_defer_pdf_processing': should_defer_pdf_processing,
            'process_uploaded_file': parser,
        },
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={'pdf': PDF_MODEL, 'text': TEXT_MODEL})))
    user = SimpleNamespace(id='owner', role='user', name='test', email='test@example.invalid')
    file = UploadFile(filename='paper.pdf', file=io.BytesIO(PDF_BYTES))
    await handler(
        request,
        file=file,
        metadata=metadata,
        process=True,
        process_in_background=False,
        user=user,
        background_tasks=None,
        db=None,
    )
    assert inserted[0].data['status'] == ('completed' if native else 'pending')
    assert inserted[0].meta['data'].get('native_model_file') is (True if native else None)
    assert parser.await_count == (0 if native else 1)


@pytest.mark.asyncio
async def test_official_message_cleanup_preserves_native_parts(environment):
    messages = [{'id': 'msg', 'role': 'user', 'content': 'q', 'files': [attachment()]}]
    await prepare_native_pdf_attachments(None, messages, [attachment()], environment.user, PDF_MODEL)
    process_messages = source_function(
        'utils/middleware.py',
        'process_messages_with_output',
        {
            'add_file_context': source_function('utils/middleware.py', 'add_file_context'),
        },
    )
    cleaned = process_messages(messages, include_file_context=True)
    assert 'files' not in cleaned[0] and 'id' not in cleaned[0]
    assert len([part for part in cleaned[0]['content'] if part['type'] == 'file']) == 1
    assert cleaned[0]['content'][0]['text'] == 'q'


@pytest.mark.asyncio
async def test_native_pdf_injects_once_and_excludes_indexed_tag(environment):
    item = attachment()
    messages = [{'role': 'user', 'content': 'Read it', 'files': [item, dict(item)]}]
    ids = await prepare_native_pdf_attachments(None, messages, [item, dict(item)], environment.user, PDF_MODEL)
    assert ids == ['pdf-id']
    assert len([part for part in messages[0]['content'] if part['type'] == 'file']) == 1
    assert messages[0]['content'][0] == {'type': 'text', 'text': 'Read it'}
    assert messages[0]['files'] == []
    environment.process.assert_not_awaited()
    environment.files.get_file_by_id.assert_awaited_once_with('pdf-id')


@pytest.mark.asyncio
async def test_historical_pdf_stays_with_its_user_message(environment):
    messages = [
        {'role': 'user', 'content': 'Original upload', 'files': [attachment()]},
        {'role': 'assistant', 'content': 'ok'},
        {'role': 'user', 'content': 'Follow up'},
    ]
    await prepare_native_pdf_attachments(None, messages, [attachment()], environment.user, PDF_MODEL)
    assert any(part['type'] == 'file' for part in messages[0]['content'])
    assert messages[-1]['content'] == 'Follow up'


@pytest.mark.asyncio
async def test_non_pdf_model_lazily_parses_live_deferred_pdf(environment):
    item = attachment(native_model_file=True, file={'meta': {'data': {'native_model_file': True}}})
    messages = [{'role': 'user', 'content': 'Read', 'files': [item]}]
    assert await prepare_native_pdf_attachments(None, messages, [item], environment.user, TEXT_MODEL) == []
    environment.process.assert_awaited_once()
    assert messages[0]['content'] == 'Read'
    assert item['file']['data']['content'] == 'Extracted PDF text'
    assert item['collection_name'] == 'file-pdf-id'


@pytest.mark.asyncio
async def test_lazy_parser_receives_live_session_for_official_commit(environment):
    original_parse = environment.process.side_effect

    async def official_commit_boundary(request, form, *, user, db):
        assert db is environment.parser_db
        assert db.active
        await db.commit()
        return await original_parse(request, form, user=user, db=db)

    environment.process.side_effect = official_commit_boundary
    await prepare_native_pdf_attachments(
        None, [{'role': 'user', 'content': 'q'}], [attachment()], environment.user, TEXT_MODEL
    )
    environment.parser_db.commit.assert_awaited_once()
    assert environment.parser_db.exits == 1 and environment.parser_db.active is False


@pytest.mark.asyncio
async def test_lazy_parser_session_closes_on_failure(environment):
    environment.process.side_effect = RuntimeError('synthetic parser failure')
    with pytest.raises(RuntimeError, match='synthetic parser failure'):
        await prepare_native_pdf_attachments(
            None, [{'role': 'user', 'content': 'q'}], [attachment()], environment.user, TEXT_MODEL
        )
    assert environment.parser_db.exits == 1 and environment.parser_db.active is False


@pytest.mark.asyncio
async def test_concurrent_model_fallbacks_share_one_extraction(environment):
    original_parse = environment.process.side_effect

    async def interleaved_parse(*args, **kwargs):
        await asyncio.sleep(0)
        return await original_parse(*args, **kwargs)

    environment.process.side_effect = interleaved_parse

    async def prepare():
        return await prepare_native_pdf_attachments(
            None, [{'role': 'user', 'content': 'q'}], [attachment()], environment.user, TEXT_MODEL
        )

    assert await asyncio.gather(prepare(), prepare()) == [[], []]
    environment.process.assert_awaited_once()


@pytest.mark.asyncio
async def test_already_parsed_pdf_can_route_native_without_reparse(environment):
    environment.record.data = {'content': 'Existing parsed text', 'status': 'completed'}
    environment.record.meta['collection_name'] = 'file-pdf-id'
    item = attachment(collection_name='file-pdf-id', context='full')
    messages = [{'role': 'user', 'content': 'Read', 'files': [item]}]
    assert await prepare_native_pdf_attachments(None, messages, [item], environment.user, PDF_MODEL) == ['pdf-id']
    environment.process.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('kb_marker', [{'source': 'knowledge'}, {'collection_name': 'kb-id'}, {'legacy': True}])
async def test_knowledge_attachment_not_native_even_with_client_marker(environment, kb_marker):
    item = attachment(native_model_file=True, **kb_marker)
    messages = [{'role': 'user', 'content': 'Question', 'files': [item]}]
    assert await prepare_native_pdf_attachments(None, messages, [item], environment.user, PDF_MODEL) == []
    assert messages[0]['content'] == 'Question'
    environment.process.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowledge_link_in_database_blocks_chat_upload_native_route(environment):
    environment.knowledge.get_knowledges_by_file_id.return_value = [SimpleNamespace(id='kb')]
    messages = [{'role': 'user', 'content': 'Question', 'files': [attachment()]}]
    assert await prepare_native_pdf_attachments(None, messages, [attachment()], environment.user, PDF_MODEL) == []
    assert messages[0]['content'] == 'Question'


@pytest.mark.asyncio
async def test_model_attached_file_never_raw_transmitted(environment):
    model = copy.deepcopy(PDF_MODEL)
    model['info']['meta']['knowledge'] = [{'type': 'file', 'id': 'pdf-id'}]
    messages = [{'role': 'user', 'content': 'Question', 'files': [attachment()]}]
    assert await prepare_native_pdf_attachments(None, messages, [attachment()], environment.user, model) == []


@pytest.mark.asyncio
async def test_client_marker_and_snapshot_cannot_spoof_server_type(environment):
    environment.record.filename = 'text.txt'
    environment.record.meta['content_type'] = 'text/plain'
    item = attachment(native_model_file=True, file={'meta': {'content_type': 'application/pdf'}})
    messages = [{'role': 'user', 'content': 'Question', 'files': [item]}]
    assert await prepare_native_pdf_attachments(None, messages, [item], environment.user, PDF_MODEL) == []


@pytest.mark.asyncio
async def test_unauthorized_file_fails_before_storage_read(environment):
    environment.user.id = 'other'
    environment.storage.get_file = lambda path: pytest.fail('Unauthorized PDF was read')
    with pytest.raises(HTTPException) as error:
        await prepare_native_pdf_attachments(
            None, [{'role': 'user', 'content': 'q'}], [attachment()], environment.user, PDF_MODEL
        )
    assert error.value.status_code == 404
    environment.process.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_read_allows_native_but_not_fallback_write(environment):
    environment.user.id = 'reader'
    environment.access.side_effect = lambda file_id, access, user: access == 'read'
    assert await prepare_native_pdf_attachments(
        None, [{'role': 'user', 'content': 'q'}], [attachment()], environment.user, PDF_MODEL
    ) == ['pdf-id']
    with pytest.raises(HTTPException) as error:
        await prepare_native_pdf_attachments(
            None, [{'role': 'user', 'content': 'q'}], [attachment()], environment.user, TEXT_MODEL
        )
    assert error.value.status_code == 400
    environment.process.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_pdf_uses_parser_not_native(environment):
    environment.path.write_bytes(b'%PDF-' + b' ' * CHAT_NATIVE_PDF_MAX_BYTES)
    messages = [{'role': 'user', 'content': 'q'}]
    assert await prepare_native_pdf_attachments(None, messages, [attachment()], environment.user, PDF_MODEL) == []
    environment.process.assert_awaited_once()
    assert messages[0]['content'] == 'q'


@pytest.mark.asyncio
async def test_pending_parse_is_not_restarted_and_parse_errors_not_hidden(environment):
    environment.record.data['status'] = 'processing'
    with pytest.raises(HTTPException) as error:
        await prepare_native_pdf_attachments(
            None, [{'role': 'user', 'content': 'q'}], [attachment()], environment.user, TEXT_MODEL
        )
    assert error.value.status_code == 409
    environment.process.assert_not_awaited()
    environment.record.data['status'] = 'completed'
    environment.process.side_effect = HTTPException(status_code=400, detail='Mock parser error')
    with pytest.raises(HTTPException, match='Mock parser error'):
        await prepare_native_pdf_attachments(
            None, [{'role': 'user', 'content': 'q'}], [attachment()], environment.user, TEXT_MODEL
        )


@pytest.fixture
def retrieval_environment(monkeypatch):
    """Use the real scope filter without importing the application's SQL graph."""
    async def resolve(user, *, knowledge_ids=(), file_ids=()):
        return SimpleNamespace(knowledge_ids=tuple(knowledge_ids), file_ids=tuple(file_ids))

    resolver = AsyncMock(side_effect=resolve)
    request_scope = source_function('retrieval/integration.py', 'request_scope', {'resolve_scope': resolver})
    image_refs = source_function('retrieval/integration.py', 'authorized_query_images', {'resolve_scope': resolver})
    evidence = SimpleNamespace(search=AsyncMock(return_value={'items': [], 'warnings': []}))
    services = AsyncMock(return_value=(None, evidence, None, None))
    integration = SimpleNamespace(
        authorized_query_images=image_refs,
        request_scope=request_scope,
        split_external_scope=AsyncMock(side_effect=lambda scope: (scope, [])),
        services=services,
        citation_sources=lambda items: [],
        model_images=AsyncMock(return_value=[]),
    )
    monkeypatch.setitem(sys.modules, 'open_webui.retrieval.integration', integration)
    return SimpleNamespace(resolver=resolver, evidence=evidence, services=services)


@pytest.mark.asyncio
async def test_retrieval_filters_only_confirmed_native_pdf_keeps_knowledge_and_other_files(environment, retrieval_environment):
    handler = source_function(
        'utils/middleware.py',
        'chat_completion_files_handler',
        {
            'is_native_pdf_attachment': is_native_pdf_attachment,
            'Config': SimpleNamespace(get_many=AsyncMock(return_value={}), get=AsyncMock(return_value=5)),
            'get_attached_knowledge': lambda model, metadata: [],
            'get_sources_from_items': AsyncMock(return_value=[]),
            'get_last_user_message': lambda messages: 'Question',
            'log': SimpleNamespace(debug=lambda *args: None, exception=lambda *args: None),
        },
    )
    native = attachment(context='full')
    other = {'id': 'text-id', 'type': 'file', 'context': 'full'}
    knowledge = {'id': 'pdf-id', 'type': 'file', 'source': 'knowledge', 'context': 'full'}
    filesystem = {'id': '/file', 'type': 'filesystem', 'context': 'full'}
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=None, RERANKING_FUNCTION=None))
    )
    body = {
        'model': 'pdf',
        'messages': [],
        'metadata': {'files': [native, other, knowledge, filesystem], 'native_pdf_file_ids': ['pdf-id']},
    }
    await handler(request, body, {'__event_emitter__': AsyncMock()}, environment.user)
    handler.__globals__['get_sources_from_items'].assert_not_awaited()
    # The accepted unified evidence path keeps both non-native attachments;
    # neither a raw PDF transmission nor the retired parallel RAG path is used.
    assert retrieval_environment.resolver.await_args.kwargs['file_ids'] == ['text-id', 'pdf-id']
    retrieval_environment.evidence.search.assert_awaited_once()
    assert retrieval_environment.evidence.search.await_args.kwargs['resolved_scope'].file_ids == ('text-id', 'pdf-id')
    body['metadata']['native_pdf_file_ids'] = []
    assert not is_native_pdf_attachment(attachment(native_model_file=True), body['metadata'])


@pytest.mark.asyncio
async def test_only_native_pdf_never_generates_rag_query_or_reads_vectors(environment, retrieval_environment):
    fail = AsyncMock(side_effect=AssertionError('Native PDF reached retrieval'))
    handler = source_function(
        'utils/middleware.py',
        'chat_completion_files_handler',
        {
            'is_native_pdf_attachment': is_native_pdf_attachment,
            'get_attached_knowledge': lambda model, metadata: [],
            'generate_queries': fail,
            'get_sources_from_items': fail,
            'Config': SimpleNamespace(get_many=fail),
        },
    )
    body = {'metadata': {'files': [attachment()], 'native_pdf_file_ids': ['pdf-id']}}
    _, flags = await handler(None, body, {'__event_emitter__': fail}, environment.user)
    assert flags == {'sources': []}
    fail.assert_not_awaited()
    retrieval_environment.services.assert_not_awaited()


def test_tool_compaction_carries_pdf_once_and_stateful_responses_not_duplicated():
    part = {'type': 'file', 'file': {'filename': 'paper.pdf', 'file_data': 'data:application/pdf;base64,JVBERg=='}}
    previous = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Read'}, part]}]
    body = {'messages': [{'role': 'user', 'content': 'Follow up'}, {'role': 'tool', 'content': 'result'}]}
    carry_native_pdf_parts(body, previous)
    carry_native_pdf_parts(body, previous)
    assert body['messages'][0]['content'].count(part) == 1
    stateful = {'messages': [{'role': 'tool', 'content': 'result'}], 'previous_response_id': 'previous'}
    carry_native_pdf_parts(stateful, previous)
    assert stateful['messages'] == [{'role': 'tool', 'content': 'result'}]


def test_official_response_converter_consumes_native_file_part():
    converter = source_function('routers/openai.py', 'convert_to_responses_payload')
    payload = {
        'model': 'pdf',
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'Question'},
                    {
                        'type': 'file',
                        'file': {'filename': 'paper.pdf', 'file_data': 'data:application/pdf;base64,JVBERg=='},
                    },
                ],
            }
        ],
    }
    converted = converter(payload)
    assert converted['input'][0]['content'][-1] == {
        'type': 'input_file',
        'filename': 'paper.pdf',
        'file_data': 'data:application/pdf;base64,JVBERg==',
    }
