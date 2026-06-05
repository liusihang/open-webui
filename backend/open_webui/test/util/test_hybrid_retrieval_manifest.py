import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import pytest

from open_webui.models import retrieval_chunks as retrieval_chunks_mod
from open_webui.models.retrieval_chunks import fetch_active_chunks_by_chunk_uid
from open_webui.retrieval.hybrid import (
    LexicalSearchHit,
    merge_rrf_by_chunk_uid,
    query_manifest_hybrid_search,
)
from open_webui.retrieval.vector.main import SearchResult


class FakeVectorClient:
    def __init__(self, result=None, *, fail=False):
        self.result = result
        self.fail = fail
        self.search_calls = []

    async def search(self, collection_name, vectors, filter=None, limit=10):
        self.search_calls.append(
            {
                "collection_name": collection_name,
                "vectors": vectors,
                "filter": filter,
                "limit": limit,
            }
        )
        if self.fail:
            raise AssertionError("vector search should not be called")
        return self.result


class FakeLexicalClient:
    def __init__(self, hits=None, *, fail=False):
        self.hits = hits or []
        self.fail = fail
        self.search_calls = []

    def search(self, query, *, collection_ids=None, knowledge_ids=None, file_ids=None, k=10):
        self.search_calls.append(
            {
                "query": query,
                "collection_ids": collection_ids,
                "knowledge_ids": knowledge_ids,
                "file_ids": file_ids,
                "k": k,
            }
        )
        if self.fail:
            raise AssertionError("lexical search should not be called")
        return self.hits


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    async def __call__(self, query, prefix=None):
        self.calls.append((query, prefix))
        if isinstance(query, list):
            return [[float(index), 0.25] for index, _ in enumerate(query)]
        return [1.0, 0.25]


def manifest_chunk(chunk_uid, text, metadata=None, *, is_active=True):
    return SimpleNamespace(
        chunk_uid=chunk_uid,
        text=text,
        metadata_=metadata or {"chunk_uid": chunk_uid, "source": "manifest"},
        is_active=is_active,
    )


@pytest.mark.asyncio
async def test_manifest_hydration_preserves_order_and_filters_inactive_or_missing_chunks(monkeypatch):
    rows = [
        manifest_chunk("chunk_c", "manifest c"),
        manifest_chunk("chunk_a", "manifest a"),
        manifest_chunk("chunk_b", "manifest b", is_active=False),
    ]

    class FakeScalarResult:
        def all(self):
            return rows

    class FakeExecuteResult:
        def scalars(self):
            return FakeScalarResult()

    class FakeSession:
        async def execute(self, statement):
            self.statement = statement
            return FakeExecuteResult()

    fake_session = FakeSession()

    @asynccontextmanager
    async def fake_get_async_db_context(db=None):
        yield fake_session

    monkeypatch.setattr(retrieval_chunks_mod, "get_async_db_context", fake_get_async_db_context)

    hydrated = await fetch_active_chunks_by_chunk_uid(
        ["chunk_a", "chunk_missing", "chunk_b", "chunk_c"]
    )

    assert [chunk.chunk_uid for chunk in hydrated] == ["chunk_a", "chunk_c"]


@pytest.mark.asyncio
async def test_hybrid_search_merges_vector_and_lexical_by_chunk_uid_and_hydrates_manifest_text():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-a", "vector-b"]],
            documents=[["derived vector shared", "derived vector only"]],
            metadatas=[[{"chunk_uid": "chunk_shared"}, {"chunk_uid": "chunk_vector"}]],
            distances=[[0.91, 0.72]],
        )
    )
    lexical_client = FakeLexicalClient(
        [
            LexicalSearchHit(chunk_uid="chunk_shared", score=12.0, metadata={"source": "opensearch"}),
            LexicalSearchHit(chunk_uid="chunk_lexical", score=8.0, metadata={"source": "opensearch"}),
        ]
    )
    embedder = FakeEmbedder()

    async def hydrate(chunk_uids):
        assert chunk_uids == ["chunk_shared", "chunk_vector", "chunk_lexical"]
        return [
            manifest_chunk("chunk_shared", "manifest shared", {"source": "sql", "chunk_uid": "chunk_shared"}),
            manifest_chunk("chunk_vector", "manifest vector", {"source": "sql", "chunk_uid": "chunk_vector"}),
            manifest_chunk("chunk_lexical", "manifest lexical", {"source": "sql", "chunk_uid": "chunk_lexical"}),
        ]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["alpha beta"],
        embedding_function=embedder,
        k=3,
        reranking_function=None,
        k_reranker=3,
        r=0.0,
        hybrid_bm25_weight=0.5,
        vector_client=vector_client,
        lexical_client=lexical_client,
        hydrate_chunks=hydrate,
    )

    assert len(embedder.calls) == 1
    assert len(vector_client.search_calls) == 1
    assert len(lexical_client.search_calls) == 1
    assert result["documents"] == [["manifest shared", "manifest vector", "manifest lexical"]]
    assert result["metadatas"] == [
        [
            {"source": "sql", "chunk_uid": "chunk_shared", "score": result["distances"][0][0]},
            {"source": "sql", "chunk_uid": "chunk_vector", "score": result["distances"][0][1]},
            {"source": "sql", "chunk_uid": "chunk_lexical", "score": result["distances"][0][2]},
        ]
    ]
    assert "derived vector shared" not in result["documents"][0]


