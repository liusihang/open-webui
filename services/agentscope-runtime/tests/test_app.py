import asyncio

import httpx
import pytest

from agentscope_runtime.app import RuntimeStore, create_app


SERVICE_TOKEN = "runtime-secret"


class RecordingOpenWebUIClient:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.final_deltas: list[dict] = []
        self.model_calls: list[dict] = []
        self.state_transitions: list[dict] = []

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

    async def append_final_delta(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        final_stream_id: str,
        delta_index: int,
        delta: str,
        participant_id: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        final_delta = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "final_stream_id": final_stream_id,
            "delta_index": delta_index,
            "delta": delta,
            "participant_id": participant_id,
            "payload": payload,
        }
        self.final_deltas.append(final_delta)
        return {"seq": len(self.events) + len(self.final_deltas), "event_type": "final.delta"}

    async def call_model(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        model_call_id: str,
        model: str,
        messages: list[dict],
        stream: bool,
        params: dict,
        metadata: dict,
    ) -> dict:
        call = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "participant_id": participant_id,
            "model_call_id": model_call_id,
            "model": model,
            "messages": messages,
            "stream": stream,
            "params": params,
            "metadata": metadata,
        }
        self.model_calls.append(call)
        return {
            "status": "success",
            "model": model,
            "response": {"content": "callback final answer"},
        }

    async def transition_state(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        from_states: list[str],
        to_state: str,
        reason: str,
        payload: dict | None = None,
    ) -> dict:
        transition = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "from_states": from_states,
            "to_state": to_state,
            "reason": reason,
            "payload": payload,
        }
        self.state_transitions.append(transition)
        return {"id": run_id, "state": to_state}


def make_client(
    openwebui_client: RecordingOpenWebUIClient | None = None,
    *,
    auto_finalize_ordinary_qa: bool = False,
) -> httpx.AsyncClient:
    app = create_app(
        service_token=SERVICE_TOKEN,
        store=RuntimeStore(),
        openwebui_client=openwebui_client or RecordingOpenWebUIClient(),
        auto_finalize_ordinary_qa=auto_finalize_ordinary_qa,
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
async def test_run_start_finalizes_ordinary_qa_through_model_and_final_delta_callbacks() -> None:
    openwebui_client = RecordingOpenWebUIClient()
    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-final",
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
        for _ in range(20):
            status = await client.get(
                "/v1/openwebui/runs/run-final/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] == "completed":
                break
            await asyncio.sleep(0.01)

    runtime_session_id = body["runtime_session_id"]
    assert [call["model_call_id"] for call in openwebui_client.model_calls] == ["model-call-1"]
    assert openwebui_client.model_calls[0]["idempotency_key"] == "model:leader:model-call-1:1"
    assert openwebui_client.model_calls[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert openwebui_client.state_transitions == [
        {
            "run_id": "run-final",
            "idempotency_key": "state:run-final:finalizing",
            "from_states": ["running"],
            "to_state": "finalizing",
            "reason": "runtime closed work",
            "payload": {"runtime_session_id": runtime_session_id},
        },
        {
            "run_id": "run-final",
            "idempotency_key": "state:run-final:completed",
            "from_states": ["finalizing"],
            "to_state": "completed",
            "reason": "runtime final answer completed",
            "payload": {"runtime_session_id": runtime_session_id},
        },
    ]
    assert openwebui_client.final_deltas == [
        {
            "run_id": "run-final",
            "idempotency_key": "final:run-final:answer:0",
            "final_stream_id": "answer",
            "delta_index": 0,
            "delta": "callback final answer",
            "participant_id": "leader",
            "payload": {"runtime_session_id": runtime_session_id},
        }
    ]
    assert [event["event_type"] for event in openwebui_client.events] == [
        "run.running",
        "final.started",
        "run.completed",
    ]
    assert openwebui_client.events[1]["phase"] == "finalizing"
    assert openwebui_client.events[2]["phase"] == "completed"


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
async def test_run_start_rejects_raw_openwebui_credentials_in_context_payload() -> None:
    openwebui_client = RecordingOpenWebUIClient()
    async with make_client(openwebui_client) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-secret",
                "chat_id": "chat-1",
                "messages": [],
                "metadata": {"user_jwt": "raw-user-token"},
                "tool_access_envelope": {
                    "tools": [
                        {
                            "id": "tool-1",
                            "name": "external",
                            "tool_server_secret": "raw-secret",
                        }
                    ]
                },
            },
        )

        assert response.status_code == 422
        assert "raw credential fields are not accepted" in response.text
        assert openwebui_client.events == []


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
