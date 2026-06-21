from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from agentscope.app import SubAgentTemplate
from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultState
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


OPENWEBUI_SUBAGENT_SYSTEM_PROMPT = """You are {member_name}, an OpenWebUI-governed \
subagent in team '{team_name}' led by {leader_name}.

Team purpose: {team_description}

Your role: {member_description}

Use only OpenWebUI-governed model and tool callbacks supplied by the runtime. \
Do not expect direct provider credentials, user JWTs, terminal keys, MCP \
secrets, or raw tool server credentials inside this AgentScope runtime."""


MODEL_CALL_RETRY_ATTEMPTS = 3
MODEL_CALL_RETRY_DELAY_SECONDS = 0.05


class OpenWebUIToolApprovalRequired(BaseException):
    """Control-flow signal: OpenWebUI paused the run for tool approval."""

    def __init__(
        self,
        *,
        response: dict[str, Any],
        tool_call_id: str,
        tool_id: str,
        tool_name: str,
    ) -> None:
        super().__init__("OpenWebUI tool approval required")
        self.response = response
        self.tool_call_id = tool_call_id
        self.tool_id = tool_id
        self.tool_name = tool_name


class OpenWebUIBridgeCallbacks(Protocol):
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

    async def append_text_delta(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        block_id: str,
        delta_index: int,
        delta: str,
        participant_id: str | None = None,
        phase: str | None = None,
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
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    async def call_tool(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        tool_call_id: str,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class AgentScopeAPISurfaces:
    subagent_template_cls: type[SubAgentTemplate]
    chat_model_base_cls: type[ChatModelBase]
    chat_response_cls: type[ChatResponse]
    tool_base_cls: type[ToolBase]
    tool_chunk_cls: type[ToolChunk]


def verify_agentscope_runtime_apis() -> AgentScopeAPISurfaces:
    required_template_fields = {"type", "description", "system_prompt_template"}
    missing_fields = required_template_fields - set(SubAgentTemplate.model_fields)
    if missing_fields:
        raise RuntimeError(
            "AgentScope SubAgentTemplate API drifted; missing fields: "
            + ", ".join(sorted(missing_fields))
        )

    if not hasattr(ChatModelBase, "_call_api"):
        raise RuntimeError("AgentScope ChatModelBase API drifted; _call_api missing")
    if not hasattr(ToolBase, "check_permissions"):
        raise RuntimeError("AgentScope ToolBase API drifted; check_permissions missing")

    return AgentScopeAPISurfaces(
        subagent_template_cls=SubAgentTemplate,
        chat_model_base_cls=ChatModelBase,
        chat_response_cls=ChatResponse,
        tool_base_cls=ToolBase,
        tool_chunk_cls=ToolChunk,
    )


class OpenWebUICallbackCredential(CredentialBase):
    name: str = "OpenWebUI callback authority"

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        return OpenWebUIAgentScopeModel


class OpenWebUIAgentScopeModel(ChatModelBase):
    class Parameters(BaseModel):
        pass

    def __init__(
        self,
        *,
        run_id: str,
        runtime_session_id: str,
        participant_id: str,
        model_id: str,
        callback_client: OpenWebUIBridgeCallbacks,
    ) -> None:
        super().__init__(
            credential=OpenWebUICallbackCredential(),
            model=model_id,
            parameters=self.Parameters(),
            stream=True,
            max_retries=0,
        )
        self.run_id = run_id
        self.runtime_session_id = runtime_session_id
        self.participant_id = participant_id
        self.callback_client = callback_client
        self._next_model_call_index = 1
        self._formatter = OpenAIChatFormatter()

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg] | list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Delegate to ``_stream_model_call`` for streaming.

        This method is ``async def`` (not an async generator function — no
        ``yield`` in body). It returns an async generator object created by
        ``_stream_model_call``. This matches the agentscope ``ChatModelBase``
        pattern where ``__call__`` does ``return await self._call_api(...)``
        and ``_reasoning_impl`` checks ``inspect.isasyncgen(res)`` to iterate
        the returned generator.
        """
        return self._stream_model_call(
            model_name=model_name,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )

    async def _stream_model_call(
        self,
        *,
        model_name: str,
        messages: list[Msg] | list[dict[str, Any]],
        tools: list[dict] | None,
        tool_choice: Any | None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Stream a model call, pushing text.delta callbacks per chunk.

        Yields intermediate ChatResponse chunks (is_last=False) with
        partial TextBlock content for agentscope's _convert_chat_response_to_event
        to consume, then a final ChatResponse (is_last=True) with the
        complete content blocks (TextBlock + ToolCallBlocks).

        Side effect: pushes text.delta events to OpenWebUI for each text
        chunk received, using a per-model-call block_id (uuid4).
        """
        model_call_id = self._allocate_model_call_id()
        params = dict(kwargs)
        idempotency_key = f"model:{self.participant_id}:{model_call_id}:1"

        formatted_messages = await self._format_messages(messages)

        block_id = uuid.uuid4().hex
        text_delta_index = 0
        accumulated_text_parts: list[str] = []
        accumulated_tool_calls: list[dict[str, Any]] = []

        for attempt in range(1, MODEL_CALL_RETRY_ATTEMPTS + 1):
            stream = self.callback_client.call_model_stream(
                run_id=self.run_id,
                idempotency_key=idempotency_key,
                participant_id=self.participant_id,
                model_call_id=model_call_id,
                model=model_name,
                messages=formatted_messages,
                params=params,
                tools=tools,
                tool_choice=_jsonable(tool_choice) if tool_choice is not None else None,
                metadata={
                    "runtime_session_id": self.runtime_session_id,
                    "agentscope_bridge": True,
                },
            )
            try:
                # Touch the first event to surface any callback-rejection
                # errors (e.g. ``model_run_rejected … while queued``,
                # httpx timeouts) that the underlying callback raises
                # before yielding any data. Successful streams just go
                # straight into the regular consumer below.
                first_event = await stream.__anext__()
                break
            except StopAsyncIteration:
                first_event = None
                break
            except Exception as exc:
                # Close the partially-started generator before retrying
                # so we don't leak a context manager.
                try:
                    await stream.aclose()
                except Exception:
                    pass
                if attempt < MODEL_CALL_RETRY_ATTEMPTS and _is_retryable_model_call_callback(exc):
                    await asyncio.sleep(MODEL_CALL_RETRY_DELAY_SECONDS)
                    continue
                raise

        events_to_consume: AsyncGenerator[dict[str, Any], None] = _prepend_event(first_event, stream)

        async for event in events_to_consume:
            event_type = event.get("type")
            if event_type == "chunk":
                delta = event.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    accumulated_text_parts.append(content)
                    try:
                        await self.callback_client.append_text_delta(
                            run_id=self.run_id,
                            idempotency_key=(
                                f"text:{self.run_id}:{self.participant_id}:"
                                f"{block_id}:{text_delta_index}"
                            ),
                            block_id=block_id,
                            delta_index=text_delta_index,
                            delta=content,
                            participant_id=self.participant_id,
                            phase="running",
                        )
                    except Exception:
                        # text.delta failure must not break the model call;
                        # the final ChatResponse still carries the full text.
                        pass
                    text_delta_index += 1
                    yield ChatResponse(
                        content=[TextBlock(text=content)],
                        is_last=False,
                        metadata={
                            "block_id": block_id,
                            "delta_index": text_delta_index - 1,
                        },
                    )
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if isinstance(tool_call, dict):
                            accumulated_tool_calls.append(tool_call)
            elif event_type == "done":
                # Non-stream fallback: full response in payload.
                payload = event.get("payload") or {}
                response = payload.get("response") or payload
                full_text = _extract_model_text(response)
                if full_text and not accumulated_text_parts:
                    accumulated_text_parts.append(full_text)
                    try:
                        await self.callback_client.append_text_delta(
                            run_id=self.run_id,
                            idempotency_key=(
                                f"text:{self.run_id}:{self.participant_id}:"
                                f"{block_id}:0"
                            ),
                            block_id=block_id,
                            delta_index=0,
                            delta=full_text,
                            participant_id=self.participant_id,
                            phase="running",
                        )
                    except Exception:
                        pass
                for tool_call in _extract_tool_calls(response):
                    accumulated_tool_calls.append(tool_call)
            elif event_type == "stream_end":
                break

        full_text = "".join(accumulated_text_parts)
        blocks: list[TextBlock | ToolCallBlock] = []
        if full_text:
            blocks.append(TextBlock(text=full_text))
        for tool_call in _merge_tool_calls(accumulated_tool_calls):
            function = tool_call.get("function") if isinstance(tool_call, dict) else None
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            raw_arguments = function.get("arguments", "{}")
            if not isinstance(raw_arguments, str):
                raw_arguments = json.dumps(raw_arguments)
            call_id = tool_call.get("id")
            blocks.append(
                ToolCallBlock(
                    id=str(call_id or f"tool-call-{len(blocks) + 1}"),
                    name=name,
                    input=raw_arguments,
                )
            )
        if not blocks:
            blocks.append(TextBlock(text=""))

        yield ChatResponse(
            content=blocks,
            is_last=True,
            metadata={
                "openwebui_response": {},
                "participant_id": self.participant_id,
                "model_call_id": model_call_id,
                "block_id": block_id,
            },
        )

    def _allocate_model_call_id(self) -> str:
        model_call_id = f"model-call-{self._next_model_call_index}"
        self._next_model_call_index += 1
        return model_call_id

    async def _format_messages(
        self,
        messages: list[Msg] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if all(isinstance(message, dict) for message in messages):
            return [dict(message) for message in messages]  # type: ignore[arg-type]
        return await self._formatter.format(messages)  # type: ignore[arg-type]


class OpenWebUIToolProxy(ToolBase):
    is_concurrency_safe = False
    is_read_only = False

    def __init__(
        self,
        *,
        run_id: str,
        runtime_session_id: str,
        participant_id: str,
        tool_id: str,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        callback_client: OpenWebUIBridgeCallbacks,
    ) -> None:
        self.run_id = run_id
        self.runtime_session_id = runtime_session_id
        self.participant_id = participant_id
        self.tool_id = tool_id
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.callback_client = callback_client
        self._next_tool_call_index = 1

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="OpenWebUI tool authority will enforce permissions.",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        tool_call_id = self._allocate_tool_call_id()
        await self.callback_client.append_event(
            run_id=self.run_id,
            idempotency_key=f"evt:{self.runtime_session_id}:{self.participant_id}:{tool_call_id}:requested",
            event_type="tool.requested",
            summary=self.description or f"{self.name} requested.",
            payload={
                "tool_id": self.tool_id,
                "tool_call_id": tool_call_id,
                "tool_name": self.name,
                "arguments": kwargs,
            },
            participant_id=self.participant_id,
            phase="running",
        )
        try:
            response = await self.callback_client.call_tool(
                run_id=self.run_id,
                idempotency_key=f"tool:{self.participant_id}:{tool_call_id}:1",
                participant_id=self.participant_id,
                tool_call_id=tool_call_id,
                tool_id=self.tool_id,
                arguments=kwargs,
            )
        except Exception as exc:
            await self.callback_client.append_event(
                run_id=self.run_id,
                idempotency_key=f"evt:{self.runtime_session_id}:{self.participant_id}:{tool_call_id}:failed",
                event_type="tool.failed",
                summary=f"{_humanize_tool_name(self.name)} failed.",
                payload={
                    "tool_id": self.tool_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": self.name,
                    "error": {"message": str(exc), "type": exc.__class__.__name__},
                },
                participant_id=self.participant_id,
                phase="running",
            )
            raise
        if _tool_requires_approval(response):
            raise OpenWebUIToolApprovalRequired(
                response=response,
                tool_call_id=tool_call_id,
                tool_id=self.tool_id,
                tool_name=self.name,
            )
        state = _tool_result_state(response)
        event_type = "tool.completed" if state == ToolResultState.SUCCESS else "tool.failed"
        await self.callback_client.append_event(
            run_id=self.run_id,
            idempotency_key=f"evt:{self.runtime_session_id}:{self.participant_id}:{tool_call_id}:completed",
            event_type=event_type,
            summary=(
                f"{_humanize_tool_name(self.name)} completed."
                if state == ToolResultState.SUCCESS
                else f"{_humanize_tool_name(self.name)} failed."
            ),
            payload={
                "tool_id": self.tool_id,
                "tool_call_id": tool_call_id,
                "tool_name": self.name,
                "status": response.get("status"),
            },
            participant_id=self.participant_id,
            phase="running",
        )
        artifacts = _extract_artifacts(response)
        for index, artifact in enumerate(artifacts):
            await self.callback_client.append_event(
                run_id=self.run_id,
                idempotency_key=f"evt:{self.runtime_session_id}:{self.participant_id}:{tool_call_id}:artifact:{index}",
                event_type="artifact.registered",
                summary=f"Artifact {artifact.get('name') or artifact.get('id') or index + 1} is ready.",
                payload={
                    "tool_id": self.tool_id,
                    "tool_call_id": tool_call_id,
                    "artifact": artifact,
                },
                participant_id=self.participant_id,
                phase="running",
            )
        return ToolChunk(
            content=[TextBlock(text=str(response.get("content") or ""))],
            state=state,
            metadata={
                "openwebui_response": response,
                "participant_id": self.participant_id,
                "tool_call_id": tool_call_id,
                "tool_id": self.tool_id,
                "artifacts": _extract_artifacts(response),
            },
        )

    def _allocate_tool_call_id(self) -> str:
        tool_call_id = f"tool-call-{self._next_tool_call_index}"
        self._next_tool_call_index += 1
        return tool_call_id


class AgentScopeRuntimeBridge:
    def __init__(
        self,
        *,
        run_id: str,
        runtime_session_id: str,
        callback_client: OpenWebUIBridgeCallbacks,
    ) -> None:
        verify_agentscope_runtime_apis()
        self.run_id = run_id
        self.runtime_session_id = runtime_session_id
        self.callback_client = callback_client

    def build_subagent_template(
        self,
        *,
        template_type: str,
        description: str,
        system_prompt_template: str = OPENWEBUI_SUBAGENT_SYSTEM_PROMPT,
    ) -> SubAgentTemplate:
        return SubAgentTemplate(
            type=template_type,
            description=description,
            system_prompt_template=system_prompt_template,
        )

    def build_model(
        self,
        *,
        participant_id: str,
        model_id: str,
    ) -> OpenWebUIAgentScopeModel:
        return OpenWebUIAgentScopeModel(
            run_id=self.run_id,
            runtime_session_id=self.runtime_session_id,
            participant_id=participant_id,
            model_id=model_id,
            callback_client=self.callback_client,
        )

    def build_tool_proxy(
        self,
        *,
        participant_id: str,
        tool_id: str,
        name: str,
        description: str,
        input_schema: dict[str, Any],
    ) -> OpenWebUIToolProxy:
        return OpenWebUIToolProxy(
            run_id=self.run_id,
            runtime_session_id=self.runtime_session_id,
            participant_id=participant_id,
            tool_id=tool_id,
            name=name,
            description=description,
            input_schema=input_schema,
            callback_client=self.callback_client,
        )


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
                if isinstance(message, dict):
                    message_content = message.get("content")
                    if isinstance(message_content, str):
                        return message_content
                delta = choice.get("delta")
                if isinstance(delta, dict):
                    delta_content = delta.get("content")
                    if isinstance(delta_content, str):
                        return delta_content
    content = response.get("content")
    return content if isinstance(content, str) else str(payload)


def _extract_chat_response_blocks(response: dict[str, Any]) -> list[TextBlock | ToolCallBlock]:
    blocks: list[TextBlock | ToolCallBlock] = []
    text = _extract_model_text(response)
    if text:
        blocks.append(TextBlock(text=text))

    for tool_call in _extract_tool_calls(response):
        function = tool_call.get("function") if isinstance(tool_call, dict) else None
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw_arguments = function.get("arguments", "{}")
        if not isinstance(raw_arguments, str):
            raw_arguments = json.dumps(raw_arguments)
        call_id = tool_call.get("id")
        blocks.append(
            ToolCallBlock(
                id=str(call_id or f"tool-call-{len(blocks) + 1}"),
                name=name,
                input=raw_arguments,
            )
        )
    if not blocks:
        blocks.append(TextBlock(text=""))
    return blocks


def _extract_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    payload = response.get("response", response)
    candidates: list[Any] = []
    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict):
                    candidates.append(message.get("tool_calls"))
                delta = choice.get("delta")
                if isinstance(delta, dict):
                    candidates.append(delta.get("tool_calls"))
        candidates.append(payload.get("tool_calls"))
    candidates.append(response.get("tool_calls"))

    tool_calls: list[dict[str, Any]] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            tool_calls.extend([item for item in candidate if isinstance(item, dict)])
    return tool_calls


def _merge_tool_calls(deltas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge OpenAI-style streaming tool_call deltas by index.

    Each delta may carry `id`, `function.name` (first delta only), and
    `function.arguments` (incremental string). We accumulate by index.
    Deltas without an index are treated as standalone tool_calls.
    """
    by_index: dict[int, dict[str, Any]] = {}
    standalone: list[dict[str, Any]] = []
    for delta in deltas:
        if not isinstance(delta, dict):
            continue
        if "index" in delta and isinstance(delta["index"], int):
            index = delta["index"]
            current = by_index.setdefault(index, {"index": index})
            if "id" in delta and delta["id"]:
                current["id"] = delta["id"]
            function = current.setdefault("function", {})
            delta_function = delta.get("function")
            if isinstance(delta_function, dict):
                if "name" in delta_function and delta_function["name"]:
                    function["name"] = delta_function["name"]
                if "arguments" in delta_function and isinstance(delta_function["arguments"], str):
                    function["arguments"] = function.get("arguments", "") + delta_function["arguments"]
        else:
            standalone.append(delta)
    merged = list(by_index.values()) + standalone
    return merged


def _content_item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        text = item.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _extract_artifacts(response: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = response.get("artifacts")
    if isinstance(artifacts, list):
        return [artifact for artifact in artifacts if isinstance(artifact, dict)]
    payload = response.get("response")
    if isinstance(payload, dict) and isinstance(payload.get("artifacts"), list):
        return [artifact for artifact in payload["artifacts"] if isinstance(artifact, dict)]
    return []


def _is_retryable_model_call_callback(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    message = str(exc)
    return "model_run_rejected" in message and "while queued" in message


async def _prepend_event(
    first_event: dict[str, Any] | None,
    stream: AsyncGenerator[dict[str, Any], None],
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield ``first_event`` (if any) then the rest of ``stream``.

    Used to re-attach the head event we already pulled from the SSE
    iterator while probing for retryable callback errors.
    """
    if first_event is not None:
        yield first_event
    async for event in stream:
        yield event


def _humanize_tool_name(name: str) -> str:
    return name.replace("_", " ").strip().capitalize() or "Tool"


def _tool_result_state(response: dict[str, Any]) -> ToolResultState:
    status = response.get("status")
    if status == "success":
        return ToolResultState.SUCCESS
    if status == "cancelled":
        return ToolResultState.INTERRUPTED
    if status == "approval_rejected":
        return ToolResultState.DENIED
    return ToolResultState.ERROR


def _tool_requires_approval(response: dict[str, Any]) -> bool:
    return response.get("status") == "approval_required"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
