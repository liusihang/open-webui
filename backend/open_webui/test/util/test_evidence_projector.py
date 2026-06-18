import os
import tempfile
from unittest.mock import patch

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from open_webui.migrations.versions import d1e2f3a4b5c6_add_multimodal_evidence_schema as evidence_migration
from open_webui.models.evidence import (
    KnowledgeEvidence,
    KnowledgeEvidenceAsset,
    KnowledgeEvidenceEmbedding,
    KnowledgeVectorSpace,
)
from open_webui.models.files import File
from open_webui.models.knowledge import Knowledge
from open_webui.models.retrieval_chunks import RetrievalChunk
from open_webui.retrieval.evidence_projector import (
    EvidenceProjectionResult,
    backfill_text_evidence_from_active_chunks,
    project_evidence_for_knowledge_file,
    project_evidence_from_job_payload,
)
import open_webui.retrieval.indexing as indexing_mod
import open_webui.retrieval.evidence_projector as projector_mod


def _run_migration(engine, direction):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(evidence_migration, "op", operations):
            getattr(evidence_migration, direction)()


@pytest_asyncio.fixture
async def db_session():
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.name}")
    async with engine.begin() as connection:
        await connection.run_sync(Knowledge.__table__.create)
        await connection.run_sync(File.__table__.create)
        await connection.run_sync(RetrievalChunk.__table__.create)
        await connection.run_sync(KnowledgeVectorSpace.__table__.create)
        await connection.run_sync(KnowledgeEvidenceAsset.__table__.create)
        await connection.run_sync(KnowledgeEvidence.__table__.create)
        await connection.run_sync(KnowledgeEvidenceEmbedding.__table__.create)

    session_factory = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()
    os.unlink(db_file.name)


async def _seed_knowledge_file(
    session: AsyncSession,
    *,
    file_id: str,
    filename: str,
    content_type: str,
    path: str,
    meta: dict | None = None,
    data: dict | None = None,
):
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
            hash="file-hash-1",
            filename=filename,
            path=path,
            data=data or {"status": "completed"},
            meta={"content_type": content_type, "name": filename, **(meta or {})},
            created_at=1,
            updated_at=1,
        )
    )
    await session.commit()


async def _seed_active_text_chunk(session: AsyncSession, *, row_id: int, file_id: str, text: str):
    session.add(
        RetrievalChunk(
            row_id=row_id,
            chunk_uid=f"chunk-{row_id}",
            collection_id="kb-1",
            knowledge_id="kb-1",
            collection_name="kb-1",
            file_id=file_id,
            file_version=1,
            chunk_version=1,
            chunk_index=row_id - 1,
            start_index=(row_id - 1) * 100,
            content_hash=f"content-hash-{row_id}",
            chunker_config_hash="chunker-hash",
            text=text,
            metadata_={"name": "doc.pdf", "page_index": 3},
            is_active=True,
            deleted_at=None,
            created_at=1,
            updated_at=1,
        )
    )
    await session.commit()


async def _seed_active_text_chunk_for_scope(
    session: AsyncSession,
    *,
    row_id: int,
    file_id: str,
    text: str,
    collection_id: str,
    knowledge_id: str,
    collection_name: str,
):
    session.add(
        RetrievalChunk(
            row_id=row_id,
            chunk_uid=f"chunk-{row_id}",
            collection_id=collection_id,
            knowledge_id=knowledge_id,
            collection_name=collection_name,
            file_id=file_id,
            file_version=1,
            chunk_version=1,
            chunk_index=row_id - 1,
            start_index=(row_id - 1) * 100,
            content_hash=f"content-hash-{row_id}",
            chunker_config_hash="chunker-hash",
            text=text,
            metadata_={"name": "doc.pdf", "page_index": 3},
            is_active=True,
            deleted_at=None,
            created_at=1,
            updated_at=1,
        )
    )
    await session.commit()


