from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agentscope.app import SubAgentTemplate
from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultState
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk
from pydantic import BaseModel

OPENWEBUI_SUBAGENT_SYSTEM_PROMPT = """You are {member_name}, an OpenWebUI-governed \
subagent in team '{team_name}' led by {leader_name}.

Team purpose: {team_description}

Your role: {member_description}

Use only OpenWebUI-governed model and tool callbacks supplied by the runtime. \
Do not expect direct provider credentials, user JWTs, terminal keys, MCP \
secrets, or raw tool server credentials inside this AgentScope runtime."""


MODEL_CALL_RETRY_ATTEMPTS = 3
MODEL_CALL_RETRY_DELAY_SECONDS = 0.05
PRIVATE_REASONING_REPLAY_MAX_CHARS = 12000
PUBLIC_ASSISTANT_CONTEXT_REPLAY_MAX_CHARS = 12000
PRIVATE_REASONING_REPLAY_BLOCKED_MODEL_MARKERS = (
    "gpt",
    "openai",
)
IN_BAND_RESPONSE_PHASE_RE = re.compile(
    r"^\s*phase\s*=\s*(commentary|final_answer)\b"
    r"(?:[ \t]*[:\uff1a-][ \t]*|[ \t]*(?:(?:\r\n|\n)){1,2}|[ \t]+)?",
    re.IGNORECASE,
)
BOLD_IN_BAND_RESPONSE_PHASE_RE = re.compile(
    r"^\s*\*\*[ \t]*phase\s*=\s*(commentary|final_answer)[ \t]*\*\*(?!\*)"
    r"(?:[ \t]*[:\uff1a-][ \t]*|[ \t]*(?:(?:\r\n|\n)){1,2}|[ \t]+|$)",
    re.IGNORECASE,
)
WRAPPED_IN_BAND_RESPONSE_PHASE_PATTERNS = (
    (
        "commentary",
        re.compile(
            r"^\s*\*\*进度说明[ \t]*\([ \t]*phase\s*=\s*commentary\b"
            r"[ \t]*\)[ \t]*：[ \t]*\*\*"
            r"(?:[ \t]*(?:(?:\r\n|\n)){1,2}|[ \t]+)?",
            re.IGNORECASE,
        ),
    ),
    (
        "final_answer",
        re.compile(
            r"^\s*\*\*总结[ \t]*\([ \t]*phase\s*=\s*final_answer\b"
            r"[ \t]*\)[ \t]*：[ \t]*\*\*"
            r"(?:[ \t]*(?:(?:\r\n|\n)){1,2}|[ \t]+)?",
            re.IGNORECASE,
        ),
    ),
)
LEADING_THINKING_OPEN_RE = re.compile(r"^\s*<thinking>", re.IGNORECASE)
LEADING_THINKING_ENVELOPE_RE = re.compile(
    r"^\s*<thinking>(.*?)</thinking>(?:\r?\n)?",
    re.IGNORECASE | re.DOTALL,
)


def _remove_leading_characters(parts: list[str], count: int) -> list[str]:
    cleaned: list[str] = []
    remaining_prefix = count
    for part in parts:
        if remaining_prefix >= len(part):
            remaining_prefix -= len(part)
            continue
        if remaining_prefix:
            part = part[remaining_prefix:]
            remaining_prefix = 0
        if part:
            cleaned.append(part)
    return cleaned


def _strip_in_band_response_phase(
    parts: list[str],
) -> tuple[str | None, list[str]]:
    """Remove one leading textual phase envelope without flattening deltas."""
    if not parts:
        return None, []
    combined = "".join(parts)
    match = IN_BAND_RESPONSE_PHASE_RE.match(combined)
    phase = match.group(1).lower() if match is not None else None
    if match is None:
        match = BOLD_IN_BAND_RESPONSE_PHASE_RE.match(combined)
        phase = match.group(1).lower() if match is not None else None
    if match is None:
        for wrapped_phase, pattern in WRAPPED_IN_BAND_RESPONSE_PHASE_PATTERNS:
            match = pattern.match(combined)
            if match is not None:
                phase = wrapped_phase
                break
    if match is None:
        return None, list(parts)

    return phase, _remove_leading_characters(parts, match.end())


