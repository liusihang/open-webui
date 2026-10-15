"""Real isolated SQL + mocked network; no application, Live DB, OCR or inference calls.

Run with ENABLE_DB_MIGRATIONS=false DATABASE_ENABLE_SESSION_SHARING=true and isolated
SQLite DATABASE_URL/DATA_DIR/STATIC_DIR plus RAG_CORE_TEST_ISOLATED=1.
"""

import hashlib
import importlib.util
import io
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from PIL import Image
from sqlalchemy import create_engine, delete, func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if (
    os.environ.get('ENABLE_DB_MIGRATIONS', '').lower() != 'false'
    or os.environ.get('RAG_CORE_TEST_ISOLATED') != '1'
    or not os.environ.get('DATABASE_URL', '').startswith('sqlite:///')
):
    pytest.skip('RAG core tests require the explicitly isolated environment documented above', allow_module_level=True)

from open_webui.internal.db import Base  # noqa: E402
from open_webui.models.access_grants import AccessGrant  # noqa: E402
from open_webui.models.evidence import (  # noqa: E402
    KnowledgeEvidence as Evidence,
)
from open_webui.models.evidence import (
    KnowledgeEvidenceAsset as Asset,
)
from open_webui.models.evidence import (
    KnowledgeEvidenceEmbedding as Embedding,
)
from open_webui.models.evidence import (
    KnowledgeVectorSpace as Space,
)
from open_webui.models.files import File  # noqa: E402
from open_webui.models.groups import Group, GroupMember  # noqa: E402
from open_webui.models.knowledge import Knowledge, KnowledgeFile  # noqa: E402
from open_webui.models.messages import Message  # noqa: E402,F401
from open_webui.models.retrieval_chunks import RetrievalChunk as Chunk  # noqa: E402
from open_webui.models.retrieval_indexes import RetrievalIndexJob as Job  # noqa: E402
from open_webui.models.retrieval_indexes import RetrievalIndexState as State  # noqa: E402
from open_webui.retrieval.evidence import EvidenceService, ResolvedScope, resolve_scope  # noqa: E402
from open_webui.retrieval.hybrid import reciprocal_rank_fusion  # noqa: E402
from open_webui.retrieval.indexing import IndexingService  # noqa: E402
from open_webui.retrieval.lexical.opensearch import OpenSearchLexicalAdapter  # noqa: E402
from open_webui.retrieval.vector.embedding_adapter import (  # noqa: E402
    EvidenceEmbeddingAdapter,
    validate_image,
    validate_vector,
)
from open_webui.retrieval.vector.main import SearchResult  # noqa: E402
from open_webui.utils.access_control.files import has_access_to_file  # noqa: E402,F401

OWNER = SimpleNamespace(id='owner', role='user')
READER = SimpleNamespace(id='reader', role='user')
STRANGER = SimpleNamespace(id='stranger', role='user')
SCOPE = ResolvedScope(('kb',), ())
CONFIG = dict(
    distance_metric='cosine',
    query_prefix=None,
    document_prefix=None,
    normalization='model-native',
    image_preprocessing='fixture-v1',
)


def png():
    stream = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(stream, 'PNG')
    return stream.getvalue()


def asset(*, image=png(), **extra):
    return dict(
        storage_uri='/fixture/image.png',
        sha256=hashlib.sha256(image).hexdigest(),
        mime_type='image/png',
        width=8,
        height=8,
        page_index=2,
        caption='blue square',
        **extra,
    )


