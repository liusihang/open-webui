from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, status

from agentscope.agent import Agent, ReActConfig
from agentscope.event import TextBlockDeltaEvent
from agentscope.message import Msg, TextBlock, ToolResultState, UserMsg
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk, Toolkit

from agentscope_runtime.agentscope_bridge import (
    AgentScopeRuntimeBridge,
    OpenWebUIToolApprovalRequired,
)
from agentscope_runtime.openwebui_client import OpenWebUIClient
from agentscope_runtime.schemas import (
    ApprovalDecisionNotification,
    RunStartRequest,
    RunStartResponse,
    RunStatusResponse,
)
from agentscope_runtime.subagents import (
    AgentScopeSubagentAdapter,
    LEADER_PARTICIPANT_ID,
    SubagentExecutionContext,
    SubagentSpec,
)

logger = logging.getLogger(__name__)


MODEL_CALL_QUEUED_RETRY_ATTEMPTS = 3
MODEL_CALL_QUEUED_RETRY_DELAY_SECONDS = 0.05
FINAL_DELTA_CHUNK_CHARS = int(os.getenv("AGENT_RUNTIME_FINAL_DELTA_CHUNK_CHARS", "32"))
FINAL_DELTA_STREAM_CHUNK_CHARS = int(
    os.getenv(
        "AGENT_RUNTIME_FINAL_DELTA_STREAM_CHUNK_CHARS",
        os.getenv("AGENT_RUNTIME_FINAL_DELTA_CHUNK_CHARS", "96"),
    )
)
FINAL_DELTA_STREAM_FLUSH_SECONDS = float(os.getenv("AGENT_RUNTIME_FINAL_DELTA_FLUSH_SECONDS", "0.05"))
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

    async def transition_state(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        from_states: list[str],
        to_state: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass
class RuntimeSession:
    run_id: str
    runtime_session_id: str
    state: str
    cancel_requested: bool = False
    start_accepted: bool = False
    request: RunStartRequest | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class FinalAnswerStreamResult:
    final_msg: Any | None = None
    streamed_text: str = ""
    next_delta_index: int = 0


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
    auto_finalize_ordinary_qa: bool = True,
) -> FastAPI:
    runtime_store = store or RuntimeStore()
    callback_client = openwebui_client or OpenWebUIClient(
        base_url=openwebui_base_url or "http://127.0.0.1:8080",
        service_token=openwebui_service_token or service_token,
    )

    def require_service_token(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="service token required")
        token = authorization.removeprefix("Bearer ").strip()
        if not secrets.compare_digest(token, service_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token")

    app = FastAPI(title="OpenWebUI AgentScope Runtime", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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

        session = runtime_store.create(request)
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
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "openwebui_callback_failed",
                    "message": str(exc),
                },
            ) from exc

        if auto_finalize_ordinary_qa:
            if _should_use_general_agent(request):
                asyncio.create_task(_finalize_general_agent_run(callback_client, session, request))
            else:
                asyncio.create_task(_finalize_ordinary_qa(callback_client, session, request))

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
    return create_app(
        service_token=service_token,
        openwebui_base_url=os.getenv("OPENWEBUI_BASE_URL") or "http://127.0.0.1:8080",
        openwebui_service_token=os.getenv("OPENWEBUI_SERVICE_TOKEN") or service_token,
        auto_finalize_ordinary_qa=auto_finalize,
    )


def _status(session: RuntimeSession) -> RunStatusResponse:
    return RunStatusResponse(
        run_id=session.run_id,
        runtime_session_id=session.runtime_session_id,
        state=session.state,
        cancel_requested=session.cancel_requested,
    )


