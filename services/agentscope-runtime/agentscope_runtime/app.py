from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agentscope.agent import Agent, ReActConfig
from agentscope.event import (
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    TextBlockDeltaEvent,
)
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import ToolBase, ToolChunk, Toolkit
from fastapi import Depends, FastAPI, Header, HTTPException, status

from agentscope_runtime.agentscope_bridge import (
    AgentScopeRuntimeBridge,
    OpenWebUIToolApprovalRejected,
    OpenWebUIToolApprovalRequired,
)
from agentscope_runtime.execution_store import (
    RuntimeApplyResult,
    RuntimeCheckpoint,
    RuntimeCheckpointNotFound,
    RuntimeCheckpointUnrecoverable,
    RuntimeCheckpointVersionMismatch,
    RuntimeExecutionCancelled,
    RuntimeExecutionCommand,
    RuntimeExecutionConflict,
    RuntimeExecutionError,
    RuntimeExecutionNotFound,
    RuntimeExecutionRecord,
    RuntimeExecutionStore,
    RuntimeProcessLock,
    SQLiteRuntimeExecutionStore,
    ToolOutcomeIndeterminate,
)
from agentscope_runtime.openwebui_client import OpenWebUIClient
from agentscope_runtime.schemas import (
    ApprovalDecisionNotification,
    RunStartRequest,
    RunStartResponse,
    RunStatusResponse,
    RuntimeExecutionPrepareRequest,
    RuntimeExecutionResponse,
)
from agentscope_runtime.subagents import (
    LEADER_PARTICIPANT_ID,
    AgentScopeSubagentAdapter,
    SubagentExecutionContext,
    SubagentSpec,
)

logger = logging.getLogger(__name__)


MODEL_CALL_QUEUED_RETRY_ATTEMPTS = 3
MODEL_CALL_QUEUED_RETRY_DELAY_SECONDS = 0.05
FAILED_CLOSEOUT_RETRY_ATTEMPTS = max(
    1,
    int(os.getenv("AGENT_RUNTIME_FAILED_CLOSEOUT_RETRY_ATTEMPTS", "3")),
)
FAILED_CLOSEOUT_RETRY_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("AGENT_RUNTIME_FAILED_CLOSEOUT_RETRY_DELAY_SECONDS", "0.1")),
)
FINAL_DELTA_CHUNK_CHARS = int(os.getenv("AGENT_RUNTIME_FINAL_DELTA_CHUNK_CHARS", "32"))
FINAL_DELTA_STREAM_CHUNK_CHARS = int(
    os.getenv(
        "AGENT_RUNTIME_FINAL_DELTA_STREAM_CHUNK_CHARS",
        os.getenv("AGENT_RUNTIME_FINAL_DELTA_CHUNK_CHARS", "96"),
    )
)
FINAL_DELTA_STREAM_FLUSH_SECONDS = float(os.getenv("AGENT_RUNTIME_FINAL_DELTA_FLUSH_SECONDS", "0.05"))
CANCELLATION_POLL_SECONDS = max(
    0.01,
    float(os.getenv("AGENT_RUNTIME_CANCELLATION_POLL_SECONDS", "0.1")),
)
PROVIDER_CONFIGURATION_UNAVAILABLE_SUMMARY = "The selected model provider is not available for this Agent Mode run."


class ProviderConfigurationUnavailable(RuntimeError):
    code = "provider_configuration_unavailable"
    user_summary = PROVIDER_CONFIGURATION_UNAVAILABLE_SUMMARY


class ApprovalRejectedError(RuntimeError):
    code = "approval_rejected"
    user_summary = "Tool approval was rejected."


