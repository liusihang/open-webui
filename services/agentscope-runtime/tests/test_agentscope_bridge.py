import asyncio
import inspect

import pytest


class RecordingBridgeCallbacks:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.model_calls: list[dict] = []
        self.tool_calls: list[dict] = []
        self.text_deltas: list[dict] = []

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
        payload: dict | None = None,
    ) -> dict:
        record = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "block_id": block_id,
            "block_kind": block_kind,
            "delta_index": delta_index,
            "delta": delta,
            "participant_id": participant_id,
            "phase": phase,
            "payload": payload,
        }
        self.text_deltas.append(record)
        return {"seq": len(self.events) + len(self.text_deltas)}

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

    async def call_model_stream(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        model_call_id: str,
        model: str,
        messages: list[dict],
        params: dict,
        metadata: dict,
        tools: list[dict] | None = None,
        tool_choice: object | None = None,
    ):
        self.model_calls.append(
            {
                "run_id": run_id,
                "idempotency_key": idempotency_key,
                "participant_id": participant_id,
                "model_call_id": model_call_id,
                "model": model,
                "messages": messages,
                "stream": True,
                "params": params,
                "metadata": metadata,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        # Default mock: emit a single text chunk then stream_end.
        yield {
            "type": "chunk",
            "delta": {
                "content": "callback answer",
                "phase": "final_answer",
                "tool_calls": None,
            },
        }
        yield {"type": "stream_end"}

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
    assert {"type", "description", "system_prompt_template"} <= set(SubAgentTemplate.model_fields)


def test_assistant_context_trim_drops_orphaned_tool_output(monkeypatch) -> None:
    import agentscope_runtime.agentscope_bridge as bridge

    monkeypatch.setattr(bridge, "PUBLIC_ASSISTANT_CONTEXT_REPLAY_MAX_CHARS", 80)

    trimmed = bridge._trim_assistant_context_messages(
        [
            {
                "type": "function_call",
                "call_id": "call-large",
                "name": "run_command",
                "arguments": "x" * 500,
            },
            {
                "type": "function_call_output",
                "call_id": "call-large",
                "output": '{"status":"success"}',
            },
        ]
    )

    assert trimmed == []


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

    response = None
    async for chunk in await model(
        [
            Msg(
                name="user",
                content=[TextBlock(text="Summarize this.")],
                role="user",
            )
        ],
        temperature=0.2,
    ):
        response = chunk
    assert isinstance(response, ChatResponse)
    assert response.content[0].text == "callback answer"
    assert response.is_last is True
    assert callbacks.model_calls[0]["idempotency_key"] == ("model:subagent:run-bridge:1:model-call-1:1")
    assert callbacks.model_calls[0]["model"] == "model-research"
    assert callbacks.model_calls[0]["messages"][0]["role"] == "user"
    assert callbacks.model_calls[0]["params"] == {"temperature": 0.2}
    assert callbacks.text_deltas == []

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
    assert callbacks.text_deltas == []
    assert [event["event_type"] for event in callbacks.events] == ["tool.requested"]
    assert callbacks.tool_calls[0]["idempotency_key"] == ("tool:subagent:run-bridge:1:tool-call-1:1")
    assert callbacks.tool_calls[0]["arguments"] == {"query": "agent mode"}

    async for _ in await model(
        [
            Msg(
                name="user",
                content=[TextBlock(text="Continue after the tool.")],
                role="user",
            )
        ],
    ):
        pass
    second_call_messages = callbacks.model_calls[-1]["messages"]
    assert second_call_messages[0]["role"] == "user"
    assert second_call_messages[1:] == []
    replay_text = "\n".join(str(message.get("content") or "") for message in second_call_messages)
    assert "Previous Agent Mode public process" not in replay_text
    assert "phase:running" not in replay_text
    assert "agent mode" not in replay_text


@pytest.mark.asyncio
async def test_tool_requested_event_uses_short_user_facing_summary() -> None:
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge

    callbacks = RecordingBridgeCallbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-tool-summary",
        runtime_session_id="rt-run-tool-summary",
        callback_client=callbacks,
    )
    tool = bridge.build_tool_proxy(
        participant_id="leader",
        tool_id="tool:terminal:terminals:write_file",
        name="write_file",
        description=(
            "Write complete text content to a file.\n\n"
            "Use when: creating a new text file or intentionally replacing the whole file."
        ),
        input_schema={"type": "object", "properties": {}},
    )

    await tool(path="/tmp/example.txt", content="example")

    requested = callbacks.events[0]
    assert requested["event_type"] == "tool.requested"
    assert requested["summary"] == "Write file requested."


@pytest.mark.asyncio
async def test_current_run_tool_results_do_not_inject_synthetic_assistant_notes() -> None:
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge

    callbacks = RecordingBridgeCallbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-order",
        runtime_session_id="rt-run-order",
        callback_client=callbacks,
    )
    model = bridge.build_model(
        participant_id="leader",
        model_id="model-order",
    )

    environment_tool = bridge.build_tool_proxy(
        participant_id="leader",
        tool_id="tool-env",
        name="get_environment",
        description="Get environment.",
        input_schema={"type": "object"},
    )
    timestamp_tool = bridge.build_tool_proxy(
        participant_id="leader",
        tool_id="tool-time",
        name="get_current_timestamp",
        description="Get current timestamp.",
        input_schema={"type": "object"},
    )
    await environment_tool()
    await timestamp_tool()

    async for _ in await model(
        [
            {"role": "user", "content": "测试下多步工具调用"},
            {
                "type": "function_call",
                "call_id": "call_env",
                "name": "get_environment",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "call_id": "call_time",
                "name": "get_current_timestamp",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_env",
                "output": '{"status":"success"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_time",
                "output": '{"status":"success"}',
            },
        ],
    ):
        pass

    messages = callbacks.model_calls[-1]["messages"]
    user_index = next(index for index, message in enumerate(messages) if message.get("role") == "user")
    function_indices = [
        index for index, message in enumerate(messages) if str(message.get("type") or "").startswith("function_call")
    ]

    assert user_index == 0
    assert function_indices == [1, 2, 3, 4]
    assert messages[5:] == []
    assert callbacks.text_deltas == []
    replay_text = "\n".join(str(message.get("content") or "") for message in messages)
    assert "I will use" not in replay_text
    assert "completed." not in replay_text


