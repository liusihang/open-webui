import json
import logging
import math
import re
from functools import lru_cache
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.task import get_task_model_id

try:
    import tiktoken  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    tiktoken = None

log = logging.getLogger(__name__)


def _load_context_config_fallbacks() -> tuple[
    object, object, object, object, object, object, object
]:
    try:
        from open_webui.config import (
            CHAT_CONTEXT_BUDGET_ENABLED,
            CHAT_CONTEXT_NON_SYSTEM_MAX_TOKENS,
            CHAT_CONTEXT_WINDOW_ROUNDS,
            CHAT_CONTEXT_COMPACTION_TRIGGER_TOKENS,
            CHAT_CONTEXT_TOOL_OUTPUT_MAX_TOKENS,
            CHAT_CONTEXT_TOOL_OUTPUT_MAX_CHARS,
            CHAT_CONTEXT_ALLOW_TEMP_OVERFLOW,
        )

        return (
            CHAT_CONTEXT_BUDGET_ENABLED,
            CHAT_CONTEXT_NON_SYSTEM_MAX_TOKENS,
            CHAT_CONTEXT_WINDOW_ROUNDS,
            CHAT_CONTEXT_COMPACTION_TRIGGER_TOKENS,
            CHAT_CONTEXT_TOOL_OUTPUT_MAX_TOKENS,
            CHAT_CONTEXT_TOOL_OUTPUT_MAX_CHARS,
            CHAT_CONTEXT_ALLOW_TEMP_OVERFLOW,
        )
    except Exception:
        return (True, 32000, 5, 50000, 1800, 6000, True)


(
    _BUDGET_ENABLED_FALLBACK,
    _NON_SYSTEM_MAX_TOKENS_FALLBACK,
    _WINDOW_ROUNDS_FALLBACK,
    _COMPACTION_TRIGGER_TOKENS_FALLBACK,
    _TOOL_OUTPUT_MAX_TOKENS_FALLBACK,
    _TOOL_OUTPUT_MAX_CHARS_FALLBACK,
    _ALLOW_TEMP_OVERFLOW_FALLBACK,
) = _load_context_config_fallbacks()


_JSON_PRIORITY_KEYS = (
    "status",
    "code",
    "message",
    "error",
    "result",
    "results",
    "data",
    "items",
    "output",
    "content",
)

_IMPORTANT_LINE_PATTERN = re.compile(
    r"(error|warning|exception|traceback|failed|failure|status|result|http|timeout)",
    re.IGNORECASE,
)


SummaryCallable = Callable[..., Awaitable[str]]


def _config_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _safe_int(value: Any, default: int, minimum: Optional[int] = None) -> int:
    try:
        parsed = int(str(_config_value(value)).strip())
    except (TypeError, ValueError):
        parsed = default

    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _safe_bool(value: Any, default: bool = False) -> bool:
    resolved = _config_value(value)
    if isinstance(resolved, bool):
        return resolved
    if isinstance(resolved, str):
        normalized = resolved.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if resolved is None:
        return default
    return bool(resolved)


@lru_cache(maxsize=8)
def _get_tiktoken_encoding(encoding_name: str):
    if tiktoken is None:
        return None

    try:
        return tiktoken.get_encoding(encoding_name)
    except Exception:
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def _estimate_text_tokens(text: str, encoding_name: str) -> int:
    if not text:
        return 0

    encoding = _get_tiktoken_encoding(encoding_name)
    if encoding is not None:
        try:
            return len(encoding.encode(text))
        except Exception:
            pass

    # Conservative character fallback for mixed-language text.
    return max(1, int(math.ceil(len(text) / 3.0)))


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type in {"text", "input_text", "output_text"}:
                    parts.append(_normalize_text(item.get("text", "")))
                elif item_type == "image_url":
                    parts.append("[image]")
                else:
                    parts.append(_normalize_text(item))
            else:
                parts.append(_normalize_text(item))
        return "\n".join([part for part in parts if part])

    if isinstance(content, dict):
        return _normalize_text(content)

    return str(content)


