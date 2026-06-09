from contextlib import asynccontextmanager
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

    monkeypatch.setattr(retrieval_router, "deactivate_chunks_for_scope", fake_deactivate)
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


@pytest.mark.asyncio
async def test_process_file_projects_evidence_for_knowledge_ingest_by_default(monkeypatch):
    enqueue_calls = []
    run_calls = []
    file_updates = []

    file = SimpleNamespace(
        id="file-1",
        user_id="owner-1",
        filename="doc.pdf",
        path=None,
        hash=None,
        data={
            "content": "alpha text",
            "document_image_assets": [
                {
                    "storage_path": "/tmp/page-1.png",
                    "mime_type": "image/png",
                }
            ],
        },
        meta={"content_type": "application/pdf"},
    )

    async def fake_get_file_by_id(file_id, db=None):
        return file

    async def fake_validate_collection_access(collection_names, user, access_type="write"):
        assert collection_names == ["kb-1"]

    async def fake_query(collection_name, filter=None):
        assert collection_name == "file-file-1"
        return None

    def fake_save_docs_to_vector_db(*args, **kwargs):
        return True

    async def fake_update_file_metadata_by_id(file_id, metadata, db=None):
        file_updates.append(("metadata", file_id, metadata))
        return file

    async def fake_update_file_data_by_id(file_id, data, db=None):
        file_updates.append(("data", file_id, data))
        return file

    async def fake_update_file_hash_by_id(file_id, hash, db=None):
        file_updates.append(("hash", file_id, hash))
        return file

    async def fake_enqueue(**kwargs):
        enqueue_calls.append(kwargs)
        return {"job": {"job_id": "job-evidence"}, "state": {"status": "pending"}}

    async def fake_run(job_id):
        run_calls.append(job_id)
        return {"result": {"evidence": {"text_evidence_upserted": 1, "image_evidence_upserted": 1}}}

    async def fake_get_knowledge_by_id(id, db=None):
        return SimpleNamespace(id=id)

    @asynccontextmanager
    async def fake_get_async_db():
        yield object()

    monkeypatch.setattr(retrieval_router.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(retrieval_router, "_validate_collection_access", fake_validate_collection_access)
    monkeypatch.setattr(retrieval_router.ASYNC_VECTOR_DB_CLIENT, "query", fake_query)
    monkeypatch.setattr(retrieval_router, "save_docs_to_vector_db", fake_save_docs_to_vector_db)
    monkeypatch.setattr(retrieval_router.Files, "update_file_metadata_by_id", fake_update_file_metadata_by_id)
    monkeypatch.setattr(retrieval_router.Files, "update_file_data_by_id", fake_update_file_data_by_id)
    monkeypatch.setattr(retrieval_router.Files, "update_file_hash_by_id", fake_update_file_hash_by_id)
    monkeypatch.setattr(retrieval_router.Knowledges, "get_knowledge_by_id", fake_get_knowledge_by_id)
    monkeypatch.setattr(retrieval_router, "enqueue_evidence_projection_job", fake_enqueue, raising=False)
    monkeypatch.setattr(retrieval_router, "run_retrieval_index_job", fake_run, raising=False)
    monkeypatch.setattr(retrieval_router, "get_async_db", fake_get_async_db)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(BYPASS_EMBEDDING_AND_RETRIEVAL=False),
                EMBEDDING_FUNCTION=lambda query, prefix=None, user=None: [0.1, 0.2],
            )
        )
    )
    user = SimpleNamespace(id="owner-1", role="admin")
    async def fake_commit():
        return None

    db = SimpleNamespace(commit=fake_commit)

    result = await retrieval_router.process_file(
        request=request,
        form_data=retrieval_router.ProcessFileForm(file_id="file-1", collection_name="kb-1"),
        user=user,
        db=db,
    )

    assert result["status"] is True
    assert enqueue_calls == [
        {
            "knowledge_id": "kb-1",
            "file_ids": ["file-1"],
            "project_document_images": True,
        }
    ]
    assert run_calls == ["job-evidence"]
    assert ("data", "file-1", {"status": "completed"}) in file_updates