@pytest.mark.asyncio
async def test_model_bridge_forwards_default_model_params_to_callback() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-reasoning",
        runtime_session_id="rt-run-reasoning",
        participant_id="leader",
        model_id="model-a",
        callback_client=callbacks,
        default_model_params={
            "reasoning": {
                "enabled": True,
                "effort": "high",
                "max_tokens": 8126,
            }
        },
    )

    async for _ in await model([{"role": "user", "content": "think carefully"}]):
        pass

    assert callbacks.model_calls[0]["params"] == {
        "reasoning": {
            "enabled": True,
            "effort": "high",
            "max_tokens": 8126,
        }
    }


@pytest.mark.asyncio
async def test_model_bridge_merges_call_kwargs_over_default_model_params() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-reasoning-merge",
        runtime_session_id="rt-run-reasoning-merge",
        participant_id="leader",
        model_id="model-a",
        callback_client=callbacks,
        default_model_params={
            "reasoning": {
                "enabled": True,
                "effort": "high",
                "max_tokens": 8126,
            },
            "temperature": 0.1,
        },
    )

    async for _ in await model(
        [{"role": "user", "content": "think carefully"}],
        reasoning={"effort": "xhigh"},
        temperature=0.2,
    ):
        pass

    assert callbacks.model_calls[0]["params"] == {
        "reasoning": {
            "enabled": True,
            "effort": "xhigh",
            "max_tokens": 8126,
        },
        "temperature": 0.2,
    }


@pytest.mark.asyncio
async def test_model_bridge_preserves_openwebui_tool_calls_as_agentscope_blocks() -> None:
    from agentscope.message import ToolCallBlock

    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class ToolCallingCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "chunk",
                "delta": {
                    "content": "I will search first.",
                    "phase": "commentary",
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
                },
            }
            yield {"type": "stream_end"}

    callbacks = ToolCallingCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-tool-bridge",
        runtime_session_id="rt-run-tool-bridge",
        participant_id="leader",
        model_id="model-a",
        callback_client=callbacks,
    )

    response = None
    async for chunk in await model(
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
    ):
        response = chunk

    assert response is not None
    tool_calls = [block for block in response.content if isinstance(block, ToolCallBlock)]
    assert callbacks.model_calls[0]["messages"] == [{"role": "user", "content": "Search for agent mode."}]
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_search_1"
    assert tool_calls[0].name == "search_web"
    assert tool_calls[0].input == "{\"query\":\"agent mode\"}"
    assert [item["delta"] for item in callbacks.text_deltas] == ["I will search first."]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_arguments", ["", " \t\r\n"])