def _message_to_text(message: Mapping[str, Any]) -> str:
    role = _normalize_text(message.get("role", ""))
    content = _content_to_text(message.get("content"))

    segments = [f"role={role}", f"content={content}"]

    tool_call_id = message.get("tool_call_id")
    if tool_call_id:
        segments.append(f"tool_call_id={_normalize_text(tool_call_id)}")

    tool_calls = message.get("tool_calls")
    if tool_calls:
        segments.append(f"tool_calls={_normalize_text(tool_calls)}")

    return "\n".join(segments)


def estimate_message_tokens(messages: Sequence[Mapping[str, Any]], encoding_name: str) -> int:
    total = 0
    for message in messages:
        if not isinstance(message, Mapping):
            continue

        text = _message_to_text(message)
        total += _estimate_text_tokens(text, encoding_name)
        total += 4

    return total


def split_system_and_non_system(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    system_messages: list[dict[str, Any]] = []
    non_system_messages: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, Mapping):
            continue
        item = dict(message)
        if item.get("role") == "system":
            system_messages.append(item)
        else:
            non_system_messages.append(item)

    return system_messages, non_system_messages


def _recent_round_start_index(messages: Sequence[Mapping[str, Any]], rounds: int) -> int:
    if rounds <= 0:
        return 0

    user_indices = [idx for idx, message in enumerate(messages) if message.get("role") == "user"]
    if not user_indices:
        return 0

    if len(user_indices) <= rounds:
        return user_indices[0]

    return user_indices[-rounds]


def select_recent_round_window(
    messages: Sequence[Mapping[str, Any]], rounds: int = 5
) -> list[dict[str, Any]]:
    if not messages:
        return []

    start_index = _recent_round_start_index(messages, rounds)
    return [dict(message) for message in messages[start_index:] if isinstance(message, Mapping)]


def _truncate_with_head_tail(text: str, max_chars: int, marker: str = "\n...[truncated]...\n") -> str:
    if max_chars <= 0:
        return ""

    if len(text) <= max_chars:
        return text

    if max_chars <= len(marker) + 8:
        return text[:max_chars]

    head_chars = int(max_chars * 0.65)
    tail_chars = max_chars - head_chars - len(marker)
    if tail_chars < 8:
        tail_chars = 8
        head_chars = max_chars - tail_chars - len(marker)

    head = text[: max(0, head_chars)].rstrip()
    tail = text[-tail_chars:].lstrip()
    return f"{head}{marker}{tail}"


def _compress_text_medium(text: str, max_chars: int) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= max_chars:
        return normalized

    lines = [line for line in normalized.splitlines() if line.strip()]
    if not lines:
        return _truncate_with_head_tail(normalized, max_chars)

    head = lines[:80]
    tail = lines[-80:]
    important = [line for line in lines if _IMPORTANT_LINE_PATTERN.search(line)]

    merged: list[str] = []
    seen: set[str] = set()
    for line in [*head, *important, *tail]:
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(line)

    compacted = "\n".join(merged)
    return _truncate_with_head_tail(compacted, max_chars)


def _compress_json_value(value: Any, depth: int = 0) -> Any:
    if depth >= 3:
        if isinstance(value, str):
            return _truncate_with_head_tail(value, 300)
        return "[truncated]"

    if isinstance(value, str):
        limit = 1200 if depth == 0 else 500
        return _truncate_with_head_tail(value, limit)

    if isinstance(value, list):
        max_items = 8 if depth == 0 else 4
        compressed_items = [_compress_json_value(item, depth + 1) for item in value[:max_items]]
        if len(value) > max_items:
            compressed_items.append(
                {
                    "_truncated_items": len(value) - max_items,
                    "_original_length": len(value),
                }
            )
        return compressed_items

    if isinstance(value, dict):
        keys = list(value.keys())
        prioritized = [key for key in _JSON_PRIORITY_KEYS if key in value]
        others = [key for key in keys if key not in prioritized]
        ordered_keys = prioritized + others

        max_keys = 20 if depth == 0 else 10
        selected_keys = ordered_keys[:max_keys]

        compressed: dict[str, Any] = {}
        for key in selected_keys:
            compressed[str(key)] = _compress_json_value(value[key], depth + 1)

        if len(ordered_keys) > max_keys:
            compressed["_truncated_keys"] = len(ordered_keys) - max_keys

        return compressed

    return value


