from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, status

from agentscope_runtime.openwebui_client import OpenWebUIClient
from agentscope_runtime.schemas import RunStartRequest, RunStartResponse, RunStatusResponse


class AppendEventClient(Protocol):
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
    openwebui_client: AppendEventClient | None = None,
    store: RuntimeStore | None = None,
    openwebui_base_url: str | None = None,
    openwebui_service_token: str | None = None,
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


def _status(session: RuntimeSession) -> RunStatusResponse:
    return RunStatusResponse(
        run_id=session.run_id,
        runtime_session_id=session.runtime_session_id,
        state=session.state,
        cancel_requested=session.cancel_requested,
    )


def _new_runtime_session_id(run_id: str) -> str:
    return f"rt_{run_id}_{secrets.token_urlsafe(8)}"
