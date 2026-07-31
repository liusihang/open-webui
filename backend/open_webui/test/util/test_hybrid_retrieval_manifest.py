import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import pytest

from open_webui.models import retrieval_chunks as retrieval_chunks_mod
from open_webui.models.retrieval_chunks import fetch_active_chunks_by_chunk_uid
from open_webui.retrieval.hybrid import (
    HybridManifestNotReady,
    LexicalSearchHit,
    merge_rrf_by_chunk_uid,
    query_manifest_hybrid_search,
)
from open_webui.retrieval import hybrid as hybrid_mod
from open_webui.retrieval import utils as retrieval_utils
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


class SemanticEmbedder:
    def __init__(self):
        self.calls = []

    async def __call__(self, query, prefix=None):
        self.calls.append((query, prefix))
        if isinstance(query, list):
            if query == ["alpha"]:
                return [[1.0, 0.0]]
            return [
                [1.0, 0.0] if text == "manifest high" else [0.0, 1.0]
                for text in query
            ]
        return [1.0, 0.0]


class CrossDocumentSemanticEmbedder:
    def __init__(self):
        self.calls = []

    async def __call__(self, query, prefix=None):
        self.calls.append((query, prefix))
        if isinstance(query, list):
            if query == ["Compare the Transformer architecture with Segment Anything prompts"]:
                return [[1.0, 0.0]]
            vectors_by_text = {
                "SAM promptable segmentation": [1.0, 0.0],
                "SAM mask decoder": [0.99, 0.0],
                "Transformer self-attention": [0.75, 0.0],
            }
            return [vectors_by_text[text] for text in query]
        return [1.0, 0.0]


def manifest_chunk(chunk_uid, text, metadata=None, *, is_active=True):
    return SimpleNamespace(
        chunk_uid=chunk_uid,
        text=text,
        metadata_=metadata or {"chunk_uid": chunk_uid, "source": "manifest"},
        is_active=is_active,
    )


@pytest.mark.asyncio
async def test_collection_hybrid_wrapper_falls_back_to_vector_only_when_manifest_not_ready(monkeypatch):
    async def fake_manifest_hybrid(**kwargs):
        raise HybridManifestNotReady("legacy vector rows missing chunk_uid")

    async def fake_native_hybrid_search(**kwargs):
        return {"distances": [[0.9]], "documents": [["vector fallback"]], "metadatas": [[{"source": "vector"}]]}

    monkeypatch.setattr(retrieval_utils, "query_manifest_hybrid_search", fake_manifest_hybrid)
    monkeypatch.setattr(retrieval_utils, "query_doc_with_native_hybrid_search", fake_native_hybrid_search)

    result = await retrieval_utils.query_collection_with_hybrid_search(
        collection_names=["collection-1"],
        queries=["legacy"],
        embedding_function=FakeEmbedder(),
        k=1,
        reranking_function=None,
        k_reranker=1,
        r=0.0,
        hybrid_bm25_weight=0.5,
    )

    assert result == {"distances": [[0.9]], "documents": [["vector fallback"]], "metadatas": [[{"source": "vector"}]]}


@pytest.mark.asyncio
async def test_doc_hybrid_wrapper_falls_back_to_vector_only_when_manifest_not_ready(monkeypatch):
    async def fake_manifest_hybrid(**kwargs):
        raise HybridManifestNotReady("legacy vector rows missing chunk_uid")

    search_calls = []

    async def fake_vector_search(
        collection_name,
        query,
        vectors,
        filter=None,
        limit=10,
        hybrid_bm25_weight=0.5,
    ):
        search_calls.append(
            {
                "collection_name": collection_name,
                "query": query,
                "vectors": vectors,
                "filter": filter,
                "limit": limit,
                "hybrid_bm25_weight": hybrid_bm25_weight,
            }
        )
        return SearchResult(
            ids=[["vector-1"]],
            documents=[["vector fallback"]],
            metadatas=[[{"source": "vector"}]],
            distances=[[0.9]],
        )

    monkeypatch.setattr(retrieval_utils, "query_manifest_hybrid_search", fake_manifest_hybrid)
    monkeypatch.setattr(retrieval_utils.ASYNC_VECTOR_DB_CLIENT, "hybrid_search", fake_vector_search)
    monkeypatch.setattr(retrieval_utils, "_supports_native_hybrid_search", lambda: True)

    result = await retrieval_utils.query_doc_with_hybrid_search(
        collection_name="collection-1",
        collection_result=None,
        query="legacy",
        embedding_function=FakeEmbedder(),
        k=1,
        reranking_function=None,
        k_reranker=1,
        r=0.0,
        hybrid_bm25_weight=0.5,
    )

    assert result["documents"] == [["vector fallback"]]
    assert result["metadatas"][0][0]["source"] == "vector"
    assert result["metadatas"][0][0]["score"] == result["distances"][0][0]
    assert search_calls == [
        {
            "collection_name": "collection-1",
            "query": "legacy",
            "vectors": [[1.0, 0.25]],
            "filter": None,
            "limit": 1,
            "hybrid_bm25_weight": 0.5,
        }
    ]


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

    assert embedder.calls.count((["alpha beta"], None)) == 1
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
async def test_missing_vector_chunk_uids_signal_manifest_not_ready_for_fallback():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-legacy"]],
            documents=[["legacy vector text"]],
            metadatas=[[{"name": "legacy row without manifest uid"}]],
            distances=[[0.99]],
        )
    )

    with pytest.raises(HybridManifestNotReady, match="missing chunk_uid"):
        await query_manifest_hybrid_search(
            collection_names=["collection-1"],
            queries=["legacy"],
            embedding_function=FakeEmbedder(),
            k=2,
            reranking_function=None,
            k_reranker=2,
            r=0.0,
            hybrid_bm25_weight=0.0,
            vector_client=vector_client,
            lexical_client=FakeLexicalClient(fail=True),
            hydrate_chunks=lambda chunk_uids: (_ for _ in ()).throw(
                AssertionError("no manifest hydration should run without chunk_uids")
            ),
        )


