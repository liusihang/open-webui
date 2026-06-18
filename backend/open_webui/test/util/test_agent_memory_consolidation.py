import importlib
import json
import os
from types import SimpleNamespace

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")
os.environ.setdefault("DATABASE_ENABLE_SESSION_SHARING", "true")

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.agent_memories import (
    AgentMemoryArtifact,
    AgentMemoryArtifacts,
    AgentMemoryConsolidationJob,
    AgentMemoryConsolidationJobs,
    AgentMemoryExtractionCache,
    AgentMemoryExtractionCaches,
)
from open_webui.models.chats import Chat
from open_webui.models.folders import Folder
from open_webui.models.groups import Group, GroupMember
from open_webui.models.notes import Note


def _config(**overrides):
    values = {
        "ENABLE_AGENT_MEMORY": True,
        "AGENT_MEMORY_CONSOLIDATION_MODEL": "",
        "AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT": 5,
        "AGENT_MEMORY_LEASE_SECONDS": 30,
        "AGENT_MEMORY_RETRY_BACKOFF_SECONDS": 10,
        "TASK_MODEL": "",
        "TASK_MODEL_EXTERNAL": "",
        "DEFAULT_MODELS": "gpt-test",
        "USER_PERMISSIONS": {"features": {"agent_memory": True}},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _session_factory(tmp_path):
    db_path = tmp_path / "agent-memory-consolidation.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    for table in [
        Chat.__table__,
        Folder.__table__,
        Note.__table__,
        Group.__table__,
        GroupMember.__table__,
        AgentMemoryExtractionCache.__table__,
        AgentMemoryConsolidationJob.__table__,
        AgentMemoryArtifact.__table__,
    ]:
        table.create(sync_engine, checkfirst=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _chat(chat_id, user_id="user-1", *, folder_id=None, updated_at=1000, meta=None):
    return Chat(
        id=chat_id,
        user_id=user_id,
        title=chat_id,
        chat={"title": chat_id, "history": {"messages": {}}},
        created_at=updated_at - 100,
        updated_at=updated_at,
        share_id=None,
        archived=False,
        pinned=False,
        meta=meta or {},
        folder_id=folder_id,
    )


def _note(note_id, user_id="user-1", *, md="old", meta=None, now=1000):
    return Note(
        id=note_id,
        user_id=user_id,
        title=note_id,
        data={"content": {"md": md}},
        meta=meta or {},
        created_at=now,
        updated_at=now,
    )


async def _cache(session, chat_id, raw_memory, rollout_summary, *, user_id="user-1", status="succeeded"):
    await AgentMemoryExtractionCaches.upsert_cache(
        user_id=user_id,
        chat_id=chat_id,
        source_updated_at=1001,
        raw_memory=raw_memory,
        rollout_summary=rollout_summary,
        rollout_slug=None,
        generated_at=1002,
        status=status,
        db=session,
    )


def test_parse_consolidation_response_is_strict_and_sanitizes_output():
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")

    parsed = consolidation.parse_consolidation_response(
        {
            "memory_summary_md": "Use pytest.\r\nURL https://signed.example.test/a",
            "memory_md": "Token sk-secretvalue\r\n# Details",
        }
    )

    assert parsed == {
        "memory_summary_md": "Use pytest.\nURL [REMOVED_URL]",
        "memory_md": "Token [REDACTED]\n# Details",
    }
    for payload in [
        '{"memory_summary_md":"x"}',
        '{"memory_summary_md":"x","memory_md":"y","extra":true}',
        '{"memory_summary_md":["x"],"memory_md":"y"}',
        "[]",
        "null",
        "not json",
    ]:
        with pytest.raises(consolidation.AgentMemoryConsolidationContractError):
            consolidation.parse_consolidation_response(payload)


@pytest.mark.asyncio
async def test_build_consolidation_input_routes_current_scope_and_human_revisions(tmp_path):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(Folder(id="folder-1", user_id="user-1", name="Project", meta={}, created_at=1, updated_at=1))
        session.add(
            Folder(
                id="folder-off",
                user_id="user-1",
                name="Private",
                meta={"agent_memory": {"disabled": True}},
                created_at=1,
                updated_at=1,
            )
        )
        session.add(_chat("global-chat"))
        session.add(_chat("folder-chat", folder_id="folder-1"))
        session.add(_chat("disabled-chat", meta={"agent_memory": {"disabled": True}}))
        session.add(_chat("disabled-folder-chat", folder_id="folder-off"))
        await _cache(session, "global-chat", "global memory", "global summary")
        await _cache(session, "folder-chat", "folder memory", "folder summary")
        await _cache(session, "disabled-chat", "disabled chat memory", "disabled")
        await _cache(session, "disabled-folder-chat", "disabled folder memory", "disabled")

        original_md = "Original summary"
        edited_md = "Human edited summary"
        note_hash = consolidation.hash_note_markdown(original_md)
        session.add(
            _note(
                "note-1",
                md=edited_md,
                meta={
                    "agent_memory": {
                        "scope_type": "global",
                        "scope_id": "",
                        "path": "memory_summary.md",
                        "managed": True,
                    }
                },
            )
        )
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "memory_summary.md",
            original_md,
            "old-hash",
            1,
            "note-1",
            note_hash,
            100,
            db=session,
        )

        global_input = await consolidation.build_consolidation_input("user-1", "global", "", db=session)
        folder_input = await consolidation.build_consolidation_input("user-1", "folder", "folder-1", db=session)

        assert [item["chat_id"] for item in global_input.cache_records] == ["global-chat"]
        assert [item["chat_id"] for item in folder_input.cache_records] == ["folder-chat"]
        assert global_input.human_revisions == [
            {
                "path": "memory_summary.md",
                "note_id": "note-1",
                "content": edited_md,
                "expected_note_hash": consolidation.hash_note_markdown(edited_md),
            }
        ]
        assert global_input.input_hash != folder_input.input_hash

    await engine.dispose()