def text_chunk(identity='existing-vector-42', text='A blue square appears in the sample.'):
    return dict(id=identity, text=text, metadata={'file_id': 'file', 'page_index': 2})


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'fixture.sqlite'))
    async with engine.begin() as connection:
        # Include the empty channel/chat/note/model tables consulted by official negative ACL checks.
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(
            File(
                id='file',
                user_id='owner',
                filename='fixture.pdf',
                path='/fixture/document.pdf',
                meta={},
                data={},
                created_at=1,
                updated_at=1,
            )
        )
        session.add(
            Knowledge(id='kb', user_id='owner', name='Shared fixture', description='', created_at=1, updated_at=1)
        )
        session.add(
            KnowledgeFile(id='kf', knowledge_id='kb', file_id='file', user_id='owner', created_at=1, updated_at=1)
        )
        session.add(
            AccessGrant(
                id='grant',
                resource_type='knowledge',
                resource_id='kb',
                principal_type='user',
                principal_id='reader',
                permission='read',
                created_at=1,
            )
        )
        await session.commit()
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def stack(db):
    vectors = SimpleNamespace(
        search=AsyncMock(
            return_value=SearchResult(
                ids=[['existing-vector-42']], documents=[['not trusted']], metadatas=[[{}]], distances=[[0.0]]
            )
        ),
        upsert=AsyncMock(),
        delete=AsyncMock(),
    )
    index = IndexingService(db=db, vector_client=vectors)
    space = await index.register_space(
        embedding_model='Qwen3-VL-Embedding-2B',
        embedding_dim=2048,
        model_revision='fixture-revision',
        encoding_config=CONFIG,
        verified=True,
        supports_images=True,
    )
    text = AsyncMock(return_value=[1.0] + [0.0] * 2047)
    post = AsyncMock(return_value={'data': [{'embedding': [1.0] + [0.0] * 2047}]})
    encoder = EvidenceEmbeddingAdapter(
        text_embedding_function=text,
        model=space.embedding_model,
        url='http://never-called.invalid/v1',
        config_fingerprint=space.config_fingerprint,
        post_json=post,
    )
    reader = AsyncMock(return_value=png())
    index.embedding, index.asset_reader = encoder, reader
    search = EvidenceService(db=db, vector_client=vectors, embedding=encoder, asset_reader=reader)
    return SimpleNamespace(
        index=index, search=search, vectors=vectors, encoder=encoder, text=text, post=post, space=space, reader=reader
    )


async def seed(stack, **extra):
    return await stack.index.index_file(
        file_id='file',
        file_version=1,
        knowledge_id='kb',
        collection_name='existing-collection',
        chunks=[text_chunk()],
        space_id=stack.space.id,
        **extra,
    )


@pytest.mark.asyncio
async def test_mapping_existing_text_is_zero_embedding_and_idempotent(db, stack):
    first = await seed(stack)
    await seed(stack)
    assert first['text_embedding_calls'] == 0
    stack.text.assert_not_called()
    stack.post.assert_not_called()
    stack.vectors.upsert.assert_not_called()
    mapping = (await db.execute(select(Embedding))).scalar_one()
    assert mapping.vector_backend_collection == 'existing-collection'
    assert mapping.vector_backend_id == 'existing-vector-42'
    assert (await db.scalar(select(func.count()).select_from(Chunk))) == 1
    assert (await db.scalar(select(func.count()).select_from(Evidence))) == 1
    assert (await db.scalar(select(func.count()).select_from(Job))) == 1


@pytest.mark.asyncio
async def test_vector_rank_is_global_within_one_verified_space(db, stack):
    async def search(collection, **kwargs):
        positive = collection == 'positive-file'
        return SearchResult(
            ids=[['pos' if positive else 'neg']],
            distances=[[0.95 if positive else 0.2]],
            documents=[['']],
            metadatas=[[{}]],
        )

    stack.vectors.search.side_effect = search
    groups = {
        (stack.space.id, name): {'space': stack.space, 'vectors': {identity: [identity]}, 'files': {'file'}}
        for name, identity in [('negative-file', 'neg'), ('positive-file', 'pos')]
    }
    ranked, _ = await stack.search._rankings(groups, ['neg', 'pos'], 'red square', None, None, OWNER, 5, None)
    assert ranked[0][0] == 'pos'


@pytest.mark.asyncio
async def test_image_upsert_obeys_official_dictionary_contract(db, stack):
    def official_upsert(collection, items):
        assert len(items[0]['vector']) == 2048
        assert items[0]['metadata']['file_id'] == 'file'

    stack.vectors.upsert.side_effect = official_upsert
    await seed(stack)
    await stack.index.index_assets(
        file_id='file',
        file_version=1,
        collection_name='file-file',
        knowledge_id='kb',
        space_id=stack.space.id,
        assets=[asset()],
    )
    mapping = (
        await db.scalars(select(Embedding).where(Embedding.vector_backend_collection == 'file-file-images'))
    ).one()
    assert mapping.embedding_status == 'ready'