@pytest.mark.asyncio
async def test_stale_candidates_with_no_active_manifest_rows_signal_manifest_not_ready_for_fallback():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-stale"]],
            documents=[["derived stale"]],
            metadatas=[[{"chunk_uid": "chunk_stale"}]],
            distances=[[0.99]],
        )
    )

    async def hydrate(chunk_uids):
        return []

    with pytest.raises(HybridManifestNotReady, match="no active manifest"):
        await query_manifest_hybrid_search(
            collection_names=["collection-1"],
            queries=["stale"],
            embedding_function=FakeEmbedder(),
            k=2,
            reranking_function=None,
            k_reranker=2,
            r=0.0,
            hybrid_bm25_weight=0.0,
            vector_client=vector_client,
            lexical_client=FakeLexicalClient(fail=True),
            hydrate_chunks=hydrate,
        )


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


@pytest.mark.asyncio
async def test_vector_only_weight_does_not_instantiate_default_lexical_client(monkeypatch):
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-a"]],
            documents=[["derived vector"]],
            metadatas=[[{"chunk_uid": "chunk_vector"}]],
            distances=[[0.9]],
        )
    )

    def fail_get_lexical_client():
        raise AssertionError("lexical client should not be created when lexical weight is zero")

    monkeypatch.setattr(hybrid_mod, "get_lexical_client", fail_get_lexical_client)

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
        hydrate_chunks=hydrate,
    )

    assert result["documents"] == [["manifest vector"]]


@pytest.mark.asyncio
async def test_no_reranker_filters_by_semantic_relevance_threshold_not_rrf_score():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-high", "vector-low"]],
            documents=[["derived high", "derived low"]],
            metadatas=[[{"chunk_uid": "chunk_high"}, {"chunk_uid": "chunk_low"}]],
            distances=[[0.99, 0.98]],
        )
    )

    async def hydrate(chunk_uids):
        return [
            manifest_chunk("chunk_high", "manifest high"),
            manifest_chunk("chunk_low", "manifest low"),
        ]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["alpha"],
        embedding_function=SemanticEmbedder(),
        k=2,
        reranking_function=None,
        k_reranker=2,
        r=0.5,
        hybrid_bm25_weight=0.0,
        vector_client=vector_client,
        lexical_client=FakeLexicalClient(fail=True),
        hydrate_chunks=hydrate,
    )

    assert result["documents"] == [["manifest high"]]
    assert result["distances"] == [[1.0]]


@pytest.mark.asyncio
async def test_hydration_scans_past_initial_branch_limit_until_enough_active_chunks():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-a", "vector-b", "vector-c", "vector-d"]],
            documents=[["derived a", "derived b", "derived c", "derived d"]],
            metadatas=[
                [
                    {"chunk_uid": "chunk_missing_a"},
                    {"chunk_uid": "chunk_missing_b"},
                    {"chunk_uid": "chunk_c"},
                    {"chunk_uid": "chunk_d"},
                ]
            ],
            distances=[[0.99, 0.98, 0.97, 0.96]],
        )
    )
    hydrate_calls = []

    async def hydrate(chunk_uids):
        hydrate_calls.append(list(chunk_uids))
        return [
            manifest_chunk("chunk_c", "manifest c"),
            manifest_chunk("chunk_d", "manifest d"),
        ]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["alpha"],
        embedding_function=FakeEmbedder(),
        k=2,
        reranking_function=None,
        k_reranker=2,
        r=0.0,
        hybrid_bm25_weight=0.0,
        vector_client=vector_client,
        lexical_client=FakeLexicalClient(fail=True),
        hydrate_chunks=hydrate,
    )

    assert hydrate_calls == [
        ["chunk_missing_a", "chunk_missing_b"],
        ["chunk_c", "chunk_d"],
    ]
    assert result["documents"] == [["manifest c", "manifest d"]]


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