async def _finalize_ordinary_qa(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    request: RunStartRequest,
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
        await _emit_final_answer(callback_client, session, answer, payload)
    except Exception as exc:
        if _is_cancelled(session):
            return
        logger.exception(
            "Runtime ordinary QA finalization failed during %s for run_id=%s runtime_session_id=%s",
            stage,
            session.run_id,
            session.runtime_session_id,
        )
        await _mark_session_failed(callback_client, session, exc, stage=stage)


async def _finalize_general_agent_run(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    request: RunStartRequest,
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
            ),
            react_config=ReActConfig(max_iters=_max_iters(request)),
        )
        stream_result = await _run_leader_streaming(
            leader,
            session,
            _request_messages_to_msgs(request),
            callback_client=callback_client,
            payload=payload,
        )
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
        )
    except OpenWebUIToolApprovalRequired:
        session.state = "waiting_approval"
        session.updated_at = time.time()
        logger.info(
            "Runtime paused for OpenWebUI tool approval run_id=%s runtime_session_id=%s",
            session.run_id,
            session.runtime_session_id,
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
        await _mark_session_failed(callback_client, session, exc, stage=stage)


async def _emit_final_answer(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    answer: str,
    payload: dict[str, Any],
    *,
    already_emitted_text: str = "",
    next_delta_index: int = 0,
) -> None:
    """Drive the run to terminal state.

    `answer` is the final answer text. It is the only content emitted through
    final.delta and therefore the only content that should populate
    AgentRun.final_text and the persisted assistant message body. Public
    transcript text, if any, must use text.delta separately and never carries
    raw provider chunks.
    """
    if session.state != "finalizing":
        await _start_final_answer_phase(callback_client, session, payload)
        if _is_cancelled(session):
            return

    remaining_answer = _remaining_final_answer(answer, already_emitted_text)
    for index, chunk in enumerate(_final_delta_chunks(remaining_answer), start=next_delta_index):
        await _append_final_answer_delta(callback_client, session, payload, index, chunk)
        if _is_cancelled(session):
            return
    await _complete_final_answer_phase(callback_client, session, payload)


async def _start_final_answer_phase(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    payload: dict[str, Any],
) -> None:
    if session.state == "finalizing":
        return
    await callback_client.transition_state(
        run_id=session.run_id,
        idempotency_key=f"state:{session.run_id}:finalizing",
        from_states=["running"],
        to_state="finalizing",
        reason="runtime closed work",
        payload=payload,
    )
    if _is_cancelled(session):
        return
    session.state = "finalizing"
    session.updated_at = time.time()

    await callback_client.append_event(
        run_id=session.run_id,
        idempotency_key=f"evt:{session.runtime_session_id}:final-started",
        event_type="final.started",
        summary="Final answer phase started.",
        payload=payload,
        participant_id="leader",
        phase="finalizing",
    )


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
) -> None:
    await callback_client.transition_state(
        run_id=session.run_id,
        idempotency_key=f"state:{session.run_id}:completed",
        from_states=["finalizing"],
        to_state="completed",
        reason="runtime final answer completed",
        payload=payload,
    )
    if _is_cancelled(session):
        return
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
        nonlocal buffered_chars, buffer_started_at, final_msg
        if _is_cancelled(session):
            return
        if (
            callback_client is not None
            and payload is not None
            and isinstance(event, TextBlockDeltaEvent)
            and event.delta
        ):
            if session.state != "finalizing":
                await _start_final_answer_phase(callback_client, session, payload)
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

    event_iterator = leader.reply_stream(messages).__aiter__()
    pending_event: asyncio.Task | None = asyncio.create_task(anext(event_iterator))
    try:
        while pending_event is not None:
            if _is_cancelled(session):
                return FinalAnswerStreamResult(
                    final_msg=final_msg,
                    streamed_text="".join(emitted_parts),
                    next_delta_index=next_delta_index,
                )
            timeout = None
            if buffered_parts:
                timeout = max(0.0, FINAL_DELTA_STREAM_FLUSH_SECONDS - buffered_elapsed())
            done, _pending = await asyncio.wait({pending_event}, timeout=timeout)
            if pending_event not in done:
                await flush_buffer()
                if _is_cancelled(session):
                    return FinalAnswerStreamResult(
                        final_msg=final_msg,
                        streamed_text="".join(emitted_parts),
                        next_delta_index=next_delta_index,
                    )
                continue

            try:
                event = pending_event.result()
            except StopAsyncIteration:
                pending_event = None
                break
            pending_event = asyncio.create_task(anext(event_iterator))
            await process_event(event)
        await flush_buffer()
    finally:
        if pending_event is not None and not pending_event.done():
            pending_event.cancel()
    return FinalAnswerStreamResult(
        final_msg=final_msg,
        streamed_text="".join(emitted_parts),
        next_delta_index=next_delta_index,
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


def _leader_system_prompt(request: RunStartRequest) -> str:
    prompt = (
        "You are the leader agent for an OpenWebUI Agent Mode run. "
        "Use the available tools and subagents when they are useful, then "
        "respond with a concise final answer for the user."
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


def _subagent_system_prompt(context: SubagentExecutionContext) -> str:
    return (
        f"You are {context.name}, a focused worker subagent. "
        f"Role: {context.description}. Complete only the delegated task and "
        "return the useful result to the leader."
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
) -> None:
    session.state = "failed"
    session.updated_at = time.time()
    summary = getattr(exc, "user_summary", "Agent runtime finalization failed.")
    event_payload = {"runtime_session_id": session.runtime_session_id, **(payload or {})}
    error = {
        "code": getattr(exc, "code", "runtime_finalization_failed"),
        "message": _format_finalization_error_message(exc, stage),
        "summary": summary,
    }
    try:
        await callback_client.transition_state(
            run_id=session.run_id,
            idempotency_key=f"state:{session.run_id}:failed",
            from_states=["queued", "running", "waiting_approval", "finalizing"],
            to_state="failed",
            reason="runtime finalization failed",
            payload={"error": error, **event_payload},
        )
    except Exception:
        pass
    try:
        await callback_client.append_event(
            run_id=session.run_id,
            idempotency_key=f"evt:{session.runtime_session_id}:run-failed",
            event_type="run.failed",
            summary=summary,
            payload={"error": error, **event_payload},
            participant_id="leader",
            phase="failed",
        )
    except Exception:
        pass


def _approval_rejected_message(decision: ApprovalDecisionNotification) -> str:
    tool_name = decision.tool_name or decision.tool_id or "tool call"
    return f"User rejected approval {decision.approval_id} for {tool_name}."


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
