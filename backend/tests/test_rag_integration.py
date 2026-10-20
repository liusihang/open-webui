"""Real route/tool/middleware wiring with isolated SQL and mocked network boundaries."""

import asyncio
import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.documents import Document
from open_webui import config as app_config
from open_webui.internal import db as internal_db
from open_webui.models.access_grants import AccessGrant
from open_webui.models.evidence import KnowledgeEvidence
from open_webui.models.files import File, Files
from open_webui.models.knowledge import KnowledgeFile
from open_webui.models.retrieval_indexes import RetrievalIndexJob
from open_webui.models.users import UserModel
from open_webui.retrieval import integration
from open_webui.retrieval.document_image_assets import ImageAssetStore
from open_webui.retrieval.evidence import ResolvedScope
from open_webui.retrieval.indexing import IndexingService
from open_webui.retrieval.vector.embedding_adapter import EvidenceEmbeddingAdapter
from open_webui.retrieval.vector.main import GetResult, SearchResult
from open_webui.routers import files, knowledge, retrieval
from open_webui.tools import builtin
from open_webui.utils import middleware, tools
from open_webui.utils.auth import get_verified_user
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_rag_core import CONFIG
from test_rag_core import db as core_db_fixture

db = core_db_fixture


def user(identity='owner'):
    return UserModel(
        id=identity,
        email=identity + '@fixture.invalid',
        name=identity,
        role='user',
        created_at=1,
        updated_at=1,
        last_active_at=1,
    )


def png():
    out = io.BytesIO()
    Image.new('RGB', (12, 10), 'blue').save(out, 'PNG')
    return out.getvalue()


class VectorMemory:
    def __init__(self):
        self.collections = {}

    def has_collection(self, collection_name):
        return bool(self.collections.get(collection_name))

    def insert(self, collection_name, items):
        self.collections.setdefault(collection_name, {}).update({i['id']: i for i in items})

    def delete_collection(self, collection_name):
        self.collections.pop(collection_name, None)

    def query(self, collection_name, filter=None):
        items = list(self.collections.get(collection_name, {}).values())
        for key, value in (filter or {}).items():
            items = [i for i in items if i['metadata'].get(key) == value]
        return GetResult(
            ids=[[i['id'] for i in items]],
            documents=[[i['text'] for i in items]],
            metadatas=[[i['metadata'] for i in items]],
        )