async def _seed_active_text_evidence(
    session: AsyncSession,
    *,
    evidence_id: str,
    file_id: str,
    content_text: str,
    content_hash: str,
):
    session.add(
        KnowledgeEvidence(
            id=evidence_id,
            evidence_ref=f"ke:kb-1:{file_id}:text_chunk:{content_hash}",
            knowledge_id="kb-1",
            file_id=file_id,
            asset_id=None,
            retrieval_chunk_uid=f"{evidence_id}-chunk",
            retrieval_chunk_row_id=1,
            modality="text",
            evidence_kind="text_chunk",
            title=None,
            content_text=content_text,
            preview_text=content_text,
            source_name="doc.pdf",
            page_index=None,
            anchor_json={},
            chunk_index=0,
            chunk_total=1,
            content_hash=content_hash,
            projection_profile="text_only",
            projection_config_hash="text-backfill-v1",
            is_active=True,
            deleted_at=None,
            created_at=1,
            updated_at=1,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_text_backfill_is_idempotent_and_bridges_retrieval_chunk_fields(db_session):
    await _seed_knowledge_file(
        db_session,
        file_id="file-doc",
        filename="doc.pdf",
        content_type="application/pdf",
        path="/tmp/doc.pdf",
    )
    await _seed_active_text_chunk(db_session, row_id=7, file_id="file-doc", text="First paragraph")

    first = await backfill_text_evidence_from_active_chunks(collection_ids=["kb-1"], db=db_session)
    second = await backfill_text_evidence_from_active_chunks(collection_ids=["kb-1"], db=db_session)

    evidence_rows = (
        (await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))).scalars().all()
    )

    assert first.text_evidence_upserted == 1
    assert second.text_evidence_upserted == 1
    assert first.failed == 0
    assert second.failed == 0
    assert len(evidence_rows) == 1
    assert evidence_rows[0].retrieval_chunk_uid == "chunk-7"
    assert evidence_rows[0].retrieval_chunk_row_id == 7
    assert evidence_rows[0].modality == "text"
    assert evidence_rows[0].evidence_kind == "text_chunk"
    assert evidence_rows[0].asset_id is None
    assert evidence_rows[0].content_text == "First paragraph"
    assert evidence_rows[0].preview_text == "First paragraph"
    assert evidence_rows[0].projection_profile == "text_only"


@pytest.mark.asyncio
async def test_project_knowledge_file_only_uses_chunks_from_target_knowledge(db_session):
    await _seed_knowledge_file(
        db_session,
        file_id="file-doc",
        filename="doc.pdf",
        content_type="application/pdf",
        path="/tmp/doc.pdf",
    )
    await _seed_active_text_chunk_for_scope(
        db_session,
        row_id=11,
        file_id="file-doc",
        text="Temporary file-level chunk",
        collection_id="file-file-doc",
        knowledge_id="file-file-doc",
        collection_name="file-file-doc",
    )
    await _seed_active_text_chunk_for_scope(
        db_session,
        row_id=12,
        file_id="file-doc",
        text="Knowledge chunk",
        collection_id="kb-1",
        knowledge_id="kb-1",
        collection_name="kb-1",
    )

    result = await project_evidence_for_knowledge_file(
        knowledge_id="kb-1",
        file_id="file-doc",
        db=db_session,
        project_document_images=True,
    )

    evidence_rows = (
        (await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))).scalars().all()
    )

    assert result.text_evidence_upserted == 1
    assert result.scanned_chunks == 1
    assert len(evidence_rows) == 1
    assert evidence_rows[0].knowledge_id == "kb-1"
    assert evidence_rows[0].content_text == "Knowledge chunk"
    assert evidence_rows[0].projection_profile == "unified_multimodal_dense"


@pytest.mark.asyncio
async def test_project_job_without_file_ids_defaults_text_evidence_to_multimodal_profile(db_session):
    await _seed_knowledge_file(
        db_session,
        file_id="file-doc",
        filename="doc.pdf",
        content_type="application/pdf",
        path="/tmp/doc.pdf",
    )
    await _seed_active_text_chunk(db_session, row_id=13, file_id="file-doc", text="Knowledge chunk")

    result = await project_evidence_from_job_payload({"knowledge_id": "kb-1"}, db=db_session)

    evidence_rows = (
        (await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))).scalars().all()
    )

    assert result.text_evidence_upserted == 1
    assert result.scanned_chunks == 1
    assert len(evidence_rows) == 1
    assert evidence_rows[0].projection_profile == "unified_multimodal_dense"