@pytest.mark.asyncio
async def test_compare_query_keeps_evidence_from_multiple_sources_after_reranking():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-sam-a", "vector-sam-b", "vector-transformer"]],
            documents=[["derived sam a", "derived sam b", "derived transformer"]],
            metadatas=[
                [
                    {"chunk_uid": "chunk_sam_a"},
                    {"chunk_uid": "chunk_sam_b"},
                    {"chunk_uid": "chunk_transformer"},
                ]
            ],
            distances=[[0.99, 0.98, 0.8]],
        )
    )

    async def hydrate(chunk_uids):
        return [
            manifest_chunk(
                "chunk_sam_a",
                "SAM promptable segmentation",
                {"chunk_uid": "chunk_sam_a", "source": "segment-anything.pdf", "file_id": "sam"},
            ),
            manifest_chunk(
                "chunk_sam_b",
                "SAM mask decoder",
                {"chunk_uid": "chunk_sam_b", "source": "segment-anything.pdf", "file_id": "sam"},
            ),
            manifest_chunk(
                "chunk_transformer",
                "Transformer self-attention",
                {
                    "chunk_uid": "chunk_transformer",
                    "source": "attention-is-all-you-need.pdf",
                    "file_id": "transformer",
                },
            ),
        ]

    def rerank(query, documents):
        assert "compare" in query.lower()
        return [0.99, 0.98, 0.75]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["Compare the Transformer architecture with Segment Anything prompts"],
        embedding_function=FakeEmbedder(),
        k=2,
        reranking_function=rerank,
        k_reranker=3,
        r=0.0,
        hybrid_bm25_weight=0.0,
        vector_client=vector_client,
        lexical_client=FakeLexicalClient(fail=True),
        hydrate_chunks=hydrate,
    )

    sources = {metadata["source"] for metadata in result["metadatas"][0]}
    assert sources == {"segment-anything.pdf", "attention-is-all-you-need.pdf"}
    assert result["documents"][0][0] == "SAM promptable segmentation"


@pytest.mark.asyncio
async def test_compare_query_keeps_evidence_from_multiple_sources_without_reranker():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-sam-a", "vector-sam-b", "vector-transformer"]],
            documents=[["derived sam a", "derived sam b", "derived transformer"]],
            metadatas=[
                [
                    {"chunk_uid": "chunk_sam_a"},
                    {"chunk_uid": "chunk_sam_b"},
                    {"chunk_uid": "chunk_transformer"},
                ]
            ],
            distances=[[0.99, 0.98, 0.8]],
        )
    )

    async def hydrate(chunk_uids):
        return [
            manifest_chunk(
                "chunk_sam_a",
                "SAM promptable segmentation",
                {"chunk_uid": "chunk_sam_a", "source": "segment-anything.pdf", "file_id": "sam"},
            ),
            manifest_chunk(
                "chunk_sam_b",
                "SAM mask decoder",
                {"chunk_uid": "chunk_sam_b", "source": "segment-anything.pdf", "file_id": "sam"},
            ),
            manifest_chunk(
                "chunk_transformer",
                "Transformer self-attention",
                {
                    "chunk_uid": "chunk_transformer",
                    "source": "attention-is-all-you-need.pdf",
                    "file_id": "transformer",
                },
            ),
        ]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["Compare the Transformer architecture with Segment Anything prompts"],
        embedding_function=CrossDocumentSemanticEmbedder(),
        k=2,
        reranking_function=None,
        k_reranker=3,
        r=0.0,
        hybrid_bm25_weight=0.0,
        vector_client=vector_client,
        lexical_client=FakeLexicalClient(fail=True),
        hydrate_chunks=hydrate,
    )

    sources = {metadata["source"] for metadata in result["metadatas"][0]}
    assert sources == {"segment-anything.pdf", "attention-is-all-you-need.pdf"}
    assert result["documents"][0][0] == "SAM promptable segmentation"


