import os
import tempfile

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from open_webui.models.evidence import KnowledgeEvidence, KnowledgeEvidenceAsset
from open_webui.models.files import File
from open_webui.models.knowledge import Knowledge
from open_webui.models.retrieval_chunks import RetrievalChunk
from open_webui.retrieval.evidence_projector import (
    EvidenceProjectionResult,
    backfill_text_evidence_from_active_chunks,
    project_evidence_for_knowledge_file,
)
import open_webui.retrieval.indexing as indexing_mod


@pytest_asyncio.fixture
async def db_session():
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.name}")
    async with engine.begin() as connection:
        await connection.run_sync(Knowledge.__table__.create)
        await connection.run_sync(File.__table__.create)
        await connection.run_sync(RetrievalChunk.__table__.create)
        await connection.run_sync(KnowledgeEvidenceAsset.__table__.create)
        await connection.run_sync(KnowledgeEvidence.__table__.create)

    session_factory = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()
    os.unlink(db_file.name)


async def _seed_knowledge_file(session: AsyncSession, *, file_id: str, filename: str, content_type: str, path: str):
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
            data={"status": "completed"},
            meta={"content_type": content_type, "name": filename},
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
        await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))
    ).scalars().all()

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
        await db_session.execute(select(KnowledgeEvidenceAsset).order_by(KnowledgeEvidenceAsset.id.asc()))
    ).scalars().all()
    evidence_rows = (
        await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))
    ).scalars().all()

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
async def test_document_image_placeholder_does_not_fabricate_image_evidence(db_session):
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
        await db_session.execute(select(KnowledgeEvidenceAsset).order_by(KnowledgeEvidenceAsset.id.asc()))
    ).scalars().all()
    evidence_rows = (
        await db_session.execute(select(KnowledgeEvidence).order_by(KnowledgeEvidence.id.asc()))
    ).scalars().all()

    assert result.text_evidence_upserted == 1
    assert result.image_assets_upserted == 0
    assert result.image_evidence_upserted == 0
    assert result.document_image_placeholders == 1
    assert len(asset_rows) == 0
    assert len(evidence_rows) == 1
    assert evidence_rows[0].evidence_kind == "text_chunk"


@pytest.mark.asyncio
async def test_evidence_job_branch_uses_existing_job_state_surface(monkeypatch):
    job_status_calls = []
    state_calls = []

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
                    "model_dump": lambda self=None: {"job_id": job_id, "status": kwargs["status"], "result": kwargs.get("result")},
                },
            )()

    class FakeStates:
        async def upsert_state(self, **kwargs):
            state_calls.append(kwargs)
            return type("State", (), {"state_id": "state-1"})()

    async def fake_project(job_payload, db=None):
        assert job_payload["workflow"] == "evidence_projection"
        return EvidenceProjectionResult(
            scanned_chunks=1,
            text_evidence_upserted=1,
            image_assets_upserted=1,
            image_evidence_upserted=1,
            document_image_placeholders=1,
            failed=0,
            evidence_refs=["ke:kb-1:file-doc:text_chunk:0:abc"],
        )

    monkeypatch.setattr(indexing_mod, "RetrievalIndexJobs", FakeJobs())
    monkeypatch.setattr(indexing_mod, "RetrievalIndexStates", FakeStates())
    monkeypatch.setattr(indexing_mod, "project_evidence_from_job_payload", fake_project)

    response = await indexing_mod.run_retrieval_index_job("job-project")

    assert response["result"]["evidence"]["text_evidence_upserted"] == 1
    assert response["result"]["evidence"]["image_evidence_upserted"] == 1
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