@pytest.mark.asyncio
async def test_build_consolidation_input_sanitizes_human_revision_content_without_changing_hash(tmp_path):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        edited_md = (
            "Human edit with token sk-humansecret and URL https://signed.example.test/a "
            "plus temp /tmp/openwebui-note"
        )
        original_md = "Original summary"
        session.add(
            _note(
                "note-1",
                md=edited_md,
                meta={
                    "agent_memory": {
                        "scope_type": "global",
                        "scope_id": "",
                        "path": "memory_summary.md",
                        "managed": True,
                    }
                },
            )
        )
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "memory_summary.md",
            original_md,
            "old-hash",
            1,
            "note-1",
            consolidation.hash_note_markdown(original_md),
            100,
            db=session,
        )

        built = await consolidation.build_consolidation_input("user-1", "global", "", db=session)

        assert built.human_revisions[0]["expected_note_hash"] == consolidation.hash_note_markdown(edited_md)
        assert "sk-humansecret" not in built.human_revisions[0]["content"]
        assert "signed.example" not in built.human_revisions[0]["content"]
        assert "openwebui-note" not in built.human_revisions[0]["content"]
        assert "[REDACTED]" in built.human_revisions[0]["content"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_build_consolidation_input_ignores_unmanaged_note_pointer(tmp_path):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(_note("ordinary-note", md="Ordinary note secret should not reach model", meta={}))
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "memory_summary.md",
            "Old summary",
            "old-hash",
            1,
            "ordinary-note",
            consolidation.hash_note_markdown("Old summary"),
            100,
            db=session,
        )

        built = await consolidation.build_consolidation_input("user-1", "global", "", db=session)
        prompt = consolidation._render_consolidation_prompt(built)

        assert built.human_revisions == []
        assert "Ordinary note secret" not in prompt

    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_and_failure_paths_are_lease_guarded(tmp_path):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        for scope_id, status, lease_until, retry_at in [
            ("", "queued", None, None),
            ("folder-1", "retry", None, 100),
            ("folder-2", "leased", 90, None),
            ("folder-3", "leased", 150, None),
        ]:
            await AgentMemoryConsolidationJobs.upsert_job(
                "user-1",
                "global" if scope_id == "" else "folder",
                scope_id,
                status,
                lease_until,
                retry_at,
                0,
                None,
                None,
                10,
                db=session,
            )

        claimed = await consolidation.claim_consolidation_jobs(now=100, limit=2, lease_seconds=30, db=session)
        assert [(job.scope_type, job.scope_id) for job in claimed] == [("global", ""), ("folder", "folder-1")]
        assert all(job.status == "leased" and job.lease_until == 130 for job in claimed)

        assert await consolidation.record_consolidation_failure(
            "user-1",
            "global",
            "",
            RuntimeError("late failure"),
            now=110,
            expected_lease_until=999,
            db=session,
        ) is None
        assert (await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)).status == "leased"

        failed = await consolidation.record_consolidation_failure(
            "user-1",
            "global",
            "",
            RuntimeError("failed once"),
            now=110,
            expected_lease_until=130,
            max_retries=1,
            db=session,
        )
        assert failed.status == "failed"
        assert failed.retry_count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_consolidation_writes_artifacts_notes_and_deletes_leased_job(tmp_path):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "leased", 130, None, 0, None, "pending", 100, db=session
        )

        await consolidation.complete_consolidation_job(
            "user-1",
            "global",
            "",
            output={"memory_summary_md": "Summary", "memory_md": "# Memory\nDetails"},
            input_hash="input-1",
            now=140,
            expected_lease_until=130,
            expected_note_hashes={},
            db=session,
        )

        summary = await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "memory_summary.md", db=session)
        memory = await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "MEMORY.md", db=session)
        assert summary.content == "Summary"
        assert summary.revision == 1
        assert summary.input_hash == "input-1"
        assert summary.note_id
        assert summary.note_content_hash == consolidation.hash_note_markdown("Summary")
        assert memory.content == "# Memory\nDetails"
        assert memory.revision == 1
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None

        note = await session.get(Note, summary.note_id)
        assert note.user_id == "user-1"
        assert note.data == {"content": {"md": "Summary"}}
        assert note.meta == {
            "agent_memory": {
                "scope_type": "global",
                "scope_id": "",
                "path": "memory_summary.md",
                "managed": True,
            }
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_consolidation_updates_synced_note_and_increments_changed_revision(tmp_path):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(
            _note(
                "summary-note",
                md="Old summary",
                meta={
                    "agent_memory": {
                        "scope_type": "global",
                        "scope_id": "",
                        "path": "memory_summary.md",
                        "managed": True,
                    }
                },
            )
        )
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "memory_summary.md",
            "Old summary",
            "input-0",
            1,
            "summary-note",
            consolidation.hash_note_markdown("Old summary"),
            100,
            db=session,
        )
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "leased", 130, None, 0, None, "pending", 100, db=session
        )

        await consolidation.complete_consolidation_job(
            "user-1",
            "global",
            "",
            output={"memory_summary_md": "New summary", "memory_md": "# Memory"},
            input_hash="input-1",
            now=140,
            expected_lease_until=130,
            expected_note_hashes={"summary-note": consolidation.hash_note_markdown("Old summary")},
            db=session,
        )

        summary = await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "memory_summary.md", db=session)
        note = await session.get(Note, "summary-note")
        assert summary.revision == 2
        assert summary.note_id == "summary-note"
        assert summary.note_content_hash == consolidation.hash_note_markdown("New summary")
        assert note.data == {"content": {"md": "New summary"}}

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_consolidation_does_not_overwrite_note_changed_after_input_build(tmp_path):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(
            _note(
                "summary-note",
                md="Human edit after build",
                meta={
                    "agent_memory": {
                        "scope_type": "global",
                        "scope_id": "",
                        "path": "memory_summary.md",
                        "managed": True,
                    }
                },
            )
        )
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "memory_summary.md",
            "Old summary",
            "input-0",
            1,
            "summary-note",
            consolidation.hash_note_markdown("Old summary"),
            100,
            db=session,
        )
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "leased", 130, None, 0, None, "pending", 100, db=session
        )

        with pytest.raises(RuntimeError, match="Human Revision"):
            await consolidation.complete_consolidation_job(
                "user-1",
                "global",
                "",
                output={"memory_summary_md": "Model summary", "memory_md": "# Memory"},
                input_hash="input-1",
                now=140,
                expected_lease_until=130,
                expected_note_hashes={"summary-note": consolidation.hash_note_markdown("Old summary")},
                db=session,
            )

        note = await session.get(Note, "summary-note")
        assert note.data == {"content": {"md": "Human edit after build"}}
        assert (
            await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)
        ).status == "leased"
        assert (
            await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "memory_summary.md", db=session)
        ).content == "Old summary"

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_consolidation_requires_stored_note_hash_even_without_expected_map(tmp_path):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(
            _note(
                "summary-note",
                md="Human edit",
                meta={
                    "agent_memory": {
                        "scope_type": "global",
                        "scope_id": "",
                        "path": "memory_summary.md",
                        "managed": True,
                    }
                },
            )
        )
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "memory_summary.md",
            "Old summary",
            "input-0",
            1,
            "summary-note",
            consolidation.hash_note_markdown("Old summary"),
            100,
            db=session,
        )
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "leased", 130, None, 0, None, "pending", 100, db=session
        )

        with pytest.raises(RuntimeError, match="Human Revision"):
            await consolidation.complete_consolidation_job(
                "user-1",
                "global",
                "",
                output={"memory_summary_md": "Model summary", "memory_md": "# Memory"},
                input_hash="input-1",
                now=140,
                expected_lease_until=130,
                expected_note_hashes={},
                db=session,
            )

        note = await session.get(Note, "summary-note")
        assert note.data == {"content": {"md": "Human edit"}}

    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_consolidation_validates_note_linkage_before_overwrite(tmp_path):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async with session_factory() as session:
        session.add(_note("ordinary-note", md="Do not overwrite", meta={}))
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "memory_summary.md",
            "Old summary",
            "input-0",
            1,
            "ordinary-note",
            consolidation.hash_note_markdown("Old summary"),
            100,
            db=session,
        )
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "leased", 130, None, 0, None, "pending", 100, db=session
        )

        await consolidation.complete_consolidation_job(
            "user-1",
            "global",
            "",
            output={"memory_summary_md": "New summary", "memory_md": "# Memory"},
            input_hash="input-1",
            now=140,
            expected_lease_until=130,
            expected_note_hashes={},
            db=session,
        )

        ordinary = await session.get(Note, "ordinary-note")
        summary = await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "memory_summary.md", db=session)
        assert ordinary.data == {"content": {"md": "Do not overwrite"}}
        assert summary.note_id != "ordinary-note"
        new_note = await session.get(Note, summary.note_id)
        assert new_note.meta["agent_memory"]["managed"] is True
        assert new_note.meta["agent_memory"]["path"] == "memory_summary.md"

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_consolidation_noops_when_input_hash_unchanged_without_human_revision(tmp_path, monkeypatch):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)
    calls = []

    async def fake_generate_chat_completion(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(consolidation, "generate_chat_completion", fake_generate_chat_completion)

    async with session_factory() as session:
        session.add(_chat("global-chat"))
        await _cache(session, "global-chat", "global memory", "summary")
        built = await consolidation.build_consolidation_input("user-1", "global", "", db=session)
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "memory_summary.md",
            "Summary",
            built.input_hash,
            1,
            None,
            None,
            100,
            db=session,
        )
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "MEMORY.md",
            "# Memory",
            built.input_hash,
            1,
            None,
            None,
            100,
            db=session,
        )
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "queued", None, None, 0, None, built.input_hash, 100, db=session
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

        assert await consolidation.run_agent_memory_consolidation_jobs_once(request, now=1200, limit=1, db=session) == 0
        assert calls == []
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None
        summary = await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "memory_summary.md", db=session)
        memory = await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "MEMORY.md", db=session)
        assert summary.note_id
        assert memory.note_id
        assert (await session.get(Note, summary.note_id)).data == {"content": {"md": "Summary"}}
        assert (await session.get(Note, memory.note_id)).data == {"content": {"md": "# Memory"}}

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_consolidation_does_not_send_existing_artifact_content_to_model(tmp_path, monkeypatch):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)
    captured = []

    async def fake_generate_chat_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        captured.append(form_data)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"memory_summary_md": "New", "memory_md": "# New"})
                    }
                }
            ]
        }

    monkeypatch.setattr(consolidation, "generate_chat_completion", fake_generate_chat_completion)

    async with session_factory() as session:
        session.add(_chat("global-chat"))
        await _cache(session, "global-chat", "new surviving memory", "new summary")
        await AgentMemoryArtifacts.upsert_artifact(
            "user-1",
            "global",
            "",
            "MEMORY.md",
            "OLD DELETED SECRET EVIDENCE",
            "old-input",
            1,
            None,
            None,
            100,
            db=session,
        )
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "queued", None, None, 0, None, None, 100, db=session
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

        assert await consolidation.run_agent_memory_consolidation_jobs_once(request, now=1200, limit=1, db=session) == 1
        prompt = captured[0]["messages"][0]["content"]
        assert "new surviving memory" in prompt
        assert "OLD DELETED SECRET EVIDENCE" not in prompt

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_consolidation_respects_user_agent_memory_permission(tmp_path, monkeypatch):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)
    calls = []

    async def fake_generate_chat_completion(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(consolidation, "generate_chat_completion", fake_generate_chat_completion)

    async with session_factory() as session:
        session.add(_chat("global-chat"))
        await _cache(session, "global-chat", "memory", "summary")
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "queued", None, None, 0, None, None, 100, db=session
        )
        request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={"gpt-test": {"id": "gpt-test", "owned_by": "openai", "info": {"params": {}}}},
                    config=_config(
                        DEFAULT_MODELS="gpt-test",
                        USER_PERMISSIONS={"features": {"agent_memory": False}},
                    ),
                )
            ),
        )

        assert await consolidation.run_agent_memory_consolidation_jobs_once(request, now=1200, limit=1, db=session) == 0
        assert calls == []
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_consolidation_records_attempted_input_hash_on_failure(tmp_path, monkeypatch):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)

    async def fake_generate_chat_completion(*args, **kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(consolidation, "generate_chat_completion", fake_generate_chat_completion)

    async with session_factory() as session:
        session.add(_chat("global-chat"))
        await _cache(session, "global-chat", "memory", "summary")
        built = await consolidation.build_consolidation_input("user-1", "global", "", db=session)
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "queued", None, None, 0, None, None, 100, db=session
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

        assert await consolidation.run_agent_memory_consolidation_jobs_once(request, now=1200, limit=1, db=session) == 0
        job = await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)
        assert job.status == "retry"
        assert job.input_hash == built.input_hash
        assert "model down" in job.last_error

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_consolidation_rejects_oversized_input_before_model_call(tmp_path, monkeypatch):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)
    calls = []

    async def fake_generate_chat_completion(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(consolidation, "generate_chat_completion", fake_generate_chat_completion)
    monkeypatch.setattr(consolidation, "DEFAULT_CONSOLIDATION_INPUT_CHARS", 10, raising=False)

    async with session_factory() as session:
        session.add(_chat("global-chat"))
        await _cache(session, "global-chat", "memory " * 100, "summary")
        built = await consolidation.build_consolidation_input("user-1", "global", "", db=session)
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "queued", None, None, 0, None, None, 100, db=session
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

        assert await consolidation.run_agent_memory_consolidation_jobs_once(request, now=1200, limit=1, db=session) == 0
        assert calls == []
        job = await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session)
        assert job.status == "retry"
        assert job.input_hash == built.input_hash
        assert "too large" in job.last_error

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_consolidation_calls_model_and_completes_artifacts(tmp_path, monkeypatch):
    consolidation = importlib.import_module("open_webui.utils.agent_memory_consolidation")
    engine, session_factory = await _session_factory(tmp_path)
    captured = []

    async def fake_generate_chat_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
        captured.append(
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
                                "memory_summary_md": "Summary from model",
                                "memory_md": "# Memory\nDetails from model",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(consolidation, "generate_chat_completion", fake_generate_chat_completion)
    rebuild_calls = []

    async def fake_rebuild_agent_memory_index_for_scope(request, user_id, scope_type, scope_id="", db=None):
        rebuild_calls.append(
            {
                "request": request,
                "user_id": user_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "db": db,
            }
        )

    monkeypatch.setattr(
        consolidation,
        "rebuild_agent_memory_index_for_scope",
        fake_rebuild_agent_memory_index_for_scope,
    )

    async with session_factory() as session:
        session.add(_chat("global-chat"))
        await _cache(session, "global-chat", "global memory", "summary")
        await AgentMemoryConsolidationJobs.upsert_job(
            "user-1", "global", "", "queued", None, None, 0, None, None, 100, db=session
        )
        request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={
                        "gpt-test": {"id": "gpt-test", "owned_by": "openai", "info": {"params": {}}},
                        "consolidator": {"id": "consolidator", "owned_by": "openai", "info": {"params": {}}},
                    },
                    config=_config(AGENT_MEMORY_CONSOLIDATION_MODEL="consolidator", DEFAULT_MODELS="gpt-test"),
                )
            ),
        )

        assert await consolidation.run_agent_memory_consolidation_jobs_once(request, now=1200, limit=1, db=session) == 1
        assert len(captured) == 1
        payload = captured[0]["form_data"]
        assert payload["model"] == "consolidator"
        assert payload["stream"] is False
        assert payload["metadata"]["task"] == "agent_memory_consolidation"
        assert "global memory" in payload["messages"][0]["content"]
        assert captured[0]["user"].role == "user"
        assert captured[0]["user"].role != "admin"
        assert captured[0]["user"].name == "Agent Memory Service"
        assert captured[0]["user"].is_service_account is True
        assert captured[0]["bypass_filter"] is False
        assert captured[0]["bypass_system_prompt"] is False
        assert (
            await AgentMemoryArtifacts.get_artifact("user-1", "global", "", "memory_summary.md", db=session)
        ).content == "Summary from model"
        assert await AgentMemoryConsolidationJobs.get_job("user-1", "global", "", db=session) is None
        assert rebuild_calls == [
            {
                "request": request,
                "user_id": "user-1",
                "scope_type": "global",
                "scope_id": "",
                "db": session,
            }
        ]

    await engine.dispose()