@pytest.mark.asyncio
async def test_unknown_minilm_is_kept_but_not_searched(db, stack):
    legacy = await stack.index.register_space(
        embedding_model='MiniLM', embedding_dim=None, encoding_config={}, space_id='legacy-space'
    )
    await stack.index.index_file(
        file_id='file',
        file_version=1,
        knowledge_id='kb',
        collection_name='old',
        chunks=[text_chunk()],
        space_id=legacy.id,
    )
    assert (await db.execute(select(Embedding))).scalar_one().embedding_status == 'quarantined'
    result = await stack.search.search('square', user=READER, resolved_scope=SCOPE)
    assert result['items'] == [] and result['warnings']
    stack.text.assert_not_called()
    stack.vectors.search.assert_not_called()


@pytest.mark.asyncio
async def test_four_directions_use_true_image_protocol(db, stack):
    result = await seed(stack, assets=[asset()])
    assert result['images']['status'] == 'ready'
    image_mapping = (
        await db.execute(
            select(Embedding).join(Evidence, Evidence.id == Embedding.evidence_id).where(Evidence.modality == 'image')
        )
    ).scalar_one()
    image_ref = (await db.execute(select(Evidence.evidence_ref).where(Evidence.modality == 'image'))).scalar_one()

    async def search(collection, **kwargs):
        ids = [image_mapping.vector_backend_id] if collection.endswith('-images') else ['existing-vector-42']
        assert kwargs['filter'] == {'file_id': {'$in': ['file']}}
        assert len(kwargs['vectors'][0]) == 2048
        return SearchResult(ids=[ids], documents=[['irrelevant']], metadatas=[[{}]], distances=[[0.01]])

    stack.vectors.search.side_effect = search
    for image_query in (False, True):
        for modality in ('text', 'image'):
            answer = await stack.search.search(
                'blue square' if not image_query else '',
                query_image_refs=(image_ref,) if image_query else (),
                modalities=(modality,),
                user=READER,
                resolved_scope=SCOPE,
            )
            assert len(answer['items']) == 1
            assert answer['items'][0]['modality'] == modality
            assert 'storage_uri' not in str(answer)
    assert stack.text.await_count == 2
    assert stack.post.await_count == 3  # one indexed image plus two image-query directions
    for call in stack.post.await_args_list:
        payload = call.kwargs['payload']
        assert 'messages' in payload and 'input' not in payload
        assert payload['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,')


@pytest.mark.asyncio
async def test_shared_kb_acl_does_not_require_file_ownership(db, stack):
    await seed(stack)
    assert (await resolve_scope(READER, knowledge_ids=['kb'], db=db)).knowledge_ids == ('kb',)
    assert (await stack.search.search('square', user=READER, resolved_scope=SCOPE))['items']
    stack.vectors.search.reset_mock()
    assert (await stack.search.search('square', user=STRANGER, resolved_scope=SCOPE))['items'] == []
    stack.vectors.search.assert_not_called()
    await db.execute(delete(AccessGrant).where(AccessGrant.id == 'grant'))
    await db.commit()
    assert (await stack.search.search('square', user=READER, resolved_scope=SCOPE))['items'] == []


@pytest.mark.asyncio
async def test_revocation_during_search_never_reaches_reranker(db, stack):
    await seed(stack)

    async def revoke(*args, **kwargs):
        await db.execute(delete(AccessGrant))
        await db.commit()
        return SearchResult(ids=[['existing-vector-42']], documents=[['private']], metadatas=[[{}]], distances=[[0.0]])

    stack.vectors.search.side_effect = revoke
    stack.search.reranker = AsyncMock()
    assert (await stack.search.search('square', user=READER, resolved_scope=SCOPE))['items'] == []
    stack.search.reranker.assert_not_called()


