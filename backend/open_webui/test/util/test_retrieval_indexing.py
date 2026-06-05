import os
from dataclasses import dataclass

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import pytest

from open_webui.retrieval.indexing import (
    RetrievalIndexingService,
    SqlAlchemyManifestChunkStore,
    SqlAlchemyVectorChunkStore,
    VectorChunkRecord,
)
import open_webui.retrieval.indexing as indexing_mod


@dataclass
class FakeManifestStore:
    rows: dict[str, dict]

    def __init__(self):
        self.rows = {}
        self.upsert_batches = []
        self.deactivate_absent_calls = []
        self.deactivated_absent_count = 0

    def upsert_chunks(self, chunks):
        chunks = list(chunks)
        self.upsert_batches.append(chunks)
        for chunk in chunks:
            self.rows[chunk["chunk_uid"]] = dict(chunk)
        return len(chunks)

    def deactivate_absent_chunks(self, *, active_chunk_uids, collection_ids, deleted_at):
        self.deactivate_absent_calls.append(
            {
                "active_chunk_uids": set(active_chunk_uids),
                "collection_ids": collection_ids,
                "deleted_at": deleted_at,
            }
        )
        count = 0
        active_chunk_uids = set(active_chunk_uids)
        collection_ids = set(collection_ids or [])
        for chunk in self.rows.values():
            in_scope = not collection_ids or chunk.get("collection_id") in collection_ids or chunk.get("collection_name") in collection_ids
            if in_scope and chunk.get("is_active") and chunk["chunk_uid"] not in active_chunk_uids:
                chunk["is_active"] = False
                chunk["deleted_at"] = deleted_at
                count += 1
        self.deactivated_absent_count = count
        return count

    def count_chunks(self):
        return {
            "total": len(self.rows),
            "active": sum(1 for row in self.rows.values() if row["is_active"]),
        }


class FakeVectorStore:
    def __init__(self, rows):
        self.rows = rows
        self.patches = []
        self.iter_calls = []

    def iter_chunks(self, collection_ids=None):
        self.iter_calls.append(collection_ids)
        collection_ids = set(collection_ids or [])
        return [
            row
            for row in self.rows
            if not collection_ids or row.collection_name in collection_ids
        ]

    def patch_chunk_metadata(self, row_id, metadata):
        self.patches.append((row_id, dict(metadata)))
        for row in self.rows:
            if row.id == row_id:
                row.metadata = dict(metadata)


class FakeLexicalClient:
    def __init__(self, *, fail_bulk=False, fail_status=False):
        self.calls = []
        self.alias = "retrieval_lexical_current"
        self.fail_bulk = fail_bulk
        self.fail_status = fail_status

    def ensure_index(self, version):
        self.calls.append(("ensure_index", version, self.alias))
        return f"retrieval_lexical_v{version}"

    def bulk_upsert(self, chunks, *, batch_size):
        chunks = list(chunks)
        self.calls.append(
            ("bulk_upsert", [chunk["chunk_uid"] for chunk in chunks], batch_size, self.alias)
        )
        if self.fail_bulk:
            raise RuntimeError("bulk failed")
        return len(chunks)

    def promote_index(self, version):
        self.calls.append(("promote_index", version, self.alias))
        return f"retrieval_lexical_v{version}"

    def status(self):
        self.calls.append(("status", self.alias))
        if self.fail_status:
            raise RuntimeError("OpenSearch unavailable")
        return {"alias": self.alias, "indices": ["retrieval_lexical_v1"]}


def _row(
    row_id,
    text,
    *,
    collection_name="knowledge-1",
    metadata=None,
):
    return VectorChunkRecord(
        id=row_id,
        collection_name=collection_name,
        text=text,
        metadata=metadata or {},
    )


def _service(rows, *, lexical_client=None):
    return RetrievalIndexingService(
        vector_store=FakeVectorStore(rows),
        manifest_store=FakeManifestStore(),
        lexical_client=lexical_client or FakeLexicalClient(),
        now_fn=lambda: 1234567890,
    )


