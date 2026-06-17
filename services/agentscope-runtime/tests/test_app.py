import httpx
import pytest

from agentscope_runtime.app import RuntimeStore, create_app


SERVICE_TOKEN = "runtime-secret"


class RecordingOpenWebUIClient:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append_event(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        event_type: str,
        summary: str | None = None,
        payload: dict | None = None,
        participant_id: str | None = None,
        phase: str | None = None,
    ) -> dict:
        event = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "event_type": event_type,
            "summary": summary,
            "payload": payload,
            "participant_id": participant_id,
            "phase": phase,
        }
        self.events.append(event)
        return {"seq": len(self.events), "event_type": event_type}


def make_client(openwebui_client: RecordingOpenWebUIClient | None = None) -> httpx.AsyncClient:
    app = create_app(
        service_token=SERVICE_TOKEN,
        store=RuntimeStore(),
        openwebui_client=openwebui_client or RecordingOpenWebUIClient(),
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://runtime.test")


@pytest.mark.asyncio
async def test_health_does_not_require_auth() -> None:
    async with make_client() as client:
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_run_start_rejects_bad_service_token() -> None:
    async with make_client() as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": "Bearer wrong"},
            json={"run_id": "run-auth", "chat_id": "chat-1", "messages": []},
        )

        assert response.status_code == 401


@pytest.mark.asyncio
async def test_run_start_accepts_run_records_session_and_appends_running_event() -> None:
    openwebui_client = RecordingOpenWebUIClient()
    async with make_client(openwebui_client) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-1",
                "chat_id": "chat-1",
                "user_message_id": "msg-user",
                "assistant_message_id": "msg-assistant",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {"source": "test"},
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["accepted"] is True
        assert body["runtime_session_id"].startswith("rt_run-1_")

        status = await client.get(
            "/v1/openwebui/runs/run-1/status",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )
        assert status.status_code == 200
        assert status.json()["state"] == "running"
        assert status.json()["cancel_requested"] is False

        assert openwebui_client.events == [
            {
                "run_id": "run-1",
                "idempotency_key": f"evt:{body['runtime_session_id']}:run-running",
                "event_type": "run.running",
                "summary": "Agent runtime accepted run.",
                "payload": {"runtime_session_id": body["runtime_session_id"]},
                "participant_id": "leader",
                "phase": "running",
            }
        ]


@pytest.mark.asyncio
async def test_run_start_surfaces_append_event_failure() -> None:
    class FailingOpenWebUIClient:
        async def append_event(self, **kwargs: object) -> dict:
            raise RuntimeError("callback unavailable")

    async with make_client(FailingOpenWebUIClient()) as client:  # type: ignore[arg-type]
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={"run_id": "run-fail", "chat_id": "chat-1", "messages": []},
        )

        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "openwebui_callback_failed"


@pytest.mark.asyncio
async def test_cancel_marks_existing_run_cancel_requested_without_killing_processes() -> None:
    async with make_client() as client:
        start = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={"run_id": "run-cancel", "chat_id": "chat-1", "messages": []},
        )
        assert start.status_code == 202

        cancel = await client.post(
            "/v1/openwebui/runs/run-cancel/cancel",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )

        assert cancel.status_code == 200
        assert cancel.json()["state"] == "cancelled"
        assert cancel.json()["cancel_requested"] is True

        status = await client.get(
            "/v1/openwebui/runs/run-cancel/status",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )
        assert status.status_code == 200
        assert status.json()["state"] == "cancelled"
        assert status.json()["cancel_requested"] is True


@pytest.mark.asyncio
async def test_status_unknown_run_returns_404() -> None:
    async with make_client() as client:
        response = await client.get(
            "/v1/openwebui/runs/missing/status",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )

        assert response.status_code == 404