def _strip_leading_thinking_envelope(parts: list[str]) -> tuple[str | None, list[str]]:
    """Remove one verified leading thinking envelope while preserving answer deltas."""
    if not parts:
        return None, []
    combined = "".join(parts)
    if LEADING_THINKING_OPEN_RE.match(combined) is None:
        return None, list(parts)
    match = LEADING_THINKING_ENVELOPE_RE.match(combined)
    if match is None:
        raise RuntimeError(
            "invalid_model_reasoning_envelope: leading thinking block is not closed"
        )
    return match.group(1), _remove_leading_characters(parts, match.end())


def _normalize_leading_response_envelopes(
    parts: list[str],
) -> tuple[str | None, str | None, list[str]]:
    """Decode one leading phase and one thinking envelope in either order."""
    marker_phase, cleaned_parts = _strip_in_band_response_phase(parts)
    private_reasoning, cleaned_parts = _strip_leading_thinking_envelope(
        cleaned_parts
    )
    if marker_phase is None:
        marker_phase, cleaned_parts = _strip_in_band_response_phase(cleaned_parts)
    duplicate_phase, _ = _strip_in_band_response_phase(cleaned_parts)
    if duplicate_phase is not None:
        raise RuntimeError(
            "invalid_model_phase_marker: duplicate leading textual phase marker"
        )
    duplicate_reasoning, _ = _strip_leading_thinking_envelope(cleaned_parts)
    if duplicate_reasoning is not None:
        raise RuntimeError(
            "invalid_model_reasoning_envelope: duplicate leading thinking envelope"
        )
    return marker_phase, private_reasoning, cleaned_parts


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