@pytest.mark.parametrize("indexed", [False, True])
async def test_model_bridge_normalizes_blank_tool_arguments_to_empty_object(
    raw_arguments: str,
    indexed: bool,
) -> None:
    from agentscope.message import ToolCallBlock

    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def blank_arguments_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        **({"index": 0} if indexed else {}),
                        "id": "call_environment",
                        "type": "function",
                        "function": {
                            "name": "get_environment",
                            "arguments": raw_arguments,
                        },
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = blank_arguments_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-blank-tool-arguments",
        runtime_session_id="rt-blank-tool-arguments",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    response = None
    async for chunk in await model(
        [{"role": "user", "content": "Inspect."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_environment",
                    "description": "Get environment.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    ):
        response = chunk

    assert response is not None
    tool_calls = [block for block in response.content if isinstance(block, ToolCallBlock)]
    assert [block.input for block in tool_calls] == ["{}"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_arguments", "expected_arguments"),
    [
        ({"query": "agent mode"}, '{"query": "agent mode"}'),
        (["agent mode"], '["agent mode"]'),
        (None, "null"),
    ],
)
async def test_model_bridge_json_serializes_non_string_tool_arguments(
    raw_arguments: object,
    expected_arguments: str,
) -> None:
    from agentscope.message import ToolCallBlock

    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def non_string_arguments_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_environment",
                        "type": "function",
                        "function": {
                            "name": "get_environment",
                            "arguments": raw_arguments,
                        },
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = non_string_arguments_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-non-string-tool-arguments",
        runtime_session_id="rt-non-string-tool-arguments",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    response = None
    async for chunk in await model(
        [{"role": "user", "content": "Inspect."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_environment",
                    "description": "Get environment.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    ):
        response = chunk

    assert response is not None
    tool_calls = [block for block in response.content if isinstance(block, ToolCallBlock)]
    assert [block.input for block in tool_calls] == [expected_arguments]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_arguments", "expected_arguments"),
    [
        ({"query": "agent mode"}, '{"query": "agent mode"}'),
        (["agent mode"], '["agent mode"]'),
        (None, "null"),
    ],
)
async def test_model_bridge_json_serializes_indexed_non_string_tool_arguments(
    raw_arguments: object,
    expected_arguments: str,
) -> None:
    from agentscope.message import ToolCallBlock

    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def indexed_non_string_arguments_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_environment",
                        "type": "function",
                        "function": {
                            "name": "get_environment",
                            "arguments": raw_arguments,
                        },
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = indexed_non_string_arguments_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-indexed-non-string-tool-arguments",
        runtime_session_id="rt-indexed-non-string-tool-arguments",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    response = None
    async for chunk in await model(
        [{"role": "user", "content": "Inspect."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_environment",
                    "description": "Get environment.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    ):
        response = chunk

    assert response is not None
    tool_calls = [block for block in response.content if isinstance(block, ToolCallBlock)]
    assert [block.input for block in tool_calls] == [expected_arguments]


@pytest.mark.asyncio
async def test_model_bridge_concatenates_indexed_string_argument_fragments() -> None:
    from agentscope.message import ToolCallBlock

    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def indexed_string_arguments_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_search",
                        "type": "function",
                        "function": {
                            "name": "search_web",
                            "arguments": '{"query":',
                        },
                    }
                ],
            },
        }
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"arguments": '"agent mode"}'},
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = indexed_string_arguments_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-indexed-string-tool-arguments",
        runtime_session_id="rt-indexed-string-tool-arguments",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    response = None
    async for chunk in await model(
        [{"role": "user", "content": "Search."}],
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
    ):
        response = chunk

    assert response is not None
    tool_calls = [block for block in response.content if isinstance(block, ToolCallBlock)]
    assert [block.input for block in tool_calls] == ['{"query":"agent mode"}']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_arguments", "second_arguments"),
    [
        ({"scope": "runtime"}, "{}"),
        ("{}", {"scope": "runtime"}),
        ("[]", ["runtime"]),
        ("null", None),
        ({"scope": "runtime"}, {"scope": "runtime"}),
        (["runtime"], ["runtime"]),
        (None, None),
    ],
)
async def test_model_bridge_rejects_unmergeable_indexed_argument_types(
    first_arguments: object,
    second_arguments: object,
) -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def mixed_indexed_arguments_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_environment",
                        "type": "function",
                        "function": {
                            "name": "get_environment",
                            "arguments": first_arguments,
                        },
                    }
                ],
            },
        }
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"arguments": second_arguments},
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = mixed_indexed_arguments_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-mixed-indexed-tool-arguments",
        runtime_session_id="rt-mixed-indexed-tool-arguments",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    with pytest.raises(RuntimeError, match="cannot merge indexed tool arguments"):
        async for _ in await model(
            [{"role": "user", "content": "Inspect."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_environment",
                        "description": "Get environment.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        ):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("indexed", [False, True])
async def test_model_bridge_preserves_malformed_nonblank_tool_arguments_for_rejection(
    indexed: bool,
) -> None:
    from agentscope._utils._common import _json_loads_with_repair
    from agentscope.exception import ToolJSONDecodeError
    from agentscope.message import ToolCallBlock

    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()
    malformed_arguments = "{bad"

    async def malformed_arguments_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        **({"index": 0} if indexed else {}),
                        "id": "call_environment",
                        "type": "function",
                        "function": {
                            "name": "get_environment",
                            "arguments": malformed_arguments,
                        },
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = malformed_arguments_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-malformed-tool-arguments",
        runtime_session_id="rt-malformed-tool-arguments",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    response = None
    async for chunk in await model(
        [{"role": "user", "content": "Inspect."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_environment",
                    "description": "Get environment.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    ):
        response = chunk

    assert response is not None
    tool_calls = [block for block in response.content if isinstance(block, ToolCallBlock)]
    assert [block.input for block in tool_calls] == [malformed_arguments]
    with pytest.raises(ToolJSONDecodeError, match="JSONDecodeError"):
        _json_loads_with_repair(tool_calls[0].input)


@pytest.mark.asyncio
async def test_model_bridge_buffers_model_commentary_before_tool_response() -> None:
    from agentscope.message import TextBlock, ToolCallBlock

    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class CommentaryToolCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "chunk",
                "delta": {
                    "content": "I will inspect ",
                    "phase": "commentary",
                    "tool_calls": None,
                },
            }
            yield {
                "type": "chunk",
                "delta": {
                    "content": "the environment.",
                    "phase": "commentary",
                    "tool_calls": None,
                },
            }
            yield {
                "type": "chunk",
                "delta": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_env",
                            "type": "function",
                            "function": {
                                "name": "get_environment",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
            }
            yield {"type": "stream_end"}

    callbacks = CommentaryToolCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-commentary-tool",
        runtime_session_id="rt-commentary-tool",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model(
            [{"role": "user", "content": "Inspect the environment."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_environment",
                        "description": "Get environment.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    ]

    assert len(chunks) == 1
    assert chunks[0].is_last is True
    assert [block.text for block in chunks[0].content if isinstance(block, TextBlock)] == [
        "I will inspect the environment."
    ]
    assert [block.name for block in chunks[0].content if isinstance(block, ToolCallBlock)] == [
        "get_environment"
    ]
    assert [(item["block_kind"], item["delta"], item["phase"]) for item in callbacks.text_deltas] == [
        ("assistant_note", "I will inspect the environment.", "running")
    ]
    assert callbacks.text_deltas[0]["payload"] == {
        "source": "model",
        "model_call_id": "model-call-1",
        "response_phase": "commentary",
    }


@pytest.mark.asyncio
async def test_model_bridge_commentary_block_ids_are_unique_across_runtime_identities() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class CommentaryToolCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            participant_id = str(kwargs["participant_id"])
            yield {
                "type": "chunk",
                "delta": {
                    "content": f"{participant_id} will inspect.",
                    "phase": "commentary",
                    "tool_calls": None,
                },
            }
            yield {
                "type": "chunk",
                "delta": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call-{participant_id}",
                            "type": "function",
                            "function": {"name": "get_environment", "arguments": "{}"},
                        }
                    ],
                },
            }
            yield {"type": "stream_end"}

    callbacks = CommentaryToolCallbacks()
    identities = (
        ("rt-commentary-participants", "leader"),
        ("rt-commentary-participants", "subagent:run-1:1"),
        ("rt-commentary-participants-restart", "leader"),
    )
    for runtime_session_id, participant_id in identities:
        model = OpenWebUIAgentScopeModel(
            run_id="run-commentary-participants",
            runtime_session_id=runtime_session_id,
            participant_id=participant_id,
            model_id="gpt-5.4",
            callback_client=callbacks,
        )
        async for _ in await model(
            [{"role": "user", "content": "Inspect."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_environment",
                        "description": "Get environment.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        ):
            pass

    assert len({item["block_id"] for item in callbacks.text_deltas}) == len(identities)


@pytest.mark.asyncio
async def test_model_bridge_flushes_commentary_before_streaming_final_answer() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class CommentaryFinalCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "chunk",
                "delta": {
                    "content": "I checked the result.",
                    "phase": "commentary",
                    "tool_calls": None,
                },
            }
            yield {
                "type": "chunk",
                "delta": {
                    "content": "Final ",
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
            yield {
                "type": "chunk",
                "delta": {
                    "content": "answer.",
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
            yield {"type": "stream_end"}

    final_texts = []
    callbacks = CommentaryFinalCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-commentary-final",
        runtime_session_id="rt-commentary-final",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
        on_final_text=lambda participant_id, text: final_texts.append((participant_id, text)),
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Finish."}])
    ]

    assert [chunk.content[0].text for chunk in chunks[:-1]] == ["Final ", "answer."]
    assert all(chunk.is_last is False for chunk in chunks[:-1])
    assert chunks[-1].is_last is True
    assert callbacks.text_deltas[0]["delta"] == "I checked the result."
    assert final_texts == [("leader", "Final answer.")]


@pytest.mark.asyncio
async def test_model_bridge_persists_provider_auxiliary_content_before_final_stream() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class AuxiliaryFinalCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "chunk",
                "delta": {
                    "content": "Web search results.",
                    "content_kind": "provider_auxiliary",
                    "auxiliary_type": "web_search_result",
                    "tool_calls": None,
                },
            }
            yield {
                "type": "chunk",
                "delta": {
                    "content": "Final answer.",
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
            yield {"type": "stream_end"}

    callbacks = AuxiliaryFinalCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-auxiliary-final",
        runtime_session_id="rt-auxiliary-final",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Search, then answer."}])
    ]

    assert [chunk.content[0].text for chunk in chunks[:-1]] == ["Final answer."]
    assert [item["delta"] for item in callbacks.text_deltas] == ["Web search results."]
    assert callbacks.text_deltas[0]["block_kind"] == "action_summary"
    assert callbacks.text_deltas[0]["payload"] == {
        "source": "provider_auxiliary",
        "model_call_id": "model-call-1",
        "auxiliary_types": ["web_search_result"],
    }


@pytest.mark.asyncio
async def test_model_bridge_classifies_unphased_no_tool_text_as_final_answer() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def phase_less_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {"content": "Untyped answer.", "tool_calls": None},
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = phase_less_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-missing-phase",
        runtime_session_id="rt-missing-phase",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Answer."}])
    ]

    assert [chunk.content[0].text for chunk in chunks[:-1]] == ["Untyped answer."]
    assert chunks[-1].content[0].text == "Untyped answer."
    assert chunks[-1].is_last is True


@pytest.mark.asyncio
async def test_model_bridge_strips_split_in_band_commentary_marker() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def phase_marker_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {"content": "phase=comm", "tool_calls": None},
        }
        yield {
            "type": "chunk",
            "delta": {"content": "entary ", "tool_calls": None},
        }
        yield {
            "type": "chunk",
            "delta": {"content": "Checking the environment.", "tool_calls": None},
        }
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_environment",
                        "type": "function",
                        "function": {
                            "name": "get_environment",
                            "arguments": "{}",
                        },
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = phase_marker_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-in-band-commentary-marker",
        runtime_session_id="rt-in-band-commentary-marker",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model(
            [{"role": "user", "content": "Inspect."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_environment",
                        "description": "Get environment.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    ]

    assert [item["delta"] for item in callbacks.text_deltas] == [
        "Checking the environment."
    ]
    assert chunks[-1].content[0].text == "Checking the environment."


@pytest.mark.asyncio
async def test_model_bridge_strips_split_in_band_final_marker_and_preserves_deltas() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def phase_marker_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        for content in (" phase=final_", "answer\n", "Final ", "answer."):
            yield {
                "type": "chunk",
                "delta": {"content": content, "tool_calls": None},
            }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = phase_marker_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-in-band-final-marker",
        runtime_session_id="rt-in-band-final-marker",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Answer."}])
    ]

    assert [chunk.content[0].text for chunk in chunks[:-1]] == ["Final ", "answer."]
    assert chunks[-1].content[0].text == "Final answer."


@pytest.mark.asyncio
async def test_model_bridge_strips_verified_wrapped_commentary_marker() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def wrapped_commentary_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        for content in ("**进度说明 (phase=comm", "entary)：**", "Checking."):
            yield {
                "type": "chunk",
                "delta": {"content": content, "tool_calls": None},
            }
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_environment",
                        "type": "function",
                        "function": {
                            "name": "get_environment",
                            "arguments": "{}",
                        },
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = wrapped_commentary_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-wrapped-commentary-marker",
        runtime_session_id="rt-wrapped-commentary-marker",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model(
            [{"role": "user", "content": "Inspect."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_environment",
                        "description": "Get environment.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    ]

    assert [item["delta"] for item in callbacks.text_deltas] == ["Checking."]
    assert chunks[-1].content[0].text == "Checking."


@pytest.mark.asyncio
async def test_model_bridge_strips_verified_wrapped_final_marker_and_preserves_deltas() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def wrapped_final_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        for content in ("**总结 (phase=final_", "answer)：**", "Final ", "answer."):
            yield {
                "type": "chunk",
                "delta": {"content": content, "tool_calls": None},
            }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = wrapped_final_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-wrapped-final-marker",
        runtime_session_id="rt-wrapped-final-marker",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Answer."}])
    ]

    assert [chunk.content[0].text for chunk in chunks[:-1]] == ["Final ", "answer."]
    assert chunks[-1].content[0].text == "Final answer."


@pytest.mark.asyncio
async def test_model_bridge_strips_split_leading_thinking_envelope_from_final() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def thinking_final_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        for content in (
            "<thi",
            "nking>private reasoning",
            "</thinking>\n",
            "Final ",
            "answer.",
        ):
            yield {
                "type": "chunk",
                "delta": {
                    "content": content,
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = thinking_final_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-leading-thinking-final",
        runtime_session_id="rt-leading-thinking-final",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Answer."}])
    ]

    assert [chunk.content[0].text for chunk in chunks[:-1]] == ["Final ", "answer."]
    assert chunks[-1].content[0].text == "Final answer."


@pytest.mark.asyncio
async def test_model_bridge_replays_stripped_leading_thinking_only_as_private_reasoning() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def thinking_replay_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        if len(callbacks.model_calls) == 1:
            content_parts = ("<thinking>private", " reasoning</thinking>", "Answer one.")
        else:
            content_parts = ("Answer two.",)
        for content in content_parts:
            yield {
                "type": "chunk",
                "delta": {
                    "content": content,
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = thinking_replay_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-leading-thinking-replay",
        runtime_session_id="rt-leading-thinking-replay",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    first_chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "First."}])
    ]
    async for _ in await model([{"role": "user", "content": "Second."}]):
        pass

    assert first_chunks[-1].content[0].text == "Answer one."
    assert callbacks.model_calls[1]["messages"] == [
        {"role": "user", "content": "Second."},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "private reasoning",
        },
    ]


@pytest.mark.asyncio
async def test_model_bridge_preserves_answer_indentation_after_leading_thinking() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def indented_answer_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        for content in ("<thinking>private</thinking>\n", "    code block"):
            yield {
                "type": "chunk",
                "delta": {
                    "content": content,
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = indented_answer_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-leading-thinking-indentation",
        runtime_session_id="rt-leading-thinking-indentation",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Answer."}])
    ]

    assert [chunk.content[0].text for chunk in chunks[:-1]] == ["    code block"]
    assert chunks[-1].content[0].text == "    code block"


@pytest.mark.asyncio
async def test_model_bridge_preserves_nonleading_literal_thinking_text() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def nonleading_thinking_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": "Keep <thinking>literal</thinking> visible.",
                "phase": "final_answer",
                "tool_calls": None,
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = nonleading_thinking_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-nonleading-thinking",
        runtime_session_id="rt-nonleading-thinking",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Answer."}])
    ]

    expected = "Keep <thinking>literal</thinking> visible."
    assert [chunk.content[0].text for chunk in chunks[:-1]] == [expected]
    assert chunks[-1].content[0].text == expected


@pytest.mark.asyncio
async def test_model_bridge_rejects_unclosed_leading_thinking_envelope_without_public_output() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def unclosed_thinking_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        for content in ("<thinking>private", " reasoning without a close"):
            yield {
                "type": "chunk",
                "delta": {
                    "content": content,
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = unclosed_thinking_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-unclosed-leading-thinking",
        runtime_session_id="rt-unclosed-leading-thinking",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    emitted = []
    with pytest.raises(RuntimeError, match="invalid_model_reasoning_envelope"):
        async for chunk in await model([{"role": "user", "content": "Answer."}]):
            emitted.append(chunk)

    assert emitted == []


@pytest.mark.asyncio
async def test_model_bridge_keeps_nonleading_wrapped_phase_text() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def nonleading_marker_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": "Keep **总结 (phase=final_answer)：** visible.",
                "phase": "final_answer",
                "tool_calls": None,
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = nonleading_marker_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-nonleading-wrapped-marker",
        runtime_session_id="rt-nonleading-wrapped-marker",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Answer."}])
    ]

    expected = "Keep **总结 (phase=final_answer)：** visible."
    assert [chunk.content[0].text for chunk in chunks[:-1]] == [expected]
    assert chunks[-1].content[0].text == expected


@pytest.mark.asyncio
async def test_model_bridge_rejects_wrapped_marker_conflicting_with_explicit_phase() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def conflicting_marker_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": "**进度说明 (phase=commentary)：**Late note.",
                "phase": "final_answer",
                "tool_calls": None,
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = conflicting_marker_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-conflicting-wrapped-marker",
        runtime_session_id="rt-conflicting-wrapped-marker",
        participant_id="leader",
        model_id="claude-sonnet-4-5",
        callback_client=callbacks,
    )

    yielded = []
    with pytest.raises(RuntimeError, match="invalid_model_phase_marker"):
        async for chunk in await model([{"role": "user", "content": "Answer."}]):
            yielded.append(chunk)

    assert yielded == []


@pytest.mark.asyncio
async def test_model_bridge_honors_in_band_final_marker_before_tool_validation() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def final_marker_with_tool_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {"content": "phase=final_answer Premature final.", "tool_calls": None},
        }
        yield {
            "type": "chunk",
            "delta": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_late",
                        "type": "function",
                        "function": {
                            "name": "get_environment",
                            "arguments": "{}",
                        },
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = final_marker_with_tool_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-in-band-final-marker-with-tool",
        runtime_session_id="rt-in-band-final-marker-with-tool",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    yielded = []
    with pytest.raises(RuntimeError, match="final_phase_with_tool_call"):
        async for chunk in await model([{"role": "user", "content": "Answer."}]):
            yielded.append(chunk)

    assert yielded == []
    assert callbacks.text_deltas == []


@pytest.mark.asyncio
async def test_model_bridge_honors_in_band_commentary_before_explicit_final() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def commentary_marker_then_final_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": "phase=commentary Checking the result.",
                "tool_calls": None,
            },
        }
        yield {
            "type": "chunk",
            "delta": {
                "content": "Final answer.",
                "phase": "final_answer",
                "tool_calls": None,
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = commentary_marker_then_final_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-in-band-commentary-before-final",
        runtime_session_id="rt-in-band-commentary-before-final",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Answer."}])
    ]

    assert [item["delta"] for item in callbacks.text_deltas] == [
        "Checking the result."
    ]
    assert [chunk.content[0].text for chunk in chunks[:-1]] == ["Final answer."]
    assert chunks[-1].content[0].text == "Checking the result.Final answer."


@pytest.mark.asyncio
async def test_model_bridge_rejects_unknown_explicit_phase() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def invalid_phase_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": "Invalid phase text.",
                "phase": "bogus",
                "tool_calls": None,
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = invalid_phase_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-invalid-phase",
        runtime_session_id="rt-invalid-phase",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    with pytest.raises(RuntimeError, match="invalid_model_phase"):
        async for _ in await model([{"role": "user", "content": "Answer."}]):
            pass


@pytest.mark.asyncio
async def test_model_bridge_preserves_order_when_phase_is_declared_midstream() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def mixed_phase_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {"content": "First ", "tool_calls": None},
        }
        yield {
            "type": "chunk",
            "delta": {
                "content": "second",
                "phase": "final_answer",
                "tool_calls": None,
            },
        }
        yield {
            "type": "chunk",
            "delta": {"content": " third.", "tool_calls": None},
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = mixed_phase_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-mixed-phase",
        runtime_session_id="rt-mixed-phase",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    chunks = [
        chunk
        async for chunk in await model([{"role": "user", "content": "Answer."}])
    ]

    assert [chunk.content[0].text for chunk in chunks[:-1]] == [
        "First ",
        "second",
        " third.",
    ]
    assert chunks[-1].content[0].text == "First second third."


@pytest.mark.asyncio
async def test_model_bridge_does_not_emit_final_before_later_tool_conflict() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def final_then_tool_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": "Premature final.",
                "phase": "final_answer",
                "tool_calls": None,
            },
        }
        yield {
            "type": "chunk",
            "delta": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_late",
                        "type": "function",
                        "function": {
                            "name": "get_environment",
                            "arguments": "{}",
                        },
                    }
                ],
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = final_then_tool_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-final-then-tool",
        runtime_session_id="rt-final-then-tool",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )
    yielded = []

    with pytest.raises(RuntimeError, match="final_phase_with_tool_call"):
        async for chunk in await model([{"role": "user", "content": "Answer."}]):
            yielded.append(chunk)

    assert yielded == []


@pytest.mark.asyncio
async def test_model_bridge_rejects_commentary_after_final_without_emitting_final() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    callbacks = RecordingBridgeCallbacks()

    async def final_then_commentary_stream(**kwargs: object):
        callbacks.model_calls.append(kwargs)
        yield {
            "type": "chunk",
            "delta": {
                "content": "Final first.",
                "phase": "final_answer",
                "tool_calls": None,
            },
        }
        yield {
            "type": "chunk",
            "delta": {
                "content": "Late commentary.",
                "phase": "commentary",
                "tool_calls": None,
            },
        }
        yield {"type": "stream_end"}

    callbacks.call_model_stream = final_then_commentary_stream
    model = OpenWebUIAgentScopeModel(
        run_id="run-final-then-commentary",
        runtime_session_id="rt-final-then-commentary",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )
    yielded = []

    with pytest.raises(RuntimeError, match="invalid_model_phase_transition"):
        async for chunk in await model([{"role": "user", "content": "Answer."}]):
            yielded.append(chunk)

    assert yielded == []


@pytest.mark.asyncio
async def test_model_bridge_rejects_final_phase_with_tool_call() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class FinalToolCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "chunk",
                "delta": {
                    "content": "This is final.",
                    "phase": "final_answer",
                    "tool_calls": [
                        {
                            "id": "call_bad",
                            "type": "function",
                            "function": {"name": "get_environment", "arguments": "{}"},
                        }
                    ],
                },
            }
            yield {"type": "stream_end"}

    callbacks = FinalToolCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-final-tool",
        runtime_session_id="rt-final-tool",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    with pytest.raises(RuntimeError, match="final_phase_with_tool_call"):
        async for _ in await model([{"role": "user", "content": "Finish."}]):
            pass


@pytest.mark.asyncio
async def test_model_bridge_rejects_malformed_tool_call_delta() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class MalformedToolCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "chunk",
                "delta": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-malformed",
                            "type": "function",
                            "function": {"arguments": "{}"},
                        }
                    ],
                },
            }
            yield {"type": "stream_end"}

    callbacks = MalformedToolCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-malformed-tool",
        runtime_session_id="rt-malformed-tool",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    with pytest.raises(RuntimeError, match="invalid_tool_call"):
        async for _ in await model(
            [{"role": "user", "content": "Use the tool."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_environment",
                        "description": "Get environment.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        ):
            pass


@pytest.mark.asyncio
async def test_model_bridge_rejects_commentary_only_without_tool_or_final() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class CommentaryOnlyCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "chunk",
                "delta": {
                    "content": "I checked the available context.",
                    "phase": "commentary",
                    "tool_calls": None,
                },
            }
            yield {"type": "stream_end"}

    callbacks = CommentaryOnlyCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-commentary-only",
        runtime_session_id="rt-commentary-only",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    with pytest.raises(RuntimeError, match="model_final_phase_missing"):
        async for _ in await model([{"role": "user", "content": "Answer."}]):
            pass

    assert [item["delta"] for item in callbacks.text_deltas] == [
        "I checked the available context."
    ]


@pytest.mark.asyncio
async def test_model_bridge_replays_private_reasoning_content_to_next_model_call() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class ReasoningCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            if len(self.model_calls) == 1:
                yield {
                    "type": "chunk",
                    "delta": {
                        "reasoning_content": "先确认构建产物，再继续。",
                        "content": None,
                        "tool_calls": None,
                    },
                }
                yield {
                    "type": "chunk",
                    "delta": {
                        "content": "公开回答。",
                        "phase": "final_answer",
                        "tool_calls": None,
                    },
                }
            else:
                yield {
                    "type": "chunk",
                    "delta": {
                        "content": "继续回答。",
                        "phase": "final_answer",
                        "tool_calls": None,
                    },
                }
            yield {"type": "stream_end"}

    callbacks = ReasoningCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-private-reasoning",
        runtime_session_id="rt-run-private-reasoning",
        participant_id="leader",
        model_id="qwen3-reasoner",
        callback_client=callbacks,
    )

    async for _ in await model([{"role": "user", "content": "第一步"}]):
        pass
    async for _ in await model([{"role": "user", "content": "第二步"}]):
        pass

    assert callbacks.model_calls[0]["messages"] == [{"role": "user", "content": "第一步"}]
    assert callbacks.model_calls[1]["messages"] == [
        {"role": "user", "content": "第二步"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "先确认构建产物，再继续。",
        },
    ]
    assert callbacks.text_deltas == []


@pytest.mark.asyncio
async def test_model_bridge_does_not_raw_replay_private_reasoning_for_gpt_models() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class ReasoningCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "chunk",
                "delta": {
                    "reasoning_content": "private gpt reasoning",
                    "content": "answer",
                    "phase": "final_answer",
                    "tool_calls": None,
                },
            }
            yield {"type": "stream_end"}

    callbacks = ReasoningCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-gpt-private-reasoning",
        runtime_session_id="rt-run-gpt-private-reasoning",
        participant_id="leader",
        model_id="gpt-5.4",
        callback_client=callbacks,
    )

    async for _ in await model([{"role": "user", "content": "第一步"}]):
        pass
    async for _ in await model([{"role": "user", "content": "第二步"}]):
        pass

    assert callbacks.model_calls[1]["messages"] == [{"role": "user", "content": "第二步"}]
    assert callbacks.text_deltas == []


