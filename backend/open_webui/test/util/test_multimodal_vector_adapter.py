import os
from unittest.mock import patch

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.migrations.versions import d1e2f3a4b5c6_add_multimodal_evidence_schema as evidence_migration
from open_webui.models.evidence import KnowledgeVectorSpaces
from open_webui.models.knowledge import Knowledge  # noqa: F401
from open_webui.retrieval.vector.multimodal import (
    MultimodalVectorSpaceError,
    build_multimodal_vector_item,
    normalize_multimodal_evidence_input,
    resolve_multimodal_vector_space,
)


def _run_migration(engine, direction):
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE IF NOT EXISTS knowledge (id TEXT PRIMARY KEY)")
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(evidence_migration, "op", operations):
            getattr(evidence_migration, direction)()


@pytest.mark.asyncio
async def test_resolve_multimodal_vector_space_prefers_explicit_profile_and_respects_capabilities(tmp_path):
    db_path = tmp_path / "multimodal_vector_space.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        text_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="text_surrogate_only",
            embedding_model="text-embed-v1",
            projection_config_hash="proj-text",
            embedding_dim=1024,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=False,
            supports_text_evidence=True,
            supports_image_evidence=False,
            active=True,
            db=session,
        )
        multimodal_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="multimodal-embed-v1",
            projection_config_hash="proj-mm",
            embedding_dim=2048,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        text_selection = await resolve_multimodal_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="text_surrogate_only",
            query_modality="text",
            evidence_modality="text",
            db=session,
        )
        image_selection = await resolve_multimodal_vector_space(
            knowledge_id="kb-1",
            vector_space_id=multimodal_space.id,
            query_modality="image",
            evidence_modality="image",
            db=session,
        )

        assert text_selection.vector_space.id == text_space.id
        assert text_selection.collection_name == f"kb-1:{text_space.id}"
        assert image_selection.vector_space.id == multimodal_space.id
        assert image_selection.collection_name == f"kb-1:{multimodal_space.id}"

    await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_multimodal_vector_space_rejects_unsupported_image_query_without_fallback(tmp_path):
    db_path = tmp_path / "unsupported_image_query.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        text_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="text_surrogate_only",
            embedding_model="text-embed-v1",
            projection_config_hash="proj-text",
            embedding_dim=1024,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=False,
            supports_text_evidence=True,
            supports_image_evidence=False,
            active=True,
            db=session,
        )

        with pytest.raises(MultimodalVectorSpaceError) as exc_info:
            await resolve_multimodal_vector_space(
                knowledge_id="kb-1",
                vector_space_id=text_space.id,
                query_modality="image",
                db=session,
            )

        assert exc_info.value.code == "unsupported_image_query"

    await engine.dispose()


def test_normalize_multimodal_evidence_input_rejects_unsafe_image_path_url_and_base64_inputs():
    base = {
        "modality": "image",
        "knowledge_id": "kb-1",
        "file_id": "file-1",
        "content_hash": "sha-1",
        "projection_config_hash": "proj-1",
        "source_name": "figure.png",
    }

    for unsafe_field, unsafe_value in (
        ("image_url", {"url": "https://example.com/image.png"}),
        ("url", "https://example.com/image.png"),
        ("path", "/tmp/image.png"),
        ("file_path", "./relative/image.png"),
        ("data_url", "data:image/png;base64,AAAA"),
        ("base64", "AAAA"),
    ):
        with pytest.raises(MultimodalVectorSpaceError) as exc_info:
            normalize_multimodal_evidence_input({**base, unsafe_field: unsafe_value})

        assert exc_info.value.code == "unsafe_image_descriptor"


@pytest.mark.asyncio
async def test_build_multimodal_vector_item_carries_evidence_identity_and_shared_space_metadata(tmp_path):
    db_path = tmp_path / "multimodal_vector_item.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="multimodal-embed-v1",
            projection_config_hash="proj-mm",
            embedding_dim=2048,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )
        selection = await resolve_multimodal_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            query_modality="image",
            evidence_modality="image",
            db=session,
        )

        text_descriptor = normalize_multimodal_evidence_input(
            {
                "modality": "text",
                "knowledge_id": "kb-1",
                "file_id": "file-1",
                "evidence_ref": "ke:kb-1:file-1:text_chunk:0:abc123",
                "evidence_kind": "text_chunk",
                "content_hash": "abc123",
                "projection_config_hash": "proj-mm",
                "source_name": "notes.md",
                "text": "alpha beta",
                "chunk_index": 1,
                "chunk_total": 2,
            }
        )
        image_descriptor = normalize_multimodal_evidence_input(
            {
                "modality": "image",
                "knowledge_id": "kb-1",
                "file_id": "file-1",
                "evidence_ref": "ke:kb-1:file-1:standalone_image:0:def456",
                "evidence_kind": "standalone_image",
                "content_hash": "def456",
                "projection_config_hash": "proj-mm",
                "source_name": "figure.png",
                "preview_text": "Figure A: comparison chart",
                "asset_ref": "ka:kb-1:file-1:standalone_image:def456",
                "chunk_index": 1,
                "chunk_total": 1,
            }
        )

        text_item = build_multimodal_vector_item(
            vector=[0.1, 0.2, 0.3],
            descriptor=text_descriptor,
            selection=selection,
        )
        image_item = build_multimodal_vector_item(
            vector=[0.4, 0.5, 0.6],
            descriptor=image_descriptor,
            selection=selection,
        )

        assert text_item.text == "alpha beta"
        assert image_item.text == "Figure A: comparison chart"
        assert text_item.id != image_item.id
        assert text_item.metadata["evidence_ref"] == "ke:kb-1:file-1:text_chunk:0:abc123"
        assert text_item.metadata["modality"] == "text"
        assert text_item.metadata["vector_space_id"] == vector_space.id
        assert text_item.metadata["vector_backend_collection"] == f"kb-1:{vector_space.id}"
        assert text_item.metadata["vector_role"] == "text_chunk_dense"
        assert image_item.metadata["evidence_ref"] == "ke:kb-1:file-1:standalone_image:0:def456"
        assert image_item.metadata["modality"] == "image"
        assert image_item.metadata["vector_space_id"] == vector_space.id
        assert image_item.metadata["vector_backend_collection"] == f"kb-1:{vector_space.id}"
        assert image_item.metadata["vector_role"] == "image_dense"
        assert image_item.metadata["retrieval_profile"] == "unified_multimodal_dense"
        assert image_item.metadata["projection_config_hash"] == "proj-mm"

    await engine.dispose()