class OpenWebUIToolApprovalRejected(RuntimeError):
    """OpenWebUI rejected an approval-gated tool call."""

    def __init__(
        self,
        *,
        response: dict[str, Any],
        tool_call_id: str,
        tool_id: str,
        tool_name: str,
    ) -> None:
        super().__init__(str(response.get("content") or "Tool approval was rejected."))
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
    ) -> dict[str, Any]: ...

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
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

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
    ) -> dict[str, Any]: ...

    def call_model_stream(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        model_call_id: str,
        model: str,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        metadata: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]: ...

    async def call_tool(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        tool_call_id: str,
        tool_id: str,
        arguments: dict[str, Any],
        checkpoint_version: int | None = None,
        decision_execution_id: str | None = None,
    ) -> dict[str, Any]: ...


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
            "AgentScope SubAgentTemplate API drifted; missing fields: " + ", ".join(sorted(missing_fields))
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
        on_final_text: Callable[[str, str], None] | None = None,
        assistant_context: Callable[[], list[dict[str, Any]]] | None = None,
        default_model_params: dict[str, Any] | None = None,
        next_model_call_index: int = 1,
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
        self._on_final_text = on_final_text
        self._assistant_context = assistant_context or (lambda: [])
        self.default_model_params = dict(default_model_params or {})
        self._next_model_call_index = max(1, int(next_model_call_index))
        self._formatter = OpenAIChatFormatter()
        self._private_reasoning_parts: list[str] = []

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
        """Stream a model call while preserving AgentScope ChatResponse chunks.

        Yields intermediate ChatResponse chunks (is_last=False) with
        partial TextBlock content for agentscope's _convert_chat_response_to_event
        to consume, then a final ChatResponse (is_last=True) with the
        complete content blocks (TextBlock + ToolCallBlocks).
        """
        model_call_id = self._allocate_model_call_id()
        params = _merge_model_params(self.default_model_params, dict(kwargs))
        idempotency_key = f"model:{self.participant_id}:{model_call_id}:1"

        formatted_messages = _inject_assistant_context_replay(
            await self._format_messages(messages),
            assistant_messages=self._assistant_context(),
        )
        formatted_messages = _append_private_reasoning_replay(
            formatted_messages,
            model_name=model_name,
            reasoning_parts=self._private_reasoning_parts,
        )
        block_id = uuid.uuid4().hex
        final_text_delta_index = 0
        commentary_parts: list[str] = []
        auxiliary_parts: list[str] = []
        auxiliary_types: list[str] = []
        final_text_parts: list[str] = []
        unclassified_text_parts: list[str] = []
        active_text_phase: str | None = None
        final_phase_started = False
        accumulated_reasoning_parts: list[str] = []
        literal_reasoning_parts: list[str] = []
        accumulated_tool_calls: list[dict[str, Any]] = []
        commentary_flushed = False
        auxiliary_flushed = False
        commentary_block_id = (
            f"{self.runtime_session_id}:{self.participant_id}:"
            f"{model_call_id}:model-commentary"
        )
        auxiliary_block_id = (
            f"{self.runtime_session_id}:{self.participant_id}:"
            f"{model_call_id}:provider-auxiliary"
        )

        async def flush_commentary() -> None:
            nonlocal commentary_flushed
            if commentary_flushed or not commentary_parts:
                return
            marker_phase, private_reasoning, cleaned_parts = (
                _normalize_leading_response_envelopes(commentary_parts)
            )
            if marker_phase is not None and marker_phase != "commentary":
                raise RuntimeError(
                    "invalid_model_phase_marker: textual phase marker "
                    f"{marker_phase} conflicts with commentary"
                )
            if private_reasoning:
                literal_reasoning_parts.append(private_reasoning)
            commentary_parts[:] = cleaned_parts
            if not commentary_parts:
                commentary_flushed = True
                return
            commentary = "".join(commentary_parts)
            await self.callback_client.append_text_delta(
                run_id=self.run_id,
                idempotency_key=(
                    f"txt:{self.runtime_session_id}:{self.participant_id}:"
                    f"{model_call_id}:model-commentary:0"
                ),
                block_id=commentary_block_id,
                block_kind="assistant_note",
                delta_index=0,
                delta=commentary,
                participant_id=self.participant_id,
                phase="running",
                payload={
                    "source": "model",
                    "model_call_id": model_call_id,
                    "response_phase": "commentary",
                },
            )
            commentary_flushed = True

        async def flush_auxiliary() -> None:
            nonlocal auxiliary_flushed
            if auxiliary_flushed or not auxiliary_parts:
                return
            await self.callback_client.append_text_delta(
                run_id=self.run_id,
                idempotency_key=(
                    f"txt:{self.runtime_session_id}:{self.participant_id}:"
                    f"{model_call_id}:provider-auxiliary:0"
                ),
                block_id=auxiliary_block_id,
                block_kind="action_summary",
                delta_index=0,
                delta="".join(auxiliary_parts),
                participant_id=self.participant_id,
                phase="running",
                payload={
                    "source": "provider_auxiliary",
                    "model_call_id": model_call_id,
                    "auxiliary_types": list(dict.fromkeys(auxiliary_types)),
                },
            )
            auxiliary_flushed = True

        async def classify_text(
            phase: str,
            content: str,
        ) -> None:
            nonlocal final_phase_started
            if phase == "commentary":
                if final_phase_started:
                    raise RuntimeError(
                        "invalid_model_phase_transition: commentary cannot follow "
                        "final_answer"
                    )
                commentary_parts.append(content)
                return
            final_phase_started = True
            if accumulated_tool_calls:
                raise RuntimeError(
                    "final_phase_with_tool_call: final-answer text cannot "
                    "share a model response with tool calls"
                )
            await flush_commentary()
            await flush_auxiliary()
            final_text_parts.append(content)

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
            if event_type == "error":
                error = event.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or "").strip()
                else:
                    message = str(error or "").strip()
                raise RuntimeError(
                    f"model_stream_error: {message or 'unknown model stream error'}"
                )
            if event_type == "chunk":
                delta = event.get("delta") or {}
                reasoning_content = delta.get("reasoning_content")
                if isinstance(reasoning_content, str) and reasoning_content:
                    accumulated_reasoning_parts.append(reasoning_content)
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if isinstance(tool_call, dict):
                            accumulated_tool_calls.append(tool_call)
                content = delta.get("content")
                if isinstance(content, str) and content:
                    if delta.get("content_kind") == "provider_auxiliary":
                        auxiliary_parts.append(content)
                        auxiliary_type = str(delta.get("auxiliary_type") or "").strip()
                        if auxiliary_type:
                            auxiliary_types.append(auxiliary_type)
                    else:
                        raw_phase = delta.get("phase")
                        phase = (
                            str(raw_phase).strip()
                            if raw_phase is not None
                            else ""
                        )
                        if phase and phase not in {"commentary", "final_answer"}:
                            raise RuntimeError(
                                "invalid_model_phase: expected commentary or "
                                f"final_answer, got {phase}"
                            )
                        if phase:
                            if unclassified_text_parts:
                                marker_phase, private_reasoning, cleaned_parts = (
                                    _normalize_leading_response_envelopes(
                                        unclassified_text_parts
                                    )
                                )
                                if private_reasoning:
                                    literal_reasoning_parts.append(private_reasoning)
                                pending_phase = marker_phase or phase
                                for pending_text in cleaned_parts:
                                    await classify_text(
                                        pending_phase,
                                        pending_text,
                                    )
                                unclassified_text_parts.clear()
                            active_text_phase = phase
                        elif active_text_phase is not None:
                            phase = active_text_phase
                        else:
                            unclassified_text_parts.append(content)
                            continue

                        await classify_text(phase, content)
            elif event_type == "done":
                # Non-stream fallback: full response in payload.
                payload = event.get("payload") or {}
                response = payload.get("response") or payload
                full_text = _extract_model_text(response)
                if full_text and not (
                    commentary_parts or final_text_parts or unclassified_text_parts
                ):
                    unclassified_text_parts.append(full_text)
                full_reasoning = _extract_model_reasoning_text(response)
                if full_reasoning and not accumulated_reasoning_parts:
                    accumulated_reasoning_parts.append(full_reasoning)
                for tool_call in _extract_tool_calls(response):
                    accumulated_tool_calls.append(tool_call)
            elif event_type == "stream_end":
                break

        merged_tool_calls = _merge_tool_calls(accumulated_tool_calls)
        tool_blocks: list[ToolCallBlock] = []
        for tool_index, tool_call in enumerate(merged_tool_calls, start=1):
            function = tool_call.get("function") if isinstance(tool_call, dict) else None
            if not isinstance(function, dict):
                raise RuntimeError(
                    "invalid_tool_call: tool call is missing a function payload"
                )
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise RuntimeError("invalid_tool_call: tool call is missing a function name")
            raw_arguments = function.get("arguments", "{}")
            if not isinstance(raw_arguments, str):
                raw_arguments = json.dumps(raw_arguments)
            elif not raw_arguments.strip():
                raw_arguments = "{}"
            call_id = tool_call.get("id")
            tool_blocks.append(
                ToolCallBlock(
                    id=str(call_id or f"tool-call-{tool_index}"),
                    name=name,
                    input=raw_arguments,
                )
            )
        if unclassified_text_parts:
            marker_phase, private_reasoning, cleaned_parts = (
                _normalize_leading_response_envelopes(unclassified_text_parts)
            )
            if private_reasoning:
                literal_reasoning_parts.append(private_reasoning)
            if marker_phase is not None:
                for pending_text in cleaned_parts:
                    await classify_text(marker_phase, pending_text)
            elif tool_blocks:
                commentary_parts.extend(cleaned_parts)
            else:
                final_text_parts.extend(cleaned_parts)
        final_marker_phase, private_reasoning, cleaned_final_parts = (
            _normalize_leading_response_envelopes(final_text_parts)
        )
        if final_marker_phase is not None and final_marker_phase != "final_answer":
            raise RuntimeError(
                "invalid_model_phase_marker: textual phase marker "
                f"{final_marker_phase} conflicts with final_answer"
            )
        if private_reasoning:
            literal_reasoning_parts.append(private_reasoning)
        final_text_parts[:] = cleaned_final_parts
        if final_text_parts and tool_blocks:
            raise RuntimeError(
                "final_phase_with_tool_call: final-answer text cannot share a model "
                "response with tool calls"
            )
        await flush_commentary()
        await flush_auxiliary()

        if (
            (commentary_parts or auxiliary_parts)
            and not final_text_parts
            and not tool_blocks
        ):
            raise RuntimeError(
                "model_final_phase_missing: commentary-only response did not "
                "declare a tool call or final_answer"
            )
        if not commentary_parts and not final_text_parts and not tool_blocks:
            raise RuntimeError("empty_model_response: model returned no public response")

        for content in final_text_parts:
            yield ChatResponse(
                content=[TextBlock(text=content)],
                is_last=False,
                metadata={
                    "block_id": block_id,
                    "delta_index": final_text_delta_index,
                },
            )
            final_text_delta_index += 1

        commentary_text = "".join(commentary_parts)
        final_text = "".join(final_text_parts)
        full_text = commentary_text + final_text
        full_reasoning = "".join(
            accumulated_reasoning_parts or literal_reasoning_parts
        )
        if full_reasoning and _raw_private_reasoning_replay_enabled(model_name):
            self._private_reasoning_parts.append(full_reasoning)
            self._private_reasoning_parts = _trim_private_reasoning_parts(self._private_reasoning_parts)
        if final_text and self._on_final_text is not None:
            self._on_final_text(self.participant_id, final_text)
        blocks: list[TextBlock | ToolCallBlock] = []
        if full_text:
            blocks.append(TextBlock(text=full_text))
        blocks.extend(tool_blocks)
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
        allocate_tool_call_id: Callable[[], str],
        durable_external_execution: bool = False,
    ) -> None:
        self.run_id = run_id
        self.runtime_session_id = runtime_session_id
        self.participant_id = participant_id
        self.tool_id = tool_id
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.callback_client = callback_client
        self._allocate_tool_call_id = allocate_tool_call_id
        self.is_external_tool = durable_external_execution

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
            summary=f"{_humanize_tool_name(self.name)} requested.",
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
        except Exception:
            raise
        if _tool_requires_approval(response):
            raise OpenWebUIToolApprovalRequired(
                response=response,
                tool_call_id=tool_call_id,
                tool_id=self.tool_id,
                tool_name=self.name,
            )
        if _tool_approval_rejected(response):
            raise OpenWebUIToolApprovalRejected(
                response=response,
                tool_call_id=tool_call_id,
                tool_id=self.tool_id,
                tool_name=self.name,
            )
        state = _tool_result_state(response)
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