@pytest_asyncio.fixture
async def app_stack(db, monkeypatch, tmp_path):  # noqa: C901
    monkeypatch.setattr(internal_db, 'AsyncSessionLocal', async_sessionmaker(db.bind, expire_on_commit=False))
    settings = dict(app_config.DEFAULT_CONFIG)
    settings.update(
        {
            'rag.embedding_engine': 'openai',
            'rag.embedding_model': 'Qwen3-VL-Embedding-2B',
            'rag.openai.api_base_url': 'http://mock.invalid/v1',
            'rag.openai.api_key': '',
            'rag.document_image_asset_root': str(tmp_path / 'assets'),
            'rag.extract_document_image_assets': True,
            'rag.content_extraction_engine': 'paddleocr_vl',
            'rag.chunk_size': 500,
            'rag.chunk_overlap': 75,
            'rag.enable_hybrid_search': False,
            'rag.enable_markdown_header_text_splitter': False,
            'rag.bypass_embedding_and_retrieval': False,
            'rag.chunk_min_size_target': 0,
            'rag.text_splitter': '',
        }
    )

    async def get_many(*keys):
        return {k: settings.get(k) for k in keys}

    async def get(key, default=None):
        return settings.get(key, default)

    async def upsert(updates):
        settings.update(updates)

    monkeypatch.setattr(app_config.Config, 'get_many', get_many)
    monkeypatch.setattr(app_config.Config, 'get', get)
    monkeypatch.setattr(app_config.Config, 'upsert', upsert)
    monkeypatch.setattr(app_config, 'PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH', 2048)
    mem = VectorMemory()
    vectors = SimpleNamespace(
        query=AsyncMock(side_effect=mem.query),
        delete_collection=AsyncMock(side_effect=mem.delete_collection),
        upsert=AsyncMock(),
        delete=AsyncMock(),
    )

    async def search(collection_name, vectors, limit, filter=None, **kwargs):
        items = list(mem.collections.get(collection_name, {}).values())
        return SearchResult(
            ids=[[i['id'] for i in items[:limit]]],
            documents=[[i['text'] for i in items[:limit]]],
            metadatas=[[i['metadata'] for i in items[:limit]]],
            distances=[[0.01] * min(limit, len(items))],
        )

    vectors.search = AsyncMock(side_effect=search)

    async def upsert_vectors(collection_name, items):
        mem.insert(collection_name, [i.model_dump() if hasattr(i, 'model_dump') else i for i in items])

    vectors.upsert.side_effect = upsert_vectors
    monkeypatch.setattr(retrieval, 'get_vector_db_client', lambda: mem)
    monkeypatch.setattr(retrieval, 'ASYNC_VECTOR_DB_CLIENT', vectors)
    monkeypatch.setattr(integration, 'ASYNC_VECTOR_DB_CLIENT', vectors)
    monkeypatch.setattr(files, 'ASYNC_VECTOR_DB_CLIENT', vectors)
    monkeypatch.setattr(knowledge, 'ASYNC_VECTOR_DB_CLIENT', vectors)
    text_calls = []

    async def encode(text, prefix=None, user=None):
        text_calls.append(text)
        vector = [1.0] + [0.0] * 2047
        return [vector for _ in text] if isinstance(text, list) else vector

    monkeypatch.setattr(retrieval, 'get_embedding_function', lambda *args, **kwargs: encode)
    from open_webui.retrieval import utils as retrieval_utils

    monkeypatch.setattr(retrieval_utils, 'get_embedding_function', lambda *args, **kwargs: encode)
    image_post = AsyncMock(return_value={'data': [{'embedding': [1.0] + [0.0] * 2047}]})
    monkeypatch.setattr(EvidenceEmbeddingAdapter, '_post', image_post)
    # Any parser/embedding network request that escapes a deliberate mock fails.
    import aiohttp
    import requests

    monkeypatch.setattr(requests.Session, 'request', lambda *a, **k: pytest.fail('Unexpected real network'))
    monkeypatch.setattr(
        aiohttp.ClientSession, '_request', AsyncMock(side_effect=AssertionError('Unexpected real network'))
    )
    monkeypatch.setattr(retrieval, 'publish_event', AsyncMock())
    monkeypatch.setattr(files, 'publish_event', AsyncMock())
    monkeypatch.setattr(knowledge, 'publish_event', AsyncMock())

    source = tmp_path / 'fixture.pdf'
    source.write_bytes(b'fixture: the parser is the mocked network boundary')
    file = await db.get(File, 'file')
    file.path, file.meta = str(source), {'content_type': 'application/pdf'}
    await db.commit()
    monkeypatch.setattr(retrieval.Storage, 'get_file', lambda path: path)
    parser_calls = []
    mode = {'text': True, 'image': True, 'skip': False}

    class Loader:
        def __init__(self, config):
            self.config = config
            self.metadata = {}

        async def aload(self, *args):
            parser_calls.append(self.config)
            metadata = {'page': 2, '_metadata_only': not mode['text']}
            if mode['image']:
                asset = ImageAssetStore(self.config['rag.document_image_asset_root'], 'file').put(
                    png(),
                    backend='paddleocr_vl',
                    origin_reference='synthetic-parser-image',
                    page_index=2,
                    caption='Blue square',
                )
                metadata['document_image_assets'] = [asset]
            if mode['skip']:
                metadata['document_image_assets_skipped'] = [{'reason': 'missing_image', 'page_index': 3}]
            return [Document(page_content='The figure is a blue square.' if mode['text'] else '', metadata=metadata)]

    monkeypatch.setattr(retrieval, 'build_loader_from_config', Loader)
    space = await IndexingService(db=db).register_space(
        embedding_model=settings['rag.embedding_model'],
        embedding_dim=2048,
        model_revision='fixture-provenance',
        encoding_config={**CONFIG, 'text_api_base_url': settings['rag.openai.api_base_url']},
        verified=True,
        supports_images=True,
    )
    settings['rag.evidence_vector_space_id'] = space.id
    app = FastAPI()
    app.state.main_loop = asyncio.get_running_loop()
    app.state.ef = None
    app.state.EMBEDDING_FUNCTION = encode
    app.state.RERANKING_FUNCTION = None
    app.include_router(files.router, prefix='/api/v1/files')
    owner = user()
    app.dependency_overrides[get_verified_user] = lambda: owner
    request = SimpleNamespace(app=app, state=SimpleNamespace())
    stack = SimpleNamespace(
        app=app,
        request=request,
        mem=mem,
        vectors=vectors,
        settings=settings,
        image_post=image_post,
        text_calls=text_calls,
        parser_calls=parser_calls,
        owner=owner,
        space=space,
        mode=mode,
    )
    yield stack
    await integration.stop_recovery(app)


