from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised through the explicit guard
    fcntl = None  # type: ignore[assignment]

STORE_SCHEMA_VERSION = 1
DEFAULT_MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024
DEFAULT_TERMINAL_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_TERMINAL_EXECUTIONS = 10_000
DEFAULT_TERMINAL_CHECKPOINT_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_TERMINAL_CHECKPOINTS = 10_000

ExecutionState = Literal[
    "prepared",
    "activated",
    "applying",
    "applied",
    "cancelled",
    "failed",
    "indeterminate",
    "unrecoverable",
]
TERMINAL_EXECUTION_STATES = {
    "applied",
    "cancelled",
    "failed",
    "indeterminate",
    "unrecoverable",
}
TERMINAL_CHECKPOINT_STATES = {
    "completed",
    "failed",
    "cancelled",
    "indeterminate",
    "unrecoverable",
}


class RuntimeExecutionError(RuntimeError):
    code = "runtime_execution_error"


class RuntimeExecutionConflict(RuntimeExecutionError):
    code = "execution_conflict"


class RuntimeExecutionNotFound(RuntimeExecutionError):
    code = "execution_not_found"


class RuntimeCheckpointNotFound(RuntimeExecutionError):
    code = "checkpoint_not_found"


class RuntimeCheckpointUnrecoverable(RuntimeExecutionError):
    code = "checkpoint_unrecoverable"


class RuntimeExecutionUnrecoverable(RuntimeExecutionError):
    code = "execution_unrecoverable"


class RuntimeCheckpointTooLarge(RuntimeExecutionError):
    code = "checkpoint_too_large"


class RuntimeExecutionCancelled(RuntimeExecutionError):
    code = "execution_cancelled"


class RuntimeSessionMismatch(RuntimeExecutionError):
    code = "stale_runtime_session"


class RuntimeCheckpointVersionMismatch(RuntimeExecutionError):
    code = "stale_checkpoint"


class RuntimeWaitMismatch(RuntimeExecutionError):
    code = "stale_wait"


class ToolOutcomeIndeterminate(RuntimeExecutionError):
    code = "tool_outcome_indeterminate"


class RuntimeCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    runtime_session_id: str
    state: str
    checkpoint_version: int = Field(ge=0)
    wait_kind: Literal["approval", "user_input"] | None = None
    wait_subject_id: str | None = None
    agent_state: dict[str, Any] = Field(default_factory=dict)
    run_request: dict[str, Any] = Field(default_factory=dict)
    bridge_state: dict[str, Any] = Field(default_factory=dict)
    pending_call: dict[str, Any] | None = None
    external_result: dict[str, Any] | None = None
    applied_execution_id: str | None = None
    cancel_requested: bool = False
    outcome: dict[str, Any] | None = None
    continuation_pending: bool = False


class RuntimeExecutionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    execution_id: str
    runtime_session_id: str
    expected_checkpoint_version: int = Field(ge=0)
    subject_id: str
    command_type: Literal["resume_approval", "resume_user_input"]
    payload: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> RuntimeExecutionCommand:
        command = cls.model_validate(value)
        expected = command.expected_fingerprint()
        if command.fingerprint != expected:
            raise RuntimeExecutionConflict("execution fingerprint does not match request body")
        return command

    def expected_fingerprint(self) -> str:
        canonical = json.dumps(
            {
                "schema_version": self.schema_version,
                "execution_id": self.execution_id,
                "runtime_session_id": self.runtime_session_id,
                "expected_checkpoint_version": self.expected_checkpoint_version,
                "subject_id": self.subject_id,
                "command_type": self.command_type,
                "payload": self.payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class RuntimeExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    run_id: str
    runtime_session_id: str
    subject_id: str
    command_type: Literal["resume_approval", "resume_user_input"]
    fingerprint: str
    payload: dict[str, Any]
    state: ExecutionState
    checkpoint_version: int
    outcome: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    duplicate: bool = False
    created_at: float
    updated_at: float


class RuntimeApplyResult(BaseModel):
    checkpoint: RuntimeCheckpoint
    outcome: dict[str, Any]


class RuntimeExecutionStore(Protocol):
    def save_checkpoint(self, checkpoint: RuntimeCheckpoint) -> RuntimeCheckpoint: ...

    def save_checkpoint_cas(
        self,
        checkpoint: RuntimeCheckpoint,
        *,
        expected_version: int,
        expected_states: set[str],
    ) -> RuntimeCheckpoint: ...

    def get_checkpoint(self, run_id: str) -> RuntimeCheckpoint | None: ...

    def list_pending_continuations(self) -> list[RuntimeCheckpoint]: ...

    def cancel_checkpoint(self, run_id: str) -> RuntimeCheckpoint: ...

    def prepare(
        self,
        command: RuntimeExecutionCommand,
        *,
        run_id: str,
    ) -> RuntimeExecutionRecord: ...

    def get_execution(self, execution_id: str) -> RuntimeExecutionRecord | None: ...

    def begin_apply(self, execution_id: str) -> tuple[RuntimeExecutionRecord, bool]: ...

    def recover_inflight(self, execution_id: str) -> tuple[RuntimeExecutionRecord, bool]: ...

    def complete_apply(
        self,
        execution_id: str,
        result: RuntimeApplyResult,
    ) -> RuntimeExecutionRecord: ...

    def mark_terminal(
        self,
        execution_id: str,
        *,
        state: Literal["cancelled", "failed", "indeterminate", "unrecoverable"],
        error: dict[str, Any],
    ) -> RuntimeExecutionRecord: ...


class SQLiteRuntimeExecutionStore:
    def __init__(
        self,
        path: str | Path,
        *,
        max_checkpoint_bytes: int = DEFAULT_MAX_CHECKPOINT_BYTES,
        terminal_retention_seconds: int = DEFAULT_TERMINAL_RETENTION_SECONDS,
        max_terminal_executions: int = DEFAULT_MAX_TERMINAL_EXECUTIONS,
        terminal_checkpoint_retention_seconds: int = (
            DEFAULT_TERMINAL_CHECKPOINT_RETENTION_SECONDS
        ),
        max_terminal_checkpoints: int = DEFAULT_MAX_TERMINAL_CHECKPOINTS,
    ) -> None:
        self.path = Path(path)
        if max_checkpoint_bytes < 1:
            raise ValueError("max_checkpoint_bytes must be positive")
        if terminal_retention_seconds < 0:
            raise ValueError("terminal_retention_seconds cannot be negative")
        if max_terminal_executions < 1:
            raise ValueError("max_terminal_executions must be positive")
        if terminal_checkpoint_retention_seconds < 0:
            raise ValueError(
                "terminal_checkpoint_retention_seconds cannot be negative"
            )
        if max_terminal_checkpoints < 1:
            raise ValueError("max_terminal_checkpoints must be positive")
        self.max_checkpoint_bytes = max_checkpoint_bytes
        self.terminal_retention_seconds = terminal_retention_seconds
        self.max_terminal_executions = max_terminal_executions
        self.terminal_checkpoint_retention_seconds = (
            terminal_checkpoint_retention_seconds
        )
        self.max_terminal_checkpoints = max_terminal_checkpoints
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()
        self.cleanup_terminal_executions()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_schema (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_checkpoint (
                    run_id TEXT PRIMARY KEY,
                    runtime_session_id TEXT NOT NULL UNIQUE,
                    checkpoint_version INTEGER NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_execution (
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
                CREATE INDEX IF NOT EXISTS ix_runtime_execution_state_updated
                    ON runtime_execution(state, updated_at);
                """
            )
            row = connection.execute(
                "SELECT value FROM runtime_schema WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO runtime_schema(key, value) VALUES ('schema_version', ?)",
                    (str(STORE_SCHEMA_VERSION),),
                )
            else:
                try:
                    version = int(row["value"])
                except (TypeError, ValueError) as exc:
                    raise RuntimeCheckpointUnrecoverable(
                        "runtime store schema version is invalid"
                    ) from exc
                self._migrate_schema(connection, version)

    def _migrate_schema(
        self,
        connection: sqlite3.Connection,
        version: int,
    ) -> None:
        if version > STORE_SCHEMA_VERSION:
            raise RuntimeCheckpointUnrecoverable(
                f"runtime store schema {version} is newer than supported {STORE_SCHEMA_VERSION}"
            )
        while version < STORE_SCHEMA_VERSION:
            if version == 0:
                version = 1
                continue
            raise RuntimeCheckpointUnrecoverable(
                f"runtime store schema {version} has no migration path"
            )
        connection.execute(
            "UPDATE runtime_schema SET value=? WHERE key='schema_version'",
            (str(version),),
        )

    def _encode_checkpoint(self, checkpoint: RuntimeCheckpoint) -> str:
        encoded = checkpoint.model_dump_json()
        size = len(encoded.encode("utf-8"))
        if size > self.max_checkpoint_bytes:
            raise RuntimeCheckpointTooLarge(
                f"runtime checkpoint is {size} bytes; limit is {self.max_checkpoint_bytes}"
            )
        return encoded

    def save_checkpoint(self, checkpoint: RuntimeCheckpoint) -> RuntimeCheckpoint:
        encoded = self._encode_checkpoint(checkpoint)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT runtime_session_id, checkpoint_version FROM runtime_checkpoint WHERE run_id = ?",
                (checkpoint.run_id,),
            ).fetchone()
            if existing is not None:
                if existing["runtime_session_id"] != checkpoint.runtime_session_id:
                    raise RuntimeSessionMismatch("runtime session id changed for persisted run")
                if checkpoint.checkpoint_version < existing["checkpoint_version"]:
                    raise RuntimeCheckpointVersionMismatch("checkpoint version moved backwards")
            connection.execute(
                """
                INSERT INTO runtime_checkpoint(
                    run_id, runtime_session_id, checkpoint_version, checkpoint_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    runtime_session_id=excluded.runtime_session_id,
                    checkpoint_version=excluded.checkpoint_version,
                    checkpoint_json=excluded.checkpoint_json,
                    updated_at=excluded.updated_at
                """,
                (
                    checkpoint.run_id,
                    checkpoint.runtime_session_id,
                    checkpoint.checkpoint_version,
                    encoded,
                    now,
                ),
            )
            connection.commit()
        if (
            checkpoint.state in TERMINAL_CHECKPOINT_STATES
            and not checkpoint.continuation_pending
        ):
            self.cleanup_terminal_executions()
        return checkpoint

    def get_checkpoint(self, run_id: str) -> RuntimeCheckpoint | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT checkpoint_json FROM runtime_checkpoint WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return RuntimeCheckpoint.model_validate_json(row["checkpoint_json"])
        except Exception as exc:
            raise RuntimeCheckpointUnrecoverable(
                f"checkpoint for run {run_id} cannot be decoded"
            ) from exc

    def list_pending_continuations(self) -> list[RuntimeCheckpoint]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT checkpoint_json FROM runtime_checkpoint
                WHERE CASE
                    WHEN json_valid(checkpoint_json) = 1
                    THEN COALESCE(
                        json_extract(checkpoint_json, '$.continuation_pending'),
                        0
                    )
                    ELSE 0
                END = 1
                ORDER BY updated_at, run_id
                """
            ).fetchall()
        checkpoints: list[RuntimeCheckpoint] = []
        for row in rows:
            try:
                checkpoint = RuntimeCheckpoint.model_validate_json(
                    row["checkpoint_json"]
                )
            except Exception:
                continue
            if (
                checkpoint.continuation_pending
                and not checkpoint.cancel_requested
                and checkpoint.state not in TERMINAL_CHECKPOINT_STATES
            ):
                checkpoints.append(checkpoint)
        return checkpoints

    def save_checkpoint_cas(
        self,
        checkpoint: RuntimeCheckpoint,
        *,
        expected_version: int,
        expected_states: set[str],
    ) -> RuntimeCheckpoint:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load_checkpoint_row(connection, checkpoint.run_id)
            if current.runtime_session_id != checkpoint.runtime_session_id:
                raise RuntimeSessionMismatch(
                    "runtime session changed while saving checkpoint"
                )
            if current.state == "cancelled" or current.cancel_requested:
                raise RuntimeExecutionCancelled("runtime session was cancelled")
            if current.state in TERMINAL_CHECKPOINT_STATES:
                raise RuntimeExecutionConflict(
                    f"terminal runtime checkpoint {current.state} cannot be overwritten"
                )
            if current.checkpoint_version != expected_version:
                raise RuntimeCheckpointVersionMismatch(
                    "checkpoint version changed before compare-and-swap"
                )
            if current.state not in expected_states:
                raise RuntimeExecutionConflict(
                    f"checkpoint state changed to {current.state} before compare-and-swap"
                )
            updated = checkpoint.model_copy(
                update={"checkpoint_version": expected_version + 1}
            )
            encoded = self._encode_checkpoint(updated)
            connection.execute(
                """
                UPDATE runtime_checkpoint
                SET checkpoint_version=?, checkpoint_json=?, updated_at=?
                WHERE run_id=? AND checkpoint_version=?
                """,
                (
                    updated.checkpoint_version,
                    encoded,
                    now,
                    updated.run_id,
                    expected_version,
                ),
            )
            if connection.total_changes != 1:
                raise RuntimeCheckpointVersionMismatch(
                    "checkpoint compare-and-swap did not update exactly one row"
                )
            connection.commit()
        if (
            updated.state in TERMINAL_CHECKPOINT_STATES
            and not updated.continuation_pending
        ):
            self.cleanup_terminal_executions()
        return updated

    def cancel_checkpoint(self, run_id: str) -> RuntimeCheckpoint:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load_checkpoint_row(connection, run_id)
            if current.state == "cancelled":
                connection.commit()
                return current
            if current.state in TERMINAL_CHECKPOINT_STATES:
                raise RuntimeExecutionConflict(
                    f"terminal runtime checkpoint {current.state} cannot be cancelled"
                )
            cancelled = current.model_copy(
                update={
                    "state": "cancelled",
                    "cancel_requested": True,
                    "checkpoint_version": current.checkpoint_version + 1,
                    "continuation_pending": False,
                }
            )
            connection.execute(
                """
                UPDATE runtime_checkpoint
                SET checkpoint_version=?, checkpoint_json=?, updated_at=?
                WHERE run_id=?
                """,
                (
                    cancelled.checkpoint_version,
                    self._encode_checkpoint(cancelled),
                    now,
                    run_id,
                ),
            )
            connection.execute(
                """
                UPDATE runtime_execution
                SET state='cancelled', error_json=?, updated_at=?
                WHERE run_id=? AND state IN ('prepared', 'activated', 'applying')
                """,
                (_json({"code": RuntimeExecutionCancelled.code}), now, run_id),
            )
            connection.commit()
        self.cleanup_terminal_executions()
        return cancelled

    def prepare(
        self,
        command: RuntimeExecutionCommand,
        *,
        run_id: str,
    ) -> RuntimeExecutionRecord:
        if command.fingerprint != command.expected_fingerprint():
            raise RuntimeExecutionConflict("execution fingerprint does not match request body")
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM runtime_execution WHERE execution_id = ?",
                (command.execution_id,),
            ).fetchone()
            if existing is not None:
                record = self._record(existing, duplicate=True)
                if (
                    record.run_id != run_id
                    or record.runtime_session_id != command.runtime_session_id
                    or record.subject_id != command.subject_id
                    or record.command_type != command.command_type
                    or record.fingerprint != command.fingerprint
                    or record.payload != command.payload
                ):
                    raise RuntimeExecutionConflict(
                        "execution id was reused with a different command"
                    )
                connection.commit()
                return record

            checkpoint = self._load_checkpoint_row(connection, run_id)
            self._validate_prepare(checkpoint, command)
            try:
                connection.execute(
                    """
                    INSERT INTO runtime_execution(
                        execution_id, run_id, runtime_session_id, subject_id,
                        command_type, fingerprint, payload_json, state,
                        checkpoint_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?)
                    """,
                    (
                        command.execution_id,
                        run_id,
                        command.runtime_session_id,
                        command.subject_id,
                        command.command_type,
                        command.fingerprint,
                        _json(command.payload),
                        checkpoint.checkpoint_version,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeExecutionConflict(
                    "a different execution already owns this wait resource"
                ) from exc
            connection.commit()
            row = connection.execute(
                "SELECT * FROM runtime_execution WHERE execution_id = ?",
                (command.execution_id,),
            ).fetchone()
            assert row is not None
            return self._record(row)

    def get_execution(self, execution_id: str) -> RuntimeExecutionRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_execution WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        return self._record(row) if row is not None else None

    def begin_apply(self, execution_id: str) -> tuple[RuntimeExecutionRecord, bool]:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runtime_execution WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise RuntimeExecutionNotFound(execution_id)
            record = self._record(row)
            if record.state in TERMINAL_EXECUTION_STATES or record.state == "applying":
                connection.commit()
                return record.model_copy(update={"duplicate": True}), False
            if record.state not in {"prepared", "activated"}:
                raise RuntimeExecutionConflict(
                    f"execution {execution_id} cannot activate from {record.state}"
                )
            checkpoint = self._load_checkpoint_row(connection, record.run_id)
            if checkpoint.cancel_requested or checkpoint.state == "cancelled":
                connection.execute(
                    "UPDATE runtime_execution SET state='cancelled', error_json=?, updated_at=? WHERE execution_id=?",
                    (_json({"code": RuntimeExecutionCancelled.code}), now, execution_id),
                )
                connection.commit()
                cancelled = self.get_execution(execution_id)
                assert cancelled is not None
                return cancelled, False
            if checkpoint.applied_execution_id == execution_id:
                connection.execute(
                    """
                    UPDATE runtime_execution
                    SET state='applied', outcome_json=?, checkpoint_version=?, updated_at=?
                    WHERE execution_id=?
                    """,
                    (
                        _json(checkpoint.outcome or {"kind": "applied"}),
                        checkpoint.checkpoint_version,
                        now,
                        execution_id,
                    ),
                )
                connection.commit()
                applied = self.get_execution(execution_id)
                assert applied is not None
                return applied.model_copy(update={"duplicate": True}), False
            connection.execute(
                "UPDATE runtime_execution SET state='applying', updated_at=? WHERE execution_id=?",
                (now, execution_id),
            )
            connection.commit()
            applying = self.get_execution(execution_id)
            assert applying is not None
            return applying, True

    def recover_inflight(self, execution_id: str) -> tuple[RuntimeExecutionRecord, bool]:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runtime_execution WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise RuntimeExecutionNotFound(execution_id)
            record = self._record(row)
            if record.state != "applying":
                connection.commit()
                return record.model_copy(update={"duplicate": True}), False
            approval_was_executable = (
                record.command_type == "resume_approval"
                and record.payload.get("decision") == "approved"
            )
            if approval_was_executable:
                error = {
                    "code": ToolOutcomeIndeterminate.code,
                    "message": (
                        "runtime restarted while an approved external tool outcome "
                        "was not durably recorded"
                    ),
                }
                connection.execute(
                    """
                    UPDATE runtime_execution
                    SET state='indeterminate', error_json=?, updated_at=?
                    WHERE execution_id=?
                    """,
                    (_json(error), now, execution_id),
                )
                connection.commit()
                terminal = self.get_execution(execution_id)
                assert terminal is not None
                return terminal, False
            connection.execute(
                "UPDATE runtime_execution SET updated_at=? WHERE execution_id=?",
                (now, execution_id),
            )
            connection.commit()
            reclaimed = self.get_execution(execution_id)
            assert reclaimed is not None
            return reclaimed, True

    def complete_apply(
        self,
        execution_id: str,
        result: RuntimeApplyResult,
    ) -> RuntimeExecutionRecord:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runtime_execution WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise RuntimeExecutionNotFound(execution_id)
            record = self._record(row)
            if record.state == "applied":
                connection.commit()
                return record.model_copy(update={"duplicate": True})
            if record.state != "applying":
                raise RuntimeExecutionConflict(
                    f"execution {execution_id} cannot complete from {record.state}"
                )
            current = self._load_checkpoint_row(connection, record.run_id)
            if current.runtime_session_id != result.checkpoint.runtime_session_id:
                raise RuntimeSessionMismatch("runtime session changed while applying execution")
            if current.state == "cancelled" or current.cancel_requested:
                raise RuntimeExecutionCancelled("runtime session was cancelled")
            if current.state in TERMINAL_CHECKPOINT_STATES:
                raise RuntimeExecutionConflict(
                    f"terminal runtime checkpoint {current.state} cannot apply execution"
                )
            if current.checkpoint_version != record.checkpoint_version:
                raise RuntimeCheckpointVersionMismatch(
                    "checkpoint changed while applying execution"
                )
            next_checkpoint = result.checkpoint.model_copy(
                update={
                    "checkpoint_version": current.checkpoint_version + 1,
                    "applied_execution_id": execution_id,
                    "outcome": result.outcome,
                }
            )
            connection.execute(
                """
                UPDATE runtime_checkpoint
                SET checkpoint_version=?, checkpoint_json=?, updated_at=?
                WHERE run_id=?
                """,
                (
                    next_checkpoint.checkpoint_version,
                    self._encode_checkpoint(next_checkpoint),
                    now,
                    next_checkpoint.run_id,
                ),
            )
            connection.execute(
                """
                UPDATE runtime_execution
                SET state='applied', outcome_json=?, error_json=NULL,
                    checkpoint_version=?, updated_at=?
                WHERE execution_id=?
                """,
                (
                    _json(result.outcome),
                    next_checkpoint.checkpoint_version,
                    now,
                    execution_id,
                ),
            )
            connection.commit()
        applied = self.get_execution(execution_id)
        assert applied is not None
        self.cleanup_terminal_executions()
        return applied

    def mark_terminal(
        self,
        execution_id: str,
        *,
        state: Literal["cancelled", "failed", "indeterminate", "unrecoverable"],
        error: dict[str, Any],
    ) -> RuntimeExecutionRecord:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runtime_execution WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise RuntimeExecutionNotFound(execution_id)
            record = self._record(row)
            if record.state in TERMINAL_EXECUTION_STATES:
                connection.commit()
                return record.model_copy(update={"duplicate": True})
            connection.execute(
                "UPDATE runtime_execution SET state=?, error_json=?, updated_at=? WHERE execution_id=?",
                (state, _json(error), now, execution_id),
            )
            connection.commit()
        terminal = self.get_execution(execution_id)
        assert terminal is not None
        self.cleanup_terminal_executions()
        return terminal

    def cleanup_terminal_executions(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        execution_cutoff = now - self.terminal_retention_seconds
        checkpoint_cutoff = now - self.terminal_checkpoint_retention_seconds
        terminal_execution_states = tuple(sorted(TERMINAL_EXECUTION_STATES))
        terminal_checkpoint_states = tuple(sorted(TERMINAL_CHECKPOINT_STATES))
        execution_placeholders = ",".join("?" for _ in terminal_execution_states)
        checkpoint_placeholders = ",".join("?" for _ in terminal_checkpoint_states)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = connection.total_changes
            connection.execute(
                f"""
                DELETE FROM runtime_execution
                WHERE state IN ({execution_placeholders})
                  AND updated_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM runtime_checkpoint
                      WHERE runtime_checkpoint.run_id = runtime_execution.run_id
                        AND (
                            CASE
                                WHEN json_valid(runtime_checkpoint.checkpoint_json) = 0
                                THEN 1
                                ELSE COALESCE(
                                    json_extract(
                                        runtime_checkpoint.checkpoint_json,
                                        '$.continuation_pending'
                                    ),
                                    0
                                )
                            END
                        ) = 1
                  )
                """,
                (*terminal_execution_states, execution_cutoff),
            )
            connection.execute(
                f"""
                DELETE FROM runtime_execution
                WHERE execution_id IN (
                    SELECT execution.execution_id FROM runtime_execution AS execution
                    WHERE execution.state IN ({execution_placeholders})
                      AND NOT EXISTS (
                          SELECT 1 FROM runtime_checkpoint
                          WHERE runtime_checkpoint.run_id = execution.run_id
                            AND (
                                CASE
                                    WHEN json_valid(runtime_checkpoint.checkpoint_json) = 0
                                    THEN 1
                                    ELSE COALESCE(
                                        json_extract(
                                            runtime_checkpoint.checkpoint_json,
                                            '$.continuation_pending'
                                        ),
                                        0
                                    )
                                END
                            ) = 1
                      )
                    ORDER BY updated_at DESC, execution_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (*terminal_execution_states, self.max_terminal_executions),
            )
            deleted_executions = connection.total_changes - before
            connection.execute(
                f"""
                DELETE FROM runtime_checkpoint
                WHERE updated_at < ?
                  AND CASE
                      WHEN json_valid(checkpoint_json) = 1
                      THEN json_extract(checkpoint_json, '$.state')
                  END IN ({checkpoint_placeholders})
                  AND CASE
                      WHEN json_valid(checkpoint_json) = 1
                      THEN COALESCE(
                          json_extract(checkpoint_json, '$.continuation_pending'),
                          0
                      )
                      ELSE 1
                  END = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM runtime_execution
                      WHERE runtime_execution.run_id = runtime_checkpoint.run_id
                        AND runtime_execution.state NOT IN ({execution_placeholders})
                  )
                """,
                (
                    checkpoint_cutoff,
                    *terminal_checkpoint_states,
                    *terminal_execution_states,
                ),
            )
            connection.execute(
                f"""
                DELETE FROM runtime_checkpoint
                WHERE run_id IN (
                    SELECT checkpoint.run_id
                    FROM runtime_checkpoint AS checkpoint
                    WHERE CASE
                        WHEN json_valid(checkpoint.checkpoint_json) = 1
                        THEN json_extract(
                            checkpoint.checkpoint_json,
                            '$.state'
                        )
                    END IN ({checkpoint_placeholders})
                      AND CASE
                          WHEN json_valid(checkpoint.checkpoint_json) = 1
                          THEN COALESCE(
                              json_extract(
                                  checkpoint.checkpoint_json,
                                  '$.continuation_pending'
                              ),
                              0
                          )
                          ELSE 1
                      END = 0
                      AND NOT EXISTS (
                          SELECT 1 FROM runtime_execution AS execution
                          WHERE execution.run_id = checkpoint.run_id
                            AND execution.state NOT IN ({execution_placeholders})
                      )
                    ORDER BY checkpoint.updated_at DESC, checkpoint.run_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (
                    *terminal_checkpoint_states,
                    *terminal_execution_states,
                    self.max_terminal_checkpoints,
                ),
            )
            connection.commit()
        return deleted_executions

    def _load_checkpoint_row(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> RuntimeCheckpoint:
        row = connection.execute(
            "SELECT checkpoint_json FROM runtime_checkpoint WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RuntimeCheckpointNotFound(f"no runtime checkpoint for run {run_id}")
        try:
            return RuntimeCheckpoint.model_validate_json(row["checkpoint_json"])
        except Exception as exc:
            raise RuntimeCheckpointUnrecoverable(
                f"checkpoint for run {run_id} cannot be decoded"
            ) from exc

    @staticmethod
    def _validate_prepare(
        checkpoint: RuntimeCheckpoint,
        command: RuntimeExecutionCommand,
    ) -> None:
        if checkpoint.cancel_requested or checkpoint.state == "cancelled":
            raise RuntimeExecutionCancelled("runtime session was cancelled")
        if checkpoint.runtime_session_id != command.runtime_session_id:
            raise RuntimeSessionMismatch("runtime session does not match checkpoint")
        if checkpoint.checkpoint_version != command.expected_checkpoint_version:
            raise RuntimeCheckpointVersionMismatch(
                "runtime checkpoint version does not match prepared execution"
            )
        expected_wait_kind = (
            "approval" if command.command_type == "resume_approval" else "user_input"
        )
        if (
            checkpoint.wait_kind != expected_wait_kind
            or checkpoint.wait_subject_id != command.subject_id
        ):
            raise RuntimeWaitMismatch("runtime checkpoint is waiting for another resource")

    @staticmethod
    def _record(row: sqlite3.Row, *, duplicate: bool = False) -> RuntimeExecutionRecord:
        try:
            return RuntimeExecutionRecord(
                execution_id=row["execution_id"],
                run_id=row["run_id"],
                runtime_session_id=row["runtime_session_id"],
                subject_id=row["subject_id"],
                command_type=row["command_type"],
                fingerprint=row["fingerprint"],
                payload=json.loads(row["payload_json"]),
                state=row["state"],
                checkpoint_version=row["checkpoint_version"],
                outcome=json.loads(row["outcome_json"]) if row["outcome_json"] else None,
                error=json.loads(row["error_json"]) if row["error_json"] else None,
                duplicate=duplicate,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except Exception as exc:
            execution_id = row["execution_id"] if "execution_id" in row.keys() else "unknown"
            raise RuntimeExecutionUnrecoverable(
                f"execution {execution_id} cannot be decoded"
            ) from exc


def persist_wait_checkpoint_then_emit(
    store: RuntimeExecutionStore,
    checkpoint: RuntimeCheckpoint,
    emit_requested: Callable[[], Any],
) -> Any:
    store.save_checkpoint(checkpoint)
    return emit_requested()


class RuntimeProcessLock:
    def __init__(self, state_path: str | Path) -> None:
        self.lock_path = Path(f"{state_path}.lock")
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        if fcntl is None:
            raise RuntimeError(
                "AgentScope runtime requires a Unix platform with fcntl.flock support"
            )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError(
                f"runtime process lock is already held: {self.lock_path}"
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd = self._fd
        self._fd = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