class AgentScopeRuntimeBridge:
    def __init__(
        self,
        *,
        run_id: str,
        runtime_session_id: str,
        callback_client: OpenWebUIBridgeCallbacks,
        assistant_context_by_participant: dict[str, list[dict[str, Any]]] | None = None,
        durable_external_tools: bool = False,
        checkpoint_state: dict[str, Any] | None = None,
    ) -> None:
        verify_agentscope_runtime_apis()
        self.run_id = run_id
        self.runtime_session_id = runtime_session_id
        self.callback_client = callback_client
        self._final_text_by_participant: dict[str, str] = {}
        self._assistant_context_by_participant: dict[str, list[dict[str, Any]]] = {
            str(participant_id): _trim_assistant_context_messages(messages)
            for participant_id, messages in (assistant_context_by_participant or {}).items()
            if isinstance(messages, list)
        }
        checkpoint_state = checkpoint_state or {}
        self._next_tool_call_index = max(
            1,
            int(checkpoint_state.get("next_tool_call_index") or 1),
        )
        self._model_call_indexes = {
            str(participant_id): max(1, int(index))
            for participant_id, index in (
                checkpoint_state.get("model_call_indexes") or {}
            ).items()
        }
        self._models_by_participant: dict[str, OpenWebUIAgentScopeModel] = {}
        self._durable_external_tools = durable_external_tools

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
        default_model_params: dict[str, Any] | None = None,
    ) -> OpenWebUIAgentScopeModel:
        model = OpenWebUIAgentScopeModel(
            run_id=self.run_id,
            runtime_session_id=self.runtime_session_id,
            participant_id=participant_id,
            model_id=model_id,
            callback_client=self.callback_client,
            on_final_text=self._record_final_text,
            assistant_context=lambda: self._assistant_context(participant_id),
            default_model_params=default_model_params,
            next_model_call_index=self._model_call_indexes.get(participant_id, 1),
        )
        self._models_by_participant[participant_id] = model
        return model

    def latest_final_text(self, participant_id: str) -> str:
        return self._final_text_by_participant.get(participant_id, "")

    def _record_final_text(self, participant_id: str, text: str) -> None:
        self._final_text_by_participant[participant_id] = text

    def _assistant_context(self, participant_id: str) -> list[dict[str, Any]]:
        return [dict(message) for message in self._assistant_context_by_participant.get(participant_id, [])]

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
            allocate_tool_call_id=self._allocate_tool_call_id,
            durable_external_execution=self._durable_external_tools,
        )

    def _allocate_tool_call_id(self) -> str:
        tool_call_id = f"tool-call-{self._next_tool_call_index}"
        self._next_tool_call_index += 1
        return tool_call_id

    def snapshot_state(self) -> dict[str, Any]:
        model_indexes = dict(self._model_call_indexes)
        model_indexes.update(
            {
                participant_id: model._next_model_call_index
                for participant_id, model in self._models_by_participant.items()
            }
        )
        return {
            "next_tool_call_index": self._next_tool_call_index,
            "model_call_indexes": model_indexes,
        }


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
    if isinstance(content, str):
        return content
    return "" if isinstance(payload, dict) else str(payload)


