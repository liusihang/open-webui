from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from numbers import Number
from typing import Any, Awaitable, Callable, Iterable

from langchain_core.documents import Document

from open_webui.models.retrieval_chunks import fetch_active_chunks_by_chunk_uid
from open_webui.retrieval.lexical.opensearch import LexicalSearchHit, OpenSearchLexicalClient

log = logging.getLogger(__name__)

RRF_RANK_CONSTANT = 60
RAG_EMBEDDING_QUERY_PREFIX = os.getenv("RAG_EMBEDDING_QUERY_PREFIX", None)


class HybridSearchFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class RrfCandidate:
    chunk_uid: str
    score: float
    first_rank: int
    first_seen: int


_LEXICAL_CLIENT: OpenSearchLexicalClient | None = None


def get_lexical_client() -> OpenSearchLexicalClient:
    global _LEXICAL_CLIENT
    if _LEXICAL_CLIENT is None:
        _LEXICAL_CLIENT = OpenSearchLexicalClient()
    return _LEXICAL_CLIENT


def _clamp_weight(value: float | None) -> float:
    if value is None:
        return 0.0
    return min(1.0, max(0.0, float(value)))


def merge_rrf_by_chunk_uid(
    *,
    vector_chunk_uids: Iterable[str],
    lexical_chunk_uids: Iterable[str],
    vector_weight: float,
    lexical_weight: float,
    rank_constant: int = RRF_RANK_CONSTANT,
) -> list[RrfCandidate]:
    scores: dict[str, float] = {}
    first_rank: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    seen_counter = 0

    def add_branch(chunk_uids: Iterable[str], weight: float) -> None:
        nonlocal seen_counter
        if weight <= 0:
            return
        seen_in_branch = set()
        for rank, chunk_uid in enumerate(chunk_uids, start=1):
            if not chunk_uid or chunk_uid in seen_in_branch:
                continue
            seen_in_branch.add(chunk_uid)
            if chunk_uid not in first_seen:
                seen_counter += 1
                first_seen[chunk_uid] = seen_counter
            scores[chunk_uid] = scores.get(chunk_uid, 0.0) + weight / (rank_constant + rank)
            first_rank[chunk_uid] = min(first_rank.get(chunk_uid, rank), rank)

    add_branch(vector_chunk_uids, vector_weight)
    add_branch(lexical_chunk_uids, lexical_weight)

    candidates = [
        RrfCandidate(
            chunk_uid=chunk_uid,
            score=score,
            first_rank=first_rank[chunk_uid],
            first_seen=first_seen[chunk_uid],
        )
        for chunk_uid, score in scores.items()
    ]
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            candidate.first_rank,
            candidate.first_seen,
            candidate.chunk_uid,
        ),
    )


