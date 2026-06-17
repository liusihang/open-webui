import json

import pytest
import respx
from httpx import Response

from agentscope_runtime.openwebui_client import OpenWebUIClient


@pytest.mark.asyncio
async def test_append_event_sends_bearer_auth_idempotency_key_and_structured_payload() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post("https://openwebui.test/api/agent/service/runs/run-1/events").mock(
            return_value=Response(200, json={"seq": 1, "event_type": "run.running"})
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_event(
            run_id="run-1",
            idempotency_key="evt:session:event-1",
            event_type="run.running",
            summary="Agent runtime accepted run.",
            payload={"runtime_session_id": "session"},
            participant_id="leader",
            phase="running",
        )

    assert response == {"seq": 1, "event_type": "run.running"}
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert request.calls.last.request.headers["x-agent-idempotency-key"] == "evt:session:event-1"
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "evt:session:event-1",
        "event_type": "run.running",
        "summary": "Agent runtime accepted run.",
        "payload": {"runtime_session_id": "session"},
        "participant_id": "leader",
        "phase": "running",
    }


@pytest.mark.asyncio
async def test_append_event_surfaces_callback_failure() -> None:
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://openwebui.test/api/agent/service/runs/run-1/events").mock(
            return_value=Response(503, json={"detail": "down"})
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        with pytest.raises(RuntimeError, match="OpenWebUI append-event failed"):
            await client.append_event(
                run_id="run-1",
                idempotency_key="evt:session:event-1",
                event_type="run.running",
            )


@pytest.mark.asyncio
async def test_register_subagent_sends_callback_contract() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post("https://openwebui.test/api/agent/service/runs/run-1/subagents").mock(
            return_value=Response(
                200,
                json={
                    "status": "accepted",
                    "participant_id": "subagent:run-1:1",
                    "team_cap": 5,
                    "remaining_slots": 4,
                    "warnings": [],
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.register_subagent(
            run_id="run-1",
            idempotency_key="subagent:run-1:subagent:run-1:1:create",
            parent_participant_id="leader",
            participant_id="subagent:run-1:1",
            name="researcher",
            description="Researches facts.",
            task="Find facts.",
            budget={"max_model_calls": 2},
            metadata={"team_cap": 5, "single_level": True},
        )

    assert response["status"] == "accepted"
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert (
        request.calls.last.request.headers["x-agent-idempotency-key"]
        == "subagent:run-1:subagent:run-1:1:create"
    )
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "subagent:run-1:subagent:run-1:1:create",
        "run_id": "run-1",
        "parent_participant_id": "leader",
        "participant_id": "subagent:run-1:1",
        "name": "researcher",
        "description": "Researches facts.",
        "task": "Find facts.",
        "budget": {"max_model_calls": 2},
        "metadata": {"team_cap": 5, "single_level": True},
    }


@pytest.mark.asyncio
async def test_select_model_uses_openwebui_model_selection_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/model-selection"
        ).mock(
            return_value=Response(
                200,
                json={
                    "selected_model_id": "model-research",
                    "choices": [{"id": "model-research"}],
                    "meta": {"agent_selection": {"reason": "fuzzy_match"}},
                    "warnings": [],
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.select_model(
            run_id="run-1",
            idempotency_key="modelsel:subagent:run-1:1:selection-1:1",
            participant_id="subagent:run-1:1",
            selection_id="selection-1",
            requested_model_id=None,
            fuzzy_request="research long context",
            source_request={"task": "Find facts."},
        )

    assert response["selected_model_id"] == "model-research"
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert (
        request.calls.last.request.headers["x-agent-idempotency-key"]
        == "modelsel:subagent:run-1:1:selection-1:1"
    )
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "modelsel:subagent:run-1:1:selection-1:1",
        "run_id": "run-1",
        "participant_id": "subagent:run-1:1",
        "selection_id": "selection-1",
        "requested_model_id": None,
        "fuzzy_request": "research long context",
        "source_request": {"task": "Find facts."},
    }