def _extract_model_reasoning_text(response: dict[str, Any]) -> str:
    payload = response.get("response", response)
    if isinstance(payload, dict):
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                for container_key in ("message", "delta"):
                    container = choice.get(container_key)
                    if not isinstance(container, dict):
                        continue
                    for key in ("reasoning_content", "reasoning", "thinking"):
                        value = container.get(key)
                        if isinstance(value, str):
                            return value
    return ""


def _raw_private_reasoning_replay_enabled(model_name: str | None) -> bool:
    lower = str(model_name or "").lower()
    return not any(marker in lower for marker in PRIVATE_REASONING_REPLAY_BLOCKED_MODEL_MARKERS)


def _append_private_reasoning_replay(
    messages: list[dict[str, Any]],
    *,
    model_name: str | None,
    reasoning_parts: list[str],
) -> list[dict[str, Any]]:
    if not reasoning_parts or not _raw_private_reasoning_replay_enabled(model_name):
        return messages
    reasoning_text = "\n".join(part for part in reasoning_parts if part)
    if not reasoning_text:
        return messages
    replay_message = {
        "role": "assistant",
        "content": "",
        "reasoning_content": reasoning_text[-PRIVATE_REASONING_REPLAY_MAX_CHARS:],
    }
    return [*messages, replay_message]