@pytest.mark.asyncio
async def test_model_bridge_does_not_turn_reasoning_only_done_payload_into_public_text() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class ReasoningOnlyCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "done",
                "payload": {
                    "response": {
                        "reasoning_content": "private reasoning only",
                    }
                },
            }
            yield {"type": "stream_end"}

    callbacks = ReasoningOnlyCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-reasoning-only",
        runtime_session_id="rt-run-reasoning-only",
        participant_id="leader",
        model_id="qwen3-reasoner",
        callback_client=callbacks,
    )

    with pytest.raises(RuntimeError, match="empty_model_response"):
        async for _ in await model([{"role": "user", "content": "第一步"}]):
            pass
    assert callbacks.text_deltas == []


@pytest.mark.asyncio
async def test_model_bridge_raises_structured_model_stream_error() -> None:
    from agentscope_runtime.agentscope_bridge import OpenWebUIAgentScopeModel

    class FailingStreamCallbacks(RecordingBridgeCallbacks):
        async def call_model_stream(self, **kwargs: object):
            self.model_calls.append(kwargs)
            yield {
                "type": "error",
                "error": {
                    "message": "provider failed",
                    "code": "provider_error",
                },
            }

    callbacks = FailingStreamCallbacks()
    model = OpenWebUIAgentScopeModel(
        run_id="run-stream-error",
        runtime_session_id="rt-run-stream-error",
        participant_id="leader",
        model_id="model-a",
        callback_client=callbacks,
    )

    with pytest.raises(RuntimeError, match="model_stream_error: provider failed"):
        async for _ in await model([{"role": "user", "content": "开始"}]):
            pass


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

    async for _ in await model(
        [{"role": "user", "content": "Read the file."}],
        tools=tools,
        tool_choice="auto",
        temperature=0.2,
    ):
        pass

    assert callbacks.model_calls[0]["tools"] == tools
    assert callbacks.model_calls[0]["tool_choice"] == "auto"
    assert callbacks.model_calls[0]["params"] == {"temperature": 0.2}


