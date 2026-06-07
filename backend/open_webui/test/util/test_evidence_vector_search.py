import os
import types
from contextlib import asynccontextmanager
from pathlib import Path

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from open_webui.migrations.versions import d1e2f3a4b5c6_add_multimodal_evidence_schema as evidence_migration
from open_webui.models.evidence import (
    KnowledgeEvidence,
    KnowledgeEvidenceAsset,
    KnowledgeEvidenceAssets,
    KnowledgeEvidenceEmbeddings,
    KnowledgeEvidences,
    KnowledgeVectorSpaces,
)
from open_webui.models.files import File
from open_webui.models.knowledge import Knowledge
from open_webui.retrieval.evidence import (
    EvidenceToolError,
    normalize_query_knowledge_evidence_args,
    search_multimodal_evidence,
)
from open_webui.retrieval.vector import multimodal as multimodal_mod
from open_webui.retrieval.vector.multimodal import MultimodalVectorSpaceError, upsert_multimodal_evidence_embedding


class _FakeVectorClient:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []
        self.upsert_calls: list[dict[str, object]] = []

    async def search(self, collection_name, vectors, filter=None, limit=10):
        vector = [round(float(value), 3) for value in vectors[0][:3]]
        self.search_calls.append(
            {
                "collection_name": collection_name,
                "vector": vector,
                "limit": limit,
            }
        )
        if vector == [1.0, 0.0, 0.0]:
            return types.SimpleNamespace(
                ids=[["vec-text"]],
                metadatas=[[{"evidence_ref": "ke:kb-1:file-text:text_chunk:1:txt", "vector_space_id": "vs-1"}]],
                distances=[[0.11]],
            )
        if vector == [0.0, 1.0, 0.0]:
            return types.SimpleNamespace(
                ids=[["vec-image"]],
                metadatas=[[{"evidence_ref": "ke:kb-1:file-img:standalone_image:1:img", "vector_space_id": "vs-1"}]],
                distances=[[0.21]],
            )
        return types.SimpleNamespace(
            ids=[["vec-mixed-image", "vec-mixed-text"]],
            metadatas=[
                [
                    {"evidence_ref": "ke:kb-1:file-img:standalone_image:1:img", "vector_space_id": "vs-1"},
                    {"evidence_ref": "ke:kb-1:file-text:text_chunk:1:txt", "vector_space_id": "vs-1"},
                ]
            ],
            distances=[[0.08, 0.19]],
        )

    async def upsert(self, collection_name, items):
        self.upsert_calls.append(
            {
                "collection_name": collection_name,
                "items": items,
            }
        )
        return None


def _run_migration(engine, direction):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(evidence_migration, "op", operations)
            getattr(evidence_migration, direction)()


@asynccontextmanager
async def _db_session_ctx(tmp_path: Path):
    db_path = tmp_path / "evidence.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")

    with sync_engine.begin() as connection:
        Knowledge.__table__.create(connection, checkfirst=True)
        File.__table__.create(connection, checkfirst=True)
        KnowledgeEvidenceAsset.__table__.create(connection, checkfirst=True)
        KnowledgeEvidence.__table__.create(connection, checkfirst=True)

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await async_engine.dispose()