@pytest.mark.asyncio
async def test_projected_evidence_embeddings_create_missing_vector_space(db_session, monkeypatch):
    await _seed_knowledge_file(
        db_session,
        file_id="file-doc",
        filename="doc.pdf",
        content_type="application/pdf",
        path="/tmp/doc.pdf",
    )
    await _seed_active_text_chunk(db_session, row_id=7, file_id="file-doc", text="First paragraph")

    projection = await project_evidence_for_knowledge_file(
        knowledge_id="kb-1",
        file_id="file-doc",
        projection_profile="unified_multimodal_dense",
        db=db_session,
    )

    upserts = []

    async def fake_embedding_function(query, prefix=None, user=None):
        return [0.1, 0.2, 0.3]

    fake_embedding_function._model = "Qwen3-VL-Embedding-2B"
    fake_embedding_function._supports_image_payloads = True

    class FakeVectorClient:
        async def upsert(self, collection_name, items):
            upserts.append((collection_name, items))

    monkeypatch.setattr(
        indexing_mod,
        "_get_evidence_embedding_runtime",
        lambda: (fake_embedding_function, FakeVectorClient()),
    )

    result = await indexing_mod.write_projected_evidence_embeddings(
        projection.evidence_refs,
        db=db_session,
    )

    vector_spaces = (
        (
            await db_session.execute(
                select(KnowledgeVectorSpace).where(KnowledgeVectorSpace.knowledge_id == "kb-1")
            )
        )
        .scalars()
        .all()
    )
    embeddings = (
        (
            await db_session.execute(
                select(KnowledgeEvidenceEmbedding).where(
                    KnowledgeEvidenceEmbedding.evidence_ref.in_(projection.evidence_refs)
                )
            )
        )
        .scalars()
        .all()
    )

    assert result.written == 1
    assert result.skipped == 0
    assert result.failed == 0
    assert len(vector_spaces) == 1
    assert vector_spaces[0].retrieval_profile == "unified_multimodal_dense"
    assert vector_spaces[0].embedding_model == "Qwen3-VL-Embedding-2B"
    assert vector_spaces[0].supports_text_evidence is True
    assert vector_spaces[0].supports_image_evidence is True
    assert len(upserts) == 1
    assert len(embeddings) == 1
    assert embeddings[0].embedding_status == "ready"


@pytest.mark.asyncio
async def test_project_knowledge_file_keeps_previous_active_evidence_when_projection_fails(db_session):
    await _seed_knowledge_file(
        db_session,
        file_id="file-doc",
        filename="doc.pdf",
        content_type="application/pdf",
        path="/tmp/doc.pdf",
        meta={
            "document_image_assets": [
                {
                    "caption": "Broken image asset",
                }
            ]
        },
    )
    await _seed_active_text_evidence(
        db_session,
        evidence_id="previous-evidence",
        file_id="file-doc",
        content_text="Previous chunk",
        content_hash="previous-hash",
    )
    await _seed_active_text_chunk(db_session, row_id=14, file_id="file-doc", text="Replacement chunk")

    result = await project_evidence_for_knowledge_file(
        knowledge_id="kb-1",
        file_id="file-doc",
        db=db_session,
        project_document_images=True,
    )

    previous_row = (
        (
            await db_session.execute(
                select(KnowledgeEvidence).where(KnowledgeEvidence.id == "previous-evidence")
            )
        )
        .scalars()
        .one()
    )
    active_rows = (
        (
            await db_session.execute(
                select(KnowledgeEvidence).where(KnowledgeEvidence.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )

    assert result.failed == 1
    assert result.text_evidence_upserted == 1
    assert previous_row.is_active is True
    assert previous_row.deleted_at is None
    assert [row.id for row in active_rows] == ["previous-evidence"]


@pytest.mark.asyncio
async def test_project_knowledge_file_deactivates_stale_evidence_for_file(db_session):
    await _seed_knowledge_file(
        db_session,
        file_id="file-doc",
        filename="doc.pdf",
        content_type="application/pdf",
        path="/tmp/doc.pdf",
    )
    db_session.add(
        KnowledgeEvidence(
            id="stale-evidence",
            evidence_ref="ke:kb-1:file-doc:text_chunk:stale",
            knowledge_id="kb-1",
            file_id="file-doc",
            asset_id=None,
            retrieval_chunk_uid="stale-chunk",
            retrieval_chunk_row_id=1,
            modality="text",
            evidence_kind="text_chunk",
            title=None,
            content_text="Old chunk",
            preview_text="Old chunk",
            source_name="doc.pdf",
            page_index=None,
            anchor_json={},
            chunk_index=0,
            chunk_total=1,
            content_hash="old-hash",
            projection_profile="text_only",
            projection_config_hash="text-backfill-v1",
            is_active=True,
            deleted_at=None,
            created_at=1,
            updated_at=1,
        )
    )
    await db_session.commit()
    await _seed_active_text_chunk(db_session, row_id=12, file_id="file-doc", text="Current chunk")

    result = await project_evidence_for_knowledge_file(
        knowledge_id="kb-1",
        file_id="file-doc",
        db=db_session,
        project_document_images=True,
    )

    evidence_rows = (
        (await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))).scalars().all()
    )
    active_rows = [row for row in evidence_rows if row.is_active]
    stale_row = next(row for row in evidence_rows if row.id == "stale-evidence")

    assert result.text_evidence_upserted == 1
    assert stale_row.is_active is False
    assert stale_row.deleted_at is not None
    assert len(active_rows) == 1
    assert active_rows[0].content_text == "Current chunk"


@pytest.mark.asyncio
async def test_standalone_image_projection_creates_asset_and_evidence_without_text_bridge(db_session):
    await _seed_knowledge_file(
        db_session,
        file_id="file-img",
        filename="box-a.png",
        content_type="image/png",
        path="/tmp/box-a.png",
    )

    result = await project_evidence_for_knowledge_file(
        knowledge_id="kb-1",
        file_id="file-img",
        db=db_session,
        project_document_images=True,
    )

    assets = (
        (await db_session.execute(select(KnowledgeEvidenceAsset).order_by(KnowledgeEvidenceAsset.id.asc())))
        .scalars()
        .all()
    )
    evidence_rows = (
        (await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))).scalars().all()
    )

    assert result.image_assets_upserted == 1
    assert result.image_evidence_upserted == 1
    assert result.text_evidence_upserted == 0
    assert result.document_image_placeholders == 0
    assert len(assets) == 1
    assert len(evidence_rows) == 1
    assert evidence_rows[0].asset_id == assets[0].id
    assert evidence_rows[0].retrieval_chunk_uid is None
    assert evidence_rows[0].modality == "image"
    assert evidence_rows[0].evidence_kind == "standalone_image"
    assert assets[0].asset_kind == "standalone_image"
    assert assets[0].status == "ready"