@pytest.mark.asyncio
async def test_compare_query_expands_candidate_window_before_source_diversification():
    sam_chunk_uids = [f"chunk_sam_{index}" for index in range(60)]
    attention_chunk_uid = "chunk_attention"
    gpt4_chunk_uid = "chunk_gpt4"
    all_chunk_uids = [*sam_chunk_uids, attention_chunk_uid, gpt4_chunk_uid]

    class WindowSensitiveVectorClient:
        def __init__(self):
            self.search_calls = []

        async def search(self, collection_name, vectors, filter=None, limit=10):
            self.search_calls.append(
                {
                    "collection_name": collection_name,
                    "limit": limit,
                }
            )
            returned_chunk_uids = all_chunk_uids if limit >= 100 else sam_chunk_uids[:limit]
            return SearchResult(
                ids=[[f"vector-{chunk_uid}" for chunk_uid in returned_chunk_uids]],
                documents=[[f"derived {chunk_uid}" for chunk_uid in returned_chunk_uids]],
                metadatas=[[
                    {"chunk_uid": chunk_uid}
                    for chunk_uid in returned_chunk_uids
                ]],
                distances=[[1.0 - (index * 0.001) for index, _ in enumerate(returned_chunk_uids)]],
            )

    vector_client = WindowSensitiveVectorClient()

    async def hydrate(chunk_uids):
        chunks = []
        for chunk_uid in chunk_uids:
            if chunk_uid == attention_chunk_uid:
                chunks.append(
                    manifest_chunk(
                        chunk_uid,
                        "Transformer encoder decoder self-attention",
                        {"chunk_uid": chunk_uid, "source": "attention-is-all-you-need.pdf", "file_id": "attention"},
                    )
                )
            elif chunk_uid == gpt4_chunk_uid:
                chunks.append(
                    manifest_chunk(
                        chunk_uid,
                        "GPT-4 benchmark results and MMLU evaluation",
                        {"chunk_uid": chunk_uid, "source": "gpt-4-technical-report.pdf", "file_id": "gpt4"},
                    )
                )
            else:
                chunks.append(
                    manifest_chunk(
                        chunk_uid,
                        "SAM promptable segmentation masks",
                        {"chunk_uid": chunk_uid, "source": "segment-anything.pdf", "file_id": "sam"},
                    )
                )
        return chunks

    def rerank(query, documents):
        return [1.0 - (index * 0.001) for index, _ in enumerate(documents)]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=[
            "Compare the Transformer architecture in Attention Is All You Need, "
            "the benchmark results in GPT-4 Technical Report, and promptable masks in Segment Anything"
        ],
        embedding_function=FakeEmbedder(),
        k=3,
        reranking_function=rerank,
        k_reranker=10,
        r=0.0,
        hybrid_bm25_weight=0.0,
        vector_client=vector_client,
        lexical_client=FakeLexicalClient(fail=True),
        hydrate_chunks=hydrate,
    )

    assert vector_client.search_calls[0]["limit"] >= 100
    assert {metadata["source"] for metadata in result["metadatas"][0]} == {
        "segment-anything.pdf",
        "attention-is-all-you-need.pdf",
        "gpt-4-technical-report.pdf",
    }


@pytest.mark.asyncio
async def test_single_document_query_keeps_score_order_without_source_diversification():
    vector_client = FakeVectorClient(
        SearchResult(
            ids=[["vector-sam-a", "vector-sam-b", "vector-transformer"]],
            documents=[["derived sam a", "derived sam b", "derived transformer"]],
            metadatas=[
                [
                    {"chunk_uid": "chunk_sam_a"},
                    {"chunk_uid": "chunk_sam_b"},
                    {"chunk_uid": "chunk_transformer"},
                ]
            ],
            distances=[[0.99, 0.98, 0.8]],
        )
    )

    async def hydrate(chunk_uids):
        return [
            manifest_chunk(
                "chunk_sam_a",
                "SAM promptable segmentation",
                {"chunk_uid": "chunk_sam_a", "source": "segment-anything.pdf", "file_id": "sam"},
            ),
            manifest_chunk(
                "chunk_sam_b",
                "SAM mask decoder",
                {"chunk_uid": "chunk_sam_b", "source": "segment-anything.pdf", "file_id": "sam"},
            ),
            manifest_chunk(
                "chunk_transformer",
                "Transformer self-attention",
                {
                    "chunk_uid": "chunk_transformer",
                    "source": "attention-is-all-you-need.pdf",
                    "file_id": "transformer",
                },
            ),
        ]

    def rerank(query, documents):
        assert "compare" not in query.lower()
        return [0.99, 0.98, 0.75]

    result = await query_manifest_hybrid_search(
        collection_names=["collection-1"],
        queries=["What prompts does Segment Anything support?"],
        embedding_function=FakeEmbedder(),
        k=2,
        reranking_function=rerank,
        k_reranker=3,
        r=0.0,
        hybrid_bm25_weight=0.0,
        vector_client=vector_client,
        lexical_client=FakeLexicalClient(fail=True),
        hydrate_chunks=hydrate,
    )

    assert result["documents"] == [["SAM promptable segmentation", "SAM mask decoder"]]
    assert [metadata["source"] for metadata in result["metadatas"][0]] == [
        "segment-anything.pdf",
        "segment-anything.pdf",
    ]