async def _seed_knowledge_and_file(session: AsyncSession, *, file_id: str, filename: str, content_type: str, path: str):
    session.add(
        Knowledge(
            id="kb-1",
            user_id="user-1",
            name="Knowledge",
            description="",
            meta={"evidence_mode": "evidence"},
            created_at=1,
            updated_at=1,
        )
    )
    session.add(
        File(
            id=file_id,
            user_id="user-1",
            hash=f"{file_id}-hash",
            filename=filename,
            path=path,
            data={"status": "completed"},
            meta={"content_type": content_type, "name": filename},
            created_at=1,
            updated_at=1,
        )
    )
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query_kwargs, expected_vector, expected_refs",
    [
        (
            {
                "query_text": "find the capsid fold",
                "knowledge_ids": ["kb-1"],
                "count": 4,
            },
            [1.0, 0.0, 0.0],
            ["ke:kb-1:file-text:text_chunk:1:txt"],
        ),
        (
            {
                "query_image_refs": ["chat:file:query-image"],
                "knowledge_ids": ["kb-1"],
                "count": 4,
            },
            [0.0, 1.0, 0.0],
            ["ke:kb-1:file-img:standalone_image:1:img"],
        ),
        (
            {
                "query_text": "find the figure and fold",
                "query_image_refs": ["chat:file:query-image"],
                "knowledge_ids": ["kb-1"],
                "count": 4,
            },
            [2.0, 2.0, 0.0],
            [
                "ke:kb-1:file-img:standalone_image:1:img",
                "ke:kb-1:file-text:text_chunk:1:txt",
            ],
        ),
    ],
)
async def test_search_multimodal_evidence_uses_query_embeddings_and_hydrates_evidence_refs(
    tmp_path,
    monkeypatch,
    query_kwargs,
    expected_vector,
    expected_refs,
):
    query_image_path = tmp_path / "query.png"
    query_image_bytes = b"\x89PNG\r\n\x1a\nquery-image"
    query_image_path.write_bytes(query_image_bytes)

    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-text",
            filename="paper.pdf",
            content_type="application/pdf",
            path="/tmp/paper.pdf",
        )
        session.add(
            File(
                id="file-img",
                user_id="user-1",
                hash="file-img-hash",
                filename="figure.png",
                path="/tmp/figure.png",
                data={"status": "completed"},
                meta={"content_type": "image/png", "name": "figure.png"},
                created_at=1,
                updated_at=1,
            )
        )
        query_file = File(
            id="query-image",
            user_id="user-1",
            hash="query-image-hash",
            filename="query.png",
            path=str(query_image_path),
            data={"status": "completed"},
            meta={"content_type": "image/png", "name": "query.png"},
            created_at=1,
            updated_at=1,
        )
        session.add(query_file)
        await session.commit()

        async def fake_get_file_by_id(file_id, db=None):
            if file_id == "query-image":
                return multimodal_mod.FileModel.model_validate(query_file)
            return None

        monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)
        monkeypatch.setattr(multimodal_mod.Storage, "get_file", lambda storage_uri: storage_uri)

        text_evidence = await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-text",
            modality="text",
            evidence_kind="text_chunk",
            content_hash="hash-text",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="paper.pdf",
            content_text="The capsid shell has a conserved HK97-like fold.",
            preview_text="Conserved HK97-like fold.",
            title="Text finding",
            retrieval_chunk_uid="chunk-1",
            retrieval_chunk_row_id=1,
            evidence_ref="ke:kb-1:file-text:text_chunk:1:txt",
            db=session,
        )
        image_asset = await KnowledgeEvidenceAssets.create_asset(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_kind="standalone_image",
            mime_type="image/png",
            storage_uri="/tmp/figure.png",
            sha256="sha-image",
            status="ready",
            db=session,
        )
        image_evidence = await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_id=image_asset.id,
            modality="image",
            evidence_kind="standalone_image",
            content_hash="hash-image",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="figure.png",
            content_text="A microscopy panel with ring-like capsid particles.",
            preview_text="Ring-like capsid particles.",
            title="Gel image",
            evidence_ref="ke:kb-1:file-img:standalone_image:1:img",
            db=session,
        )

        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="fake-multimodal-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        embed_calls: list[object] = []

        async def fake_embedding(query, prefix=None, user=None):
            embed_calls.append(query)
            if isinstance(query, dict):
                query_images = query.get("query_images") or []
                if query_images:
                    assert query_images[0]["ref"] == "chat:file:query-image"
                    assert query_images[0]["file_id"] == "query-image"
                    assert query_images[0]["mime_type"] == "image/png"
                    assert query_images[0]["image_bytes"] == query_image_bytes
                assert "query_image_refs" not in query
                if query_images and query.get("query_text"):
                    return [2.0, 2.0, 0.0]
                if query_images:
                    return [0.0, 1.0, 0.0]
            return [1.0, 0.0, 0.0]

        vector_client = _FakeVectorClient()

        query = normalize_query_knowledge_evidence_args(**query_kwargs)
        hits = await search_multimodal_evidence(
            query=query,
            vector_spaces=[vector_space],
            embedding_function=fake_embedding,
            vector_client=vector_client,
            user={"id": "user-1", "role": "user"},
            request=None,
        )

        assert [hit["evidence_ref"] for hit in hits] == expected_refs
        assert vector_client.search_calls[0]["vector"] == expected_vector
        if query.query_image_refs and query.query_text:
            assert isinstance(embed_calls[0], dict)
            assert embed_calls[0]["query_text"] == "find the figure and fold"
            assert embed_calls[0]["query_images"][0]["image_bytes"] == query_image_bytes
        elif query.query_image_refs:
            assert isinstance(embed_calls[0], dict)
            assert embed_calls[0]["query_images"][0]["image_bytes"] == query_image_bytes
            assert embed_calls[0]["query_text"] is None
        else:
            assert embed_calls[0] == "find the capsid fold"


@pytest.mark.asyncio
async def test_search_multimodal_evidence_rejects_image_query_when_vector_space_cannot_support_it(tmp_path):
    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-img",
            filename="figure.png",
            content_type="image/png",
            path="/tmp/figure.png",
        )

        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="text_surrogate_only",
            embedding_model="fake-text-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=False,
            supports_text_evidence=True,
            supports_image_evidence=False,
            active=True,
            db=session,
        )

        query = normalize_query_knowledge_evidence_args(
            query_image_refs=["chat:file:query-image"],
            knowledge_ids=["kb-1"],
            count=4,
        )

        with pytest.raises(MultimodalVectorSpaceError) as exc_info:
            await search_multimodal_evidence(
                query=query,
                vector_spaces=[vector_space],
                embedding_function=lambda *_args, **_kwargs: [0.0, 1.0, 0.0],
                vector_client=_FakeVectorClient(),
                user={"id": "user-1", "role": "user"},
                request=None,
            )

        assert exc_info.value.code == "unsupported_image_query"


