import os
from unittest.mock import patch

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, BigInteger, Integer, Text, UniqueConstraint, create_engine

from open_webui.internal.db import JSONField
from open_webui.migrations.versions import b6f7c8d9e0a1_add_retrieval_chunk_manifest as retrieval_chunk_migration
from open_webui.models.retrieval_chunks import (
    RetrievalChunk,
    compute_chunk_uid,
    compute_chunker_config_hash,
    compute_content_hash,
)


def _run_migration(engine, direction):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(retrieval_chunk_migration, "op", operations):
            getattr(retrieval_chunk_migration, direction)()


def test_content_hash_requires_explicit_string_content():
    assert compute_content_hash("") == compute_content_hash("")

    with pytest.raises(TypeError, match="text must be a string"):
        compute_content_hash(None)

    with pytest.raises(TypeError, match="text must be a string"):
        compute_content_hash(123)


def test_chunker_config_hash_is_independent_of_dict_key_order():
    left = {"chunk_size": 800, "overlap": 120, "separators": ["\n\n", "\n", " "]}
    right = {"separators": ["\n\n", "\n", " "], "overlap": 120, "chunk_size": 800}

    assert compute_chunker_config_hash(left) == compute_chunker_config_hash(right)
    assert compute_chunker_config_hash(None) == compute_chunker_config_hash({})


def test_chunk_uid_is_stable_and_changes_when_manifest_identity_changes():
    content_hash = compute_content_hash("alpha beta")
    chunker_hash = compute_chunker_config_hash({"chunk_size": 800, "overlap": 120})
    base_kwargs = {
        "collection_id": "collection-1",
        "knowledge_id": "knowledge-legacy",
        "collection_name": "Legacy Collection",
        "file_id": "file-1",
        "file_version": 1,
        "chunker_config_hash": chunker_hash,
        "chunk_index": 3,
        "content_hash": content_hash,
    }

    assert compute_chunk_uid(**base_kwargs) == compute_chunk_uid(**dict(reversed(base_kwargs.items())))

    changed_content = {**base_kwargs, "content_hash": compute_content_hash("alpha gamma")}
    changed_index = {**base_kwargs, "chunk_index": 4}
    changed_file_version = {**base_kwargs, "file_version": 2}
    changed_chunker = {
        **base_kwargs,
        "chunker_config_hash": compute_chunker_config_hash({"overlap": 120, "chunk_size": 1200}),
    }

    assert compute_chunk_uid(**base_kwargs).startswith("chunk_")
    assert compute_chunk_uid(**base_kwargs) != compute_chunk_uid(**changed_content)
    assert compute_chunk_uid(**base_kwargs) != compute_chunk_uid(**changed_index)
    assert compute_chunk_uid(**base_kwargs) != compute_chunk_uid(**changed_file_version)
    assert compute_chunk_uid(**base_kwargs) != compute_chunk_uid(**changed_chunker)


def test_chunk_uid_uses_legacy_disambiguators_when_collection_id_is_missing():
    content_hash = compute_content_hash("same text")
    chunker_hash = compute_chunker_config_hash({"chunk_size": 800})
    base_kwargs = {
        "collection_id": None,
        "knowledge_id": "knowledge-a",
        "collection_name": "Collection A",
        "file_id": "file-1",
        "file_version": 1,
        "chunker_config_hash": chunker_hash,
        "chunk_index": 0,
        "content_hash": content_hash,
    }

    assert compute_chunk_uid(**base_kwargs) != compute_chunk_uid(
        **{**base_kwargs, "knowledge_id": "knowledge-b"}
    )
    assert compute_chunk_uid(**base_kwargs) != compute_chunk_uid(
        **{**base_kwargs, "collection_name": "Collection B"}
    )


def test_retrieval_chunk_metadata_declares_expected_columns_constraints_and_indexes():
    table = RetrievalChunk.__table__

    assert table.name == "retrieval_chunk"
    assert set(table.columns.keys()) >= {
        "row_id",
        "chunk_uid",
        "collection_id",
        "knowledge_id",
        "collection_name",
        "file_id",
        "file_version",
        "chunk_version",
        "chunk_index",
        "start_index",
        "content_hash",
        "chunker_config_hash",
        "text",
        "metadata",
        "is_active",
        "deleted_at",
        "created_at",
        "updated_at",
    }

    assert table.c.row_id.primary_key
    assert isinstance(table.c.row_id.type, (Integer, BigInteger))
    assert isinstance(table.c.chunk_uid.type, Text)
    assert not table.c.chunk_uid.nullable
    assert not table.c.content_hash.nullable
    assert not table.c.chunker_config_hash.nullable
    assert not table.c.file_version.nullable
    assert not table.c.chunk_version.nullable
    assert not table.c.is_active.nullable
    assert not table.c.created_at.nullable
    assert not table.c.updated_at.nullable
    assert isinstance(table.c.metadata.type, JSONField)
    assert isinstance(table.c.is_active.type, Boolean)

    unique_constraints = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("chunk_uid",) in unique_constraints

    index_columns = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert index_columns["ix_retrieval_chunk_collection_id"] == ("collection_id",)
    assert index_columns["ix_retrieval_chunk_knowledge_id"] == ("knowledge_id",)
    assert index_columns["ix_retrieval_chunk_file_id"] == ("file_id",)
    assert index_columns["ix_retrieval_chunk_collection_active"] == ("collection_id", "is_active")


def test_retrieval_chunk_migration_upgrade_and_downgrade_tolerate_existing_surface():
    engine = create_engine("sqlite:///:memory:")

    _run_migration(engine, "upgrade")
    _run_migration(engine, "upgrade")

    _run_migration(engine, "downgrade")
    _run_migration(engine, "downgrade")
