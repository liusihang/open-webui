"""Web submits descriptors, two serial containers claim them; no implicit retries."""

import asyncio
import time
import uuid

from open_webui.internal.db import get_async_db_context
from open_webui.models.files import File
from open_webui.models.ingestion_jobs import IngestionJob as Job
from open_webui.models.retrieval_chunks import canonical_hash
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError

LEASE_SECONDS = 45
QUEUE_SECONDS = 7200
TERMINAL = frozenset(('succeeded', 'failed', 'cancelled', 'rejected'))


async def patch_file_status(db, file_id, values, *, job_id=None, job_field='ingestion_job_id'):
    """Merge small status fields on the DB side, never fetch a legacy huge body."""
    import json
    from sqlalchemy import cast, func, literal
    from sqlalchemy.dialects.postgresql import JSONB

    query = update(File).where(File.id == file_id)
    if job_id:
        query = query.where(File.data[job_field].as_string() == job_id)
    if db.bind.dialect.name == 'postgresql':
        original = func.coalesce(cast(File.data, JSONB), cast(literal('{}'), JSONB))
        data = original.op('||')(cast(literal(json.dumps(values)), JSONB))
        if 'retrieval_status' in values:
            nested = func.coalesce(original.op('->')('retrieval_status'), cast(literal('{}'), JSONB)).op('||')(
                cast(literal(json.dumps(values['retrieval_status'])), JSONB)
            )
            data = data.op('||')(func.jsonb_build_object('retrieval_status', nested))
    else:
        data = func.json_patch(func.coalesce(File.data, '{}'), json.dumps(values))
    await db.execute(query.values(data=data).execution_options(synchronize_session=False))


class DuplicateIngestion(ValueError):
    def __init__(self, job_id, file_id=None):
        self.job_id = job_id
        self.file_id = file_id
        super().__init__('Identical content already being indexed in this permission scope; task ' + job_id)


def job_key(*, file_id, user_id, knowledge_id, source_hash, config_hash, payload):
    identity = dict(
        user=user_id,
        knowledge=knowledge_id,
        source=source_hash,
        config=config_hash,
        collection=payload.get('logical_collection'),
        retry_images=payload.get('retry_images', False),
    )
    if payload.get('retry_images'):
        identity.update(image_file=file_id, source_version=payload.get('source_version'))
    return canonical_hash(identity)


async def enqueue(*, file_id, user_id, knowledge_id, source_hash, config_hash, payload):
    key = job_key(
        file_id=file_id,
        user_id=user_id,
        knowledge_id=knowledge_id,
        source_hash=source_hash,
        config_hash=config_hash,
        payload=payload,
    )
    created, identity = time.time_ns() // 1000, str(uuid.uuid4())
    async with get_async_db_context() as db:
        try:
            import os
            from open_webui.retrieval.admission import AdmissionError

            if db.bind.dialect.name == 'postgresql':
                from sqlalchemy import text

                await db.execute(text('SELECT pg_advisory_xact_lock(170124, 1)'))
            duplicate = await db.scalar(select(Job).where(Job.active_key == key))
            if duplicate:
                raise DuplicateIngestion(duplicate.job_id, duplicate.file_id)
            queued = await db.scalar(select(func.count()).select_from(Job).where(Job.status == 'queued'))
            own = await db.scalar(
                select(func.count()).select_from(Job).where(Job.status == 'queued', Job.user_id == user_id)
            )
            if queued >= int(os.getenv('INDEXING_MAX_QUEUED', '200')) or own >= int(
                os.getenv('INDEXING_MAX_QUEUED_PER_USER', '20')
            ):
                raise AdmissionError(
                    'Indexing queue budget reached; original retained. Wait for existing tasks to finish.'
                )
            job = Job(
                job_id=identity,
                file_id=file_id,
                user_id=user_id,
                knowledge_id=knowledge_id,
                source_hash=source_hash,
                config_hash=config_hash,
                active_key=key,
                active_file=file_id,
                payload=payload,
                status='queued',
                created_at=created,
            )
            db.add(job)
            await db.flush()
            values = (
                {'image_ingestion_job_id': identity, 'retrieval_status': {'images': 'pending'}}
                if payload.get('retry_images')
                else {'status': 'pending', 'error': None, 'ingestion_job_id': identity}
            )
            await patch_file_status(db, file_id, values)
            await db.commit()
            return identity
        except IntegrityError:
            await db.rollback()
            duplicate = await db.scalar(select(Job).where(Job.active_key == key))
            existing = duplicate.job_id if duplicate else None
            if not existing:
                existing = await db.scalar(select(Job.job_id).where(Job.active_file == file_id))
                if existing:
                    raise ValueError('File already has an indexing task; wait for task ' + existing) from None
                raise
            raise DuplicateIngestion(existing, duplicate.file_id) from None


async def fail_job(job_id, reason, *, token=None, status='failed'):
    async with get_async_db_context() as db:
        job = (await db.execute(select(Job).where(Job.job_id == job_id).with_for_update())).scalar_one_or_none()
        if not job or job.status in TERMINAL or (token and job.lease_token != token):
            return False
        job.status, job.error, job.finished_at = status, reason[:500], int(time.time())
        job.active_key = job.active_file = job.worker_slot = job.lease_until = None
        await _mark_failure(db, job)
        await db.commit()
        return True