def test_manifest_backfill_is_deterministic_and_idempotent():
    rows = [
        _row(
            "vec-2",
            "second chunk",
            metadata={"file_id": "file-1", "start_index": 100},
        ),
        _row(
            "vec-1",
            "first chunk",
            metadata={"file_id": "file-1", "start_index": 0},
        ),
    ]
    service = _service(rows)

    first = service.reindex_lexical(index_version=7, promote_alias=False)
    first_uids = sorted(service.manifest_store.rows)
    second = service.reindex_lexical(index_version=7, promote_alias=False)
    second_uids = sorted(service.manifest_store.rows)

    assert first.scanned == 2
    assert second.scanned == 2
    assert first.failed == 0
    assert second.failed == 0
    assert first_uids == second_uids
    assert len(service.manifest_store.rows) == 2
    chunks_by_text = {
        chunk["text"]: chunk
        for batch in service.manifest_store.upsert_batches
        for chunk in batch
    }
    assert chunks_by_text["first chunk"]["chunk_index"] == 0
    assert chunks_by_text["second chunk"]["chunk_index"] == 1
    assert all(uid.startswith("chunk_") for uid in first_uids)


def test_vector_metadata_patch_uses_same_uid_as_manifest_and_lexical_index():
    lexical_client = FakeLexicalClient()
    rows = [
        _row(
            "vec-1",
            "alpha beta",
            metadata={"file_id": "file-1", "chunk_index": 4},
        )
    ]
    service = _service(rows, lexical_client=lexical_client)

    result = service.reindex_lexical(index_version=2, promote_alias=True)
    chunk_uid = next(iter(service.manifest_store.rows))

    assert result.metadata_patched == 1
    assert service.vector_store.patches == [
        (
            "vec-1",
            {
                "file_id": "file-1",
                "chunk_index": 4,
                "chunk_uid": chunk_uid,
            },
        )
    ]
    assert service.manifest_store.rows[chunk_uid]["metadata"]["chunk_uid"] == chunk_uid
    assert lexical_client.calls[1] == (
        "bulk_upsert",
        [chunk_uid],
        500,
        "retrieval_lexical_v2",
    )


def test_lexical_reindex_promotes_only_after_successful_bulk_index():
    lexical_client = FakeLexicalClient()
    service = _service([_row("vec-1", "alpha")], lexical_client=lexical_client)

    result = service.reindex_lexical(index_version=3, promote_alias=True, batch_size=12)

    assert result.lexical_indexed == 1
    assert lexical_client.calls == [
        ("ensure_index", 3, "retrieval_lexical_current"),
        ("bulk_upsert", [result.chunk_uids[0]], 12, "retrieval_lexical_v3"),
        ("promote_index", 3, "retrieval_lexical_current"),
    ]
    assert result.alias_promoted is True


def test_scoped_reindex_with_promote_requested_fails_before_mutations():
    lexical_client = FakeLexicalClient()
    service = _service(
        [
            _row("vec-1", "alpha", collection_name="knowledge-1"),
            _row("vec-2", "beta", collection_name="knowledge-2"),
        ],
        lexical_client=lexical_client,
    )

    result = service.reindex_lexical(
        collection_ids=["knowledge-1"],
        index_version=3,
        promote_alias=True,
    )

    assert result.failed == 1
    assert result.alias_promoted is False
    assert result.failures == [
        {
            "error": "promote_alias=True is not allowed for scoped lexical reindex",
            "stage": "validation",
        }
    ]
    assert service.vector_store.iter_calls == []
    assert service.vector_store.patches == []
    assert service.manifest_store.upsert_batches == []
    assert lexical_client.calls == []


