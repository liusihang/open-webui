import importlib
import json
import os
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from sqlalchemy import event
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.agent_memories import (
    AgentMemoryConsolidationJob,
    AgentMemoryConsolidationJobs,
    AgentMemoryExtractionCache,
    AgentMemoryExtractionCaches,
    AgentMemoryExtractionJob,
    AgentMemoryExtractionJobs,
)
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat
from open_webui.models.folders import Folder
from open_webui.models.groups import Group, GroupMember


def _config(**overrides):
    values = {
        "ENABLE_AGENT_MEMORY": True,
        "AGENT_MEMORY_EXTRACTION_MODEL": "",
        "AGENT_MEMORY_IDLE_THRESHOLD_SECONDS": 60,
        "AGENT_MEMORY_LEASE_SECONDS": 30,
        "AGENT_MEMORY_RETRY_BACKOFF_SECONDS": 10,
        "AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT": 5,
        "TASK_MODEL": "",
        "TASK_MODEL_EXTERNAL": "",
        "DEFAULT_MODELS": "gpt-test",
        "USER_PERMISSIONS": {"features": {"agent_memory": True}},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _session_factory(tmp_path):
    db_path = tmp_path / "agent-memory-extraction.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    for table in [
        Chat.__table__,
        ChatMessage.__table__,
        Folder.__table__,
        Group.__table__,
        GroupMember.__table__,
        AgentMemoryExtractionCache.__table__,
        AgentMemoryExtractionJob.__table__,
        AgentMemoryConsolidationJob.__table__,
    ]:
        table.create(sync_engine, checkfirst=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _chat(
    chat_id,
    user_id="user-1",
    *,
    updated_at=1000,
    meta=None,
    folder_id=None,
    chat=None,
    share_id=None,
):
    return Chat(
        id=chat_id,
        user_id=user_id,
        title=chat_id,
        chat=chat or {"title": chat_id, "history": {"messages": {}}},
        created_at=updated_at - 100,
        updated_at=updated_at,
        share_id=share_id,
        archived=False,
        pinned=False,
        meta=meta or {},
        folder_id=folder_id,
    )


def _message(
    chat_id,
    message_id,
    role,
    content,
    *,
    user_id="user-1",
    created_at=1000,
    updated_at=1000,
    done=True,
    error=None,
    model_id=None,
    output=None,
):
    return ChatMessage(
        id=f"{chat_id}-{message_id}",
        chat_id=chat_id,
        user_id=user_id,
        role=role,
        content=content,
        output=output,
        model_id=model_id,
        done=done,
        error=error,
        created_at=created_at,
        updated_at=updated_at,
    )


async def _insert_exchange(session, chat_id, *, user_id="user-1", assistant_done=True, assistant_error=None):
    session.add(_chat(chat_id, user_id=user_id, updated_at=1000))
    session.add_all(
        [
            _message(chat_id, "u1", "user", "remember my preferred build command", user_id=user_id, created_at=1000),
            _message(
                chat_id,
                "a1",
                "assistant",
                "Use npm run check before frontend commits.",
                user_id=user_id,
                created_at=1001,
                updated_at=1001,
                done=assistant_done,
                error=assistant_error,
                model_id="gpt-test",
            ),
        ]
    )


@pytest.mark.asyncio
async def test_enqueue_excludes_ineligible_chat_shapes_and_reenqueues_stale_sources(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "good")
        await _insert_exchange(session, "local:temp")
        await _insert_exchange(session, "channel:room")
        await _insert_exchange(session, "shared-copy")
        await _insert_exchange(session, "unfinished", assistant_done=False)
        await _insert_exchange(session, "error-final", assistant_error={"detail": "boom"})

        session.add(_chat("empty", updated_at=1000))
        session.add(_chat("tool-only", updated_at=1000))
        session.add(_message("tool-only", "t1", "tool", "transient tool output", created_at=1000))
        session.add(_chat("system-only", updated_at=1000))
        session.add(_message("system-only", "s1", "system", "hidden prompt", created_at=1000))
        copied = await session.get(Chat, "shared-copy")
        copied.chat = {**copied.chat, "originalChatId": "source-chat", "branchPointMessageId": "a1"}
        await session.commit()

        await AgentMemoryExtractionCaches.upsert_cache(
            user_id="user-1",
            chat_id="good",
            source_updated_at=1000,
            raw_memory="old",
            rollout_summary="old summary",
            rollout_slug=None,
            generated_at=1001,
            status="succeeded",
            db=session,
        )

        enqueued = {}
        for chat_id in [
            "good",
            "local:temp",
            "channel:room",
            "shared-copy",
            "empty",
            "tool-only",
            "system-only",
            "unfinished",
            "error-final",
        ]:
            enqueued[chat_id] = await extraction.enqueue_chat_extraction_if_needed(
                chat_id,
                config=_config(),
                now=1200,
                db=session,
            )

        assert enqueued == {
            "good": True,
            "local:temp": False,
            "channel:room": False,
            "shared-copy": False,
            "empty": False,
            "tool-only": False,
            "system-only": False,
            "unfinished": False,
            "error-final": False,
        }
        assert (await AgentMemoryExtractionJobs.get_job("user-1", "good", db=session)).status == "queued"
        assert (await AgentMemoryExtractionCaches.get_cache("user-1", "good", db=session)).status == "stale"

    await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_respects_global_permission_folder_opt_out_and_idle_window(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(Folder(id="folder-1", user_id="user-1", name="Project", meta={}, created_at=1, updated_at=1))
        session.add(
            Folder(
                id="folder-disabled",
                user_id="user-1",
                name="Private",
                meta={"agent_memory": {"disabled": True}},
                created_at=1,
                updated_at=1,
            )
        )
        await _insert_exchange(session, "enabled-folder")
        await _insert_exchange(session, "disabled-chat")
        await _insert_exchange(session, "disabled-folder")
        await _insert_exchange(session, "too-recent")

        (await session.get(Chat, "enabled-folder")).folder_id = "folder-1"
        (await session.get(Chat, "disabled-chat")).meta = {"agent_memory": {"disabled": True}}
        (await session.get(Chat, "disabled-folder")).folder_id = "folder-disabled"
        (await session.get(Chat, "too-recent")).updated_at = 1190
        await session.commit()

        assert await extraction.enqueue_chat_extraction_if_needed("enabled-folder", _config(), now=1200, db=session)
        assert not await extraction.enqueue_chat_extraction_if_needed("disabled-chat", _config(), now=1200, db=session)
        assert not await extraction.enqueue_chat_extraction_if_needed("disabled-folder", _config(), now=1200, db=session)
        assert not await extraction.enqueue_chat_extraction_if_needed("too-recent", _config(), now=1200, db=session)
        assert not await extraction.enqueue_chat_extraction_if_needed(
            "enabled-folder",
            _config(ENABLE_AGENT_MEMORY=False),
            now=1200,
            db=session,
        )
        assert not await extraction.enqueue_chat_extraction_if_needed(
            "enabled-folder",
            _config(USER_PERMISSIONS={"features": {"agent_memory": False}}),
            now=1200,
            db=session,
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_completion_enqueue_marks_recent_chat_for_future_idle_claim(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "recent-chat")
        (await session.get(Chat, "recent-chat")).updated_at = 1190
        await session.commit()

        assert await extraction.enqueue_chat_extraction_if_needed(
            "recent-chat",
            _config(),
            now=1200,
            db=session,
            require_idle=False,
        )

        job = await AgentMemoryExtractionJobs.get_job("user-1", "recent-chat", db=session)
        assert job.status == "queued"
        assert job.retry_at == 1250
        assert await extraction.claim_extraction_jobs(now=1249, limit=1, lease_seconds=30, db=session) == []
        assert [job.chat_id for job in await extraction.claim_extraction_jobs(now=1250, limit=1, lease_seconds=30, db=session)] == [
            "recent-chat"
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_completion_enqueue_refreshes_existing_future_idle_deadline(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "recent-chat")
        chat = await session.get(Chat, "recent-chat")
        chat.updated_at = 1190
        await session.commit()

        assert await extraction.enqueue_chat_extraction_if_needed(
            "recent-chat", _config(), now=1200, db=session, require_idle=False
        )
        assert (await AgentMemoryExtractionJobs.get_job("user-1", "recent-chat", db=session)).retry_at == 1250

        chat = await session.get(Chat, "recent-chat")
        chat.updated_at = 1230
        await session.commit()
        assert await extraction.enqueue_chat_extraction_if_needed(
            "recent-chat", _config(), now=1240, db=session, require_idle=False
        )

        job = await AgentMemoryExtractionJobs.get_job("user-1", "recent-chat", db=session)
        assert job.status == "queued"
        assert job.retry_at == 1290
        assert await extraction.claim_extraction_jobs(now=1250, limit=1, lease_seconds=30, db=session) == []
        assert [job.chat_id for job in await extraction.claim_extraction_jobs(now=1290, limit=1, lease_seconds=30, db=session)] == [
            "recent-chat"
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_idle_chats_for_extraction_is_bounded(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        for chat_id in ["chat-1", "chat-2", "chat-3"]:
            await _insert_exchange(session, chat_id)
        await session.commit()

        enqueued = await extraction.enqueue_idle_chats_for_extraction(
            config=_config(),
            now=1200,
            limit=2,
            db=session,
        )

        assert enqueued == ["chat-1", "chat-2"]
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-2", db=session)
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-3", db=session) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_backlog_uses_startup_limit_and_is_wired_from_lifespan(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        for chat_id in ["chat-1", "chat-2"]:
            await _insert_exchange(session, chat_id)
        await session.commit()

        app = SimpleNamespace(
            state=SimpleNamespace(config=_config(AGENT_MEMORY_STARTUP_CLAIM_LIMIT=1))
        )
        enqueued = await extraction.enqueue_startup_agent_memory_backlog(app, now=1200, db=session)

        assert enqueued == ["chat-1"]
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-2", db=session) is None

    main_source = open(os.path.join(os.path.dirname(__file__), "../../main.py")).read()
    assert "enqueue_startup_agent_memory_backlog" in main_source

    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_jobs_is_bounded_and_reclaims_expired_leases(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        for chat_id, status, lease_until, retry_at in [
            ("queued-1", "queued", None, None),
            ("queued-2", "queued", None, None),
            ("expired", "leased", 90, None),
            ("leased", "leased", 150, None),
            ("future-retry", "retry", None, 150),
        ]:
            await AgentMemoryExtractionJobs.upsert_job(
                "user-1",
                chat_id,
                status=status,
                lease_until=lease_until,
                retry_at=retry_at,
                retry_count=0,
                last_error=None,
                updated_at=10,
                db=session,
            )

        claimed = await extraction.claim_extraction_jobs(now=100, limit=2, lease_seconds=30, db=session)

        assert [job.chat_id for job in claimed] == ["queued-1", "queued-2"]
        assert all(job.status == "leased" and job.lease_until == 130 for job in claimed)
        assert (await AgentMemoryExtractionJobs.get_job("user-1", "expired", db=session)).status == "leased"
        assert (await AgentMemoryExtractionJobs.get_job("user-1", "future-retry", db=session)).status == "retry"

        reclaimed = await extraction.claim_extraction_jobs(now=131, limit=10, lease_seconds=30, db=session)
        assert [job.chat_id for job in reclaimed] == ["queued-1", "queued-2", "expired"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_record_extraction_failure_retries_then_fails(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1",
            "chat-1",
            status="leased",
            lease_until=130,
            retry_at=None,
            retry_count=0,
            last_error=None,
            updated_at=100,
            db=session,
        )

        await extraction.record_extraction_failure(
            "user-1",
            "chat-1",
            error=RuntimeError("secret failure should be truncated " + "x" * 500),
            now=101,
            max_retries=2,
            retry_backoff_seconds=10,
            db=session,
        )
        first = await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        assert first.status == "retry"
        assert first.retry_count == 1
        assert first.retry_at == 111
        assert len(first.last_error) <= 240

        claimed = await extraction.claim_extraction_jobs(now=111, limit=1, lease_seconds=30, db=session)
        assert [job.chat_id for job in claimed] == ["chat-1"]
        await extraction.record_extraction_failure(
            "user-1",
            "chat-1",
            error=RuntimeError("second failure"),
            now=112,
            max_retries=2,
            retry_backoff_seconds=10,
            db=session,
        )
        second = await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        assert second.status == "failed"
        assert second.retry_count == 2
        assert second.retry_at is None
        assert second.lease_until is None

    await engine.dispose()


def test_parse_extraction_response_is_strict_json_with_exact_keys_and_types():
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")

    parsed = extraction.parse_extraction_response(
        '{"raw_memory":"- Prefers pytest","rollout_summary":"Tested memory extraction","rollout_slug":null}'
    )

    assert parsed == {
        "raw_memory": "- Prefers pytest",
        "rollout_summary": "Tested memory extraction",
        "rollout_slug": None,
    }
    for payload in [
        '{"raw_memory":"x","rollout_summary":"y"}',
        '{"raw_memory":"x","rollout_summary":"y","rollout_slug":null,"extra":true}',
        '{"raw_memory":["x"],"rollout_summary":"y","rollout_slug":null}',
        'not json',
        '[]',
        'null',
    ]:
        with pytest.raises(extraction.AgentMemoryExtractionContractError):
            extraction.parse_extraction_response(payload)


def test_sanitize_messages_removes_injected_context_large_payloads_and_redacts_secrets():
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")

    sanitized = extraction.sanitize_messages_for_extraction(
        [
            {"role": "system", "content": "system prompt must not leak"},
            {"role": "developer", "content": "developer instruction must not leak"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "My token is sk-testsecret and password=letmein."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 200}},
                ],
                "files": [{"name": "full-file.py", "content": "print('entire file')"}],
            },
            {
                "role": "assistant",
                "content": "<details>hidden RAG context</details>Remember the project uses pytest.",
            },
            {"role": "tool", "content": "tool output " + "B" * 5000},
        ],
        max_chars=600,
    )
    blob = json.dumps(sanitized)

    assert "system prompt" not in blob
    assert "developer instruction" not in blob
    assert "data:image" not in blob
    assert "full-file.py" not in blob
    assert "hidden RAG context" not in blob
    assert "sk-testsecret" not in blob
    assert "password=letmein" not in blob
    assert "[REDACTED]" in blob
    assert "Remember the project uses pytest" in blob
    assert len(blob) <= 900


def test_sanitize_messages_prefers_recent_messages_under_budget():
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    latest_user = "LATEST USER DECISION: prefer focused pytest."
    latest_assistant = "LATEST ASSISTANT DECISION: run targeted checks before commit."

    sanitized = extraction.sanitize_messages_for_extraction(
        [
            {"role": "user", "content": "EARLIEST CONTEXT " + "x" * 200},
            {"role": "assistant", "content": "Older assistant summary that can be dropped."},
            {"role": "user", "content": latest_user},
            {"role": "assistant", "content": latest_assistant},
        ],
        max_chars=len(latest_user) + len(latest_assistant),
    )
    blob = json.dumps(sanitized)

    assert sanitized == [
        {"role": "user", "content": latest_user},
        {"role": "assistant", "content": latest_assistant},
    ]
    assert "EARLIEST CONTEXT" not in blob
    assert "Older assistant summary" not in blob


def test_sanitize_messages_keeps_tail_of_recent_oversized_message():
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    latest_decision = "LATEST DECISION: run worker loop verification."

    sanitized = extraction.sanitize_messages_for_extraction(
        [
            {"role": "user", "content": "old decision can be dropped"},
            {
                "role": "assistant",
                "content": ("prefix " * 30) + latest_decision,
            },
        ],
        max_chars=len(latest_decision),
    )

    assert sanitized == [
        {
            "role": "assistant",
            "content": latest_decision,
        }
    ]


def test_sanitize_messages_does_not_preserve_raw_tool_payloads():
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")

    sanitized = extraction.sanitize_messages_for_extraction(
        [
            {
                "role": "tool",
                "content": (
                    "raw database dump should not leak "
                    "https://signed.example.test/object?X-Amz-Signature=abc "
                    "data:image/png;base64," + "A" * 200 + " "
                    "/tmp/openwebui-secret-file "
                    "api_key=supersecret"
                ),
            },
            {"role": "assistant", "content": "The useful durable outcome is pytest is required."},
        ],
        max_chars=1200,
    )
    blob = json.dumps(sanitized)

    assert "raw database dump" not in blob
    assert "signed.example" not in blob
    assert "data:image" not in blob
    assert "openwebui-secret-file" not in blob
    assert "supersecret" not in blob
    assert "pytest is required" in blob


@pytest.mark.asyncio
async def test_run_extraction_jobs_once_uses_sanitized_payload_and_completes_cache(tmp_path, monkeypatch):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)
    captured_payloads = []

    async def fake_generate_chat_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        captured_payloads.append(
            {
                "form_data": form_data,
                "user": user,
                "bypass_filter": bypass_filter,
                "bypass_system_prompt": bypass_system_prompt,
            }
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "raw_memory": "The user prefers pytest. Token sk-outputsecret.",
                                "rollout_summary": "Captured test command preference.",
                                "rollout_slug": "pytest-preference",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(extraction, "generate_chat_completion", fake_generate_chat_completion)

    async with session_factory() as session:
        session.add(_chat("chat-1", updated_at=1000))
        session.add_all(
            [
                _message("chat-1", "s1", "system", "system prompt must not leak", created_at=999),
                _message("chat-1", "u1", "user", "My token is sk-inputsecret. I prefer pytest.", created_at=1000),
                _message("chat-1", "a1", "assistant", "Use pytest for this repo.", created_at=1001),
            ]
        )
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "chat-1", "queued", None, None, 0, None, 100, db=session
        )

        request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={
                        "gpt-test": {"id": "gpt-test", "owned_by": "openai", "info": {"params": {"max_tokens": 4096}}},
                        "extractor": {"id": "extractor", "owned_by": "openai", "info": {"params": {"max_tokens": 4096}}},
                    },
                    config=_config(AGENT_MEMORY_EXTRACTION_MODEL="extractor", DEFAULT_MODELS="gpt-test"),
                )
            ),
        )

        processed = await extraction.run_agent_memory_extraction_jobs_once(request, now=1200, limit=1, db=session)

        assert processed == 1
        assert len(captured_payloads) == 1
        payload = captured_payloads[0]["form_data"]
        prompt = payload["messages"][0]["content"]
        assert payload["model"] == "extractor"
        assert payload["stream"] is False
        assert payload["metadata"]["task"] == "agent_memory_extraction"
        assert captured_payloads[0]["user"].role == "user"
        assert captured_payloads[0]["user"].role != "admin"
        assert captured_payloads[0]["user"].name == "Agent Memory Service"
        assert captured_payloads[0]["user"].is_service_account is True
        assert captured_payloads[0]["bypass_filter"] is False
        assert captured_payloads[0]["bypass_system_prompt"] is False
        assert "system prompt must not leak" not in prompt
        assert "sk-inputsecret" not in prompt
        assert "[REDACTED]" in prompt

        cache = await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session)
        assert cache.status == "succeeded"
        assert "sk-outputsecret" not in cache.raw_memory
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session) is None
        assert (await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)).status == "queued"

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_extraction_jobs_once_fails_loud_for_missing_explicit_model(tmp_path, monkeypatch):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)
    calls = []

    async def fake_generate_chat_completion(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(extraction, "generate_chat_completion", fake_generate_chat_completion)

    async with session_factory() as session:
        await _insert_exchange(session, "chat-1")
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "chat-1", "queued", None, None, 0, None, 100, db=session
        )
        request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={"gpt-test": {"id": "gpt-test", "owned_by": "openai", "info": {"params": {}}}},
                    config=_config(AGENT_MEMORY_EXTRACTION_MODEL="missing-model", DEFAULT_MODELS="gpt-test"),
                )
            ),
        )

        assert await extraction.run_agent_memory_extraction_jobs_once(request, now=1200, limit=1, db=session) == 0
        assert calls == []
        job = await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        assert job.status == "retry"
        assert "missing-model" in job.last_error

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_extraction_jobs_once_skips_disabled_or_opted_out_jobs(tmp_path, monkeypatch):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)
    calls = []

    async def fake_generate_chat_completion(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(extraction, "generate_chat_completion", fake_generate_chat_completion)

    async with session_factory() as session:
        await _insert_exchange(session, "chat-1")
        (await session.get(Chat, "chat-1")).meta = {"agent_memory": {"disabled": True}}
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "chat-1", "queued", None, None, 0, None, 100, db=session
        )
        request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={"gpt-test": {"id": "gpt-test", "owned_by": "openai", "info": {"params": {}}}},
                    config=_config(DEFAULT_MODELS="gpt-test"),
                )
            ),
        )

        assert await extraction.run_agent_memory_extraction_jobs_once(request, now=1200, limit=1, db=session) == 0
        assert calls == []
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session) is None

        (await session.get(Chat, "chat-1")).meta = {}
        await session.commit()
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "chat-1", "queued", None, None, 0, None, 100, db=session
        )
        request.app.state.config = _config(ENABLE_AGENT_MEMORY=False, DEFAULT_MODELS="gpt-test")
        assert await extraction.run_agent_memory_extraction_jobs_once(request, now=1200, limit=1, db=session) == 0
        assert calls == []
        assert (await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)).status == "queued"

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_extraction_jobs_once_skips_leftover_job_when_cache_is_fresh(tmp_path, monkeypatch):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)
    calls = []

    async def fake_generate_chat_completion(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(extraction, "generate_chat_completion", fake_generate_chat_completion)

    async with session_factory() as session:
        await _insert_exchange(session, "chat-1")
        await AgentMemoryExtractionCaches.upsert_cache(
            "user-1",
            "chat-1",
            source_updated_at=1001,
            raw_memory="fresh",
            rollout_summary="fresh summary",
            rollout_slug=None,
            generated_at=1002,
            status="succeeded",
            db=session,
        )
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "chat-1", "queued", None, None, 0, None, 100, db=session
        )
        request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={"gpt-test": {"id": "gpt-test", "owned_by": "openai", "info": {"params": {}}}},
                    config=_config(DEFAULT_MODELS="gpt-test"),
                )
            ),
        )

        assert await extraction.run_agent_memory_extraction_jobs_once(request, now=1200, limit=1, db=session) == 0
        assert calls == []
        assert await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_extraction_writes_redacted_cache_deletes_job_and_enqueues_scope(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(Folder(id="folder-1", user_id="user-1", name="Project", meta={}, created_at=1, updated_at=1))
        await _insert_exchange(session, "folder-chat")
        (await session.get(Chat, "folder-chat")).folder_id = "folder-1"
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "folder-chat", "leased", 130, None, 0, None, 100, db=session
        )
        await session.commit()

        await extraction.complete_extraction_job(
            "user-1",
            "folder-chat",
            source_updated_at=1001,
            output={
                "raw_memory": "User API key is sk-outputsecret; project uses pytest.",
                "rollout_summary": "Discussed pytest.",
                "rollout_slug": "pytest-memory",
            },
            now=140,
            db=session,
        )

        cache = await AgentMemoryExtractionCaches.get_cache("user-1", "folder-chat", db=session)
        assert cache.status == "succeeded"
        assert "sk-outputsecret" not in cache.raw_memory
        assert "[REDACTED]" in cache.raw_memory
        assert await AgentMemoryExtractionJobs.get_job("user-1", "folder-chat", db=session) is None
        consolidation = await AgentMemoryConsolidationJobs.get_job("user-1", "folder", "folder-1", db=session)
        assert consolidation.status == "queued"

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_extraction_sanitizes_transient_model_output_before_cache_write(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "chat-1")
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "chat-1", "leased", 130, None, 0, None, 100, db=session
        )

        await extraction.complete_extraction_job(
            "user-1",
            "chat-1",
            source_updated_at=1001,
            output={
                "raw_memory": (
                    "Use pytest. URL https://signed.example.test/object?sig=abc "
                    "image data:image/png;base64," + "A" * 200 + " "
                    "tmp /tmp/openwebui-cache-file token sk-outputsecret"
                ),
                "rollout_summary": "Summary with api_key=supersecret and /var/folders/tmpfile",
                "rollout_slug": "pytest-memory",
            },
            now=140,
            db=session,
        )

        cache = await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session)
        blob = json.dumps(cache.model_dump())
        assert "Use pytest" in cache.raw_memory
        assert "signed.example" not in blob
        assert "data:image" not in blob
        assert "openwebui-cache-file" not in blob
        assert "sk-outputsecret" not in blob
        assert "supersecret" not in blob
        assert "[REMOVED_URL]" in blob
        assert "[REDACTED]" in blob

    await engine.dispose()


