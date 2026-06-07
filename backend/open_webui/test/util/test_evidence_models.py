import os
from unittest.mock import patch

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, Boolean, Integer, Text, UniqueConstraint, create_engine, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.internal.db import JSONField
from open_webui.migrations.versions import d1e2f3a4b5c6_add_multimodal_evidence_schema as evidence_migration
from open_webui.models.evidence import (
    KnowledgeEvidence,
    KnowledgeEvidenceAsset,
    KnowledgeEvidenceAssetVariant,
    KnowledgeEvidenceAssetVariants,
    KnowledgeEvidenceAssets,
    KnowledgeEvidenceEmbedding,
    KnowledgeEvidenceEmbeddings,
    KnowledgeEvidences,
    KnowledgeVectorSpace,
    KnowledgeVectorSpaces,
    compute_knowledge_evidence_asset_ref,
    compute_knowledge_evidence_embedding_id,
    compute_knowledge_evidence_ref,
    compute_knowledge_vector_space_id,
)


def _run_migration(engine, direction):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(evidence_migration, "op", operations):
            getattr(evidence_migration, direction)()


def _assert_unique_constraint(table, expected_columns):
    unique_constraints = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert tuple(expected_columns) in unique_constraints


def test_knowledge_evidence_asset_metadata_declares_expected_columns_constraints_and_indexes():
    table = KnowledgeEvidenceAsset.__table__

    assert table.name == "knowledge_evidence_asset"
    assert set(table.columns.keys()) >= {
        "id",
        "knowledge_id",
        "file_id",
        "asset_ref",
        "asset_kind",
        "mime_type",
        "storage_uri",
        "sha256",
        "width",
        "height",
        "page_index",
        "bbox_json",
        "anchor_json",
        "caption",
        "ocr_text",
        "surrounding_text",
        "status",
        "error",
        "created_at",
        "updated_at",
    }

    assert table.c.id.primary_key
    assert isinstance(table.c.id.type, Text)
    assert isinstance(table.c.bbox_json.type, JSONField)
    assert isinstance(table.c.anchor_json.type, JSONField)
    assert isinstance(table.c.created_at.type, BigInteger)

    _assert_unique_constraint(table, ("asset_ref",))
    index_columns = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert index_columns["ix_knowledge_evidence_asset_knowledge_id"] == ("knowledge_id",)
    assert index_columns["ix_knowledge_evidence_asset_file_id"] == ("file_id",)
    assert index_columns["ix_knowledge_evidence_asset_status"] == ("status",)


def test_knowledge_evidence_asset_variant_metadata_declares_expected_columns_constraints_and_indexes():
    table = KnowledgeEvidenceAssetVariant.__table__

    assert table.name == "knowledge_evidence_asset_variant"
    assert set(table.columns.keys()) >= {
        "id",
        "asset_id",
        "variant_kind",
        "storage_uri",
        "mime_type",
        "width",
        "height",
        "byte_size",
        "transform_config_hash",
        "expires_at",
        "created_at",
        "updated_at",
    }

    assert table.c.id.primary_key
    assert isinstance(table.c.id.type, Text)
    assert isinstance(table.c.byte_size.type, Integer)
    assert isinstance(table.c.expires_at.type, BigInteger)

    _assert_unique_constraint(table, ("asset_id", "variant_kind", "transform_config_hash"))
    index_columns = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert index_columns["ix_knowledge_evidence_asset_variant_asset_id"] == ("asset_id",)
    assert index_columns["ix_knowledge_evidence_asset_variant_variant_kind"] == ("variant_kind",)