async def _mark_failure(db, job):
    if (job.payload or {}).get('retry_images'):
        message = job.error
        if message and message.startswith('Indexing execution time budget exceeded'):
            message = '图片处理任务超过执行时间上限，已停止；正文和原文件不受影响，可稍后重试图片。'
        elif message and message.startswith('Indexing child exited'):
            message = '图片处理进程异常退出或被终止，未自动重试；正文和原文件不受影响。'
        elif message and message.startswith('Indexing interrupted or queue wait expired'):
            message = '图片处理任务中断或排队超时，未自动重试；正文和原文件不受影响。'
        await patch_file_status(
            db,
            job.file_id,
            {'retrieval_status': {'images': 'failed', 'image_error': message, 'image_retryable': True}},
            job_id=job.job_id,
            job_field='image_ingestion_job_id',
        )
    else:
        await patch_file_status(db, job.file_id, {'status': 'failed', 'error': job.error}, job_id=job.job_id)


async def expire_jobs(*, job_id=None):
    now = int(time.time())
    async with get_async_db_context() as db:
        ids = list(
            await db.scalars(
                select(Job.job_id)
                .where(
                    ((Job.status == 'running') & (Job.lease_until < now))
                    | ((Job.status == 'queued') & (Job.created_at < (now - QUEUE_SECONDS) * 1_000_000))
                )
                .where(Job.job_id == job_id if job_id else True)
                .limit(100)
            )
        )
    changed = 0
    for identity in ids:
        # Recheck under lock: heartbeat/publication may have won since selection.
        async with get_async_db_context() as db:
            job = (await db.execute(select(Job).where(Job.job_id == identity).with_for_update())).scalar_one_or_none()
            expired = job and (
                (job.status == 'running' and (job.lease_until or 0) < now)
                or (job.status == 'queued' and job.created_at < (now - QUEUE_SECONDS) * 1_000_000)
            )
            if not expired:
                continue
            job.status, job.error, job.finished_at = (
                'failed',
                'Indexing interrupted or queue wait expired; no automatic retry',
                now,
            )
            job.active_key = job.active_file = job.worker_slot = job.lease_until = None
            await _mark_failure(db, job)
            await db.commit()
            changed += 1
        await cleanup_inputs(identity)
    return changed


async def cleanup_inputs(identity):
    """Remove only generated edit/web spool inputs after a terminal receipt.

    Uploaded originals and parser image assets are never cleanup targets here.
    """
    from pathlib import Path

    async with get_async_db_context() as db:
        job = await db.get(Job, identity)
        if not job or job.status not in TERMINAL:
            return
        payload = job.payload or {}
    if not any(payload.get(key) for key in ('content_path', 'documents_path')):
        return
    from open_webui.config import UPLOAD_DIR

    root = (Path(UPLOAD_DIR) / '.indexing-inputs').resolve()
    for key in ('content_path', 'documents_path'):
        value = payload.get(key)
        if not value:
            continue
        path = Path(value)
        if path.is_symlink() or path.parent.resolve() != root:
            raise ValueError('refusing indexing-input cleanup outside private spool directory')
        path.unlink(missing_ok=True)


async def claim(slot):
    if slot not in (0, 1):
        raise ValueError('only indexing slots 0 and 1 are allowed')
    now = int(time.time())
    # The read-only budget directory must be on the actual database filesystem,
    # not the container overlay. Reserve both fixed worker slots conservatively.
    import os
    import shutil

    budget_path = os.getenv('INDEXING_DB_STORAGE_PATH')
    if budget_path:
        available = shutil.disk_usage(budget_path).free
        reserve = int(os.getenv('INDEXING_STORAGE_RESERVE_BYTES', str(2 * 1024**3)))
        minimum = int(os.getenv('INDEXING_STORAGE_MIN_FREE_BYTES', str(8 * 1024**3)))
        if available - reserve < minimum:
            return None
    async with get_async_db_context() as db:
        busy_users = select(Job.user_id).where(Job.status == 'running')
        busy_files = select(Job.file_id).where(Job.status == 'running')
        query = (
            select(Job)
            .where(Job.status == 'queued', Job.user_id.not_in(busy_users), Job.file_id.not_in(busy_files))
            .order_by(Job.created_at, Job.job_id)
        )
        # A short transaction-wide PG lock serializes the two claimers. Without
        # it, SKIP LOCKED alone permits two jobs for the same user to start.
        if db.bind.dialect.name == 'postgresql':
            from sqlalchemy import text

            await db.execute(text('SELECT pg_advisory_xact_lock(170124, 2)'))
        if await db.scalar(select(Job.job_id).where(Job.worker_slot == slot)):
            return None
        job = (await db.execute(query.with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
        if not job:
            return None
        job.status, job.started_at = 'running', now
        job.worker_slot, job.lease_token = slot, str(uuid.uuid4())
        job.lease_until = now + LEASE_SECONDS
        result = (job.job_id, job.lease_token)
        await db.commit()
        return result


async def heartbeat(job_id, token):
    now = int(time.time())
    async with get_async_db_context() as db:
        result = await db.execute(
            update(Job)
            .where(Job.job_id == job_id, Job.status == 'running', Job.lease_token == token, Job.lease_until >= now)
            .values(lease_until=now + LEASE_SECONDS)
        )
        await db.commit()
        return bool(result.rowcount)


async def lease_is_current(job_id, token):
    async with get_async_db_context() as db:
        return bool(
            await db.scalar(
                select(Job.job_id).where(
                    Job.job_id == job_id,
                    Job.lease_token == token,
                    Job.status == 'running',
                    Job.lease_until >= int(time.time()),
                )
            )
        )


async def wait_for_job(identity, *, timeout=QUEUE_SECONDS + 1800):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with get_async_db_context() as db:
            job = await db.get(Job, identity)
            if not job:
                raise ValueError('indexing task not found')
            if job.status == 'succeeded':
                return job.result
            if job.status in TERMINAL:
                raise ValueError(job.error or 'indexing did not complete')
        await asyncio.sleep(2)
    raise TimeoutError('Indexing is still queued/running; check the existing task before retrying')