async def upload(stack, db):
    return await retrieval.process_file(
        stack.request, retrieval.ProcessFileForm(file_id='file'), user=stack.owner, db=db
    )


async def wait_images(stack):
    await asyncio.gather(*list(getattr(stack.app.state, 'rag_index_tasks', ())))


@pytest.mark.asyncio
async def test_slim_docx_selects_lightweight_loader_without_external_call(db, app_stack, tmp_path, monkeypatch):
    from open_webui.retrieval.loaders import main as loaders
    from open_webui.retrieval.loaders.local import DocxLoader

    monkeypatch.setattr(loaders, 'USE_SLIM', True)
    config = {**app_stack.settings, 'rag.content_extraction_engine': '', 'rag.mineru_file_extensions': ['pdf']}
    loader = loaders.Loader(config)._get_loader('geometry.docx', None, str(tmp_path / 'geometry.docx'))
    assert isinstance(loader, DocxLoader)


@pytest.mark.asyncio
async def test_upload_missing_mime_type_uses_document_loader(db, app_stack):
    file = await db.get(File, 'file')
    file.meta = {**file.meta, 'content_type': None}
    await db.commit()
    result = await upload(app_stack, db)
    assert result['status']
    await wait_images(app_stack)
    assert len(app_stack.parser_calls) == 1
    assert (await Files.get_file_by_id('file')).data['retrieval_status']['text'] == 'ready'


@pytest.mark.asyncio
async def test_upload_actual_items_metadata_and_independent_images(db, app_stack):
    s = app_stack
    result = await upload(s, db)
    assert result['status']
    file = await Files.get_file_by_id('file')
    assert file.data['retrieval_status']['text'] == 'ready'
    assert file.data['document_image_assets'][0]['page_index'] == 2
    assert len(s.text_calls) == 1
    items = list(s.mem.collections['file-file'].values())
    evidence = (await db.scalars(select(KnowledgeEvidence).where(KnowledgeEvidence.modality == 'text'))).one()
    assert evidence.page_index == 2
    assert items[0]['metadata']['file_id'] == 'file'
    jobs = (await db.scalars(select(RetrievalIndexJob).where(RetrievalIndexJob.index_kind == 'image'))).all()
    assert jobs and jobs[0].payload['assets'][0]['sha256']
    await wait_images(s)
    assert (await Files.get_file_by_id('file')).data['retrieval_status']['images'] == 'ready'
    assert s.image_post.await_count == 1
    assert len(s.parser_calls) == 1


@pytest.mark.asyncio
async def test_metadata_only_image_upload_survives_filtering(db, app_stack):
    s = app_stack
    s.mode['text'] = False
    await upload(s, db)
    assert s.text_calls == []
    await wait_images(s)
    assert (await Files.get_file_by_id('file')).data['document_image_assets']
    assert (await db.scalars(select(KnowledgeEvidence).where(KnowledgeEvidence.modality == 'image'))).all()


@pytest.mark.asyncio
async def test_attach_kb_reuses_text_and_image_vectors(db, app_stack):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    await retrieval.process_file(
        s.request, retrieval.ProcessFileForm(file_id='file', collection_name='kb'), user=s.owner, db=db
    )
    await wait_images(s)
    assert len(s.text_calls) == 1
    assert s.image_post.await_count == 1
    assert 'kb' not in s.mem.collections
    assert (await Files.get_file_by_id('file')).data['retrieval_version'] == 1


@pytest.mark.asyncio
async def test_text_ready_image_failure_does_not_revert_version(db, app_stack):
    s = app_stack
    s.image_post.side_effect = RuntimeError('simulated encoder unavailable')
    await upload(s, db)
    await wait_images(s)
    state = (await Files.get_file_by_id('file')).data['retrieval_status']
    assert state['text'] == 'ready' and state['images'] == 'failed'
    indexer, evidence, _, _ = await integration.services(s.request)
    result = await evidence.search('blue', user=s.owner, resolved_scope=ResolvedScope(file_ids=('file',)))
    assert result['items'][0]['modality'] == 'text'


