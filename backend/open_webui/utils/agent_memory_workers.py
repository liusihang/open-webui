from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Awaitable, Callable

from open_webui.internal.db import get_async_db_context
from open_webui.models.agent_memories import AgentMemoryConsolidationJob, AgentMemoryExtractionJob
from open_webui.utils.agent_memory_consolidation import run_agent_memory_consolidation_jobs_once
from open_webui.utils.agent_memory_extraction import (
    is_agent_memory_generation_enabled,
    run_agent_memory_extraction_jobs_once,
)
from sqlalchemy import func, or_, select

log = logging.getLogger(__name__)

DEFAULT_AGENT_MEMORY_WORKER_POLL_INTERVAL_SECONDS = 10.0

SleepFn = Callable[[float], Awaitable[None]]


def _config_value(config, name: str, default):
    value = getattr(config, name, default)
    return default if value is None else value


def _build_worker_request(app):
    return SimpleNamespace(app=app)


def _ready_filter(job_model, now: int):
    return or_(
        (job_model.status == "queued") & or_(job_model.retry_at.is_(None), job_model.retry_at <= now),
        (job_model.status == "retry") & or_(job_model.retry_at.is_(None), job_model.retry_at <= now),
        (job_model.status == "leased") & (job_model.lease_until <= now),
    )


async def _job_metrics(job_model, now: int, db=None) -> dict[str, int | None]:
    async with get_async_db_context(db) as session:
        status_rows = await session.execute(select(job_model.status, func.count()).group_by(job_model.status))
        counts = {status: int(count) for status, count in status_rows.all()}

        ready_result = await session.execute(
            select(func.count(), func.min(job_model.updated_at)).where(_ready_filter(job_model, now))
        )
        ready_count, oldest_ready_updated_at = ready_result.one()

    oldest_age = None
    if oldest_ready_updated_at is not None:
        oldest_age = max(0, int(now - oldest_ready_updated_at))

    return {
        "queued": counts.get("queued", 0),
        "retry": counts.get("retry", 0),
        "failed": counts.get("failed", 0),
        "leased": counts.get("leased", 0),
        "ready": int(ready_count or 0),
        "oldest_ready_age_seconds": oldest_age,
    }


async def get_agent_memory_job_metrics(now: int | None = None, db=None) -> dict[str, dict[str, int | None]]:
    import time

    resolved_now = int(now or time.time())
    return {
        "extraction": await _job_metrics(AgentMemoryExtractionJob, resolved_now, db=db),
        "consolidation": await _job_metrics(AgentMemoryConsolidationJob, resolved_now, db=db),
    }


async def run_agent_memory_worker_cycle(app, db=None) -> dict[str, int]:
    config = app.state.config
    if not is_agent_memory_generation_enabled(config):
        return {"extraction_completed": 0, "consolidation_completed": 0}

    request = _build_worker_request(app)
    extraction_completed = await run_agent_memory_extraction_jobs_once(
        request,
        limit=int(_config_value(config, "AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT", 5)),
        db=db,
    )
    consolidation_completed = await run_agent_memory_consolidation_jobs_once(
        request,
        limit=int(_config_value(config, "AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT", 5)),
        db=db,
    )
    return {
        "extraction_completed": int(extraction_completed or 0),
        "consolidation_completed": int(consolidation_completed or 0),
    }


async def agent_memory_worker_loop(app, *, sleep: SleepFn = asyncio.sleep) -> None:
    config = app.state.config
    poll_interval = float(
        _config_value(
            config,
            "AGENT_MEMORY_WORKER_POLL_INTERVAL_SECONDS",
            DEFAULT_AGENT_MEMORY_WORKER_POLL_INTERVAL_SECONDS,
        )
    )
    poll_interval = max(0.0, poll_interval)
    log.info("Agent Memory worker started (poll interval: %ss)", poll_interval)

    try:
        while True:
            try:
                await run_agent_memory_worker_cycle(app)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Agent Memory worker cycle failed")

            await sleep(poll_interval)
    except asyncio.CancelledError:
        log.info("Agent Memory worker stopped")
        raise


def start_agent_memory_worker_tasks(app) -> list[asyncio.Task]:
    existing_tasks = getattr(app.state, "agent_memory_worker_tasks", None)
    if existing_tasks:
        return list(existing_tasks)

    tasks = [
        asyncio.create_task(
            agent_memory_worker_loop(app),
            name="agent-memory-worker",
        )
    ]
    app.state.agent_memory_worker_tasks = tasks
    return tasks


async def stop_agent_memory_worker_tasks(app) -> None:
    tasks = list(getattr(app.state, "agent_memory_worker_tasks", []) or [])
    if not tasks:
        return

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    app.state.agent_memory_worker_tasks = []
