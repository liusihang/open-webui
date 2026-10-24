"""Only task-owned unpublished candidates and generated input spools are reaped."""

import time
from pathlib import Path

from sqlalchemy import delete, select, update

from open_webui.internal.db import get_async_db_context
from open_webui.models.evidence import KnowledgeEvidence as Evidence, KnowledgeEvidenceEmbedding as Mapping, stable_id
from open_webui.models.evidence import KnowledgeEvidenceAsset as Asset
from open_webui.models.ingestion_jobs import IngestionJob as Job
from open_webui.models.retrieval_chunks import RetrievalChunk as Chunk
from open_webui.models.retrieval_indexes import RetrievalIndexState as State
from open_webui.retrieval.ingestion_queue import TERMINAL, cleanup_inputs


async def cleanup_job(identity, *, process_stopped=False):
    async with get_async_db_context() as db:
        job = await db.get(Job, identity)
        if not job or job.cleaned_at or job.status not in TERMINAL:
            return
        if not process_stopped and (job.finished_at or int(time.time())) > time.time() - 60:
            return  # Allow the former supervisor to kill its child first.
    if job.status != 'succeeded':
        collection = 'ingest-' + identity
        targets = (collection, collection + '-images')
        async with get_async_db_context() as db:
            active = await db.scalar(
                select(Evidence.id)
                .join(Mapping, Mapping.evidence_ref == Evidence.evidence_ref)
                .where(Mapping.vector_backend_collection.in_(targets), Evidence.is_active.is_(True))
                .limit(1)
            )
        if active:
            raise ValueError('refusing cleanup of a published vector collection')
        # A failed parse may never have created any vectors. Avoid initializing
        # the vector backend just to clean a rejected source.
        version = (job.payload or {}).get('candidate_version')
        if version:
            from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

            for target in targets:
                if await ASYNC_VECTOR_DB_CLIENT.has_collection(target):
                    await ASYNC_VECTOR_DB_CLIENT.delete_collection(target)
            async with get_async_db_context() as db:
                condition = (
                    (Evidence.file_id == job.file_id)
                    & (Evidence.file_version == version)
                    & Evidence.is_active.is_(False)
                )
                # Only raw candidate versions have a task-owned collection.
                # KB-only association failures reuse old vector identities and
                # are intentionally not deleted by this raw-candidate cleanup.
                refs = (
                    select(Evidence.evidence_ref)
                    .join(Chunk, Evidence.retrieval_chunk_uid == Chunk.chunk_uid)
                    .where(condition, Chunk.collection_name == collection)
                )
                await db.execute(delete(Mapping).where(Mapping.evidence_ref.in_(refs)))
                await db.execute(delete(Evidence).where(Evidence.evidence_ref.in_(refs)))
                await db.execute(
                    delete(Chunk).where(
                        Chunk.file_id == job.file_id,
                        Chunk.file_version == version,
                        Chunk.collection_name == collection,
                        Chunk.is_active.is_(False),
                    )
                )
                await db.execute(
                    update(State)
                    .where(State.state_id == stable_id('candidate', file_id=job.file_id, owner=identity))
                    .values(status='failed', updated_at=int(time.time()))
                )
                await db.commit()
            value = (job.payload or {}).get('asset_root')
            if value:
                import hashlib
                import shutil
                from open_webui.config import Config
                from open_webui.retrieval.integration import asset_root

                root = Path(value)
                allowed = asset_root(await Config.get_many('rag.document_image_asset_root'))
                source = hashlib.sha256(job.file_id.encode()).hexdigest()[:32]
                if (
                    root.name != identity
                    or root.parent.name != str(version)
                    or root.parent.parent.name != source
                    or root.is_symlink()
                    or not root.resolve().is_relative_to(allowed)
                ):
                    raise ValueError('refusing cleanup outside task-owned extraction directory')
                async with get_async_db_context() as db:
                    owned = select(Asset.id).where(
                        Asset.file_id == job.file_id,
                        Asset.file_version == version,
                        Asset.storage_uri.startswith(str(root) + '/', autoescape=True),
                    )
                    active = await db.scalar(
                        select(Evidence.id).where(Evidence.asset_id.in_(owned), Evidence.is_active.is_(True)).limit(1)
                    )
                    if active:
                        raise ValueError('refusing cleanup of published image assets')
                    refs = select(Evidence.evidence_ref).where(Evidence.asset_id.in_(owned))
                    await db.execute(delete(Mapping).where(Mapping.evidence_ref.in_(refs)))
                    await db.execute(delete(Evidence).where(Evidence.asset_id.in_(owned)))
                    await db.execute(delete(Asset).where(Asset.id.in_(owned)))
                    await db.commit()
                if root.exists():
                    shutil.rmtree(root)
    await cleanup_inputs(identity)
    async with get_async_db_context() as db:
        await db.execute(
            update(Job).where(Job.job_id == identity, Job.status.in_(TERMINAL)).values(cleaned_at=int(time.time()))
        )
        await db.commit()


async def cleanup_finished_jobs():
    async with get_async_db_context() as db:
        identity = await db.scalar(
            select(Job.job_id)
            .where(Job.status.in_(TERMINAL), Job.cleaned_at.is_(None), Job.finished_at < int(time.time()) - 60)
            .order_by(Job.finished_at)
            .limit(1)
        )
    if identity:
        await cleanup_job(identity)