def compress_tool_message_content(
    content: Any,
    level: str = "medium",
    max_tokens: int = 1800,
    max_chars: int = 6000,
    encoding_name: str = "cl100k_base",
) -> str:
    raw_text = _content_to_text(content)
    if not raw_text:
        return ""

    if level != "medium":
        return _truncate_with_head_tail(raw_text, max_chars)

    if len(raw_text) <= max_chars and _estimate_text_tokens(raw_text, encoding_name) <= max_tokens:
        return raw_text

    compacted = ""

    try:
        parsed = json.loads(raw_text)
        compacted_json = _compress_json_value(parsed)
        compacted = json.dumps(compacted_json, ensure_ascii=False, indent=2)
    except Exception:
        compacted = _compress_text_medium(raw_text, max_chars)

    compacted = _truncate_with_head_tail(compacted, max_chars)

    if _estimate_text_tokens(compacted, encoding_name) > max_tokens:
        shrinking_limit = max_chars
        while shrinking_limit > 400 and _estimate_text_tokens(compacted, encoding_name) > max_tokens:
            shrinking_limit = int(shrinking_limit * 0.85)
            compacted = _truncate_with_head_tail(compacted, shrinking_limit)

    return compacted


def compress_tool_messages(
    messages: Sequence[Mapping[str, Any]],
    level: str = "medium",
    max_tokens: int = 1800,
    max_chars: int = 6000,
    encoding_name: str = "cl100k_base",
) -> list[dict[str, Any]]:
    compressed_messages: list[dict[str, Any]] = []

    for message in messages:
        item = dict(message)
        if item.get("role") == "tool":
            item["content"] = compress_tool_message_content(
                item.get("content", ""),
                level=level,
                max_tokens=max_tokens,
                max_chars=max_chars,
                encoding_name=encoding_name,
            )
        compressed_messages.append(item)

    return compressed_messages