def _inject_assistant_context_replay(
    messages: list[dict[str, Any]],
    *,
    assistant_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    replay_messages = [
        normalized
        for message in assistant_messages
        if (normalized := _normalize_assistant_context_item(message)) is not None
    ]
    if not replay_messages:
        return messages
    insert_at = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role") or "").lower() == "user":
            insert_at = index
            break
    return [*messages[:insert_at], *replay_messages, *messages[insert_at:]]


def _normalize_assistant_context_item(message: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    item_type = str(message.get("type") or "").strip()
    if item_type == "function_call":
        call_id = str(message.get("call_id") or "").strip()
        name = str(message.get("name") or "").strip()
        if not call_id or not name:
            return None
        arguments = message.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments if arguments is not None else {})
        item = {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
        }
        item_id = message.get("id")
        if isinstance(item_id, str) and item_id:
            item["id"] = item_id
        return item
    if item_type == "function_call_output":
        call_id = str(message.get("call_id") or "").strip()
        if not call_id:
            return None
        output = message.get("output")
        if not isinstance(output, str):
            output = json.dumps(output if output is not None else "")
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        }
    if str(message.get("role") or "").lower() != "assistant":
        return None
    clean_content = " ".join(str(message.get("content") or "").split())
    if not clean_content:
        return None
    phase = str(message.get("phase") or "commentary").strip()
    if phase not in {"commentary", "final_answer"}:
        phase = "commentary"
    item = {
        "role": "assistant",
        "content": clean_content,
        "phase": phase,
    }
    for key in ("id", "status"):
        value = message.get(key)
        if isinstance(value, str) and value:
            item[key] = value
    return item