class RuntimeCallbackClient(Protocol):
    async def append_event(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        event_type: str,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        participant_id: str | None = None,
        phase: str | None = None,
    ) -> dict[str, Any]: ...

    async def append_text_delta(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        block_id: str,
        block_kind: str,
        delta_index: int,
        delta: str,
        participant_id: str | None = None,
        phase: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def append_final_delta(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        final_stream_id: str,
        delta_index: int,
        delta: str,
        participant_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def call_model(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        model_call_id: str,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool,
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def call_tool(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        tool_call_id: str,
        tool_id: str,
        arguments: dict[str, Any],
        checkpoint_version: int | None = None,
        decision_execution_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def request_user_input(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        user_input_id: str,
        tool_call_id: str,
        checkpoint_version: int,
        message: str,
        requested_schema: dict[str, Any],
        timeout_seconds: float | None = None,
        allow_cancel: bool = True,
    ) -> dict[str, Any]: ...

    async def register_subagent(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        parent_participant_id: str,
        participant_id: str,
        name: str,
        description: str,
        task: str,
        budget: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def select_model(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        selection_id: str,
        requested_model_id: str | None = None,
        fuzzy_request: str | None = None,
        source_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

@dataclass
class RuntimeSession:
    run_id: str
    runtime_session_id: str
    state: str
    cancel_requested: bool = False
    start_accepted: bool = False
    failed_closeout_completed: bool = False
    request: RunStartRequest | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class FinalAnswerStreamResult:
    final_msg: Any | None = None
    streamed_text: str = ""
    next_delta_index: int = 0
    pause_event: RequireExternalExecutionEvent | None = None


class RuntimeStore:
    def __init__(self) -> None:
        self._sessions: dict[str, RuntimeSession] = {}

    def create(self, request: RunStartRequest) -> RuntimeSession:
        session = RuntimeSession(
            run_id=request.run_id,
            runtime_session_id=_new_runtime_session_id(request.run_id),
            state="running",
            request=request,
        )
        self._sessions[request.run_id] = session
        return session

    def get(self, run_id: str) -> RuntimeSession | None:
        return self._sessions.get(run_id)

    def restore(self, session: RuntimeSession) -> RuntimeSession:
        self._sessions[session.run_id] = session
        return session

    def cancel(self, run_id: str) -> RuntimeSession | None:
        session = self.get(run_id)
        if session is None:
            return None
        session.cancel_requested = True
        session.state = "cancelled"
        session.updated_at = time.time()
        return session


def create_app(
    *,
    service_token: str,
    openwebui_client: RuntimeCallbackClient | None = None,
    store: RuntimeStore | None = None,
    openwebui_base_url: str | None = None,
    openwebui_service_token: str | None = None,
    model_call_connect_timeout: float = 10.0,
    model_call_read_idle_timeout: float = 30.0,
    model_call_total_timeout: float = 300.0,
    auto_finalize_ordinary_qa: bool = True,
    execution_store: RuntimeExecutionStore | None = None,
    execution_applier: Callable[
        [RuntimeCheckpoint, RuntimeExecutionRecord],
        Awaitable[RuntimeApplyResult],
    ]
    | None = None,
    execution_continuation: Callable[
        [RuntimeCheckpoint, RuntimeExecutionRecord],
        Awaitable[None],
    ]
    | None = None,
    process_lock: RuntimeProcessLock | None = None,
) -> FastAPI:
    runtime_store = store or RuntimeStore()
    callback_client = openwebui_client or OpenWebUIClient(
        base_url=openwebui_base_url or "http://127.0.0.1:8080",
        service_token=openwebui_service_token or service_token,
        model_call_connect_timeout=model_call_connect_timeout,
        model_call_read_idle_timeout=model_call_read_idle_timeout,
        model_call_total_timeout=model_call_total_timeout,
    )
    apply_execution = execution_applier or _build_default_execution_applier(callback_client)
    continue_execution = execution_continuation or (
        _build_default_execution_continuation(
            callback_client,
            execution_store,
            runtime_store,
        )
        if execution_applier is None and execution_store is not None
        else None
    )
    continuations_dispatched: set[str] = set()
    continuation_tasks: dict[str, asyncio.Task[None]] = {}
    run_tasks: dict[str, set[asyncio.Task[Any]]] = {}
    active_executions: set[str] = set()

    def track_run_task(
        run_id: str,
        task: asyncio.Task[Any],
    ) -> asyncio.Task[Any]:
        tasks = run_tasks.setdefault(run_id, set())
        tasks.add(task)

        def discard_finished(_task: asyncio.Task[Any]) -> None:
            current = run_tasks.get(run_id)
            if current is None:
                return
            current.discard(_task)
            if not current:
                run_tasks.pop(run_id, None)

        task.add_done_callback(discard_finished)
        return task

    def spawn_run_task(
        run_id: str,
        coroutine: Awaitable[Any],
        *,
        name: str,
    ) -> asyncio.Task[Any]:
        return track_run_task(
            run_id,
            asyncio.create_task(coroutine, name=name),
        )

    async def cancel_run_tasks(run_id: str) -> None:
        current = asyncio.current_task()
        tasks = [
            task
            for task in run_tasks.get(run_id, set())
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def dispatch_continuation(record: RuntimeExecutionRecord) -> None:
        if continue_execution is None or record.execution_id in continuations_dispatched:
            return
        checkpoint = execution_store.get_checkpoint(record.run_id) if execution_store else None
        if checkpoint is None or not checkpoint.continuation_pending:
            return

        async def run_continuation() -> None:
            try:
                await continue_execution(checkpoint, record)
            except asyncio.CancelledError:
                logger.info(
                    "Durable continuation cancelled and left retryable execution_id=%s",
                    record.execution_id,
                )
            except Exception:
                logger.exception(
                    "Durable continuation failed and left retryable execution_id=%s",
                    record.execution_id,
                )
            finally:
                continuations_dispatched.discard(record.execution_id)
                continuation_tasks.pop(record.execution_id, None)

        task = spawn_run_task(
            record.run_id,
            run_continuation(),
            name=f"runtime-continuation:{record.execution_id}",
        )
        continuation_tasks[record.execution_id] = task
        continuations_dispatched.add(record.execution_id)

    def require_service_token(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="service token required")
        token = authorization.removeprefix("Bearer ").strip()
        if not secrets.compare_digest(token, service_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if process_lock is not None:
            process_lock.acquire()
        try:
            if execution_store is not None:
                for checkpoint in execution_store.list_pending_continuations():
                    record = _pending_continuation_record(
                        execution_store,
                        checkpoint,
                    )
                    if record is not None:
                        dispatch_continuation(record)
            yield
        finally:
            tasks = {
                task
                for tracked in run_tasks.values()
                for task in tracked
                if not task.done()
            }
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if process_lock is not None:
                process_lock.release()

    app = FastAPI(
        title="OpenWebUI AgentScope Runtime",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.put(
        "/v1/openwebui/runs/{run_id}/executions/{execution_id}",
        response_model=RuntimeExecutionResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_service_token)],
    )
    async def prepare_runtime_execution(
        run_id: str,
        execution_id: str,
        body: RuntimeExecutionPrepareRequest,
    ) -> RuntimeExecutionResponse:
        store_ = _require_execution_store(execution_store)
        if body.execution_id != execution_id:
            raise _execution_http_error(
                RuntimeExecutionConflict("path and body execution ids differ")
            )
        try:
            command = RuntimeExecutionCommand.from_mapping(body.model_dump(mode="json"))
            record = store_.prepare(command, run_id=run_id)
        except RuntimeExecutionError as exc:
            raise _execution_http_error(exc) from exc
        return _execution_response(record)

    @app.post(
        "/v1/openwebui/runs/{run_id}/executions/{execution_id}/activate",
        response_model=RuntimeExecutionResponse,
        dependencies=[Depends(require_service_token)],
    )
    async def activate_runtime_execution(
        run_id: str,
        execution_id: str,
    ) -> RuntimeExecutionResponse:
        store_ = _require_execution_store(execution_store)
        apply_owned = False
        try:
            record = store_.get_execution(execution_id)
            if record is None or record.run_id != run_id:
                raise RuntimeExecutionNotFound(execution_id)
            if record.state == "applying" and execution_id not in active_executions:
                applying, owner = store_.recover_inflight(execution_id)
            else:
                applying, owner = store_.begin_apply(execution_id)
            if not owner:
                dispatch_continuation(applying)
                return _execution_response(applying)
            apply_owned = True
            active_executions.add(execution_id)
            current_task = asyncio.current_task()
            if current_task is not None:
                track_run_task(run_id, current_task)
            checkpoint = store_.get_checkpoint(run_id)
            if checkpoint is None:
                raise RuntimeCheckpointNotFound(run_id)
            result = await apply_execution(checkpoint, applying)
            completed = store_.complete_apply(execution_id, result)
            dispatch_continuation(completed)
            return _execution_response(completed)
        except ToolOutcomeIndeterminate as exc:
            terminal = store_.mark_terminal(
                execution_id,
                state="indeterminate",
                error={"code": exc.code, "message": str(exc)},
            )
            return _execution_response(terminal)
        except RuntimeExecutionError as exc:
            if isinstance(exc, RuntimeCheckpointUnrecoverable):
                terminal = store_.mark_terminal(
                    execution_id,
                    state="unrecoverable",
                    error={"code": exc.code, "message": str(exc)},
                )
                return _execution_response(terminal)
            if apply_owned:
                terminal_state = (
                    "cancelled"
                    if isinstance(exc, RuntimeExecutionCancelled)
                    else "failed"
                )
                terminal = store_.mark_terminal(
                    execution_id,
                    state=terminal_state,
                    error={"code": exc.code, "message": str(exc)},
                )
                return _execution_response(terminal)
            raise _execution_http_error(exc) from exc
        finally:
            if apply_owned:
                active_executions.discard(execution_id)

    @app.get(
        "/v1/openwebui/runs/{run_id}/executions/{execution_id}",
        response_model=RuntimeExecutionResponse,
        dependencies=[Depends(require_service_token)],
    )
    async def query_runtime_execution(
        run_id: str,
        execution_id: str,
    ) -> RuntimeExecutionResponse:
        store_ = _require_execution_store(execution_store)
        record = store_.get_execution(execution_id)
        if record is None or record.run_id != run_id:
            raise _execution_http_error(RuntimeExecutionNotFound(execution_id))
        return _execution_response(record)

    @app.post(
        "/v1/openwebui/runs",
        response_model=RunStartResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_service_token)],
    )
    async def start_run(request: RunStartRequest) -> RunStartResponse:
        existing_session = runtime_store.get(request.run_id)
        if existing_session is not None and existing_session.start_accepted:
            return RunStartResponse(
                runtime_session_id=existing_session.runtime_session_id,
                accepted=True,
            )

        if execution_store is not None:
            try:
                durable_checkpoint = execution_store.get_checkpoint(request.run_id)
            except RuntimeCheckpointUnrecoverable as exc:
                raise _execution_http_error(exc) from exc
            if durable_checkpoint is not None:
                if (
                    durable_checkpoint.external_result is not None
                    and durable_checkpoint.state
                    not in {
                        "completed",
                        "failed",
                        "cancelled",
                        "indeterminate",
                        "unrecoverable",
                    }
                ):
                    _recover_persisted_external_result(
                        execution_store,
                        durable_checkpoint,
                    )
                    durable_checkpoint = execution_store.get_checkpoint(request.run_id)
                    if durable_checkpoint is None:
                        raise _execution_http_error(
                            RuntimeCheckpointNotFound(request.run_id)
                        )
                session = RuntimeSession(
                    run_id=request.run_id,
                    runtime_session_id=durable_checkpoint.runtime_session_id,
                    state=durable_checkpoint.state,
                    cancel_requested=durable_checkpoint.cancel_requested,
                    start_accepted=True,
                    request=request,
                )
                runtime_store.restore(session)
                if (
                    durable_checkpoint.continuation_pending
                    and durable_checkpoint.external_result is not None
                ):
                    dispatch_continuation(
                        _external_continuation_record(durable_checkpoint)
                    )
                return RunStartResponse(
                    runtime_session_id=session.runtime_session_id,
                    accepted=True,
                )

        session = runtime_store.create(request)
        if execution_store is not None:
            execution_store.save_checkpoint(
                RuntimeCheckpoint(
                    run_id=request.run_id,
                    runtime_session_id=session.runtime_session_id,
                    state="running",
                    checkpoint_version=0,
                    run_request=request.model_dump(mode="json"),
                )
            )
        idempotency_key = f"evt:{session.runtime_session_id}:run-running"
        try:
            await callback_client.append_event(
                run_id=request.run_id,
                idempotency_key=idempotency_key,
                event_type="run.running",
                summary="Agent runtime accepted run.",
                payload={"runtime_session_id": session.runtime_session_id},
                participant_id="leader",
                phase="running",
            )
            session.start_accepted = True
            session.updated_at = time.time()
        except Exception as exc:
            session.state = "failed"
            session.updated_at = time.time()
            _persist_session_state(execution_store, session)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "openwebui_callback_failed",
                    "message": str(exc),
                },
            ) from exc

        if auto_finalize_ordinary_qa:
            if _should_use_general_agent(request):
                spawn_run_task(
                    request.run_id,
                    _finalize_general_agent_run(
                        callback_client,
                        session,
                        request,
                        execution_store=execution_store,
                    ),
                    name=f"runtime-finalize:{request.run_id}",
                )
            else:
                spawn_run_task(
                    request.run_id,
                    _finalize_ordinary_qa(
                        callback_client,
                        session,
                        request,
                        execution_store=execution_store,
                    ),
                    name=f"runtime-finalize:{request.run_id}",
                )

        return RunStartResponse(
            runtime_session_id=session.runtime_session_id,
            accepted=True,
        )

    @app.post(
        "/v1/openwebui/runs/{run_id}/cancel",
        response_model=RunStatusResponse,
        dependencies=[Depends(require_service_token)],
    )
    async def cancel_run(run_id: str) -> RunStatusResponse:
        if execution_store is not None:
            try:
                checkpoint = execution_store.cancel_checkpoint(run_id)
            except RuntimeExecutionError as exc:
                raise _execution_http_error(exc) from exc
            await cancel_run_tasks(run_id)
            session = _restore_session_from_checkpoint(runtime_store, checkpoint)
            return _status(session)
        session = runtime_store.cancel(run_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        return _status(session)

    @app.get(
        "/v1/openwebui/runs/{run_id}/status",
        response_model=RunStatusResponse,
        dependencies=[Depends(require_service_token)],
    )
    async def get_run_status(run_id: str) -> RunStatusResponse:
        if execution_store is not None:
            try:
                checkpoint = execution_store.get_checkpoint(run_id)
            except RuntimeExecutionError as exc:
                raise _execution_http_error(exc) from exc
            if checkpoint is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="run not found"
                )
            return _status(_restore_session_from_checkpoint(runtime_store, checkpoint))
        session = runtime_store.get(run_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        return _status(session)

    @app.post(
        "/v1/openwebui/runs/{run_id}/approval-decision",
        response_model=RunStatusResponse,
        dependencies=[Depends(require_service_token)],
    )
    async def notify_approval_decision(
        run_id: str,
        decision: ApprovalDecisionNotification,
    ) -> RunStatusResponse:
        if execution_store is not None:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={
                    "code": "legacy_approval_endpoint_disabled",
                    "message": (
                        "Durable runtimes must resume approvals through the "
                        "execution prepare and activate protocol."
                    ),
                },
            )
        session = runtime_store.get(run_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        if decision.decision != "rejected":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "approval_resume_not_supported",
                    "message": "Approved approval decisions require a backend/runtime resume contract.",
                },
            )
        if session.state == "failed":
            return _status(session)
        if session.state != "waiting_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "run_not_waiting_approval",
                    "message": f"Run {run_id} is {session.state}, not waiting_approval.",
                },
            )

        payload = {
            "runtime_session_id": session.runtime_session_id,
            "approval_id": decision.approval_id,
            "decision": decision.decision,
            "tool_call_id": decision.tool_call_id,
            "tool_id": decision.tool_id,
            "tool_name": decision.tool_name,
        }
        await _mark_session_failed(
            callback_client,
            session,
            ApprovalRejectedError(_approval_rejected_message(decision)),
            stage="approval-decision",
            payload=payload,
        )
        return _status(session)

    return app