@pytest.mark.asyncio
async def test_process_file_does_not_project_evidence_for_non_knowledge_collection(monkeypatch):
    enqueue_calls = []

    file = SimpleNamespace(
        id="file-1",
        user_id="owner-1",
        filename="doc.pdf",
        path=None,
        hash=None,
        data={"content": "alpha text"},
        meta={"content_type": "application/pdf"},
    )

    async def fake_get_file_by_id(file_id, db=None):
        return file

    async def fake_validate_collection_access(collection_names, user, access_type="write"):
        assert collection_names == ["custom-collection"]

    async def fake_query(collection_name, filter=None):
        return None

    def fake_save_docs_to_vector_db(*args, **kwargs):
        return True

    async def fake_update_file_metadata_by_id(file_id, metadata, db=None):
        return file

    async def fake_update_file_data_by_id(file_id, data, db=None):
        return file

    async def fake_update_file_hash_by_id(file_id, hash, db=None):
        return file

    async def fake_get_knowledge_by_id(id, db=None):
        return None

    async def fake_enqueue(**kwargs):
        enqueue_calls.append(kwargs)
        return {"job": {"job_id": "job-evidence"}, "state": {"status": "pending"}}

    @asynccontextmanager
    async def fake_get_async_db():
        yield object()

    monkeypatch.setattr(retrieval_router.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(retrieval_router, "_validate_collection_access", fake_validate_collection_access)
    monkeypatch.setattr(retrieval_router.ASYNC_VECTOR_DB_CLIENT, "query", fake_query)
    monkeypatch.setattr(retrieval_router, "save_docs_to_vector_db", fake_save_docs_to_vector_db)
    monkeypatch.setattr(retrieval_router.Files, "update_file_metadata_by_id", fake_update_file_metadata_by_id)
    monkeypatch.setattr(retrieval_router.Files, "update_file_data_by_id", fake_update_file_data_by_id)
    monkeypatch.setattr(retrieval_router.Files, "update_file_hash_by_id", fake_update_file_hash_by_id)
    monkeypatch.setattr(retrieval_router.Knowledges, "get_knowledge_by_id", fake_get_knowledge_by_id)
    monkeypatch.setattr(retrieval_router, "enqueue_evidence_projection_job", fake_enqueue, raising=False)
    monkeypatch.setattr(retrieval_router, "get_async_db", fake_get_async_db)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(BYPASS_EMBEDDING_AND_RETRIEVAL=False),
                EMBEDDING_FUNCTION=lambda query, prefix=None, user=None: [0.1, 0.2],
            )
        )
    )
    user = SimpleNamespace(id="owner-1", role="admin")

    async def fake_commit():
        return None

    result = await retrieval_router.process_file(
        request=request,
        form_data=retrieval_router.ProcessFileForm(file_id="file-1", collection_name="custom-collection"),
        user=user,
        db=SimpleNamespace(commit=fake_commit),
    )

    assert result["status"] is True
    assert enqueue_calls == []


@pytest.mark.asyncio
async def test_process_files_batch_projects_evidence_for_completed_knowledge_files(monkeypatch):
    enqueue_calls = []
    run_calls = []

    files = [
        SimpleNamespace(
            id="file-1",
            user_id="owner-1",
            filename="doc.pdf",
            path=None,
            hash=None,
            data={"content": "alpha", "document_image_assets": [{"storage_path": "/tmp/page-1.png"}]},
            meta={"content_type": "application/pdf"},
            created_at=1,
            updated_at=1,
        ),
        SimpleNamespace(
            id="file-2",
            user_id="owner-1",
            filename="notes.txt",
            path=None,
            hash=None,
            data={"content": "beta"},
            meta={"content_type": "text/plain"},
            created_at=1,
            updated_at=1,
        ),
    ]

    async def fake_get_file_by_id(file_id, db=None):
        return next(file for file in files if file.id == file_id)

    async def fake_validate_collection_access(collection_names, user, access_type="write"):
        assert collection_names == ["kb-1"]

    def fake_save_docs_to_vector_db(*args, **kwargs):
        return True

    async def fake_update_file_by_id(id, form_data, db=None):
        return next(file for file in files if file.id == id)

    async def fake_enqueue(**kwargs):
        enqueue_calls.append(kwargs)
        return {"job": {"job_id": f"job-{kwargs['file_ids'][0]}"}, "state": {"status": "pending"}}

    async def fake_run(job_id):
        run_calls.append(job_id)
        return {"result": {"evidence": {"text_evidence_upserted": 1}}}

    async def fake_get_knowledge_by_id(id, db=None):
        return SimpleNamespace(id=id)

    monkeypatch.setattr(retrieval_router.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(retrieval_router.Files, "update_file_by_id", fake_update_file_by_id)
    monkeypatch.setattr(retrieval_router.Knowledges, "get_knowledge_by_id", fake_get_knowledge_by_id)
    monkeypatch.setattr(retrieval_router, "_validate_collection_access", fake_validate_collection_access)
    monkeypatch.setattr(retrieval_router, "save_docs_to_vector_db", fake_save_docs_to_vector_db)
    monkeypatch.setattr(retrieval_router, "enqueue_evidence_projection_job", fake_enqueue, raising=False)
    monkeypatch.setattr(retrieval_router, "run_retrieval_index_job", fake_run, raising=False)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace())))
    user = SimpleNamespace(id="owner-1", role="admin")

    result = await retrieval_router.process_files_batch(
        request=request,
        form_data=retrieval_router.BatchProcessFilesForm(files=files, collection_name="kb-1"),
        user=user,
        db=object(),
    )

    assert [file_result.status for file_result in result.results] == ["completed", "completed"]
    assert enqueue_calls == [
        {
            "knowledge_id": "kb-1",
            "file_ids": ["file-1"],
            "project_document_images": True,
        },
        {
            "knowledge_id": "kb-1",
            "file_ids": ["file-2"],
            "project_document_images": True,
        },
    ]
    assert run_calls == ["job-file-1", "job-file-2"]


def test_build_retrieval_manifest_chunks_preserves_kb_file_and_vector_identity():
    chunks = retrieval_router._build_retrieval_manifest_chunks_from_vector_items(
        collection_name="kb-1",
        now=123,
        items=[
            {
                "id": "vector-1",
                "text": "alpha text",
                "metadata": {
                    "file_id": "file-1",
                    "chunk_index": 4,
                    "start_index": 80,
                    "chunker_config_hash": "chunker-hash",
                },
            }
        ],
    )

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["collection_id"] == "kb-1"
    assert chunk["knowledge_id"] == "kb-1"
    assert chunk["collection_name"] == "kb-1"
    assert chunk["file_id"] == "file-1"
    assert chunk["text"] == "alpha text"
    assert chunk["is_active"] is True
    assert chunk["created_at"] == 123
    assert chunk["metadata"]["vector_id"] == "vector-1"
    assert chunk["metadata"]["chunk_uid"] == chunk["chunk_uid"]