def test_scoped_reindex_without_promote_indexes_target_without_alias_promotion():
    lexical_client = FakeLexicalClient()
    service = _service([_row("vec-1", "alpha")], lexical_client=lexical_client)

    result = service.reindex_lexical(
        collection_ids=["knowledge-1"],
        index_version=5,
        promote_alias=False,
    )

    assert result.failed == 0
    assert result.alias_promoted is False
    assert [call[0] for call in lexical_client.calls] == ["ensure_index", "bulk_upsert"]
    assert lexical_client.calls[1][3] == "retrieval_lexical_v5"


def test_batch_size_validation_fails_before_db_or_opensearch_mutations():
    lexical_client = FakeLexicalClient()
    service = _service([_row("vec-1", "alpha")], lexical_client=lexical_client)

    result = service.reindex_lexical(batch_size=0, promote_alias=False)

    assert result.failed == 1
    assert result.failures == [
        {
            "error": "batch_size must be at least 1",
            "stage": "validation",
        }
    ]
    assert service.vector_store.iter_calls == []
    assert service.vector_store.patches == []
    assert service.manifest_store.upsert_batches == []
    assert lexical_client.calls == []


def test_index_version_validation_fails_before_db_or_opensearch_mutations():
    lexical_client = FakeLexicalClient()
    service = _service([_row("vec-1", "alpha")], lexical_client=lexical_client)

    result = service.reindex_lexical(index_version=0, promote_alias=False)

    assert result.failed == 1
    assert result.failures == [
        {
            "error": "index_version must be at least 1",
            "stage": "validation",
        }
    ]
    assert service.vector_store.iter_calls == []
    assert service.vector_store.patches == []
    assert service.manifest_store.upsert_batches == []
    assert lexical_client.calls == []


def test_scoped_reindex_deactivates_active_manifest_rows_absent_from_vector_source():
    service = _service([_row("vec-1", "alpha", collection_name="knowledge-1")])
    service.manifest_store.rows["chunk_stale"] = {
        "chunk_uid": "chunk_stale",
        "collection_id": "knowledge-1",
        "collection_name": "knowledge-1",
        "is_active": True,
        "deleted_at": None,
    }

    result = service.reindex_lexical(
        collection_ids=["knowledge-1"],
        index_version=5,
        promote_alias=False,
    )

    active_uid = result.chunk_uids[0]
    assert result.manifest_deactivated == 1
    assert service.manifest_store.rows["chunk_stale"]["is_active"] is False
    assert service.manifest_store.rows["chunk_stale"]["deleted_at"] == 1234567890
    assert service.manifest_store.deactivate_absent_calls == [
        {
            "active_chunk_uids": {active_uid},
            "collection_ids": ["knowledge-1"],
            "deleted_at": 1234567890,
        }
    ]


def test_reindex_with_no_current_rows_still_deactivates_in_scope_manifest_rows():
    service = _service([])
    service.manifest_store.rows["chunk_stale"] = {
        "chunk_uid": "chunk_stale",
        "collection_id": "knowledge-1",
        "collection_name": "knowledge-1",
        "is_active": True,
        "deleted_at": None,
    }

    result = service.reindex_lexical(
        collection_ids=["knowledge-1"],
        index_version=5,
        promote_alias=False,
    )

    assert result.scanned == 0
    assert result.manifest_deactivated == 1
    assert service.manifest_store.rows["chunk_stale"]["is_active"] is False
    assert service.vector_store.patches == []
    assert service.manifest_store.upsert_batches == []
    assert service.lexical_client.calls == []


def test_lexical_reindex_does_not_promote_after_bulk_failure():
    lexical_client = FakeLexicalClient(fail_bulk=True)
    service = _service([_row("vec-1", "alpha")], lexical_client=lexical_client)

    result = service.reindex_lexical(index_version=4, promote_alias=True)

    assert result.lexical_indexed == 0
    assert result.alias_promoted is False
    assert result.failed == 1
    assert "bulk failed" in result.failures[0]["error"]
    assert [call[0] for call in lexical_client.calls] == ["ensure_index", "bulk_upsert"]