@pytest.mark.asyncio
async def test_native_and_automatic_retrieval_share_backend_without_double_search(db, app_stack, monkeypatch):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    model = {'info': {'meta': {'knowledge': [{'type': 'collection', 'id': 'kb'}], 'capabilities': {'vision': False}}}}
    metadata = {
        'params': {'function_calling': 'native'},
        'files': [{'type': 'file', 'id': 'file'}],
        'tools': {'query_knowledge_files': {}},
    }
    body = {'messages': [{'role': 'user', 'content': 'blue square'}], 'metadata': metadata}
    emitter = AsyncMock()
    old_search = AsyncMock(side_effect=AssertionError('Legacy search must not run'))
    monkeypatch.setattr(middleware, 'get_sources_from_items', old_search)
    await middleware.chat_completion_files_handler(
        s.request, body, {'__event_emitter__': emitter, '__model__': model}, s.owner
    )
    assert s.vectors.search.await_count == 0
    result = json.loads(
        await builtin.query_knowledge_files(
            'blue',
            __request__=s.request,
            __user__=s.owner.model_dump(),
            __model_knowledge__=model['info']['meta']['knowledge'],
            __metadata__=metadata,
            __model__=model,
        )
    )
    assert result['items'] and all('storage_uri' not in json.dumps(i) for i in result['items'])
    second = json.loads(
        await builtin.query_knowledge_files(
            'blue',
            __request__=s.request,
            __user__=s.owner.model_dump(),
            __model_knowledge__=(),
            __metadata__=metadata,
            __model__=model,
        )
    )
    assert second['items'] == []
    auto_metadata = {**metadata, 'params': {'function_calling': 'legacy'}, 'rag_seen_refs': []}
    body['metadata'] = auto_metadata
    _, flags = await middleware.chat_completion_files_handler(
        s.request, body, {'__event_emitter__': emitter, '__model__': model}, s.owner
    )
    assert flags['sources'] and flags['sources'][0]['metadata'][0]['evidence_ref']
    old_search.assert_not_called()


@pytest.mark.asyncio
async def test_imported_index_status_without_file_cache(db, app_stack):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    from open_webui.models.evidence import KnowledgeEvidenceEmbedding
    from sqlalchemy import update

    await db.execute(update(File).where(File.id == 'file').values(data={}))
    await db.execute(update(KnowledgeEvidenceEmbedding).values(embedding_status='quarantined'))
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=s.app), base_url='http://isolated.test') as client:
        response = await client.get('/api/v1/files/file/retrieval/status')
        assert response.status_code == 200
        status = response.json()
        assert status['text'] == status['images'] == 'quarantined'
        assert status['status_source'] == 'sql'
        assert status['image_retryable'] is False
        assert status['indexed_evidence_count'] > 0
        assert status['searchable_evidence_count'] == 0
        assert (await Files.get_file_by_id('file')).data == {}
        s.app.dependency_overrides[get_verified_user] = lambda: user('stranger')
        assert (await client.get('/api/v1/files/file/retrieval/status')).status_code == 404


@pytest.mark.asyncio
async def test_routes_real_auth_thumbnail_and_revocation(db, app_stack):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    ref = (await db.scalars(select(KnowledgeEvidence).where(KnowledgeEvidence.modality == 'image'))).one().evidence_ref
    async with AsyncClient(transport=ASGITransport(app=s.app), base_url='http://isolated.test') as client:
        path = f'/api/v1/files/file/evidence/{ref}'
        response = await client.get(path + '/thumbnail')
        assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'
        assert Image.open(io.BytesIO(response.content)).format == 'JPEG'
        assert 'storage_uri' not in (await client.get(path)).text
        s.app.dependency_overrides[get_verified_user] = lambda: user('reader')
        assert (await client.get(path)).status_code == 200
        await db.execute(delete(AccessGrant))
        await db.commit()
        assert (await client.get(path + '/thumbnail')).status_code == 404


@pytest.mark.asyncio
async def test_new_version_hides_old_citation_before_parser(db, app_stack, monkeypatch):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    ref = (await db.scalars(select(KnowledgeEvidence).where(KnowledgeEvidence.modality == 'text'))).one().evidence_ref

    # Directly accept the next version to test the pre-parser boundary, without a new encode.
    await integration.accept_version(s.request, 'file', new_content=True, db=db)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await files.get_file_evidence(s.request, 'file', ref, s.owner)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_space_and_changed_endpoint_remain_unusable(db, app_stack):
    s = app_stack
    s.settings['rag.openai.api_base_url'] = 'http://other.invalid/v1'
    _, _, identity, runtime = await integration.services(s.request)
    assert identity is None and runtime['status'] == 'incompatible'
    s.settings['rag.evidence_vector_space_id'] = ''
    _, _, identity, runtime = await integration.services(s.request)
    assert identity is None and runtime['status'] == 'unassigned'


