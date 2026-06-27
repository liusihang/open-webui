import json

import pytest
import respx
from httpx import Response
from pydantic import ValidationError

from agentscope_runtime.openwebui_client import OpenWebUIClient


@pytest.mark.asyncio
async def test_call_model_uses_dedicated_model_call_timeout(monkeypatch) -> None:
    captured_timeouts = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            captured_timeouts.append(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, json=None):
            return Response(
                200,
                json={
                    "status": "success",
                    "model": "model-research",
                    "response": {"content": "model answer"},
                    "metadata": {},
                },
            )

    monkeypatch.setattr("agentscope_runtime.openwebui_client.httpx.AsyncClient", FakeAsyncClient)
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
        timeout=3.0,
        model_call_timeout=45.0,
    )

    await client.append_final_delta(
        run_id="run-1",
        idempotency_key="final:run-1:answer:0",
        final_stream_id="answer",
        delta_index=0,
        delta="final answer",
    )
    await client.call_model(
        run_id="run-1",
        idempotency_key="model:leader:model-call-1:1",
        participant_id="leader",
        model_call_id="model-call-1",
        model="model-research",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert captured_timeouts[0] == 3.0
    assert captured_timeouts[1].connect == 3.0
    assert captured_timeouts[1].read == 45.0


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
async def test_append_event_treats_409_idempotency_conflict_as_success() -> None:
    """When openwebui returns 409 idempotency_conflict for event.append, the
    runtime must treat it as success (event already stored) and not crash the
    agent run. See docs/handoff-agent-runtime-streaming-text-2026-06-20.md.
    """
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://openwebui.test/api/agent/service/runs/run-1/events").mock(
            return_value=Response(
                409,
                json={"detail": "idempotency_conflict", "seq": 7},
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_event(
            run_id="run-1",
            idempotency_key="evt:session:sub-1:completed",
            event_type="subagent.completed",
            summary="Subagent finished.",
            payload={"participant_id": "sub-1", "content": "ok"},
            participant_id="sub-1",
            phase="running",
        )

    assert response["detail"] == "idempotency_conflict"
    assert response["seq"] == 7


@pytest.mark.asyncio
async def test_append_event_treats_409_with_empty_body_as_idempotency_conflict() -> None:
    """A 409 without a JSON body still resolves to a synthetic
    idempotency_conflict payload rather than raising."""
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://openwebui.test/api/agent/service/runs/run-1/events").mock(
            return_value=Response(409, text="")
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_event(
            run_id="run-1",
            idempotency_key="evt:session:run-running",
            event_type="run.running",
        )

    assert response["detail"] == "idempotency_conflict"


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
async def test_append_text_delta_includes_public_block_kind() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post("https://openwebui.test/api/agent/service/runs/run-1/text-delta").mock(
            return_value=Response(200, json={"seq": 4, "event_type": "text.delta"})
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_text_delta(
            run_id="run-1",
            idempotency_key="text:run-1:leader:block-1:0",
            block_id="block-1",
            block_kind="assistant_note",
            delta_index=0,
            delta="Public progress note.",
            participant_id="leader",
            phase="running",
            payload={"runtime_session_id": "session"},
        )

    assert response == {"seq": 4, "event_type": "text.delta"}
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert request.calls.last.request.headers["x-agent-idempotency-key"] == "text:run-1:leader:block-1:0"
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "text:run-1:leader:block-1:0",
        "run_id": "run-1",
        "block_id": "block-1",
        "block_kind": "assistant_note",
        "delta_index": 0,
        "delta": "Public progress note.",
        "participant_id": "leader",
        "phase": "running",
        "payload": {"runtime_session_id": "session"},
    }


@pytest.mark.asyncio
async def test_append_text_delta_rejects_debug_payload_before_callback() -> None:
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
    )

    with pytest.raises(ValidationError, match="debug"):
        await client.append_text_delta(
            run_id="run-1",
            idempotency_key="text:run-1:leader:block-1:0",
            block_id="block-1",
            block_kind="assistant_note",
            delta_index=0,
            delta="Public progress note.",
            payload={"debug": {"trace": "private"}},
        )


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
async def test_call_model_retries_operation_in_progress_until_cached_success() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/model-call"
        ).mock(
            side_effect=[
                Response(202, json={"detail": "operation_in_progress"}),
                Response(202, json={"detail": "operation_in_progress"}),
                Response(
                    200,
                    json={
                        "status": "success",
                        "model": "model-research",
                        "response": {"content": "cached model answer"},
                        "metadata": {"operation_id": "model-call-1"},
                    },
                ),
            ]
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.call_model(
            run_id="run-1",
            idempotency_key="model:leader:model-call-1:1",
            participant_id="leader",
            model_call_id="model-call-1",
            model="model-research",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert response["response"]["content"] == "cached model answer"
    assert len(request.calls) == 3
    assert {call.request.headers["x-agent-idempotency-key"] for call in request.calls} == {
        "model:leader:model-call-1:1"
    }


@pytest.mark.asyncio
async def test_call_model_sends_tools_and_tool_choice_as_top_level_callback_fields() -> None:
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
                    "metadata": {},
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        await client.call_model(
            run_id="run-1",
            idempotency_key="model:leader:model-call-1:1",
            participant_id="leader",
            model_call_id="model-call-1",
            model="model-research",
            messages=[{"role": "user", "content": "read"}],
            stream=False,
            params={"temperature": 0.2},
            tools=tools,
            tool_choice="auto",
            metadata={"runtime_session_id": "rt-run-1"},
        )

    body = json.loads(request.calls.last.request.content)
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
    assert body["params"] == {"temperature": 0.2}
    assert "tools" not in body["params"]
    assert "tool_choice" not in body["params"]


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