@pytest.mark.asyncio
async def test_document_image_assets_projection_creates_real_asset_and_evidence(db_session, tmp_path):
    image_path = tmp_path / "page-003-image-001.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    await _seed_knowledge_file(
        db_session,
        file_id="file-doc",
        filename="doc.pdf",
        content_type="application/pdf",
        path="/tmp/doc.pdf",
        meta={
            "document_image_assets": [
                {
                    "storage_path": str(image_path),
                    "asset_kind": "figure",
                    "image_fingerprint": "sha256:" + "a" * 64,
                    "caption": "Box A placement diagram",
                    "surrounding_text": "The sample should be placed into Box A.",
                    "page": 3,
                    "anchor": {"page": 3, "block_id": "page-003-image-001"},
                    "metadata": {"width": 640, "height": 480, "origin_reference": "images/box-a.png"},
                }
            ]
        },
    )
    await _seed_active_text_chunk(db_session, row_id=11, file_id="file-doc", text="Chunk one")

    result = await project_evidence_for_knowledge_file(
        knowledge_id="kb-1",
        file_id="file-doc",
        db=db_session,
        project_document_images=True,
    )

    asset_rows = (
        (await db_session.execute(select(KnowledgeEvidenceAsset).order_by(KnowledgeEvidenceAsset.id.asc())))
        .scalars()
        .all()
    )
    evidence_rows = (
        (await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))).scalars().all()
    )

    assert result.text_evidence_upserted == 1
    assert result.image_assets_upserted == 1
    assert result.image_evidence_upserted == 1
    assert result.document_image_placeholders == 0
    assert len(asset_rows) == 1
    assert len(evidence_rows) == 2
    image_evidence = next(row for row in evidence_rows if row.modality == "image")
    assert image_evidence.asset_id == asset_rows[0].id
    assert image_evidence.evidence_kind == "figure"
    assert image_evidence.page_index == 3
    assert image_evidence.content_text == (
        "doc.pdf | Box A placement diagram | Page: 3 | " "Context: The sample should be placed into Box A."
    )
    assert asset_rows[0].asset_kind == "figure"
    assert asset_rows[0].storage_uri == str(image_path)
    assert asset_rows[0].width == 640
    assert asset_rows[0].height == 480
    assert asset_rows[0].anchor_json == {"page": 3, "block_id": "page-003-image-001"}


