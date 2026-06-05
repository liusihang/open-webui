import os
from dataclasses import dataclass

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import pytest

from open_webui.retrieval.indexing import (
    RetrievalIndexingService,
    VectorChunkRecord,
)


@dataclass
class FakeManifestStore:
    rows: dict[str, dict]

    def __init__(self):
        self.rows = {}
        self.upsert_batches = []

    def upsert_chunks(self, chunks):
        chunks = list(chunks)
        self.upsert_batches.append(chunks)
        for chunk in chunks:
            self.rows[chunk["chunk_uid"]] = dict(chunk)
        return len(chunks)

    def count_chunks(self):
        return {
            "total": len(self.rows),
            "active": sum(1 for row in self.rows.values() if row["is_active"]),
        }


class FakeVectorStore:
    def __init__(self, rows):
        self.rows = rows
        self.patches = []

    def iter_chunks(self, collection_ids=None):
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


def test_lexical_reindex_does_not_promote_after_bulk_failure():
    lexical_client = FakeLexicalClient(fail_bulk=True)
    service = _service([_row("vec-1", "alpha")], lexical_client=lexical_client)

    result = service.reindex_lexical(index_version=4, promote_alias=True)

    assert result.lexical_indexed == 0
    assert result.alias_promoted is False
    assert result.failed == 1
    assert "bulk failed" in result.failures[0]["error"]
    assert [call[0] for call in lexical_client.calls] == ["ensure_index", "bulk_upsert"]


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