def create_app_from_env() -> FastAPI:
    service_token = os.getenv("AGENT_RUNTIME_SERVICE_TOKEN", "").strip()
    if not service_token:
        raise RuntimeError("AGENT_RUNTIME_SERVICE_TOKEN is required")

    auto_finalize = os.getenv("AGENT_RUNTIME_AUTO_FINALIZE_ORDINARY_QA", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    state_path = os.getenv("AGENT_RUNTIME_STATE_PATH", "").strip()
    if not state_path:
        raise RuntimeError("AGENT_RUNTIME_STATE_PATH is required")
    _require_single_runtime_worker()
    execution_store = SQLiteRuntimeExecutionStore(
        Path(state_path),
        max_checkpoint_bytes=_nonnegative_env_int(
            "AGENT_RUNTIME_MAX_CHECKPOINT_BYTES",
            16 * 1024 * 1024,
            minimum=1,
        ),
        terminal_retention_seconds=_nonnegative_env_int(
            "AGENT_RUNTIME_TERMINAL_RETENTION_SECONDS",
            7 * 24 * 60 * 60,
        ),
        max_terminal_executions=_nonnegative_env_int(
            "AGENT_RUNTIME_MAX_TERMINAL_EXECUTIONS",
            10_000,
            minimum=1,
        ),
        terminal_checkpoint_retention_seconds=_nonnegative_env_int(
            "AGENT_RUNTIME_TERMINAL_CHECKPOINT_RETENTION_SECONDS",
            7 * 24 * 60 * 60,
        ),
        max_terminal_checkpoints=_nonnegative_env_int(
            "AGENT_RUNTIME_MAX_TERMINAL_CHECKPOINTS",
            10_000,
            minimum=1,
        ),
    )
    return create_app(
        service_token=service_token,
        openwebui_base_url=os.getenv("OPENWEBUI_BASE_URL") or "http://127.0.0.1:8080",
        openwebui_service_token=os.getenv("OPENWEBUI_SERVICE_TOKEN") or service_token,
        model_call_connect_timeout=_positive_env_float(
            "AGENT_RUNTIME_MODEL_CALL_CONNECT_TIMEOUT_SECONDS", 10.0
        ),
        model_call_read_idle_timeout=_positive_env_float(
            "AGENT_RUNTIME_MODEL_CALL_READ_IDLE_TIMEOUT_SECONDS", 30.0
        ),
        model_call_total_timeout=_positive_env_float(
            "AGENT_RUNTIME_MODEL_CALL_TOTAL_TIMEOUT_SECONDS", 300.0
        ),
        auto_finalize_ordinary_qa=auto_finalize,
        execution_store=execution_store,
        process_lock=RuntimeProcessLock(state_path),
    )


def _positive_env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def _nonnegative_env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


def _require_single_runtime_worker() -> None:
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        raw = os.getenv(name, "").strip()
        if not raw:
            continue
        try:
            workers = int(raw)
        except ValueError as exc:
            raise RuntimeError(f"{name} must be 1") from exc
        if workers != 1:
            raise RuntimeError(f"{name} must be 1")


def _status(session: RuntimeSession) -> RunStatusResponse:
    return RunStatusResponse(
        run_id=session.run_id,
        runtime_session_id=session.runtime_session_id,
        state=session.state,
        cancel_requested=session.cancel_requested,
    )


def _restore_session_from_checkpoint(
    runtime_store: RuntimeStore,
    checkpoint: RuntimeCheckpoint,
) -> RuntimeSession:
    session = runtime_store.get(checkpoint.run_id)
    if session is None:
        session = RuntimeSession(
            run_id=checkpoint.run_id,
            runtime_session_id=checkpoint.runtime_session_id,
            state=checkpoint.state,
        )
        runtime_store.restore(session)
    session.runtime_session_id = checkpoint.runtime_session_id
    session.state = checkpoint.state
    session.cancel_requested = checkpoint.cancel_requested
    session.start_accepted = True
    session.updated_at = time.time()
    return session


def _persist_session_state(
    execution_store: RuntimeExecutionStore | None,
    session: RuntimeSession,
) -> RuntimeCheckpoint | None:
    if execution_store is None:
        return None
    for _attempt in range(3):
        checkpoint = execution_store.get_checkpoint(session.run_id)
        if checkpoint is None:
            raise RuntimeCheckpointNotFound(session.run_id)
        if checkpoint.state in {
            "completed",
            "failed",
            "cancelled",
            "indeterminate",
            "unrecoverable",
        }:
            session.state = checkpoint.state
            session.cancel_requested = checkpoint.cancel_requested
            return checkpoint
        continuation_pending = (
            False
            if session.state in {"completed", "failed", "cancelled"}
            else checkpoint.continuation_pending
        )
        if (
            checkpoint.state == session.state
            and checkpoint.cancel_requested == session.cancel_requested
            and checkpoint.continuation_pending == continuation_pending
        ):
            return checkpoint
        updated = checkpoint.model_copy(
            update={
                "state": session.state,
                "cancel_requested": session.cancel_requested,
                "continuation_pending": continuation_pending,
            }
        )
        try:
            return execution_store.save_checkpoint_cas(
                updated,
                expected_version=checkpoint.checkpoint_version,
                expected_states={checkpoint.state},
            )
        except RuntimeCheckpointVersionMismatch:
            continue
        except RuntimeExecutionCancelled:
            cancelled = execution_store.get_checkpoint(session.run_id)
            if cancelled is None:
                raise RuntimeCheckpointNotFound(session.run_id)
            session.state = cancelled.state
            session.cancel_requested = cancelled.cancel_requested
            return cancelled
    raise RuntimeCheckpointVersionMismatch(
        "checkpoint kept changing while persisting runtime state"
    )


def _require_execution_store(
    store: RuntimeExecutionStore | None,
) -> RuntimeExecutionStore:
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "runtime_execution_store_unavailable",
                "message": "AGENT_RUNTIME_STATE_PATH is not configured.",
            },
        )
    return store


