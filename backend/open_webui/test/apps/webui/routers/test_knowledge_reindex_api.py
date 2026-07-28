import os
import sqlite3
import tempfile
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db_file.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_file.name}")

with sqlite3.connect(_db_file.name) as conn:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY,
            data JSON NOT NULL,
            version INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME
        )
        """
    )

import pytest
from pydantic import ValidationError

from open_webui.routers import knowledge
from open_webui.utils.auth import get_admin_user


def _route(path, method):
    for route in knowledge.router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"missing route {method} {path}")


def test_reindex_admin_routes_are_registered_before_dynamic_id_routes():
    paths = [getattr(route, "path", None) for route in knowledge.router.routes]

    assert paths.index("/{id}/evidence/rebuild") < paths.index("/{id}")
    assert paths.index("/reindex/lexical") < paths.index("/{id}")
    assert paths.index("/reindex/full") < paths.index("/{id}")
    assert paths.index("/index/status") < paths.index("/{id}")
    assert paths.index("/index/jobs/{job_id}") < paths.index("/{id}")
    assert paths.index("/index/jobs/{job_id}/run") < paths.index("/{id}")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/{id}/evidence/rebuild", "POST"),
        ("/reindex/lexical", "POST"),
        ("/reindex/full", "POST"),
        ("/index/status", "GET"),
        ("/index/jobs/{job_id}", "GET"),
        ("/index/jobs/{job_id}/run", "POST"),
    ],
)
def test_reindex_routes_are_admin_only(path, method):
    route = _route(path, method)
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}

    assert get_admin_user in dependency_calls


@pytest.mark.parametrize("index_version", [0, -1])
def test_reindex_request_requires_positive_index_version(index_version):
    with pytest.raises(ValidationError):
        knowledge.KnowledgeReindexRequest(index_version=index_version)


def test_reindex_request_requires_positive_batch_size():
    with pytest.raises(ValidationError):
        knowledge.KnowledgeReindexRequest(batch_size=0)


@pytest.mark.asyncio
async def test_remove_file_from_knowledge_deactivates_manifest_when_vector_cleanup_fails(monkeypatch):
    deactivate_calls = []

    async def fake_get_knowledge_by_id(id, db=None):
        return SimpleNamespace(
            id=id,
            user_id="owner-1",
            name="Knowledge",
            description="",
            meta=None,
            access_grants=[],
            created_at=1,
            updated_at=1,
            model_dump=lambda: {
                "id": id,
                "user_id": "owner-1",
                "name": "Knowledge",
                "description": "",
                "meta": None,
                "access_grants": [],
                "created_at": 1,
                "updated_at": 1,
            },
        )

    async def fake_get_file_by_id(file_id, db=None):
        return SimpleNamespace(id=file_id, hash="hash-1", user_id="owner-1")

    async def fake_true(*args, **kwargs):
        return True

    async def fake_none(*args, **kwargs):
        return None

    async def fake_empty_list(*args, **kwargs):
        return []

    async def fake_vector_delete(*args, **kwargs):
        raise RuntimeError("derived vector index already unavailable")

    async def fake_has_collection(*args, **kwargs):
        return False

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

    async def fake_publish_event(*args, **kwargs):
        return None

    monkeypatch.setattr(knowledge.Knowledges, "get_knowledge_by_id", fake_get_knowledge_by_id)
    monkeypatch.setattr(knowledge.Knowledges, "has_file", fake_true)
    monkeypatch.setattr(knowledge.Knowledges, "remove_file_from_knowledge_by_id", fake_none)
    monkeypatch.setattr(knowledge.Knowledges, "get_file_metadatas_by_id", fake_empty_list)
    monkeypatch.setattr(knowledge.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(knowledge.Files, "delete_file_by_id", fake_none)
    monkeypatch.setattr(knowledge.KnowledgeLayers, "delete_layers_by_file", fake_none)
    monkeypatch.setattr(knowledge, "delete_layer_embeddings_by_file_id", fake_none)
    monkeypatch.setattr(knowledge.ASYNC_VECTOR_DB_CLIENT, "delete", fake_vector_delete)
    monkeypatch.setattr(knowledge.ASYNC_VECTOR_DB_CLIENT, "has_collection", fake_has_collection)
    monkeypatch.setattr(knowledge, "deactivate_chunks_for_scope", fake_deactivate)
    monkeypatch.setattr(knowledge, "publish_event", fake_publish_event)

    db = object()
    await knowledge.remove_file_from_knowledge_by_id(
        request=SimpleNamespace(),
        id="knowledge-1",
        form_data=knowledge.KnowledgeFileIdForm(file_id="file-1"),
        delete_file=True,
        user=SimpleNamespace(id="owner-1", role="user"),
        db=db,
    )

    assert deactivate_calls == [
        {
            "collection_id": "knowledge-1",
            "collection_name": "knowledge-1",
            "file_id": "file-1",
            "db": db,
        },
        {
            "collection_id": "file-file-1",
            "collection_name": "file-file-1",
            "file_id": "file-1",
            "db": db,
        }
    ]


@pytest.mark.asyncio
async def test_reindex_full_explicitly_does_not_reembed(monkeypatch):
    enqueue_calls = []
    run_calls = []

    async def fake_enqueue(**kwargs):
        enqueue_calls.append(kwargs)
        return {"job": {"job_id": "job-full"}, "state": {"status": "pending"}}

    async def fake_run(job_id):
        run_calls.append(job_id)
        return {
            "result": {
                "embedding_reindexed": False,
                "lexical": {"scanned": 1, "index_version": 9},
            }
        }

    monkeypatch.setattr(knowledge, "enqueue_retrieval_index_job", fake_enqueue)
    monkeypatch.setattr(knowledge, "run_retrieval_index_job", fake_run)

    response = await knowledge.reindex_knowledge_full(
        form_data=knowledge.KnowledgeReindexRequest(index_version=9),
        user=SimpleNamespace(id="admin", role="admin"),
    )

    assert response["embedding_reindexed"] is False
    assert response["lexical"]["scanned"] == 1
    assert enqueue_calls == [
        {
            "index_kind": "full",
            "collection_ids": None,
            "index_version": 9,
            "promote_alias": True,
            "batch_size": 500,
        }
    ]
    assert run_calls == ["job-full"]


@pytest.mark.asyncio
async def test_reindex_lexical_creates_job_then_runs_it_by_default(monkeypatch):
    enqueue_calls = []
    run_calls = []

    async def fake_enqueue(**kwargs):
        enqueue_calls.append(kwargs)
        return {"job": {"job_id": "job-lexical"}, "state": {"status": "pending"}}

    async def fake_run(job_id):
        run_calls.append(job_id)
        return {"result": {"index_version": 8, "scanned": 0}}

    monkeypatch.setattr(knowledge, "enqueue_retrieval_index_job", fake_enqueue)
    monkeypatch.setattr(knowledge, "run_retrieval_index_job", fake_run)

    response = await knowledge.reindex_knowledge_lexical(
        form_data=knowledge.KnowledgeReindexRequest(
            collection_ids=["knowledge-1"],
            index_version=8,
            promote_alias=False,
            batch_size=25,
        ),
        user=SimpleNamespace(id="admin", role="admin"),
    )

    assert response["index_version"] == 8
    assert enqueue_calls == [
        {
            "index_kind": "lexical",
            "collection_ids": ["knowledge-1"],
            "index_version": 8,
            "promote_alias": False,
            "batch_size": 25,
        }
    ]
    assert run_calls == ["job-lexical"]


@pytest.mark.asyncio
async def test_reindex_lexical_can_enqueue_without_running(monkeypatch):
    run_calls = []

    async def fake_enqueue(**kwargs):
        return {"job": {"job_id": "job-queued"}, "state": {"status": "pending"}}

    async def fake_run(job_id):
        run_calls.append(job_id)
        raise AssertionError("queued jobs should not run inline")

    monkeypatch.setattr(knowledge, "enqueue_retrieval_index_job", fake_enqueue)
    monkeypatch.setattr(knowledge, "run_retrieval_index_job", fake_run)

    response = await knowledge.reindex_knowledge_lexical(
        form_data=knowledge.KnowledgeReindexRequest(run_async=True),
        user=SimpleNamespace(id="admin", role="admin"),
    )

    assert response == {
        "queued": True,
        "job": {"job_id": "job-queued"},
        "state": {"status": "pending"},
    }
    assert run_calls == []


@pytest.mark.asyncio
async def test_rebuild_evidence_creates_job_then_runs_it_by_default(monkeypatch):
    enqueue_calls = []
    run_calls = []

    async def fake_enqueue(**kwargs):
        enqueue_calls.append(kwargs)
        return {"job": {"job_id": "job-evidence"}, "state": {"status": "pending"}}

    async def fake_run(job_id):
        run_calls.append(job_id)
        return {"result": {"evidence": {"text_evidence_upserted": 1, "image_evidence_upserted": 1}}}

    monkeypatch.setattr(knowledge, "enqueue_evidence_projection_job", fake_enqueue)
    monkeypatch.setattr(knowledge, "run_retrieval_index_job", fake_run)

    response = await knowledge.rebuild_knowledge_evidence(
        id="knowledge-1",
        form_data=knowledge.KnowledgeEvidenceRebuildRequest(
            file_ids=["file-1"],
            project_document_images=True,
        ),
        user=SimpleNamespace(id="admin", role="admin"),
    )

    assert response["evidence"]["text_evidence_upserted"] == 1
    assert enqueue_calls == [
        {
            "knowledge_id": "knowledge-1",
            "file_ids": ["file-1"],
            "project_document_images": True,
        }
    ]
    assert run_calls == ["job-evidence"]


@pytest.mark.asyncio
async def test_index_status_handles_lexical_status_errors(monkeypatch):
    async def fake_status():
        return {
            "manifest": {"total": 3, "active": 2},
            "lexical": {"error": "OpenSearch unavailable"},
            "jobs": [],
            "states": [],
        }

    monkeypatch.setattr(
        knowledge,
        "get_retrieval_index_status_async",
        fake_status,
    )

    response = await knowledge.get_knowledge_index_status(
        user=SimpleNamespace(id="admin", role="admin"),
    )

    assert response["manifest"] == {"total": 3, "active": 2}
    assert response["lexical"]["error"] == "OpenSearch unavailable"
    assert response["jobs"] == []
    assert response["states"] == []