async def query_manifest_hybrid_search(
    *,
    collection_names: list[str],
    queries: list[str],
    embedding_function,
    k: int,
    reranking_function,
    k_reranker: int,
    r: float,
    hybrid_bm25_weight: float,
    vector_client: Any | None = None,
    lexical_client: Any | None = None,
    hydrate_chunks: Callable[[list[str]], Awaitable[list[Any]]] | None = None,
) -> dict:
    queries = [query for query in queries if query]
    if not queries or not collection_names or k <= 0:
        return {"distances": [[]], "documents": [[]], "metadatas": [[]]}

    lexical_weight = _clamp_weight(hybrid_bm25_weight)
    vector_weight = 1.0 - lexical_weight
    branch_limit = max(k, k_reranker or k)
    if vector_client is None:
        from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

        vector_client = ASYNC_VECTOR_DB_CLIENT
    lexical_client = lexical_client or get_lexical_client()
    hydrate_chunks = hydrate_chunks or fetch_active_chunks_by_chunk_uid

    query_embeddings = None
    if vector_weight > 0:
        query_embeddings = await embedding_function(queries, prefix=RAG_EMBEDDING_QUERY_PREFIX)
        query_embeddings = _normalize_query_embeddings(query_embeddings, len(queries))

    vector_chunk_uids: list[str] = []
    lexical_chunk_uids: list[str] = []
    errors = []

    for query_index, query in enumerate(queries):
        for collection_name in collection_names:
            if vector_weight > 0:
                try:
                    vector_result = await vector_client.search(
                        collection_name=collection_name,
                        vectors=[query_embeddings[query_index]],
                        limit=branch_limit,
                    )
                    vector_chunk_uids.extend(
                        _chunk_uids_from_vector_result(vector_result, collection_name=collection_name)
                    )
                except Exception as exc:
                    log.warning(
                        "hybrid vector search failed for collection %s: %s",
                        collection_name,
                        exc,
                        exc_info=True,
                    )
                    errors.append(exc)

            if lexical_weight > 0:
                try:
                    lexical_hits = await asyncio.to_thread(
                        lexical_client.search,
                        query,
                        collection_ids=[collection_name],
                        k=branch_limit,
                    )
                    lexical_chunk_uids.extend(hit.chunk_uid for hit in lexical_hits if hit.chunk_uid)
                except Exception as exc:
                    log.warning(
                        "hybrid lexical search failed for collection %s: %s",
                        collection_name,
                        exc,
                        exc_info=True,
                    )
                    errors.append(exc)

    candidates = merge_rrf_by_chunk_uid(
        vector_chunk_uids=vector_chunk_uids,
        lexical_chunk_uids=lexical_chunk_uids,
        vector_weight=vector_weight,
        lexical_weight=lexical_weight,
    )
    if not candidates and errors:
        raise HybridSearchFailed("Hybrid search failed for all branches") from errors[-1]

    candidate_by_uid = {candidate.chunk_uid: candidate for candidate in candidates}
    chunk_uids = [candidate.chunk_uid for candidate in candidates[:branch_limit]]
    hydrated_chunks = await hydrate_chunks(chunk_uids)
    documents = [
        Document(
            page_content=chunk.text or "",
            metadata=_metadata_from_chunk(chunk, candidate_by_uid[chunk.chunk_uid].score),
        )
        for chunk in hydrated_chunks
        if chunk.chunk_uid in candidate_by_uid
    ]

    if reranking_function is not None:
        documents = await _rerank_documents(
            documents,
            query=queries[0],
            reranking_function=reranking_function,
            top_n=k_reranker,
            r_score=r,
        )
        if k < len(documents):
            documents = documents[:k]
    else:
        documents = documents[:k]

    return {
        "distances": [[document.metadata.get("score") for document in documents]],
        "documents": [[document.page_content for document in documents]],
        "metadatas": [[document.metadata for document in documents]],
    }


def _normalize_query_embeddings(value: Any, query_count: int) -> list[list[float | int]]:
    if query_count == 1 and _looks_like_embedding_vector(value):
        return [value]
    return value


def _looks_like_embedding_vector(value: Any) -> bool:
    return isinstance(value, list) and (not value or isinstance(value[0], Number))


def _chunk_uids_from_vector_result(vector_result: Any, *, collection_name: str) -> list[str]:
    if not vector_result or not getattr(vector_result, "metadatas", None):
        return []

    metadatas = vector_result.metadatas[0] if vector_result.metadatas else []
    chunk_uids = []
    missing_chunk_uid_count = 0
    for metadata in metadatas:
        chunk_uid = metadata.get("chunk_uid") if isinstance(metadata, dict) else None
        if not chunk_uid:
            missing_chunk_uid_count += 1
            continue
        chunk_uids.append(chunk_uid)

    if missing_chunk_uid_count:
        log.warning(
            "Skipped %d vector hit(s) without chunk_uid for collection %s",
            missing_chunk_uid_count,
            collection_name,
        )
    return chunk_uids


def _metadata_from_chunk(chunk: Any, score: float) -> dict[str, Any]:
    metadata = getattr(chunk, "metadata_", None)
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)
    metadata.setdefault("chunk_uid", chunk.chunk_uid)
    metadata["score"] = score
    return metadata


async def _rerank_documents(
    documents: list[Document],
    *,
    query: str,
    reranking_function,
    top_n: int,
    r_score: float,
) -> list[Document]:
    if not documents:
        return []

    scores = await asyncio.to_thread(reranking_function, query, documents)
    if scores is None:
        log.warning("No valid scores found from reranking function. Returning RRF-ranked documents.")
        return documents[:top_n]

    if hasattr(scores, "tolist"):
        scores = scores.tolist()

    docs_with_scores = list(zip(documents, scores))
    if r_score:
        docs_with_scores = [(document, score) for document, score in docs_with_scores if score >= r_score]

    ranked = sorted(docs_with_scores, key=lambda item: item[1], reverse=True)
    final_documents = []
    for document, score in ranked[:top_n]:
        metadata = dict(document.metadata)
        metadata["score"] = score
        final_documents.append(Document(page_content=document.page_content, metadata=metadata))
    return final_documents
