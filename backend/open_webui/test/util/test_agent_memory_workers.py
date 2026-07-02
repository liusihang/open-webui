import asyncio
import importlib
import os
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.agent_memories import (
    AgentMemoryConsolidationJob,
    AgentMemoryConsolidationJobs,
    AgentMemoryExtractionJob,
    AgentMemoryExtractionJobs,
)


def _app(**config_overrides):
    config = {
        "ENABLE_AGENT_MEMORY": True,
        "AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT": 3,
        "AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT": 2,
        "AGENT_MEMORY_LEASE_SECONDS": 30,
        "AGENT_MEMORY_WORKER_POLL_INTERVAL_SECONDS": 0,
    }
    config.update(config_overrides)
    return SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(**config)))


@pytest.mark.asyncio
async def test_internal_agent_memory_chat_completion_strips_tools_and_preserves_task_metadata():
    chat = importlib.import_module("open_webui.utils.chat")
    captured = {}

    async def fake_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        captured.update(
            {
                "request": request,
                "form_data": form_data,
                "user": user,
                "bypass_filter": bypass_filter,
                "bypass_system_prompt": bypass_system_prompt,
            }
        )
        return {"ok": True}

    app = SimpleNamespace(state=SimpleNamespace())
    request = SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            metadata={
                "task": "ordinary_chat",
                "tools": {"agent_memory_search": {}},
                "features": {"web_search": True},
            }
        ),
    )
    user = SimpleNamespace(id="user-1", role="user")
    response = await chat.generate_agent_memory_internal_chat_completion(
        request,
        form_data={
            "model": "gpt-test",
            "messages": [{"role": "user", "content": "Summarize"}],
            "stream": False,
            "tools": [{"type": "function", "function": {"name": "memory_update"}}],
            "tool_choice": "auto",
            "tool_ids": ["user-memory"],
            "features": {"web_search": True},
            "metadata": {
                "task": "agent_memory_extraction",
                "tools": {"agent_memory_search": {}},
                "features": {"web_search": True},
            },
        },
        user=user,
        completion_fn=fake_completion,
    )

    assert response == {"ok": True}
    payload = captured["form_data"]
    assert captured["request"] is not request
    assert captured["request"].app is app
    assert not hasattr(captured["request"].state, "metadata")
    assert payload["metadata"] == {"task": "agent_memory_extraction"}
    assert "tools" not in payload
    assert "tool_choice" not in payload
    assert "tool_ids" not in payload
    assert "features" not in payload
    assert captured["user"] is user


async def _session_factory(tmp_path):
    db_path = tmp_path / "agent-memory-workers.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    for table in [
        AgentMemoryExtractionJob.__table__,
        AgentMemoryConsolidationJob.__table__,
    ]:
        table.create(sync_engine, checkfirst=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_agent_memory_worker_loop_runs_extraction_and_consolidation_without_manual_api(monkeypatch):
    workers = importlib.import_module("open_webui.utils.agent_memory_workers")
    app = _app()
    calls = []

    async def fake_extraction(request, limit=None, db=None):
        calls.append(("extraction", request.app is app, limit))
        return 1

    async def fake_consolidation(request, limit=None, db=None):
        calls.append(("consolidation", request.app is app, limit))
        return 1

    sleep_calls = 0

    async def fake_sleep(_delay):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(workers, "run_agent_memory_extraction_jobs_once", fake_extraction)
    monkeypatch.setattr(workers, "run_agent_memory_consolidation_jobs_once", fake_consolidation)

    with pytest.raises(asyncio.CancelledError):
        await workers.agent_memory_worker_loop(app, sleep=fake_sleep)

    assert calls == [
        ("extraction", True, 3),
        ("consolidation", True, 2),
    ]


@pytest.mark.asyncio
async def test_worker_cycle_respects_generation_gate_even_when_jobs_exist(tmp_path, monkeypatch):
    workers = importlib.import_module("open_webui.utils.agent_memory_workers")
    engine, session_factory = await _session_factory(tmp_path)
    app = _app(ENABLE_AGENT_MEMORY=True, ENABLE_AGENT_MEMORY_GENERATION=False)
    calls = []

    async def fake_extraction(*args, **kwargs):
        calls.append(("extraction", args, kwargs))
        return 1

    async def fake_consolidation(*args, **kwargs):
        calls.append(("consolidation", args, kwargs))
        return 1

    monkeypatch.setattr(workers, "run_agent_memory_extraction_jobs_once", fake_extraction)
    monkeypatch.setattr(workers, "run_agent_memory_consolidation_jobs_once", fake_consolidation)

    async with session_factory() as session:
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1",
            "chat-ready",
            status="queued",
            lease_until=None,
            retry_at=None,
            retry_count=0,
            last_error=None,
            updated_at=100,
            db=session,
        )
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1",
            "global",
            "",
            status="queued",
            lease_until=None,
            retry_at=None,
            retry_count=0,
            last_error=None,
            input_hash=None,
            updated_at=100,
            db=session,
        )

        assert await workers.run_agent_memory_worker_cycle(app, db=session) == {
            "extraction_completed": 0,
            "consolidation_completed": 0,
        }
        assert calls == []
        assert (await AgentMemoryExtractionJobs.get_job("user-1", "chat-ready", db=session)).status == "queued"
        assert (await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)).status == "queued"

    await engine.dispose()


def test_lifespan_starts_and_stops_agent_memory_worker_tasks():
    main_path = os.path.join(os.path.dirname(__file__), "../../main.py")
    main_source = open(main_path).read()

    assert "start_agent_memory_worker_tasks(app)" in main_source
    assert "stop_agent_memory_worker_tasks(app)" in main_source


@pytest.mark.asyncio
async def test_agent_memory_job_metrics_report_depth_age_failures_and_retries(tmp_path):
    workers = importlib.import_module("open_webui.utils.agent_memory_workers")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1",
            "chat-queued",
            status="queued",
            lease_until=None,
            retry_at=None,
            retry_count=0,
            last_error=None,
            updated_at=100,
            db=session,
        )
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1",
            "chat-retry",
            status="retry",
            lease_until=None,
            retry_at=120,
            retry_count=2,
            last_error="rate limited",
            updated_at=110,
            db=session,
        )
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1",
            "chat-failed",
            status="failed",
            lease_until=None,
            retry_at=None,
            retry_count=3,
            last_error="bad response",
            updated_at=130,
            db=session,
        )
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1",
            "global",
            "",
            status="queued",
            lease_until=None,
            retry_at=None,
            retry_count=0,
            last_error=None,
            input_hash=None,
            updated_at=90,
            db=session,
        )

        metrics = await workers.get_agent_memory_job_metrics(now=150, db=session)

    assert metrics == {
        "extraction": {
            "queued": 1,
            "retry": 1,
            "failed": 1,
            "leased": 0,
            "ready": 2,
            "oldest_ready_age_seconds": 50,
        },
        "consolidation": {
            "queued": 1,
            "retry": 0,
            "failed": 0,
            "leased": 0,
            "ready": 1,
            "oldest_ready_age_seconds": 60,
        },
    }

    await engine.dispose()