@pytest.mark.asyncio
async def test_update_text_publishes_without_old_images_and_rejects_stale_version(db, stack):
    await seed(stack, assets=[asset()])
    old_refs = list((await db.scalars(select(Evidence.evidence_ref))).all())
    stack.post.side_effect = RuntimeError('simulated OOM')
    result = await stack.index.index_file(
        file_id='file',
        file_version=2,
        knowledge_id='kb',
        collection_name='existing-collection',
        chunks=[text_chunk('new-vector', 'new source text')],
        assets=[asset()],
        space_id=stack.space.id,
    )
    assert result['text_mapped'] == 1 and result['images']['status'] == 'failed'
    read = await stack.search.read(old_refs, user=READER, resolved_scope=SCOPE)
    assert all(item['status'] == 'unavailable' for item in read['items'])
    visible = await stack.search._rows(SCOPE)
    assert all(parts[0].file_version == 2 for parts in visible.values())
    assert any(parts[0].modality == 'text' for parts in visible.values())
    with pytest.raises(ValueError, match='stale'):
        await seed(stack)


@pytest.mark.asyncio
async def test_accept_new_version_hides_old_content_before_slow_parser(db, stack):
    await seed(stack)
    assert (await stack.index.accept_file_version('file', 2))['status'] == 'indexing'
    assert not (await stack.search._rows(SCOPE))
    stack.text.assert_not_called()


@pytest.mark.asyncio
async def test_deleted_sources_disappear_even_when_cleanup_fails(db, stack):
    await seed(stack)
    ref = (await db.scalars(select(Evidence.evidence_ref))).one()
    stack.vectors.delete.side_effect = RuntimeError('offline')
    result = await stack.index.deactivate_file('file')
    assert result['status'] == 'failed'
    assert (await db.scalars(select(Chunk))).one().text is None
    assert (await stack.search.read([ref], user=READER, resolved_scope=SCOPE))['items'][0]['status'] == 'unavailable'
    with pytest.raises(ValueError, match='deleted'):
        await seed(stack)


@pytest.mark.asyncio
async def test_retry_old_delete_does_not_invalidate_new_file_version(db, stack):
    await seed(stack)
    stack.vectors.delete.side_effect = RuntimeError('offline')
    await stack.index.deactivate_file('file')
    job_id = (await db.scalars(select(Job.job_id).where(Job.index_kind == 'delete'))).one()
    space_id = stack.space.id
    await stack.index.index_file(
        file_id='file',
        file_version=2,
        knowledge_id='kb',
        collection_name='existing-collection',
        chunks=[text_chunk('new-vector', 'new source')],
        space_id=space_id,
    )
    stack.vectors.delete.side_effect = None
    assert (await stack.index.run_job(job_id))['status'] == 'succeeded'
    assert await stack.search._rows(SCOPE)
    assert all(parts[0].file_version == 2 for parts in (await stack.search._rows(SCOPE)).values())


@pytest.mark.asyncio
async def test_image_permission_revoked_while_reading_is_denied(db, stack):
    await seed(stack, assets=[asset()])
    ref = (await db.scalars(select(Evidence.evidence_ref).where(Evidence.modality == 'image'))).one()

    async def revoked_reader(asset):
        await db.execute(delete(AccessGrant))
        await db.commit()
        return png()

    stack.search.asset_reader = revoked_reader
    with pytest.raises(PermissionError):
        await stack.search.image_content(ref, user=READER, resolved_scope=SCOPE)


@pytest.mark.asyncio
async def test_remove_kb_membership_is_immediate(db, stack):
    await seed(stack)
    await db.execute(delete(KnowledgeFile))
    await db.commit()
    assert (await stack.search.search('square', user=READER, resolved_scope=SCOPE))['items'] == []


@pytest.mark.asyncio
async def test_hybrid_off_does_not_construct_or_enqueue_lexical(db, stack):
    factory = Mock(side_effect=AssertionError('must remain lazy'))
    stack.index.lexical_factory = stack.search.lexical_factory = factory
    await seed(stack)
    await stack.search.search('square', user=READER, resolved_scope=SCOPE)
    await stack.index.deactivate_file('file')
    factory.assert_not_called()
    assert not (await db.scalars(select(Job).where(Job.index_kind == 'lexical'))).all()


