"""A fixed serial worker supervises a fresh child per job inside its cgroup.

Run two containers with INDEXING_WORKER_SLOT=0 and =1. Never mount Docker's
socket. A child OOM/exit cannot be reported as success or retried implicitly.
"""

import asyncio
import logging
import os
import signal
import sys
import time

log = logging.getLogger(__name__)


def mark_ready():
    from pathlib import Path

    root = Path(os.getenv('INDEXING_TEMP_DIR', '/tmp/indexing'))
    root.mkdir(parents=True, exist_ok=True)
    (root / 'worker-ready').touch()


def cleanup_job_temp(job_id):
    """Reap this child's bounded scratch even after SIGKILL/OOM."""
    import shutil
    import uuid
    from pathlib import Path

    if str(uuid.UUID(job_id)) != job_id:
        raise ValueError('invalid indexing job identity')
    root = Path(os.getenv('INDEXING_TEMP_DIR', '/tmp/indexing')).resolve()
    for path in root.glob('job-' + job_id + '-*'):
        if not path.is_symlink() and path.is_dir() and path.resolve().parent == root:
            shutil.rmtree(path)


async def stop_child(process):
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 5)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def supervise(job_id, token):
    from open_webui.retrieval.ingestion_queue import fail_job, heartbeat

    env = {**os.environ, 'ENABLE_DB_MIGRATIONS': 'false'}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        '-m',
        'open_webui.retrieval.ingestion_worker',
        'execute',
        job_id,
        token,
        env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + int(os.getenv('INDEXING_JOB_TIMEOUT', '1200'))
    try:
        while process.returncode is None:
            if time.monotonic() >= deadline:
                await stop_child(process)
                await fail_job(job_id, 'Indexing execution time budget exceeded; no automatic retry', token=token)
                return
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                if not await heartbeat(job_id, token):
                    await stop_child(process)
                    return
                mark_ready()
        # Publication itself sets succeeded in the same DB transaction. A zero
        # exit without that receipt is a failure, not evidence of completion.
        await fail_job(
            job_id, f'Indexing child exited ({process.returncode}) before publication; no automatic retry', token=token
        )
    except BaseException:
        await stop_child(process)
        raise
    finally:
        # The child has stopped before its private scratch is removed. Normal
        # TemporaryDirectory cleanup cannot run after a cgroup OOM/SIGKILL.
        await stop_child(process)
        cleanup_job_temp(job_id)


async def run():
    from open_webui.retrieval.ingestion_queue import claim, expire_jobs
    from open_webui.retrieval.ingestion_cleanup import cleanup_job, cleanup_finished_jobs

    slot = int(os.environ['INDEXING_WORKER_SLOT'])
    if slot not in (0, 1):
        raise ValueError('exactly two indexing slots are supported: 0 and 1')
    while True:
        try:
            await expire_jobs()
            receipt = await claim(slot)
            mark_ready()
            if receipt:
                await supervise(*receipt)
                await cleanup_job(receipt[0], process_stopped=True)
            else:
                await cleanup_finished_jobs()
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error('Indexing worker unavailable: %s', type(exc).__name__)
            await asyncio.sleep(5)


async def child(job_id, token):
    from open_webui.retrieval.admission import AdmissionError
    from open_webui.retrieval.ingestion import execute
    from open_webui.retrieval.ingestion_queue import fail_job

    try:
        await execute(job_id, token)
    except Exception as exc:
        # Do not persist remote response bodies, credentials or document contents.
        reason = (
            str(exc)
            if isinstance(exc, AdmissionError)
            else ('Indexing failed: ' + type(exc).__name__ + '; previous published index retained')
        )
        await fail_job(job_id, reason, token=token, status='rejected' if isinstance(exc, AdmissionError) else 'failed')
        log.error('Indexing task %s failed: %s', job_id, type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:2] == ['execute']:
        asyncio.run(child(*sys.argv[2:4]))
    else:
        asyncio.run(run())
