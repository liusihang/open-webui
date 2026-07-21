from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

SERVICE_TOKEN = "runtime-secret"


def _protocol():
    try:
        return importlib.import_module("agentscope_runtime.execution_store")
    except ModuleNotFoundError:
        pytest.fail("agentscope_runtime.execution_store is required")


def _fingerprint(
    *,
    execution_id: str,
    runtime_session_id: str,
    checkpoint_version: int,
    subject_id: str,
    command_type: str,
    payload: dict,
) -> str:
    canonical = json.dumps(
        {
            "schema_version": 1,
            "execution_id": execution_id,
            "runtime_session_id": runtime_session_id,
            "expected_checkpoint_version": checkpoint_version,
            "subject_id": subject_id,
            "command_type": command_type,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _prepare_body(
    *,
    execution_id: str = "rex-1",
    runtime_session_id: str = "rt-run-1",
    checkpoint_version: int = 1,
    subject_id: str = "approval-1",
    command_type: str = "resume_approval",
    payload: dict | None = None,
) -> dict:
    payload = payload or {"decision": "approved"}
    return {
        "schema_version": 1,
        "execution_id": execution_id,
        "runtime_session_id": runtime_session_id,
        "expected_checkpoint_version": checkpoint_version,
        "subject_id": subject_id,
        "command_type": command_type,
        "payload": payload,
        "fingerprint": _fingerprint(
            execution_id=execution_id,
            runtime_session_id=runtime_session_id,
            checkpoint_version=checkpoint_version,
            subject_id=subject_id,
            command_type=command_type,
            payload=payload,
        ),
    }


def _checkpoint(protocol, **overrides):
    values = {
        "run_id": "run-1",
        "runtime_session_id": "rt-run-1",
        "state": "waiting_approval",
        "checkpoint_version": 1,
        "wait_kind": "approval",
        "wait_subject_id": "approval-1",
        "agent_state": {"context": [], "cur_iter": 0},
        "bridge_state": {"next_tool_call_index": 2, "model_call_indexes": {"leader": 2}},
        "pending_call": {
            "participant_id": "leader",
            "tool_call_id": "tool-call-1",
            "tool_id": "tool:terminal:main:write_file",
            "arguments": {"path": "/workspace/report.txt", "content": "replacement"},
            "idempotency_key": "tool:leader:tool-call-1:1",
        },
    }
    values.update(overrides)
    return protocol.RuntimeCheckpoint(**values)


def test_sqlite_prepare_replays_same_execution_and_rejects_conflicting_fingerprint(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "runtime.sqlite3")
    store.save_checkpoint(_checkpoint(protocol))
    command = protocol.RuntimeExecutionCommand.from_mapping(_prepare_body())

    first = store.prepare(command, run_id="run-1")
    replay = store.prepare(command, run_id="run-1")

    assert first.state == "prepared"
    assert replay.state == "prepared"
    assert replay.duplicate is True

    conflict = protocol.RuntimeExecutionCommand.from_mapping(
        _prepare_body(payload={"decision": "rejected"})
    )
    with pytest.raises(protocol.RuntimeExecutionConflict):
        store.prepare(conflict, run_id="run-1")


def test_store_schema_version_has_migration_seam(tmp_path: Path) -> None:
    protocol = _protocol()
    path = tmp_path / "schema.sqlite3"
    protocol.SQLiteRuntimeExecutionStore(path)
    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT value FROM runtime_schema WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == "2"
        connection.execute(
            "UPDATE runtime_schema SET value='0' WHERE key='schema_version'"
        )

    protocol.SQLiteRuntimeExecutionStore(path)
    with sqlite3.connect(path) as connection:
        migrated = connection.execute(
            "SELECT value FROM runtime_schema WHERE key='schema_version'"
        ).fetchone()[0]
    assert migrated == "2"


def test_store_v1_upgrade_backfills_pending_applied_continuation(tmp_path: Path) -> None:
    protocol = _protocol()
    path = tmp_path / "schema-v1-upgrade.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE runtime_schema (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO runtime_schema(key, value) VALUES ('schema_version', '1');
            CREATE TABLE runtime_checkpoint (
                run_id TEXT PRIMARY KEY,
                runtime_session_id TEXT NOT NULL UNIQUE,
                checkpoint_version INTEGER NOT NULL,
                checkpoint_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE runtime_execution (
                execution_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                runtime_session_id TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                command_type TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL,
                checkpoint_version INTEGER NOT NULL,
                outcome_json TEXT,
                error_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(run_id, command_type, subject_id)
            );
            """
        )
        checkpoint = _checkpoint(
            protocol,
            state="running",
            checkpoint_version=2,
            wait_kind=None,
            wait_subject_id=None,
            applied_execution_id="rex-legacy",
            continuation_pending=True,
        )
        connection.execute(
            """
            INSERT INTO runtime_checkpoint(
                run_id, runtime_session_id, checkpoint_version,
                checkpoint_json, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                checkpoint.run_id,
                checkpoint.runtime_session_id,
                checkpoint.checkpoint_version,
                checkpoint.model_dump_json(),
                1.0,
            ),
        )
        connection.execute(
            """
            INSERT INTO runtime_execution(
                execution_id, run_id, runtime_session_id, subject_id,
                command_type, fingerprint, payload_json, state,
                checkpoint_version, outcome_json, error_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rex-legacy",
                "run-1",
                "rt-run-1",
                "approval-1",
                "resume_approval",
                "f" * 64,
                '{"decision":"rejected"}',
                "applied",
                2,
                '{"kind":"applied"}',
                None,
                1.0,
                1.0,
            ),
        )

    store = protocol.SQLiteRuntimeExecutionStore(path)

    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT value FROM runtime_schema WHERE key='schema_version'"
        ).fetchone()[0]
        continuation_state = connection.execute(
            "SELECT continuation_state FROM runtime_execution WHERE execution_id='rex-legacy'"
        ).fetchone()[0]
    assert version == "2"
    assert continuation_state == "pending"
    assert [
        candidate.execution.execution_id
        for candidate in store.list_pending_continuation_recoveries()
    ] == ["rex-legacy"]


def test_corrupt_execution_json_raises_domain_unrecoverable(tmp_path: Path) -> None:
    protocol = _protocol()
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "corrupt-execution.sqlite3")
    store.save_checkpoint(_checkpoint(protocol))
    store.prepare(
        protocol.RuntimeExecutionCommand.from_mapping(_prepare_body()),
        run_id="run-1",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE runtime_execution SET payload_json='{broken' WHERE execution_id='rex-1'"
        )

    with pytest.raises(protocol.RuntimeExecutionUnrecoverable):
        store.get_execution("rex-1")


def test_checkpoint_size_limit_fails_before_write(tmp_path: Path) -> None:
    protocol = _protocol()
    store = protocol.SQLiteRuntimeExecutionStore(
        tmp_path / "bounded.sqlite3",
        max_checkpoint_bytes=512,
    )
    oversized = _checkpoint(
        protocol,
        agent_state={"context": [], "blob": "x" * 2048},
    )

    with pytest.raises(protocol.RuntimeCheckpointTooLarge):
        store.save_checkpoint(oversized)
    assert store.get_checkpoint("run-1") is None


def test_terminal_execution_cleanup_bounds_retained_rows(tmp_path: Path) -> None:
    protocol = _protocol()
    store = protocol.SQLiteRuntimeExecutionStore(
        tmp_path / "cleanup.sqlite3",
        terminal_retention_seconds=3600,
        max_terminal_executions=1,
    )
    with sqlite3.connect(store.path) as connection:
        for index in range(3):
            connection.execute(
                """
                INSERT INTO runtime_execution(
                    execution_id, run_id, runtime_session_id, subject_id,
                    command_type, fingerprint, payload_json, state,
                    checkpoint_version, outcome_json, error_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'resume_approval', ?, '{}', 'failed', 1, NULL, '{}', ?, ?)
                """,
                (
                    f"rex-cleanup-{index}",
                    f"run-cleanup-{index}",
                    f"rt-cleanup-{index}",
                    f"approval-cleanup-{index}",
                    "f" * 64,
                    float(index + 1),
                    float(index + 1),
                ),
            )

    deleted = store.cleanup_terminal_executions(now=10)
    with sqlite3.connect(store.path) as connection:
        retained = connection.execute(
            "SELECT COUNT(*) FROM runtime_execution WHERE state='failed'"
        ).fetchone()[0]
    assert deleted == 2
    assert retained == 1


def test_terminal_checkpoint_cleanup_is_independent_and_preserves_live_work(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    store = protocol.SQLiteRuntimeExecutionStore(
        tmp_path / "checkpoint-cleanup.sqlite3",
        terminal_checkpoint_retention_seconds=5,
        max_terminal_checkpoints=100,
    )
    checkpoints = {
        "run-active": _checkpoint(
            protocol,
            run_id="run-active",
            runtime_session_id="rt-active",
            state="running",
            wait_kind=None,
            wait_subject_id=None,
            pending_call=None,
        ),
        "run-waiting": _checkpoint(
            protocol,
            run_id="run-waiting",
            runtime_session_id="rt-waiting",
        ),
        "run-pending": _checkpoint(
            protocol,
            run_id="run-pending",
            runtime_session_id="rt-pending",
            state="completed",
            wait_kind=None,
            wait_subject_id=None,
            pending_call=None,
            continuation_pending=True,
        ),
        "run-old-terminal": _checkpoint(
            protocol,
            run_id="run-old-terminal",
            runtime_session_id="rt-old-terminal",
            state="failed",
            wait_kind=None,
            wait_subject_id=None,
            pending_call=None,
        ),
        "run-recent-terminal": _checkpoint(
            protocol,
            run_id="run-recent-terminal",
            runtime_session_id="rt-recent-terminal",
            state="cancelled",
            wait_kind=None,
            wait_subject_id=None,
            pending_call=None,
        ),
        "run-latest-terminal": _checkpoint(
            protocol,
            run_id="run-latest-terminal",
            runtime_session_id="rt-latest-terminal",
            state="completed",
            wait_kind=None,
            wait_subject_id=None,
            pending_call=None,
        ),
    }
    for checkpoint in checkpoints.values():
        store.save_checkpoint(checkpoint)
    with sqlite3.connect(store.path) as connection:
        for run_id, updated_at in {
            "run-active": 1,
            "run-waiting": 1,
            "run-pending": 1,
            "run-old-terminal": 1,
            "run-recent-terminal": 9,
            "run-latest-terminal": 10,
        }.items():
            connection.execute(
                "UPDATE runtime_checkpoint SET updated_at=? WHERE run_id=?",
                (updated_at, run_id),
            )

    store.max_terminal_checkpoints = 1
    store.cleanup_terminal_executions(now=10)

    assert store.get_checkpoint("run-active") is not None
    assert store.get_checkpoint("run-waiting") is not None
    assert store.get_checkpoint("run-pending") is not None
    assert store.get_checkpoint("run-old-terminal") is None
    assert store.get_checkpoint("run-recent-terminal") is None
    assert store.get_checkpoint("run-latest-terminal") is not None


def test_terminal_checkpoint_transition_triggers_bounded_cleanup(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    store = protocol.SQLiteRuntimeExecutionStore(
        tmp_path / "checkpoint-transition-cleanup.sqlite3",
        terminal_checkpoint_retention_seconds=3600,
        max_terminal_checkpoints=1,
    )
    for index in range(2):
        running = _checkpoint(
            protocol,
            run_id=f"run-terminal-{index}",
            runtime_session_id=f"rt-terminal-{index}",
            state="running",
            wait_kind=None,
            wait_subject_id=None,
            pending_call=None,
        )
        store.save_checkpoint(running)
        store.save_checkpoint_cas(
            running.model_copy(update={"state": "completed"}),
            expected_version=running.checkpoint_version,
            expected_states={"running"},
        )

    with sqlite3.connect(store.path) as connection:
        retained = connection.execute(
            "SELECT run_id FROM runtime_checkpoint ORDER BY updated_at DESC"
        ).fetchall()
    assert len(retained) == 1


def test_process_lock_fails_clearly_without_fcntl(tmp_path: Path, monkeypatch) -> None:
    protocol = _protocol()
    monkeypatch.setattr(protocol, "fcntl", None)
    lock = protocol.RuntimeProcessLock(tmp_path / "runtime.sqlite3")

    with pytest.raises(RuntimeError, match="Unix.*fcntl"):
        lock.acquire()


@pytest.mark.asyncio
async def test_prepare_query_and_activate_survive_runtime_restart_and_lost_responses(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    path = tmp_path / "runtime.sqlite3"
    first_store = protocol.SQLiteRuntimeExecutionStore(path)
    first_store.save_checkpoint(_checkpoint(protocol))
    apply_calls: list[str] = []

    async def apply_execution(checkpoint, execution):
        apply_calls.append(execution.execution_id)
        return protocol.RuntimeApplyResult(
            checkpoint=checkpoint.model_copy(
                update={
                    "state": "running",
                    "wait_kind": None,
                    "wait_subject_id": None,
                    "applied_execution_id": execution.execution_id,
                }
            ),
            outcome={"kind": "applied", "runtime_state": "running"},
        )

    first_app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=first_store,
        execution_applier=apply_execution,
        auto_finalize_ordinary_qa=False,
    )
    body = _prepare_body()
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first_app), base_url="http://runtime.test"
    ) as client:
        prepared = await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1", json=body, headers=headers
        )
        assert prepared.status_code == 202
        assert prepared.json()["state"] == "prepared"
        assert prepared.json()["fingerprint"] == body["fingerprint"]

    restarted_store = protocol.SQLiteRuntimeExecutionStore(path)
    restarted_app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=restarted_store,
        execution_applier=apply_execution,
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted_app), base_url="http://runtime.test"
    ) as client:
        query = await client.get(
            "/v1/openwebui/runs/run-1/executions/rex-1", headers=headers
        )
        assert query.status_code == 200
        assert query.json()["state"] == "prepared"
        assert query.json()["fingerprint"] == body["fingerprint"]

        activated = await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )
        assert activated.status_code == 200
        assert activated.json()["state"] == "applied"
        assert activated.json()["fingerprint"] == body["fingerprint"]

    second_restart_app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=protocol.SQLiteRuntimeExecutionStore(path),
        execution_applier=apply_execution,
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=second_restart_app), base_url="http://runtime.test"
    ) as client:
        replay = await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )
        assert replay.status_code == 200
        assert replay.json()["state"] == "applied"
        assert replay.json()["duplicate"] is True

    assert apply_calls == ["rex-1"]


@pytest.mark.asyncio
async def test_rejected_approval_and_user_input_statuses_apply_once_without_tool_replay(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    path = tmp_path / "runtime.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)
    store.save_checkpoint(_checkpoint(protocol))
    tool_calls: list[str] = []
    applied: list[tuple[str, str]] = []

    async def apply_execution(checkpoint, execution):
        status = execution.payload.get("decision") or execution.payload.get("status")
        if execution.command_type == "resume_approval" and status == "approved":
            tool_calls.append(execution.execution_id)
        applied.append((execution.execution_id, str(status)))
        return protocol.RuntimeApplyResult(
            checkpoint=checkpoint.model_copy(
                update={
                    "state": "running",
                    "wait_kind": None,
                    "wait_subject_id": None,
                    "applied_execution_id": execution.execution_id,
                }
            ),
            outcome={"kind": "applied", "status": status},
        )

    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        execution_applier=apply_execution,
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        rejected_body = _prepare_body(payload={"decision": "rejected"})
        assert (
            await client.put(
                "/v1/openwebui/runs/run-1/executions/rex-1",
                json=rejected_body,
                headers=headers,
            )
        ).status_code == 202
        for _ in range(2):
            response = await client.post(
                "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
            )
            assert response.json()["state"] == "applied"

    assert tool_calls == []
    assert applied == [("rex-1", "rejected")]

    for index, status in enumerate(("accepted", "declined", "cancelled", "timeout"), start=2):
        session_id = f"rt-run-{index}"
        run_id = f"run-{index}"
        subject_id = f"user-input-{index}"
        store.save_checkpoint(
            _checkpoint(
                protocol,
                run_id=run_id,
                runtime_session_id=session_id,
                state="waiting_user_input",
                wait_kind="user_input",
                wait_subject_id=subject_id,
                checkpoint_version=1,
                pending_call={"tool_call_id": f"tool-call-{index}"},
            )
        )
        execution_id = f"rex-{index}"
        body = _prepare_body(
            execution_id=execution_id,
            runtime_session_id=session_id,
            subject_id=subject_id,
            command_type="resume_user_input",
            payload={"status": status, "content": {"answer": status}},
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
        ) as client:
            await client.put(
                f"/v1/openwebui/runs/{run_id}/executions/{execution_id}",
                json=body,
                headers=headers,
            )
            await client.post(
                f"/v1/openwebui/runs/{run_id}/executions/{execution_id}/activate",
                headers=headers,
            )
            await client.post(
                f"/v1/openwebui/runs/{run_id}/executions/{execution_id}/activate",
                headers=headers,
            )

    assert applied == [
        ("rex-1", "rejected"),
        ("rex-2", "accepted"),
        ("rex-3", "declined"),
        ("rex-4", "cancelled"),
        ("rex-5", "timeout"),
    ]


@pytest.mark.asyncio
async def test_corrupt_checkpoint_indeterminate_tool_and_cancelled_session_are_fail_closed(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}

    corrupt_path = tmp_path / "corrupt.sqlite3"
    corrupt_store = protocol.SQLiteRuntimeExecutionStore(corrupt_path)
    corrupt_store.save_checkpoint(_checkpoint(protocol))
    with sqlite3.connect(corrupt_path) as connection:
        connection.execute(
            "UPDATE runtime_checkpoint SET checkpoint_json = ? WHERE run_id = ?",
            ("{broken", "run-1"),
        )
        connection.commit()
    corrupt_app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=protocol.SQLiteRuntimeExecutionStore(corrupt_path),
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=corrupt_app), base_url="http://runtime.test"
    ) as client:
        response = await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(),
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "checkpoint_unrecoverable"

    path = tmp_path / "runtime.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)
    store.save_checkpoint(_checkpoint(protocol))
    apply_calls = 0

    async def indeterminate(checkpoint, execution):
        nonlocal apply_calls
        apply_calls += 1
        raise protocol.ToolOutcomeIndeterminate("tool side effect may have occurred")

    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        execution_applier=indeterminate,
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(),
            headers=headers,
        )
        first = await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )
        replay = await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )
        assert first.json()["state"] == "indeterminate"
        assert replay.json()["state"] == "indeterminate"
    assert apply_calls == 1

    cancelled_store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "cancelled.sqlite3")
    cancelled_store.save_checkpoint(_checkpoint(protocol, cancel_requested=True, state="cancelled"))
    cancelled_app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=cancelled_store,
        execution_applier=indeterminate,
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=cancelled_app), base_url="http://runtime.test"
    ) as client:
        response = await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(),
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "execution_cancelled"
    assert apply_calls == 1


def test_wait_checkpoint_is_durable_before_requested_event_is_emitted(tmp_path: Path) -> None:
    protocol = _protocol()
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "runtime.sqlite3")
    observed: list[str] = []

    def emit_requested() -> None:
        loaded = store.get_checkpoint("run-1")
        assert loaded is not None
        assert loaded.wait_kind == "approval"
        observed.append("requested")

    protocol.persist_wait_checkpoint_then_emit(
        store,
        _checkpoint(protocol),
        emit_requested,
    )

    assert observed == ["requested"]


@pytest.mark.asyncio
async def test_default_applier_replays_approved_tool_once_and_injects_result() -> None:
    protocol = _protocol()
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.app import _build_default_execution_applier

    class Callbacks:
        def __init__(self) -> None:
            self.tool_calls: list[dict] = []

        async def call_tool(self, **kwargs):
            self.tool_calls.append(kwargs)
            return {"status": "success", "content": "written"}

    agent_state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="provider-call-1",
                        name="write_file",
                        input='{"path":"/workspace/report.txt"}',
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    checkpoint = _checkpoint(
        protocol,
        agent_state=agent_state.model_dump(mode="json"),
        run_request={
            "run_id": "run-1",
            "chat_id": "chat-1",
            "leader_model_id": "model-a",
        },
        pending_call={
            "participant_id": "leader",
            "tool_call_id": "provider-call-1",
            "tool_name": "write_file",
            "tool_id": "tool:terminal:main:write_file",
            "arguments": {"path": "/workspace/report.txt"},
            "idempotency_key": "tool:leader:provider-call-1:1",
        },
    )
    execution = protocol.RuntimeExecutionRecord(
        execution_id="rex-1",
        run_id="run-1",
        runtime_session_id="rt-run-1",
        subject_id="approval-1",
        command_type="resume_approval",
        fingerprint="f" * 64,
        payload={"decision": "approved"},
        state="applying",
        checkpoint_version=1,
        created_at=1,
        updated_at=1,
    )
    callbacks = Callbacks()

    result = await _build_default_execution_applier(callbacks)(checkpoint, execution)

    assert len(callbacks.tool_calls) == 1
    assert callbacks.tool_calls[0]["decision_execution_id"] == "rex-1"
    assert callbacks.tool_calls[0]["tool_call_id"] == "provider-call-1"
    restored = AgentState.model_validate(result.checkpoint.agent_state)
    assert [block.id for block in restored.context[-1].get_content_blocks("tool_result")] == [
        "provider-call-1"
    ]
    assert result.checkpoint.continuation_pending is True


@pytest.mark.asyncio
async def test_default_applier_rejection_and_user_input_never_call_backend_tool() -> None:
    protocol = _protocol()
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.app import _build_default_execution_applier

    class Callbacks:
        def __init__(self) -> None:
            self.tool_calls = 0

        async def call_tool(self, **kwargs):
            self.tool_calls += 1
            raise AssertionError("tool must not be called")

    callbacks = Callbacks()

    async def apply(command_type: str, payload: dict, wait_kind: str):
        tool_name = "request_user_input" if wait_kind == "user_input" else "delete_entry"
        state = AgentState(
            reply_id="reply-1",
            context=[
                AssistantMsg(
                    id="reply-1",
                    name="leader",
                    content=[
                        ToolCallBlock(
                            id="provider-call-1",
                            name=tool_name,
                            input="{}",
                            state=ToolCallState.SUBMITTED,
                        )
                    ],
                )
            ],
        )
        checkpoint = _checkpoint(
            protocol,
            state=f"waiting_{wait_kind}",
            wait_kind=wait_kind,
            wait_subject_id="subject-1",
            agent_state=state.model_dump(mode="json"),
            run_request={
                "run_id": "run-1",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
            },
            pending_call={
                "participant_id": "leader",
                "tool_call_id": "provider-call-1",
                "tool_name": tool_name,
                "tool_id": tool_name,
                "arguments": {},
                "idempotency_key": "tool:leader:provider-call-1:1",
            },
        )
        execution = protocol.RuntimeExecutionRecord(
            execution_id=f"rex-{command_type}",
            run_id="run-1",
            runtime_session_id="rt-run-1",
            subject_id="subject-1",
            command_type=command_type,
            fingerprint="f" * 64,
            payload=payload,
            state="applying",
            checkpoint_version=1,
            created_at=1,
            updated_at=1,
        )
        return await _build_default_execution_applier(callbacks)(checkpoint, execution)

    rejected = await apply(
        "resume_approval",
        {"decision": "rejected", "status": "accepted"},
        "approval",
    )
    answered = await apply(
        "resume_user_input",
        {
            "decision": "rejected",
            "status": "accepted",
            "content": {"answer": "yes"},
        },
        "user_input",
    )
    declined = await apply(
        "resume_user_input",
        {"decision": "approved", "status": "declined"},
        "user_input",
    )

    assert callbacks.tool_calls == 0
    assert rejected.outcome["decision"] == "rejected"
    assert answered.outcome["status"] == "accepted"
    assert declined.outcome["status"] == "declined"


@pytest.mark.asyncio
async def test_start_run_persists_restartable_runtime_checkpoint_before_running_event(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    class Callbacks:
        def __init__(self, store) -> None:
            self.store = store
            self.events: list[str] = []

        async def append_event(self, **kwargs):
            checkpoint = self.store.get_checkpoint(kwargs["run_id"])
            assert checkpoint is not None
            assert checkpoint.state == "running"
            self.events.append(kwargs["event_type"])
            return {"seq": 1}

    path = tmp_path / "runtime.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)
    callbacks = Callbacks(store)
    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        openwebui_client=callbacks,
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={"run_id": "run-start", "chat_id": "chat-1", "messages": []},
        )

    assert response.status_code == 202
    checkpoint = protocol.SQLiteRuntimeExecutionStore(path).get_checkpoint("run-start")
    assert checkpoint is not None
    assert checkpoint.runtime_session_id == response.json()["runtime_session_id"]
    assert checkpoint.run_request["run_id"] == "run-start"
    assert callbacks.events == ["run.running"]


@pytest.mark.asyncio
async def test_durable_external_pause_persists_wait_before_backend_requested_event(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope.event import RequireExternalExecutionEvent
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge
    from agentscope_runtime.app import (
        RuntimeSession,
        _resolve_durable_external_event,
    )
    from agentscope_runtime.schemas import RunStartRequest

    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "runtime.sqlite3")
    request = RunStartRequest(
        run_id="run-1",
        chat_id="chat-1",
        leader_model_id="model-a",
        tool_access_envelope={
            "tools": [
                {
                    "id": "tool:terminal:main:write_file",
                    "name": "write_file",
                    "schema": {"parameters": {"type": "object"}},
                }
            ]
        },
    )
    session = RuntimeSession(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        state="running",
        request=request,
    )
    state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="provider-call-1",
                        name="write_file",
                        input='{"path":"/workspace/report.txt"}',
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    leader = type("Leader", (), {"state": state})()

    class Callbacks:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def append_event(self, **kwargs):
            checkpoint = store.get_checkpoint("run-1")
            assert checkpoint is not None
            assert checkpoint.wait_kind == "approval"
            assert checkpoint.wait_subject_id == "approval:run-1:provider-call-1"
            self.events.append(kwargs["event_type"])
            return {"seq": len(self.events)}

        async def call_tool(self, **kwargs):
            checkpoint = store.get_checkpoint("run-1")
            assert checkpoint is not None
            assert checkpoint.pending_call["tool_call_id"] == "provider-call-1"
            return {
                "status": "approval_required",
                "raw": {"approval_id": "approval:run-1:provider-call-1"},
            }

    callbacks = Callbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        callback_client=callbacks,
        durable_external_tools=True,
    )
    event = RequireExternalExecutionEvent(
        reply_id="reply-1",
        tool_calls=state.context[-1].get_content_blocks("tool_call"),
    )

    result = await _resolve_durable_external_event(
        callback_client=callbacks,
        execution_store=store,
        session=session,
        request=request,
        leader=leader,
        bridge=bridge,
        event=event,
    )

    assert result is None
    assert session.state == "waiting_approval"
    assert callbacks.events == ["tool.requested"]


@pytest.mark.asyncio
async def test_leader_streaming_returns_external_pause_without_finalizing() -> None:
    from agentscope.event import RequireExternalExecutionEvent
    from agentscope.message import ToolCallBlock, ToolCallState
    from agentscope_runtime.app import RuntimeSession, _run_leader_streaming

    event = RequireExternalExecutionEvent(
        reply_id="reply-1",
        tool_calls=[
            ToolCallBlock(
                id="provider-call-1",
                name="write_file",
                input="{}",
                state=ToolCallState.SUBMITTED,
            )
        ],
    )

    class Leader:
        async def reply_stream(self, _inputs):
            yield event

    result = await _run_leader_streaming(
        Leader(),
        RuntimeSession(
            run_id="run-1",
            runtime_session_id="rt-run-1",
            state="running",
        ),
        [],
    )

    assert result.pause_event is event
    assert result.final_msg is None


@pytest.mark.asyncio
async def test_activation_dispatches_persisted_continuation_once(tmp_path: Path) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "runtime.sqlite3")
    store.save_checkpoint(_checkpoint(protocol))
    continued: list[str] = []

    async def apply_execution(checkpoint, execution):
        return protocol.RuntimeApplyResult(
            checkpoint=checkpoint.model_copy(
                update={
                    "state": "running",
                    "wait_kind": None,
                    "wait_subject_id": None,
                    "continuation_pending": True,
                }
            ),
            outcome={"kind": "applied"},
        )

    async def continue_execution(checkpoint, execution):
        continued.append(execution.execution_id)

    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        execution_applier=apply_execution,
        execution_continuation=continue_execution,
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(),
            headers=headers,
        )
        await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )
        await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )

    assert continued == ["rex-1"]


@pytest.mark.asyncio
async def test_default_continuation_restores_agent_state_and_finishes_run(tmp_path: Path) -> None:
    protocol = _protocol()
    from agentscope.message import UserMsg
    from agentscope.state import AgentState
    from agentscope_runtime.app import _build_default_execution_continuation

    class Callbacks:
        def __init__(self) -> None:
            self.events: list[str] = []
            self.final_deltas: list[str] = []

        async def call_model_stream(self, **kwargs):
            yield {
                "type": "chunk",
                "delta": {
                    "content": "continued final",
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
            yield {"type": "stream_end"}

        async def append_event(self, **kwargs):
            self.events.append(kwargs["event_type"])
            return {"seq": len(self.events)}

        async def append_final_delta(self, **kwargs):
            self.final_deltas.append(kwargs["delta"])
            return {"seq": len(self.final_deltas)}

        async def append_text_delta(self, **kwargs):
            return {"seq": 1}

    state = AgentState(context=[UserMsg(name="user", content="continue")])
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "runtime.sqlite3")
    checkpoint = _checkpoint(
        protocol,
        state="running",
        wait_kind=None,
        wait_subject_id=None,
        pending_call=None,
        agent_state=state.model_dump(mode="json"),
        run_request={
            "run_id": "run-1",
            "chat_id": "chat-1",
            "leader_model_id": "model-a",
            "messages": [],
        },
        continuation_pending=True,
    )
    store.save_checkpoint(checkpoint)
    execution = protocol.RuntimeExecutionRecord(
        execution_id="rex-1",
        run_id="run-1",
        runtime_session_id="rt-run-1",
        subject_id="approval-1",
        command_type="resume_approval",
        fingerprint="f" * 64,
        payload={"decision": "approved"},
        state="applied",
        checkpoint_version=1,
        created_at=1,
        updated_at=1,
    )
    callbacks = Callbacks()

    await _build_default_execution_continuation(callbacks, store)(checkpoint, execution)

    restored = store.get_checkpoint("run-1")
    assert restored is not None
    assert restored.state == "completed"
    assert restored.continuation_pending is False
    assert callbacks.events == ["final.started", "run.completed"]
    assert "".join(callbacks.final_deltas) == "continued final"


@pytest.mark.asyncio
async def test_cancel_endpoint_persists_cancel_and_blocks_late_prepare(tmp_path: Path) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    class Callbacks:
        async def append_event(self, **kwargs):
            return {"seq": 1}

    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "runtime.sqlite3")
    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        openwebui_client=Callbacks(),
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        start = await client.post(
            "/v1/openwebui/runs",
            headers=headers,
            json={"run_id": "run-1", "chat_id": "chat-1", "messages": []},
        )
        assert start.status_code == 202
        checkpoint = store.get_checkpoint("run-1")
        store.save_checkpoint(
            checkpoint.model_copy(
                update={
                    "state": "waiting_approval",
                    "checkpoint_version": checkpoint.checkpoint_version + 1,
                    "wait_kind": "approval",
                    "wait_subject_id": "approval-1",
                }
            )
        )
        cancel = await client.post("/v1/openwebui/runs/run-1/cancel", headers=headers)
        prepare = await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(checkpoint_version=1),
            headers=headers,
        )

    assert cancel.status_code == 200
    restored = store.get_checkpoint("run-1")
    assert restored.cancel_requested is True
    assert restored.state == "cancelled"
    assert prepare.status_code == 409
    assert prepare.json()["detail"]["code"] == "execution_cancelled"


@pytest.mark.asyncio
async def test_restart_fails_closed_for_inflight_approved_tool_but_reclaims_user_input(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    path = tmp_path / "runtime.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)
    store.save_checkpoint(_checkpoint(protocol))
    approved = protocol.RuntimeExecutionCommand.from_mapping(_prepare_body())
    store.prepare(approved, run_id="run-1")
    applying, owner = store.begin_apply("rex-1")
    assert owner is True and applying.state == "applying"

    calls: list[str] = []

    async def apply_execution(checkpoint, execution):
        calls.append(execution.execution_id)
        return protocol.RuntimeApplyResult(
            checkpoint=checkpoint,
            outcome={"kind": "applied"},
        )

    restarted = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=protocol.SQLiteRuntimeExecutionStore(path),
        execution_applier=apply_execution,
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://runtime.test"
    ) as client:
        response = await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )
    assert response.json()["state"] == "indeterminate"
    assert calls == []

    user_store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "user.sqlite3")
    user_store.save_checkpoint(
        _checkpoint(
            protocol,
            run_id="run-2",
            runtime_session_id="rt-run-2",
            state="waiting_user_input",
            wait_kind="user_input",
            wait_subject_id="input-2",
        )
    )
    body = _prepare_body(
        execution_id="rex-2",
        runtime_session_id="rt-run-2",
        subject_id="input-2",
        command_type="resume_user_input",
        payload={"status": "accepted", "content": {"answer": "yes"}},
    )
    user_store.prepare(protocol.RuntimeExecutionCommand.from_mapping(body), run_id="run-2")
    user_store.begin_apply("rex-2")
    user_app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=protocol.SQLiteRuntimeExecutionStore(tmp_path / "user.sqlite3"),
        execution_applier=apply_execution,
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=user_app), base_url="http://runtime.test"
    ) as client:
        recovered = await client.post(
            "/v1/openwebui/runs/run-2/executions/rex-2/activate", headers=headers
        )
    assert recovered.json()["state"] == "applied"
    assert calls == ["rex-2"]


@pytest.mark.asyncio
async def test_activation_protocol_error_becomes_terminal_failed_not_stuck_applying(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.app import create_app

    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "runtime.sqlite3")
    agent_state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="tool-call-1",
                        name="write_file",
                        input="{}",
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    store.save_checkpoint(
        _checkpoint(
            protocol,
            agent_state=agent_state.model_dump(mode="json"),
            run_request={
                "run_id": "run-1",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
            },
            pending_call={
                "participant_id": "leader",
                "tool_call_id": "tool-call-1",
                "tool_name": "write_file",
                "tool_id": "tool:test:write_file",
                "arguments": {},
                "idempotency_key": "tool:leader:tool-call-1:1",
            },
        )
    )
    body = _prepare_body(payload={"decision": "unexpected"})
    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1", json=body, headers=headers
        )
        response = await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )
        query = await client.get(
            "/v1/openwebui/runs/run-1/executions/rex-1", headers=headers
        )

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert query.json()["state"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "run_request",
        "agent_state",
        "pending_tool_call",
        "leader_model",
        "bridge_state",
        "pending_arguments",
        "pending_tool_id",
        "tool_schema",
    ],
)
async def test_activation_preflights_entire_continuation_before_tool_callback(
    tmp_path: Path,
    corruption: str,
) -> None:
    protocol = _protocol()
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.app import create_app

    class Callbacks:
        def __init__(self) -> None:
            self.tool_calls: list[dict] = []

        async def call_tool(self, **kwargs):
            self.tool_calls.append(kwargs)
            return {"status": "success", "content": "side effect happened"}

    state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="provider-call-1",
                        name="write_file",
                        input="{}",
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    checkpoint = _checkpoint(
        protocol,
        agent_state=state.model_dump(mode="json"),
        run_request={
            "run_id": "run-1",
            "chat_id": "chat-1",
            "leader_model_id": "model-a",
            "tool_access_envelope": {
                "tools": [
                    {
                        "id": "tool:terminal:main:write_file",
                        "name": "write_file",
                        "schema": {
                            "name": "write_file",
                            "description": "Write a file.",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ]
            },
        },
        pending_call={
            "participant_id": "leader",
            "tool_call_id": "provider-call-1",
            "tool_name": "write_file",
            "tool_id": "tool:terminal:main:write_file",
            "arguments": {},
            "idempotency_key": "tool:leader:provider-call-1:1",
        },
    )
    if corruption == "run_request":
        checkpoint = checkpoint.model_copy(update={"run_request": {"chat_id": "chat-1"}})
    elif corruption == "agent_state":
        checkpoint = checkpoint.model_copy(update={"agent_state": {"context": "corrupt"}})
    elif corruption == "pending_tool_call":
        checkpoint = checkpoint.model_copy(
            update={
                "pending_call": {
                    **checkpoint.pending_call,
                    "tool_call_id": "missing-provider-call",
                }
            }
        )
    elif corruption == "leader_model":
        checkpoint = checkpoint.model_copy(
            update={
                "run_request": {
                    "run_id": "run-1",
                    "chat_id": "chat-1",
                }
            }
        )
    elif corruption == "bridge_state":
        checkpoint = checkpoint.model_copy(
            update={"bridge_state": {"next_tool_call_index": "not-an-int"}}
        )
    elif corruption == "pending_arguments":
        checkpoint = checkpoint.model_copy(
            update={
                "pending_call": {
                    **checkpoint.pending_call,
                    "arguments": ["not", "an", "object"],
                }
            }
        )
    elif corruption == "pending_tool_id":
        checkpoint = checkpoint.model_copy(
            update={
                "pending_call": {
                    **checkpoint.pending_call,
                    "tool_id": "",
                }
            }
        )
    else:
        run_request = dict(checkpoint.run_request)
        run_request["tool_access_envelope"] = {
            "tools": [
                {
                    "id": "tool:terminal:main:write_file",
                    "name": "write_file",
                    "schema": {
                        "name": "write_file",
                        "description": "Write a file.",
                        "parameters": {"type": "not-a-json-schema-type"},
                    },
                }
            ]
        }
        checkpoint = checkpoint.model_copy(update={"run_request": run_request})

    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / f"{corruption}.sqlite3")
    store.save_checkpoint(checkpoint)
    callbacks = Callbacks()
    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        openwebui_client=callbacks,
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(),
            headers=headers,
        )
        activated = await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate",
            headers=headers,
        )

    assert activated.status_code == 200
    assert activated.json()["state"] == "unrecoverable"
    assert callbacks.tool_calls == []
    persisted = store.get_checkpoint("run-1")
    assert persisted is not None
    assert persisted.state == "waiting_approval"
    assert persisted.applied_execution_id is None


@pytest.mark.asyncio
async def test_durable_runtime_rejects_legacy_approval_decision_endpoint(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "runtime.sqlite3")
    store.save_checkpoint(_checkpoint(protocol))
    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        response = await client.post(
            "/v1/openwebui/runs/run-1/approval-decision",
            headers=headers,
            json={
                "approval_id": "approval-1",
                "decision": "rejected",
                "tool_call_id": "provider-call-1",
                "tool_id": "write_file",
                "tool_name": "write_file",
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "legacy_approval_endpoint_disabled"
    persisted = store.get_checkpoint("run-1")
    assert persisted is not None
    assert persisted.state == "waiting_approval"


@pytest.mark.asyncio
async def test_restart_status_and_cancel_use_durable_checkpoint_as_authority(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    path = tmp_path / "runtime.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)
    store.save_checkpoint(_checkpoint(protocol))
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}

    restarted = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=protocol.SQLiteRuntimeExecutionStore(path),
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://runtime.test"
    ) as client:
        status_response = await client.get(
            "/v1/openwebui/runs/run-1/status", headers=headers
        )
        cancel_response = await client.post(
            "/v1/openwebui/runs/run-1/cancel", headers=headers
        )

    assert status_response.status_code == 200
    assert status_response.json()["state"] == "waiting_approval"
    assert cancel_response.status_code == 200
    assert cancel_response.json()["state"] == "cancelled"
    assert cancel_response.json()["cancel_requested"] is True

    second_restart = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=protocol.SQLiteRuntimeExecutionStore(path),
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=second_restart),
        base_url="http://runtime.test",
    ) as client:
        persisted_status = await client.get(
            "/v1/openwebui/runs/run-1/status", headers=headers
        )

    assert persisted_status.status_code == 200
    assert persisted_status.json()["state"] == "cancelled"
    assert persisted_status.json()["cancel_requested"] is True


def test_every_sqlite_connection_explicitly_enables_full_synchronous(
    tmp_path: Path,
    monkeypatch,
) -> None:
    protocol = _protocol()
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "runtime.sqlite3")
    real_connect = protocol.sqlite3.connect
    statements: list[str] = []

    class TrackingConnection:
        def __init__(self, connection) -> None:
            self.connection = connection

        def execute(self, statement, *args, **kwargs):
            statements.append(statement)
            return self.connection.execute(statement, *args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        protocol.sqlite3,
        "connect",
        lambda *args, **kwargs: TrackingConnection(real_connect(*args, **kwargs)),
    )

    with store._connect() as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2

    assert "PRAGMA synchronous = FULL" in statements


@pytest.mark.asyncio
@pytest.mark.parametrize("general_agent", [False, True], ids=["ordinary", "general"])
async def test_no_pause_completion_persists_terminal_checkpoint_for_restart(
    tmp_path: Path,
    general_agent: bool,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    class Callbacks:
        def __init__(self, store) -> None:
            self.store = store
            self.finalizing_observed = False

        async def append_event(self, **kwargs):
            return {"seq": 1}

        async def append_final_delta(self, **kwargs):
            checkpoint = self.store.get_checkpoint(kwargs["run_id"])
            assert checkpoint is not None
            assert checkpoint.state == "finalizing"
            self.finalizing_observed = True
            return {"seq": 1}

        async def call_model(self, **kwargs):
            return {
                "status": "success",
                "model": kwargs["model"],
                "response": {"content": "ordinary final"},
            }

        async def call_model_stream(self, **kwargs):
            yield {
                "type": "chunk",
                "delta": {
                    "content": "general final",
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
            yield {"type": "stream_end"}

    path = tmp_path / f"{'general' if general_agent else 'ordinary'}.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)
    callbacks = Callbacks(store)
    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        openwebui_client=callbacks,
    )
    request = {
        "run_id": "run-1",
        "chat_id": "chat-1",
        "leader_model_id": "model-a",
        "messages": [{"role": "user", "content": "hello"}],
    }
    if general_agent:
        request["tool_access_envelope"] = {
            "tools": [
                {
                    "id": "tool:test:noop",
                    "name": "noop",
                    "schema": {
                        "name": "noop",
                        "description": "Do nothing.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        }
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        start = await client.post("/v1/openwebui/runs", json=request, headers=headers)
        assert start.status_code == 202
        for _ in range(100):
            checkpoint = store.get_checkpoint("run-1")
            if checkpoint is not None and checkpoint.state == "completed":
                break
            await asyncio.sleep(0.01)

    checkpoint = store.get_checkpoint("run-1")
    assert checkpoint is not None
    assert checkpoint.state == "completed"
    assert callbacks.finalizing_observed is True

    restarted = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=protocol.SQLiteRuntimeExecutionStore(path),
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://runtime.test"
    ) as client:
        status_response = await client.get(
            "/v1/openwebui/runs/run-1/status", headers=headers
        )
        cancel_response = await client.post(
            "/v1/openwebui/runs/run-1/cancel", headers=headers
        )

    assert status_response.status_code == 200
    assert status_response.json()["state"] == "completed"
    assert cancel_response.status_code == 409
    assert protocol.SQLiteRuntimeExecutionStore(path).get_checkpoint("run-1").state == "completed"


@pytest.mark.asyncio
async def test_failed_run_persists_terminal_checkpoint_for_restart_and_rejects_cancel(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    class Callbacks:
        async def append_event(self, **kwargs):
            return {"seq": 1}

        async def call_model(self, **kwargs):
            raise RuntimeError("model failed")

    path = tmp_path / "failed.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)
    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        openwebui_client=Callbacks(),
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        start = await client.post(
            "/v1/openwebui/runs",
            headers=headers,
            json={
                "run_id": "run-1",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "fail"}],
            },
        )
        assert start.status_code == 202
        for _ in range(100):
            checkpoint = store.get_checkpoint("run-1")
            if checkpoint is not None and checkpoint.state == "failed":
                break
            await asyncio.sleep(0.01)

    checkpoint = store.get_checkpoint("run-1")
    assert checkpoint is not None
    assert checkpoint.state == "failed"

    restarted = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=protocol.SQLiteRuntimeExecutionStore(path),
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://runtime.test"
    ) as client:
        status_response = await client.get(
            "/v1/openwebui/runs/run-1/status", headers=headers
        )
        cancel_response = await client.post(
            "/v1/openwebui/runs/run-1/cancel", headers=headers
        )

    assert status_response.status_code == 200
    assert status_response.json()["state"] == "failed"
    assert cancel_response.status_code == 409
    assert protocol.SQLiteRuntimeExecutionStore(path).get_checkpoint("run-1").state == "failed"


@pytest.mark.asyncio
async def test_start_callback_failure_persists_failed_checkpoint(tmp_path: Path) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    class Callbacks:
        async def append_event(self, **kwargs):
            raise RuntimeError("run.running callback failed")

    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "start-failed.sqlite3")
    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        openwebui_client=Callbacks(),
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={"run_id": "run-1", "chat_id": "chat-1", "messages": []},
        )

    assert response.status_code == 502
    checkpoint = store.get_checkpoint("run-1")
    assert checkpoint is not None
    assert checkpoint.state == "failed"


def test_checkpoint_cas_rejects_stale_write_after_cancel(tmp_path: Path) -> None:
    protocol = _protocol()
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "cas.sqlite3")
    running = _checkpoint(
        protocol,
        state="running",
        wait_kind=None,
        wait_subject_id=None,
        checkpoint_version=1,
    )
    store.save_checkpoint(running)
    cancelled = store.cancel_checkpoint("run-1")

    with pytest.raises(protocol.RuntimeExecutionCancelled):
        store.save_checkpoint_cas(
            running.model_copy(update={"state": "completed"}),
            expected_version=1,
            expected_states={"running", "finalizing"},
        )

    persisted = store.get_checkpoint("run-1")
    assert persisted == cancelled
    assert persisted.cancel_requested is True


@pytest.mark.asyncio
async def test_cancel_wins_against_running_durable_continuation(tmp_path: Path) -> None:
    protocol = _protocol()
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.app import RuntimeStore, create_app

    model_started = asyncio.Event()
    model_cancelled = asyncio.Event()
    release_model = asyncio.Event()

    class Callbacks:
        async def call_tool(self, **kwargs):
            return {"status": "success", "content": "tool result"}

        async def call_model_stream(self, **kwargs):
            model_started.set()
            try:
                await release_model.wait()
            except asyncio.CancelledError:
                model_cancelled.set()
                raise
            yield {
                "type": "chunk",
                "delta": {
                    "content": "late final",
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
            yield {"type": "stream_end"}

        async def append_event(self, **kwargs):
            return {"seq": 1}

        async def append_final_delta(self, **kwargs):
            return {"seq": 1}

        async def append_text_delta(self, **kwargs):
            return {"seq": 1}

    state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="provider-call-1",
                        name="write_file",
                        input="{}",
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "race.sqlite3")
    store.save_checkpoint(
        _checkpoint(
            protocol,
            agent_state=state.model_dump(mode="json"),
            run_request={
                "run_id": "run-1",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
            },
            pending_call={
                "participant_id": "leader",
                "tool_call_id": "provider-call-1",
                "tool_name": "write_file",
                "tool_id": "tool:test:write_file",
                "arguments": {},
                "idempotency_key": "tool:leader:provider-call-1:1",
            },
        )
    )
    runtime_store = RuntimeStore()
    app = create_app(
        service_token=SERVICE_TOKEN,
        store=runtime_store,
        execution_store=store,
        openwebui_client=Callbacks(),
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(),
            headers=headers,
        )
        activate_task = asyncio.create_task(
            client.post(
                "/v1/openwebui/runs/run-1/executions/rex-1/activate",
                headers=headers,
            )
        )
        await asyncio.wait_for(model_started.wait(), timeout=2)
        cancel = await client.post("/v1/openwebui/runs/run-1/cancel", headers=headers)
        try:
            await asyncio.wait_for(model_cancelled.wait(), timeout=0.5)
            cancellation_reached_continuation = True
        except TimeoutError:
            cancellation_reached_continuation = False
        finally:
            release_model.set()
        activate = await asyncio.wait_for(activate_task, timeout=2)
        await asyncio.sleep(0.05)

    assert activate.status_code == 200
    assert cancel.status_code == 200
    assert cancellation_reached_continuation is True
    persisted = store.get_checkpoint("run-1")
    assert persisted is not None
    assert persisted.state == "cancelled"
    assert persisted.cancel_requested is True
    assert persisted.continuation_pending is False


@pytest.mark.asyncio
async def test_continuation_does_not_replace_session_cancelled_before_task_start(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope.message import UserMsg
    from agentscope.state import AgentState
    from agentscope_runtime.app import (
        RuntimeSession,
        RuntimeStore,
        _build_default_execution_continuation,
    )

    class Callbacks:
        def __init__(self) -> None:
            self.model_calls = 0

        async def call_model_stream(self, **kwargs):
            self.model_calls += 1
            raise AssertionError("cancelled continuation must not call the model")
            yield {"type": "stream_end"}

    runtime_store = RuntimeStore()
    runtime_store.restore(
        RuntimeSession(
            run_id="run-1",
            runtime_session_id="rt-run-1",
            state="cancelled",
            cancel_requested=True,
            start_accepted=True,
        )
    )
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "pre-cancelled.sqlite3")
    state = AgentState(context=[UserMsg(name="user", content="continue")])
    checkpoint = _checkpoint(
        protocol,
        state="cancelled",
        wait_kind=None,
        wait_subject_id=None,
        cancel_requested=True,
        continuation_pending=False,
        agent_state=state.model_dump(mode="json"),
        run_request={
            "run_id": "run-1",
            "chat_id": "chat-1",
            "leader_model_id": "model-a",
        },
        pending_call=None,
    )
    store.save_checkpoint(checkpoint)
    execution = protocol.RuntimeExecutionRecord(
        execution_id="rex-1",
        run_id="run-1",
        runtime_session_id="rt-run-1",
        subject_id="approval-1",
        command_type="resume_approval",
        fingerprint="f" * 64,
        payload={"decision": "approved"},
        state="applied",
        checkpoint_version=checkpoint.checkpoint_version,
        created_at=1,
        updated_at=1,
    )
    callbacks = Callbacks()

    stale_checkpoint = checkpoint.model_copy(
        update={"state": "running", "cancel_requested": False}
    )
    await _build_default_execution_continuation(
        callbacks,
        store,
        runtime_store,
    )(stale_checkpoint, execution)

    assert callbacks.model_calls == 0
    persisted = store.get_checkpoint("run-1")
    assert persisted.state == "cancelled"
    assert persisted.cancel_requested is True


@pytest.mark.asyncio
async def test_cancel_endpoint_cancels_blocked_approved_activation_task(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.app import create_app

    tool_started = asyncio.Event()
    tool_cancelled = asyncio.Event()
    release_tool = asyncio.Event()

    class Callbacks:
        async def call_tool(self, **kwargs):
            tool_started.set()
            try:
                await release_tool.wait()
            except asyncio.CancelledError:
                tool_cancelled.set()
                raise
            return {"status": "success", "content": "must not complete"}

    state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="provider-call-1",
                        name="write_file",
                        input="{}",
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "cancel-activation.sqlite3")
    store.save_checkpoint(
        _checkpoint(
            protocol,
            agent_state=state.model_dump(mode="json"),
            run_request={
                "run_id": "run-1",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "tool_access_envelope": {
                    "tools": [
                        {
                            "id": "tool:test:write_file",
                            "name": "write_file",
                            "schema": {
                                "name": "write_file",
                                "description": "Write a file.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {},
                                },
                            },
                        }
                    ]
                },
            },
            pending_call={
                "participant_id": "leader",
                "tool_call_id": "provider-call-1",
                "tool_name": "write_file",
                "tool_id": "tool:test:write_file",
                "arguments": {},
                "idempotency_key": "tool:leader:provider-call-1:1",
            },
        )
    )
    app = create_app(
        service_token=SERVICE_TOKEN,
        openwebui_client=Callbacks(),
        execution_store=store,
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://runtime.test",
    ) as client:
        prepared = await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(),
            headers=headers,
        )
        assert prepared.status_code == 202
        activation = asyncio.create_task(
            client.post(
                "/v1/openwebui/runs/run-1/executions/rex-1/activate",
                headers=headers,
            )
        )
        await asyncio.wait_for(tool_started.wait(), timeout=1)
        cancelled = await client.post(
            "/v1/openwebui/runs/run-1/cancel",
            headers=headers,
        )
        try:
            await asyncio.wait_for(tool_cancelled.wait(), timeout=0.2)
        except TimeoutError:
            release_tool.set()
            await activation
            pytest.fail("cancel endpoint did not cancel the active tool callback")
        with pytest.raises(asyncio.CancelledError):
            await activation

    assert cancelled.status_code == 200
    checkpoint = store.get_checkpoint("run-1")
    execution = store.get_execution("rex-1")
    assert checkpoint is not None
    assert checkpoint.state == "cancelled"
    assert checkpoint.cancel_requested is True
    assert checkpoint.continuation_pending is False
    assert execution is not None
    assert execution.state == "cancelled"


@pytest.mark.asyncio
async def test_activate_returns_before_background_continuation_finishes(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "background.sqlite3")
    store.save_checkpoint(_checkpoint(protocol))
    continuation_started = asyncio.Event()
    release_continuation = asyncio.Event()

    async def apply_execution(checkpoint, execution):
        return protocol.RuntimeApplyResult(
            checkpoint=checkpoint.model_copy(
                update={
                    "state": "running",
                    "wait_kind": None,
                    "wait_subject_id": None,
                    "continuation_pending": True,
                }
            ),
            outcome={"kind": "applied"},
        )

    async def continue_execution(checkpoint, execution):
        continuation_started.set()
        await release_continuation.wait()

    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        execution_applier=apply_execution,
        execution_continuation=continue_execution,
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(),
            headers=headers,
        )
        started_at = asyncio.get_running_loop().time()
        response = await asyncio.wait_for(
            client.post(
                "/v1/openwebui/runs/run-1/executions/rex-1/activate",
                headers=headers,
            ),
            timeout=0.25,
        )
        elapsed = asyncio.get_running_loop().time() - started_at
        await asyncio.wait_for(continuation_started.wait(), timeout=0.25)
        release_continuation.set()

    assert response.status_code == 200
    assert response.json()["state"] == "applied"
    assert elapsed < 0.25


@pytest.mark.asyncio
@pytest.mark.parametrize("first_failure", ["error", "cancelled"])
async def test_failed_background_continuation_is_retryable_on_next_activate(
    tmp_path: Path,
    first_failure: str,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    store = protocol.SQLiteRuntimeExecutionStore(
        tmp_path / f"retry-{first_failure}.sqlite3",
        max_terminal_executions=1,
    )
    store.save_checkpoint(_checkpoint(protocol))
    first_finished = asyncio.Event()
    second_finished = asyncio.Event()
    attempts = 0

    async def apply_execution(checkpoint, execution):
        return protocol.RuntimeApplyResult(
            checkpoint=checkpoint.model_copy(
                update={
                    "state": "running",
                    "wait_kind": None,
                    "wait_subject_id": None,
                    "continuation_pending": True,
                }
            ),
            outcome={"kind": "applied"},
        )

    async def continue_execution(checkpoint, execution):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            first_finished.set()
            if first_failure == "cancelled":
                raise asyncio.CancelledError
            raise RuntimeError("continuation construction failed")
        latest = store.get_checkpoint(checkpoint.run_id)
        store.save_checkpoint_cas(
            latest.model_copy(update={"continuation_pending": False}),
            expected_version=latest.checkpoint_version,
            expected_states={latest.state},
        )
        second_finished.set()

    app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=store,
        execution_applier=apply_execution,
        execution_continuation=continue_execution,
        auto_finalize_ordinary_qa=False,
    )
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        await client.put(
            "/v1/openwebui/runs/run-1/executions/rex-1",
            json=_prepare_body(),
            headers=headers,
        )
        first = await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )
        await asyncio.wait_for(first_finished.wait(), timeout=0.5)
        await asyncio.sleep(0)
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                """
                INSERT INTO runtime_execution(
                    execution_id, run_id, runtime_session_id, subject_id,
                    command_type, fingerprint, payload_json, state,
                    checkpoint_version, outcome_json, error_json,
                    created_at, updated_at
                ) VALUES (
                    'rex-newer-terminal', 'run-newer-terminal',
                    'rt-newer-terminal', 'approval-newer-terminal',
                    'resume_approval', ?, '{}', 'failed', 1, NULL, '{}', ?, ?
                )
                """,
                ("f" * 64, time.time() + 10, time.time() + 10),
            )
        store.cleanup_terminal_executions()
        assert store.get_execution("rex-1") is not None
        second = await client.post(
            "/v1/openwebui/runs/run-1/executions/rex-1/activate", headers=headers
        )
        await asyncio.wait_for(second_finished.wait(), timeout=0.5)

    assert first.status_code == 200
    assert second.status_code == 200
    assert attempts == 2
    assert store.get_checkpoint("run-1").continuation_pending is False


@pytest.mark.asyncio
async def test_immediate_external_result_survives_crash_before_agent_injection(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope.event import RequireExternalExecutionEvent
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge
    from agentscope_runtime.app import (
        RuntimeSession,
        _recover_persisted_external_result,
        _resolve_durable_external_event,
    )
    from agentscope_runtime.schemas import RunStartRequest

    class SimulatedCrash(BaseException):
        pass

    class Callbacks:
        def __init__(self) -> None:
            self.tool_calls = 0

        async def append_event(self, **kwargs):
            return {"seq": 1}

        async def call_tool(self, **kwargs):
            self.tool_calls += 1
            return {"status": "success", "content": "durable tool result"}

    request = RunStartRequest(
        run_id="run-1",
        chat_id="chat-1",
        leader_model_id="model-a",
        tool_access_envelope={
            "tools": [
                {
                    "id": "tool:test:write_file",
                    "name": "write_file",
                    "schema": {
                        "name": "write_file",
                        "description": "Write a file.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        },
    )
    session = RuntimeSession(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        state="running",
        request=request,
    )
    agent_state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="provider-call-1",
                        name="write_file",
                        input="{}",
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    leader = type("Leader", (), {"state": agent_state})()
    callbacks = Callbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        callback_client=callbacks,
        durable_external_tools=True,
    )
    event = RequireExternalExecutionEvent(
        reply_id="reply-1",
        tool_calls=agent_state.context[-1].get_content_blocks("tool_call"),
    )
    path = tmp_path / "external-result.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)

    def crash_after_persist() -> None:
        raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        await _resolve_durable_external_event(
            callback_client=callbacks,
            execution_store=store,
            session=session,
            request=request,
            leader=leader,
            bridge=bridge,
            event=event,
            crash_after_external_result_persist=crash_after_persist,
        )

    assert callbacks.tool_calls == 1
    reopened = protocol.SQLiteRuntimeExecutionStore(path)
    persisted = reopened.get_checkpoint("run-1")
    assert persisted is not None
    assert persisted.external_result["tool_call_id"] == "provider-call-1"
    assert persisted.external_result["execution_id"].startswith("external:")

    recovered_event = _recover_persisted_external_result(reopened, persisted)
    recovered_again = _recover_persisted_external_result(
        reopened, reopened.get_checkpoint("run-1")
    )
    assert recovered_event is not None
    assert recovered_again is not None
    recovered = reopened.get_checkpoint("run-1")
    restored_state = AgentState.model_validate(recovered.agent_state)
    results = restored_state.context[-1].get_content_blocks("tool_result")
    assert [result.id for result in results] == ["provider-call-1"]
    assert results[0].output == "durable tool result"
    assert recovered.state == "running"
    assert recovered.continuation_pending is True
    assert callbacks.tool_calls == 1

    continuation_started = asyncio.Event()

    async def unused_apply(checkpoint, execution):
        raise AssertionError("restart must not re-apply the external tool")

    async def continue_recovered(checkpoint, execution):
        assert execution.execution_id == recovered.external_result["execution_id"]
        continuation_started.set()

    from agentscope_runtime.app import create_app

    restarted_app = create_app(
        service_token=SERVICE_TOKEN,
        execution_store=reopened,
        execution_applier=unused_apply,
        execution_continuation=continue_recovered,
        auto_finalize_ordinary_qa=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted_app),
        base_url="http://runtime.test",
    ) as client:
        start = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json=request.model_dump(mode="json"),
        )
        assert start.status_code == 202
        await asyncio.wait_for(continuation_started.wait(), timeout=0.5)

    assert callbacks.tool_calls == 1


@pytest.mark.asyncio
async def test_initial_external_tool_real_client_has_checkpoint_but_no_decision_header(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope.event import RequireExternalExecutionEvent
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge
    from agentscope_runtime.app import RuntimeSession, _resolve_durable_external_event
    from agentscope_runtime.openwebui_client import OpenWebUIClient
    from agentscope_runtime.schemas import RunStartRequest

    request = RunStartRequest(
        run_id="run-1",
        chat_id="chat-1",
        leader_model_id="model-a",
        tool_access_envelope={
            "tools": [
                {
                    "id": "tool:test:write_file",
                    "name": "write_file",
                    "schema": {
                        "name": "write_file",
                        "description": "Write a file.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        },
    )
    session = RuntimeSession(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        state="running",
        request=request,
    )
    state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="provider-call-1",
                        name="write_file",
                        input="{}",
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    leader = type("Leader", (), {"state": state})()
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="runtime-secret",
    )
    bridge = AgentScopeRuntimeBridge(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        callback_client=client,
        durable_external_tools=True,
    )
    event = RequireExternalExecutionEvent(
        reply_id="reply-1",
        tool_calls=state.context[-1].get_content_blocks("tool_call"),
    )
    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "real-client.sqlite3")

    async with respx.mock(assert_all_called=True) as router:
        router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/events"
        ).mock(return_value=httpx.Response(200, json={"seq": 1}))
        tool_request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/tool-call"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "content": "written"},
            )
        )

        await _resolve_durable_external_event(
            callback_client=client,
            execution_store=store,
            session=session,
            request=request,
            leader=leader,
            bridge=bridge,
            event=event,
        )

    sent = tool_request.calls.last.request
    assert "x-agent-decision-execution-id" not in sent.headers
    assert json.loads(sent.content)["checkpoint_version"] == 1


@pytest.mark.asyncio
async def test_durable_user_input_callback_observes_persisted_checkpoint_version(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope.event import RequireExternalExecutionEvent
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge
    from agentscope_runtime.app import RuntimeSession, _resolve_durable_external_event
    from agentscope_runtime.schemas import RunStartRequest

    store = protocol.SQLiteRuntimeExecutionStore(tmp_path / "user-input-version.sqlite3")
    observed_versions: list[int] = []

    class Callbacks:
        async def request_user_input(self, *, checkpoint_version: int, **kwargs):
            persisted = store.get_checkpoint("run-1")
            assert persisted is not None
            assert persisted.checkpoint_version == checkpoint_version
            assert isinstance(checkpoint_version, int)
            observed_versions.append(checkpoint_version)
            return {"status": "requested"}

    request = RunStartRequest(
        run_id="run-1",
        chat_id="chat-1",
        leader_model_id="model-a",
    )
    session = RuntimeSession(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        state="running",
        request=request,
    )
    state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="provider-call-1",
                        name="request_user_input",
                        input=json.dumps({"message": "Choose one"}),
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    callbacks = Callbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        callback_client=callbacks,
        durable_external_tools=True,
    )

    result = await _resolve_durable_external_event(
        callback_client=callbacks,
        execution_store=store,
        session=session,
        request=request,
        leader=type("Leader", (), {"state": state})(),
        bridge=bridge,
        event=RequireExternalExecutionEvent(
            reply_id="reply-1",
            tool_calls=state.context[-1].get_content_blocks("tool_call"),
        ),
    )

    assert result is None
    assert observed_versions == [1]


def test_external_result_recovers_once_across_real_subprocess_crash_and_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "real-crash.sqlite3"
    tool_count = tmp_path / "tool-count.txt"
    service_root = Path(__file__).parents[1]
    crash_script = textwrap.dedent(
        """
        import asyncio
        import os
        import sys
        from pathlib import Path

        from agentscope.event import RequireExternalExecutionEvent
        from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
        from agentscope.state import AgentState
        from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge
        from agentscope_runtime.app import RuntimeSession, _resolve_durable_external_event
        from agentscope_runtime.execution_store import SQLiteRuntimeExecutionStore
        from agentscope_runtime.schemas import RunStartRequest

        class Callbacks:
            async def append_event(self, **kwargs):
                return {"seq": 1}

            async def call_tool(self, **kwargs):
                count_path = Path(sys.argv[2])
                count = int(count_path.read_text()) if count_path.exists() else 0
                count_path.write_text(str(count + 1))
                return {"status": "success", "content": "persisted before crash"}

        async def main():
            request = RunStartRequest(
                run_id="run-1",
                chat_id="chat-1",
                leader_model_id="model-a",
                tool_access_envelope={
                    "tools": [{
                        "id": "tool:test:write_file",
                        "name": "write_file",
                        "schema": {
                            "name": "write_file",
                            "description": "Write a file.",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }],
                },
            )
            session = RuntimeSession(
                run_id="run-1",
                runtime_session_id="rt-run-1",
                state="running",
                request=request,
            )
            state = AgentState(
                reply_id="reply-1",
                context=[AssistantMsg(
                    id="reply-1",
                    name="leader",
                    content=[ToolCallBlock(
                        id="provider-call-1",
                        name="write_file",
                        input="{}",
                        state=ToolCallState.SUBMITTED,
                    )],
                )],
            )
            leader = type("Leader", (), {"state": state})()
            callbacks = Callbacks()
            bridge = AgentScopeRuntimeBridge(
                run_id="run-1",
                runtime_session_id="rt-run-1",
                callback_client=callbacks,
                durable_external_tools=True,
            )
            event = RequireExternalExecutionEvent(
                reply_id="reply-1",
                tool_calls=state.context[-1].get_content_blocks("tool_call"),
            )
            await _resolve_durable_external_event(
                callback_client=callbacks,
                execution_store=SQLiteRuntimeExecutionStore(sys.argv[1]),
                session=session,
                request=request,
                leader=leader,
                bridge=bridge,
                event=event,
                crash_after_external_result_persist=lambda: os._exit(91),
            )

        asyncio.run(main())
        """
    )
    restart_script = textwrap.dedent(
        """
        import asyncio
        import json
        import sys
        from pathlib import Path

        import httpx
        from agentscope.state import AgentState
        from agentscope_runtime.app import create_app
        from agentscope_runtime.execution_store import (
            RuntimeApplyResult,
            SQLiteRuntimeExecutionStore,
        )

        async def main():
            store = SQLiteRuntimeExecutionStore(sys.argv[1])
            continuation_finished = asyncio.Event()
            continuation_calls = 0

            class Callbacks:
                async def call_tool(self, **kwargs):
                    raise AssertionError("restart must not execute the persisted tool again")

            async def unused_apply(checkpoint, execution):
                raise AssertionError("restart must not re-apply an external execution")

            async def continue_execution(checkpoint, execution):
                nonlocal continuation_calls
                continuation_calls += 1
                latest = store.get_checkpoint(checkpoint.run_id)
                state = AgentState.model_validate(latest.agent_state)
                results = state.context[-1].get_content_blocks("tool_result")
                assert [result.id for result in results] == ["provider-call-1"]
                store.save_checkpoint_cas(
                    latest.model_copy(update={
                        "state": "completed",
                        "continuation_pending": False,
                        "outcome": {"kind": "completed"},
                    }),
                    expected_version=latest.checkpoint_version,
                    expected_states={latest.state},
                )
                continuation_finished.set()

            app = create_app(
                service_token="runtime-secret",
                openwebui_client=Callbacks(),
                execution_store=store,
                execution_applier=unused_apply,
                execution_continuation=continue_execution,
                auto_finalize_ordinary_qa=False,
            )
            checkpoint = store.get_checkpoint("run-1")
            request = checkpoint.run_request
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://runtime.test",
            ) as client:
                response = await client.post(
                    "/v1/openwebui/runs",
                    headers={"Authorization": "Bearer runtime-secret"},
                    json=request,
                )
                assert response.status_code == 202, response.text
                await asyncio.wait_for(continuation_finished.wait(), timeout=2)

            final = store.get_checkpoint("run-1")
            final_state = AgentState.model_validate(final.agent_state)
            results = final_state.context[-1].get_content_blocks("tool_result")
            print(json.dumps({
                "tool_calls": int(Path(sys.argv[2]).read_text()),
                "continuation_calls": continuation_calls,
                "checkpoint_state": final.state,
                "continuation_pending": final.continuation_pending,
                "result_ids": [result.id for result in results],
                "result_output": results[0].output,
            }))

        asyncio.run(main())
        """
    )

    crashed = subprocess.run(
        [sys.executable, "-c", crash_script, str(database), str(tool_count)],
        cwd=service_root,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert crashed.returncode == 91, (crashed.stdout, crashed.stderr)
    restarted = subprocess.run(
        [sys.executable, "-c", restart_script, str(database), str(tool_count)],
        cwd=service_root,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert restarted.returncode == 0, (restarted.stdout, restarted.stderr)
    outcome = json.loads(restarted.stdout.strip().splitlines()[-1])
    assert outcome == {
        "tool_calls": 1,
        "continuation_calls": 1,
        "checkpoint_state": "completed",
        "continuation_pending": False,
        "result_ids": ["provider-call-1"],
        "result_output": "persisted before crash",
    }


def test_startup_auto_resumes_applied_continuation_after_real_subprocess_crash(
    tmp_path: Path,
) -> None:
    database = tmp_path / "startup-resume.sqlite3"
    continuation_count = tmp_path / "continuation-count.txt"
    service_root = Path(__file__).parents[1]
    crash_script = textwrap.dedent(
        """
        import asyncio
        import hashlib
        import json
        import os
        import sys

        import httpx
        from agentscope_runtime.app import create_app
        from agentscope_runtime.execution_store import (
            RuntimeApplyResult,
            RuntimeCheckpoint,
            SQLiteRuntimeExecutionStore,
        )

        class CrashAfterApplyStore(SQLiteRuntimeExecutionStore):
            def complete_apply(self, execution_id, result):
                super().complete_apply(execution_id, result)
                os._exit(92)

        def prepare_body():
            payload = {"decision": "rejected"}
            canonical = json.dumps({
                "schema_version": 1,
                "execution_id": "rex-1",
                "runtime_session_id": "rt-run-1",
                "expected_checkpoint_version": 1,
                "subject_id": "approval-1",
                "command_type": "resume_approval",
                "payload": payload,
            }, sort_keys=True, separators=(",", ":"))
            return {
                "schema_version": 1,
                "execution_id": "rex-1",
                "runtime_session_id": "rt-run-1",
                "expected_checkpoint_version": 1,
                "subject_id": "approval-1",
                "command_type": "resume_approval",
                "payload": payload,
                "fingerprint": hashlib.sha256(canonical.encode()).hexdigest(),
            }

        async def apply_execution(checkpoint, execution):
            return RuntimeApplyResult(
                checkpoint=checkpoint.model_copy(update={
                    "state": "running",
                    "wait_kind": None,
                    "wait_subject_id": None,
                    "continuation_pending": True,
                }),
                outcome={"kind": "applied"},
            )

        async def main():
            store = CrashAfterApplyStore(sys.argv[1])
            store.save_checkpoint(RuntimeCheckpoint(
                run_id="run-1",
                runtime_session_id="rt-run-1",
                state="waiting_approval",
                checkpoint_version=1,
                wait_kind="approval",
                wait_subject_id="approval-1",
                run_request={"run_id": "run-1", "chat_id": "chat-1"},
            ))
            app = create_app(
                service_token="runtime-secret",
                execution_store=store,
                execution_applier=apply_execution,
                auto_finalize_ordinary_qa=False,
            )
            headers = {"Authorization": "Bearer runtime-secret"}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://runtime.test",
            ) as client:
                prepared = await client.put(
                    "/v1/openwebui/runs/run-1/executions/rex-1",
                    headers=headers,
                    json=prepare_body(),
                )
                assert prepared.status_code == 202, prepared.text
                await client.post(
                    "/v1/openwebui/runs/run-1/executions/rex-1/activate",
                    headers=headers,
                )

        asyncio.run(main())
        """
    )
    restart_script = textwrap.dedent(
        """
        import asyncio
        import json
        import sys
        import threading
        from pathlib import Path

        from fastapi.testclient import TestClient
        from agentscope_runtime.app import create_app
        from agentscope_runtime.execution_store import SQLiteRuntimeExecutionStore

        store = SQLiteRuntimeExecutionStore(sys.argv[1])
        continuation_finished = threading.Event()

        async def unused_apply(checkpoint, execution):
            raise AssertionError("startup recovery must not re-apply execution")

        async def continue_execution(checkpoint, execution):
            count_path = Path(sys.argv[2])
            count = int(count_path.read_text()) if count_path.exists() else 0
            count_path.write_text(str(count + 1))
            latest = store.get_checkpoint(checkpoint.run_id)
            store.save_checkpoint_cas(
                latest.model_copy(update={
                    "state": "completed",
                    "continuation_pending": False,
                }),
                expected_version=latest.checkpoint_version,
                expected_states={latest.state},
            )
            continuation_finished.set()

        app = create_app(
            service_token="runtime-secret",
            execution_store=store,
            execution_applier=unused_apply,
            execution_continuation=continue_execution,
            auto_finalize_ordinary_qa=False,
        )
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert continuation_finished.wait(2), "startup did not resume continuation"

        final = store.get_checkpoint("run-1")
        execution = store.get_execution("rex-1")
        print(json.dumps({
            "continuation_calls": int(Path(sys.argv[2]).read_text()),
            "checkpoint_state": final.state,
            "continuation_pending": final.continuation_pending,
            "execution_state": execution.state,
            "continuation_state": execution.continuation_state,
        }))
        """
    )

    crashed = subprocess.run(
        [sys.executable, "-c", crash_script, str(database)],
        cwd=service_root,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert crashed.returncode == 92, (crashed.stdout, crashed.stderr)
    restarted = subprocess.run(
        [
            sys.executable,
            "-c",
            restart_script,
            str(database),
            str(continuation_count),
        ],
        cwd=service_root,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert restarted.returncode == 0, (restarted.stdout, restarted.stderr)
    assert json.loads(restarted.stdout.strip().splitlines()[-1]) == {
        "continuation_calls": 1,
        "checkpoint_state": "completed",
        "continuation_pending": False,
        "execution_state": "applied",
        "continuation_state": "completed",
    }


@pytest.mark.parametrize(
    ("checkpoint_damage", "error_code"),
    [
        ("corrupt", "checkpoint_unrecoverable"),
        ("missing", "checkpoint_not_found"),
    ],
)
def test_startup_fail_closes_unrecoverable_pending_continuation_once(
    tmp_path: Path,
    checkpoint_damage: str,
    error_code: str,
) -> None:
    protocol = _protocol()
    from agentscope_runtime.app import create_app

    path = tmp_path / f"startup-{checkpoint_damage}.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)
    checkpoint = _checkpoint(protocol)
    store.save_checkpoint(checkpoint)
    command = protocol.RuntimeExecutionCommand.from_mapping(
        _prepare_body(payload={"decision": "rejected"})
    )
    store.prepare(command, run_id=checkpoint.run_id)
    applying, owner = store.begin_apply(command.execution_id)
    assert owner is True
    store.complete_apply(
        applying.execution_id,
        protocol.RuntimeApplyResult(
            checkpoint=checkpoint.model_copy(
                update={
                    "state": "running",
                    "wait_kind": None,
                    "wait_subject_id": None,
                    "continuation_pending": True,
                }
            ),
            outcome={"kind": "applied"},
        ),
    )
    with sqlite3.connect(path) as connection:
        if checkpoint_damage == "corrupt":
            connection.execute(
                "UPDATE runtime_checkpoint SET checkpoint_json='{broken' WHERE run_id='run-1'"
            )
        else:
            connection.execute("DELETE FROM runtime_checkpoint WHERE run_id='run-1'")

    class Callbacks:
        def __init__(self) -> None:
            self.events: list[dict] = []

        async def append_event(self, **kwargs):
            self.events.append(kwargs)
            return {"seq": len(self.events)}

    callbacks = Callbacks()

    async def unused_apply(checkpoint, execution):
        raise AssertionError("startup recovery must not re-apply execution")

    async def unused_continuation(checkpoint, execution):
        raise AssertionError("unrecoverable startup recovery must fail close")

    for _restart in range(2):
        reopened = protocol.SQLiteRuntimeExecutionStore(path)
        app = create_app(
            service_token=SERVICE_TOKEN,
            openwebui_client=callbacks,
            execution_store=reopened,
            execution_applier=unused_apply,
            execution_continuation=unused_continuation,
            auto_finalize_ordinary_qa=False,
        )
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200

    assert len(callbacks.events) == 1
    failed = callbacks.events[0]
    assert failed["event_type"] == "run.failed"
    assert failed["idempotency_key"] == (
        "evt:rt-run-1:continuation-recovery-failed:rex-1"
    )
    assert failed["payload"]["execution_id"] == "rex-1"
    assert failed["payload"]["error"]["code"] == error_code
    execution = protocol.SQLiteRuntimeExecutionStore(path).get_execution("rex-1")
    assert execution is not None
    assert execution.state == "unrecoverable"
    assert execution.continuation_state == "failed"


@pytest.mark.asyncio
async def test_uncertain_immediate_external_callback_becomes_indeterminate_without_retry(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    from agentscope.event import RequireExternalExecutionEvent
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
    from agentscope.state import AgentState
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge
    from agentscope_runtime.app import RuntimeSession, _resolve_durable_external_event
    from agentscope_runtime.schemas import RunStartRequest

    class Callbacks:
        def __init__(self) -> None:
            self.tool_calls = 0

        async def append_event(self, **kwargs):
            return {"seq": 1}

        async def call_tool(self, **kwargs):
            self.tool_calls += 1
            raise TimeoutError("callback outcome unknown")

    request = RunStartRequest(
        run_id="run-1",
        chat_id="chat-1",
        leader_model_id="model-a",
        tool_access_envelope={
            "tools": [
                {
                    "id": "tool:test:write_file",
                    "name": "write_file",
                    "schema": {
                        "name": "write_file",
                        "description": "Write a file.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        },
    )
    session = RuntimeSession(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        state="running",
        request=request,
    )
    state = AgentState(
        reply_id="reply-1",
        context=[
            AssistantMsg(
                id="reply-1",
                name="leader",
                content=[
                    ToolCallBlock(
                        id="provider-call-1",
                        name="write_file",
                        input="{}",
                        state=ToolCallState.SUBMITTED,
                    )
                ],
            )
        ],
    )
    leader = type("Leader", (), {"state": state})()
    callbacks = Callbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-1",
        runtime_session_id="rt-run-1",
        callback_client=callbacks,
        durable_external_tools=True,
    )
    event = RequireExternalExecutionEvent(
        reply_id="reply-1",
        tool_calls=state.context[-1].get_content_blocks("tool_call"),
    )
    path = tmp_path / "indeterminate.sqlite3"
    store = protocol.SQLiteRuntimeExecutionStore(path)

    with pytest.raises(protocol.ToolOutcomeIndeterminate):
        await _resolve_durable_external_event(
            callback_client=callbacks,
            execution_store=store,
            session=session,
            request=request,
            leader=leader,
            bridge=bridge,
            event=event,
        )

    persisted = protocol.SQLiteRuntimeExecutionStore(path).get_checkpoint("run-1")
    assert persisted.state == "indeterminate"
    assert persisted.external_result["status"] == "indeterminate"
    assert persisted.continuation_pending is False
    assert callbacks.tool_calls == 1
