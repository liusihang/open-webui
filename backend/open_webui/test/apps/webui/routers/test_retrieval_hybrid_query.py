from types import SimpleNamespace

import pytest

from open_webui.routers import retrieval as retrieval_router


@pytest.mark.asyncio
async def test_query_doc_hybrid_form_accepts_weight_and_does_not_pass_user_kwarg(monkeypatch):
    async def fake_validate_collection_access(collection_names, user, access_type="read"):
        return None

    captured_kwargs = {}

    async def fake_query_doc_with_hybrid_search(**kwargs):
        captured_kwargs.update(kwargs)
        return {"distances": [[1.0]], "documents": [["ok"]], "metadatas": [[{}]]}

    monkeypatch.setattr(
        retrieval_router,
        "_validate_collection_access",
        fake_validate_collection_access,
    )
    monkeypatch.setattr(
        retrieval_router,
        "query_doc_with_hybrid_search",
        fake_query_doc_with_hybrid_search,
    )

    async def fake_get(collection_name):
        return SimpleNamespace(documents=[["legacy"]], metadatas=[[{}]], ids=[["legacy-id"]])

    monkeypatch.setattr(retrieval_router.ASYNC_VECTOR_DB_CLIENT, "get", fake_get)

    form = retrieval_router.QueryDocForm(
        collection_name="collection-1",
        query="alpha",
        hybrid=True,
        hybrid_bm25_weight=0.25,
        enable_enriched_texts=True,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_RAG_HYBRID_SEARCH=True,
                    TOP_K=4,
                    TOP_K_RERANKER=4,
                    RELEVANCE_THRESHOLD=0.0,
                    HYBRID_BM25_WEIGHT=0.5,
                    ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS=False,
                ),
                EMBEDDING_FUNCTION=lambda query, prefix=None, user=None: [0.1, 0.2],
                RERANKING_FUNCTION=None,
            )
        )
    )
    user = SimpleNamespace(id="user-1", role="admin")

    result = await retrieval_router.query_doc_handler(request=request, form_data=form, user=user)

    assert result == {"distances": [[1.0]], "documents": [["ok"]], "metadatas": [[{}]]}
    assert captured_kwargs["hybrid_bm25_weight"] == 0.25
    assert captured_kwargs["enable_enriched_texts"] is True
    assert "user" not in captured_kwargs


@pytest.mark.asyncio
async def test_query_collection_explicit_hybrid_false_uses_vector_only_even_when_global_hybrid_enabled(monkeypatch):
    async def fake_validate_collection_access(collection_names, user, access_type="read"):
        return None

    async def fail_hybrid_search(**kwargs):
        raise AssertionError("explicit hybrid=False must not call hybrid collection search")

    captured_query_collection_kwargs = {}

    async def fake_query_collection(request, **kwargs):
        captured_query_collection_kwargs.update(kwargs)
        if request is not None:
            raise AssertionError("vector-only collection path must not re-enter global hybrid")
        return {"distances": [[0.9]], "documents": [["vector only"]], "metadatas": [[{"source": "vector"}]]}

    monkeypatch.setattr(
        retrieval_router,
        "_validate_collection_access",
        fake_validate_collection_access,
    )
    monkeypatch.setattr(
        retrieval_router,
        "query_collection_with_hybrid_search",
        fail_hybrid_search,
    )
    monkeypatch.setattr(retrieval_router, "query_collection", fake_query_collection)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_RAG_HYBRID_SEARCH=True,
                    TOP_K=4,
                    TOP_K_RERANKER=4,
                    RELEVANCE_THRESHOLD=0.0,
                    HYBRID_BM25_WEIGHT=0.5,
                    ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS=True,
                ),
                EMBEDDING_FUNCTION=lambda query, prefix=None, user=None: [0.1, 0.2],
                RERANKING_FUNCTION=None,
            )
        )
    )
    form = retrieval_router.QueryCollectionsForm(
        collection_names=["collection-1"],
        query="alpha",
        hybrid=False,
    )
    user = SimpleNamespace(id="user-1", role="admin")

    result = await retrieval_router.query_collection_handler(request=request, form_data=form, user=user)

    assert result == {
        "distances": [[0.9]],
        "documents": [["vector only"]],
        "metadatas": [[{"source": "vector"}]],
    }
    assert captured_query_collection_kwargs["collection_names"] == ["collection-1"]
    assert captured_query_collection_kwargs["queries"] == ["alpha"]


@pytest.mark.asyncio
async def test_delete_entries_from_collection_deactivates_manifest_chunks(monkeypatch):
    deactivate_calls = []
    vector_delete_calls = []

    async def fake_deactivate(*, collection_id=None, collection_name=None, file_id=None, db=None):
        deactivate_calls.append(
            {
                "collection_id": collection_id,
                "collection_name": collection_name,
                "file_id": file_id,
                "db": db,
            }
        )
        return 1

    async def fake_has_collection(collection_name):
        return True

    async def fake_delete(collection_name, ids=None, filter=None):
        vector_delete_calls.append(
            {
                "collection_name": collection_name,
                "ids": ids,
                "filter": filter,
            }
        )

    class FakeFiles:
        async def get_file_by_id(self, file_id, db=None):
            return SimpleNamespace(id=file_id, hash="hash-1")

    monkeypatch.setattr(retrieval_router, "deactivate_active_chunks", fake_deactivate)
    monkeypatch.setattr(retrieval_router.ASYNC_VECTOR_DB_CLIENT, "has_collection", fake_has_collection)
    monkeypatch.setattr(retrieval_router.ASYNC_VECTOR_DB_CLIENT, "delete", fake_delete)
    monkeypatch.setattr(retrieval_router, "Files", FakeFiles())

    result = await retrieval_router.delete_entries_from_collection(
        form_data=retrieval_router.DeleteForm(collection_name="knowledge-1", file_id="file-1"),
        user=SimpleNamespace(id="admin", role="admin"),
        db=object(),
    )

    assert result == {"status": True}
    assert vector_delete_calls == [
        {
            "collection_name": "knowledge-1",
            "ids": None,
            "filter": {"hash": "hash-1"},
        }
    ]
    assert deactivate_calls == [
        {
            "collection_id": "knowledge-1",
            "collection_name": "knowledge-1",
            "file_id": "file-1",
            "db": deactivate_calls[0]["db"],
        }
    ]