@pytest.mark.asyncio
async def test_controlled_asset_root_does_not_trust_entire_data_dir(db, app_stack, tmp_path):
    s = app_stack
    outside = tmp_path / 'other-image.png'
    outside.write_bytes(png())
    asset = SimpleNamespace(storage_uri=str(outside), sha256=hashlib.sha256(png()).hexdigest())
    with pytest.raises(PermissionError):
        await integration.controlled_asset_reader(asset, root=integration.asset_root(s.settings))


@pytest.mark.asyncio
async def test_query_schema_scope_and_forbidden_image_arguments(db, app_stack):
    s = app_stack
    selected = await integration.request_scope(
        s.owner,
        model_knowledge=[{'type': 'collection', 'id': 'kb'}],
        metadata={'files': [{'type': 'file', 'id': 'file'}]},
        knowledge_ids=['not-attached'],
    )
    assert selected.knowledge_ids == () and selected.file_ids == ()
    attached = tools.get_attached_knowledge(
        {'info': {'meta': {'capabilities': {'file_context': True}}}},
        {'folder_knowledge': [{'type': 'collection', 'id': 'kb'}], 'files': [{'type': 'file', 'id': 'file'}]},
    )
    assert {i['id'] for i in attached} == {'kb', 'file'}
    spec = tools.get_builtin_tool_spec(builtin.query_knowledge_files)
    assert set(spec['parameters']['properties']) == {
        'query',
        'knowledge_ids',
        'count',
        'query_image_refs',
        'modalities',
        'file_ids',
    }
    await upload(s, db)
    result = json.loads(
        await builtin.query_knowledge_files(
            '',
            query_image_refs=['http://forbidden.invalid/image.png'],
            __request__=s.request,
            __user__=s.owner.model_dump(),
            __metadata__={'files': [{'type': 'file', 'id': 'file'}]},
        )
    )
    assert result.get('error')


@pytest.mark.asyncio
async def test_vision_only_receives_authorized_hit_pixels(db, app_stack):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    metadata = {'files': [{'type': 'file', 'id': 'file'}]}
    kwargs = dict(__request__=s.request, __user__=s.owner.model_dump(), __metadata__=metadata)
    await builtin.query_knowledge_files('blue', **kwargs)
    assert s.request.state.rag_model_images == []
    metadata['rag_seen_refs'] = []
    result = await builtin.query_knowledge_files(
        'blue', **kwargs, __model__={'info': {'meta': {'capabilities': {'vision': True}}}}
    )
    assert s.request.state.rag_model_images[0]['image_url'].startswith('data:image/jpeg;base64,')
    parsed, media, _ = await middleware.process_tool_result(
        s.request, 'query_knowledge_files', result, 'builtin', metadata=metadata, user=s.owner
    )
    assert media[0]['rag_llm_only'] and 'data:image/' not in middleware.tool_result_content(parsed)


@pytest.mark.asyncio
async def test_file_delete_invalidates_before_official_source_row(db, app_stack, monkeypatch):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    ref = (await db.scalars(select(KnowledgeEvidence).where(KnowledgeEvidence.modality == 'image'))).one().evidence_ref
    original_delete = Files.delete_file_by_id

    async def delete_file(identity, db=None):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await files.get_file_evidence(s.request, 'file', ref, s.owner)
        return await original_delete(identity, db=db)

    monkeypatch.setattr(Files, 'delete_file_by_id', delete_file)
    monkeypatch.setattr(files.Storage, 'delete_file', lambda path: None)
    await files.delete_file_by_id(s.request, 'file', user=s.owner, db=db)
    assert not await Files.get_file_by_id('file')


@pytest.mark.asyncio
async def test_startup_recovery_is_single_owner_and_no_history_replay(db, app_stack):
    s = app_stack
    await integration.start_recovery(s.app)
    assert s.app.state.rag_index_owner
    second = FastAPI()
    second.state.EMBEDDING_FUNCTION = s.app.state.EMBEDDING_FUNCTION
    await integration.start_recovery(second)
    assert not hasattr(second.state, 'rag_index_owner')
    assert s.image_post.await_count == 0 and s.text_calls == []