@pytest.mark.asyncio
async def test_hybrid_on_rrf_acl_filter_and_keyword_outage(db, stack):
    await seed(stack)
    ref = (await db.scalars(select(Evidence.evidence_ref))).one()
    lexical = SimpleNamespace(search=AsyncMock(return_value=['unauthorized-ref', ref]), index=AsyncMock())
    stack.index.lexical_enabled = stack.search.lexical_enabled = True
    stack.index.lexical_factory = stack.search.lexical_factory = lambda: lexical
    await stack.index.index_lexical(file_id='file', file_version=1, knowledge_id='kb')
    stack.text.assert_not_called()
    answer = await stack.search.search('square', user=READER, resolved_scope=SCOPE)
    assert [item['evidence_ref'] for item in answer['items']] == [ref]
    assert answer['items'][0]['score_type'] == 'rrf'
    lexical.search.side_effect = RuntimeError('offline')
    answer = await stack.search.search('square', user=READER, resolved_scope=SCOPE)
    assert answer['items'] and answer['warnings']


@pytest.mark.asyncio
async def test_same_turn_dedup_keeps_different_document_identity(db, stack):
    await seed(stack)
    seen = set()
    first = await stack.search.search('square', user=READER, resolved_scope=SCOPE, seen_refs=seen)
    assert len(first['items']) == 1
    assert (await stack.search.search('square', user=READER, resolved_scope=SCOPE, seen_refs=seen))['items'] == []


@pytest.mark.asyncio
async def test_changed_source_cannot_reuse_same_version(db, stack):
    await seed(stack)
    with pytest.raises(ValueError, match='new file version'):
        await stack.index.index_file(
            file_id='file',
            file_version=1,
            knowledge_id='kb',
            collection_name='existing-collection',
            chunks=[text_chunk('new-id', 'changed content')],
        )
    assert (await db.scalar(select(func.count()).select_from(Evidence))) == 1


@pytest.mark.asyncio
async def test_missing_file_metadata_is_backfill_not_reembedding(db, stack):
    chunk = text_chunk()
    chunk['metadata'] = {}
    result = await stack.index.index_file(
        file_id='file',
        file_version=1,
        knowledge_id='kb',
        collection_name='existing-collection',
        chunks=[chunk],
        space_id=stack.space.id,
    )
    assert result['metadata_backfill_ids'] == ['existing-vector-42']
    assert (await db.scalars(select(Embedding))).one().embedding_status == 'metadata_pending'
    await seed(stack)  # Represents metadata-only backfill followed by re-mapping, same IDs/version.
    assert (await db.scalars(select(Embedding))).one().embedding_status == 'ready'
    stack.text.assert_not_called()
    stack.vectors.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_official_group_and_public_grants_are_honored(db, stack):
    await seed(stack)
    await db.execute(delete(AccessGrant))
    db.add(Group(id='group', user_id='owner', name='team', description='', created_at=1, updated_at=1))
    db.add(GroupMember(id='member', group_id='group', user_id='reader', created_at=1, updated_at=1))
    db.add(
        AccessGrant(
            id='group-grant',
            resource_type='knowledge',
            resource_id='kb',
            principal_type='group',
            principal_id='group',
            permission='read',
            created_at=1,
        )
    )
    await db.commit()
    assert (await stack.search.search('square', user=READER, resolved_scope=SCOPE))['items']
    await db.execute(delete(AccessGrant))
    db.add(
        AccessGrant(
            id='public-grant',
            resource_type='knowledge',
            resource_id='kb',
            principal_type='user',
            principal_id='*',
            permission='read',
            created_at=1,
        )
    )
    await db.commit()
    assert (await stack.search.search('square', user=STRANGER, resolved_scope=SCOPE))['items']


@pytest.mark.asyncio
async def test_partial_image_retry_only_encodes_missing_image(db, stack):
    success = {'data': [{'embedding': [1.0] + [0.0] * 2047}]}
    stack.post.side_effect = [success, RuntimeError('simulated OOM'), success]
    assets = [asset(), {**asset(), 'page_index': 3}]
    result = await seed(stack, assets=assets)
    assert result['images']['status'] == 'partial' and result['images']['indexed'] == 1
    assert (await db.scalars(select(Embedding).where(Embedding.embedding_status == 'failed'))).one()
    retry = await stack.index.index_assets(
        file_id='file',
        file_version=1,
        collection_name='existing-collection',
        knowledge_id='kb',
        space_id=stack.space.id,
        assets=assets,
    )
    assert retry['status'] == 'ready'
    assert stack.post.await_count == 3
    stack.text.assert_not_called()