@pytest.mark.asyncio
async def test_document_image_placeholder_does_not_fabricate_image_evidence_without_assets(db_session):
    await _seed_knowledge_file(
        db_session,
        file_id="file-doc",
        filename="doc.pdf",
        content_type="application/pdf",
        path="/tmp/doc.pdf",
    )
    await _seed_active_text_chunk(db_session, row_id=11, file_id="file-doc", text="Chunk one")

    result = await project_evidence_for_knowledge_file(
        knowledge_id="kb-1",
        file_id="file-doc",
        db=db_session,
        project_document_images=True,
    )

    asset_rows = (
        (await db_session.execute(select(KnowledgeEvidenceAsset).order_by(KnowledgeEvidenceAsset.id.asc())))
        .scalars()
        .all()
    )
    evidence_rows = (
        (await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))).scalars().all()
    )

    assert result.text_evidence_upserted == 1
    assert result.image_assets_upserted == 0
    assert result.image_evidence_upserted == 0
    assert result.document_image_placeholders == 1
    assert len(asset_rows) == 0
    assert len(evidence_rows) == 1
    assert evidence_rows[0].evidence_kind == "text_chunk"


@pytest.mark.asyncio
async def test_db_none_projector_path_uses_internal_session_scope(tmp_path, monkeypatch):
    db_path = tmp_path / "projector.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    _run_migration(sync_engine, "upgrade")
    with sync_engine.begin() as connection:
        Knowledge.__table__.create(connection, checkfirst=True)
        File.__table__.create(connection, checkfirst=True)
        RetrievalChunk.__table__.create(connection, checkfirst=True)
        KnowledgeEvidenceAsset.__table__.create(connection, checkfirst=True)
        KnowledgeEvidence.__table__.create(connection, checkfirst=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as shared_session:
        await _seed_knowledge_file(
            shared_session,
            file_id="file-img",
            filename="box-a.png",
            content_type="image/png",
            path="/tmp/box-a.png",
        )
        await _seed_active_text_chunk(shared_session, row_id=21, file_id="file-img", text="hello")

        @projector_mod.asynccontextmanager
        async def fake_get_async_db_context(db=None):
            yield shared_session

        monkeypatch.setattr(projector_mod, "get_async_db_context", fake_get_async_db_context)

        result = await project_evidence_from_job_payload(
            {"knowledge_id": "kb-1", "file_ids": ["file-img"], "project_document_images": True},
            db=None,
        )

        assert result.text_evidence_upserted == 1
        assert result.image_assets_upserted == 1
        assert result.image_evidence_upserted == 1
        evidence_rows = (
            (await shared_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc())))
            .scalars()
            .all()
        )
        assert len(evidence_rows) == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_evidence_job_branch_uses_existing_job_state_surface(monkeypatch):
    job_status_calls = []
    state_calls = []
    finalize_calls = []

    class FakeJobs:
        async def get_job_by_id(self, job_id):
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "index_kind": "project",
                    "collection_id": "kb-1",
                    "knowledge_id": "kb-1",
                    "collection_name": "kb-1",
                    "file_id": None,
                    "target_config_hash": "evidence-target-hash",
                    "payload": {
                        "workflow": "evidence_projection",
                        "knowledge_id": "kb-1",
                        "file_ids": ["file-doc"],
                        "project_document_images": True,
                    },
                },
            )()

        async def update_job_status(self, job_id, **kwargs):
            job_status_calls.append((job_id, kwargs))
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": kwargs["status"],
                    "result": kwargs.get("result"),
                    "model_dump": lambda self=None: {
                        "job_id": job_id,
                        "status": kwargs["status"],
                        "result": kwargs.get("result"),
                    },
                },
            )()

    class FakeStates:
        async def upsert_state(self, **kwargs):
            state_calls.append(kwargs)
            return type("State", (), {"state_id": "state-1"})()

    async def fake_project(job_payload, db=None, activate=True):
        assert job_payload["workflow"] == "evidence_projection"
        assert activate is False
        return EvidenceProjectionResult(
            scanned_chunks=1,
            text_evidence_upserted=1,
            image_assets_upserted=1,
            image_evidence_upserted=1,
            document_image_placeholders=1,
            failed=0,
            evidence_refs=["ke:kb-1:file-doc:text_chunk:0:abc"],
        )

    async def fake_write_embeddings(evidence_refs, db=None):
        assert evidence_refs == ["ke:kb-1:file-doc:text_chunk:0:abc"]
        return indexing_mod.EvidenceEmbeddingProjectionResult(
            written=1,
            evidence_refs=list(evidence_refs),
        )

    async def fake_finalize_projected_evidence(job_payload, evidence_refs, db=None):
        finalize_calls.append((job_payload["file_ids"], list(evidence_refs)))

    monkeypatch.setattr(indexing_mod, "RetrievalIndexJobs", FakeJobs())
    monkeypatch.setattr(indexing_mod, "RetrievalIndexStates", FakeStates())
    monkeypatch.setattr(indexing_mod, "project_evidence_from_job_payload", fake_project)
    monkeypatch.setattr(indexing_mod, "write_projected_evidence_embeddings", fake_write_embeddings)
    monkeypatch.setattr(
        indexing_mod,
        "finalize_projected_evidence_from_job_payload",
        fake_finalize_projected_evidence,
        raising=False,
    )

    response = await indexing_mod.run_retrieval_index_job("job-project")

    assert response["result"]["evidence"]["text_evidence_upserted"] == 1
    assert response["result"]["evidence"]["image_evidence_upserted"] == 1
    assert finalize_calls == [
        (
            ["file-doc"],
            ["ke:kb-1:file-doc:text_chunk:0:abc"],
        )
    ]
    assert job_status_calls[0] == ("job-project", {"status": "running"})
    assert job_status_calls[-1][0] == "job-project"
    assert job_status_calls[-1][1]["status"] == "succeeded"
    assert state_calls == [
        {
            "index_kind": "project",
            "status": "ready",
            "collection_id": "kb-1",
            "knowledge_id": "kb-1",
            "collection_name": "kb-1",
            "file_id": None,
            "target_config_hash": "evidence-target-hash",
            "active_chunk_count": 1,
            "indexed_chunk_count": 2,
            "last_job_id": "job-project",
            "error": None,
        }
    ]