@pytest.mark.asyncio
async def test_vector_hits_missing_chunk_uid_are_skipped_without_content_hash_merge():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-missing"]],
            documents=[["same text as lexical"]],
            metadatas=[[{"name": "missing chunk uid"}]],
            distances=[[0.99]],
        )
    )
    lexical_client = FakeLexicalClient([LexicalSearchHit(chunk_uid="chunk_lexical", score=10.0)])
    embedder = FakeEmbedder()

    async def hydrate(chunk_uids):
        assert chunk_uids == ["chunk_lexical"]
        return [manifest_chunk("chunk_lexical", "manifest lexical")]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["same text as lexical"],
        embedding_function=embedder,
        k=2,
        reranking_function=None,
        k_reranker=2,
        r=0.0,
        hybrid_bm25_weight=0.5,
        vector_client=vector_client,
        lexical_client=lexical_client,
        hydrate_chunks=hydrate,
    )

    assert result["documents"] == [["manifest lexical"]]
    assert len(vector_client.search_calls) == 1


@pytest.mark.asyncio
async def test_lexical_only_weight_does_not_require_vector_hits():
    lexical_client = FakeLexicalClient([LexicalSearchHit(chunk_uid="chunk_lexical", score=10.0)])

    async def hydrate(chunk_uids):
        return [manifest_chunk("chunk_lexical", "manifest lexical")]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["alpha"],
        embedding_function=FakeEmbedder(),
        k=1,
        reranking_function=None,
        k_reranker=1,
        r=0.0,
        hybrid_bm25_weight=1.0,
        vector_client=FakeVectorClient(fail=True),
        lexical_client=lexical_client,
        hydrate_chunks=hydrate,
    )

    assert result["documents"] == [["manifest lexical"]]


@pytest.mark.asyncio
async def test_vector_only_weight_does_not_require_lexical_hits():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-a"]],
            documents=[["derived vector"]],
            metadatas=[[{"chunk_uid": "chunk_vector"}]],
            distances=[[0.9]],
        )
    )

    async def hydrate(chunk_uids):
        return [manifest_chunk("chunk_vector", "manifest vector")]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["alpha"],
        embedding_function=FakeEmbedder(),
        k=1,
        reranking_function=None,
        k_reranker=1,
        r=0.0,
        hybrid_bm25_weight=0.0,
        vector_client=vector_client,
        lexical_client=FakeLexicalClient(fail=True),
        hydrate_chunks=hydrate,
    )

    assert result["documents"] == [["manifest vector"]]


def test_rrf_ranking_is_deterministic_and_stable_when_scores_tie():
    first = merge_rrf_by_chunk_uid(
        vector_chunk_uids=["chunk_a", "chunk_b"],
        lexical_chunk_uids=["chunk_b", "chunk_a"],
        vector_weight=0.5,
        lexical_weight=0.5,
    )
    second = merge_rrf_by_chunk_uid(
        vector_chunk_uids=["chunk_a", "chunk_b"],
        lexical_chunk_uids=["chunk_b", "chunk_a"],
        vector_weight=0.5,
        lexical_weight=0.5,
    )

    assert first == second
    assert [candidate.chunk_uid for candidate in first] == ["chunk_a", "chunk_b"]


@pytest.mark.asyncio
async def test_reranker_receives_manifest_text_after_hydration():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-a", "vector-b"]],
            documents=[["derived a", "derived b"]],
            metadatas=[[{"chunk_uid": "chunk_a"}, {"chunk_uid": "chunk_b"}]],
            distances=[[0.9, 0.8]],
        )
    )

    async def hydrate(chunk_uids):
        return [
            manifest_chunk("chunk_a", "manifest a", {"chunk_uid": "chunk_a"}),
            manifest_chunk("chunk_b", "manifest b", {"chunk_uid": "chunk_b"}),
        ]

    seen_documents = []

    def rerank(query, documents):
        seen_documents.extend([doc.page_content for doc in documents])
        return [0.1, 0.9]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["alpha"],
        embedding_function=FakeEmbedder(),
        k=2,
        reranking_function=rerank,
        k_reranker=2,
        r=0.0,
        hybrid_bm25_weight=0.0,
        vector_client=vector_client,
        lexical_client=FakeLexicalClient(fail=True),
        hydrate_chunks=hydrate,
    )

    assert seen_documents == ["manifest a", "manifest b"]
    assert result["documents"] == [["manifest b", "manifest a"]]
    assert result["distances"] == [[0.9, 0.1]]