@pytest.mark.asyncio
async def test_no_output_writes_succeeded_no_output_and_only_reconsolidates_previous_output(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "new-no-output")
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "new-no-output", "leased", 130, None, 0, None, 100, db=session
        )
        await extraction.complete_extraction_job(
            "user-1",
            "new-no-output",
            source_updated_at=1001,
            output={"raw_memory": "", "rollout_summary": "", "rollout_slug": None},
            now=140,
            db=session,
        )
        assert (await AgentMemoryExtractionCaches.get_cache("user-1", "new-no-output", db=session)).status == (
            "succeeded_no_output"
        )
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None

        await _insert_exchange(session, "old-output")
        await AgentMemoryExtractionCaches.upsert_cache(
            "user-1", "old-output", 900, "old memory", "old summary", None, 901, "succeeded", db=session
        )
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "old-output", "leased", 130, None, 0, None, 100, db=session
        )
        await extraction.complete_extraction_job(
            "user-1",
            "old-output",
            source_updated_at=1001,
            output={"raw_memory": "", "rollout_summary": "", "rollout_slug": None},
            now=140,
            db=session,
        )
        assert (await AgentMemoryExtractionCaches.get_cache("user-1", "old-output", db=session)).status == (
            "succeeded_no_output"
        )
        assert (await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)).status == "queued"

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_previous_output_reconsolidates_when_new_extraction_has_no_output(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "old-output")
        await AgentMemoryExtractionCaches.upsert_cache(
            "user-1", "old-output", 1001, "old memory", "old summary", None, 1002, "succeeded", db=session
        )
        chat = await session.get(Chat, "old-output")
        chat.updated_at = 1100
        await session.commit()

        assert await extraction.enqueue_chat_extraction_if_needed("old-output", _config(), now=1200, db=session)
        assert (await AgentMemoryExtractionCaches.get_cache("user-1", "old-output", db=session)).status == "stale"

        await extraction.complete_extraction_job(
            "user-1",
            "old-output",
            source_updated_at=1100,
            output={"raw_memory": "", "rollout_summary": "", "rollout_slug": None},
            now=1210,
            db=session,
        )

        assert (await AgentMemoryExtractionCaches.get_cache("user-1", "old-output", db=session)).status == (
            "succeeded_no_output"
        )
        assert (await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)).status == "queued"

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_jobs_are_terminal_for_normal_enqueue(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "chat-1")
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1",
            "chat-1",
            status="failed",
            lease_until=None,
            retry_at=None,
            retry_count=3,
            last_error="permanent failure",
            updated_at=100,
            db=session,
        )

        assert not await extraction.enqueue_chat_extraction_if_needed("chat-1", _config(), now=1200, db=session)
        job = await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        assert job.status == "failed"
        assert job.retry_count == 3
        assert job.last_error == "permanent failure"

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_jobs_do_not_consume_idle_backlog_limit(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "failed-chat")
        await _insert_exchange(session, "eligible-chat")
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1",
            "failed-chat",
            status="failed",
            lease_until=None,
            retry_at=None,
            retry_count=3,
            last_error="terminal",
            updated_at=100,
            db=session,
        )

        enqueued = await extraction.enqueue_idle_chats_for_extraction(_config(), now=1200, limit=1, db=session)

        assert enqueued == ["eligible-chat"]
        assert (await AgentMemoryExtractionJobs.get_job("user-1", "failed-chat", db=session)).status == "failed"
        assert (await AgentMemoryExtractionJobs.get_job("user-1", "eligible-chat", db=session)).status == "queued"

    await engine.dispose()