def _execution_http_error(exc: RuntimeExecutionError) -> HTTPException:
    status_code = (
        status.HTTP_404_NOT_FOUND
        if isinstance(exc, (RuntimeExecutionNotFound, RuntimeCheckpointNotFound))
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _execution_response(record: RuntimeExecutionRecord) -> RuntimeExecutionResponse:
    return RuntimeExecutionResponse(
        execution_id=record.execution_id,
        run_id=record.run_id,
        runtime_session_id=record.runtime_session_id,
        subject_id=record.subject_id,
        command_type=record.command_type,
        fingerprint=record.fingerprint,
        state=record.state,
        checkpoint_version=record.checkpoint_version,
        duplicate=record.duplicate,
        outcome=record.outcome,
        error=record.error,
    )


def _external_continuation_record(
    checkpoint: RuntimeCheckpoint,
) -> RuntimeExecutionRecord:
    external_result = checkpoint.external_result or {}
    execution_id = str(external_result.get("execution_id") or "")
    tool_call_id = str(external_result.get("tool_call_id") or "")
    if not execution_id or not tool_call_id:
        raise RuntimeCheckpointUnrecoverable(
            "persisted external result cannot identify its continuation"
        )
    now = time.time()
    return RuntimeExecutionRecord(
        execution_id=execution_id,
        run_id=checkpoint.run_id,
        runtime_session_id=checkpoint.runtime_session_id,
        subject_id=tool_call_id,
        command_type="resume_approval",
        fingerprint=execution_id,
        payload={"kind": "external_result"},
        state="applied",
        checkpoint_version=checkpoint.checkpoint_version,
        outcome={"kind": "external_result"},
        created_at=now,
        updated_at=now,
    )


def _pending_continuation_record(
    execution_store: RuntimeExecutionStore,
    checkpoint: RuntimeCheckpoint,
) -> RuntimeExecutionRecord | None:
    if checkpoint.applied_execution_id:
        record = execution_store.get_execution(checkpoint.applied_execution_id)
        if record is not None and record.state == "applied":
            return record
    if checkpoint.external_result is not None:
        return _external_continuation_record(checkpoint)
    return None


async def _apply_execution_to_checkpoint(
    checkpoint: RuntimeCheckpoint,
    execution: RuntimeExecutionRecord,
) -> RuntimeApplyResult:
    expected_wait_kind = (
        "approval" if execution.command_type == "resume_approval" else "user_input"
    )
    if (
        checkpoint.wait_kind != expected_wait_kind
        or checkpoint.wait_subject_id != execution.subject_id
    ):
        raise RuntimeExecutionConflict(
            "runtime checkpoint no longer matches prepared execution"
        )
    if checkpoint.cancel_requested or checkpoint.state == "cancelled":
        raise RuntimeExecutionCancelled("runtime session was cancelled")
    outcome = {
        "kind": "applied",
        "runtime_state": "running",
        "command_type": execution.command_type,
        "subject_id": execution.subject_id,
    }
    return RuntimeApplyResult(
        checkpoint=checkpoint.model_copy(
            update={
                "state": "running",
                "wait_kind": None,
                "wait_subject_id": None,
                "applied_execution_id": execution.execution_id,
                "outcome": outcome,
            }
        ),
        outcome=outcome,
    )


def _build_default_execution_applier(
    callback_client: RuntimeCallbackClient,
) -> Callable[
    [RuntimeCheckpoint, RuntimeExecutionRecord],
    Awaitable[RuntimeApplyResult],
]:
    async def apply(
        checkpoint: RuntimeCheckpoint,
        execution: RuntimeExecutionRecord,
    ) -> RuntimeApplyResult:
        expected_wait_kind = (
            "approval" if execution.command_type == "resume_approval" else "user_input"
        )
        if (
            checkpoint.wait_kind != expected_wait_kind
            or checkpoint.wait_subject_id != execution.subject_id
        ):
            raise RuntimeExecutionConflict(
                "runtime checkpoint no longer matches prepared execution"
            )
        if checkpoint.cancel_requested or checkpoint.state == "cancelled":
            raise RuntimeExecutionCancelled("runtime session was cancelled")
        await _preflight_durable_continuation(
            checkpoint,
            callback_client=callback_client,
            require_pending_tool_call=True,
        )
        pending = checkpoint.pending_call or {}
        tool_call_id = str(pending.get("tool_call_id") or "")
        tool_name = str(pending.get("tool_name") or pending.get("tool_id") or "tool")
        if not tool_call_id:
            raise RuntimeCheckpointUnrecoverable(
                "runtime checkpoint does not contain a pending tool call"
            )

        if execution.command_type == "resume_approval":
            decision = str(execution.payload.get("decision") or "")
            if decision not in {"approved", "rejected"}:
                raise RuntimeExecutionConflict("invalid approval decision")
            if decision == "approved":
                try:
                    response = await callback_client.call_tool(
                        run_id=checkpoint.run_id,
                        idempotency_key=str(
                            pending.get("idempotency_key")
                            or f"tool:{pending.get('participant_id') or 'leader'}:{tool_call_id}:1"
                        ),
                        participant_id=str(pending.get("participant_id") or "leader"),
                        tool_call_id=tool_call_id,
                        tool_id=str(pending.get("tool_id") or tool_name),
                        arguments=dict(pending.get("arguments") or {}),
                        decision_execution_id=execution.execution_id,
                    )
                except Exception as exc:
                    raise ToolOutcomeIndeterminate(
                        "backend tool outcome is not authoritative after activation failure"
                    ) from exc
                if response.get("status") == "approval_required":
                    raise RuntimeExecutionConflict(
                        "backend did not consume the prepared approval decision"
                    )
                result_state = (
                    ToolResultState.SUCCESS
                    if response.get("status") == "success"
                    else ToolResultState.ERROR
                )
                result_output = str(response.get("content") or "")
            else:
                result_state = ToolResultState.DENIED
                result_output = json.dumps(
                    {
                        "status": "approval_rejected",
                        "decision": "rejected",
                        "approval_id": execution.subject_id,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            outcome = {
                "kind": "applied",
                "runtime_state": "running",
                "decision": decision,
                "tool_call_id": tool_call_id,
            }
        else:
            input_status = str(execution.payload.get("status") or "")
            if input_status not in {"accepted", "declined", "cancelled", "timeout"}:
                raise RuntimeExecutionConflict("invalid user input status")
            result_state = {
                "accepted": ToolResultState.SUCCESS,
                "declined": ToolResultState.DENIED,
                "cancelled": ToolResultState.INTERRUPTED,
                "timeout": ToolResultState.INTERRUPTED,
            }[input_status]
            result_output = json.dumps(
                {
                    "status": input_status,
                    "user_input_id": execution.subject_id,
                    **(
                        {"content": execution.payload["content"]}
                        if "content" in execution.payload
                        else {}
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            outcome = {
                "kind": "applied",
                "runtime_state": "running",
                "status": input_status,
                "tool_call_id": tool_call_id,
            }

        agent_state = _inject_external_tool_result(
            checkpoint.agent_state,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            output=result_output,
            state=result_state,
        )
        return RuntimeApplyResult(
            checkpoint=checkpoint.model_copy(
                update={
                    "state": "running",
                    "wait_kind": None,
                    "wait_subject_id": None,
                    "pending_call": None,
                    "agent_state": agent_state,
                    "applied_execution_id": execution.execution_id,
                    "outcome": outcome,
                    "continuation_pending": True,
                }
            ),
            outcome=outcome,
        )

    return apply


def _build_default_execution_continuation(
    callback_client: RuntimeCallbackClient,
    execution_store: RuntimeExecutionStore,
    runtime_store: RuntimeStore | None = None,
) -> Callable[
    [RuntimeCheckpoint, RuntimeExecutionRecord],
    Awaitable[None],
]:
    async def continue_execution(
        checkpoint: RuntimeCheckpoint,
        execution: RuntimeExecutionRecord,
    ) -> None:
        authoritative = execution_store.get_checkpoint(checkpoint.run_id)
        if authoritative is None:
            raise RuntimeCheckpointNotFound(checkpoint.run_id)
        if (
            authoritative.cancel_requested
            or authoritative.state in {
                "cancelled",
                "completed",
                "failed",
                "indeterminate",
                "unrecoverable",
            }
        ):
            return
        checkpoint = authoritative
        request, agent_state, leader_model_id = await _preflight_durable_continuation(
            checkpoint,
            callback_client=callback_client,
        )
        session = RuntimeSession(
            run_id=checkpoint.run_id,
            runtime_session_id=checkpoint.runtime_session_id,
            state="running",
            cancel_requested=checkpoint.cancel_requested,
            start_accepted=True,
            request=request,
        )
        if runtime_store is not None:
            existing_session = runtime_store.get(checkpoint.run_id)
            if existing_session is not None:
                if _is_cancelled(existing_session):
                    return
                existing_session.state = checkpoint.state
                existing_session.request = request
                session = existing_session
            else:
                session = runtime_store.restore(session)
        bridge = AgentScopeRuntimeBridge(
            run_id=session.run_id,
            runtime_session_id=session.runtime_session_id,
            callback_client=callback_client,
            assistant_context_by_participant={
                LEADER_PARTICIPANT_ID: _agent_context_replay_assistant_messages(
                    request.metadata.get("agent_context_replay")
                )
            },
            durable_external_tools=True,
            checkpoint_state=checkpoint.bridge_state,
        )
        leader = Agent(
            name=LEADER_PARTICIPANT_ID,
            system_prompt=_leader_system_prompt(request),
            model=bridge.build_model(
                participant_id=LEADER_PARTICIPANT_ID,
                model_id=leader_model_id,
                default_model_params=_request_model_params(request),
            ),
            toolkit=_build_toolkit(
                bridge=bridge,
                callback_client=callback_client,
                session=session,
                request=request,
                participant_id=LEADER_PARTICIPANT_ID,
                include_subagent_tool=_subagents_enabled(request),
                leader_model_id=leader_model_id,
                durable_external_tools=True,
            ),
            state=agent_state,
            react_config=ReActConfig(max_iters=_max_iters(request)),
        )
        payload = {"runtime_session_id": session.runtime_session_id}
        try:
            inputs: Any = None
            while True:
                stream_result = await _run_leader_streaming(
                    leader,
                    session,
                    inputs,
                    callback_client=callback_client,
                    payload=payload,
                    execution_store=execution_store,
                )
                if stream_result.pause_event is None:
                    break
                resolved = await _resolve_durable_external_event(
                    callback_client=callback_client,
                    execution_store=execution_store,
                    session=session,
                    request=request,
                    leader=leader,
                    bridge=bridge,
                    event=stream_result.pause_event,
                )
                if resolved is None:
                    return
                inputs = resolved
                session.state = "running"
            final_answer = (
                _msg_text(stream_result.final_msg)
                or bridge.latest_final_text(LEADER_PARTICIPANT_ID)
                or stream_result.streamed_text
            )
            await _emit_final_answer(
                callback_client,
                session,
                final_answer,
                payload,
                already_emitted_text=stream_result.streamed_text,
                next_delta_index=stream_result.next_delta_index,
                execution_store=execution_store,
            )
        except Exception as exc:
            if not _is_cancelled(session):
                await _mark_session_failed(
                    callback_client,
                    session,
                    exc,
                    stage="durable-continuation",
                    payload={"execution_id": execution.execution_id},
                    execution_store=execution_store,
                )
    return continue_execution


async def _preflight_durable_continuation(
    checkpoint: RuntimeCheckpoint,
    *,
    callback_client: RuntimeCallbackClient,
    require_pending_tool_call: bool = False,
) -> tuple[RunStartRequest, AgentState, str]:
    try:
        request = RunStartRequest.model_validate(checkpoint.run_request)
        agent_state = AgentState.model_validate(checkpoint.agent_state)
    except Exception as exc:
        raise RuntimeCheckpointUnrecoverable(
            "durable continuation checkpoint cannot be decoded"
        ) from exc
    if request.run_id != checkpoint.run_id:
        raise RuntimeCheckpointUnrecoverable(
            "durable continuation run id does not match checkpoint"
        )
    leader_model_id = request.leader_model_id or _first_model_catalog_id(
        request.model_catalog
    )
    if not leader_model_id:
        raise RuntimeCheckpointUnrecoverable(
            "leader model id is missing from durable checkpoint"
        )
    bridge_state = checkpoint.bridge_state
    if not isinstance(bridge_state, dict):
        raise RuntimeCheckpointUnrecoverable("bridge checkpoint state must be an object")
    next_tool_call_index = bridge_state.get("next_tool_call_index", 1)
    model_call_indexes = bridge_state.get("model_call_indexes", {})
    if (
        isinstance(next_tool_call_index, bool)
        or not isinstance(next_tool_call_index, int)
        or next_tool_call_index < 1
    ):
        raise RuntimeCheckpointUnrecoverable(
            "bridge next_tool_call_index must be a positive integer"
        )
    if not isinstance(model_call_indexes, dict) or any(
        not isinstance(participant_id, str)
        or isinstance(index, bool)
        or not isinstance(index, int)
        or index < 1
        for participant_id, index in model_call_indexes.items()
    ):
        raise RuntimeCheckpointUnrecoverable(
            "bridge model_call_indexes must contain positive integer counters"
        )
    if require_pending_tool_call:
        pending = checkpoint.pending_call or {}
        tool_call_id = str(pending.get("tool_call_id") or "")
        if not tool_call_id:
            raise RuntimeCheckpointUnrecoverable(
                "runtime checkpoint does not contain a pending tool call"
            )
        required_text_fields = (
            "participant_id",
            "tool_name",
            "tool_id",
            "idempotency_key",
        )
        if any(not str(pending.get(field) or "").strip() for field in required_text_fields):
            raise RuntimeCheckpointUnrecoverable(
                "runtime checkpoint pending tool identity is incomplete"
            )
        if not isinstance(pending.get("arguments"), dict):
            raise RuntimeCheckpointUnrecoverable(
                "runtime checkpoint pending tool arguments must be an object"
            )
        if not agent_state.context:
            raise RuntimeCheckpointUnrecoverable(
                "AgentScope state has no pending context"
            )
        pending_tool_calls = {
            block.id
            for block in agent_state.context[-1].get_content_blocks("tool_call")
        }
        if tool_call_id not in pending_tool_calls:
            raise RuntimeCheckpointUnrecoverable(
                f"AgentScope state is not waiting for tool call {tool_call_id}"
            )
    try:
        session = RuntimeSession(
            run_id=checkpoint.run_id,
            runtime_session_id=checkpoint.runtime_session_id,
            state=checkpoint.state,
            cancel_requested=checkpoint.cancel_requested,
            start_accepted=True,
            request=request,
        )
        bridge = AgentScopeRuntimeBridge(
            run_id=session.run_id,
            runtime_session_id=session.runtime_session_id,
            callback_client=callback_client,
            assistant_context_by_participant={
                LEADER_PARTICIPANT_ID: _agent_context_replay_assistant_messages(
                    request.metadata.get("agent_context_replay")
                )
            },
            durable_external_tools=True,
            checkpoint_state=bridge_state,
        )
        leader = Agent(
            name=LEADER_PARTICIPANT_ID,
            system_prompt=_leader_system_prompt(request),
            model=bridge.build_model(
                participant_id=LEADER_PARTICIPANT_ID,
                model_id=leader_model_id,
                default_model_params=_request_model_params(request),
            ),
            toolkit=_build_toolkit(
                bridge=bridge,
                callback_client=callback_client,
                session=session,
                request=request,
                participant_id=LEADER_PARTICIPANT_ID,
                include_subagent_tool=_subagents_enabled(request),
                leader_model_id=leader_model_id,
                durable_external_tools=True,
            ),
            state=agent_state,
            react_config=ReActConfig(max_iters=_max_iters(request)),
        )
        await leader.toolkit.get_tool_schemas()
    except RuntimeExecutionError:
        raise
    except Exception as exc:
        raise RuntimeCheckpointUnrecoverable(
            "durable continuation objects cannot be reconstructed"
        ) from exc
    return request, agent_state, leader_model_id


def _inject_external_tool_result(
    serialized_state: dict[str, Any],
    *,
    tool_call_id: str,
    tool_name: str,
    output: str,
    state: ToolResultState,
) -> dict[str, Any]:
    try:
        agent_state = AgentState.model_validate(serialized_state)
    except Exception as exc:
        raise RuntimeCheckpointUnrecoverable("AgentScope state cannot be decoded") from exc
    if not agent_state.context:
        raise RuntimeCheckpointUnrecoverable("AgentScope state has no pending context")
    last_message = agent_state.context[-1]
    tool_calls = {
        block.id: block for block in last_message.get_content_blocks("tool_call")
    }
    tool_call = tool_calls.get(tool_call_id)
    if tool_call is None:
        raise RuntimeCheckpointUnrecoverable(
            f"AgentScope state is not waiting for tool call {tool_call_id}"
        )
    existing_results = {
        block.id for block in last_message.get_content_blocks("tool_result")
    }
    if tool_call_id not in existing_results:
        content = last_message.content
        if not isinstance(content, list):
            content = [TextBlock(text=str(content))]
            last_message.content = content
        content.append(
            ToolResultBlock(
                id=tool_call_id,
                name=tool_name,
                output=output,
                state=state,
            )
        )
    tool_call.state = ToolCallState.FINISHED
    return agent_state.model_dump(mode="json")


async def _resolve_durable_external_event(
    *,
    callback_client: RuntimeCallbackClient,
    execution_store: RuntimeExecutionStore,
    session: RuntimeSession,
    request: RunStartRequest,
    leader: Any,
    bridge: AgentScopeRuntimeBridge,
    event: RequireExternalExecutionEvent,
    crash_after_external_result_persist: Callable[[], None] | None = None,
) -> ExternalExecutionResultEvent | None:
    if len(event.tool_calls) != 1:
        raise RuntimeExecutionConflict(
            "durable external execution requires one sequential tool call"
        )
    tool_call = event.tool_calls[0]
    try:
        arguments = json.loads(tool_call.input or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeExecutionConflict("external tool arguments are not valid JSON") from exc
    if not isinstance(arguments, dict):
        raise RuntimeExecutionConflict("external tool arguments must be an object")
    participant_id = LEADER_PARTICIPANT_ID
    tool_name = str(tool_call.name)
    if tool_name == RequestUserInputTool.name:
        wait_kind = "user_input"
        subject_id = f"user-input:{session.run_id}:{tool_call.id}"
        tool_id = RequestUserInputTool.name
        idempotency_key = f"user-input:{participant_id}:{tool_call.id}:1"
        waiting_state = "waiting_user_input"
    else:
        wait_kind = "approval"
        subject_id = f"approval:{session.run_id}:{tool_call.id}"
        tool_spec = next(
            (spec for spec in _tool_specs(request) if str(spec.get("name")) == tool_name),
            None,
        )
        if tool_spec is None:
            raise RuntimeCheckpointUnrecoverable(
                f"tool specification is missing for {tool_name}"
            )
        tool_id = str(tool_spec.get("id") or tool_name)
        idempotency_key = f"tool:{participant_id}:{tool_call.id}:1"
        waiting_state = "waiting_approval"

    existing = execution_store.get_checkpoint(session.run_id)
    next_version = (existing.checkpoint_version + 1) if existing is not None else 1
    checkpoint = RuntimeCheckpoint(
        run_id=session.run_id,
        runtime_session_id=session.runtime_session_id,
        state=waiting_state,
        checkpoint_version=next_version,
        wait_kind=wait_kind,
        wait_subject_id=subject_id,
        agent_state=leader.state.model_dump(mode="json"),
        run_request=request.model_dump(mode="json"),
        bridge_state=bridge.snapshot_state(),
        pending_call={
            "participant_id": participant_id,
            "tool_call_id": tool_call.id,
            "tool_name": tool_name,
            "tool_id": tool_id,
            "arguments": arguments,
            "idempotency_key": idempotency_key,
        },
    )
    execution_store.save_checkpoint(checkpoint)
    session.state = waiting_state
    session.updated_at = time.time()
    external_execution_id = (
        f"external:{session.runtime_session_id}:{tool_call.id}"
    )

    if wait_kind == "user_input":
        try:
            response = await callback_client.request_user_input(
                run_id=session.run_id,
                idempotency_key=idempotency_key,
                participant_id=participant_id,
                user_input_id=subject_id,
                tool_call_id=tool_call.id,
                checkpoint_version=next_version,
                message=str(arguments.get("message") or "Input required"),
                requested_schema=_user_input_requested_schema(
                    arguments.get("requested_schema")
                    if isinstance(arguments.get("requested_schema"), dict)
                    else None,
                    arguments.get("questions")
                    if isinstance(arguments.get("questions"), list)
                    else None,
                ),
                timeout_seconds=arguments.get("timeout_seconds"),
                allow_cancel=bool(arguments.get("allow_cancel", True)),
            )
        except Exception as exc:
            _mark_external_outcome_indeterminate(
                execution_store,
                checkpoint,
                session,
                external_execution_id,
            )
            raise ToolOutcomeIndeterminate(
                "user-input outcome is not authoritative after callback failure"
            ) from exc
        if response.get("status") == "requested":
            return None
        return _persist_and_recover_external_result(
            execution_store=execution_store,
            checkpoint=checkpoint,
            session=session,
            event=event,
            tool_call_id=tool_call.id,
            tool_name=tool_name,
            tool_id=tool_id,
            execution_id=external_execution_id,
            response=response,
            crash_after_persist=crash_after_external_result_persist,
        )

    await callback_client.append_event(
        run_id=session.run_id,
        idempotency_key=(
            f"evt:{session.runtime_session_id}:{participant_id}:{tool_call.id}:requested"
        ),
        event_type="tool.requested",
        summary=f"{tool_name.replace('_', ' ').strip().title()} requested.",
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call.id,
            "tool_name": tool_name,
            "arguments": arguments,
            "runtime_session_id": session.runtime_session_id,
            "checkpoint_version": next_version,
        },
        participant_id=participant_id,
        phase="running",
    )
    try:
        response = await callback_client.call_tool(
            run_id=session.run_id,
            idempotency_key=idempotency_key,
            participant_id=participant_id,
            tool_call_id=tool_call.id,
            tool_id=tool_id,
            arguments=arguments,
            checkpoint_version=next_version,
        )
    except Exception as exc:
        _mark_external_outcome_indeterminate(
            execution_store,
            checkpoint,
            session,
            external_execution_id,
        )
        raise ToolOutcomeIndeterminate(
            "external tool outcome is not authoritative after callback failure"
        ) from exc
    if response.get("status") == "approval_required":
        approval_id = _approval_id_from_tool_response(response) or subject_id
        if approval_id != subject_id:
            checkpoint = execution_store.save_checkpoint_cas(
                checkpoint.model_copy(update={"wait_subject_id": approval_id}),
                expected_version=checkpoint.checkpoint_version,
                expected_states={checkpoint.state},
            )
        return None
    return _persist_and_recover_external_result(
        execution_store=execution_store,
        checkpoint=checkpoint,
        session=session,
        event=event,
        tool_call_id=tool_call.id,
        tool_name=tool_name,
        tool_id=tool_id,
        execution_id=external_execution_id,
        response=response,
        crash_after_persist=crash_after_external_result_persist,
    )


def _persist_and_recover_external_result(
    *,
    execution_store: RuntimeExecutionStore,
    checkpoint: RuntimeCheckpoint,
    session: RuntimeSession,
    event: RequireExternalExecutionEvent,
    tool_call_id: str,
    tool_name: str,
    tool_id: str,
    execution_id: str,
    response: dict[str, Any],
    crash_after_persist: Callable[[], None] | None,
) -> ExternalExecutionResultEvent:
    result_event = _external_result_event(
        event,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        response=response,
    )
    result_block = result_event.execution_results[0]
    external_result = {
        "execution_id": execution_id,
        "reply_id": event.reply_id,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "tool_id": tool_id,
        "status": str(response.get("status") or "error"),
        "output": result_block.output,
        "result_state": str(result_block.state),
    }
    try:
        persisted = execution_store.save_checkpoint_cas(
            checkpoint.model_copy(update={"external_result": external_result}),
            expected_version=checkpoint.checkpoint_version,
            expected_states={checkpoint.state},
        )
    except RuntimeExecutionCancelled:
        raise
    except Exception as exc:
        _mark_external_outcome_indeterminate(
            execution_store,
            checkpoint,
            session,
            execution_id,
        )
        raise ToolOutcomeIndeterminate(
            "external tool returned but its outcome could not be persisted"
        ) from exc
    if crash_after_persist is not None:
        crash_after_persist()
    recovered = _recover_persisted_external_result(execution_store, persisted)
    assert recovered is not None
    return recovered


def _recover_persisted_external_result(
    execution_store: RuntimeExecutionStore,
    checkpoint: RuntimeCheckpoint,
) -> ExternalExecutionResultEvent | None:
    external_result = checkpoint.external_result
    if not external_result:
        return None
    tool_call_id = str(external_result.get("tool_call_id") or "")
    tool_name = str(external_result.get("tool_name") or "")
    reply_id = str(external_result.get("reply_id") or "")
    if not tool_call_id or not tool_name or not reply_id:
        raise RuntimeCheckpointUnrecoverable(
            "persisted external result is missing tool identity"
        )
    try:
        result_state = ToolResultState(str(external_result.get("result_state")))
    except ValueError as exc:
        raise RuntimeCheckpointUnrecoverable(
            "persisted external result has an invalid state"
        ) from exc
    injected_state = _inject_external_tool_result(
        checkpoint.agent_state,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        output=str(external_result.get("output") or ""),
        state=result_state,
    )
    already_recovered = (
        checkpoint.pending_call is None
        and checkpoint.continuation_pending
        and checkpoint.state == "running"
    )
    if not already_recovered:
        candidate = checkpoint.model_copy(
            update={
                "state": "running",
                "wait_kind": None,
                "wait_subject_id": None,
                "pending_call": None,
                "agent_state": injected_state,
                "continuation_pending": True,
            }
        )
        checkpoint = execution_store.save_checkpoint_cas(
            candidate,
            expected_version=checkpoint.checkpoint_version,
            expected_states={checkpoint.state},
        )
    return ExternalExecutionResultEvent(
        reply_id=reply_id,
        execution_results=[
            ToolResultBlock(
                id=tool_call_id,
                name=tool_name,
                output=str(external_result.get("output") or ""),
                state=result_state,
            )
        ],
    )


def _mark_external_outcome_indeterminate(
    execution_store: RuntimeExecutionStore,
    checkpoint: RuntimeCheckpoint,
    session: RuntimeSession,
    execution_id: str,
) -> None:
    session.state = "indeterminate"
    try:
        execution_store.save_checkpoint_cas(
            checkpoint.model_copy(
                update={
                    "state": "indeterminate",
                    "external_result": {
                        "execution_id": execution_id,
                        "status": "indeterminate",
                    },
                    "continuation_pending": False,
                }
            ),
            expected_version=checkpoint.checkpoint_version,
            expected_states={checkpoint.state},
        )
    except RuntimeExecutionError:
        logger.exception(
            "Failed to persist indeterminate external outcome run_id=%s execution_id=%s",
            checkpoint.run_id,
            execution_id,
        )


def _external_result_event(
    event: RequireExternalExecutionEvent,
    *,
    tool_call_id: str,
    tool_name: str,
    response: dict[str, Any],
) -> ExternalExecutionResultEvent:
    response_status = str(response.get("status") or "error")
    state = {
        "success": ToolResultState.SUCCESS,
        "approval_rejected": ToolResultState.DENIED,
        "accepted": ToolResultState.SUCCESS,
        "declined": ToolResultState.DENIED,
        "cancelled": ToolResultState.INTERRUPTED,
        "timeout": ToolResultState.INTERRUPTED,
    }.get(response_status, ToolResultState.ERROR)
    output = response.get("content")
    if not isinstance(output, str):
        output = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    return ExternalExecutionResultEvent(
        reply_id=event.reply_id,
        execution_results=[
            ToolResultBlock(
                id=tool_call_id,
                name=tool_name,
                output=output,
                state=state,
            )
        ],
    )


async def _finalize_ordinary_qa(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    request: RunStartRequest,
    execution_store: RuntimeExecutionStore | None = None,
) -> None:
    payload = {"runtime_session_id": session.runtime_session_id}
    stage = "model-call"
    try:
        if _is_cancelled(session):
            return
        answer = await _call_leader_model(callback_client, session, request)
        if _is_cancelled(session):
            return

        stage = "emit-final-answer"
        await _emit_final_answer(
            callback_client,
            session,
            answer,
            payload,
            execution_store=execution_store,
        )
    except Exception as exc:
        if _is_cancelled(session):
            return
        logger.exception(
            "Runtime ordinary QA finalization failed during %s for run_id=%s runtime_session_id=%s",
            stage,
            session.run_id,
            session.runtime_session_id,
        )
        await _mark_session_failed(
            callback_client,
            session,
            exc,
            stage=stage,
            execution_store=execution_store,
        )


async def _finalize_general_agent_run(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    request: RunStartRequest,
    execution_store: RuntimeExecutionStore | None = None,
) -> None:
    payload = {"runtime_session_id": session.runtime_session_id}
    stage = "general-agent-setup"
    try:
        if _is_cancelled(session):
            return
        bridge = AgentScopeRuntimeBridge(
            run_id=session.run_id,
            runtime_session_id=session.runtime_session_id,
            callback_client=callback_client,
            assistant_context_by_participant={
                LEADER_PARTICIPANT_ID: _agent_context_replay_assistant_messages(
                    request.metadata.get("agent_context_replay"),
                ),
            },
            durable_external_tools=execution_store is not None,
        )
        leader_model_id = request.leader_model_id or _first_model_catalog_id(request.model_catalog)
        if not leader_model_id:
            raise RuntimeError("leader model id is required for agent execution")

        stage = "general-agent-reply"
        leader = Agent(
            name=LEADER_PARTICIPANT_ID,
            system_prompt=_leader_system_prompt(request),
            model=bridge.build_model(
                participant_id=LEADER_PARTICIPANT_ID,
                model_id=leader_model_id,
                default_model_params=_request_model_params(request),
            ),
            toolkit=_build_toolkit(
                bridge=bridge,
                callback_client=callback_client,
                session=session,
                request=request,
                participant_id=LEADER_PARTICIPANT_ID,
                include_subagent_tool=_subagents_enabled(request),
                leader_model_id=leader_model_id,
                durable_external_tools=execution_store is not None,
            ),
            react_config=ReActConfig(max_iters=_max_iters(request)),
        )
        inputs: Any = _request_messages_to_msgs(request)
        while True:
            stream_result = await _run_leader_streaming(
                leader,
                session,
                inputs,
                callback_client=callback_client,
                payload=payload,
                execution_store=execution_store,
            )
            if stream_result.pause_event is None:
                break
            if execution_store is None:
                raise RuntimeError(
                    "AgentScope requested external execution without a durable runtime store"
                )
            resolved = await _resolve_durable_external_event(
                callback_client=callback_client,
                execution_store=execution_store,
                session=session,
                request=request,
                leader=leader,
                bridge=bridge,
                event=stream_result.pause_event,
            )
            if resolved is None:
                return
            session.state = "running"
            session.updated_at = time.time()
            inputs = resolved
        if _is_cancelled(session):
            return
        final_answer = (
            _msg_text(stream_result.final_msg)
            or bridge.latest_final_text(LEADER_PARTICIPANT_ID)
            or stream_result.streamed_text
        )
        stage = "emit-final-answer"
        await _emit_final_answer(
            callback_client,
            session,
            final_answer,
            payload,
            already_emitted_text=stream_result.streamed_text,
            next_delta_index=stream_result.next_delta_index,
            execution_store=execution_store,
        )
    except OpenWebUIToolApprovalRequired:
        if session.state == "failed":
            return
        session.state = "waiting_approval"
        session.updated_at = time.time()
        logger.info(
            "Runtime paused for OpenWebUI tool approval run_id=%s runtime_session_id=%s",
            session.run_id,
            session.runtime_session_id,
        )
    except OpenWebUIToolApprovalRejected as exc:
        await _mark_session_failed(
            callback_client,
            session,
            ApprovalRejectedError(str(exc)),
            stage="approval-rejected",
            payload={
                "approval_id": _approval_id_from_tool_response(exc.response),
                "decision": "rejected",
                "tool_call_id": exc.tool_call_id,
                "tool_id": exc.tool_id,
                "tool_name": exc.tool_name,
            },
            execution_store=execution_store,
        )
    except Exception as exc:
        if _is_cancelled(session):
            return
        provider_error = _provider_configuration_error_from_text(str(exc))
        if provider_error is not None:
            exc = provider_error
        logger.exception(
            "Runtime general agent execution failed during %s for run_id=%s runtime_session_id=%s",
            stage,
            session.run_id,
            session.runtime_session_id,
        )
        await _mark_session_failed(
            callback_client,
            session,
            exc,
            stage=stage,
            execution_store=execution_store,
        )


async def _emit_final_answer(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    answer: str,
    payload: dict[str, Any],
    *,
    already_emitted_text: str = "",
    next_delta_index: int = 0,
    execution_store: RuntimeExecutionStore | None = None,
) -> None:
    """Drive the run to terminal state.

    `answer` is the final answer text. It is the only content emitted through
    final.delta and therefore the only content that should populate
    AgentRun.final_text and the persisted assistant message body. Public
    transcript text, if any, must use text.delta separately and never carries
    raw provider chunks.
    """
    if session.state != "finalizing":
        await _start_final_answer_phase(
            callback_client,
            session,
            payload,
            execution_store=execution_store,
        )
        if _is_cancelled(session):
            return

    remaining_answer = _remaining_final_answer(answer, already_emitted_text)
    for index, chunk in enumerate(_final_delta_chunks(remaining_answer), start=next_delta_index):
        await _append_final_answer_delta(callback_client, session, payload, index, chunk)
        if _is_cancelled(session):
            return
    await _complete_final_answer_phase(
        callback_client,
        session,
        payload,
        execution_store=execution_store,
    )


async def _start_final_answer_phase(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    payload: dict[str, Any],
    execution_store: RuntimeExecutionStore | None = None,
) -> None:
    if session.state != "finalizing":
        await callback_client.append_event(
            run_id=session.run_id,
            idempotency_key=f"evt:{session.runtime_session_id}:final-started",
            event_type="final.started",
            summary="Final answer phase started.",
            payload=payload,
            participant_id="leader",
            phase="finalizing",
        )
        if _is_cancelled(session):
            return
        session.state = "finalizing"
        session.updated_at = time.time()
    _persist_session_state(execution_store, session)


async def _append_final_answer_delta(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    payload: dict[str, Any],
    delta_index: int,
    delta: str,
) -> None:
    await callback_client.append_final_delta(
        run_id=session.run_id,
        idempotency_key=f"final:{session.run_id}:answer:{delta_index}",
        final_stream_id="answer",
        delta_index=delta_index,
        delta=delta,
        participant_id="leader",
        payload=payload,
    )
    await asyncio.sleep(0)


async def _complete_final_answer_phase(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    payload: dict[str, Any],
    execution_store: RuntimeExecutionStore | None = None,
) -> None:
    await callback_client.append_event(
        run_id=session.run_id,
        idempotency_key=f"evt:{session.runtime_session_id}:run-completed",
        event_type="run.completed",
        summary="Agent run completed.",
        payload=payload,
        participant_id="leader",
        phase="completed",
    )
    session.state = "completed"
    session.updated_at = time.time()
    _persist_session_state(execution_store, session)


def _remaining_final_answer(answer: str, already_emitted_text: str) -> str:
    if not already_emitted_text:
        return answer
    if answer.startswith(already_emitted_text):
        return answer[len(already_emitted_text) :]
    logger.warning(
        "Runtime streamed final prefix did not match completed final answer; "
        "suppressing duplicate final replay for run answer mismatch",
    )
    return ""


def _final_delta_chunks(answer: str) -> list[str]:
    if not answer:
        return []
    chunk_size = max(1, FINAL_DELTA_CHUNK_CHARS)
    return [answer[index : index + chunk_size] for index in range(0, len(answer), chunk_size)]


async def _run_leader_streaming(
    leader,
    session: RuntimeSession,
    messages: list[Msg],
    *,
    callback_client: RuntimeCallbackClient | None = None,
    payload: dict[str, Any] | None = None,
    execution_store: RuntimeExecutionStore | None = None,
) -> FinalAnswerStreamResult:
    """Consume leader.reply_stream() events.

    TextBlockDeltaEvent carries provider chunks produced by the leader model.
    When a final-answer chunk arrives, start the final phase and forward it as
    final.delta so the user sees the answer grow while the model is generating.
    Tool and transcript events keep using their existing callback paths.
    """
    final_msg = None
    emitted_parts: list[str] = []
    buffered_parts: list[str] = []
    buffered_chars = 0
    buffer_started_at: float | None = None
    next_delta_index = 0
    pause_event: RequireExternalExecutionEvent | None = None

    def buffered_elapsed() -> float:
        if buffer_started_at is None:
            return 0.0
        return time.monotonic() - buffer_started_at

    async def flush_buffer() -> None:
        nonlocal buffered_chars, buffer_started_at, next_delta_index
        if not buffered_parts or callback_client is None or payload is None:
            return
        chunk = "".join(buffered_parts)
        await _append_final_answer_delta(
            callback_client,
            session,
            payload,
            next_delta_index,
            chunk,
        )
        emitted_parts.append(chunk)
        buffered_parts.clear()
        buffered_chars = 0
        buffer_started_at = None
        next_delta_index += 1

    async def process_event(event) -> None:
        nonlocal buffered_chars, buffer_started_at, final_msg, pause_event
        if _is_cancelled(session):
            return
        if (
            callback_client is not None
            and payload is not None
            and isinstance(event, TextBlockDeltaEvent)
            and event.delta
        ):
            if session.state != "finalizing":
                await _start_final_answer_phase(
                    callback_client,
                    session,
                    payload,
                    execution_store=execution_store,
                )
            if _is_cancelled(session):
                return
            if not buffered_parts:
                buffer_started_at = time.monotonic()
            buffered_parts.append(event.delta)
            buffered_chars += len(event.delta)
            if (
                buffered_chars >= max(1, FINAL_DELTA_STREAM_CHUNK_CHARS)
                or buffered_elapsed() >= max(0.0, FINAL_DELTA_STREAM_FLUSH_SECONDS)
            ):
                await flush_buffer()
        if _msg_text(event):
            final_msg = event
        if isinstance(event, RequireExternalExecutionEvent):
            pause_event = event

    event_iterator = leader.reply_stream(messages).__aiter__()
    pending_event: asyncio.Task | None = asyncio.create_task(anext(event_iterator))
    try:
        while pending_event is not None:
            if _is_cancelled(session):
                return FinalAnswerStreamResult(
                    final_msg=final_msg,
                    streamed_text="".join(emitted_parts),
                    next_delta_index=next_delta_index,
                    pause_event=pause_event,
                )
            timeout = CANCELLATION_POLL_SECONDS
            if buffered_parts:
                timeout = min(
                    timeout,
                    max(0.0, FINAL_DELTA_STREAM_FLUSH_SECONDS - buffered_elapsed()),
                )
            done, _pending = await asyncio.wait({pending_event}, timeout=timeout)
            if pending_event not in done:
                if buffered_parts and buffered_elapsed() >= max(
                    0.0, FINAL_DELTA_STREAM_FLUSH_SECONDS
                ):
                    await flush_buffer()
                if _is_cancelled(session):
                    return FinalAnswerStreamResult(
                        final_msg=final_msg,
                        streamed_text="".join(emitted_parts),
                        next_delta_index=next_delta_index,
                        pause_event=pause_event,
                    )
                continue

            try:
                event = pending_event.result()
            except StopAsyncIteration:
                pending_event = None
                break
            pending_event = asyncio.create_task(anext(event_iterator))
            await process_event(event)
            if pause_event is not None:
                break
        await flush_buffer()
    finally:
        if pending_event is not None and not pending_event.done():
            pending_event.cancel()
            try:
                await pending_event
            except asyncio.CancelledError:
                pass
        close_stream = getattr(event_iterator, "aclose", None)
        if callable(close_stream):
            try:
                await close_stream()
            except RuntimeError:
                pass
    return FinalAnswerStreamResult(
        final_msg=final_msg,
        streamed_text="".join(emitted_parts),
        next_delta_index=next_delta_index,
        pause_event=pause_event,
    )


def _should_use_general_agent(request: RunStartRequest) -> bool:
    return _has_tool_access(request) or _subagents_enabled(request)


def _has_tool_access(request: RunStartRequest) -> bool:
    tools = request.tool_access_envelope.get("tools")
    return isinstance(tools, list) and bool(tools)


def _subagents_enabled(request: RunStartRequest) -> bool:
    if request.team_cap is not None:
        return request.team_cap > 0
    if request.budget.get("max_subagent_model_calls") is not None:
        return True
    return request.metadata.get("enable_subagents") is True


def _build_toolkit(
    *,
    bridge: AgentScopeRuntimeBridge,
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    request: RunStartRequest,
    participant_id: str,
    include_subagent_tool: bool,
    leader_model_id: str | None = None,
    durable_external_tools: bool = False,
) -> Toolkit:
    tools: list[ToolBase] = []
    for tool_spec in _tool_specs(request):
        schema = tool_spec.get("schema") if isinstance(tool_spec.get("schema"), dict) else {}
        name = str(tool_spec.get("name") or schema.get("name") or tool_spec.get("id"))
        if not name:
            continue
        description = str(schema.get("description") or tool_spec.get("description") or name)
        input_schema = schema.get("parameters")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        tools.append(
            bridge.build_tool_proxy(
                participant_id=participant_id,
                tool_id=str(tool_spec.get("id") or name),
                name=name,
                description=description,
                input_schema=input_schema,
            )
        )

    tools.append(
        RequestUserInputTool(
            callback_client=callback_client,
            session=session,
            participant_id=participant_id,
            allocate_tool_call_id=bridge._allocate_tool_call_id,
            durable_external_execution=durable_external_tools,
        )
    )

    if include_subagent_tool:
        tools.append(
            CreateSubagentTool(
                callback_client=callback_client,
                session=session,
                request=request,
                leader_model_id=leader_model_id,
            )
        )
    return Toolkit(tools=tools)


def _tool_specs(request: RunStartRequest) -> list[dict[str, Any]]:
    tools = request.tool_access_envelope.get("tools")
    if not isinstance(tools, list):
        return []
    return [tool for tool in tools if isinstance(tool, dict)]


def _user_input_requested_schema(
    requested_schema: dict[str, Any] | None,
    questions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    schema = dict(requested_schema or {})
    if questions:
        schema.setdefault("type", "object")
        schema["questions"] = questions[:3]
    if not schema:
        schema = {
            "type": "object",
            "properties": {
                "response": {
                    "type": "string",
                    "title": "Response",
                }
            },
            "required": ["response"],
        }
    return schema


class RequestUserInputTool(ToolBase):
    name = "request_user_input"
    description = (
        "Use this tool only when the agent cannot continue without user-provided "
        "information, preference, or selection. Do not use it for safety approvals, "
        "secrets, passwords, API keys, tokens, cookies, private credentials, or "
        "information already available in the conversation. Ask one concise question "
        "unless a structured form is necessary. Returns a JSON object with status "
        "accepted, declined, cancelled, or timeout, and accepted content when provided. "
        "Prefer questions[].options[] when the user should choose from 2-3 clear options; "
        "the UI will still allow a custom answer."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Concise user-facing question or instruction.",
            },
            "questions": {
                "type": "array",
                "description": "Optional Codex-style choice questions. Prefer 1 question; maximum 3.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "header": {"type": "string"},
                        "question": {"type": "string"},
                        "response_key": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                    "value": {},
                                    "recommended": {"type": "boolean"},
                                },
                                "required": ["label"],
                            },
                        },
                        "allow_custom": {"type": "boolean"},
                    },
                    "required": ["question", "options"],
                },
            },
            "requested_schema": {
                "type": "object",
                "description": "Fallback JSON schema for the user response. Prefer a small object with 1-3 fields.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Optional wait timeout in seconds.",
            },
            "allow_cancel": {
                "type": "boolean",
                "description": "Whether the user may cancel the request.",
            },
        },
        "required": ["message"],
    }
    is_concurrency_safe = False
    is_read_only = True

    def __init__(
        self,
        *,
        callback_client: RuntimeCallbackClient,
        session: RuntimeSession,
        participant_id: str,
        allocate_tool_call_id,
        durable_external_execution: bool = False,
    ) -> None:
        self.callback_client = callback_client
        self.session = session
        self.participant_id = participant_id
        self._allocate_tool_call_id = allocate_tool_call_id
        self.is_external_tool = durable_external_execution

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="OpenWebUI will request input from the current user.",
        )

    async def __call__(
        self,
        *,
        message: str,
        requested_schema: dict[str, Any] | None = None,
        questions: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
        allow_cancel: bool = True,
    ) -> ToolChunk:
        tool_call_id = self._allocate_tool_call_id()
        user_input_id = f"user-input:{self.session.run_id}:{tool_call_id}"
        self.session.state = "waiting_user_input"
        self.session.updated_at = time.time()
        try:
            response = await self.callback_client.request_user_input(
                run_id=self.session.run_id,
                idempotency_key=f"user-input:{self.participant_id}:{tool_call_id}:1",
                participant_id=self.participant_id,
                user_input_id=user_input_id,
                tool_call_id=tool_call_id,
                checkpoint_version=0,
                message=message,
                requested_schema=_user_input_requested_schema(requested_schema, questions),
                timeout_seconds=timeout_seconds,
                allow_cancel=allow_cancel,
            )
        finally:
            if self.session.state == "waiting_user_input":
                self.session.state = "running"
                self.session.updated_at = time.time()
        status = str(response.get("status") or "cancelled")
        if status == "accepted":
            state = ToolResultState.SUCCESS
        elif status == "declined":
            state = ToolResultState.DENIED
        else:
            state = ToolResultState.INTERRUPTED
        return ToolChunk(
            content=[TextBlock(text=json.dumps(response, ensure_ascii=False, separators=(",", ":")))],
            state=state,
            metadata={
                "user_input": response,
                "participant_id": self.participant_id,
                "tool_call_id": tool_call_id,
                "user_input_id": user_input_id,
            },
        )