def test_result_returns_bounded_chunk_uid_sample():
    rows = [_row(f"vec-{index}", f"text {index}") for index in range(5)]
    service = _service(rows)
    service.chunk_uid_sample_limit = 2

    result = service.reindex_lexical(promote_alias=False)

    assert len(result.chunk_uids) == 2
    assert result.chunk_uid_sample == result.chunk_uids
    assert result.chunk_uid_sample_truncated is True


def test_none_text_rows_are_reported_and_skipped():
    service = _service(
        [
            _row("vec-1", None),
            _row("vec-2", "kept"),
        ]
    )

    result = service.reindex_lexical()

    assert result.scanned == 2
    assert result.manifest_upserted == 1
    assert result.metadata_patched == 1
    assert result.lexical_indexed == 1
    assert result.failed == 1
    assert result.failures == [
        {
            "vector_id": "vec-1",
            "collection_name": "knowledge-1",
            "error": "document_chunk.text is None",
        }
    ]
    assert set(service.manifest_store.rows) == {result.chunk_uids[0]}


def test_status_returns_manifest_counts_and_lexical_errors_without_crashing():
    service = _service([], lexical_client=FakeLexicalClient(fail_status=True))
    service.manifest_store.rows["chunk_1"] = {"is_active": True}

    status = service.get_status()

    assert status["manifest"] == {"total": 1, "active": 1}
    assert status["lexical"]["error"] == "OpenSearch unavailable"


def test_pgcrypto_reindex_fails_clearly_without_faking_success():
    service = RetrievalIndexingService(
        vector_store=FakeVectorStore([_row("vec-1", "alpha")]),
        manifest_store=FakeManifestStore(),
        lexical_client=FakeLexicalClient(),
        pgcrypto_enabled=True,
    )

    result = service.reindex_lexical()

    assert result.scanned == 0
    assert result.failed == 1
    assert result.unsupported is True
    assert "PGVECTOR_PGCRYPTO" in result.failures[0]["error"]


class ExistingChunkQuery:
    def __init__(self, existing):
        self.existing = existing

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.existing

    def count(self):
        return 1


class FakeManifestSession:
    def __init__(self, existing):
        self.existing = existing
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        return ExistingChunkQuery(self.existing)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_manifest_store_preserves_created_at_on_existing_rows():
    existing = type("ExistingChunk", (), {})()
    existing.created_at = 111
    existing.updated_at = 111
    session = FakeManifestSession(existing)
    store = SqlAlchemyManifestChunkStore(session=session)

    count = store.upsert_chunks(
        [
            {
                "chunk_uid": "chunk_existing",
                "collection_id": "collection-1",
                "knowledge_id": "knowledge-1",
                "collection_name": "knowledge-1",
                "file_id": "file-1",
                "file_version": 1,
                "chunk_version": 1,
                "chunk_index": 0,
                "start_index": 0,
                "content_hash": "content-hash",
                "chunker_config_hash": "chunker-hash",
                "text": "updated text",
                "metadata": {"chunk_uid": "chunk_existing"},
                "is_active": True,
                "deleted_at": None,
                "created_at": 999,
                "updated_at": 999,
            }
        ]
    )

    assert count == 1
    assert existing.created_at == 111
    assert existing.updated_at == 999
    assert session.added == []
    assert session.commits == 1


def test_vector_store_factory_prefers_existing_pgvector_session(monkeypatch):
    existing_session = object()

    monkeypatch.setattr(indexing_mod, "_existing_pgvector_session", lambda: existing_session)
    monkeypatch.setattr(
        indexing_mod,
        "_lightweight_pgvector_session",
        lambda: (_ for _ in ()).throw(AssertionError("lightweight fallback should not be used")),
    )

    store = SqlAlchemyVectorChunkStore.from_existing_or_lightweight_session(
        document_chunk_model=object(),
    )

    assert store.session is existing_session