def _compress_assistant_message_content(content: Any, max_chars: int = 4500) -> Any:
    if isinstance(content, list):
        compressed_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"text", "input_text", "output_text"}:
                compressed_parts.append({**part, "text": _compress_text_medium(_normalize_text(part.get("text", "")), max_chars // 2)})
            else:
                compressed_parts.append(part)
        return compressed_parts

    if isinstance(content, str):
        return _compress_text_medium(content, max_chars)

    return content


def _compress_assistant_messages_for_budget(
    messages: Sequence[Mapping[str, Any]], max_chars: int = 4500
) -> list[dict[str, Any]]:
    compressed: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        if item.get("role") == "assistant":
            item["content"] = _compress_assistant_message_content(item.get("content"), max_chars=max_chars)
        compressed.append(item)
    return compressed


def _extract_chat_completion_text(response: Any) -> Optional[str]:
    data = response

    if isinstance(data, JSONResponse):
        try:
            data = json.loads(data.body.decode("utf-8", "replace"))
        except Exception:
            return None

    if isinstance(data, list) and data:
        data = data[0]

    if isinstance(data, StreamingResponse):
        return None

    if not isinstance(data, dict):
        return None

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        if isinstance(message, dict):
            content = message.get("content") or message.get("reasoning_content")
            if isinstance(content, str) and content.strip():
                return content.strip()

    output = data.get("output")
    if isinstance(output, list):
        text_blocks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                content = item.get("content", [])
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") in {
                            "output_text",
                            "text",
                            "input_text",
                        }:
                            text_blocks.append(_normalize_text(part.get("text", "")))
        joined = "\n".join([block for block in text_blocks if block]).strip()
        if joined:
            return joined

    return None


def _resolve_models(request: Request, models: Optional[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(models, dict) and models:
        return models

    if getattr(request.state, "direct", False) and hasattr(request.state, "model"):
        model = request.state.model
        if isinstance(model, dict) and model.get("id"):
            return {model["id"]: model}

    return request.app.state.MODELS


def _build_summary_input(messages: Sequence[Mapping[str, Any]], max_chars: int = 18000) -> str:
    lines: list[str] = []
    for message in messages:
        role = _normalize_text(message.get("role", "assistant")).upper()
        content = _content_to_text(message.get("content"))
        content = _truncate_with_head_tail(content, 1200)
        lines.append(f"{role}: {content}")

    history = "\n".join(lines)
    return _truncate_with_head_tail(history, max_chars)


def _fallback_compaction_summary(messages: Sequence[Mapping[str, Any]]) -> str:
    user_messages = [
        _truncate_with_head_tail(_content_to_text(message.get("content")), 260)
        for message in messages
        if message.get("role") == "user"
    ]
    assistant_messages = [
        _truncate_with_head_tail(_content_to_text(message.get("content")), 260)
        for message in messages
        if message.get("role") in {"assistant", "tool"}
    ]

    lines = [
        "Compressed prior context summary:",
        f"- Compacted messages: {len(messages)}",
    ]

    if user_messages:
        lines.append(f"- Earlier user intent: {user_messages[0]}")
        lines.append(f"- Latest pre-window user point: {user_messages[-1]}")

    if assistant_messages:
        lines.append(f"- Key assistant/tool outcome: {assistant_messages[-1]}")

    return "\n".join(lines)


async def summarize_old_context(
    request: Request,
    user: Any,
    old_messages: Sequence[Mapping[str, Any]],
    current_model_id: str,
    *,
    models: Optional[dict[str, Any]] = None,
    max_summary_tokens: int = 500,
) -> str:
    if not old_messages:
        return ""

    resolved_models = _resolve_models(request, models)
    if not resolved_models:
        return _fallback_compaction_summary(old_messages)

    model_id = current_model_id if current_model_id in resolved_models else next(iter(resolved_models.keys()))

    try:
        task_model_id = get_task_model_id(
            model_id,
            request.app.state.config.TASK_MODEL,
            request.app.state.config.TASK_MODEL_EXTERNAL,
            resolved_models,
        )
    except Exception:
        task_model_id = model_id

    history = _build_summary_input(old_messages)

    prompt = (
        "Summarize the prior conversation context for future turns. "
        "Keep it compact and factual. Include user goals, decisions, constraints, and unresolved items. "
        "Use concise bullet points and avoid speculation."
    )

    payload: dict[str, Any] = {
        "model": task_model_id,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": history},
        ],
        "stream": False,
        "metadata": {
            "task": "chat_context_compaction",
            "chat_id": (
                request.state.metadata.get("chat_id")
                if isinstance(getattr(request.state, "metadata", None), dict)
                else None
            ),
        },
    }

    model = resolved_models.get(task_model_id, {})
    owned_by = model.get("owned_by") if isinstance(model, dict) else None

    if owned_by == "ollama":
        payload["max_tokens"] = max_summary_tokens
    else:
        payload["max_completion_tokens"] = max_summary_tokens

    try:
        response = await generate_chat_completion(request, payload, user=user)
        summary = _extract_chat_completion_text(response)
        if summary:
            return _truncate_with_head_tail(summary, 2400)
    except Exception as exc:
        log.debug(f"Compaction summarization failed: {exc}")

    return _fallback_compaction_summary(old_messages)


async def apply_context_budget_policy(
    request: Request,
    form_data: dict[str, Any],
    user: Any,
    *,
    models: Optional[dict[str, Any]] = None,
    summarizer: Optional[SummaryCallable] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    messages = form_data.get("messages")
    if not isinstance(messages, list) or not messages:
        return form_data, {"applied": False, "reason": "empty_messages"}

    config = request.app.state.config
    encoding_name = str(_config_value(getattr(config, "TIKTOKEN_ENCODING_NAME", "cl100k_base")))
    budget_enabled = _safe_bool(
        getattr(config, "CHAT_CONTEXT_BUDGET_ENABLED", _BUDGET_ENABLED_FALLBACK),
        default=True,
    )
    if not budget_enabled:
        return form_data, {"applied": False, "reason": "disabled", "enabled": False}

    non_system_max_tokens = _safe_int(
        getattr(config, "CHAT_CONTEXT_NON_SYSTEM_MAX_TOKENS", _NON_SYSTEM_MAX_TOKENS_FALLBACK),
        32000,
        minimum=256,
    )
    window_rounds = _safe_int(
        getattr(config, "CHAT_CONTEXT_WINDOW_ROUNDS", _WINDOW_ROUNDS_FALLBACK),
        5,
        minimum=1,
    )
    compaction_trigger_tokens = _safe_int(
        getattr(
            config,
            "CHAT_CONTEXT_COMPACTION_TRIGGER_TOKENS",
            _COMPACTION_TRIGGER_TOKENS_FALLBACK,
        ),
        50000,
        minimum=non_system_max_tokens,
    )
    tool_output_max_tokens = _safe_int(
        getattr(
            config,
            "CHAT_CONTEXT_TOOL_OUTPUT_MAX_TOKENS",
            _TOOL_OUTPUT_MAX_TOKENS_FALLBACK,
        ),
        1800,
        minimum=128,
    )
    tool_output_max_chars = _safe_int(
        getattr(
            config,
            "CHAT_CONTEXT_TOOL_OUTPUT_MAX_CHARS",
            _TOOL_OUTPUT_MAX_CHARS_FALLBACK,
        ),
        6000,
        minimum=256,
    )
    allow_temp_overflow = _safe_bool(
        getattr(config, "CHAT_CONTEXT_ALLOW_TEMP_OVERFLOW", _ALLOW_TEMP_OVERFLOW_FALLBACK),
        default=True,
    )

    valid_messages = [dict(message) for message in messages if isinstance(message, Mapping)]
    system_messages, non_system_messages = split_system_and_non_system(valid_messages)

    tokens_before = estimate_message_tokens(non_system_messages, encoding_name)

    windowed_non_system = select_recent_round_window(non_system_messages, rounds=window_rounds)
    window_start = len(non_system_messages) - len(windowed_non_system)
    compactable_prefix = non_system_messages[: max(0, window_start)]

    working_non_system = list(windowed_non_system)
    compaction_applied = False

    if tokens_before > compaction_trigger_tokens and compactable_prefix:
        try:
            summary_fn = summarizer or summarize_old_context
            summary = await summary_fn(
                request=request,
                user=user,
                old_messages=compactable_prefix,
                current_model_id=str(form_data.get("model", "")),
                models=models,
            )
            summary = (summary or "").strip()
        except Exception as exc:
            log.debug(f"Compaction summary generation failed: {exc}")
            summary = _fallback_compaction_summary(compactable_prefix)

        if summary:
            working_non_system = [
                {
                    "role": "assistant",
                    "content": f"[Compacted Context Summary]\n{summary}",
                },
                *working_non_system,
            ]
            compaction_applied = True

    working_non_system = compress_tool_messages(
        working_non_system,
        level="medium",
        max_tokens=tool_output_max_tokens,
        max_chars=tool_output_max_chars,
        encoding_name=encoding_name,
    )

    tokens_after_tool_compression = estimate_message_tokens(working_non_system, encoding_name)

    if tokens_after_tool_compression > non_system_max_tokens:
        working_non_system = _compress_assistant_messages_for_budget(
            working_non_system,
            max_chars=max(800, int(tool_output_max_chars * 0.75)),
        )

    tokens_after_assistant_compression = estimate_message_tokens(
        working_non_system, encoding_name
    )

    overflow = tokens_after_assistant_compression > non_system_max_tokens

    if overflow and not allow_temp_overflow:
        # Strict fallback path: retain latest user turn and trim oldest non-user messages.
        trimmed: list[dict[str, Any]] = []
        for message in working_non_system:
            if message.get("role") == "user":
                trimmed.append(message)
            elif not trimmed:
                # allow leading assistant summary only if no user yet
                trimmed.append(message)

        if trimmed:
            working_non_system = trimmed
            tokens_after_assistant_compression = estimate_message_tokens(
                working_non_system, encoding_name
            )
            overflow = tokens_after_assistant_compression > non_system_max_tokens

    if overflow:
        log.warning(
            "Context budget overflow after compression "
            f"(non_system_tokens={tokens_after_assistant_compression}, max={non_system_max_tokens}, "
            f"window_rounds={window_rounds}, allow_temp_overflow={allow_temp_overflow})"
        )

    form_data["messages"] = [*system_messages, *working_non_system]

    diagnostics = {
        "applied": True,
        "enabled": budget_enabled,
        "window_rounds": window_rounds,
        "tokens_before": tokens_before,
        "tokens_after_tool_compression": tokens_after_tool_compression,
        "tokens_after_assistant_compression": tokens_after_assistant_compression,
        "non_system_max_tokens": non_system_max_tokens,
        "compaction_trigger_tokens": compaction_trigger_tokens,
        "compaction_applied": compaction_applied,
        "overflow": overflow,
        "allow_temp_overflow": allow_temp_overflow,
        "windowed_message_count": len(working_non_system),
    }

    return form_data, diagnostics