@pytest.mark.asyncio
async def test_bridge_allocates_unique_tool_call_ids_across_different_tools() -> None:
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge

    callbacks = RecordingBridgeCallbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-parallel-tools",
        runtime_session_id="rt-run-parallel-tools",
        callback_client=callbacks,
    )
    health = bridge.build_tool_proxy(
        participant_id="leader",
        tool_id="tool:terminal:terminals:health_check",
        name="health_check",
        description="Returns service status.",
        input_schema={"type": "object", "properties": {}},
    )
    timestamp = bridge.build_tool_proxy(
        participant_id="leader",
        tool_id="tool:builtin:get_current_timestamp:get_current_timestamp",
        name="get_current_timestamp",
        description="Get current timestamp.",
        input_schema={"type": "object", "properties": {}},
    )

    await asyncio.gather(health(), timestamp())

    tool_call_ids = [call["tool_call_id"] for call in callbacks.tool_calls]
    idempotency_keys = [call["idempotency_key"] for call in callbacks.tool_calls]
    assert len(set(tool_call_ids)) == 2
    assert len(set(idempotency_keys)) == 2


@pytest.mark.asyncio
async def test_tool_proxy_failure_emits_structured_events_without_synthetic_text() -> None:
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge

    class FailingCallbacks(RecordingBridgeCallbacks):
        async def call_tool(self, **kwargs: object) -> dict:
            self.tool_calls.append(kwargs)
            raise RuntimeError("private backend path /secret/raw-output.txt")

    callbacks = FailingCallbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-tool-failure",
        runtime_session_id="rt-run-tool-failure",
        callback_client=callbacks,
    )
    tool = bridge.build_tool_proxy(
        participant_id="leader",
        tool_id="tool:builtin:write_file:write_file",
        name="write_file",
        description="Write a file.",
        input_schema={"type": "object", "properties": {}},
    )

    with pytest.raises(RuntimeError, match="private backend path"):
        await tool(path="/tmp/private.txt", content="secret value")

    assert callbacks.text_deltas == []
    assert [event["event_type"] for event in callbacks.events] == ["tool.requested"]