@pytest.mark.asyncio
async def test_search_multimodal_evidence_fails_closed_when_query_image_ref_cannot_be_resolved(
    tmp_path, monkeypatch
):
    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-img",
            filename="figure.png",
            content_type="image/png",
            path="/tmp/figure.png",
        )

        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="fake-multimodal-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        async def fake_get_file_by_id(file_id, db=None):
            return None

        monkeypatch.setattr(multimodal_mod.Files, "get_file_by_id", fake_get_file_by_id)

        query = normalize_query_knowledge_evidence_args(
            query_image_refs=["chat:file:missing-query-image"],
            knowledge_ids=["kb-1"],
            count=4,
        )

        with pytest.raises(MultimodalVectorSpaceError) as exc_info:
            await search_multimodal_evidence(
                query=query,
                vector_spaces=[vector_space],
                embedding_function=lambda *_args, **_kwargs: [0.0, 1.0, 0.0],
                vector_client=_FakeVectorClient(),
                user={"id": "user-1", "role": "user"},
                request=None,
            )

        assert exc_info.value.code == "unsupported_image_query"


@pytest.mark.asyncio
async def test_upsert_multimodal_evidence_embedding_links_truth_rows_to_vector_backend_ids(tmp_path):
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")

    async with _db_session_ctx(tmp_path) as session:
        await _seed_knowledge_and_file(
            session,
            file_id="file-img",
            filename="figure.png",
            content_type="image/png",
            path=str(image_path),
        )

        asset = await KnowledgeEvidenceAssets.create_asset(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_kind="standalone_image",
            mime_type="image/png",
            storage_uri=str(image_path),
            sha256="sha-image",
            status="ready",
            db=session,
        )
        evidence = await KnowledgeEvidences.create_evidence(
            knowledge_id="kb-1",
            file_id="file-img",
            asset_id=asset.id,
            modality="image",
            evidence_kind="standalone_image",
            content_hash="hash-image",
            projection_profile="unified_multimodal_dense",
            projection_config_hash="profile-hash",
            chunk_index=1,
            chunk_total=1,
            source_name="figure.png",
            content_text="A microscopy panel with ring-like capsid particles.",
            preview_text="Ring-like capsid particles.",
            title="Gel image",
            evidence_ref="ke:kb-1:file-img:standalone_image:1:img",
            db=session,
        )
        vector_space = await KnowledgeVectorSpaces.create_vector_space(
            knowledge_id="kb-1",
            retrieval_profile="unified_multimodal_dense",
            embedding_model="fake-multimodal-embed",
            projection_config_hash="profile-hash",
            embedding_dim=3,
            distance_metric="cosine",
            vector_backend="pgvector",
            supports_text_query=True,
            supports_image_query=True,
            supports_text_evidence=True,
            supports_image_evidence=True,
            active=True,
            db=session,
        )

        embed_calls: list[object] = []

        async def fake_embedding(payload, prefix=None, user=None):
            embed_calls.append(payload)
            assert isinstance(payload, dict)
            assert payload["evidence_ref"] == evidence.evidence_ref
            assert payload["modality"] == "image"
            assert payload["image_bytes"] == image_path.read_bytes()
            return [0.1, 0.2, 0.3]

        vector_client = _FakeVectorClient()

        result = await upsert_multimodal_evidence_embedding(
            evidence=evidence,
            vector_space=vector_space,
            embedding_function=fake_embedding,
            vector_client=vector_client,
            db=session,
        )

        embeddings = await KnowledgeEvidenceEmbeddings.list_embeddings(
            evidence_ref=evidence.evidence_ref,
            vector_space_id=vector_space.id,
            db=session,
        )

        assert result.embedding.embedding_status == "ready"
        assert result.embedding.vector_backend_collection == f"kb-1:{vector_space.id}"
        assert result.embedding.vector_backend_id == result.vector_item.id
        assert embed_calls and isinstance(embed_calls[0], dict)
        assert vector_client.upsert_calls[0]["collection_name"] == f"kb-1:{vector_space.id}"
        assert vector_client.upsert_calls[0]["items"][0].metadata["evidence_ref"] == evidence.evidence_ref
        assert vector_client.upsert_calls[0]["items"][0].metadata["knowledge_id"] == "kb-1"
        assert vector_client.upsert_calls[0]["items"][0].metadata["file_id"] == "file-img"
        assert vector_client.upsert_calls[0]["items"][0].metadata["vector_space_id"] == vector_space.id
        assert len(embeddings) == 1
        assert embeddings[0].vector_backend_id == result.vector_item.id
        assert embeddings[0].embedding_status == "ready"