@pytest.mark.asyncio
async def test_evidence_job_branch_fails_when_embeddings_are_skipped(monkeypatch):
    job_status_calls = []
    state_calls = []
    finalize_calls = []

    class FakeJobs:
        async def get_job_by_id(self, job_id):
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "index_kind": "project",
                    "collection_id": "kb-1",
                    "knowledge_id": "kb-1",
                    "collection_name": "kb-1",
                    "file_id": None,
                    "target_config_hash": "evidence-target-hash",
                    "payload": {
                        "workflow": "evidence_projection",
                        "knowledge_id": "kb-1",
                        "file_ids": ["file-doc"],
                    },
                },
            )()

        async def update_job_status(self, job_id, **kwargs):
            job_status_calls.append((job_id, kwargs))
            return type(
                "Job",
                (),
                {
                    "job_id": job_id,
                    "status": kwargs["status"],
                    "result": kwargs.get("result"),
                    "error": kwargs.get("error"),
                    "model_dump": lambda self=None: {
                        "job_id": job_id,
                        "status": kwargs["status"],
                        "result": kwargs.get("result"),
                        "error": kwargs.get("error"),
                    },
                },
            )()

    class FakeStates:
        async def upsert_state(self, **kwargs):
            state_calls.append(kwargs)
            return type("State", (), {"state_id": "state-1"})()

    async def fake_project(job_payload, db=None, activate=True):
        assert activate is False
        return EvidenceProjectionResult(
            scanned_chunks=1,
            text_evidence_upserted=1,
            failed=0,
            evidence_refs=["ke:kb-1:file-doc:text_chunk:0:abc"],
        )

    async def fake_write_embeddings(evidence_refs, db=None):
        return indexing_mod.EvidenceEmbeddingProjectionResult(
            skipped=len(evidence_refs),
        )

    async def fake_finalize_projected_evidence(job_payload, evidence_refs, db=None):
        finalize_calls.append((job_payload["file_ids"], list(evidence_refs)))

    monkeypatch.setattr(indexing_mod, "RetrievalIndexJobs", FakeJobs())
    monkeypatch.setattr(indexing_mod, "RetrievalIndexStates", FakeStates())
    monkeypatch.setattr(indexing_mod, "project_evidence_from_job_payload", fake_project)
    monkeypatch.setattr(indexing_mod, "write_projected_evidence_embeddings", fake_write_embeddings)
    monkeypatch.setattr(
        indexing_mod,
        "finalize_projected_evidence_from_job_payload",
        fake_finalize_projected_evidence,
        raising=False,
    )

    response = await indexing_mod.run_retrieval_index_job("job-project")

    assert response["job"]["status"] == "failed"
    assert response["job"]["error"] == "evidence embedding write skipped 1 projected evidence rows"
    assert finalize_calls == []
    assert job_status_calls[-1][1]["status"] == "failed"
    assert state_calls[-1]["status"] == "failed"
    assert state_calls[-1]["error"] == "evidence embedding write skipped 1 projected evidence rows"