def test_knowledge_evidence_metadata_declares_expected_columns_constraints_and_indexes():
    table = KnowledgeEvidence.__table__

    assert table.name == "knowledge_evidence"
    assert set(table.columns.keys()) >= {
        "id",
        "evidence_ref",
        "knowledge_id",
        "file_id",
        "asset_id",
        "retrieval_chunk_uid",
        "retrieval_chunk_row_id",
        "modality",
        "evidence_kind",
        "title",
        "content_text",
        "preview_text",
        "source_name",
        "page_index",
        "anchor_json",
        "chunk_index",
        "chunk_total",
        "content_hash",
        "projection_profile",
        "projection_config_hash",
        "is_active",
        "deleted_at",
        "created_at",
        "updated_at",
    }

    assert table.c.id.primary_key
    assert isinstance(table.c.id.type, Text)
    assert isinstance(table.c.anchor_json.type, JSONField)
    assert isinstance(table.c.is_active.type, Boolean)
    assert isinstance(table.c.created_at.type, BigInteger)

    _assert_unique_constraint(table, ("evidence_ref",))
    index_columns = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert index_columns["ix_knowledge_evidence_knowledge_id"] == ("knowledge_id",)
    assert index_columns["ix_knowledge_evidence_file_id"] == ("file_id",)
    assert index_columns["ix_knowledge_evidence_asset_id"] == ("asset_id",)
    assert index_columns["ix_knowledge_evidence_retrieval_chunk_uid"] == ("retrieval_chunk_uid",)
    assert index_columns["ix_knowledge_evidence_retrieval_chunk_row_id"] == ("retrieval_chunk_row_id",)


def test_knowledge_vector_space_metadata_declares_expected_columns_constraints_and_indexes():
    table = KnowledgeVectorSpace.__table__

    assert table.name == "knowledge_vector_space"
    assert set(table.columns.keys()) >= {
        "id",
        "knowledge_id",
        "retrieval_profile",
        "embedding_model",
        "embedding_dim",
        "distance_metric",
        "vector_backend",
        "supports_text_query",
        "supports_image_query",
        "supports_text_evidence",
        "supports_image_evidence",
        "supports_multivector",
        "projection_config_hash",
        "active",
        "created_at",
        "updated_at",
    }

    assert table.c.id.primary_key
    assert isinstance(table.c.id.type, Text)
    assert isinstance(table.c.embedding_dim.type, Integer)
    assert isinstance(table.c.active.type, Boolean)

    _assert_unique_constraint(table, ("knowledge_id", "retrieval_profile", "projection_config_hash"))
    index_columns = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert index_columns["ix_knowledge_vector_space_knowledge_id"] == ("knowledge_id",)
    assert index_columns["ix_knowledge_vector_space_active"] == ("active",)


def test_knowledge_evidence_embedding_metadata_declares_expected_columns_constraints_and_indexes():
    table = KnowledgeEvidenceEmbedding.__table__

    assert table.name == "knowledge_evidence_embedding"
    assert set(table.columns.keys()) >= {
        "id",
        "evidence_id",
        "evidence_ref",
        "vector_space_id",
        "vector_backend_collection",
        "vector_backend_id",
        "vector_role",
        "embedding_format",
        "embedding_status",
        "embedding_error",
        "created_at",
        "updated_at",
    }

    assert table.c.id.primary_key
    assert isinstance(table.c.id.type, Text)
    assert isinstance(table.c.created_at.type, BigInteger)

    _assert_unique_constraint(table, ("evidence_id", "vector_space_id", "vector_role", "vector_backend_collection"))
    index_columns = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert index_columns["ix_knowledge_evidence_embedding_evidence_id"] == ("evidence_id",)
    assert index_columns["ix_knowledge_evidence_embedding_evidence_ref"] == ("evidence_ref",)
    assert index_columns["ix_knowledge_evidence_embedding_vector_space_id"] == ("vector_space_id",)
    assert index_columns["ix_knowledge_evidence_embedding_status"] == ("embedding_status",)