@pytest.mark.asyncio
async def test_backend_none_is_failure_not_empty_success(db, stack):
    await seed(stack)
    stack.vectors.search.return_value = None
    with pytest.raises(RuntimeError, match='all retrieval branches failed'):
        await stack.search.search('square', user=READER, resolved_scope=SCOPE)


@pytest.mark.asyncio
async def test_keyword_branch_can_survive_encoder_outage(db, stack):
    await seed(stack)
    ref = (await db.scalars(select(Evidence.evidence_ref))).one()
    stack.text.side_effect = RuntimeError('simulated unavailable encoder')
    stack.search.lexical_enabled = True
    stack.search.lexical_factory = lambda: SimpleNamespace(search=AsyncMock(return_value=[ref]))
    result = await stack.search.search('square', user=READER, resolved_scope=SCOPE)
    assert result['items'] and 'Vector encoding unavailable.' in result['warnings']


@pytest.mark.asyncio
async def test_cross_kb_same_source_is_not_injected_twice_but_other_files_are(db, stack):
    await seed(stack)
    db.add(Knowledge(id='kb2', user_id='owner', name='Second KB', description='', created_at=1, updated_at=1))
    db.add(KnowledgeFile(id='kf2', knowledge_id='kb2', file_id='file', user_id='owner', created_at=1, updated_at=1))
    await db.commit()
    await stack.index.index_file(
        file_id='file',
        file_version=1,
        knowledge_id='kb2',
        collection_name='existing-collection',
        chunks=[text_chunk()],
        space_id=stack.space.id,
    )
    answer = await stack.search.search('square', user=OWNER, resolved_scope=ResolvedScope(('kb', 'kb2')))
    assert len(answer['items']) == 1
    # Identical content from a *different file* retains its independent source.
    db.add(File(id='other-file', user_id='owner', filename='other.pdf', meta={}, data={}, created_at=1, updated_at=1))
    db.add(
        KnowledgeFile(id='kf3', knowledge_id='kb', file_id='other-file', user_id='owner', created_at=1, updated_at=1)
    )
    await db.commit()
    other_chunk = text_chunk('other-vector')
    other_chunk['metadata']['file_id'] = 'other-file'
    await stack.index.index_file(
        file_id='other-file',
        file_version=1,
        knowledge_id='kb',
        collection_name='existing-collection',
        chunks=[other_chunk],
        space_id=stack.space.id,
    )
    stack.vectors.search.return_value = SearchResult(
        ids=[['existing-vector-42', 'other-vector']], documents=[['', '']], metadatas=[[{}, {}]], distances=[[0.0, 0.0]]
    )
    answer = await stack.search.search('square', user=OWNER, resolved_scope=ResolvedScope(('kb', 'kb2')))
    assert {item['source']['file_id'] for item in answer['items']} == {'file', 'other-file'}


@pytest.mark.asyncio
async def test_image_retry_reuses_success_and_never_reencodes_text(db, stack):
    await seed(stack, assets=[asset()])
    calls = stack.post.await_count
    answer = await stack.index.index_assets(
        file_id='file',
        file_version=1,
        collection_name='existing-collection',
        knowledge_id='kb',
        space_id=stack.space.id,
        assets=[asset()],
    )
    assert answer['status'] == 'ready' and stack.post.await_count == calls
    stack.text.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_only_new_pending_running_jobs(db, stack):
    await seed(stack)
    now = 1
    for identity, executor, kind in [('historical', 'legacy', 'project'), ('interrupted', 'evidence-v1', 'image')]:
        db.add(
            Job(
                job_id=identity,
                executor_version=executor,
                index_kind=kind,
                file_id='file',
                file_version=1,
                status='running',
                retry_count=0,
                max_retries=3,
                created_at=now,
                updated_at=now,
            )
        )
    await db.commit()
    ids = await stack.index.recover_jobs()
    assert ids == ['interrupted']
    assert (await db.get(Job, 'historical')).status == 'running'
    with pytest.raises(ValueError, match='historical'):
        await stack.index.run_job('historical')