class CreateSubagentTool(ToolBase):
    name = "create_subagent"
    description = "Delegate a focused subtask to a separate worker agent."
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short worker name."},
            "description": {"type": "string", "description": "Worker role."},
            "task": {"type": "string", "description": "Specific delegated task."},
            "requested_model_id": {
                "type": "string",
                "description": "Optional exact model id for the worker.",
            },
            "fuzzy_model_request": {
                "type": "string",
                "description": "Optional model capability request.",
            },
        },
        "required": ["name", "description", "task"],
    }
    is_concurrency_safe = False
    is_read_only = False

    def __init__(
        self,
        *,
        callback_client: RuntimeCallbackClient,
        session: RuntimeSession,
        request: RunStartRequest,
        leader_model_id: str | None = None,
    ) -> None:
        self.callback_client = callback_client
        self.session = session
        self.request = request
        self.leader_model_id = leader_model_id

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Subagent creation is governed by OpenWebUI runtime limits.",
        )

    async def __call__(
        self,
        *,
        name: str,
        description: str,
        task: str,
        requested_model_id: str | None = None,
        fuzzy_model_request: str | None = None,
    ) -> ToolChunk:
        adapter = AgentScopeSubagentAdapter(
            run_id=self.session.run_id,
            runtime_session_id=self.session.runtime_session_id,
            callback_client=self.callback_client,
            leader_model_id=self.leader_model_id,
            team_cap=self.request.team_cap or 5,
            is_cancelled=lambda: _is_cancelled(self.session),
        )
        spec = SubagentSpec(
            name=name,
            description=description,
            task=task,
            requested_model_id=requested_model_id,
            fuzzy_model_request=fuzzy_model_request,
            budget=_subagent_budget(self.request),
        )
        result = await adapter.run_subagent(
            parent_participant_id=LEADER_PARTICIPANT_ID,
            spec=spec,
            executor=lambda context: _execute_subagent(
                callback_client=self.callback_client,
                session=self.session,
                request=self.request,
                context=context,
            ),
        )
        state = ToolResultState.SUCCESS if result.status == "completed" else ToolResultState.ERROR
        content = result.content or "Subagent finished without a text result."
        return ToolChunk(
            content=[TextBlock(text=content)],
            state=state,
            metadata={"subagent_result": result.metadata},
        )