@pytest.mark.asyncio
async def test_pending_image_job_recovers_without_reparse_or_text_embedding(db, app_stack, monkeypatch):
    s = app_stack
    monkeypatch.setattr(retrieval, 'spawn', lambda request, coroutine: coroutine.close())
    await upload(s, db)
    assert s.image_post.await_count == 0
    assert (await Files.get_file_by_id('file')).data['retrieval_status']['images'] == 'pending'
    await integration.start_recovery(s.app)
    await wait_images(s)
    assert s.image_post.await_count == 1 and len(s.text_calls) == 1 and len(s.parser_calls) == 1
    assert (await Files.get_file_by_id('file')).data['retrieval_status']['images'] == 'ready'


@pytest.mark.asyncio
async def test_delete_pending_images_cleans_only_its_private_files(db, app_stack, monkeypatch, tmp_path):
    from pathlib import Path

    s = app_stack
    monkeypatch.setattr(retrieval, 'spawn', lambda request, coroutine: coroutine.close())
    await upload(s, db)
    file = await Files.get_file_by_id('file')
    path = Path(file.data['document_image_assets'][0]['storage_uri'])
    other = integration.asset_root(s.settings) / 'unrelated.png'
    other.write_bytes(png())
    monkeypatch.setattr(files.Storage, 'delete_file', lambda path: None)
    await files.delete_file_by_id(s.request, 'file', user=s.owner, db=db)
    assert not path.exists() and other.exists()


@pytest.mark.asyncio
async def test_explicit_image_retry_never_reparses_or_reembeds_text(db, app_stack):
    s = app_stack
    s.image_post.side_effect = RuntimeError('simulated temporary failure')
    await upload(s, db)
    await wait_images(s)
    s.image_post.side_effect = None
    result = await files.retry_file_image_index(s.request, 'file', user=s.owner, db=db)
    assert result['scheduled'] == 1
    await wait_images(s)
    assert s.image_post.await_count == 2 and len(s.text_calls) == 1 and len(s.parser_calls) == 1
    assert (await Files.get_file_by_id('file')).data['retrieval_status']['images'] == 'ready'


@pytest.mark.asyncio
async def test_kb_batch_uses_actual_existing_items_without_embedding(db, app_stack):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    file = await Files.get_file_by_id('file')
    result = await retrieval.process_files_batch(
        s.request, retrieval.BatchProcessFilesForm(files=[file], collection_name='kb'), user=s.owner
    )
    await wait_images(s)
    assert result.results[0].status == 'completed' and not result.errors
    assert len(s.text_calls) == 1 and s.image_post.await_count == 1


@pytest.mark.asyncio
async def test_kb_removal_invalidates_scoped_evidence_but_preserves_file_asset(db, app_stack):
    from pathlib import Path

    s = app_stack
    await upload(s, db)
    await wait_images(s)
    await retrieval.process_file(
        s.request, retrieval.ProcessFileForm(file_id='file', collection_name='kb'), user=s.owner, db=db
    )
    await wait_images(s)
    file = await Files.get_file_by_id('file')
    image_path = Path(file.data['document_image_assets'][0]['storage_uri'])
    await knowledge.remove_file_from_knowledge_by_id(
        s.request, 'kb', knowledge.KnowledgeFileIdForm(file_id='file'), delete_file=False, user=s.owner, db=db
    )
    _, evidence, _, _ = await integration.services(s.request)
    result = await evidence.search('blue', user=s.owner, resolved_scope=ResolvedScope(knowledge_ids=('kb',)))
    assert result['items'] == [] and image_path.is_file()
    result = await evidence.search('blue', user=s.owner, resolved_scope=ResolvedScope(file_ids=('file',)))
    assert result['items']


@pytest.mark.asyncio
async def test_official_external_connector_is_not_lost_in_unified_search(db, app_stack, monkeypatch):
    from open_webui.models.knowledge import Knowledge
    from open_webui.retrieval import external

    s = app_stack
    row = await db.get(Knowledge, 'kb')
    row.meta = {'source': 'external'}
    await db.commit()
    retrieve = AsyncMock(
        return_value={
            'documents': [['Official external result']],
            'metadatas': [[{'source': 'External fixture'}]],
            'distances': [[0.5]],
        }
    )
    monkeypatch.setattr(external, 'retrieve_external_knowledge', retrieve)
    result = json.loads(
        await builtin.query_knowledge_files(
            'query', knowledge_ids=['kb'], __request__=s.request, __user__=s.owner.model_dump()
        )
    )
    assert result['items'] == [] and result['legacy_items'][0]['content'] == 'Official external result'
    assert retrieve.await_count == 1 and s.text_calls == []
    sources = middleware.get_citation_source_from_tool_result('query_knowledge_files', {}, json.dumps(result))
    assert sources[0]['document'] == ['Official external result']