def _trim_assistant_context_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    total = 0
    for message in reversed(messages):
        normalized = _normalize_assistant_context_item(message)
        if normalized is None:
            continue
        content = _assistant_context_item_text(normalized)
        remaining = PUBLIC_ASSISTANT_CONTEXT_REPLAY_MAX_CHARS - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            if normalized.get("role") != "assistant":
                break
            normalized = {**normalized, "content": content[-remaining:]}
            kept.append(normalized)
            break
        kept.append(normalized)
        total += len(content) + 1
    return _drop_orphan_assistant_tool_items(list(reversed(kept)))


def _drop_orphan_assistant_tool_items(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    call_ids = {
        str(message.get("call_id") or "")
        for message in messages
        if message.get("type") == "function_call" and message.get("call_id")
    }
    output_ids = {
        str(message.get("call_id") or "")
        for message in messages
        if message.get("type") == "function_call_output" and message.get("call_id")
    }
    paired_ids = call_ids & output_ids
    return [
        message
        for message in messages
        if message.get("type") not in {"function_call", "function_call_output"}
        or str(message.get("call_id") or "") in paired_ids
    ]


def _assistant_context_item_text(item: dict[str, Any]) -> str:
    if item.get("role") == "assistant":
        return str(item.get("content") or "")
    if item.get("type") == "function_call":
        return f"{item.get('name') or ''} {item.get('arguments') or ''}"
    if item.get("type") == "function_call_output":
        return str(item.get("output") or "")
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _trim_private_reasoning_parts(parts: list[str]) -> list[str]:
    kept: list[str] = []
    total = 0
    for part in reversed(parts):
        if not part:
            continue
        remaining = PRIVATE_REASONING_REPLAY_MAX_CHARS - total
        if remaining <= 0:
            break
        if len(part) > remaining:
            kept.append(part[-remaining:])
            break
        kept.append(part)
        total += len(part)
    return list(reversed(kept))


def _merge_model_params(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    params = dict(defaults)
    for key, value in overrides.items():
        if key == "reasoning" and isinstance(params.get("reasoning"), dict) and isinstance(value, dict):
            params[key] = {**params[key], **value}
        else:
            params[key] = value
    return params


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
                if "arguments" in delta_function:
                    delta_arguments = delta_function["arguments"]
                    if "arguments" not in function:
                        function["arguments"] = delta_arguments
                    elif isinstance(function["arguments"], str) and isinstance(
                        delta_arguments,
                        str,
                    ):
                        function["arguments"] += delta_arguments
                    else:
                        raise RuntimeError(
                            "invalid_tool_call: cannot merge indexed tool arguments "
                            "with mixed or repeated non-string values"
                        )
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


def _tool_approval_rejected(response: dict[str, Any]) -> bool:
    return response.get("status") == "approval_rejected"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
