import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, Integer, Text, UniqueConstraint, create_engine

from open_webui.internal.db import JSONField
from open_webui.migrations.versions import c7d8e9f0a1b2_add_retrieval_index_jobs as index_job_migration
from open_webui.models.retrieval_indexes import (
    RetrievalIndexJob,
    RetrievalIndexState,
    compute_index_state_id,
    compute_target_config_hash,
    normalize_index_kind,
    normalize_index_status,
    normalize_job_status,
)
import open_webui.models.retrieval_indexes as retrieval_indexes_mod


def _run_migration(engine, direction):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(index_job_migration, "op", operations):
            getattr(index_job_migration, direction)()


def test_target_config_hash_is_stable_for_canonical_config():
    left = {"model": "embed-large", "dimensions": 2048, "engine": "openai"}
    right = {"engine": "openai", "dimensions": 2048, "model": "embed-large"}

    assert compute_target_config_hash(left) == compute_target_config_hash(right)
    assert compute_target_config_hash(left) != compute_target_config_hash({**left, "dimensions": 1536})


def test_index_state_id_is_deterministic_and_scope_aware():
    base = {
        "index_kind": "lexical",
        "collection_id": "knowledge-1",
        "knowledge_id": "knowledge-1",
        "collection_name": "Knowledge",
        "file_id": "file-1",
        "chunker_config_hash": "chunker-a",
        "target_config_hash": "lexical-v1",
    }

    assert compute_index_state_id(**base) == compute_index_state_id(**dict(reversed(base.items())))
    assert compute_index_state_id(**base).startswith("retrieval_index_state_")
    assert compute_index_state_id(**base) != compute_index_state_id(
        **{**base, "target_config_hash": "lexical-v2"}
    )
    assert compute_index_state_id(**base) != compute_index_state_id(
        **{**base, "index_kind": "embedding"}
    )


@pytest.mark.parametrize(
    ("normalizer", "valid", "invalid"),
    [
        (normalize_index_kind, "PROJECT", "vector"),
        (normalize_job_status, "RUNNING", "waiting"),
        (normalize_index_status, "STALE", "waiting"),
    ],
)
def test_retrieval_index_normalizers_are_strict(normalizer, valid, invalid):
    assert normalizer(valid) == valid.lower()
    with pytest.raises(ValueError):
        normalizer(invalid)


def test_retrieval_index_job_metadata_declares_expected_columns_constraints_and_indexes():
    table = RetrievalIndexJob.__table__

    assert table.name == "retrieval_index_job"
    assert set(table.columns.keys()) >= {
        "job_id",
        "index_kind",
        "collection_id",
        "knowledge_id",
        "collection_name",
        "file_id",
        "chunker_config_hash",
        "target_config_hash",
        "status",
        "payload",
        "result",
        "error",
        "retry_count",
        "max_retries",
        "created_at",
        "started_at",
        "finished_at",
        "updated_at",
    }

    assert table.c.job_id.primary_key
    assert isinstance(table.c.job_id.type, Text)
    assert isinstance(table.c.payload.type, JSONField)
    assert isinstance(table.c.result.type, JSONField)
    assert isinstance(table.c.retry_count.type, Integer)
    assert isinstance(table.c.created_at.type, BigInteger)

    unique_constraints = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("job_id",) in unique_constraints

    index_columns = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert index_columns["ix_retrieval_index_job_status"] == ("status",)
    assert index_columns["ix_retrieval_index_job_kind_status"] == ("index_kind", "status")
    assert index_columns["ix_retrieval_index_job_collection_id"] == ("collection_id",)
    assert index_columns["ix_retrieval_index_job_file_id"] == ("file_id",)


def test_retrieval_index_state_metadata_declares_expected_columns_constraints_and_indexes():
    table = RetrievalIndexState.__table__

    assert table.name == "retrieval_index_state"
    assert set(table.columns.keys()) >= {
        "state_id",
        "index_kind",
        "collection_id",
        "knowledge_id",
        "collection_name",
        "file_id",
        "chunker_config_hash",
        "target_config_hash",
        "status",
        "active_chunk_count",
        "indexed_chunk_count",
        "last_job_id",
        "error",
        "created_at",
        "updated_at",
    }

    assert table.c.state_id.primary_key
    assert isinstance(table.c.state_id.type, Text)
    assert isinstance(table.c.active_chunk_count.type, Integer)
    assert isinstance(table.c.updated_at.type, BigInteger)

    unique_constraints = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("state_id",) in unique_constraints

    index_columns = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert index_columns["ix_retrieval_index_state_kind_status"] == ("index_kind", "status")
    assert index_columns["ix_retrieval_index_state_collection_id"] == ("collection_id",)
    assert index_columns["ix_retrieval_index_state_file_id"] == ("file_id",)


def test_retrieval_index_job_migration_upgrade_and_downgrade_tolerate_existing_surface():
    engine = create_engine("sqlite:///:memory:")

    _run_migration(engine, "upgrade")
    _run_migration(engine, "upgrade")

    _run_migration(engine, "downgrade")
    _run_migration(engine, "downgrade")


class _FakeScalars:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _FakeExecuteResult:
    def __init__(self, row):
        self.row = row

    def scalars(self):
        return _FakeScalars(self.row)


@pytest.mark.asyncio
async def test_update_job_status_increments_retry_count_on_failure(monkeypatch):
    row = SimpleNamespace(
        job_id="job-1",
        index_kind="lexical",
        collection_id=None,
        knowledge_id=None,
        collection_name=None,
        file_id=None,
        chunker_config_hash=None,
        target_config_hash=None,
        status="running",
        payload={},
        result=None,
        error=None,
        retry_count=2,
        max_retries=3,
        created_at=1,
        started_at=2,
        finished_at=None,
        updated_at=2,
    )

    class FakeSession:
        async def execute(self, statement):
            return _FakeExecuteResult(row)

        async def commit(self):
            return None

        async def refresh(self, value):
            return None

    @asynccontextmanager
    async def fake_db_context(db=None):
        yield FakeSession()

    monkeypatch.setattr(retrieval_indexes_mod, "get_async_db_context", fake_db_context)

    updated = await retrieval_indexes_mod.RetrievalIndexJobs.update_job_status(
        "job-1",
        status="failed",
        error="boom",
    )

    assert row.status == "failed"
    assert row.retry_count == 3
    assert row.error == "boom"
    assert row.finished_at is not None
    assert updated.retry_count == 3