@pytest.mark.asyncio
async def test_space_registration_requires_explicit_provenance_and_config_validation(db, app_stack):
    from fastapi import HTTPException

    s = app_stack
    form = retrieval.EvidenceSpaceForm(embedding_model='Qwen3-VL-Embedding-2B')
    result = await retrieval.register_evidence_space(s.request, form, user=s.owner)
    assert result['status'] == 'quarantined'
    with pytest.raises(HTTPException):
        await retrieval.register_evidence_space(
            s.request,
            retrieval.EvidenceSpaceForm(embedding_model='Qwen3-VL-Embedding-2B', verified=True, embedding_dim=2048),
            user=s.owner,
        )
    with pytest.raises(HTTPException):
        await retrieval.update_evidence_config(s.request, {'rag.document_image_asset_root': '/'}, user=s.owner)
    with pytest.raises(HTTPException):
        await retrieval.update_evidence_config(s.request, {'rag.paddleocr_vl_poll_interval': 0}, user=s.owner)
    result = await retrieval.update_evidence_config(
        s.request,
        {
            'rag.paddleocr_vl_api_mode': 'jobs',
            'rag.pdf_image_asset_source': 'parser',
            'rag.paddleocr_vl_allowed_remote_origins': ['https://ocr-cdn.invalid'],
        },
        user=s.owner,
    )
    assert result['settings']['rag.paddleocr_vl_api_mode'] == 'jobs'
    assert s.text_calls == [] and s.image_post.await_count == 0


@pytest.mark.asyncio
async def test_uploaded_query_image_can_search_selected_kb_as_independent_input(db, app_stack, tmp_path):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    await retrieval.process_file(
        s.request, retrieval.ProcessFileForm(file_id='file', collection_name='kb'), user=s.owner, db=db
    )
    await wait_images(s)
    path = tmp_path / 'query.png'
    path.write_bytes(png())
    db.add(
        File(
            id='query-image',
            user_id='owner',
            filename='query.png',
            path=str(path),
            meta={'content_type': 'image/png'},
            data={},
            created_at=1,
            updated_at=1,
        )
    )
    await db.commit()
    result = json.loads(
        await builtin.query_knowledge_files(
            '',
            knowledge_ids=['kb'],
            query_image_refs=['query-image'],
            modalities=['text'],
            __request__=s.request,
            __user__=s.owner.model_dump(),
            __metadata__={'files': [{'type': 'file', 'id': 'query-image'}]},
        )
    )
    assert result['items'] and all(i['modality'] == 'text' for i in result['items'])
    assert s.image_post.call_args.kwargs['payload']['messages'][0]['content'][0]['type'] == 'image_url'
    metadata = {
        'params': {'function_calling': 'legacy'},
        'files': [{'type': 'collection', 'id': 'kb'}, {'type': 'image', 'id': 'query-image'}],
    }
    _, flags = await middleware.chat_completion_files_handler(
        s.request,
        {'messages': [{'role': 'user', 'content': ''}], 'metadata': metadata},
        {'__event_emitter__': AsyncMock(), '__model__': {}},
        s.owner,
    )
    assert flags['sources'] and len(s.text_calls) == 1
    assert await integration.authorized_query_images(s.owner, [{'type': 'image', 'id': 'query-image'}]) == [
        'query-image'
    ]
    assert await integration.authorized_query_images(user('stranger'), [{'type': 'image', 'id': 'query-image'}]) == []


@pytest.mark.asyncio
async def test_file_scope_narrows_selected_kb_and_does_not_expand_to_another_upload(db, app_stack):
    s = app_stack
    selected = [{'type': 'collection', 'id': 'kb'}]
    scope = await integration.request_scope(s.owner, model_knowledge=selected, file_ids=['file'])
    assert scope.file_ids == ('file',) and scope.knowledge_ids == ()
    db.add(File(id='other', user_id='owner', filename='other.txt', meta={}, data={}, created_at=1, updated_at=1))
    await db.commit()
    rejected = await integration.request_scope(s.owner, model_knowledge=selected, file_ids=['other'])
    assert rejected.file_ids == () and rejected.knowledge_ids == ()
    with pytest.raises(ValueError, match='not both'):
        await integration.request_scope(s.owner, knowledge_ids=['kb'], file_ids=['file'])
    await db.execute(delete(KnowledgeFile).where(KnowledgeFile.file_id == 'file'))
    await db.commit()
    removed = await integration.request_scope(s.owner, model_knowledge=selected, file_ids=['file'])
    assert removed.file_ids == ()