@pytest.mark.asyncio
async def test_tool_proxy_success_leaves_terminal_event_to_backend_owner() -> None:
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge

    callbacks = RecordingBridgeCallbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-tool-success",
        runtime_session_id="rt-run-tool-success",
        callback_client=callbacks,
    )
    tool = bridge.build_tool_proxy(
        participant_id="leader",
        tool_id="tool:builtin:read_file:read_file",
        name="read_file",
        description="Read a file.",
        input_schema={"type": "object", "properties": {}},
    )

    result = await tool(path="/tmp/input.txt")

    assert result.state.value == "success"
    assert [event["event_type"] for event in callbacks.events] == ["tool.requested"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "exception_name"),
    [
        ("approval_required", "OpenWebUIToolApprovalRequired"),
        ("approval_rejected", "OpenWebUIToolApprovalRejected"),
    ],
)
async def test_tool_proxy_approval_emits_no_synthetic_text(
    status: str,
    exception_name: str,
) -> None:
    import agentscope_runtime.agentscope_bridge as bridge_module

    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge

    class ApprovalCallbacks(RecordingBridgeCallbacks):
        async def call_tool(self, **kwargs: object) -> dict:
            self.tool_calls.append(kwargs)
            return {"status": status, "content": "approval state"}

    callbacks = ApprovalCallbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id=f"run-{status}",
        runtime_session_id=f"rt-{status}",
        callback_client=callbacks,
    )
    tool = bridge.build_tool_proxy(
        participant_id="leader",
        tool_id="tool:shell:run",
        name="run_command",
        description="Run a command.",
        input_schema={"type": "object", "properties": {}},
    )
    exception_type = getattr(bridge_module, exception_name)

    with pytest.raises(exception_type):
        await tool(command="pwd")

    assert callbacks.text_deltas == []
    assert [event["event_type"] for event in callbacks.events] == ["tool.requested"]