async def _execute_subagent(
    *,
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    request: RunStartRequest,
    context: SubagentExecutionContext,
) -> dict[str, Any]:
    bridge = AgentScopeRuntimeBridge(
        run_id=session.run_id,
        runtime_session_id=session.runtime_session_id,
        callback_client=callback_client,
    )
    worker = Agent(
        name=context.participant_id,
        system_prompt=_subagent_system_prompt(context),
        model=bridge.build_model(
            participant_id=context.participant_id,
            model_id=context.model_id,
            default_model_params=_request_model_params(request),
        ),
        toolkit=_build_toolkit(
            bridge=bridge,
            callback_client=callback_client,
            session=session,
            request=request,
            participant_id=context.participant_id,
            include_subagent_tool=False,
        ),
        react_config=ReActConfig(max_iters=_subagent_max_iters(context.budget)),
    )
    reply = await worker.reply(UserMsg(name="user", content=context.task))
    return {
        "content": _msg_text(reply) or bridge.latest_final_text(context.participant_id),
        "metadata": {"model_id": context.model_id},
    }


def _request_messages_to_msgs(request: RunStartRequest) -> list[Msg]:
    msgs: list[Msg] = []
    for message in request.messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        if role == "system":
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        name = str(message.get("name") or role)
        blocks = _message_content_text_blocks(message.get("content", ""))
        msgs.append(Msg(name=name, role=role, content=blocks))
    if msgs:
        return msgs
    return [UserMsg(name="user", content="")]


