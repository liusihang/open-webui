import inspect

import pytest


class RecordingBridgeCallbacks:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.model_calls: list[dict] = []
        self.tool_calls: list[dict] = []

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
        return {"seq": len(self.events)}

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
        self.model_calls.append(
            {
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
        )
        return {
            "status": "success",
            "model": model,
            "response": {"content": "callback answer"},
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
        self.tool_calls.append(
            {
                "run_id": run_id,
                "idempotency_key": idempotency_key,
                "participant_id": participant_id,
                "tool_call_id": tool_call_id,
                "tool_id": tool_id,
                "arguments": arguments,
            }
        )
        return {"status": "success", "content": "tool callback answer"}


def test_verified_agentscope_api_surfaces_are_importable_and_stable() -> None:
    from agentscope.app import SubAgentTemplate
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.tool import ToolBase, ToolChunk

    from agentscope_runtime.agentscope_bridge import verify_agentscope_runtime_apis

    surfaces = verify_agentscope_runtime_apis()

    assert surfaces.subagent_template_cls is SubAgentTemplate
    assert surfaces.chat_model_base_cls is ChatModelBase
    assert surfaces.chat_response_cls is ChatResponse
    assert surfaces.tool_base_cls is ToolBase
    assert surfaces.tool_chunk_cls is ToolChunk
    assert "model_name" in inspect.signature(ChatModelBase._call_api).parameters
    assert {"type", "description", "system_prompt_template"} <= set(
        SubAgentTemplate.model_fields
    )


@pytest.mark.asyncio
async def test_bridge_builds_agentscope_template_model_and_tool_callback_boundaries() -> None:
    from agentscope.app import SubAgentTemplate
    from agentscope.message import Msg, TextBlock, ToolResultState
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.permission import PermissionBehavior, PermissionContext
    from agentscope.tool import ToolBase, ToolChunk

    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge

    callbacks = RecordingBridgeCallbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-bridge",
        runtime_session_id="rt-run-bridge",
        callback_client=callbacks,
    )

    template = bridge.build_subagent_template(
        template_type="openwebui-worker",
        description="OpenWebUI-governed worker.",
    )
    assert isinstance(template, SubAgentTemplate)
    assert template.type == "openwebui-worker"
    assert "{member_name}" in template.system_prompt_template

    model = bridge.build_model(
        participant_id="subagent:run-bridge:1",
        model_id="model-research",
    )
    assert isinstance(model, ChatModelBase)

    response = await model(
        [
            Msg(
                name="user",
                content=[TextBlock(text="Summarize this.")],
                role="user",
            )
        ],
        temperature=0.2,
    )
    assert isinstance(response, ChatResponse)
    assert response.content[0].text == "callback answer"
    assert callbacks.model_calls[0]["idempotency_key"] == (
        "model:subagent:run-bridge:1:model-call-1:1"
    )
    assert callbacks.model_calls[0]["model"] == "model-research"
    assert callbacks.model_calls[0]["messages"][0]["role"] == "user"
    assert callbacks.model_calls[0]["params"] == {"temperature": 0.2}

    tool = bridge.build_tool_proxy(
        participant_id="subagent:run-bridge:1",
        tool_id="tool-search",
        name="search",
        description="Search through OpenWebUI tool authority.",
        input_schema={"type": "object"},
    )
    assert isinstance(tool, ToolBase)

    decision = await tool.check_permissions({"query": "agent mode"}, PermissionContext())
    assert decision.behavior is PermissionBehavior.ALLOW
    assert "OpenWebUI" in decision.message

    tool_chunk = await tool(query="agent mode")
    assert isinstance(tool_chunk, ToolChunk)
    assert tool_chunk.state == ToolResultState.SUCCESS
    assert tool_chunk.content[0].text == "tool callback answer"
    assert callbacks.tool_calls[0]["idempotency_key"] == (
        "tool:subagent:run-bridge:1:tool-call-1:1"
    )
    assert callbacks.tool_calls[0]["arguments"] == {"query": "agent mode"}


@pytest.mark.asyncio
async def test_model_bridge_preserves_openwebui_tool_calls_as_agentscope_blocks() -> None:
    from agentscope.message import ToolCallBlock

    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class ToolCallingCallbacks(RecordingBridgeCallbacks):
        async def call_model(self, **kwargs: object) -> dict:
            self.model_calls.append(kwargs)
            return {
                "status": "success",
                "response": {
                    "choices": [
                        {
                            "message": {
                                "content": "I will search first.",
                                "tool_calls": [
                                    {
                                        "id": "call_search_1",
                                        "type": "function",
                                        "function": {
                                            "name": "search_web",
                                            "arguments": "{\"query\":\"agent mode\"}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            }

    callbacks = ToolCallingCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-tool-bridge",
        runtime_session_id="rt-run-tool-bridge",
        participant_id="leader",
        model_id="model-a",
        callback_client=callbacks,
    )

    response = await model(
        [{"role": "user", "content": "Search for agent mode."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search the web.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    tool_calls = [block for block in response.content if isinstance(block, ToolCallBlock)]
    assert callbacks.model_calls[0]["messages"] == [
        {"role": "user", "content": "Search for agent mode."}
    ]
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_search_1"
    assert tool_calls[0].name == "search_web"
    assert tool_calls[0].input == "{\"query\":\"agent mode\"}"


@pytest.mark.asyncio
async def test_model_bridge_passes_tools_and_tool_choice_as_top_level_callback_fields() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-tool-bridge",
        runtime_session_id="rt-run-tool-bridge",
        participant_id="leader",
        model_id="model-a",
        callback_client=callbacks,
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

    await model(
        [{"role": "user", "content": "Read the file."}],
        tools=tools,
        tool_choice="auto",
        temperature=0.2,
    )

    assert callbacks.model_calls[0]["tools"] == tools
    assert callbacks.model_calls[0]["tool_choice"] == "auto"
    assert callbacks.model_calls[0]["params"] == {"temperature": 0.2}