@pytest.mark.asyncio
async def test_record_extraction_failure_does_not_overwrite_newer_or_unleased_jobs(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1",
            "newer-lease",
            status="leased",
            lease_until=200,
            retry_at=None,
            retry_count=0,
            last_error=None,
            updated_at=100,
            db=session,
        )
        assert await extraction.record_extraction_failure(
            "user-1",
            "newer-lease",
            error=RuntimeError("old worker failed late"),
            now=150,
            expected_lease_until=130,
            db=session,
        ) is None
        newer = await AgentMemoryExtractionJobs.get_job("user-1", "newer-lease", db=session)
        assert newer.status == "leased"
        assert newer.lease_until == 200
        assert newer.retry_count == 0
        assert newer.last_error is None

        await AgentMemoryExtractionJobs.upsert_job(
            "user-1",
            "queued-again",
            status="queued",
            lease_until=None,
            retry_at=None,
            retry_count=0,
            last_error=None,
            updated_at=100,
            db=session,
        )
        assert await extraction.record_extraction_failure(
            "user-1",
            "queued-again",
            error=RuntimeError("not leased"),
            now=150,
            db=session,
        ) is None
        queued = await AgentMemoryExtractionJobs.get_job("user-1", "queued-again", db=session)
        assert queued.status == "queued"
        assert queued.retry_count == 0
        assert queued.last_error is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_extraction_rolls_back_cache_and_job_delete_when_consolidation_enqueue_fails(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "chat-1")
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "chat-1", "leased", 130, None, 0, None, 100, db=session
        )
        await session.commit()

        def fail_consolidation_enqueue(conn, cursor, statement, parameters, context, executemany):
            if "agent_memory_consolidation_job" in statement:
                raise RuntimeError("simulated consolidation enqueue failure")

        event.listen(engine.sync_engine, "before_cursor_execute", fail_consolidation_enqueue)
        try:
            with pytest.raises(RuntimeError, match="simulated consolidation enqueue failure"):
                await extraction.complete_extraction_job(
                    "user-1",
                    "chat-1",
                    source_updated_at=1001,
                    output={"raw_memory": "remember pytest", "rollout_summary": "pytest", "rollout_slug": None},
                    now=140,
                    db=session,
                )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", fail_consolidation_enqueue)
            await session.rollback()

        assert await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session) is None
        job = await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        assert job.status == "leased"
        assert job.lease_until == 130

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_extraction_requires_expected_lease_before_success_write(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "chat-1")
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "chat-1", "leased", 200, None, 0, None, 150, db=session
        )

        with pytest.raises(RuntimeError, match="lease"):
            await extraction.complete_extraction_job(
                "user-1",
                "chat-1",
                source_updated_at=1001,
                output={"raw_memory": "remember pytest", "rollout_summary": "pytest", "rollout_slug": None},
                now=160,
                expected_lease_until=130,
                db=session,
            )

        assert await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session) is None
        job = await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        assert job.status == "leased"
        assert job.lease_until == 200
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_extraction_rolls_back_if_lease_changes_before_job_delete(tmp_path):
    extraction = importlib.import_module("open_webui.utils.agent_memory_extraction")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await _insert_exchange(session, "chat-1")
        await AgentMemoryExtractionJobs.upsert_job(
            "user-1", "chat-1", "leased", 130, None, 0, None, 100, db=session
        )
        await session.commit()

        fired = {"value": False}

        def reclaim_before_delete(conn, cursor, statement, parameters, context, executemany):
            if not fired["value"] and "DELETE" in statement and "agent_memory_extraction_job" in statement:
                fired["value"] = True
                cursor.execute(
                    "UPDATE agent_memory_extraction_job SET lease_until = 200, updated_at = 150 "
                    "WHERE user_id = ? AND chat_id = ?",
                    ("user-1", "chat-1"),
                )

        event.listen(engine.sync_engine, "before_cursor_execute", reclaim_before_delete)
        try:
            with pytest.raises(RuntimeError, match="lease"):
                await extraction.complete_extraction_job(
                    "user-1",
                    "chat-1",
                    source_updated_at=1001,
                    output={"raw_memory": "remember pytest", "rollout_summary": "pytest", "rollout_slug": None},
                    now=160,
                    expected_lease_until=130,
                    db=session,
                )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", reclaim_before_delete)
            await session.rollback()

        assert fired["value"] is True
        assert await AgentMemoryExtractionCaches.get_cache("user-1", "chat-1", db=session) is None
        job = await AgentMemoryExtractionJobs.get_job("user-1", "chat-1", db=session)
        assert job.status == "leased"
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_completed_chat_background_hook_only_enqueues_and_does_not_run_extraction_inline(monkeypatch):
    middleware = importlib.import_module("open_webui.utils.middleware")
    calls = []

    async def fake_enqueue(request, chat_id, user):
        calls.append(("enqueue", chat_id, user.id))
        return True

    async def fake_run(*args, **kwargs):
        calls.append(("run", args, kwargs))

    async def fake_messages_map(chat_id):
        return {
            "u1": {"id": "u1", "role": "user", "content": "remember this", "childrenIds": ["a1"]},
            "a1": {
                "id": "a1",
                "role": "assistant",
                "content": "done",
                "model": "gpt-test",
                "parentId": "u1",
                "childrenIds": [],
            },
        }

    monkeypatch.setattr(middleware, "enqueue_agent_memory_extraction_after_completion", fake_enqueue, raising=False)
    monkeypatch.setattr(middleware, "run_agent_memory_extraction_jobs_once", fake_run, raising=False)
    monkeypatch.setattr(middleware.Chats, "get_messages_map_by_chat_id", fake_messages_map)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=_config())))
    user = SimpleNamespace(id="user-1", role="user", model_dump=lambda: {"id": "user-1"})
    await middleware.background_tasks_handler(
        {
            "request": request,
            "form_data": {"messages": []},
            "user": user,
            "metadata": {"chat_id": "chat-1", "message_id": "a1"},
            "tasks": {},
            "event_emitter": lambda event: None,
        }
    )

    assert calls == [("enqueue", "chat-1", "user-1")]