def _message_content_text_blocks(content: Any) -> list[TextBlock]:
    if isinstance(content, str):
        return [TextBlock(text=content)]
    if isinstance(content, list):
        return [
            TextBlock(text=str(item.get("text", "")))
            for item in content
            if isinstance(item, dict) and item.get("type", "text") == "text"
        ]
    return [TextBlock(text=str(content))]


def _message_content_text(content: Any) -> str:
    return "".join(block.text for block in _message_content_text_blocks(content)).strip()


def _msg_text(msg: Any | None) -> str:
    if msg is None:
        return ""
    parts: list[str] = []
    if hasattr(msg, "get_content_blocks"):
        blocks = msg.get_content_blocks()
    elif hasattr(msg, "content"):
        blocks = getattr(msg, "content")
    elif isinstance(msg, dict):
        return _message_content_text(msg.get("content", ""))
    else:
        return ""
    if not isinstance(blocks, list):
        blocks = [blocks]
    for block in blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif hasattr(block, "text"):
            text = getattr(block, "text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def _response_phase_protocol_prompt() -> str:
    return (
        "Response phase protocol: every user-visible text response must declare a phase. "
        "Use phase=commentary for progress text emitted with or before tool use, and use "
        "phase=final_answer only for the terminal answer when no tool call is emitted."
    )


def _leader_system_prompt(request: RunStartRequest) -> str:
    prompt = (
        "You are the leader agent for an OpenWebUI Agent Mode run. "
        "Use the available tools and subagents when they are useful, then "
        f"respond with a concise final answer for the user. {_response_phase_protocol_prompt()}"
    )
    fragments = [prompt]
    outputs_path = request.default_paths.get("outputs")
    if isinstance(outputs_path, str) and outputs_path.strip():
        fragments.append(
            "When the user asks for real files or downloadable artifacts, write them under "
            f"request.default_paths.outputs: {outputs_path.strip()}. Do not invent output paths. "
            "Notes are not a substitute for downloadable files; create the file with a tool and "
            "reference the real path or registered artifact."
        )
    system_fragments = [
        _message_content_text(message.get("content", ""))
        for message in request.messages
        if isinstance(message, dict) and str(message.get("role") or "") == "system"
    ]
    system_fragments = [fragment for fragment in system_fragments if fragment]
    return "\n\n".join([*fragments, *system_fragments])


def _agent_context_replay_assistant_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    messages: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        item_messages = item.get("items")
        if not isinstance(item_messages, list):
            item_messages = item.get("messages")
        if not isinstance(item_messages, list):
            continue
        messages.extend(message for message in item_messages if isinstance(message, dict))
    return messages


def _subagent_system_prompt(context: SubagentExecutionContext) -> str:
    return (
        f"You are {context.name}, a focused worker subagent. "
        f"Role: {context.description}. Complete only the delegated task and "
        f"return the useful result to the leader. {_response_phase_protocol_prompt()}"
    )


def _max_iters(request: RunStartRequest) -> int:
    value = request.budget.get("max_model_calls") or request.budget.get("max_iters")
    try:
        return max(1, min(int(value), 20))
    except (TypeError, ValueError):
        return 10


def _subagent_max_iters(budget: dict[str, Any]) -> int:
    value = budget.get("max_model_calls")
    try:
        return max(1, min(int(value), 10))
    except (TypeError, ValueError):
        return 3


def _subagent_budget(request: RunStartRequest) -> dict[str, Any]:
    value = request.budget.get("max_subagent_model_calls")
    if value is None:
        return {}
    return {"max_model_calls": value}


def _is_cancelled(session: RuntimeSession) -> bool:
    return session.cancel_requested or session.state == "cancelled"


def _request_model_params(request: RunStartRequest) -> dict[str, Any]:
    model_params = request.metadata.get("model_params")
    if not isinstance(model_params, dict):
        return {}
    return dict(model_params)


async def _call_leader_model(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    request: RunStartRequest,
) -> str:
    model_id = request.leader_model_id or _first_model_catalog_id(request.model_catalog)
    if not model_id:
        raise RuntimeError("leader model id is required for ordinary Q&A finalization")

    for attempt in range(1, MODEL_CALL_QUEUED_RETRY_ATTEMPTS + 1):
        try:
            response = await callback_client.call_model(
                run_id=session.run_id,
                idempotency_key="model:leader:model-call-1:1",
                participant_id="leader",
                model_call_id="model-call-1",
                model=model_id,
                messages=request.messages,
                stream=False,
                params=_request_model_params(request),
                metadata={"runtime_session_id": session.runtime_session_id},
            )
            break
        except Exception as exc:
            if attempt < MODEL_CALL_QUEUED_RETRY_ATTEMPTS and _is_queued_model_call_rejection(exc):
                logger.info(
                    "OpenWebUI rejected model call while run is queued; retrying "
                    "run_id=%s runtime_session_id=%s attempt=%s",
                    session.run_id,
                    session.runtime_session_id,
                    attempt,
                )
                await asyncio.sleep(MODEL_CALL_QUEUED_RETRY_DELAY_SECONDS)
                continue

            provider_error = _provider_configuration_error_from_text(str(exc))
            if provider_error is not None:
                raise provider_error from exc
            raise

    provider_error = _provider_configuration_error_from_model_response(response)
    if provider_error is not None:
        raise provider_error

    return _extract_model_text(response)


async def _mark_session_failed(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    exc: Exception,
    *,
    stage: str = "unknown",
    payload: dict[str, Any] | None = None,
    execution_store: RuntimeExecutionStore | None = None,
) -> None:
    if session.state == "failed" and session.failed_closeout_completed:
        return
    session.state = "failed"
    session.updated_at = time.time()
    _persist_session_state(execution_store, session)
    summary = getattr(exc, "user_summary", "Agent runtime finalization failed.")
    event_payload = {"runtime_session_id": session.runtime_session_id, **(payload or {})}
    error = {
        "code": getattr(exc, "code", "runtime_finalization_failed"),
        "message": _format_finalization_error_message(exc, stage),
        "summary": summary,
    }

    event_written = await _retry_failed_closeout_callback(
        "run.failed event",
        session,
        lambda: callback_client.append_event(
            run_id=session.run_id,
            idempotency_key=f"evt:{session.runtime_session_id}:run-failed",
            event_type="run.failed",
            summary=summary,
            payload={"error": error, **event_payload},
            participant_id="leader",
            phase="failed",
        ),
    )
    session.failed_closeout_completed = event_written


async def _retry_failed_closeout_callback(
    operation: str,
    session: RuntimeSession,
    callback,
) -> bool:
    last_exc: Exception | None = None
    for attempt in range(1, FAILED_CLOSEOUT_RETRY_ATTEMPTS + 1):
        try:
            await callback()
            return True
        except Exception as exc:
            last_exc = exc
            if attempt >= FAILED_CLOSEOUT_RETRY_ATTEMPTS:
                break
            logger.warning(
                "Runtime failed closeout callback %s failed for run_id=%s "
                "runtime_session_id=%s attempt=%s/%s: %s",
                operation,
                session.run_id,
                session.runtime_session_id,
                attempt,
                FAILED_CLOSEOUT_RETRY_ATTEMPTS,
                exc,
            )
            await asyncio.sleep(FAILED_CLOSEOUT_RETRY_DELAY_SECONDS)

    logger.error(
        "Runtime could not write failed closeout callback %s for run_id=%s "
        "runtime_session_id=%s after %s attempts: %s",
        operation,
        session.run_id,
        session.runtime_session_id,
        FAILED_CLOSEOUT_RETRY_ATTEMPTS,
        last_exc,
    )
    return False


def _approval_rejected_message(decision: ApprovalDecisionNotification) -> str:
    tool_name = decision.tool_name or decision.tool_id or "tool call"
    return f"User rejected approval {decision.approval_id} for {tool_name}."


def _approval_id_from_tool_response(response: dict[str, Any]) -> str | None:
    raw = response.get("raw")
    if isinstance(raw, dict):
        approval_id = raw.get("approval_id")
        return approval_id if isinstance(approval_id, str) else None
    return None


def _provider_configuration_error_from_model_response(response: dict[str, Any]) -> Exception | None:
    return _provider_configuration_error_from_text(_diagnostic_text(response))


def _provider_configuration_error_from_text(text: str) -> Exception | None:
    normalized = text.lower()
    has_http_error_context = (
        "error http" in normalized
        or "openwebui callback failed" in normalized
        or "http 502" in normalized
        or "http 503" in normalized
    )
    has_auth_failure = "auth_unavailable" in normalized or "no auth available" in normalized
    has_unknown_provider = "unknown provider for model" in normalized
    if has_http_error_context and (has_auth_failure or has_unknown_provider):
        return ProviderConfigurationUnavailable(_truncate_diagnostic_message(text))
    return None


def _diagnostic_text(value: Any) -> str:
    parts: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            parts.append(item)
            return
        if isinstance(item, dict):
            for nested in item.values():
                collect(nested)
            return
        if isinstance(item, list):
            for nested in item:
                collect(nested)
            return
        if item is not None:
            parts.append(str(item))

    collect(value)
    return " ".join(part for part in parts if part)


def _truncate_diagnostic_message(message: str, limit: int = 4000) -> str:
    message = message.strip()
    if len(message) <= limit:
        return message
    return f"{message[:limit]}... [truncated]"


def _format_finalization_error_message(exc: Exception, stage: str) -> str:
    exc_type = type(exc).__name__
    detail = str(exc).strip()
    if detail:
        return f"runtime finalization failed during {stage}: {exc_type}: {detail}"
    return f"runtime finalization failed during {stage}: {exc_type}"


def _is_queued_model_call_rejection(exc: Exception) -> bool:
    message = str(exc)
    return "model_run_rejected" in message and "while queued" in message


def _extract_model_text(response: dict[str, Any]) -> str:
    payload = response.get("response", response)
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(_content_item_text(item) for item in content)
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"]
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    return delta["content"]
        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text
    content = response.get("content")
    return content if isinstance(content, str) else str(payload)


def _content_item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        text = item.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _first_model_catalog_id(model_catalog: list[dict[str, Any]]) -> str | None:
    if not model_catalog:
        return None
    model_id = model_catalog[0].get("id")
    return str(model_id) if model_id else None


def _new_runtime_session_id(run_id: str) -> str:
    return f"rt_{run_id}_{secrets.token_urlsafe(8)}"
