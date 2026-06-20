import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest

from agentscope_runtime.app import RuntimeStore, _msg_text, create_app, create_app_from_env


SERVICE_TOKEN = "runtime-secret"


class RecordingOpenWebUIClient:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.final_deltas: list[dict] = []
        self.model_calls: list[dict] = []
        self.tool_calls: list[dict] = []
        self.subagent_registrations: list[dict] = []
        self.model_selections: list[dict] = []
        self.state_transitions: list[dict] = []
        self.model_responses: list[dict] = [
            {
                "status": "success",
                "model": "model-a",
                "response": {"content": "callback final answer"},
            }
        ]

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
        tools: list[dict] | None = None,
        tool_choice: object | None = None,
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
            "tools": tools,
            "tool_choice": tool_choice,
        }
        self.model_calls.append(call)
        if self.model_responses:
            return self.model_responses.pop(0)
        return {
            "status": "success",
            "model": model,
            "response": {"content": "callback final answer"},
        }

    async def call_tool(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        tool_call_id: str,
        tool_id: str,
        arguments: dict,
    ) -> dict:
        call = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "participant_id": participant_id,
            "tool_call_id": tool_call_id,
            "tool_id": tool_id,
            "arguments": arguments,
        }
        self.tool_calls.append(call)
        return {
            "status": "success",
            "content": "tool callback result",
            "artifacts": [
                {
                    "id": "artifact-1",
                    "name": "result.txt",
                    "mime_type": "text/plain",
                    "url": "/api/files/artifact-1",
                }
            ],
        }

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
        budget: dict,
        metadata: dict,
    ) -> dict:
        registration = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "parent_participant_id": parent_participant_id,
            "participant_id": participant_id,
            "name": name,
            "description": description,
            "task": task,
            "budget": budget,
            "metadata": metadata,
        }
        self.subagent_registrations.append(registration)
        return {"status": "accepted", "participant_id": participant_id}

    async def select_model(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        selection_id: str,
        requested_model_id: str | None = None,
        fuzzy_request: str | None = None,
        source_request: dict | None = None,
    ) -> dict:
        selection = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "participant_id": participant_id,
            "selection_id": selection_id,
            "requested_model_id": requested_model_id,
            "fuzzy_request": fuzzy_request,
            "source_request": source_request or {},
        }
        self.model_selections.append(selection)
        return {"selected_model_id": requested_model_id or "model-a"}

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


class BlockingModelOpenWebUIClient(RecordingOpenWebUIClient):
    def __init__(self) -> None:
        super().__init__()
        self.model_started = asyncio.Event()
        self.release_model = asyncio.Event()

    async def call_model(self, **kwargs: object) -> dict:
        self.model_calls.append(kwargs)
        self.model_started.set()
        await self.release_model.wait()
        return {
            "status": "success",
            "model": kwargs["model"],
            "response": {"content": "callback final answer"},
        }


class FailFirstRunningEventOpenWebUIClient(RecordingOpenWebUIClient):
    def __init__(self) -> None:
        super().__init__()
        self.running_event_attempts = 0

    async def append_event(self, **kwargs: object) -> dict:
        if kwargs["event_type"] == "run.running":
            self.running_event_attempts += 1
            if self.running_event_attempts == 1:
                raise RuntimeError("transient callback outage")
        return await super().append_event(**kwargs)


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


def test_msg_text_extracts_text_from_agentscope_objects_and_dict_like_blocks() -> None:
    from agentscope.message import TextBlock

    msg = SimpleNamespace(
        get_content_blocks=lambda: [
            TextBlock(text="object text"),
            {"type": "text", "text": " dict text"},
            {"type": "data", "name": "artifact.png"},
        ]
    )

    assert _msg_text(msg) == "object text dict text"


