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
        "run_id": "run-1",
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
async def test_append_final_delta_uses_openwebui_final_delta_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post("https://openwebui.test/api/agent/service/runs/run-1/final-delta").mock(
            return_value=Response(200, json={"seq": 3, "event_type": "final.delta"})
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_final_delta(
            run_id="run-1",
            idempotency_key="final:run-1:answer:0",
            final_stream_id="answer",
            delta_index=0,
            delta="final answer",
            participant_id="leader",
            payload={"runtime_session_id": "session"},
        )

    assert response == {"seq": 3, "event_type": "final.delta"}
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert request.calls.last.request.headers["x-agent-idempotency-key"] == "final:run-1:answer:0"
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "final:run-1:answer:0",
        "run_id": "run-1",
        "final_stream_id": "answer",
        "delta_index": 0,
        "delta": "final answer",
        "participant_id": "leader",
        "payload": {"runtime_session_id": "session"},
    }


@pytest.mark.asyncio
async def test_transition_state_uses_openwebui_state_transition_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/state-transition"
        ).mock(return_value=Response(200, json={"id": "run-1", "state": "finalizing"}))
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.transition_state(
            run_id="run-1",
            idempotency_key="state:run-1:finalizing",
            from_states=["running"],
            to_state="finalizing",
            reason="runtime closed work",
            payload={"runtime_session_id": "session"},
        )

    assert response == {"id": "run-1", "state": "finalizing"}
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert request.calls.last.request.headers["x-agent-idempotency-key"] == "state:run-1:finalizing"
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "state:run-1:finalizing",
        "run_id": "run-1",
        "from_states": ["running"],
        "to_state": "finalizing",
        "reason": "runtime closed work",
        "payload": {"runtime_session_id": "session"},
    }


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


@pytest.mark.asyncio
async def test_call_model_uses_openwebui_model_call_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/model-call"
        ).mock(
            return_value=Response(
                200,
                json={
                    "status": "success",
                    "model": "model-research",
                    "response": {"content": "model answer"},
                    "metadata": {"participant_id": "subagent:run-1:1"},
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.call_model(
            run_id="run-1",
            idempotency_key="model:subagent:run-1:1:model-call-1:1",
            participant_id="subagent:run-1:1",
            model_call_id="model-call-1",
            model="model-research",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            stream=False,
            params={"temperature": 0.2},
            metadata={"runtime_session_id": "rt-run-1"},
        )

    assert response["response"]["content"] == "model answer"
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert (
        request.calls.last.request.headers["x-agent-idempotency-key"]
        == "model:subagent:run-1:1:model-call-1:1"
    )
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "model:subagent:run-1:1:model-call-1:1",
        "run_id": "run-1",
        "participant_id": "subagent:run-1:1",
        "model_call_id": "model-call-1",
        "model": "model-research",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "stream": False,
        "params": {"temperature": 0.2},
        "metadata": {"runtime_session_id": "rt-run-1"},
    }


@pytest.mark.asyncio
async def test_call_model_uses_long_model_call_timeout() -> None:
    seen_timeout: dict[str, object] = {}

    def handler(request):
        seen_timeout.update(request.extensions["timeout"])
        return Response(
            200,
            json={
                "status": "success",
                "model": "model-research",
                "response": {"content": "model answer"},
                "metadata": {"participant_id": "leader"},
            },
        )

    async with respx.mock(assert_all_called=True) as router:
        router.post("https://openwebui.test/api/agent/service/runs/run-1/model-call").mock(
            side_effect=handler
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
            timeout=3.0,
            model_call_timeout=90.0,
        )

        response = await client.call_model(
            run_id="run-1",
            idempotency_key="model:leader:model-call-1:1",
            participant_id="leader",
            model_call_id="model-call-1",
            model="model-research",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert response["response"]["content"] == "model answer"
    assert seen_timeout["read"] == 90.0
    assert seen_timeout["connect"] == 3.0


@pytest.mark.asyncio
async def test_call_tool_uses_openwebui_tool_call_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/tool-call"
        ).mock(
            return_value=Response(
                200,
                json={
                    "status": "success",
                    "content": "tool answer",
                    "files": [],
                    "embeds": [],
                    "sources": [],
                    "artifacts": [],
                    "process_refs": [],
                    "warnings": [],
                    "structured_error": None,
                    "raw": None,
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.call_tool(
            run_id="run-1",
            idempotency_key="tool:subagent:run-1:1:tool-call-1:1",
            participant_id="subagent:run-1:1",
            tool_call_id="tool-call-1",
            tool_id="tool-search",
            arguments={"query": "agent mode"},
        )

    assert response["content"] == "tool answer"
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert (
        request.calls.last.request.headers["x-agent-idempotency-key"]
        == "tool:subagent:run-1:1:tool-call-1:1"
    )
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "tool:subagent:run-1:1:tool-call-1:1",
        "run_id": "run-1",
        "participant_id": "subagent:run-1:1",
        "tool_call_id": "tool-call-1",
        "tool_id": "tool-search",
        "arguments": {"query": "agent mode"},
    }
