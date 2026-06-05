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

from open_webui.routers import knowledge
from open_webui.retrieval.indexing import ReindexResult
from open_webui.utils.auth import get_admin_user


def _route(path, method):
    for route in knowledge.router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"missing route {method} {path}")


def test_reindex_admin_routes_are_registered_before_dynamic_id_routes():
    paths = [getattr(route, "path", None) for route in knowledge.router.routes]

    assert paths.index("/reindex/lexical") < paths.index("/{id}")
    assert paths.index("/reindex/full") < paths.index("/{id}")
    assert paths.index("/index/status") < paths.index("/{id}")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/reindex/lexical", "POST"),
        ("/reindex/full", "POST"),
        ("/index/status", "GET"),
    ],
)
def test_reindex_routes_are_admin_only(path, method):
    route = _route(path, method)
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}

    assert get_admin_user in dependency_calls


@pytest.mark.asyncio
async def test_reindex_full_explicitly_does_not_reembed(monkeypatch):
    reindex_calls = []
    threadpool_calls = []

    def fake_reindex(**kwargs):
        reindex_calls.append(kwargs)
        return ReindexResult(
            scanned=1,
            manifest_upserted=1,
            metadata_patched=1,
            lexical_indexed=1,
            failed=0,
            failures=[],
            index_version=kwargs["index_version"],
            alias_promoted=True,
            chunk_uids=["chunk_1"],
        )

    async def fake_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(knowledge, "reindex_lexical_from_current_vector_store", fake_reindex)
    monkeypatch.setattr(knowledge, "run_in_threadpool", fake_run_in_threadpool)

    response = await knowledge.reindex_knowledge_full(
        form_data=knowledge.KnowledgeReindexRequest(index_version=9),
        user=SimpleNamespace(id="admin", role="admin"),
    )

    assert response["embedding_reindexed"] is False
    assert response["lexical"]["scanned"] == 1
    assert reindex_calls == [
        {
            "collection_ids": None,
            "index_version": 9,
            "promote_alias": True,
            "batch_size": 500,
        }
    ]
    assert threadpool_calls == [
        (
            fake_reindex,
            (),
            {
                "collection_ids": None,
                "index_version": 9,
                "promote_alias": True,
                "batch_size": 500,
            },
        )
    ]


@pytest.mark.asyncio
async def test_reindex_lexical_uses_threadpool(monkeypatch):
    threadpool_calls = []

    def fake_reindex(**kwargs):
        return ReindexResult(
            scanned=0,
            manifest_upserted=0,
            metadata_patched=0,
            lexical_indexed=0,
            failed=0,
            failures=[],
            index_version=kwargs["index_version"],
            alias_promoted=False,
        )

    async def fake_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(knowledge, "reindex_lexical_from_current_vector_store", fake_reindex)
    monkeypatch.setattr(knowledge, "run_in_threadpool", fake_run_in_threadpool)

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
    assert threadpool_calls == [
        (
            fake_reindex,
            (),
            {
                "collection_ids": ["knowledge-1"],
                "index_version": 8,
                "promote_alias": False,
                "batch_size": 25,
            },
        )
    ]


@pytest.mark.asyncio
async def test_index_status_handles_lexical_status_errors(monkeypatch):
    threadpool_calls = []

    def fake_status():
        return {
            "manifest": {"total": 3, "active": 2},
            "lexical": {"error": "OpenSearch unavailable"},
        }

    async def fake_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(
        knowledge,
        "get_retrieval_index_status",
        fake_status,
    )
    monkeypatch.setattr(knowledge, "run_in_threadpool", fake_run_in_threadpool)

    response = await knowledge.get_knowledge_index_status(
        user=SimpleNamespace(id="admin", role="admin"),
    )

    assert response["manifest"] == {"total": 3, "active": 2}
    assert response["lexical"]["error"] == "OpenSearch unavailable"
    assert threadpool_calls == [(fake_status, (), {})]