@pytest.mark.asyncio
async def test_health_does_not_require_auth() -> None:
    async with make_client() as client:
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_create_app_from_env_uses_operator_runtime_configuration(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_SERVICE_TOKEN", "env-token")
    monkeypatch.setenv("OPENWEBUI_BASE_URL", "https://openwebui.internal")
    monkeypatch.setenv("OPENWEBUI_SERVICE_TOKEN", "callback-token")
    monkeypatch.setenv("AGENT_RUNTIME_AUTO_FINALIZE_ORDINARY_QA", "false")

    app = create_app_from_env()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://runtime.test",
    ) as client:
        health = await client.get("/health")
        unauthorized = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": "Bearer wrong"},
            json={"run_id": "run-env", "chat_id": "chat-1", "messages": []},
        )

    assert health.status_code == 200
    assert unauthorized.status_code == 401


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
async def test_run_start_with_tool_envelope_drives_tool_artifact_and_final_lifecycle() -> None:
    openwebui_client = RecordingOpenWebUIClient()
    openwebui_client.model_responses = [
        {
            "status": "success",
            "response": {
                "choices": [
                    {
                        "message": {
                            "content": "I will read the file.",
                            "tool_calls": [
                                {
                                    "id": "call_read_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{\"path\":\"/tmp/input.txt\"}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        },
        {
            "status": "success",
            "response": {"content": "The file says: tool callback result"},
        },
    ]

    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-tool",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "Read the file."}],
                "tool_access_envelope": {
                    "tools": [
                        {
                            "id": "tool:terminal:main:read_file",
                            "name": "read_file",
                            "type": "terminal",
                            "schema": {
                                "name": "read_file",
                                "description": "Read a file.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"path": {"type": "string"}},
                                    "required": ["path"],
                                },
                            },
                        }
                    ]
                },
            },
        )

        assert response.status_code == 202
        for _ in range(40):
            status = await client.get(
                "/v1/openwebui/runs/run-tool/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] == "completed":
                break
            await asyncio.sleep(0.01)

    assert [call["model_call_id"] for call in openwebui_client.model_calls] == [
        "model-call-1",
        "model-call-2",
    ]
    assert openwebui_client.model_calls[0]["tools"][0]["function"]["name"] == "read_file"
    assert "tools" not in openwebui_client.model_calls[0]["params"]
    assert openwebui_client.tool_calls == [
        {
            "run_id": "run-tool",
            "idempotency_key": "tool:leader:tool-call-1:1",
            "participant_id": "leader",
            "tool_call_id": "tool-call-1",
            "tool_id": "tool:terminal:main:read_file",
            "arguments": {"path": "/tmp/input.txt"},
        }
    ]
    assert [event["event_type"] for event in openwebui_client.events] == [
        "run.running",
        "tool.requested",
        "tool.completed",
        "artifact.registered",
        "final.started",
        "run.completed",
    ]
    assert openwebui_client.events[1]["summary"] == "Read a file."
    assert openwebui_client.events[2]["summary"] == "Read file completed."
    assert openwebui_client.events[3]["payload"]["artifact"]["id"] == "artifact-1"
    assert openwebui_client.final_deltas[0]["delta"] == "The file says: tool callback result"


@pytest.mark.asyncio
async def test_general_agent_stops_when_tool_authority_requires_approval() -> None:
    class ApprovalRequiredToolClient(RecordingOpenWebUIClient):
        async def call_model(self, **kwargs: object) -> dict:
            await super().call_model(**kwargs)  # type: ignore[arg-type]
            if len(self.model_calls) > 1:
                raise RuntimeError(
                    "OpenWebUI callback failed with status 403: "
                    '{"detail":{"code":"model_run_rejected","message":'
                    '"Agent run run-tool-approval cannot execute model calls while waiting_approval"}}'
                )
            return {
                "status": "success",
                "response": {
                    "choices": [
                        {
                            "message": {
                                "content": "I need to inspect the file.",
                                "tool_calls": [
                                    {
                                        "id": "call_read_1",
                                        "type": "function",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": "{\"path\":\"/tmp/input.txt\"}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            }

        async def call_tool(self, **kwargs: object) -> dict:
            await super().call_tool(**kwargs)  # type: ignore[arg-type]
            return {
                "status": "approval_required",
                "content": "Approval is required before running read_file.",
                "approval_request_id": "approval-1",
            }

    openwebui_client = ApprovalRequiredToolClient()
    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-tool-approval",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "Read the file."}],
                "tool_access_envelope": {
                    "tools": [
                        {
                            "id": "tool:terminal:main:read_file",
                            "name": "read_file",
                            "type": "terminal",
                            "schema": {
                                "name": "read_file",
                                "description": "Read a file.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"path": {"type": "string"}},
                                    "required": ["path"],
                                },
                            },
                        }
                    ]
                },
            },
        )

        assert response.status_code == 202
        for _ in range(40):
            status = await client.get(
                "/v1/openwebui/runs/run-tool-approval/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] in {"waiting_approval", "completed", "failed"}:
                break
            await asyncio.sleep(0.01)

    assert status.json()["state"] == "waiting_approval"
    assert [call["model_call_id"] for call in openwebui_client.model_calls] == ["model-call-1"]
    assert len(openwebui_client.tool_calls) == 1
    assert [event["event_type"] for event in openwebui_client.events] == [
        "run.running",
        "tool.requested",
    ]
    assert openwebui_client.final_deltas == []
    assert not any(event["event_type"] == "run.failed" for event in openwebui_client.events)


@pytest.mark.asyncio
async def test_general_agent_finalizes_with_code_interpreter_system_pyodide_message() -> None:
    openwebui_client = RecordingOpenWebUIClient()
    openwebui_client.model_responses = [
        {
            "status": "success",
            "response": {"content": "I can use the code interpreter when needed."},
        }
    ]

    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-code-interpreter-system",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [
                    {
                        "role": "system",
                        "name": "system",
                        "content": "##### Pyodide Environment\nPython packages are available in-browser.",
                    },
                    {"role": "user", "content": "Use code if it helps, then answer."},
                ],
                "tool_access_envelope": {
                    "tools": [
                        {
                            "id": "tool:code_interpreter:main:run_python",
                            "name": "run_python",
                            "type": "code_interpreter",
                            "schema": {
                                "name": "run_python",
                                "description": "Run Python code in Pyodide.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"code": {"type": "string"}},
                                    "required": ["code"],
                                },
                            },
                        }
                    ]
                },
            },
        )

        assert response.status_code == 202
        for _ in range(40):
            status = await client.get(
                "/v1/openwebui/runs/run-code-interpreter-system/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)

    assert status.json()["state"] == "completed"
    model_messages = openwebui_client.model_calls[0]["messages"]
    assert [message["role"] for message in model_messages] == ["system", "user"]
    assert "##### Pyodide Environment" in model_messages[0]["content"][0]["text"]
    assert model_messages[1]["content"][0]["text"] == "Use code if it helps, then answer."
    assert openwebui_client.final_deltas[0]["delta"] == "I can use the code interpreter when needed."
    assert not any(event["event_type"] == "run.failed" for event in openwebui_client.events)


@pytest.mark.asyncio
async def test_general_agent_model_call_retries_queued_rejection() -> None:
    class QueuedOnceAgentScopeModelClient(RecordingOpenWebUIClient):
        async def call_model(self, **kwargs: object) -> dict:
            await super().call_model(**kwargs)  # type: ignore[arg-type]
            if len(self.model_calls) == 1:
                raise RuntimeError(
                    "OpenWebUI callback failed with status 403: "
                    '{"detail":{"code":"model_run_rejected","message":'
                    '"Agent run run-agentscope-queued cannot execute model calls while queued"}}'
                )
            return {
                "status": "success",
                "model": kwargs["model"],
                "response": {"content": "retried AgentScope final answer"},
            }

    openwebui_client = QueuedOnceAgentScopeModelClient()
    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-agentscope-queued",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "Use a tool if useful, then answer."}],
                "tool_access_envelope": {
                    "tools": [
                        {
                            "id": "tool:terminal:main:list_files",
                            "name": "list_files",
                            "type": "terminal",
                            "schema": {
                                "name": "list_files",
                                "description": "List files.",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ]
                },
            },
        )

        assert response.status_code == 202
        for _ in range(40):
            status = await client.get(
                "/v1/openwebui/runs/run-agentscope-queued/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)

    assert status.json()["state"] == "completed"
    assert [call["model_call_id"] for call in openwebui_client.model_calls] == [
        "model-call-1",
        "model-call-1",
    ]
    assert {call["idempotency_key"] for call in openwebui_client.model_calls} == {
        "model:leader:model-call-1:1"
    }
    assert openwebui_client.final_deltas[0]["delta"] == "retried AgentScope final answer"
    assert not any(event["event_type"] == "run.failed" for event in openwebui_client.events)


@pytest.mark.asyncio
async def test_general_agent_model_call_retries_timeout_until_cached_success() -> None:
    class TimeoutThenCachedSuccessAgentScopeModelClient(RecordingOpenWebUIClient):
        async def call_model(self, **kwargs: object) -> dict:
            await super().call_model(**kwargs)  # type: ignore[arg-type]
            if len(self.model_calls) == 1:
                raise httpx.ReadTimeout("model callback still in flight")
            return {
                "status": "success",
                "model": kwargs["model"],
                "response": {"content": "cached callback final answer"},
            }

    openwebui_client = TimeoutThenCachedSuccessAgentScopeModelClient()
    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-agentscope-timeout",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "Use a tool if useful, then answer."}],
                "tool_access_envelope": {
                    "tools": [
                        {
                            "id": "tool:terminal:main:list_files",
                            "name": "list_files",
                            "type": "terminal",
                            "schema": {
                                "name": "list_files",
                                "description": "List files.",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ]
                },
            },
        )

        assert response.status_code == 202
        for _ in range(40):
            status = await client.get(
                "/v1/openwebui/runs/run-agentscope-timeout/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)

    assert status.json()["state"] == "completed"
    assert [call["model_call_id"] for call in openwebui_client.model_calls] == [
        "model-call-1",
        "model-call-1",
    ]
    assert {call["idempotency_key"] for call in openwebui_client.model_calls} == {
        "model:leader:model-call-1:1"
    }
    assert openwebui_client.final_deltas[0]["delta"] == "cached callback final answer"
    assert not any(event["event_type"] == "run.failed" for event in openwebui_client.events)


@pytest.mark.asyncio
async def test_leader_system_prompt_guides_real_files_to_default_outputs_path() -> None:
    openwebui_client = RecordingOpenWebUIClient()
    openwebui_client.model_responses = [
        {
            "status": "success",
            "response": {"content": "I will write real outputs into the default outputs path."},
        }
    ]

    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-default-outputs-guidance",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "Create a downloadable artifact."}],
                "default_paths": {"outputs": "/srv/agent-runs/run-default-outputs-guidance/outputs"},
                "tool_access_envelope": {
                    "tools": [
                        {
                            "id": "tool:terminal:main:list_files",
                            "name": "list_files",
                            "type": "terminal",
                            "schema": {
                                "name": "list_files",
                                "description": "List files.",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ]
                },
            },
        )

        assert response.status_code == 202
        for _ in range(40):
            status = await client.get(
                "/v1/openwebui/runs/run-default-outputs-guidance/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)

    assert status.json()["state"] == "completed"
    system_text = openwebui_client.model_calls[0]["messages"][0]["content"][0]["text"]
    assert "/srv/agent-runs/run-default-outputs-guidance/outputs" in system_text
    assert "request.default_paths.outputs" in system_text
    assert "Notes are not a substitute" in system_text


@pytest.mark.asyncio
async def test_create_subagent_tool_emits_subagent_events_and_integrates_result() -> None:
    openwebui_client = RecordingOpenWebUIClient()
    openwebui_client.model_responses = [
        {
            "status": "success",
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_subagent_1",
                                    "type": "function",
                                    "function": {
                                        "name": "create_subagent",
                                        "arguments": (
                                            "{\"name\":\"Researcher\","
                                            "\"description\":\"Checks the facts.\","
                                            "\"task\":\"Find the key fact.\","
                                            "\"requested_model_id\":\"model-a\"}"
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        },
        {
            "status": "success",
            "response": {"content": "Subagent found the key fact."},
        },
        {
            "status": "success",
            "response": {"content": "Integrated: Subagent found the key fact."},
        },
    ]

    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-subagent",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "Delegate research."}],
                "model_catalog": [{"id": "model-a"}],
                "budget": {"max_subagent_model_calls": 1},
            },
        )

        assert response.status_code == 202
        for _ in range(60):
            status = await client.get(
                "/v1/openwebui/runs/run-subagent/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] == "completed":
                break
            await asyncio.sleep(0.01)

    assert [registration["name"] for registration in openwebui_client.subagent_registrations] == [
        "Researcher"
    ]
    assert openwebui_client.model_selections[0]["requested_model_id"] == "model-a"
    assert [call["participant_id"] for call in openwebui_client.model_calls] == [
        "leader",
        "subagent:run-subagent:1",
        "leader",
    ]
    assert "Subagent found the key fact." in openwebui_client.final_deltas[0]["delta"]
    assert "subagent.created" in [event["event_type"] for event in openwebui_client.events]
    assert "subagent.completed" in [event["event_type"] for event in openwebui_client.events]


@pytest.mark.asyncio
async def test_create_subagent_tool_uses_leader_model_when_selection_has_no_choices() -> None:
    class NoAllowedModelChoicesClient(RecordingOpenWebUIClient):
        async def select_model(self, **kwargs: object) -> dict:
            await super().select_model(**kwargs)
            raise RuntimeError(
                "OpenWebUI callback failed with status 403: "
                '{"detail":{"code":"model_selection_not_allowed",'
                '"message":"No models are available for this run.",'
                '"warnings":[{"code":"no_permission_valid_models",'
                '"message":"No models are available for this run."}]}}'
            )

    openwebui_client = NoAllowedModelChoicesClient()
    openwebui_client.model_responses = [
        {
            "status": "success",
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_subagent_1",
                                    "type": "function",
                                    "function": {
                                        "name": "create_subagent",
                                        "arguments": (
                                            "{\"name\":\"Researcher\","
                                            "\"description\":\"Checks the facts.\","
                                            "\"task\":\"Find the key fact.\","
                                            "\"fuzzy_model_request\":\"small fast model\"}"
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        },
        {
            "status": "success",
            "response": {"content": "Subagent completed via leader fallback."},
        },
        {
            "status": "success",
            "response": {"content": "Integrated fallback result."},
        },
    ]

    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-subagent-fallback",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "Delegate research."}],
                "model_catalog": [{"id": "model-a"}],
                "budget": {"max_subagent_model_calls": 1},
            },
        )

        assert response.status_code == 202
        for _ in range(60):
            status_response = await client.get(
                "/v1/openwebui/runs/run-subagent-fallback/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status_response.json()["state"] == "completed":
                break
            await asyncio.sleep(0.01)

    assert openwebui_client.model_selections[0]["requested_model_id"] is None
    assert openwebui_client.model_selections[0]["fuzzy_request"] == "small fast model"
    assert [call["model"] for call in openwebui_client.model_calls] == [
        "model-a",
        "model-a",
        "model-a",
    ]
    created_event = next(
        event for event in openwebui_client.events if event["event_type"] == "subagent.created"
    )
    assert created_event["payload"]["model_selection"]["fallback"] is True
    assert created_event["payload"]["model_selection"]["meta"]["agent_selection"]["reason"] == (
        "leader_model_fallback_no_allowed_choices"
    )
    assert "Integrated fallback result." in openwebui_client.final_deltas[0]["delta"]


@pytest.mark.asyncio
async def test_cancel_during_ordinary_qa_finalization_keeps_session_cancelled_without_final_callbacks() -> None:
    openwebui_client = BlockingModelOpenWebUIClient()
    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        start = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-cancel-finalizing",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert start.status_code == 202
        await asyncio.wait_for(openwebui_client.model_started.wait(), timeout=1)

        cancel = await client.post(
            "/v1/openwebui/runs/run-cancel-finalizing/cancel",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )
        assert cancel.status_code == 200
        assert cancel.json()["state"] == "cancelled"

        openwebui_client.release_model.set()
        await asyncio.sleep(0.05)

        status = await client.get(
            "/v1/openwebui/runs/run-cancel-finalizing/status",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )

    assert status.status_code == 200
    assert status.json()["state"] == "cancelled"
    assert status.json()["cancel_requested"] is True
    assert openwebui_client.final_deltas == []
    assert openwebui_client.state_transitions == []
    assert [event["event_type"] for event in openwebui_client.events] == ["run.running"]


@pytest.mark.asyncio
async def test_run_start_retries_queued_model_call_before_finalization_failure() -> None:
    class QueuedOnceModelClient(RecordingOpenWebUIClient):
        async def call_model(self, **kwargs: object) -> dict:
            await super().call_model(**kwargs)  # type: ignore[arg-type]
            if len(self.model_calls) == 1:
                raise RuntimeError(
                    "OpenWebUI callback failed with status 403: "
                    '{"detail":{"code":"model_run_rejected","message":'
                    '"Agent run run-queued-race cannot execute model calls while queued"}}'
                )
            return {
                "status": "success",
                "model": kwargs["model"],
                "response": {"content": "retried final answer"},
            }

    openwebui_client = QueuedOnceModelClient()
    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-queued-race",
                "chat_id": "chat-1",
                "user_message_id": "msg-user",
                "assistant_message_id": "msg-assistant",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

        assert response.status_code == 202
        for _ in range(20):
            status = await client.get(
                "/v1/openwebui/runs/run-queued-race/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] == "completed":
                break
            await asyncio.sleep(0.01)

    assert [call["model_call_id"] for call in openwebui_client.model_calls] == [
        "model-call-1",
        "model-call-1",
    ]
    assert openwebui_client.final_deltas[0]["delta"] == "retried final answer"
    assert [event["event_type"] for event in openwebui_client.events] == [
        "run.running",
        "final.started",
        "run.completed",
    ]
    assert not any(event["event_type"] == "run.failed" for event in openwebui_client.events)


@pytest.mark.asyncio
async def test_provider_auth_error_text_from_model_call_fails_run_without_final_answer() -> None:
    class ProviderAuthErrorAsContentClient(RecordingOpenWebUIClient):
        async def call_model(self, **kwargs: object) -> dict:
            await super().call_model(**kwargs)  # type: ignore[arg-type]
            return {
                "status": "success",
                "model": kwargs["model"],
                "response": {
                    "content": (
                        "Error HTTP 503 auth_unavailable: no auth available for "
                        "model claude-3-5-haiku-latest\nTraceback: provider stack noise"
                    )
                },
            }

    openwebui_client = ProviderAuthErrorAsContentClient()
    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-provider-auth",
                "chat_id": "chat-1",
                "leader_model_id": "claude-3-5-haiku-latest",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

        assert response.status_code == 202
        for _ in range(20):
            status = await client.get(
                "/v1/openwebui/runs/run-provider-auth/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] == "failed":
                break
            await asyncio.sleep(0.01)

    assert status.json()["state"] == "failed"
    assert openwebui_client.final_deltas == []
    assert [transition["to_state"] for transition in openwebui_client.state_transitions] == ["failed"]

    failed_event = next(event for event in openwebui_client.events if event["event_type"] == "run.failed")
    error = failed_event["payload"]["error"]
    assert error["code"] == "provider_configuration_unavailable"
    assert error["summary"] == "The selected model provider is not available for this Agent Mode run."
    assert "auth_unavailable" in error["message"]
    assert "Traceback: provider stack noise" in error["message"]
    assert "Traceback: provider stack noise" not in failed_event["summary"]


@pytest.mark.asyncio
async def test_unknown_provider_callback_failure_has_user_summary_and_diagnostics() -> None:
    class UnknownProviderModelClient(RecordingOpenWebUIClient):
        async def call_model(self, **kwargs: object) -> dict:
            await super().call_model(**kwargs)  # type: ignore[arg-type]
            raise RuntimeError(
                "OpenWebUI callback failed with status 502: "
                '{"detail":{"code":"model_authority_error","message":'
                '"HTTP 502 unknown provider for model gpt-5-codex-mini"}}'
            )

    openwebui_client = UnknownProviderModelClient()
    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        response = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-unknown-provider",
                "chat_id": "chat-1",
                "leader_model_id": "gpt-5-codex-mini",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

        assert response.status_code == 202
        for _ in range(20):
            status = await client.get(
                "/v1/openwebui/runs/run-unknown-provider/status",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
            if status.json()["state"] == "failed":
                break
            await asyncio.sleep(0.01)

    assert status.json()["state"] == "failed"
    assert openwebui_client.final_deltas == []

    failed_event = next(event for event in openwebui_client.events if event["event_type"] == "run.failed")
    error = failed_event["payload"]["error"]
    assert error["code"] == "provider_configuration_unavailable"
    assert error["summary"] == "The selected model provider is not available for this Agent Mode run."
    assert "unknown provider for model gpt-5-codex-mini" in error["message"]


@pytest.mark.asyncio
async def test_run_start_finalization_failure_keeps_diagnostic_message_and_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class EmptyMessageModelFailureClient(RecordingOpenWebUIClient):
        async def call_model(self, **kwargs: object) -> dict:
            await super().call_model(**kwargs)  # type: ignore[arg-type]
            raise Exception()

    openwebui_client = EmptyMessageModelFailureClient()
    with caplog.at_level(logging.ERROR, logger="agentscope_runtime.app"):
        async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
            response = await client.post(
                "/v1/openwebui/runs",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
                json={
                    "run_id": "run-final-empty-error",
                    "chat_id": "chat-1",
                    "leader_model_id": "model-a",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

            assert response.status_code == 202
            for _ in range(20):
                status = await client.get(
                    "/v1/openwebui/runs/run-final-empty-error/status",
                    headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
                )
                if status.json()["state"] == "failed":
                    break
                await asyncio.sleep(0.01)

    failed_event = next(event for event in openwebui_client.events if event["event_type"] == "run.failed")
    message = failed_event["payload"]["error"]["message"]
    assert message
    assert "runtime finalization failed during model-call" in message
    assert "Exception" in message
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.asyncio
async def test_duplicate_run_start_reuses_existing_session_without_reemitting_running_or_rescheduling() -> None:
    openwebui_client = BlockingModelOpenWebUIClient()
    async with make_client(openwebui_client, auto_finalize_ordinary_qa=True) as client:
        first = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-duplicate",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert first.status_code == 202
        first_body = first.json()
        await asyncio.wait_for(openwebui_client.model_started.wait(), timeout=1)

        try:
            second = await client.post(
                "/v1/openwebui/runs",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
                json={
                    "run_id": "run-duplicate",
                    "chat_id": "chat-1",
                    "leader_model_id": "model-a",
                    "messages": [{"role": "user", "content": "hello again"}],
                },
            )
            second_body = second.json()
            await asyncio.sleep(0.05)

            assert second.status_code == 202
            assert second_body["accepted"] is True
            assert second_body["runtime_session_id"] == first_body["runtime_session_id"]
            assert [event["event_type"] for event in openwebui_client.events] == ["run.running"]
            assert len(openwebui_client.model_calls) == 1
        finally:
            openwebui_client.release_model.set()
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_run_start_retry_after_callback_failure_creates_fresh_session() -> None:
    openwebui_client = FailFirstRunningEventOpenWebUIClient()
    async with make_client(openwebui_client) as client:
        first = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-start-retry",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert first.status_code == 502

        retry = await client.post(
            "/v1/openwebui/runs",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "run_id": "run-start-retry",
                "chat_id": "chat-1",
                "leader_model_id": "model-a",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        retry_body = retry.json()

        status = await client.get(
            "/v1/openwebui/runs/run-start-retry/status",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )

    assert retry.status_code == 202
    assert retry_body["accepted"] is True
    assert status.json()["state"] == "running"
    assert openwebui_client.running_event_attempts == 2
    assert [event["event_type"] for event in openwebui_client.events] == ["run.running"]


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