def test_evidence_identity_helpers_are_deterministic_and_scope_aware():
    asset_kwargs = {
        "knowledge_id": "kb-1",
        "file_id": "file-1",
        "asset_kind": "standalone_image",
        "sha256": "abc123",
        "page_index": 2,
        "bbox_json": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4, "unit": "page_ratio"},
        "anchor_json": {"page_index": 2, "figure_id": "fig-1"},
    }
    evidence_kwargs = {
        "knowledge_id": "kb-1",
        "file_id": "file-1",
        "modality": "image",
        "evidence_kind": "document_image",
        "content_hash": "abc123",
        "projection_config_hash": "proj-a",
        "chunk_index": 1,
        "chunk_total": 1,
    }
    vector_space_kwargs = {
        "knowledge_id": "kb-1",
        "retrieval_profile": "unified_multimodal_dense",
        "embedding_model": "multimodal-embed-v1",
        "projection_config_hash": "proj-a",
        "distance_metric": "cosine",
        "vector_backend": "pgvector",
    }

    assert compute_knowledge_evidence_asset_ref(**asset_kwargs) == compute_knowledge_evidence_asset_ref(
        **dict(reversed(asset_kwargs.items()))
    )
    assert compute_knowledge_evidence_asset_ref(**asset_kwargs) != compute_knowledge_evidence_asset_ref(
        **{**asset_kwargs, "sha256": "def456"}
    )

    assert compute_knowledge_evidence_ref(**evidence_kwargs) == compute_knowledge_evidence_ref(
        **dict(reversed(evidence_kwargs.items()))
    )
    assert compute_knowledge_evidence_ref(**evidence_kwargs) != compute_knowledge_evidence_ref(
        **{**evidence_kwargs, "content_hash": "def456"}
    )

    assert compute_knowledge_vector_space_id(**vector_space_kwargs) == compute_knowledge_vector_space_id(
        **dict(reversed(vector_space_kwargs.items()))
    )
    assert compute_knowledge_vector_space_id(**vector_space_kwargs) != compute_knowledge_vector_space_id(
        **{**vector_space_kwargs, "projection_config_hash": "proj-b"}
    )

    embedding_id = compute_knowledge_evidence_embedding_id(
        evidence_ref="ke:kb-1:file-1:document_image:1:abc123",
        vector_space_id="kvs:kb-1:unified_multimodal_dense:abc123",
        vector_role="image_dense",
        vector_backend_collection="kb-1:kvs-1",
    )
    assert embedding_id == compute_knowledge_evidence_embedding_id(
        evidence_ref="ke:kb-1:file-1:document_image:1:abc123",
        vector_space_id="kvs:kb-1:unified_multimodal_dense:abc123",
        vector_role="image_dense",
        vector_backend_collection="kb-1:kvs-1",
    )
    assert embedding_id != compute_knowledge_evidence_embedding_id(
        evidence_ref="ke:kb-1:file-1:document_image:1:def456",
        vector_space_id="kvs:kb-1:unified_multimodal_dense:abc123",
        vector_role="image_dense",
        vector_backend_collection="kb-1:kvs-1",
    )