@pytest.mark.asyncio
async def test_chat_and_knowledge_search_use_same_versioned_evidence_and_citations(db, app_stack):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    context = {'__request__': s.request, '__user__': s.owner.model_dump()}
    attached = [{'type': 'file', 'id': 'file'}]
    legacy = json.loads(await builtin.query_chat_files('blue', __files__=attached, **context))
    canonical = json.loads(
        await builtin.query_knowledge_files('blue', file_ids=['file'], __metadata__={'files': attached}, **context)
    )
    assert legacy['items'] and canonical['items']
    assert {i['evidence_ref'] for i in legacy['items']} == {i['evidence_ref'] for i in canonical['items']}
    assert all('preview' not in i and 'score' not in i for i in canonical['items'])
    assert len(s.text_calls) == 3  # One index operation, two queries; no re-index.
    for name, result in [('query_chat_files', legacy), ('query_knowledge_files', canonical)]:
        sources = middleware.get_citation_source_from_tool_result(name, {}, json.dumps(result))
        assert sources and sources[0]['metadata'][0]['evidence_ref']


@pytest.mark.asyncio
async def test_canonical_evidence_reader_keeps_image_channel_and_explicit_reread(db, app_stack):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    metadata = {'files': [{'type': 'file', 'id': 'file'}]}
    context = {
        '__request__': s.request,
        '__user__': s.owner.model_dump(),
        '__metadata__': metadata,
        '__model__': {'info': {'meta': {'capabilities': {'vision': True}}}},
    }
    found = json.loads(await builtin.query_knowledge_files('blue', **context))
    refs = [item['evidence_ref'] for item in found['items']]
    assert len(refs) == 2
    for _ in range(2):
        result = await builtin.view_knowledge_file(evidence_refs=refs, **context)
        assert len(json.loads(result)['items']) == 2
        sources = middleware.get_citation_source_from_tool_result('view_knowledge_file', {}, result)
        assert sources[0]['metadata'][0]['evidence_ref']
        parsed, media, _ = await middleware.process_tool_result(
            s.request, 'view_knowledge_file', result, 'builtin', metadata=metadata, user=s.owner
        )
        assert media and media[0]['rag_llm_only']
        assert 'data:image/' not in middleware.tool_result_content(parsed)
    await builtin.view_knowledge_file('file', **context)
    assert s.request.state.rag_model_images == []


@pytest.mark.asyncio
async def test_canonical_file_and_evidence_reads_recheck_revoked_acl(db, app_stack):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    context = {
        '__request__': s.request,
        '__user__': user('reader').model_dump(),
        '__metadata__': {'files': [{'type': 'file', 'id': 'file'}]},
        '__knowledge_enabled__': False,
    }
    found = json.loads(await builtin.query_knowledge_files('blue', **context))
    assert found['items']
    refs = [item['evidence_ref'] for item in found['items']]
    assert 'error' not in json.loads(await builtin.view_knowledge_file('file', **context))
    await db.execute(delete(AccessGrant).where(AccessGrant.id == 'grant'))
    await db.commit()
    assert 'error' in json.loads(await builtin.view_knowledge_file('file', **context))
    assert 'error' in json.loads(await builtin.grep_knowledge_files('blue', **context))
    assert json.loads(await builtin.search_knowledge_files(**context)) == []
    revoked = json.loads(await builtin.view_knowledge_file(evidence_refs=refs, **context))
    assert {item['evidence_ref'] for item in revoked['items']} == set(refs)
    assert all(item['status'] == 'unavailable' and 'content' not in item for item in revoked['items'])
    assert s.request.state.rag_model_images == []


@pytest.mark.asyncio
async def test_file_restriction_does_not_also_query_selected_notes(db, app_stack, monkeypatch):
    s = app_stack
    await upload(s, db)
    await wait_images(s)
    old_path = AsyncMock(side_effect=AssertionError('Unselected notes must not be queried'))
    monkeypatch.setattr(builtin, '_query_knowledge_files_legacy', old_path)
    result = json.loads(
        await builtin.query_knowledge_files(
            'blue',
            file_ids=['file'],
            __request__=s.request,
            __user__=s.owner.model_dump(),
            __metadata__={'files': [{'type': 'file', 'id': 'file'}, {'type': 'note', 'id': 'unselected-note'}]},
        )
    )
    assert result['items']
    old_path.assert_not_awaited()