@pytest.mark.asyncio
async def test_interrupted_image_job_recovers_from_persisted_assets(db, stack):
    stack.post.side_effect = RuntimeError('simulated process failure')
    result = await seed(stack, assets=[asset()])
    job_id = result['images']['job_id']
    job = await db.get(Job, job_id)
    job.status = 'running'
    await db.commit()
    assert await stack.index.recover_jobs() == [job_id]
    stack.post.side_effect = None
    assert (await stack.index.run_job(job_id))['status'] == 'ready'
    stack.text.assert_not_called()


@pytest.mark.parametrize(
    'vector', [[1.0] * 384, [1.0] * 2049, [float('nan')] * 2048, [float('inf')] * 2048, [True] * 2048, [0.0] * 2048]
)
def test_reject_invalid_vectors(vector):
    with pytest.raises(ValueError):
        validate_vector(vector, 2048)


@pytest.mark.parametrize('mime,data', [('image/jpeg', png()), ('text/html', b'not an image'), ('image/png', b'fake')])
def test_actual_image_validation(mime, data):
    with pytest.raises(ValueError):
        validate_image(data, mime)


@pytest.mark.asyncio
async def test_model_dimensions_checked_before_backend_padding(db, stack):
    await seed(stack)
    stack.text.return_value = [1.0] * 384
    with pytest.raises(ValueError, match='2048'):
        await stack.search.search('square', user=READER, resolved_scope=SCOPE)
    stack.vectors.search.assert_not_called()


@pytest.mark.asyncio
async def test_arbitrary_query_image_url_rejected_before_reader(db, stack):
    await seed(stack)
    with pytest.raises(ValueError):
        await stack.search.search(
            '', query_image_refs=['http://private-server/secret'], user=READER, resolved_scope=SCOPE
        )
    stack.reader.assert_not_called()


@pytest.mark.asyncio
async def test_uploaded_image_file_id_is_authorized_and_encoded(db, stack, tmp_path):
    await seed(stack)
    image_path = tmp_path / 'query.png'
    image_path.write_bytes(png())
    db.add(
        File(
            id='query-image',
            user_id='owner',
            filename='query.png',
            path=str(image_path),
            meta={'content_type': 'image/png'},
            data={},
            created_at=1,
            updated_at=1,
        )
    )
    await db.commit()
    result = await stack.search.search(
        '', query_image_refs=['query-image'], modalities=('text',), user=OWNER, resolved_scope=SCOPE
    )
    assert result['items'] and result['items'][0]['modality'] == 'text'
    stack.post.assert_awaited_once()
    stack.text.assert_not_called()
    with pytest.raises(PermissionError):
        await stack.search.resolve_query_image('query-image', user=STRANGER, resolved_scope=SCOPE)


def test_rrf_deduplicates_per_branch_and_preserves_identity():
    result = reciprocal_rank_fusion(['a', 'a', 'b'], ['b', 'c'], count=3)
    assert result[0][0] == 'b'
    assert len(result) == 3


@pytest.mark.asyncio
async def test_opensearch_bulk_search_delete_protocol():
    request = AsyncMock(side_effect=[{'errors': False}, {'hits': {'hits': [{'_id': 'ref'}]}}, {'errors': False}])
    adapter = OpenSearchLexicalAdapter(url='https://configured.invalid', request_json=request)
    await adapter.index([{'evidence_ref': 'ref', 'text': 'square'}])
    assert await adapter.search('square', evidence_refs=['ref']) == ['ref']
    assert request.await_args.kwargs['payload']['query']['bool']['filter'] == [{'ids': {'values': ['ref']}}]
    await adapter.delete(['ref'])


def test_migration_real_sql_roundtrip_and_schema_matches_models(tmp_path):
    path = Path(__file__).parents[1] / 'open_webui/migrations/versions/c92e41b7d630_add_unified_evidence_indexes.py'
    spec = importlib.util.spec_from_file_location('core_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.sqlite'))
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            inspector = inspect(connection)
            for table in (Chunk, State, Job, Asset, Evidence, Space, Embedding):
                assert set(table.__table__.columns.keys()) == {
                    col['name'] for col in inspector.get_columns(table.__tablename__)
                }
            assert len(inspector.get_table_names()) == 7
            with pytest.raises(RuntimeError, match='Legacy evidence'):
                module.upgrade()
            module.downgrade()
            assert not inspect(connection).get_table_names()
    engine.dispose()