@pytest.mark.asyncio
async def test_evidence_model_helpers_create_list_and_fetch_rows(tmp_path):
    db_path = tmp_path / "evidence.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        asset = await KnowledgeEvidenceAssets.create_asset(
            knowledge_id="kb-1",
            file_id="file-1",
            asset_kind="standalone_image",
            mime_type="image/png",
            storage_uri="storage://asset-1.png",
            sha256="asset-sha-1",
            width=1024,
            height=768,
            page_index=3,
            bbox_json={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4, "unit": "page_ratio"},
            anchor_json={"page_index": 3, "figure_id": "fig-1"},
            caption="A blue box",
            ocr_text="BOX A",
            surrounding_text="This figure shows box A.",
            status="ready",
            db=session,
        )

        variant = await KnowledgeEvidenceAssetVariants.create_variant(
            asset_id=asset.id,
            variant_kind="thumbnail",
            storage_uri="storage://asset-1-thumb.webp",
            mime_type="image/webp",
            width=256,
            height=192,
            byte_size=1024,
            transform_config_hash="thumb-v1",
            db=session,
        )

        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="multimodal-embed-v1",
            projection_config_hash="proj-a",
            embedding_dim=2048,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            supports_multivector=False,
            active=True,
            db=session,
        )

        evidence_ref = compute_knowledge_evidence_ref(
            knowledge_id="kb-1",
            file_id="file-1",
            modality="image",
            evidence_kind="standalone_image",
            content_hash="asset-sha-1",
            projection_config_hash="proj-a",
            chunk_index=1,
            chunk_total=1,
        )
        evidence = await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-1",
            asset_id=asset.id,
            retrieval_chunk_uid=None,
            retrieval_chunk_row_id=None,
            modality="image",
            evidence_kind="standalone_image",
            title="Box A",
            content_text="A blue box. OCR: BOX A.",
            preview_text="A blue box. OCR: BOX A.",
            source_name="box-a.png",
            page_index=3,
            anchor_json={"page_index": 3, "figure_id": "fig-1"},
            chunk_index=1,
            chunk_total=1,
            content_hash="asset-sha-1",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="proj-a",
            evidence_ref=evidence_ref,
            is_active=True,
            db=session,
        )

        embedding = await KnowledgeEvidenceEmbeddings.create_embedding(
            evidence_id=evidence.id,
            evidence_ref=evidence.evidence_ref,
            vector_space_id=vector_space.id,
            vector_backend_collection="kb-1:vector-space-1",
            vector_backend_id="vector-row-1",
            vector_role="image_dense",
            embedding_format="single_dense",
            embedding_status="ready",
            db=session,
        )

        fetched_asset = await KnowledgeEvidenceAssets.get_asset_by_id(asset.id, db=session)
        fetched_asset_by_ref = await KnowledgeEvidenceAssets.get_asset_by_ref(asset.asset_ref, db=session)
        fetched_variant = await KnowledgeEvidenceAssetVariants.get_variant_by_id(variant.id, db=session)
        fetched_vector_space = await KnowledgeVectorSpaces.get_vector_space_by_id(vector_space.id, db=session)
        active_vector_space = await KnowledgeVectorSpaces.get_active_vector_space(
            knowledge_id="kb-1",
            db=session,
        )
        fetched_evidence = await KnowledgeEvidences.get_evidence_by_id(evidence.id, db=session)
        fetched_evidence_by_ref = await KnowledgeEvidences.get_evidence_by_ref(evidence.evidence_ref, db=session)
        fetched_embedding = await KnowledgeEvidenceEmbeddings.get_embedding_by_id(embedding.id, db=session)

        assert fetched_asset is not None
        assert fetched_asset.asset_ref == asset.asset_ref
        assert fetched_asset_by_ref is not None
        assert fetched_asset_by_ref.id == asset.id
        assert fetched_variant is not None
        assert fetched_variant.asset_id == asset.id
        assert fetched_vector_space is not None
        assert fetched_vector_space.id == vector_space.id
        assert active_vector_space is not None
        assert active_vector_space.id == vector_space.id
        assert fetched_evidence is not None
        assert fetched_evidence.evidence_ref == evidence.evidence_ref
        assert fetched_evidence_by_ref is not None
        assert fetched_evidence_by_ref.asset_id == asset.id
        assert fetched_embedding is not None
        assert fetched_embedding.vector_space_id == vector_space.id

        assets = await KnowledgeEvidenceAssets.list_assets(knowledge_id="kb-1", db=session)
        variants = await KnowledgeEvidenceAssetVariants.list_variants(asset_id=asset.id, db=session)
        evidences = await KnowledgeEvidences.list_evidences(knowledge_id="kb-1", db=session)
        vector_spaces = await KnowledgeVectorSpaces.list_vector_spaces(knowledge_id="kb-1", db=session)
        embeddings = await KnowledgeEvidenceEmbeddings.list_embeddings(
            evidence_ref=evidence.evidence_ref,
            db=session,
        )

        assert [row.id for row in assets] == [asset.id]
        assert [row.id for row in variants] == [variant.id]
        assert [row.id for row in evidences] == [evidence.id]
        assert [row.id for row in vector_spaces] == [vector_space.id]
        assert [row.id for row in embeddings] == [embedding.id]

    await engine.dispose()


def test_evidence_migration_upgrade_and_downgrade_tolerate_existing_surface():
    engine = create_engine("sqlite:///:memory:")

    _run_migration(engine, "upgrade")
    _run_migration(engine, "upgrade")

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= {
        "knowledge_evidence_asset",
        "knowledge_evidence_asset_variant",
        "knowledge_evidence",
        "knowledge_vector_space",
        "knowledge_evidence_embedding",
    }

    _run_migration(engine, "downgrade")
    _run_migration(engine, "downgrade")