def test_durable_bridge_exposes_backend_tools_as_agentscope_external_executions() -> None:
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge

    bridge = AgentScopeRuntimeBridge(
        run_id="run-durable",
        runtime_session_id="rt-run-durable",
        callback_client=RecordingBridgeCallbacks(),
        durable_external_tools=True,
    )
    tool = bridge.build_tool_proxy(
        participant_id="leader",
        tool_id="tool:terminal:main:write_file",
        name="write_file",
        description="Write a file",
        input_schema={"type": "object"},
    )

    assert tool.is_external_tool is True


def test_bridge_checkpoint_state_restores_stable_tool_and_model_counters() -> None:
    from agentscope_runtime.agentscope_bridge import AgentScopeRuntimeBridge

    callbacks = RecordingBridgeCallbacks()
    bridge = AgentScopeRuntimeBridge(
        run_id="run-checkpoint",
        runtime_session_id="rt-run-checkpoint",
        callback_client=callbacks,
        checkpoint_state={
            "next_tool_call_index": 7,
            "model_call_indexes": {"leader": 5},
        },
    )
    model = bridge.build_model(participant_id="leader", model_id="model-a")

    assert bridge._allocate_tool_call_id() == "tool-call-7"
    assert model._allocate_model_call_id() == "model-call-5"
    assert bridge.snapshot_state() == {
        "next_tool_call_index": 8,
        "model_call_indexes": {"leader": 6},
    }
