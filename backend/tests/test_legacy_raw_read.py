"""Explicit legacy query routing; unknown sources and write paths stay closed."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from open_webui.models.evidence import KnowledgeEvidenceEmbedding as Embedding
from open_webui.retrieval.vector.embedding_adapter import LegacyReadEmbeddingAdapter, QueryEmbeddingRouter
from sqlalchemy import select
from test_rag_core import OWNER, READER, SCOPE, STRANGER, seed
from test_rag_core import db as db_fixture
from test_rag_core import stack as stack_fixture

db = db_fixture
stack = stack_fixture

TOOLS = Path(__file__).resolve().parents[2] / 'tools/migration/v0120'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('legacy_copy', TOOLS / 'legacy_raw_reuse.py')
legacy_copy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy_copy)


def adapter(space):
    return LegacyReadEmbeddingAdapter(
        text_embedding_function=AsyncMock(return_value=[1.0] + [0.0] * 2047),
        model=space.embedding_model, url=legacy_copy.RAW_URL,
        config_fingerprint=space.config_fingerprint,
    )


@pytest.mark.asyncio
async def test_legacy_real_sql_query_acl_and_revision_warning(db, stack):
    await seed(stack)
    space = stack.space
    for key, value in legacy_copy.profile('e' * 64, images=False, now=1).items():
        if key != 'id':
            setattr(space, key, value)
    mapping = (await db.scalars(select(Embedding))).one()
    mapping.config_fingerprint = space.config_fingerprint
    await db.commit()
    raw = adapter(space)
    stack.search.embedding = QueryEmbeddingRouter({space.id: raw})
    for user in (OWNER, READER):
        result = await stack.search.search('query', user=user, resolved_scope=SCOPE)
        assert len(result['items']) == 1
        assert 'historical encoder revision is unverified' in result['warnings'][0]
    assert not (await stack.search.search('query', user=STRANGER, resolved_scope=SCOPE))['items']
    raw.text_embedding_function.assert_awaited()
    raw.text_embedding_function.assert_awaited_with('query', prefix=None, user=READER)
    with pytest.raises(ValueError):
        await raw.encode(space=space, query_text='do not write', document=True)
    with pytest.raises(ValueError):
        await stack.search.embedding.encode(space=space, query_text='do not write', document=True)
    space.verification_status = 'quarantined'
    await db.commit()
    assert not (await stack.search.search('query', user=OWNER, resolved_scope=SCOPE))['items']


@pytest.mark.parametrize('changed', [
    {'verification_status': 'verified'}, {'active': False}, {'embedding_dim': 384},
    {'embedding_model': 'other'}, {'config_fingerprint': 'other'},
    {'encoding_config': {}},
])
def test_legacy_adapter_rejects_wrong_profile(changed):
    from types import SimpleNamespace

    values = legacy_copy.profile('e' * 64, images=False, now=1)
    space = SimpleNamespace(**values)
    raw = adapter(space)
    for key, value in changed.items():
        setattr(space, key, value)
    with pytest.raises(ValueError):
        raw.validate_space(space)


def test_source_family_accepts_actual_dimension_but_not_wrong_metadata():
    import json

    vector = {'vector': json.dumps([1.0] + [0.0] * 2047), 'vmetadata': {
        'embedding_config': "{'engine': 'openai', 'model': 'Qwen3-VL-Embedding-2B'}",
        'embedding_config_hash': legacy_copy.TEXT_METADATA_HASH,
    }}
    assert legacy_copy.accepted_vector(vector)
    vector['vmetadata']['embedding_config_hash'] = 'unobserved'
    assert not legacy_copy.accepted_vector(vector)
    vector['vmetadata']['embedding_config_hash'] = legacy_copy.TEXT_METADATA_HASH
    vector['vector'] = json.dumps([1.0] * 384)
    assert not legacy_copy.accepted_vector(vector)


@pytest.mark.asyncio
async def test_runtime_legacy_wiring_is_query_only_and_bound_to_current_endpoint(db, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from open_webui.models.evidence import KnowledgeVectorSpace as Space
    from open_webui.retrieval import integration, utils

    space = Space(**legacy_copy.profile('e' * 64, images=False, now=1))
    db.add(space)
    await db.commit()

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(integration, 'get_async_db', session)
    encode = AsyncMock(return_value=[1.0] + [0.0] * 2047)
    monkeypatch.setattr(utils, 'get_embedding_function', lambda *a, **k: encode)
    config = {'rag.embedding_engine': 'openai', 'rag.embedding_model': legacy_copy.MODEL,
              'rag.openai.api_base_url': legacy_copy.RAW_URL.replace('/v1', '/qwen3vl-retrieval-v1/v1')}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=encode)))
    adapters = await integration._legacy_query_adapters(request, config)
    assert space.id in adapters
    with pytest.raises(ValueError):
        adapters[space.id].validate_space(space, document=True)
    config['rag.openai.api_base_url'] = 'http://different.invalid/qwen3vl-retrieval-v1/v1'
    assert not await integration._legacy_query_adapters(request, config)


def test_search_result_keeps_cosine_metric_not_rrf_and_unknown_anchor():
    from open_webui.retrieval.integration import collection_search_result

    item = {
        'evidence_ref': 'ref', 'content': 'stored text', 'source': {'file_id': 'f', 'anchor': None},
        'modality': 'text', 'citation_key': 'citation', 'score': 1 / 61,
        'vector_scores': {'legacy-space': 0.97},
    }
    result = collection_search_result([item, {**item, 'vector_scores': {}}])
    assert result.ids == [['ref']]
    assert result.distances == [[0.97]]
    assert result.metadatas[0][0]['rrf_score'] == 1 / 61
    assert result.metadatas[0][0]['score_type'] == 'normalized_cosine_similarity'
    assert result.metadatas[0][0]['anchor'] == 'None' or result.metadatas[0][0]['anchor'] is None


@pytest.mark.asyncio
async def test_public_query_doc_uses_legacy_service_and_preserves_nonlocal_route(db, stack, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI, HTTPException
    from httpx import ASGITransport, AsyncClient
    from open_webui.models.knowledge import Knowledge, Knowledges
    from open_webui.retrieval import integration
    from open_webui.retrieval.evidence import resolve_scope
    from open_webui.routers import retrieval
    from open_webui.utils.auth import get_verified_user

    await seed(stack)
    space = stack.space
    for key, value in legacy_copy.profile('e' * 64, images=False, now=1).items():
        if key != 'id':
            setattr(space, key, value)
    mapping = (await db.scalars(select(Embedding))).one()
    mapping.config_fingerprint = space.config_fingerprint
    await db.commit()
    raw = adapter(space)
    stack.search.embedding = QueryEmbeddingRouter({space.id: raw})

    async def get_knowledge(identity, **kwargs):
        return await db.get(Knowledge, identity)

    async def scope(user, **kwargs):
        return await resolve_scope(user, db=db, **kwargs)

    async def services(*args, **kwargs):
        return stack.index, stack.search, None, {}

    async def access(collections, user):
        if collections == ['kb'] and not (await scope(user, knowledge_ids=['kb'])).knowledge_ids:
            raise HTTPException(403, 'not authorized')

    monkeypatch.setattr(Knowledges, 'get_knowledge_by_id', get_knowledge)
    monkeypatch.setattr(integration, 'resolve_scope', scope)
    monkeypatch.setattr(integration, 'services', services)
    monkeypatch.setattr(retrieval, '_validate_collection_access', access)
    monkeypatch.setattr(
        retrieval.Config, 'get_many', AsyncMock(return_value={'rag.top_k': 5, 'rag.enable_hybrid_search': False})
    )
    original = AsyncMock(return_value=[1.0] * 2048)
    app = FastAPI()
    app.state.EMBEDDING_FUNCTION = original
    app.include_router(retrieval.router, prefix='/retrieval')
    current = {'user': OWNER}
    app.dependency_overrides[get_verified_user] = lambda: current['user']
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://isolated.test') as client:
        for principal in (OWNER, READER):
            current['user'] = principal
            response = await client.post(
                '/retrieval/query/doc', json={'collection_name': 'kb', 'query': 'query', 'k': 2}
            )
            assert response.status_code == 200
            result = response.json()
            assert len(result['ids'][0]) == 1
            assert result['distances'] == [[0.0]]
            assert result['metadatas'][0][0]['score_type'] == 'normalized_cosine_similarity'
        original.assert_not_awaited()
        current['user'] = STRANGER
        response = await client.post('/retrieval/query/doc', json={'collection_name': 'kb', 'query': 'query'})
        assert response.status_code == 403
        current['user'] = OWNER
        fallback = SimpleNamespace(ids=[['official']], distances=[[0.8]], documents=[['text']], metadatas=[[{}]])
        monkeypatch.setattr(retrieval, 'query_doc', lambda **kwargs: fallback.__dict__)
        response = await client.post('/retrieval/query/doc', json={'collection_name': 'nonlocal', 'query': 'query'})
        assert response.status_code == 200 and response.json()['ids'] == [['official']]
        original.assert_awaited_once()
