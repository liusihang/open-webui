from __future__ import annotations

import asyncio
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, status

from agentscope_runtime.openwebui_client import OpenWebUIClient
from agentscope_runtime.schemas import RunStartRequest, RunStartResponse, RunStatusResponse


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
    ) -> dict[str, Any]:
        ...

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
    ) -> dict[str, Any]:
        ...

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
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    async def transition_state(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        from_states: list[str],
        to_state: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


@dataclass
class RuntimeSession:
    run_id: str
    runtime_session_id: str
    state: str
    cancel_requested: bool = False
    request: RunStartRequest | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


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
    try:
        answer = await _call_leader_model(callback_client, session, request)
        await callback_client.transition_state(
            run_id=session.run_id,
            idempotency_key=f"state:{session.run_id}:finalizing",
            from_states=["running"],
            to_state="finalizing",
            reason="runtime closed work",
            payload=payload,
        )
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
        await callback_client.append_final_delta(
            run_id=session.run_id,
            idempotency_key=f"final:{session.run_id}:answer:0",
            final_stream_id="answer",
            delta_index=0,
            delta=answer,
            participant_id="leader",
            payload=payload,
        )
        await callback_client.transition_state(
            run_id=session.run_id,
            idempotency_key=f"state:{session.run_id}:completed",
            from_states=["finalizing"],
            to_state="completed",
            reason="runtime final answer completed",
            payload=payload,
        )
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
    except Exception as exc:
        await _mark_session_failed(callback_client, session, exc)


async def _call_leader_model(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    request: RunStartRequest,
) -> str:
    model_id = request.leader_model_id or _first_model_catalog_id(request.model_catalog)
    if not model_id:
        raise RuntimeError("leader model id is required for ordinary Q&A finalization")

    response = await callback_client.call_model(
        run_id=session.run_id,
        idempotency_key="model:leader:model-call-1:1",
        participant_id="leader",
        model_call_id="model-call-1",
        model=model_id,
        messages=request.messages,
        stream=False,
        params={},
        metadata={"runtime_session_id": session.runtime_session_id},
    )
    return _extract_model_text(response)


async def _mark_session_failed(
    callback_client: RuntimeCallbackClient,
    session: RuntimeSession,
    exc: Exception,
) -> None:
    session.state = "failed"
    session.updated_at = time.time()
    error = {
        "code": getattr(exc, "code", "runtime_finalization_failed"),
        "message": str(exc),
    }
    try:
        await callback_client.transition_state(
            run_id=session.run_id,
            idempotency_key=f"state:{session.run_id}:failed",
            from_states=["queued", "running", "waiting_approval", "finalizing"],
            to_state="failed",
            reason="runtime finalization failed",
            payload={"error": error, "runtime_session_id": session.runtime_session_id},
        )
    except Exception:
        pass
    try:
        await callback_client.append_event(
            run_id=session.run_id,
            idempotency_key=f"evt:{session.runtime_session_id}:run-failed",
            event_type="run.failed",
            summary="Agent runtime finalization failed.",
            payload={"error": error, "runtime_session_id": session.runtime_session_id},
            participant_id="leader",
            phase="failed",
        )
    except Exception:
        pass


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
