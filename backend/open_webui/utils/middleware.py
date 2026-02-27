import time
import logging
import sys
import os
import base64
import textwrap

import asyncio
from aiocache import cached
from typing import Any, Optional
import random
import json
import html
import inspect
import re
import ast

from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor


from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
from starlette.responses import Response, StreamingResponse, JSONResponse


from open_webui.utils.misc import is_string_allowed
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.chats import Chats
from open_webui.models.folders import Folders
from open_webui.models.files import Files
from open_webui.models.access_grants import AccessGrants
from open_webui.models.users import Users
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
)
from open_webui.routers.tasks import (
    generate_queries,
    generate_title,
    generate_follow_ups,
    generate_image_prompt,
    generate_chat_tags,
)
from open_webui.routers.retrieval import (
    process_web_search,
    SearchForm,
)
from open_webui.utils.tools import get_builtin_tools
from open_webui.routers.images import (
    image_generations,
    CreateImageForm,
    image_edits,
    EditImageForm,
)
from open_webui.routers.pipelines import (
    process_pipeline_inlet_filter,
    process_pipeline_outlet_filter,
)
from open_webui.routers.memories import query_memory, QueryMemoryForm

from open_webui.utils.webhook import post_webhook
from open_webui.utils.files import (
    convert_markdown_base64_images,
    get_file_url_from_base64,
    get_image_base64_from_url,
    get_image_url_from_base64,
)


from open_webui.models.users import UserModel
from open_webui.models.functions import Functions
from open_webui.models.models import Models

from open_webui.retrieval.utils import get_sources_from_items
from open_webui.utils.adaptive_file_context import (
    apply_adaptive_context_to_items,
    resolve_adaptive_config,
)
from open_webui.utils.chat_context_budget import apply_context_budget_policy
from open_webui.utils.message_merge import merge_messages_preserving_incoming_tail


from open_webui.utils.sanitize import sanitize_code
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.task import (
    get_task_model_id,
    rag_template,
    tools_function_calling_generation_template,
)
from open_webui.utils.misc import (
    deep_update,
    extract_urls,
    get_message_list,
    add_or_update_system_message,
    add_or_update_user_message,
    get_last_user_message,
    get_last_user_message_item,
    get_last_assistant_message,
    get_system_message,
    prepend_to_first_user_message_content,
    convert_logit_bias_input_to_json,
    get_content_from_message,
    convert_output_to_messages,
)
from open_webui.utils.tools import (
    get_tools,
    get_updated_tool_function,
    has_tool_server_access,
)
from open_webui.utils.plugin import load_function_module_by_id
from open_webui.utils.filter import (
    get_sorted_filter_ids,
    process_filter_functions,
)
from open_webui.utils.code_interpreter import execute_code_jupyter
from open_webui.utils.payload import apply_system_prompt_to_body
from open_webui.utils.response import normalize_usage
from open_webui.utils.mcp.client import MCPClient
from open_webui.utils.auto_memory import run_auto_memory_writeback


from open_webui.config import (
    CACHE_DIR,
    DEFAULT_VOICE_MODE_PROMPT_TEMPLATE,
    DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE,
    DEFAULT_CODE_INTERPRETER_PROMPT,
    CODE_INTERPRETER_BLOCKED_MODULES,
    ADAPTIVE_FILE_CONTEXT_ENABLED,
    ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE,
    ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE,
    ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST,
    ADAPTIVE_FILE_CONTEXT_DEBUG,
)
from open_webui.env import (
    GLOBAL_LOG_LEVEL,
    ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION,
    CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE,
    CHAT_RESPONSE_MAX_TOOL_CALL_RETRIES,
    BYPASS_MODEL_ACCESS_CONTROL,
    ENABLE_REALTIME_CHAT_SAVE,
    ENABLE_QUERIES_CACHE,
    RAG_SYSTEM_CONTEXT,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    FORWARD_SESSION_INFO_HEADER_CHAT_ID,
    FORWARD_SESSION_INFO_HEADER_MESSAGE_ID,
)
from open_webui.utils.headers import include_user_info_headers
from open_webui.constants import TASKS

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


DEFAULT_REASONING_TAGS = [
    ("<think>", "</think>"),
    ("<thinking>", "</thinking>"),
    ("<reason>", "</reason>"),
    ("<reasoning>", "</reasoning>"),
    ("<thought>", "</thought>"),
    ("<Thought>", "</Thought>"),
    ("<|begin_of_thought|>", "<|end_of_thought|>"),
    ("◁think▷", "◁/think▷"),
]
DEFAULT_SOLUTION_TAGS = [("<|begin_of_solution|>", "<|end_of_solution|>")]
DEFAULT_CODE_INTERPRETER_TAGS = [("<code_interpreter>", "</code_interpreter>")]

MEMORY_EXPLICIT_CUES = (
    "remember",
    "what do you remember",
    "do you remember",
    "as i said",
    "i told you",
    "my preference",
    "my preferences",
    "we discussed",
    "last time",
    "previously",
    "继续",
    "还记得",
    "记得",
    "上次",
    "之前说过",
    "我的偏好",
)

MEMORY_CONTINUITY_CUES = (
    "continue",
    "pick up where we left off",
    "same as before",
    "the previous",
    "that plan",
    "this plan",
    "same project",
    "接着",
    "继续这个",
    "延续",
    "按照之前",
    "上一个方案",
)

MEMORY_PERSONAL_CUES = (
    "i prefer",
    "i like",
    "my style",
    "my workflow",
    "for me",
    "about me",
    "我喜欢",
    "我偏好",
    "我的习惯",
    "我的风格",
    "对我来说",
)

STATELESS_QUERY_PREFIXES = (
    "what is",
    "what are",
    "who is",
    "when is",
    "where is",
    "define",
    "explain",
    "translate",
    "summarize",
    "计算",
    "解释",
    "翻译",
    "总结",
    "是什么",
)


def output_id(prefix: str) -> str:
    """Generate OR-style ID: prefix + 24-char hex UUID."""
    return f"{prefix}_{uuid4().hex[:24]}"


def get_citation_source_from_tool_result(
    tool_name: str, tool_params: dict, tool_result: str, tool_id: str = ""
) -> list[dict]:
    """
    Parse a tool's result and convert it to source dicts for citation display.

    Follows the source format conventions from get_sources_from_items:
    - source: file/item info object with id, name, type
    - document: list of document contents
    - metadata: list of metadata objects with source, file_id, name fields

    Returns a list of sources (usually one, but query_knowledge_files may return multiple).
    """
    try:
        try:
            tool_result = json.loads(tool_result)
        except (json.JSONDecodeError, TypeError):
            pass  # keep tool_result as-is (e.g. fetch_url returns plain text)
        if isinstance(tool_result, dict) and "error" in tool_result:
            return []

        if tool_name == "search_web":
            # Parse JSON array: [{"title": "...", "link": "...", "snippet": "..."}]
            results = tool_result
            documents = []
            metadata = []

            for result in results:
                title = result.get("title", "")
                link = result.get("link", "")
                snippet = result.get("snippet", "")

                documents.append(f"{title}\n{snippet}")
                metadata.append(
                    {
                        "source": link,
                        "name": title,
                        "url": link,
                    }
                )

            return [
                {
                    "source": {"name": "search_web", "id": "search_web"},
                    "document": documents,
                    "metadata": metadata,
                }
            ]

        elif tool_name == "view_knowledge_file":
            file_data = tool_result
            filename = file_data.get("filename", "Unknown File")
            file_id = file_data.get("id", "")
            knowledge_name = file_data.get("knowledge_name", "")

            return [
                {
                    "source": {
                        "id": file_id,
                        "name": filename,
                        "type": "file",
                    },
                    "document": [file_data.get("content", "")],
                    "metadata": [
                        {
                            "file_id": file_id,
                            "name": filename,
                            "source": filename,
                            **(
                                {"knowledge_name": knowledge_name}
                                if knowledge_name
                                else {}
                            ),
                        }
                    ],
                }
            ]

        elif tool_name == "fetch_url":
            url = tool_params.get("url", "")
            content = tool_result if isinstance(tool_result, str) else str(tool_result)
            snippet = content[:500] + ("..." if len(content) > 500 else "")

            return [
                {
                    "source": {"name": url or "fetch_url", "id": url or "fetch_url"},
                    "document": [snippet],
                    "metadata": [
                        {
                            "source": url,
                            "name": url,
                            "url": url,
                        }
                    ],
                }
            ]

        elif tool_name == "query_knowledge_files":
            chunks = tool_result

            # Group chunks by source for better citation display
            # Each unique source becomes a separate source entry
            sources_by_file = {}

            for chunk in chunks:
                source_name = chunk.get("source", "Unknown")
                file_id = chunk.get("file_id", "")
                note_id = chunk.get("note_id", "")
                chunk_type = chunk.get("type", "file")
                content = chunk.get("content", "")

                # Use file_id or note_id as the key
                key = file_id or note_id or source_name

                if key not in sources_by_file:
                    sources_by_file[key] = {
                        "source": {
                            "id": file_id or note_id,
                            "name": source_name,
                            "type": chunk_type,
                        },
                        "document": [],
                        "metadata": [],
                    }

                sources_by_file[key]["document"].append(content)
                sources_by_file[key]["metadata"].append(
                    {
                        "file_id": file_id,
                        "name": source_name,
                        "source": source_name,
                        **({"note_id": note_id} if note_id else {}),
                    }
                )

            # Return all grouped sources as a list
            if sources_by_file:
                return list(sources_by_file.values())

            # Empty result fallback
            return []

        else:
            # Fallback for other tools
            return [
                {
                    "source": {
                        "name": tool_name,
                        "type": "tool",
                        "id": tool_id or tool_name,
                    },
                    "document": [str(tool_result)],
                    "metadata": [{"source": tool_name, "name": tool_name}],
                }
            ]
    except Exception as e:
        log.exception(f"Error parsing tool result for {tool_name}: {e}")
        return [
            {
                "source": {"name": tool_name, "type": "tool"},
                "document": [str(tool_result)],
                "metadata": [{"source": tool_name}],
            }
        ]


def split_content_and_whitespace(content):
    content_stripped = content.rstrip()
    original_whitespace = (
        content[len(content_stripped) :] if len(content) > len(content_stripped) else ""
    )
    return content_stripped, original_whitespace


def is_opening_code_block(content):
    backtick_segments = content.split("```")
    # Even number of segments means the last backticks are opening a new block
    return len(backtick_segments) > 1 and len(backtick_segments) % 2 == 0


def serialize_output(output: list) -> str:
    """
    Convert OR-aligned output items to HTML for display.
    For LLM consumption, use convert_output_to_messages() instead.
    """
    content = ""

    # First pass: collect function_call_output items by call_id for lookup
    tool_outputs = {}
    for item in output:
        if item.get("type") == "function_call_output":
            tool_outputs[item.get("call_id")] = item

    # Second pass: render items in order
    for idx, item in enumerate(output):
        item_type = item.get("type", "")

        if item_type == "message":
            for content_part in item.get("content", []):
                if "text" in content_part:
                    text = content_part.get("text", "").strip()
                    if text:
                        content = f"{content}{text}\n"

        elif item_type == "function_call":
            # Render tool call inline with its result (if available)
            if content and not content.endswith("\n"):
                content += "\n"

            call_id = item.get("call_id", "")
            name = item.get("name", "")
            arguments = item.get("arguments", "")

            result_item = tool_outputs.get(call_id)
            if result_item:
                result_text = ""
                for out in result_item.get("output", []):
                    if "text" in out:
                        result_text += out.get("text", "")
                files = result_item.get("files")
                embeds = result_item.get("embeds", "")

                content += f'<details type="tool_calls" done="true" id="{call_id}" name="{name}" arguments="{html.escape(json.dumps(arguments))}" result="{html.escape(json.dumps(result_text, ensure_ascii=False))}" files="{html.escape(json.dumps(files)) if files else ""}" embeds="{html.escape(json.dumps(embeds))}">\n<summary>Tool Executed</summary>\n</details>\n'
            else:
                content += f'<details type="tool_calls" done="false" id="{call_id}" name="{name}" arguments="{html.escape(json.dumps(arguments))}">\n<summary>Executing...</summary>\n</details>\n'

        elif item_type == "function_call_output":
            # Already handled inline with function_call above
            pass

        elif item_type == "reasoning":
            reasoning_parts = []
            # Check for 'summary' (new structure) or 'content' (legacy/fallback)
            source_list = item.get("summary", []) or item.get("content", [])
            for content_part in source_list:
                if _is_encrypted_reasoning_part(content_part):
                    continue
                if not isinstance(content_part, dict):
                    continue
                if "text" in content_part:
                    text_value = content_part.get("text", "")
                    if (
                        isinstance(text_value, str)
                        and text_value.strip()
                        and not _is_encrypted_reasoning_text(text_value)
                    ):
                        reasoning_parts.append(text_value.strip())
                elif "summary" in content_part:  # Handle potential nested logic if any
                    pass

            reasoning_content = "\n\n".join(reasoning_parts)

            duration = item.get("duration")
            status = item.get("status", "in_progress")
            has_encrypted_content = isinstance(item.get("encrypted_content"), str)

            if not reasoning_content and (
                has_encrypted_content or str(status).lower() == "completed"
            ):
                continue

            # Infer completion: if this reasoning item is NOT the last item,
            # render as done (a subsequent item means reasoning is complete)
            is_last_item = idx == len(output) - 1

            if content and not content.endswith("\n"):
                content += "\n"

            display = html.escape(
                "\n".join(
                    (f"> {line}" if not line.startswith(">") else line)
                    for line in reasoning_content.splitlines()
                )
            )

            if status == "completed" or duration is not None or not is_last_item:
                content = f'{content}<details type="reasoning" done="true" duration="{duration or 0}">\n<summary>Thought for {duration or 0} seconds</summary>\n{display}\n</details>\n'
            else:
                content = f'{content}<details type="reasoning" done="false">\n<summary>Thinking…</summary>\n{display}\n</details>\n'

        elif item_type == "open_webui:code_interpreter":
            content_stripped, original_whitespace = split_content_and_whitespace(
                content
            )
            if is_opening_code_block(content_stripped):
                content = content_stripped.rstrip("`").rstrip() + original_whitespace
            else:
                content = content_stripped + original_whitespace

            if content and not content.endswith("\n"):
                content += "\n"

            # Render the code_interpreter item as a <details> block
            # so the frontend Collapsible renders "Analyzing..."/"Analyzed".
            code = item.get("code", "").strip()
            lang = item.get("lang", "python")
            status = item.get("status", "in_progress")
            duration = item.get("duration")
            is_last_item = idx == len(output) - 1

            # Build inner content: code block
            display = ""
            if code:
                display = f"```{lang}\n{code}\n```"

            # Build output attribute as HTML-escaped JSON for CodeBlock.svelte
            ci_output = item.get("output")
            output_attr = ""
            if ci_output:
                if isinstance(ci_output, dict):
                    output_json = json.dumps(ci_output, ensure_ascii=False)
                else:
                    output_json = json.dumps(
                        {"result": str(ci_output)}, ensure_ascii=False
                    )
                output_attr = f' output="{html.escape(output_json)}"'

            if status == "completed" or duration is not None or not is_last_item:
                content += f'<details type="code_interpreter" done="true" duration="{duration or 0}"{output_attr}>\n<summary>Analyzed</summary>\n{display}\n</details>\n'
            else:
                content += f'<details type="code_interpreter" done="false"{output_attr}>\n<summary>Analyzing…</summary>\n{display}\n</details>\n'

    return content.strip()


def extract_reasoning_text(payload: Optional[dict[str, Any]]) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in ("reasoning_content", "reasoning", "thinking"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    details = payload.get("reasoning_details")
    if not isinstance(details, list):
        return ""

    parts = []
    for item in details:
        if isinstance(item, str):
            if item.strip():
                parts.append(item)
            continue

        if not isinstance(item, dict):
            continue

        for key in ("text", "reasoning", "content", "summary"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)

    normalized_parts = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    return "\n\n".join(normalized_parts)


def merge_output_item_preserve_content(existing: Any, incoming: Any) -> Any:
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return incoming if incoming is not None else existing

    merged = dict(existing)

    for key, incoming_value in incoming.items():
        existing_value = merged.get(key)

        if isinstance(incoming_value, str):
            if incoming_value == "" and isinstance(existing_value, str) and existing_value:
                continue
            merged[key] = incoming_value
            continue

        if isinstance(incoming_value, dict):
            if isinstance(existing_value, dict):
                merged[key] = merge_output_item_preserve_content(existing_value, incoming_value)
            else:
                merged[key] = incoming_value
            continue

        if isinstance(incoming_value, list):
            if not incoming_value and isinstance(existing_value, list) and existing_value:
                continue

            if isinstance(existing_value, list):
                merged_list: list[Any] = []
                max_len = max(len(existing_value), len(incoming_value))
                for idx in range(max_len):
                    has_existing = idx < len(existing_value)
                    has_incoming = idx < len(incoming_value)

                    if has_existing and has_incoming:
                        existing_item = existing_value[idx]
                        incoming_item = incoming_value[idx]
                        if isinstance(existing_item, dict) and isinstance(incoming_item, dict):
                            merged_list.append(
                                merge_output_item_preserve_content(existing_item, incoming_item)
                            )
                        elif (
                            isinstance(incoming_item, str)
                            and incoming_item == ""
                            and isinstance(existing_item, str)
                            and existing_item
                        ):
                            merged_list.append(existing_item)
                        else:
                            merged_list.append(incoming_item)
                    elif has_incoming:
                        merged_list.append(incoming_value[idx])
                    else:
                        merged_list.append(existing_value[idx])

                merged[key] = merged_list
            else:
                merged[key] = incoming_value
            continue

        merged[key] = incoming_value

    return merged


def merge_final_output_preserve_content(
    current_output: list[Any], final_output: list[Any]
) -> list[Any]:
    if not isinstance(final_output, list):
        return current_output

    if not isinstance(current_output, list):
        return final_output

    merged_output = list(current_output)

    for idx, final_item in enumerate(final_output):
        if idx < len(merged_output):
            merged_output[idx] = merge_output_item_preserve_content(
                merged_output[idx], final_item
            )
        else:
            merged_output.append(final_item)

    return merged_output


def _to_timestamp(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _normalize_reasoning_token(value: str) -> str:
    return re.sub(r"[\s_]+", " ", value).strip().lower()


def _is_encrypted_reasoning_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    normalized = _normalize_reasoning_token(value)
    return normalized in {
        "encrypted reasoning content",
        "reasoning encrypted content",
    }


def _is_encrypted_reasoning_part(part: Any) -> bool:
    if not isinstance(part, dict):
        return False

    part_type = str(part.get("type") or "").strip().lower()
    if "encrypted" in part_type:
        return True

    if isinstance(part.get("encrypted_content"), str):
        return True

    text = part.get("text")
    if _is_encrypted_reasoning_text(text):
        return True

    signature = part.get("signature")
    if isinstance(signature, str) and not isinstance(text, str):
        return True

    return False


def _sanitize_reasoning_parts(parts: Any) -> list[dict[str, Any]]:
    if not isinstance(parts, list):
        return []

    sanitized: list[dict[str, Any]] = []
    for raw_part in parts:
        if not isinstance(raw_part, dict):
            continue

        part = dict(raw_part)
        if _is_encrypted_reasoning_part(part):
            continue

        text = part.get("text")
        if isinstance(text, str):
            text = text.strip()
            if not text or _is_encrypted_reasoning_text(text):
                continue
            part["text"] = text

        sanitized.append(part)

    return sanitized


def _reasoning_item_has_visible_text(item: dict[str, Any]) -> bool:
    for key in ("summary", "content"):
        for part in item.get(key) or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip() and not _is_encrypted_reasoning_text(
                text
            ):
                return True
    return False


def _clone_output_item(item: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(item)

    if isinstance(item.get("attributes"), dict):
        cloned["attributes"] = dict(item["attributes"])

    for key in ("content", "summary", "output"):
        if isinstance(item.get(key), list):
            cloned[key] = [
                dict(part) if isinstance(part, dict) else part for part in item[key]
            ]

    return cloned


def _finalize_timed_item(
    item: dict[str, Any],
    ended_at: Optional[float] = None,
    mark_completed: bool = True,
) -> None:
    end_ts = _to_timestamp(ended_at) or _to_timestamp(item.get("ended_at")) or time.time()
    start_ts = _to_timestamp(item.get("started_at")) or end_ts
    if start_ts > end_ts:
        start_ts = end_ts

    item["started_at"] = start_ts
    item["ended_at"] = end_ts
    item["duration"] = int(max(0.0, end_ts - start_ts))
    if mark_completed:
        item["status"] = "completed"


def _normalize_reasoning_item(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("type") != "reasoning":
        return item

    encrypted_marker = False

    if isinstance(item.get("encrypted_content"), str):
        encrypted_marker = True
    item.pop("encrypted_content", None)

    summary = _sanitize_reasoning_parts(item.get("summary"))
    if summary:
        item["summary"] = summary
    else:
        item.pop("summary", None)

    content_parts = _sanitize_reasoning_parts(item.get("content"))
    if content_parts:
        item["content"] = content_parts
    else:
        item.pop("content", None)

    if encrypted_marker and not _reasoning_item_has_visible_text(item):
        item["_encrypted_reasoning"] = True

    started_at = _to_timestamp(item.get("started_at"))
    ended_at = _to_timestamp(item.get("ended_at"))
    status = str(item.get("status") or "").lower()

    if started_at is None:
        started_at = ended_at if ended_at is not None else time.time()
        item["started_at"] = started_at

    if status == "completed":
        _finalize_timed_item(item, ended_at=ended_at, mark_completed=True)

    return item


def _merge_reasoning_items(
    base_item: dict[str, Any], incoming_item: dict[str, Any]
) -> dict[str, Any]:
    merged = _normalize_reasoning_item(_clone_output_item(base_item))
    incoming = _normalize_reasoning_item(_clone_output_item(incoming_item))

    merged_content = list(merged.get("content") or [])
    incoming_content = incoming.get("content") or []
    if isinstance(incoming_content, list):
        merged_content.extend(incoming_content)
    merged["content"] = merged_content

    merged_summary = list(merged.get("summary") or [])
    incoming_summary = incoming.get("summary") or []
    if isinstance(incoming_summary, list):
        merged_summary.extend(incoming_summary)
    merged["summary"] = merged_summary

    base_started = _to_timestamp(merged.get("started_at"))
    incoming_started = _to_timestamp(incoming.get("started_at"))
    if base_started is None:
        base_started = incoming_started
    elif incoming_started is not None:
        base_started = min(base_started, incoming_started)
    if base_started is not None:
        merged["started_at"] = base_started

    base_status = str(merged.get("status") or "in_progress").lower()
    incoming_status = str(incoming.get("status") or "in_progress").lower()

    if "in_progress" in (base_status, incoming_status):
        merged["status"] = "in_progress"
        merged.pop("ended_at", None)
        merged.pop("duration", None)
    else:
        ended_candidates = [
            ts
            for ts in (
                _to_timestamp(merged.get("ended_at")),
                _to_timestamp(incoming.get("ended_at")),
            )
            if ts is not None
        ]
        merged_end = max(ended_candidates) if ended_candidates else base_started
        _finalize_timed_item(merged, ended_at=merged_end, mark_completed=True)

    return merged


def normalize_reasoning_output_items(output: Any) -> Any:
    if not isinstance(output, list):
        return output

    normalized: list[Any] = []
    for raw_item in output:
        if not isinstance(raw_item, dict):
            normalized.append(raw_item)
            continue

        item = _clone_output_item(raw_item)
        if item.get("type") != "reasoning":
            normalized.append(item)
            continue

        item = _normalize_reasoning_item(item)
        is_encrypted_reasoning = bool(item.pop("_encrypted_reasoning", False))
        item_status = str(item.get("status") or "").lower()

        if not _reasoning_item_has_visible_text(item):
            if is_encrypted_reasoning or item_status == "completed":
                continue

        if (
            normalized
            and isinstance(normalized[-1], dict)
            and normalized[-1].get("type") == "reasoning"
        ):
            normalized[-1] = _merge_reasoning_items(normalized[-1], item)
        else:
            normalized.append(item)

    return normalized


def deep_merge(target, source):
    """
    Merge source into target recursively (returning new structure).
    - Dicts: Recursive merge.
    - Strings: Concatenation.
    - Others: Overwrite.
    """
    if isinstance(target, dict) and isinstance(source, dict):
        new_target = target.copy()
        for k, v in source.items():
            if k in new_target:
                new_target[k] = deep_merge(new_target[k], v)
            else:
                new_target[k] = v
        return new_target
    elif isinstance(target, str) and isinstance(source, str):
        return target + source
    else:
        return source


def handle_responses_streaming_event(
    data: dict,
    current_output: list,
) -> tuple[list, dict | None]:
    """
    Handle Responses API streaming events in a pure functional way.

    Args:
        data: The event data
        current_output: List of output items (treated as immutable)

    Returns:
        tuple[list, dict | None]: (new_output, metadata)
        - new_output: The updated output list.
        - metadata: Metadata to emit (e.g. usage), {} if update occurred, None if skip.
    """
    # Default: no change
    # Note: treating current_output as immutable, but avoiding full deepcopy for perf.
    # We will shallow copy only if we need to modify the list structure or items.

    event_type = data.get("type", "")

    if event_type == "response.output_item.added":
        item = data.get("item", {})
        if item:
            if isinstance(item, dict) and item.get("type") == "reasoning":
                item = _normalize_reasoning_item(_clone_output_item(item))
            new_output = list(current_output)
            new_output.append(item)
            return new_output, None
        return current_output, None

    elif event_type == "response.content_part.added":
        part = data.get("part", {})
        output_index = data.get("output_index", len(current_output) - 1)

        if current_output and 0 <= output_index < len(current_output):
            new_output = list(current_output)
            # Copy the item to mutate it
            item = new_output[output_index].copy()
            new_output[output_index] = item

            if "content" not in item:
                item["content"] = []
            else:
                # Copy content list
                item["content"] = list(item["content"])

            if item.get("type") == "reasoning":
                # Reasoning items should not have content parts
                pass
            else:
                item["content"].append(part)
            return new_output, None
        return current_output, None

    elif event_type == "response.reasoning_summary_part.added":
        part = data.get("part", {})
        output_index = data.get("output_index", len(current_output) - 1)

        if current_output and 0 <= output_index < len(current_output):
            new_output = list(current_output)
            item = new_output[output_index].copy()
            new_output[output_index] = item

            if "summary" not in item:
                item["summary"] = []
            else:
                item["summary"] = list(item["summary"])

            item["summary"].append(part)
            return new_output, None
        return current_output, None

    elif event_type.startswith("response.") and event_type.endswith(".delta"):
        # Generic Delta Handling
        parts = event_type.split(".")
        if len(parts) >= 3:
            delta_type = parts[1]
            delta = data.get("delta", "")

            output_index = data.get("output_index", len(current_output) - 1)

            if current_output and 0 <= output_index < len(current_output):
                new_output = list(current_output)
                item = new_output[output_index].copy()
                new_output[output_index] = item
                item_type = item.get("type", "")

                # Determine target field and object based on delta_type and item_type
                if delta_type == "function_call_arguments":
                    key = "arguments"
                    if item_type == "function_call":
                        # Function call args are usually strings
                        item[key] = item.get(key, "") + str(delta)
                else:
                    # Generic handling, refined by item type below
                    pass

                    if item_type == "message":
                        # Message items: "text"/"output_text" -> "text"
                        # "reasoning_text" -> Skipped (should use reasoning item)
                        if delta_type in ["text", "output_text"]:
                            key = "text"
                        elif delta_type in ["reasoning_text", "reasoning_summary_text"]:
                            # Skip reasoning updates for message items
                            return new_output, None
                        else:
                            key = delta_type

                        content_index = data.get("content_index", 0)
                        if "content" not in item:
                            item["content"] = []
                        else:
                            item["content"] = list(item["content"])
                        content_list = item["content"]

                        while len(content_list) <= content_index:
                            content_list.append({"type": "output_text", "text": ""})

                        # Copy the part to mutate it
                        part = content_list[content_index].copy()
                        content_list[content_index] = part

                        current_val = part.get(key)
                        if current_val is None:
                            # Initialize based on delta type
                            current_val = {} if isinstance(delta, dict) else ""

                        part[key] = deep_merge(current_val, delta)

                    elif item_type == "reasoning":
                        # Reasoning items: "reasoning_text"/"reasoning_summary_text" -> "text"
                        # "text"/"output_text" -> Skipped (should use message item)
                        if delta_type == "reasoning_summary_text":
                            # Summary updates -> item['summary']
                            key = "text"
                            summary_index = data.get("summary_index", 0)
                            if "summary" not in item:
                                item["summary"] = []
                            else:
                                item["summary"] = list(item["summary"])
                            summary_list = item["summary"]

                            while len(summary_list) <= summary_index:
                                summary_list.append(
                                    {"type": "summary_text", "text": ""}
                                )

                            part = summary_list[summary_index].copy()
                            summary_list[summary_index] = part

                            target_val = part.get(key, "")
                            part[key] = deep_merge(target_val, delta)

                        elif delta_type == "reasoning_text":
                            # Reasoning body updates -> item['content']
                            key = "text"
                            content_index = data.get("content_index", 0)
                            if "content" not in item:
                                item["content"] = []
                            else:
                                item["content"] = list(item["content"])
                            content_list = item["content"]

                            while len(content_list) <= content_index:
                                # Keep OR-compatible output part typing.
                                content_list.append({"type": "output_text", "text": ""})

                            part = content_list[content_index].copy()
                            content_list[content_index] = part

                            target_val = part.get(key, "")
                            part[key] = deep_merge(target_val, delta)

                        elif delta_type in ["text", "output_text"]:
                            return new_output, None
                        else:
                            # Fallback just in case other deltas target reasoning?
                            pass

                    else:
                        # Fallback for other item types
                        if delta_type in ["text", "output_text"]:
                            key = "text"
                        else:
                            key = delta_type

                        current_val = item.get(key)
                        if current_val is None:
                            current_val = {} if isinstance(delta, dict) else ""
                        item[key] = deep_merge(current_val, delta)

            return new_output, None

    elif event_type.startswith("response.") and event_type.endswith(".done"):
        # Delta Events: response.content_part.done, response.text.done, etc.
        parts = event_type.split(".")
        if len(parts) >= 3:
            type_name = parts[1]

            # 1. Handle specific Delta "done" signals
            if type_name == "content_part":
                # "Signaling that no further changes will occur to a content part"
                # If payloads contains the full part, we could update it.
                # Usually purely signaling in standard implementation, but we check payload.
                part = data.get("part")
                output_index = data.get("output_index", len(current_output) - 1)

                if part and current_output and 0 <= output_index < len(current_output):
                    new_output = list(current_output)
                    item = new_output[output_index].copy()
                    new_output[output_index] = item

                    if "content" in item:
                        item["content"] = list(item["content"])
                        content_index = data.get(
                            "content_index", len(item["content"]) - 1
                        )
                        if 0 <= content_index < len(item["content"]):
                            item["content"][content_index] = part
                            return new_output, {}
                return current_output, None

            elif type_name == "reasoning_summary_part":
                part = data.get("part")
                output_index = data.get("output_index", len(current_output) - 1)

                if part and current_output and 0 <= output_index < len(current_output):
                    new_output = list(current_output)
                    item = new_output[output_index].copy()
                    new_output[output_index] = item

                    if "summary" in item:
                        item["summary"] = list(item["summary"])
                        summary_index = data.get(
                            "summary_index", len(item["summary"]) - 1
                        )
                        if 0 <= summary_index < len(item["summary"]):
                            item["summary"][summary_index] = part
                            return new_output, {}
                return current_output, None

            # 2. Skip Output Item done (handled specifically below)
            if type_name == "output_item":
                pass

            # 3. Generic Field Done (text.done, audio.done)
            elif type_name not in ["completed", "failed"]:
                output_index = data.get("output_index", len(current_output) - 1)
                if current_output and 0 <= output_index < len(current_output):

                    key = (
                        "text"
                        if type_name
                        in [
                            "text",
                            "output_text",
                            "reasoning_text",
                            "reasoning_summary_text",
                        ]
                        else type_name
                    )
                    if type_name == "function_call_arguments":
                        key = "arguments"

                    if key in data:
                        final_value = data[key]
                        new_output = list(current_output)
                        item = new_output[output_index].copy()
                        new_output[output_index] = item
                        item_type = item.get("type", "")

                        if type_name == "function_call_arguments":
                            if item_type == "function_call":
                                item["arguments"] = final_value
                        elif item_type == "message":
                            content_index = data.get("content_index", 0)
                            if "content" in item:
                                item["content"] = list(item["content"])
                                if len(item["content"]) > content_index:
                                    part = item["content"][content_index].copy()
                                    item["content"][content_index] = part
                                    part[key] = final_value
                        elif item_type == "reasoning":
                            item["status"] = "completed"
                        else:
                            item[key] = final_value

                        return new_output, {}

        return current_output, None

    elif event_type == "response.output_item.done":
        # Delta Event: Output item complete
        item = data.get("item")
        output_index = data.get("output_index", len(current_output) - 1)

        new_output = list(current_output)
        if isinstance(item, dict) and item.get("type") == "reasoning":
            item = _normalize_reasoning_item(_clone_output_item(item))
        if item and 0 <= output_index < len(current_output):
            new_output[output_index] = merge_output_item_preserve_content(
                new_output[output_index], item
            )
        elif item:
            new_output.append(item)
        return new_output, {}

    elif event_type == "response.completed":
        # State Machine Event: Completed
        response_data = data.get("response", {})
        final_output = response_data.get("output")

        if final_output is not None:
            new_output = merge_final_output_preserve_content(current_output, final_output)
        else:
            new_output = current_output

        # Ensure reasoning items are marked as completed in the final output
        if new_output:
            for item in new_output:
                if (
                    item.get("type") == "reasoning"
                    and item.get("status") != "completed"
                ):
                    item["status"] = "completed"

            # Normalize timing fields and fold fragmented reasoning chunks.
            new_output = normalize_reasoning_output_items(new_output)

        return new_output, {"usage": response_data.get("usage"), "done": True}

    elif event_type == "response.in_progress":
        # State Machine Event: In Progress
        # We could extract metadata if needed, but for now just acknowledge iteration
        return current_output, None

    elif event_type == "response.failed":
        # State Machine Event: Failed
        error = data.get("response", {}).get("error", {})
        return current_output, {"error": error}

    else:
        return current_output, None


def apply_source_context_to_messages(
    request: Request,
    messages: list,
    sources: list,
    user_message: str,
    include_content: bool = True,
) -> list:
    """
    Build source context from citation sources and apply to messages.
    Uses RAG template to format context for model consumption.

    When include_content is False, emit <source> tags with id/name but no
    document body — useful when the content is already present elsewhere
    (e.g. in a tool result message) and only citation markers are needed.
    """
    if not sources or not user_message:
        return messages

    context_string = ""
    citation_idx = {}

    for source in sources:
        for doc, meta in zip(source.get("document", []), source.get("metadata", [])):
            src_id = meta.get("source") or source.get("source", {}).get("id") or "N/A"
            if src_id not in citation_idx:
                citation_idx[src_id] = len(citation_idx) + 1
            src_name = source.get("source", {}).get("name")
            body = doc if include_content else ""
            context_string += (
                f'<source id="{citation_idx[src_id]}"'
                + (f' name="{src_name}"' if src_name else "")
                + f">{body}</source>\n"
            )

    context_string = context_string.strip()
    if not context_string:
        return messages

    if RAG_SYSTEM_CONTEXT:
        return add_or_update_system_message(
            rag_template(
                request.app.state.config.RAG_TEMPLATE, context_string, user_message
            ),
            messages,
            append=True,
        )
    else:
        return add_or_update_user_message(
            rag_template(
                request.app.state.config.RAG_TEMPLATE, context_string, user_message
            ),
            messages,
            append=False,
        )


def process_tool_result(
    request,
    tool_function_name,
    tool_result,
    tool_type,
    direct_tool=False,
    metadata=None,
    user=None,
):
    tool_result_embeds = []

    if isinstance(tool_result, HTMLResponse):
        content_disposition = tool_result.headers.get("Content-Disposition", "")
        if "inline" in content_disposition:
            content = tool_result.body.decode("utf-8", "replace")
            tool_result_embeds.append(content)

            if 200 <= tool_result.status_code < 300:
                tool_result = {
                    "status": "success",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                }
            elif 400 <= tool_result.status_code < 500:
                tool_result = {
                    "status": "error",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Client error {tool_result.status_code} from embedded UI result.",
                }
            elif 500 <= tool_result.status_code < 600:
                tool_result = {
                    "status": "error",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Server error {tool_result.status_code} from embedded UI result.",
                }
            else:
                tool_result = {
                    "status": "error",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Unexpected status code {tool_result.status_code} from embedded UI result.",
                }
        else:
            tool_result = tool_result.body.decode("utf-8", "replace")

    elif (tool_type in ("external", "action") and isinstance(tool_result, tuple)) or (
        direct_tool and isinstance(tool_result, list) and len(tool_result) == 2
    ):
        tool_result, tool_response_headers = tool_result

        try:
            if not isinstance(tool_response_headers, dict):
                tool_response_headers = dict(tool_response_headers)
        except Exception as e:
            tool_response_headers = {}
            log.debug(e)

        if tool_response_headers and isinstance(tool_response_headers, dict):
            content_disposition = tool_response_headers.get(
                "Content-Disposition",
                tool_response_headers.get("content-disposition", ""),
            )

            if "inline" in content_disposition:
                content_type = tool_response_headers.get(
                    "Content-Type",
                    tool_response_headers.get("content-type", ""),
                )
                location = tool_response_headers.get(
                    "Location",
                    tool_response_headers.get("location", ""),
                )

                if "text/html" in content_type:
                    # Display as iframe embed
                    tool_result_embeds.append(tool_result)
                    tool_result = {
                        "status": "success",
                        "code": "ui_component",
                        "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                    }
                elif location:
                    tool_result_embeds.append(location)
                    tool_result = {
                        "status": "success",
                        "code": "ui_component",
                        "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                    }

    tool_result_files = []

    # Defensive normalization: some tool integrations may accidentally return tuples.
    # Keep downstream handling JSON-serializable and string-safe.
    if isinstance(tool_result, tuple):
        tool_result = list(tool_result)

    if isinstance(tool_result, list):
        if tool_type == "mcp":  # MCP
            tool_response = []
            for item in tool_result:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text = item.get("text", "")
                        if isinstance(text, str):
                            try:
                                text = json.loads(text)
                            except json.JSONDecodeError:
                                pass
                        tool_response.append(text)
                    elif item.get("type") in ["image", "audio"]:
                        file_url = get_file_url_from_base64(
                            request,
                            f"data:{item.get('mimeType')};base64,{item.get('data', item.get('blob', ''))}",
                            {
                                "chat_id": metadata.get("chat_id", None),
                                "message_id": metadata.get("message_id", None),
                                "session_id": metadata.get("session_id", None),
                                "result": item,
                            },
                            user,
                        )

                        tool_result_files.append(
                            {
                                "type": item.get("type", "data"),
                                "url": file_url,
                            }
                        )
            tool_result = tool_response[0] if len(tool_response) == 1 else tool_response
        else:  # OpenAPI
            for item in tool_result:
                if isinstance(item, str) and item.startswith("data:"):
                    tool_result_files.append(
                        {
                            "type": "data",
                            "content": item,
                        }
                    )
                    tool_result.remove(item)

    if isinstance(tool_result, list):
        tool_result = {"results": tool_result}

    if isinstance(tool_result, (dict, list, tuple)):
        tool_result = json.dumps(tool_result, indent=2, ensure_ascii=False)
    elif tool_result is not None and not isinstance(tool_result, str):
        tool_result = str(tool_result)

    return tool_result, tool_result_files, tool_result_embeds


async def chat_completion_tools_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel, models, tools
) -> tuple[dict, dict]:
    async def get_content_from_response(response) -> Optional[str]:
        content = None
        if hasattr(response, "body_iterator"):
            async for chunk in response.body_iterator:
                data = json.loads(chunk.decode("utf-8", "replace"))
                content = data["choices"][0]["message"]["content"]

            # Cleanup any remaining background tasks if necessary
            if response.background is not None:
                await response.background()
        else:
            content = response["choices"][0]["message"]["content"]
        return content

    def get_tools_function_calling_payload(messages, task_model_id, content):
        user_message = get_last_user_message(messages)

        if user_message and messages and messages[-1]["role"] == "user":
            # Remove the last user message to avoid duplication
            messages = messages[:-1]

        recent_messages = messages[-4:] if len(messages) > 4 else messages
        chat_history = "\n".join(
            f"{message['role'].upper()}: \"\"\"{get_content_from_message(message)}\"\"\""
            for message in recent_messages
        )

        prompt = (
            f"History:\n{chat_history}\nQuery: {user_message}"
            if chat_history
            else f"Query: {user_message}"
        )

        return {
            "model": task_model_id,
            "messages": [
                {"role": "system", "content": content},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "metadata": {"task": str(TASKS.FUNCTION_CALLING)},
        }

    event_caller = extra_params["__event_call__"]
    event_emitter = extra_params["__event_emitter__"]
    metadata = extra_params["__metadata__"]

    task_model_id = get_task_model_id(
        body["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    skip_files = False
    sources = []

    specs = [tool["spec"] for tool in tools.values()]
    tools_specs = json.dumps(specs, ensure_ascii=False)

    if request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE != "":
        template = request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE
    else:
        template = DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE

    tools_function_calling_prompt = tools_function_calling_generation_template(
        template, tools_specs
    )
    payload = get_tools_function_calling_payload(
        body["messages"], task_model_id, tools_function_calling_prompt
    )

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        log.debug(f"{response=}")
        content = await get_content_from_response(response)
        log.debug(f"{content=}")

        if not content:
            return body, {}

        try:
            content = content[content.find("{") : content.rfind("}") + 1]
            if not content:
                raise Exception("No JSON object found in the response")

            result = json.loads(content)

            async def tool_call_handler(tool_call):
                nonlocal skip_files

                log.debug(f"{tool_call=}")

                tool_function_name = tool_call.get("name", None)
                if tool_function_name not in tools:
                    return body, {}

                tool_function_params = tool_call.get("parameters", {})

                tool = None
                tool_type = ""
                direct_tool = False

                try:
                    tool = tools[tool_function_name]
                    tool_type = tool.get("type", "")
                    direct_tool = tool.get("direct", False)

                    spec = tool.get("spec", {})
                    allowed_params = (
                        spec.get("parameters", {}).get("properties", {}).keys()
                    )
                    tool_function_params = {
                        k: v
                        for k, v in tool_function_params.items()
                        if k in allowed_params
                    }

                    if tool.get("direct", False):
                        tool_result = await event_caller(
                            {
                                "type": "execute:tool",
                                "data": {
                                    "id": str(uuid4()),
                                    "name": tool_function_name,
                                    "params": tool_function_params,
                                    "server": tool.get("server", {}),
                                    "session_id": metadata.get("session_id", None),
                                },
                            }
                        )
                    else:
                        tool_function = tool["callable"]
                        tool_result = await tool_function(**tool_function_params)

                except Exception as e:
                    tool_result = str(e)

                tool_result, tool_result_files, tool_result_embeds = (
                    process_tool_result(
                        request,
                        tool_function_name,
                        tool_result,
                        tool_type,
                        direct_tool,
                        metadata,
                        user,
                    )
                )

                if event_emitter:
                    if tool_result_files:
                        await event_emitter(
                            {
                                "type": "files",
                                "data": {
                                    "files": tool_result_files,
                                },
                            }
                        )

                    if tool_result_embeds:
                        await event_emitter(
                            {
                                "type": "embeds",
                                "data": {
                                    "embeds": tool_result_embeds,
                                },
                            }
                        )

                if tool_result:
                    tool = tools[tool_function_name]
                    tool_id = tool.get("tool_id", "")

                    tool_name = (
                        f"{tool_id}/{tool_function_name}"
                        if tool_id
                        else f"{tool_function_name}"
                    )

                    # Citation is enabled for this tool
                    sources.append(
                        {
                            "source": {
                                "name": (f"{tool_name}"),
                            },
                            "document": [str(tool_result)],
                            "metadata": [
                                {
                                    "source": (f"{tool_name}"),
                                    "parameters": tool_function_params,
                                }
                            ],
                            "tool_result": True,
                        }
                    )

                    if (
                        tools[tool_function_name]
                        .get("metadata", {})
                        .get("file_handler", False)
                    ):
                        skip_files = True

            # check if "tool_calls" in result
            if result.get("tool_calls"):
                for tool_call in result.get("tool_calls"):
                    await tool_call_handler(tool_call)
            else:
                await tool_call_handler(result)

        except Exception as e:
            log.debug(f"Error: {e}")
            content = None
    except Exception as e:
        log.debug(f"Error: {e}")
        content = None

    log.debug(f"tool_contexts: {sources}")

    if skip_files and "files" in body.get("metadata", {}):
        del body["metadata"]["files"]

    return body, {"sources": sources}


async def chat_memory_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    event_emitter = extra_params.get("__event_emitter__")

    async def emit_memory_status(status_data: dict) -> None:
        if not event_emitter:
            return

        try:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "memory_retrieval",
                        **status_data,
                    },
                }
            )
        except Exception as e:
            log.debug(f"Failed to emit memory status: {e}")

    def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
        return max(minimum, min(maximum, value))

    def safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def normalize_text(value: Optional[str]) -> str:
        return re.sub(r"\s+", " ", (value or "")).strip()

    def tokenize_for_overlap(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.lower()))

    def contains_any_cue(value: str, cues: tuple[str, ...]) -> bool:
        return any(cue in value for cue in cues)

    def estimate_memory_intent_score(last_user_message: str, messages: list[dict]) -> float:
        normalized = normalize_text(last_user_message)
        if not normalized:
            return 0.0

        text = normalized.lower()
        explicit = 1.0 if contains_any_cue(text, MEMORY_EXPLICIT_CUES) else 0.0
        continuity = 1.0 if contains_any_cue(text, MEMORY_CONTINUITY_CUES) else 0.0
        personal = 1.0 if contains_any_cue(text, MEMORY_PERSONAL_CUES) else 0.0

        token_count = len(tokenize_for_overlap(normalized))
        user_turn_count = len([msg for msg in messages if msg.get("role") == "user"])
        short_follow_up = 1.0 if user_turn_count >= 2 and token_count <= 14 else 0.0
        question_boost = 1.0 if normalized.endswith("?") or normalized.endswith("？") else 0.0

        score = (
            0.55 * explicit
            + 0.20 * continuity
            + 0.20 * personal
            + 0.05 * question_boost
            + 0.10 * short_follow_up
        )
        return clamp(score)

    def estimate_session_continuity_score(messages: list[dict]) -> float:
        user_messages = [
            normalize_text(get_content_from_message(msg))
            for msg in messages
            if msg.get("role") == "user"
        ]
        user_messages = [msg for msg in user_messages if msg]
        if len(user_messages) < 2:
            return 0.0

        current = user_messages[-1]
        previous = user_messages[-2]
        current_tokens = tokenize_for_overlap(current)
        previous_tokens = tokenize_for_overlap(previous)

        overlap = 0.0
        union = current_tokens | previous_tokens
        if union:
            overlap = len(current_tokens & previous_tokens) / len(union)

        short_follow_up = 1.0 if len(current_tokens) <= 14 else 0.0
        pronoun_reference = 1.0 if re.match(r"^(this|that|it|these|those)\b", current.lower()) else 0.0
        pronoun_reference = max(
            pronoun_reference,
            1.0 if current.startswith(("这个", "那个", "它", "这", "那")) else 0.0,
        )

        score = 0.20 + (0.55 * overlap) + (0.15 * short_follow_up) + (0.10 * pronoun_reference)
        return clamp(score)

    def is_stateless_query(last_user_message: str) -> bool:
        normalized = normalize_text(last_user_message)
        if not normalized:
            return False

        text = normalized.lower()
        if (
            contains_any_cue(text, MEMORY_EXPLICIT_CUES)
            or contains_any_cue(text, MEMORY_CONTINUITY_CUES)
            or contains_any_cue(text, MEMORY_PERSONAL_CUES)
        ):
            return False

        return any(text.startswith(prefix) for prefix in STATELESS_QUERY_PREFIXES)

    def normalize_similarity(raw_similarity: Any) -> float:
        score = safe_float(raw_similarity, 0.0)
        if score <= 0:
            return 0.0
        if score <= 1:
            return score
        if score <= 2:
            return score / 2.0
        return 1.0

    def recency_score(timestamp: Optional[int]) -> float:
        if not timestamp:
            return 0.5

        age_days = max((time.time() - timestamp) / 86400, 0)
        return clamp(1.0 / (1.0 + (age_days / 30.0)))

    def extract_memory_candidates(results: Any) -> list[dict]:
        if not results or not hasattr(results, "documents") or not results.documents:
            return []

        documents = results.documents[0] if results.documents else []
        metadatas = results.metadatas[0] if getattr(results, "metadatas", None) else []
        ids = results.ids[0] if getattr(results, "ids", None) else []
        distances = (
            results.distances[0] if getattr(results, "distances", None) else []
        )

        candidates = []
        seen_keys = set()

        for index, raw_doc in enumerate(documents):
            content = normalize_text(raw_doc)
            if not content:
                continue

            dedupe_key = content.lower()
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            metadata = (
                metadatas[index]
                if index < len(metadatas) and isinstance(metadatas[index], dict)
                else {}
            )
            similarity = normalize_similarity(
                distances[index] if index < len(distances) else None
            )
            created_at = safe_int(metadata.get("created_at"), 0)
            updated_at = safe_int(metadata.get("updated_at"), 0)
            freshness = recency_score(updated_at or created_at)
            rank_score = clamp((0.80 * similarity) + (0.20 * freshness))

            candidates.append(
                {
                    "id": ids[index] if index < len(ids) else None,
                    "content": content,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "similarity": similarity,
                    "freshness": freshness,
                    "rank_score": rank_score,
                }
            )

        candidates.sort(key=lambda item: item["rank_score"], reverse=True)
        return candidates

    def compute_relevance_score(
        candidates: list[dict], min_top1_similarity: float
    ) -> float:
        if not candidates:
            return 0.0

        top1_similarity = candidates[0]["similarity"]
        top_candidates = candidates[:3]

        avg_similarity = sum(c["similarity"] for c in top_candidates) / len(top_candidates)
        avg_rank_score = sum(c["rank_score"] for c in top_candidates) / len(top_candidates)

        score = (0.55 * top1_similarity) + (0.25 * avg_similarity) + (0.20 * avg_rank_score)
        if top1_similarity < min_top1_similarity:
            score = min(score, 0.25 + (0.35 * top1_similarity))

        return clamp(score)

    def build_memory_context(
        candidates: list[dict], level: str, top_n: int, max_context_chars: int
    ) -> str:
        if not candidates:
            return ""

        selected_candidates = candidates[: max(1, top_n)]
        date_fallback = "Unknown Date"
        lines = []

        for idx, candidate in enumerate(selected_candidates):
            timestamp = candidate.get("updated_at") or candidate.get("created_at")
            date_label = (
                time.strftime("%Y-%m-%d", time.localtime(timestamp))
                if timestamp
                else date_fallback
            )
            lines.append(f"{idx + 1}. [{date_label}] {candidate['content']}")

        if not lines:
            return ""

        if level == "strong":
            header = "User Memory Context (high confidence):"
            guidance = (
                "Use these facts when they materially improve personalization or continuity."
            )
        else:
            header = "User Memory Context (possible match):"
            guidance = (
                "Use these only if directly relevant to the current request."
            )

        context = f"{header}\n{guidance}\n" + "\n".join(lines) + "\n"
        if len(context) > max_context_chars:
            context = context[: max_context_chars - 3].rstrip() + "..."
        return context

    def resolve_mode_config(config: Any) -> tuple[str, dict]:
        raw_mode = str(getattr(config, "MEMORY_RETRIEVAL_MODE", "balanced") or "balanced")
        mode = raw_mode.lower()

        presets = {
            "aggressive": {
                "bias": 0.08,
                "soft_delta": -0.05,
                "strong_delta": -0.05,
                "query_k_delta": 2,
            },
            "balanced": {
                "bias": 0.0,
                "soft_delta": 0.0,
                "strong_delta": 0.0,
                "query_k_delta": 0,
            },
            "conservative": {
                "bias": -0.06,
                "soft_delta": 0.05,
                "strong_delta": 0.05,
                "query_k_delta": -2,
            },
        }

        if mode not in presets:
            mode = "balanced"

        return mode, presets[mode]

    query = normalize_text(get_last_user_message(form_data.get("messages", [])))
    if not query:
        return form_data

    await emit_memory_status(
        {
            "description": "Searching memories",
            "done": False,
        }
    )

    config = request.app.state.config
    mode, mode_preset = resolve_mode_config(config)

    query_k = max(1, safe_int(getattr(config, "MEMORY_RETRIEVAL_QUERY_K", 8), 8))
    query_k = max(1, query_k + mode_preset["query_k_delta"])

    strong_threshold_base = clamp(
        safe_float(getattr(config, "MEMORY_NEED_STRONG_THRESHOLD", 0.70), 0.70)
    )
    soft_threshold_base = clamp(
        safe_float(getattr(config, "MEMORY_NEED_SOFT_THRESHOLD", 0.45), 0.45)
    )
    soft_threshold = clamp(soft_threshold_base + mode_preset["soft_delta"])
    strong_threshold = clamp(strong_threshold_base + mode_preset["strong_delta"])
    strong_threshold = max(strong_threshold, min(1.0, soft_threshold + 0.01))

    min_top1_similarity = clamp(
        safe_float(getattr(config, "MEMORY_MIN_TOP1_SIMILARITY", 0.35), 0.35)
    )
    strong_top_n = max(
        1, safe_int(getattr(config, "MEMORY_INJECTION_STRONG_TOP_N", 2), 2)
    )
    soft_top_n = max(
        1, safe_int(getattr(config, "MEMORY_INJECTION_SOFT_TOP_N", 1), 1)
    )
    max_context_chars = max(
        300, safe_int(getattr(config, "MEMORY_MAX_CONTEXT_CHARS", 1400), 1400)
    )

    intent_weight = max(
        0.0, safe_float(getattr(config, "MEMORY_NEED_INTENT_WEIGHT", 0.45), 0.45)
    )
    relevance_weight = max(
        0.0, safe_float(getattr(config, "MEMORY_NEED_RELEVANCE_WEIGHT", 0.45), 0.45)
    )
    continuity_weight = max(
        0.0, safe_float(getattr(config, "MEMORY_NEED_CONTINUITY_WEIGHT", 0.10), 0.10)
    )
    total_weight = intent_weight + relevance_weight + continuity_weight
    if total_weight <= 0:
        intent_weight, relevance_weight, continuity_weight = 0.45, 0.45, 0.10
    else:
        intent_weight /= total_weight
        relevance_weight /= total_weight
        continuity_weight /= total_weight

    stateless_penalty = clamp(
        safe_float(getattr(config, "MEMORY_STATELESS_PENALTY", 0.15), 0.15)
    )

    intent_score = estimate_memory_intent_score(query, form_data.get("messages", []))
    continuity_score = estimate_session_continuity_score(form_data.get("messages", []))

    memory_retrieval_error = False
    try:
        results = await query_memory(
            request,
            QueryMemoryForm(content=query, k=query_k),
            user,
        )
    except Exception as e:
        log.debug(e)
        results = None
        memory_retrieval_error = True

    candidates = extract_memory_candidates(results)
    relevance_score = compute_relevance_score(candidates, min_top1_similarity)

    penalty = stateless_penalty if is_stateless_query(query) else 0.0
    need_memory_score = clamp(
        (intent_weight * intent_score)
        + (relevance_weight * relevance_score)
        + (continuity_weight * continuity_score)
        + mode_preset["bias"]
        - penalty
    )

    memory_level = None
    memory_top_n = 0
    if candidates and relevance_score > 0:
        if need_memory_score >= strong_threshold:
            memory_level = "strong"
            memory_top_n = strong_top_n
        elif need_memory_score >= soft_threshold:
            memory_level = "soft"
            memory_top_n = soft_top_n

    user_context = ""
    if memory_level:
        user_context = build_memory_context(
            candidates, memory_level, memory_top_n, max_context_chars
        )

    if user_context:
        form_data["messages"] = add_or_update_system_message(
            user_context, form_data["messages"], append=True
        )

    retrieved_count = len(candidates)
    injected_count = min(memory_top_n, retrieved_count) if user_context else 0

    if memory_retrieval_error:
        memory_description = "An error occurred while retrieving memories"
    elif retrieved_count == 0:
        memory_description = "No matching memories found"
    elif injected_count > 0:
        memory_description = (
            f"Retrieved {retrieved_count} memories and injected {injected_count}"
        )
    else:
        memory_description = (
            f"Retrieved {retrieved_count} memories but did not inject context"
        )

    await emit_memory_status(
        {
            "description": memory_description,
            "done": True,
            "error": memory_retrieval_error,
            "count": retrieved_count,
            "injected_count": injected_count,
            "level": memory_level,
            "query_k": query_k,
            "need_score": round(need_memory_score, 4),
            "relevance_score": round(relevance_score, 4),
        }
    )

    log.debug(
        "memory_orchestrator: "
        f"mode={mode} intent={intent_score:.3f} continuity={continuity_score:.3f} "
        f"relevance={relevance_score:.3f} need={need_memory_score:.3f} "
        f"thresholds=(soft={soft_threshold:.3f},strong={strong_threshold:.3f}) "
        f"candidates={len(candidates)} injected={bool(user_context)} level={memory_level}"
    )

    return form_data


async def chat_web_search_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    event_emitter = extra_params["__event_emitter__"]
    await event_emitter(
        {
            "type": "status",
            "data": {
                "action": "web_search",
                "description": "Searching the web",
                "done": False,
            },
        }
    )

    messages = form_data["messages"]
    user_message = get_last_user_message(messages)

    queries = []
    try:
        res = await generate_queries(
            request,
            {
                "model": form_data["model"],
                "messages": messages,
                "prompt": user_message,
                "type": "web_search",
                "chat_id": extra_params.get("__chat_id__"),
            },
            user,
        )

        response = res["choices"][0]["message"]["content"]

        try:
            bracket_start = response.find("{")
            bracket_end = response.rfind("}") + 1

            if bracket_start == -1 or bracket_end == -1:
                raise Exception("No JSON object found in the response")

            response = response[bracket_start:bracket_end]
            queries = json.loads(response)
            queries = queries.get("queries", [])
        except Exception as e:
            queries = [response]

        if ENABLE_QUERIES_CACHE:
            request.state.cached_queries = queries

    except Exception as e:
        log.exception(e)
        queries = [user_message]

    # Check if generated queries are empty
    if len(queries) == 1 and queries[0].strip() == "":
        queries = [user_message]

    # Check if queries are not found
    if len(queries) == 0:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "No search query generated",
                    "done": True,
                },
            }
        )
        return form_data

    await event_emitter(
        {
            "type": "status",
            "data": {
                "action": "web_search_queries_generated",
                "queries": queries,
                "done": False,
            },
        }
    )

    try:
        results = await process_web_search(
            request,
            SearchForm(queries=queries),
            user=user,
        )

        if results:
            files = form_data.get("files", [])

            if results.get("collection_names"):
                for col_idx, collection_name in enumerate(
                    results.get("collection_names")
                ):
                    files.append(
                        {
                            "collection_name": collection_name,
                            "name": ", ".join(queries),
                            "type": "web_search",
                            "urls": results["filenames"],
                            "queries": queries,
                        }
                    )
            elif results.get("docs"):
                # Invoked when bypass embedding and retrieval is set to True
                docs = results["docs"]
                files.append(
                    {
                        "docs": docs,
                        "name": ", ".join(queries),
                        "type": "web_search",
                        "urls": results["filenames"],
                        "queries": queries,
                    }
                )

            form_data["files"] = files

            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": "Searched {{count}} sites",
                        "urls": results["filenames"],
                        "items": results.get("items", []),
                        "done": True,
                    },
                }
            )
        else:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": "No search results found",
                        "done": True,
                        "error": True,
                    },
                }
            )

    except Exception as e:
        log.exception(e)
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "An error occurred while searching the web",
                    "queries": queries,
                    "done": True,
                    "error": True,
                },
            }
        )

    return form_data


def get_images_from_messages(message_list):
    images = []

    for message in reversed(message_list):

        message_images = []
        for file in message.get("files", []):
            if _is_image_attachment(file):
                message_images.append(file.get("url"))

        if message_images:
            images.append(message_images)

    return images


def _normalize_attachment_content_type(value) -> str:
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, str)), None)
    if not isinstance(value, str):
        return ""

    normalized = value.strip().lower()
    if not normalized:
        return ""

    # Handle serialized list-like values such as ["image/jpeg"].
    if normalized.startswith("[") and normalized.endswith("]"):
        inner_items = re.findall(r"([a-z0-9.+-]+/[a-z0-9.+-]+)", normalized)
        if inner_items:
            return inner_items[0]

    mime_match = re.search(r"([a-z0-9.+-]+/[a-z0-9.+-]+)", normalized)
    if mime_match:
        return mime_match.group(1)

    return normalized.split(";")[0]


def _extract_attachment_content_type(item: dict) -> str:
    if not isinstance(item, dict):
        return ""

    candidates = [
        item.get("content_type"),
        item.get("mime_type"),
        item.get("mimeType"),
    ]

    meta = item.get("meta")
    if isinstance(meta, dict):
        candidates.extend(
            [
                meta.get("content_type"),
                meta.get("mime_type"),
                meta.get("mimeType"),
            ]
        )

    file_obj = item.get("file")
    if isinstance(file_obj, dict):
        candidates.extend(
            [
                file_obj.get("content_type"),
                file_obj.get("mime_type"),
                file_obj.get("mimeType"),
            ]
        )

    for candidate in candidates:
        normalized = _normalize_attachment_content_type(candidate)
        if normalized:
            return normalized

    return ""


def _extract_attachment_name(item: dict) -> str:
    if not isinstance(item, dict):
        return ""

    for key in ("name", "filename"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    meta = item.get("meta")
    if isinstance(meta, dict):
        for key in ("name", "filename"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    file_obj = item.get("file")
    if isinstance(file_obj, dict):
        for key in ("name", "filename"):
            value = file_obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _looks_like_image_path(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False

    stripped = value.strip().lower()
    if stripped.startswith("data:image/"):
        return True

    path = stripped.split("?", 1)[0].split("#", 1)[0]
    return path.endswith(
        (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".bmp",
            ".tif",
            ".tiff",
            ".heic",
            ".heif",
            ".avif",
            ".svg",
            ".ico",
        )
    )


def _get_image_urls_from_message_content(content: Any) -> set[str]:
    if not isinstance(content, list):
        return set()

    urls: set[str] = set()
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "image_url":
            continue
        image_url = part.get("image_url")
        if isinstance(image_url, dict):
            url = image_url.get("url")
        else:
            url = image_url
        if isinstance(url, str) and url.strip():
            urls.add(url.strip())

    return urls


def _is_image_attachment(item: dict) -> bool:
    if not isinstance(item, dict):
        return False

    if str(item.get("type", "")).lower() in {"image", "image_url", "input_image"}:
        return True

    content_type = _extract_attachment_content_type(item)
    if content_type.startswith("image/"):
        return True

    attachment_name = _extract_attachment_name(item)
    if _looks_like_image_path(attachment_name):
        return True

    url = item.get("url")
    if isinstance(url, str) and _looks_like_image_path(url):
        return True

    return False


def get_image_urls(delta_images, request, metadata, user) -> list[str]:
    if not isinstance(delta_images, list):
        return []

    image_urls = []
    for img in delta_images:
        if not isinstance(img, dict) or img.get("type") != "image_url":
            continue

        url = img.get("image_url", {}).get("url")
        if not url:
            continue

        if url.startswith("data:image/png;base64"):
            url = get_image_url_from_base64(request, url, metadata, user)

        image_urls.append(url)

    return image_urls


def add_file_context(messages: list, chat_id: str, user) -> list:
    """
    Add file URLs to messages for native function calling.
    """
    if not chat_id or chat_id.startswith("local:"):
        return messages

    chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
    if not chat:
        return messages

    history = chat.chat.get("history", {})
    stored_messages = get_message_list(
        history.get("messages", {}), history.get("currentId")
    )

    def format_file_tag(file):
        attrs = f'type="{file.get("type", "file")}" url="{file["url"]}"'
        if file.get("content_type"):
            attrs += f' content_type="{file["content_type"]}"'
        if file.get("name"):
            attrs += f' name="{file["name"]}"'
        return f"<file {attrs}/>"

    for message, stored_message in zip(messages, stored_messages):
        image_urls = _get_image_urls_from_message_content(message.get("content"))
        files_with_urls = [
            file
            for file in stored_message.get("files", [])
            if file.get("url")
            and not file.get("url").startswith("data:")
            and str(file.get("url")) not in image_urls
            and not _is_image_attachment(file)
        ]
        if not files_with_urls:
            continue

        file_tags = [format_file_tag(file) for file in files_with_urls]
        file_context = (
            "<attached_files>\n" + "\n".join(file_tags) + "\n</attached_files>\n\n"
        )

        content = message.get("content", "")
        if isinstance(content, (list, tuple)):
            content_blocks = list(content)
            if all(isinstance(block, dict) for block in content_blocks):
                message["content"] = [{"type": "text", "text": file_context}] + content_blocks
            else:
                message["content"] = file_context + "".join(str(block) for block in content_blocks)
        elif isinstance(content, str):
            message["content"] = file_context + content
        elif content is None:
            message["content"] = file_context
        else:
            message["content"] = file_context + str(content)

    return messages


async def chat_image_generation_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    metadata = extra_params.get("__metadata__", {})
    chat_id = metadata.get("chat_id", None)
    __event_emitter__ = extra_params.get("__event_emitter__", None)

    if not chat_id or not isinstance(chat_id, str) or not __event_emitter__:
        return form_data

    if chat_id.startswith("local:"):
        message_list = form_data.get("messages", [])
    else:
        chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
        await __event_emitter__(
            {
                "type": "status",
                "data": {"description": "Creating image", "done": False},
            }
        )

        messages_map = chat.chat.get("history", {}).get("messages", {})
        message_id = chat.chat.get("history", {}).get("currentId")
        message_list = get_message_list(messages_map, message_id)

    user_message = get_last_user_message(message_list)

    prompt = user_message
    message_images = get_images_from_messages(message_list)

    # Limit to first 2 sets of images
    # We may want to change this in the future to allow more images
    input_images = []
    for idx, images in enumerate(message_images):
        if idx >= 2:
            break
        for image in images:
            input_images.append(image)

    system_message_content = ""

    if len(input_images) > 0 and request.app.state.config.ENABLE_IMAGE_EDIT:
        # Edit image(s)
        try:
            images = await image_edits(
                request=request,
                form_data=EditImageForm(**{"prompt": prompt, "image": input_images}),
                metadata={
                    "chat_id": metadata.get("chat_id", None),
                    "message_id": metadata.get("message_id", None),
                },
                user=user,
            )

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Image created", "done": True},
                }
            )

            await __event_emitter__(
                {
                    "type": "files",
                    "data": {
                        "files": [
                            {
                                "type": "image",
                                "url": image["url"],
                            }
                            for image in images
                        ]
                    },
                }
            )

            system_message_content = "<context>The requested image has been edited and created and is now being shown to the user. Let them know that it has been generated.</context>"
        except Exception as e:
            log.debug(e)

            error_message = ""
            if isinstance(e, HTTPException):
                if e.detail and isinstance(e.detail, dict):
                    error_message = e.detail.get("message", str(e.detail))
                else:
                    error_message = str(e.detail)

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"An error occurred while generating an image",
                        "done": True,
                    },
                }
            )

            system_message_content = f"<context>Image generation was attempted but failed. The system is currently unable to generate the image. Tell the user that the following error occurred: {error_message}</context>"

    else:
        # Create image(s)
        if request.app.state.config.ENABLE_IMAGE_PROMPT_GENERATION:
            try:
                res = await generate_image_prompt(
                    request,
                    {
                        "model": form_data["model"],
                        "messages": form_data["messages"],
                        "chat_id": metadata.get("chat_id"),
                    },
                    user,
                )

                response = res["choices"][0]["message"]["content"]

                try:
                    bracket_start = response.find("{")
                    bracket_end = response.rfind("}") + 1

                    if bracket_start == -1 or bracket_end == -1:
                        raise Exception("No JSON object found in the response")

                    response = response[bracket_start:bracket_end]
                    response = json.loads(response)
                    prompt = response.get("prompt", [])
                except Exception as e:
                    prompt = user_message

            except Exception as e:
                log.exception(e)
                prompt = user_message

        try:
            images = await image_generations(
                request=request,
                form_data=CreateImageForm(**{"prompt": prompt}),
                metadata={
                    "chat_id": metadata.get("chat_id", None),
                    "message_id": metadata.get("message_id", None),
                },
                user=user,
            )

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Image created", "done": True},
                }
            )

            await __event_emitter__(
                {
                    "type": "files",
                    "data": {
                        "files": [
                            {
                                "type": "image",
                                "url": image["url"],
                            }
                            for image in images
                        ]
                    },
                }
            )

            system_message_content = "<context>The requested image has been created by the system successfully and is now being shown to the user. Let the user know that the image they requested has been generated and is now shown in the chat.</context>"
        except Exception as e:
            log.debug(e)

            error_message = ""
            if isinstance(e, HTTPException):
                if e.detail and isinstance(e.detail, dict):
                    error_message = e.detail.get("message", str(e.detail))
                else:
                    error_message = str(e.detail)

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"An error occurred while generating an image",
                        "done": True,
                    },
                }
            )

            system_message_content = f"<context>Image generation was attempted but failed because of an error. The system is currently unable to generate the image. Tell the user that the following error occurred: {error_message}</context>"

    if system_message_content:
        form_data["messages"] = add_or_update_system_message(
            system_message_content, form_data["messages"]
        )

    return form_data


async def chat_completion_files_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel
) -> tuple[dict, dict[str, list]]:
    __event_emitter__ = extra_params["__event_emitter__"]
    sources = []

    metadata = body.get("metadata", {})
    files = metadata.get("files", None)

    if files and not isinstance(files, list):
        files = []

    if files:
        sanitized_files: list[dict] = []
        for candidate in files:
            if not isinstance(candidate, dict):
                sanitized_files.append({"type": "file", "context": "retrieval"})
                continue

            item = dict(candidate)

            # Images are handled as vision inputs; do not send them into
            # retrieval/file-context pipeline.
            if _is_image_attachment(item):
                continue

            if str(item.get("type") or "file").lower() == "file" and item.get("id"):
                file_id = str(item.get("id"))
                has_access = False
                if user.role == "admin":
                    has_access = True
                else:
                    file_obj = Files.get_file_by_id(file_id)
                    if file_obj and file_obj.user_id == user.id:
                        has_access = True
                    elif AccessGrants.has_access(
                        user_id=user.id,
                        resource_type="file",
                        resource_id=file_id,
                        permission="read",
                    ):
                        has_access = True

                if not has_access:
                    item["_adaptive_excluded"] = True
                    item["_adaptive_reason"] = "scope_denied"

            sanitized_files.append(item)

        files = sanitized_files
        metadata["files"] = files
        body["metadata"] = metadata

        adaptive_enabled = getattr(
            request.app.state.config,
            "ADAPTIVE_FILE_CONTEXT_ENABLED",
            ADAPTIVE_FILE_CONTEXT_ENABLED,
        )
        adaptive_debug = getattr(
            request.app.state.config,
            "ADAPTIVE_FILE_CONTEXT_DEBUG",
            ADAPTIVE_FILE_CONTEXT_DEBUG,
        )

        if adaptive_enabled:
            prompt = get_last_user_message(body.get("messages", []))
            adaptive_config = resolve_adaptive_config(
                {
                    "default_mode": getattr(
                        request.app.state.config,
                        "ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE",
                        ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE,
                    ),
                    "max_tokens_per_file": getattr(
                        request.app.state.config,
                        "ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE",
                        ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE,
                    ),
                    "max_tokens_per_request": getattr(
                        request.app.state.config,
                        "ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST",
                        ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST,
                    ),
                }
            )

            files, decisions = apply_adaptive_context_to_items(
                query=prompt or "",
                items=files,
                config=adaptive_config,
            )

            if adaptive_debug:
                payload = []
                for item, decision in zip(files, decisions):
                    payload.append(
                        {
                            "id": item.get("id"),
                            "type": item.get("type", "file"),
                            "context": item.get("context", "retrieval"),
                            "reason": decision.reason,
                            "estimated_tokens": decision.estimated_tokens,
                            "applied": decision.applied,
                        }
                    )
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "action": "adaptive_file_context",
                            "decisions": payload,
                            "config": {
                                "default_mode": adaptive_config["default_mode"],
                                "max_tokens_per_file": adaptive_config[
                                    "max_tokens_per_file"
                                ],
                                "max_tokens_per_request": adaptive_config[
                                    "max_tokens_per_request"
                                ],
                            },
                            "done": False,
                        },
                    }
                )

        # Check if all files are in full context mode
        all_full_context = all(item.get("context") == "full" for item in files)

        queries = []
        if not all_full_context:
            try:
                queries_response = await generate_queries(
                    request,
                    {
                        "model": body["model"],
                        "messages": body["messages"],
                        "type": "retrieval",
                        "chat_id": body.get("metadata", {}).get("chat_id"),
                    },
                    user,
                )
                queries_response = queries_response["choices"][0]["message"]["content"]

                try:
                    bracket_start = queries_response.find("{")
                    bracket_end = queries_response.rfind("}") + 1

                    if bracket_start == -1 or bracket_end == -1:
                        raise Exception("No JSON object found in the response")

                    queries_response = queries_response[bracket_start:bracket_end]
                    queries_response = json.loads(queries_response)
                except Exception as e:
                    queries_response = {"queries": [queries_response]}

                queries = queries_response.get("queries", [])
            except:
                pass

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "action": "queries_generated",
                        "queries": queries,
                        "done": False,
                    },
                }
            )

        if len(queries) == 0:
            queries = [get_last_user_message(body["messages"])]

        try:
            # Directly await async get_sources_from_items (no thread needed - fully async now)
            sources = await get_sources_from_items(
                request=request,
                items=files,
                queries=queries,
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=request.app.state.config.TOP_K,
                reranking_function=(
                    (
                        lambda query, documents: request.app.state.RERANKING_FUNCTION(
                            query, documents, user=user
                        )
                    )
                    if request.app.state.RERANKING_FUNCTION
                    else None
                ),
                k_reranker=request.app.state.config.TOP_K_RERANKER,
                r=request.app.state.config.RELEVANCE_THRESHOLD,
                hybrid_bm25_weight=request.app.state.config.HYBRID_BM25_WEIGHT,
                hybrid_search=request.app.state.config.ENABLE_RAG_HYBRID_SEARCH,
                full_context=all_full_context
                or request.app.state.config.RAG_FULL_CONTEXT,
                user=user,
            )
        except Exception as e:
            log.exception(e)

        log.debug(f"rag_contexts:sources: {sources}")

        unique_ids = set()
        for source in sources or []:
            if not source or len(source.keys()) == 0:
                continue

            documents = source.get("document") or []
            metadatas = source.get("metadata") or []
            src_info = source.get("source") or {}

            for index, _ in enumerate(documents):
                metadata = metadatas[index] if index < len(metadatas) else None
                _id = (
                    (metadata or {}).get("source")
                    or (src_info or {}).get("id")
                    or "N/A"
                )
                unique_ids.add(_id)

        sources_count = len(unique_ids)
        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "action": "sources_retrieved",
                    "count": sources_count,
                    "done": True,
                },
            }
        )

    return body, {"sources": sources}


def apply_params_to_form_data(form_data, model):
    params = form_data.pop("params", {})
    custom_params = params.pop("custom_params", {})

    open_webui_params = {
        "stream_response": bool,
        "stream_delta_chunk_size": int,
        "function_calling": str,
        "reasoning_tags": list,
        "system": str,
    }

    for key in list(params.keys()):
        if key in open_webui_params:
            del params[key]

    if custom_params:
        # Attempt to parse custom_params if they are strings
        for key, value in custom_params.items():
            if isinstance(value, str):
                try:
                    # Attempt to parse the string as JSON
                    custom_params[key] = json.loads(value)
                except json.JSONDecodeError:
                    # If it fails, keep the original string
                    pass

        # If custom_params are provided, merge them into params
        params = deep_update(params, custom_params)

    if model.get("owned_by") == "ollama":
        # Ollama specific parameters
        form_data["options"] = params
    else:
        if isinstance(params, dict):
            for key, value in params.items():
                if value is not None:
                    form_data[key] = value

        if "logit_bias" in params and params["logit_bias"] is not None:
            try:
                logit_bias = convert_logit_bias_input_to_json(params["logit_bias"])

                if logit_bias:
                    form_data["logit_bias"] = json.loads(logit_bias)
            except Exception as e:
                log.exception(f"Error parsing logit_bias: {e}")

    return form_data


async def convert_url_images_to_base64(form_data):
    messages = form_data.get("messages", [])

    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue

        new_content = []

        for item in content:
            if not isinstance(item, dict) or item.get("type") != "image_url":
                new_content.append(item)
                continue

            image_url = item.get("image_url", {}).get("url", "")
            if image_url.startswith("data:image/"):
                new_content.append(item)
                continue

            try:
                base64_data = await asyncio.to_thread(
                    get_image_base64_from_url, image_url
                )
                new_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": base64_data},
                    }
                )
            except Exception as e:
                log.debug(f"Error converting image URL to base64: {e}")
                new_content.append(item)

        message["content"] = new_content

    return form_data

def load_messages_from_db(chat_id: str, message_id: str) -> Optional[list[dict]]:
    """
    Load the message chain from DB up to message_id,
    keeping only LLM-relevant fields (role, content, output).
    """
    messages_map = Chats.get_messages_map_by_chat_id(chat_id)
    if not messages_map:
        return None

    db_messages = get_message_list(messages_map, message_id)
    if not db_messages:
        return None

    return [
        {k: v for k, v in msg.items() if k in ("role", "content", "output")}
        for msg in db_messages
    ]


def process_messages_with_output(messages: list[dict]) -> list[dict]:
    """
    Process messages with OR-aligned output items for LLM consumption.

    For assistant messages with 'output' field, produces properly formatted
    OpenAI-style messages (tool_calls + tool results). Strips 'output' before LLM.
    """
    processed = []

    for message in messages:
        if message.get("role") == "assistant" and message.get("output"):
            # Use output items for clean OpenAI-format messages
            output_messages = convert_output_to_messages(message["output"])
            if output_messages:
                processed.extend(output_messages)
                continue

        # Strip 'output' field before adding (LLM shouldn't see it)
        clean_message = {k: v for k, v in message.items() if k != "output"}
        processed.append(clean_message)

    return processed


async def process_chat_payload(request, form_data, user, metadata, model):
    # Pipeline Inlet -> Filter Inlet -> Chat Memory -> Chat Web Search -> Chat Image Generation
    # -> Chat Code Interpreter (Form Data Update) -> (Default) Chat Tools Function Calling
    # -> Chat Files

    form_data = apply_params_to_form_data(form_data, model)
    log.debug(f"form_data: {form_data}")

    # Load messages from DB when available — DB preserves structured 'output' items
    # which the frontend strips, causing tool calls to be merged into content.
    chat_id = metadata.get("chat_id")
    parent_message_id = metadata.get("parent_message_id")

    if chat_id and parent_message_id and not chat_id.startswith("local:"):
        db_messages = load_messages_from_db(chat_id, parent_message_id)
        if db_messages:
            system_message = get_system_message(form_data.get("messages", []))
            incoming_messages = [
                message
                for message in form_data.get("messages", [])
                if isinstance(message, dict) and message.get("role") != "system"
            ]
            merged_messages = merge_messages_preserving_incoming_tail(
                db_messages=db_messages,
                incoming_messages=incoming_messages,
            )
            form_data["messages"] = (
                [system_message, *merged_messages] if system_message else merged_messages
            )

    # Process messages with OR-aligned output items for clean LLM messages
    form_data["messages"] = process_messages_with_output(form_data.get("messages", []))

    system_message = get_system_message(form_data.get("messages", []))
    if system_message:  # Chat Controls/User Settings
        try:
            form_data = apply_system_prompt_to_body(
                system_message.get("content"), form_data, metadata, user, replace=True
            )  # Required to handle system prompt variables
        except:
            pass

    form_data = await convert_url_images_to_base64(form_data)

    event_emitter = get_event_emitter(metadata)
    event_caller = get_event_call(metadata)

    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_caller,
        "__user__": user.model_dump() if isinstance(user, UserModel) else {},
        "__metadata__": metadata,
        "__oauth_token__": await get_system_oauth_token(request, user),
        "__request__": request,
        "__model__": model,
        "__chat_id__": metadata.get("chat_id"),
        "__message_id__": metadata.get("message_id"),
    }
    # Initialize events to store additional event to be sent to the client
    # Initialize contexts and citation
    if getattr(request.state, "direct", False) and hasattr(request.state, "model"):
        models = {
            request.state.model["id"]: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    task_model_id = get_task_model_id(
        form_data["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    events = []
    sources = []

    # Folder "Project" handling
    # Check if the request has chat_id and is inside of a folder
    # Uses lightweight column query — only fetches folder_id, not the full chat JSON blob
    chat_id = metadata.get("chat_id", None)
    if chat_id and user:
        folder_id = Chats.get_chat_folder_id(chat_id, user.id)
        if folder_id:
            folder = Folders.get_folder_by_id_and_user_id(folder_id, user.id)

            if folder and folder.data:
                if "system_prompt" in folder.data:
                    form_data = apply_system_prompt_to_body(
                        folder.data["system_prompt"], form_data, metadata, user
                    )
                if "files" in folder.data:
                    form_data["files"] = [
                        *folder.data["files"],
                        *form_data.get("files", []),
                    ]

    # Model "Knowledge" handling
    user_message = get_last_user_message(form_data["messages"])
    model_knowledge = model.get("info", {}).get("meta", {}).get("knowledge", False)

    if (
        model_knowledge
        and metadata.get("params", {}).get("function_calling") != "native"
    ):
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": False,
                },
            }
        )

        knowledge_files = []
        for item in model_knowledge:
            if item.get("collection_name"):
                knowledge_files.append(
                    {
                        "id": item.get("collection_name"),
                        "name": item.get("name"),
                        "legacy": True,
                    }
                )
            elif item.get("collection_names"):
                knowledge_files.append(
                    {
                        "name": item.get("name"),
                        "type": "collection",
                        "collection_names": item.get("collection_names"),
                        "legacy": True,
                    }
                )
            else:
                knowledge_files.append(item)

        files = form_data.get("files", [])
        files.extend(knowledge_files)
        form_data["files"] = files

    variables = form_data.pop("variables", None)

    # Process the form_data through the pipeline
    try:
        form_data = await process_pipeline_inlet_filter(
            request, form_data, user, models
        )
    except Exception as e:
        raise e

    try:
        filter_ids = get_sorted_filter_ids(
            request, model, metadata.get("filter_ids", [])
        )
        filter_functions = Functions.get_functions_by_ids(filter_ids)

        form_data, flags = await process_filter_functions(
            request=request,
            filter_functions=filter_functions,
            filter_type="inlet",
            form_data=form_data,
            extra_params=extra_params,
        )
    except Exception as e:
        raise Exception(f"{e}")

    features = form_data.pop("features", None) or {}
    extra_params["__features__"] = features
    metadata["features"] = features
    if features:
        if "voice" in features and features["voice"]:
            if request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE != None:
                if request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE != "":
                    template = request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE
                else:
                    template = DEFAULT_VOICE_MODE_PROMPT_TEMPLATE

                form_data["messages"] = add_or_update_system_message(
                    template,
                    form_data["messages"],
                )

        if "memory" in features and features["memory"]:
            # Hybrid memory orchestration runs in both default and native FC modes.
            # Native FC still receives memory tools; this step only injects high-confidence context.
            form_data = await chat_memory_handler(
                request, form_data, extra_params, user
            )

        if "web_search" in features and features["web_search"]:
            # Skip forced RAG web search when native FC is enabled - model can use web_search tool
            if metadata.get("params", {}).get("function_calling") != "native":
                form_data = await chat_web_search_handler(
                    request, form_data, extra_params, user
                )

        if "image_generation" in features and features["image_generation"]:
            # Skip forced image generation when native FC is enabled - model can use generate_image tool
            if metadata.get("params", {}).get("function_calling") != "native":
                form_data = await chat_image_generation_handler(
                    request, form_data, extra_params, user
                )

        if "code_interpreter" in features and features["code_interpreter"]:
            # Skip XML-tag prompt injection when native FC is enabled —
            # execute_code will be injected as a builtin tool instead
            if metadata.get("params", {}).get("function_calling") != "native":
                form_data["messages"] = add_or_update_user_message(
                    (
                        request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE
                        if request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE
                        != ""
                        else DEFAULT_CODE_INTERPRETER_PROMPT
                    ),
                    form_data["messages"],
                )

    tool_ids = form_data.pop("tool_ids", None)
    files = form_data.pop("files", None)

    # Caller-provided OpenAI-style tools take precedence over server-side
    # tool resolution (tool_ids, MCP servers, builtin tools).
    payload_tools = form_data.get("tools", None)

    # Skills
    user_skill_ids = set(form_data.pop("skill_ids", None) or [])
    model_skill_ids = set(model.get("info", {}).get("meta", {}).get("skillIds", []))

    all_skill_ids = user_skill_ids | model_skill_ids
    available_skills = []
    if all_skill_ids:
        from open_webui.models.skills import Skills as SkillsModel

        accessible_skill_ids = {
            s.id for s in SkillsModel.get_skills_by_user_id(user.id, "read")
        }
        available_skills = [
            s
            for sid in all_skill_ids
            if sid in accessible_skill_ids
            and (s := SkillsModel.get_skill_by_id(sid))
            and s.is_active
        ]

        skill_descriptions = ""
        for skill in available_skills:
            if skill.id in user_skill_ids:
                # User-selected: inject full content
                form_data["messages"] = add_or_update_system_message(
                    f'<skill name="{skill.name}">\n{skill.content}\n</skill>',
                    form_data["messages"],
                    append=True,
                )
            else:
                # Model-attached: name+description only
                skill_descriptions += f"<skill>\n<name>{skill.name}</name>\n<description>{skill.description or ''}</description>\n</skill>\n"

        if skill_descriptions:
            form_data["messages"] = add_or_update_system_message(
                f"<available_skills>\n{skill_descriptions}</available_skills>",
                form_data["messages"],
                append=True,
            )

    prompt = get_last_user_message(form_data["messages"])
    # TODO: re-enable URL extraction from prompt
    # urls = []
    # if prompt and len(prompt or "") < 500 and (not files or len(files) == 0):
    #     urls = extract_urls(prompt)

    if files:
        if not files:
            files = []

        for file_item in files:
            if file_item.get("type", "file") == "folder":
                # Get folder files
                folder_id = file_item.get("id", None)
                if folder_id:
                    folder = Folders.get_folder_by_id_and_user_id(folder_id, user.id)
                    if folder and folder.data and "files" in folder.data:
                        files = [f for f in files if f.get("id", None) != folder_id]
                        files = [*files, *folder.data["files"]]

        # files = [*files, *[{"type": "url", "url": url, "name": url} for url in urls]]
        # Remove duplicate files based on their content
        files = list({json.dumps(f, sort_keys=True): f for f in files}.values())

    metadata = {
        **metadata,
        "tool_ids": tool_ids,
        "files": files,
    }
    form_data["metadata"] = metadata

    # When the caller provides an explicit OpenAI-style `tools` array in the
    # request body, skip all server-side tool resolution and pass the caller's
    # tools through to the model unchanged.
    if not payload_tools:
        # Server side tools
        tool_ids = metadata.get("tool_ids", None)
        # Client side tools
        direct_tool_servers = metadata.get("tool_servers", None)

        log.debug(f"{tool_ids=}")
        log.debug(f"{direct_tool_servers=}")

        tools_dict = {}

        mcp_clients = {}
        mcp_tools_dict = {}

        if tool_ids:
            for tool_id in tool_ids:
                if tool_id.startswith("server:mcp:"):
                    try:
                        server_id = tool_id[len("server:mcp:") :]

                        mcp_server_connection = None
                        for (
                            server_connection
                        ) in request.app.state.config.TOOL_SERVER_CONNECTIONS:
                            if (
                                server_connection.get("type", "") == "mcp"
                                and server_connection.get("info", {}).get("id")
                                == server_id
                            ):
                                mcp_server_connection = server_connection
                                break

                        if not mcp_server_connection:
                            log.error(f"MCP server with id {server_id} not found")
                            continue

                        # Check access control for MCP server
                        if not has_tool_server_access(user, mcp_server_connection):
                            log.warning(
                                f"Access denied to MCP server {server_id} for user {user.id}"
                            )
                            continue

                        auth_type = mcp_server_connection.get("auth_type", "")
                        headers = {}
                        if auth_type == "bearer":
                            headers["Authorization"] = (
                                f"Bearer {mcp_server_connection.get('key', '')}"
                            )
                        elif auth_type == "none":
                            # No authentication
                            pass
                        elif auth_type == "session":
                            headers["Authorization"] = (
                                f"Bearer {request.state.token.credentials}"
                            )
                        elif auth_type == "system_oauth":
                            oauth_token = extra_params.get("__oauth_token__", None)
                            if oauth_token:
                                headers["Authorization"] = (
                                    f"Bearer {oauth_token.get('access_token', '')}"
                                )
                        elif auth_type == "oauth_2.1":
                            try:
                                splits = server_id.split(":")
                                server_id = splits[-1] if len(splits) > 1 else server_id

                                oauth_token = await request.app.state.oauth_client_manager.get_oauth_token(
                                    user.id, f"mcp:{server_id}"
                                )

                                if oauth_token:
                                    headers["Authorization"] = (
                                        f"Bearer {oauth_token.get('access_token', '')}"
                                    )
                            except Exception as e:
                                log.error(f"Error getting OAuth token: {e}")
                                oauth_token = None

                        connection_headers = mcp_server_connection.get("headers", None)
                        if connection_headers and isinstance(connection_headers, dict):
                            for key, value in connection_headers.items():
                                headers[key] = value

                        # Add user info headers if enabled
                        if ENABLE_FORWARD_USER_INFO_HEADERS and user:
                            headers = include_user_info_headers(headers, user)
                            if metadata and metadata.get("chat_id"):
                                headers[FORWARD_SESSION_INFO_HEADER_CHAT_ID] = (
                                    metadata.get("chat_id")
                                )
                            if metadata and metadata.get("message_id"):
                                headers[FORWARD_SESSION_INFO_HEADER_MESSAGE_ID] = (
                                    metadata.get("message_id")
                                )

                        mcp_clients[server_id] = MCPClient()
                        await mcp_clients[server_id].connect(
                            url=mcp_server_connection.get("url", ""),
                            headers=headers if headers else None,
                        )

                        function_name_filter_list = mcp_server_connection.get(
                            "config", {}
                        ).get("function_name_filter_list", "")

                        if isinstance(function_name_filter_list, str):
                            function_name_filter_list = function_name_filter_list.split(
                                ","
                            )

                        tool_specs = await mcp_clients[server_id].list_tool_specs()
                        for tool_spec in tool_specs:

                            def make_tool_function(client, function_name):
                                async def tool_function(**kwargs):
                                    return await client.call_tool(
                                        function_name,
                                        function_args=kwargs,
                                    )

                                return tool_function

                            if function_name_filter_list:
                                if not is_string_allowed(
                                    tool_spec["name"], function_name_filter_list
                                ):
                                    # Skip this function
                                    continue

                            tool_function = make_tool_function(
                                mcp_clients[server_id], tool_spec["name"]
                            )

                            mcp_tools_dict[f"{server_id}_{tool_spec['name']}"] = {
                                "spec": {
                                    **tool_spec,
                                    "name": f"{server_id}_{tool_spec['name']}",
                                },
                                "callable": tool_function,
                                "type": "mcp",
                                "client": mcp_clients[server_id],
                                "direct": False,
                            }
                    except Exception as e:
                        log.debug(e)
                        if event_emitter:
                            await event_emitter(
                                {
                                    "type": "chat:message:error",
                                    "data": {
                                        "error": {
                                            "content": f"Failed to connect to MCP server '{server_id}'"
                                        }
                                    },
                                }
                            )
                        continue

            tools_dict = await get_tools(
                request,
                tool_ids,
                user,
                {
                    **extra_params,
                    "__model__": models[task_model_id],
                    "__messages__": form_data["messages"],
                    "__files__": metadata.get("files", []),
                },
            )

            if mcp_tools_dict:
                tools_dict = {**tools_dict, **mcp_tools_dict}

        if direct_tool_servers:
            for tool_server in direct_tool_servers:
                tool_specs = tool_server.pop("specs", [])

                for tool in tool_specs:
                    tools_dict[tool["name"]] = {
                        "spec": tool,
                        "direct": True,
                        "server": tool_server,
                    }

        if mcp_clients:
            metadata["mcp_clients"] = mcp_clients

        # Inject builtin tools for native function calling based on enabled features and model capability
        # Check if builtin_tools capability is enabled for this model (defaults to True if not specified)
        builtin_tools_enabled = (
            model.get("info", {}).get("meta", {}).get("capabilities") or {}
        ).get("builtin_tools", True)
        if (
            metadata.get("params", {}).get("function_calling") == "native"
            and builtin_tools_enabled
        ):
            # Add file context to user messages
            chat_id = metadata.get("chat_id")
            form_data["messages"] = add_file_context(
                form_data.get("messages", []), chat_id, user
            )
            builtin_tools = get_builtin_tools(
                request,
                {
                    **extra_params,
                    "__event_emitter__": event_emitter,
                    "__skill_ids__": [
                        s.id for s in available_skills if s.id not in user_skill_ids
                    ],
                },
                features,
                model,
            )
            for name, tool_dict in builtin_tools.items():
                if name not in tools_dict:
                    tools_dict[name] = tool_dict

        if tools_dict:
            if metadata.get("params", {}).get("function_calling") == "native":
                # If the function calling is native, then call the tools function calling handler
                metadata["tools"] = tools_dict
                form_data["tools"] = [
                    {"type": "function", "function": tool.get("spec", {})}
                    for tool in tools_dict.values()
                ]

        else:
            # If the function calling is not native, then call the tools function calling handler
            try:
                form_data, flags = await chat_completion_tools_handler(
                    request, form_data, extra_params, user, models, tools_dict
                )
                sources.extend(flags.get("sources", []))
            except Exception as e:
                log.exception(e)

    # Check if file context extraction is enabled for this model (default True)
    file_context_enabled = (
        model.get("info", {}).get("meta", {}).get("capabilities") or {}
    ).get("file_context", True)

    if file_context_enabled:
        try:
            form_data, flags = await chat_completion_files_handler(
                request, form_data, extra_params, user
            )
            sources.extend(flags.get("sources", []))
        except Exception as e:
            log.exception(e)

    # If context is not empty, insert it into the messages
    if sources and prompt:
        form_data["messages"] = apply_source_context_to_messages(
            request, form_data["messages"], sources, prompt
        )

    # If there are citations, add them to the data_items
    sources = [
        source
        for source in sources
        if source.get("source", {}).get("name", "")
        or source.get("source", {}).get("id", "")
    ]

    if len(sources) > 0:
        events.append({"sources": sources})

    if model_knowledge:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": True,
                    "hidden": True,
                },
            }
        )

    try:
        form_data, budget_diagnostics = await apply_context_budget_policy(
            request=request,
            form_data=form_data,
            user=user,
            models=models,
        )
        if budget_diagnostics.get("overflow"):
            log.warning(
                "Context budget overflow on primary payload "
                f"(chat_id={metadata.get('chat_id')}, message_id={metadata.get('message_id')})"
            )
    except Exception as e:
        log.debug(f"Context budget policy failed on primary payload: {e}")

    return form_data, metadata, events


def get_event_emitter_and_caller(metadata):
    event_emitter = None
    event_caller = None
    if (
        "session_id" in metadata
        and metadata["session_id"]
        and "chat_id" in metadata
        and metadata["chat_id"]
        and "message_id" in metadata
        and metadata["message_id"]
    ):
        event_emitter = get_event_emitter(metadata)
        event_caller = get_event_call(metadata)
    return event_emitter, event_caller


def build_chat_response_context(
    request, form_data, user, model, metadata, tasks, events
):
    event_emitter, event_caller = get_event_emitter_and_caller(metadata)
    return {
        "request": request,
        "form_data": form_data,
        "user": user,
        "model": model,
        "metadata": metadata,
        "tasks": tasks,
        "events": events,
        "event_emitter": event_emitter,
        "event_caller": event_caller,
    }


def get_response_data(response):
    if isinstance(response, list) and len(response) == 1:
        # If the response is a single-item list, unwrap it #17213
        response = response[0]

    if isinstance(response, JSONResponse):
        if isinstance(response.body, bytes):
            try:
                response_data = json.loads(response.body.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                response_data = {"error": {"detail": "Invalid JSON response"}}
        else:
            response_data = response
    elif isinstance(response, dict):
        response_data = response
    else:
        response_data = None

    return response, response_data


def merge_events_into_response(response_data, events):
    if events and isinstance(events, list):
        extra_response = {}
        for event in events:
            if isinstance(event, dict):
                extra_response.update(event)
            else:
                extra_response[event] = True

        return {
            **extra_response,
            **response_data,
        }
    return response_data


def build_response_object(response, response_data):
    if isinstance(response, dict):
        return response_data
    if isinstance(response, JSONResponse):
        return JSONResponse(
            content=response_data,
            headers=response.headers,
            status_code=response.status_code,
        )
    return response


async def get_system_oauth_token(request, user):
    oauth_token = None
    try:
        if request.cookies.get("oauth_session_id", None):
            oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                user.id,
                request.cookies.get("oauth_session_id", None),
            )
    except Exception as e:
        log.error(f"Error getting OAuth token: {e}")
    return oauth_token


async def background_tasks_handler(ctx):
    request = ctx["request"]
    form_data = ctx["form_data"]
    user = ctx["user"]
    metadata = ctx["metadata"]
    tasks = ctx["tasks"]
    event_emitter = ctx["event_emitter"]

    message = None
    messages = []

    if "chat_id" in metadata and not metadata["chat_id"].startswith("local:"):
        messages_map = Chats.get_messages_map_by_chat_id(metadata["chat_id"])
        message = messages_map.get(metadata["message_id"]) if messages_map else None

        message_list = get_message_list(messages_map, metadata["message_id"])

        # Remove details tags and files from the messages.
        # as get_message_list creates a new list, it does not affect
        # the original messages outside of this handler

        messages = []
        for message in message_list:
            content = message.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        content = item["text"]
                        break

            if isinstance(content, str):
                content = re.sub(
                    r"<details\b[^>]*>.*?<\/details>|!\[.*?\]\(.*?\)",
                    "",
                    content,
                    flags=re.S | re.I,
                ).strip()

            messages.append(
                {
                    **message,
                    "role": message.get(
                        "role", "assistant"
                    ),  # Safe fallback for missing role
                    "content": content,
                }
            )
    else:
        # Local temp chat, get the model and message from the form_data
        message = get_last_user_message_item(form_data.get("messages", []))
        messages = form_data.get("messages", [])
        if message:
            message["model"] = form_data.get("model")

    if message and "model" in message:
        if tasks and messages:
            if (
                TASKS.FOLLOW_UP_GENERATION in tasks
                and tasks[TASKS.FOLLOW_UP_GENERATION]
            ):
                res = await generate_follow_ups(
                    request,
                    {
                        "model": message["model"],
                        "messages": messages,
                        "message_id": metadata["message_id"],
                        "chat_id": metadata["chat_id"],
                    },
                    user,
                )

                if res and isinstance(res, dict):
                    if len(res.get("choices", [])) == 1:
                        response_message = res.get("choices", [])[0].get("message", {})

                        follow_ups_string = response_message.get("content") or extract_reasoning_text(
                            response_message
                        )
                    else:
                        follow_ups_string = ""

                    follow_ups_string = follow_ups_string[
                        follow_ups_string.find("{") : follow_ups_string.rfind("}") + 1
                    ]

                    try:
                        follow_ups = json.loads(follow_ups_string).get("follow_ups", [])
                        await event_emitter(
                            {
                                "type": "chat:message:follow_ups",
                                "data": {
                                    "follow_ups": follow_ups,
                                },
                            }
                        )

                        if not metadata.get("chat_id", "").startswith("local:"):
                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                metadata["chat_id"],
                                metadata["message_id"],
                                {
                                    "followUps": follow_ups,
                                },
                            )

                    except Exception as e:
                        pass

            if not metadata.get("chat_id", "").startswith(
                "local:"
            ):  # Only update titles and tags for non-temp chats
                if TASKS.TITLE_GENERATION in tasks:
                    user_message = get_last_user_message(messages)
                    if user_message and len(user_message) > 100:
                        user_message = user_message[:100] + "..."

                    title = None
                    if tasks[TASKS.TITLE_GENERATION]:
                        res = await generate_title(
                            request,
                            {
                                "model": message["model"],
                                "messages": messages,
                                "chat_id": metadata["chat_id"],
                            },
                            user,
                        )

                        if res and isinstance(res, dict):
                            if len(res.get("choices", [])) == 1:
                                response_message = res.get("choices", [])[0].get(
                                    "message", {}
                                )

                                title_string = (
                                    response_message.get("content")
                                    or extract_reasoning_text(response_message)
                                    or message.get("content", user_message)
                                )
                            else:
                                title_string = ""

                            title_string = title_string[
                                title_string.find("{") : title_string.rfind("}") + 1
                            ]

                            try:
                                title = json.loads(title_string).get(
                                    "title", user_message
                                )
                            except Exception as e:
                                title = ""

                            if not title:
                                title = messages[0].get("content", user_message)

                            Chats.update_chat_title_by_id(metadata["chat_id"], title)

                            await event_emitter(
                                {
                                    "type": "chat:title",
                                    "data": title,
                                }
                            )

                    if title == None and len(messages) == 2:
                        title = messages[0].get("content", user_message)

                        Chats.update_chat_title_by_id(metadata["chat_id"], title)

                        await event_emitter(
                            {
                                "type": "chat:title",
                                "data": message.get("content", user_message),
                            }
                        )

                if TASKS.TAGS_GENERATION in tasks and tasks[TASKS.TAGS_GENERATION]:
                    res = await generate_chat_tags(
                        request,
                        {
                            "model": message["model"],
                            "messages": messages,
                            "chat_id": metadata["chat_id"],
                        },
                        user,
                    )

                    if res and isinstance(res, dict):
                        if len(res.get("choices", [])) == 1:
                            response_message = res.get("choices", [])[0].get(
                                "message", {}
                            )

                            tags_string = response_message.get("content") or extract_reasoning_text(
                                response_message
                            )
                        else:
                            tags_string = ""

                        tags_string = tags_string[
                            tags_string.find("{") : tags_string.rfind("}") + 1
                        ]

                        try:
                            tags = json.loads(tags_string).get("tags", [])
                            Chats.update_chat_tags_by_id(
                                metadata["chat_id"], tags, user
                            )

                            await event_emitter(
                                {
                                    "type": "chat:tags",
                                    "data": tags,
                                }
                            )
                        except Exception as e:
                            pass

        try:
            await run_auto_memory_writeback(
                request=request,
                user=user,
                metadata=metadata,
                form_data=form_data,
                messages=messages,
                event_emitter=event_emitter,
            )
        except Exception as e:
            log.debug(f"auto memory writeback error: {e}")


async def non_streaming_chat_response_handler(response, ctx):
    request = ctx["request"]

    user = ctx["user"]
    metadata = ctx["metadata"]
    events = ctx["events"]

    event_emitter = ctx["event_emitter"]

    response, response_data = get_response_data(response)
    if response_data is None:
        return response

    if event_emitter:
        try:
            if "error" in response_data:
                error = response_data.get("error")

                if isinstance(error, dict):
                    error = error.get("detail", error)
                else:
                    error = str(error)

                Chats.upsert_message_to_chat_by_id_and_message_id(
                    metadata["chat_id"],
                    metadata["message_id"],
                    {
                        "error": {"content": error},
                    },
                )
                if isinstance(error, str) or isinstance(error, dict):
                    await event_emitter(
                        {
                            "type": "chat:message:error",
                            "data": {"error": {"content": error}},
                        }
                    )

            if "selected_model_id" in response_data:
                Chats.upsert_message_to_chat_by_id_and_message_id(
                    metadata["chat_id"],
                    metadata["message_id"],
                    {
                        "selectedModelId": response_data["selected_model_id"],
                    },
                )

            choices = response_data.get("choices", [])
            response_message = choices[0].get("message", {}) if choices else {}
            content = response_message.get("content", "")
            reasoning_content = extract_reasoning_text(response_message)
            response_output = response_data.get("output")

            if choices and (content or reasoning_content or response_output):
                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": response_data,
                    }
                )

                title = Chats.get_chat_title_by_id(metadata["chat_id"])

                # Use output from backend if provided (OR-compliant backends),
                # otherwise generate from response content/reasoning.
                if not response_output:
                    response_output = []

                    if reasoning_content:
                        response_output.append(
                            {
                                "type": "reasoning",
                                "id": output_id("r"),
                                "status": "completed",
                                "start_tag": "<think>",
                                "end_tag": "</think>",
                                "attributes": {"type": "reasoning_content"},
                                "content": [
                                    {"type": "output_text", "text": reasoning_content}
                                ],
                                "summary": None,
                            }
                        )

                    response_output.append(
                        {
                            "type": "message",
                            "id": output_id("msg"),
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content}],
                        }
                    )

                content_to_store = content or serialize_output(response_output)

                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": {
                            "done": True,
                            "content": content_to_store,
                            "output": response_output,
                            "title": title,
                        },
                    }
                )

                # Save message in the database
                Chats.upsert_message_to_chat_by_id_and_message_id(
                    metadata["chat_id"],
                    metadata["message_id"],
                    {
                        "role": "assistant",
                        "content": content_to_store,
                        "output": response_output,
                    },
                )

                # Send a webhook notification if the user is not active
                if not Users.is_user_active(user.id):
                    webhook_url = Users.get_user_webhook_url_by_id(user.id)
                    if webhook_url:
                        await post_webhook(
                            request.app.state.WEBUI_NAME,
                            webhook_url,
                            f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{content_to_store}",
                            {
                                "action": "chat",
                                "message": content_to_store,
                                "title": title,
                                "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                            },
                        )

                await background_tasks_handler(ctx)

            response = build_response_object(
                response, merge_events_into_response(response_data, events)
            )
        except Exception as e:
            log.debug(f"Error occurred while processing request: {e}")
            pass

        return response

    if isinstance(response, dict):
        response = merge_events_into_response(response_data, events)

    return response


async def streaming_chat_response_handler(response, ctx):
    request = ctx["request"]

    form_data = ctx["form_data"]

    user = ctx["user"]
    model = ctx["model"]

    metadata = ctx["metadata"]
    events = ctx["events"]

    event_emitter = ctx["event_emitter"]
    event_caller = ctx["event_caller"]

    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_caller,
        "__user__": user.model_dump() if isinstance(user, UserModel) else {},
        "__metadata__": metadata,
        "__oauth_token__": await get_system_oauth_token(request, user),
        "__request__": request,
        "__model__": model,
    }

    filter_functions = [
        Functions.get_function_by_id(filter_id)
        for filter_id in get_sorted_filter_ids(
            request, model, metadata.get("filter_ids", [])
        )
    ]

    # Standard streaming response handler
    if event_emitter and event_caller:
        task_id = str(uuid4())  # Create a unique task ID.
        model_id = form_data.get("model", "")

        # Handle as a background task
        async def response_handler(response, events):
            def tag_output_handler(content_type, tags, output):
                """
                Detect special tags (reasoning, solution, code_interpreter) in streaming
                content and create corresponding OR-aligned output items directly.
                Operates on output items instead of content_blocks.

                Uses the text from the output items themselves for tag detection,
                eliminating state divergence between accumulated content and items.
                """
                end_flag = False

                def extract_attributes(tag_content):
                    """Extract attributes from a tag if they exist."""
                    attributes = {}
                    if not tag_content:
                        return attributes
                    matches = re.findall(r'(\w+)\s*=\s*"([^"]+)"', tag_content)
                    for key, value in matches:
                        attributes[key] = value
                    return attributes

                def get_last_text(out):
                    """Get text from last message item, or empty string."""
                    if out and out[-1].get("type") == "message":
                        parts = out[-1].get("content", [])
                        if parts and parts[-1].get("type") == "output_text":
                            return parts[-1].get("text", "")
                    return ""

                def set_last_text(out, text):
                    """Set text on last message item's output_text."""
                    if out and out[-1].get("type") == "message":
                        parts = out[-1].get("content", [])
                        if parts and parts[-1].get("type") == "output_text":
                            parts[-1]["text"] = text

                # Map content_type to output item type
                output_type_map = {
                    "reasoning": "reasoning",
                    "solution": "message",  # solution tags just produce text
                    "code_interpreter": "open_webui:code_interpreter",
                }
                output_item_type = output_type_map.get(content_type, content_type)

                last_type = output[-1].get("type", "") if output else ""

                if last_type == "message":
                    # Use the output item's own text for tag detection
                    item_text = get_last_text(output)
                    for start_tag, end_tag in tags:

                        start_tag_pattern = rf"{re.escape(start_tag)}"
                        if start_tag.startswith("<") and start_tag.endswith(">"):
                            start_tag_pattern = (
                                rf"<{re.escape(start_tag[1:-1])}(\s.*?)?>"
                            )

                        match = re.search(start_tag_pattern, item_text)
                        if match:
                            try:
                                attr_content = match.group(1) if match.group(1) else ""
                            except:
                                attr_content = ""

                            attributes = extract_attributes(attr_content)

                            before_tag = item_text[: match.start()]
                            after_tag = item_text[match.end() :]

                            # Keep only text before the tag in the message
                            set_last_text(output, before_tag)

                            if not before_tag.strip():
                                # Remove empty message item
                                if output and output[-1].get("type") == "message":
                                    output.pop()

                            # Append the new output item
                            if output_item_type == "reasoning":
                                output.append(
                                    {
                                        "type": "reasoning",
                                        "id": output_id("r"),
                                        "status": "in_progress",
                                        "start_tag": start_tag,
                                        "end_tag": end_tag,
                                        "attributes": attributes,
                                        "content": [],
                                        "summary": None,
                                        "started_at": time.time(),
                                    }
                                )
                            elif output_item_type == "open_webui:code_interpreter":
                                output.append(
                                    {
                                        "type": "open_webui:code_interpreter",
                                        "id": output_id("ci"),
                                        "status": "in_progress",
                                        "start_tag": start_tag,
                                        "end_tag": end_tag,
                                        "attributes": attributes,
                                        "lang": attributes.get("lang", "python"),
                                        "code": "",
                                        "output": None,
                                        "started_at": time.time(),
                                    }
                                )
                            else:
                                # solution or other text-producing tag
                                output.append(
                                    {
                                        "type": "message",
                                        "id": output_id("msg"),
                                        "status": "in_progress",
                                        "role": "assistant",
                                        "content": [
                                            {"type": "output_text", "text": ""}
                                        ],
                                        "_tag_type": content_type,
                                        "start_tag": start_tag,
                                        "end_tag": end_tag,
                                        "attributes": attributes,
                                        "started_at": time.time(),
                                    }
                                )

                            if after_tag:
                                # Set the after_tag content on the new item
                                if output_item_type == "reasoning":
                                    output[-1]["content"] = [
                                        {"type": "output_text", "text": after_tag}
                                    ]
                                elif output_item_type == "open_webui:code_interpreter":
                                    output[-1]["code"] = after_tag
                                else:
                                    set_last_text(output, after_tag)

                                _, recursive_end = tag_output_handler(
                                    content_type, tags, output
                                )
                                if recursive_end:
                                    end_flag = True

                            break

                elif (
                    (last_type == "reasoning" and content_type == "reasoning")
                    or (
                        last_type == "open_webui:code_interpreter"
                        and content_type == "code_interpreter"
                    )
                    or (
                        last_type == "message"
                        and output[-1].get("_tag_type") == content_type
                    )
                ):
                    item = output[-1]
                    start_tag = item.get("start_tag", "")
                    end_tag = item.get("end_tag", "")

                    end_tag_pattern = rf"{re.escape(end_tag)}"

                    # Get the block content from the item itself
                    if last_type == "reasoning":
                        parts = item.get("content", [])
                        block_content = ""
                        if parts and parts[-1].get("type") == "output_text":
                            block_content = parts[-1].get("text", "")
                    elif last_type == "open_webui:code_interpreter":
                        block_content = item.get("code", "")
                    else:
                        block_content = get_last_text(output)

                    if re.search(end_tag_pattern, block_content):
                        end_flag = True

                        # Strip start and end tags from content
                        start_tag_pattern = rf"{re.escape(start_tag)}"
                        if start_tag.startswith("<") and start_tag.endswith(">"):
                            start_tag_pattern = (
                                rf"<{re.escape(start_tag[1:-1])}(\s.*?)?>"
                            )
                        block_content = re.sub(
                            start_tag_pattern, "", block_content
                        ).strip()

                        end_tag_regex = re.compile(end_tag_pattern, re.DOTALL)
                        split_content = end_tag_regex.split(block_content, maxsplit=1)

                        block_content = (
                            split_content[0].strip() if split_content else ""
                        )
                        leftover_content = (
                            split_content[1].strip() if len(split_content) > 1 else ""
                        )

                        if block_content:
                            # Update the item with final content
                            if last_type == "reasoning":
                                item["content"] = [
                                    {"type": "output_text", "text": block_content}
                                ]
                                _finalize_timed_item(
                                    item, ended_at=time.time(), mark_completed=True
                                )
                            elif last_type == "open_webui:code_interpreter":
                                item["code"] = block_content
                                _finalize_timed_item(
                                    item, ended_at=time.time(), mark_completed=False
                                )
                            else:
                                set_last_text(output, block_content)
                                item["ended_at"] = time.time()

                            # Reset by appending a new message item for leftover
                            output.append(
                                {
                                    "type": "message",
                                    "id": output_id("msg"),
                                    "status": "in_progress",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": leftover_content,
                                        }
                                    ],
                                }
                            )
                        else:
                            # Remove the block if content is empty
                            output.pop()
                            output.append(
                                {
                                    "type": "message",
                                    "id": output_id("msg"),
                                    "status": "in_progress",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": leftover_content,
                                        }
                                    ],
                                }
                            )

                return output, end_flag

            message = Chats.get_message_by_id_and_message_id(
                metadata["chat_id"], metadata["message_id"]
            )

            tool_calls = []

            last_assistant_message = None
            try:
                if form_data["messages"][-1]["role"] == "assistant":
                    last_assistant_message = get_last_assistant_message(
                        form_data["messages"]
                    )
            except Exception as e:
                pass

            content = (
                message.get("content", "")
                if message
                else last_assistant_message if last_assistant_message else ""
            )

            # Initialize output: use existing from message if continuing, else create new
            existing_output = message.get("output") if message else None
            if existing_output:
                output = existing_output
            else:
                # Only create an initial message item if there is content to initialize with
                if content:
                    output = [
                        {
                            "type": "message",
                            "id": output_id("msg"),
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content}],
                        }
                    ]
                else:
                    output = []

            usage = None

            reasoning_tags_param = metadata.get("params", {}).get("reasoning_tags")
            DETECT_REASONING_TAGS = reasoning_tags_param is not False
            DETECT_CODE_INTERPRETER = metadata.get("features", {}).get(
                "code_interpreter", False
            )

            reasoning_tags = []
            if DETECT_REASONING_TAGS:
                if (
                    isinstance(reasoning_tags_param, list)
                    and len(reasoning_tags_param) == 2
                ):
                    reasoning_tags = [
                        (reasoning_tags_param[0], reasoning_tags_param[1])
                    ]
                else:
                    reasoning_tags = DEFAULT_REASONING_TAGS

            try:
                for event in events:
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": event,
                        }
                    )

                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            **event,
                        },
                    )

                async def stream_body_handler(response, form_data):
                    nonlocal content
                    nonlocal usage
                    nonlocal output

                    response_tool_calls = []

                    delta_count = 0
                    delta_chunk_size = max(
                        CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE,
                        int(
                            metadata.get("params", {}).get("stream_delta_chunk_size")
                            or 1
                        ),
                    )
                    last_delta_data = None
                    saw_responses_events = False

                    async def flush_pending_delta_data(threshold: int = 0):
                        nonlocal delta_count
                        nonlocal last_delta_data

                        if delta_count >= threshold and last_delta_data:
                            await event_emitter(
                                {
                                    "type": "chat:completion",
                                    "data": last_delta_data,
                                }
                            )
                            delta_count = 0
                            last_delta_data = None

                    async for line in response.body_iterator:
                        line = (
                            line.decode("utf-8", "replace")
                            if isinstance(line, bytes)
                            else line
                        )
                        data = line

                        # Skip empty lines
                        if not data.strip():
                            continue

                        # "data:" is the prefix for each event
                        if not data.startswith("data:"):
                            continue

                        # Remove the prefix
                        data = data[len("data:") :].strip()

                        try:
                            data = json.loads(data)

                            data, _ = await process_filter_functions(
                                request=request,
                                filter_functions=filter_functions,
                                filter_type="stream",
                                form_data=data,
                                extra_params={"__body__": form_data, **extra_params},
                            )

                            if data:
                                if "event" in data and not getattr(
                                    request.state, "direct", False
                                ):
                                    await event_emitter(data.get("event", {}))

                                if "selected_model_id" in data:
                                    model_id = data["selected_model_id"]
                                    Chats.upsert_message_to_chat_by_id_and_message_id(
                                        metadata["chat_id"],
                                        metadata["message_id"],
                                        {
                                            "selectedModelId": model_id,
                                        },
                                    )
                                    await event_emitter(
                                        {
                                            "type": "chat:completion",
                                            "data": data,
                                        }
                                    )
                                # Check for Responses API events (type field starts with "response.")
                                elif data.get("type", "").startswith("response."):
                                    saw_responses_events = True
                                    output, response_metadata = (
                                        handle_responses_streaming_event(data, output)
                                    )
                                    display_output = normalize_reasoning_output_items(
                                        output
                                    )

                                    processed_data = {
                                        "output": display_output,
                                        "content": serialize_output(display_output),
                                    }

                                    # print(data)
                                    # print(processed_data)

                                    # Merge any metadata (usage, done, etc.)
                                    if response_metadata:
                                        processed_data.update(response_metadata)

                                    await event_emitter(
                                        {
                                            "type": "chat:completion",
                                            "data": processed_data,
                                        }
                                    )
                                    continue
                                else:
                                    choices = data.get("choices", [])

                                    # Normalize usage data to standard format
                                    raw_usage = data.get("usage", {}) or {}
                                    raw_usage.update(
                                        data.get("timings", {})
                                    )  # llama.cpp
                                    if raw_usage:
                                        usage = normalize_usage(raw_usage)
                                        await event_emitter(
                                            {
                                                "type": "chat:completion",
                                                "data": {
                                                    "usage": usage,
                                                },
                                            }
                                        )

                                    if not choices:
                                        error = data.get("error", {})
                                        if error:
                                            await event_emitter(
                                                {
                                                    "type": "chat:completion",
                                                    "data": {
                                                        "error": error,
                                                    },
                                                }
                                            )
                                        continue

                                    delta = choices[0].get("delta", {})

                                    # Handle delta annotations
                                    annotations = delta.get("annotations")
                                    if annotations:
                                        for annotation in annotations:
                                            if (
                                                annotation.get("type") == "url_citation"
                                                and "url_citation" in annotation
                                            ):
                                                url_citation = annotation[
                                                    "url_citation"
                                                ]

                                                url = url_citation.get("url", "")
                                                title = url_citation.get("title", url)

                                                await event_emitter(
                                                    {
                                                        "type": "source",
                                                        "data": {
                                                            "source": {
                                                                "name": title,
                                                                "url": url,
                                                            },
                                                            "document": [title],
                                                            "metadata": [
                                                                {
                                                                    "source": url,
                                                                    "name": title,
                                                                }
                                                            ],
                                                        },
                                                    }
                                                )

                                    delta_tool_calls = delta.get("tool_calls", None)
                                    if delta_tool_calls:
                                        for delta_tool_call in delta_tool_calls:
                                            tool_call_index = delta_tool_call.get(
                                                "index"
                                            )

                                            if tool_call_index is not None:
                                                # Check if the tool call already exists
                                                current_response_tool_call = None
                                                for (
                                                    response_tool_call
                                                ) in response_tool_calls:
                                                    if (
                                                        response_tool_call.get("index")
                                                        == tool_call_index
                                                    ):
                                                        current_response_tool_call = (
                                                            response_tool_call
                                                        )
                                                        break

                                                if current_response_tool_call is None:
                                                    # Add the new tool call
                                                    delta_tool_call.setdefault(
                                                        "function", {}
                                                    )
                                                    delta_tool_call[
                                                        "function"
                                                    ].setdefault("name", "")
                                                    delta_tool_call[
                                                        "function"
                                                    ].setdefault("arguments", "")
                                                    delta_tool_call_thought_sig = (
                                                        delta_tool_call.get("thoughtSignature")
                                                        or delta_tool_call.get("thought_signature")
                                                        or delta_tool_call.get("function", {}).get("thoughtSignature")
                                                        or delta_tool_call.get("function", {}).get("thought_signature")
                                                    )
                                                    if delta_tool_call_thought_sig:
                                                        delta_tool_call["thoughtSignature"] = delta_tool_call_thought_sig
                                                        delta_tool_call["thought_signature"] = delta_tool_call_thought_sig
                                                        delta_tool_call["function"]["thoughtSignature"] = delta_tool_call_thought_sig
                                                        delta_tool_call["function"]["thought_signature"] = delta_tool_call_thought_sig
                                                    response_tool_calls.append(
                                                        delta_tool_call
                                                    )
                                                else:
                                                    # Update the existing tool call
                                                    delta_name = delta_tool_call.get(
                                                        "function", {}
                                                    ).get("name")
                                                    delta_arguments = (
                                                        delta_tool_call.get(
                                                            "function", {}
                                                        ).get("arguments")
                                                    )

                                                    if delta_name:
                                                        current_response_tool_call[
                                                            "function"
                                                        ]["name"] += delta_name

                                                    if delta_arguments:
                                                        current_response_tool_call[
                                                            "function"
                                                        ][
                                                            "arguments"
                                                        ] += delta_arguments

                                                    delta_thought_sig = (
                                                        delta_tool_call.get("thoughtSignature")
                                                        or delta_tool_call.get("thought_signature")
                                                        or delta_tool_call.get("function", {}).get("thoughtSignature")
                                                        or delta_tool_call.get("function", {}).get("thought_signature")
                                                    )
                                                    if delta_thought_sig:
                                                        current_response_tool_call["thoughtSignature"] = delta_thought_sig
                                                        current_response_tool_call["thought_signature"] = delta_thought_sig
                                                        current_response_tool_call["function"]["thoughtSignature"] = delta_thought_sig
                                                        current_response_tool_call["function"]["thought_signature"] = delta_thought_sig

                                        # Emit pending tool calls in real-time
                                        if response_tool_calls:
                                            # Flush any pending text first
                                            await flush_pending_delta_data()

                                            # Build pending function_call output items for display
                                            pending_fc_items = []
                                            for tc in response_tool_calls:
                                                call_id = tc.get("id", "")
                                                func = tc.get("function", {})
                                                tc_thought_sig = (
                                                    tc.get("thoughtSignature")
                                                    or tc.get("thought_signature")
                                                    or func.get("thoughtSignature")
                                                    or func.get("thought_signature")
                                                )
                                                pending_fc_items.append(
                                                    {
                                                        "type": "function_call",
                                                        "id": call_id
                                                        or output_id("fc"),
                                                        "call_id": call_id,
                                                        "name": func.get("name", ""),
                                                        "arguments": func.get(
                                                            "arguments", "{}"
                                                        ),
                                                        "status": "in_progress",
                                                        **(
                                                            {"thoughtSignature": tc_thought_sig}
                                                            if tc_thought_sig
                                                            else {}
                                                        ),
                                                        **(
                                                            {"thought_signature": tc_thought_sig}
                                                            if tc_thought_sig
                                                            else {}
                                                        ),
                                                    }
                                                )
                                            pending_output = output + pending_fc_items
                                            await event_emitter(
                                                {
                                                    "type": "chat:completion",
                                                    "data": {
                                                        "content": serialize_output(
                                                            pending_output
                                                        ),
                                                    },
                                                }
                                            )

                                    image_urls = get_image_urls(
                                        delta.get("images", []), request, metadata, user
                                    )
                                    if image_urls:
                                        message_files = Chats.add_message_files_by_id_and_message_id(
                                            metadata["chat_id"],
                                            metadata["message_id"],
                                            [
                                                {"type": "image", "url": url}
                                                for url in image_urls
                                            ],
                                        )

                                        await event_emitter(
                                            {
                                                "type": "files",
                                                "data": {"files": message_files},
                                            }
                                        )

                                    value = delta.get("content")

                                    reasoning_content = extract_reasoning_text(delta)
                                    if reasoning_content:
                                        if (
                                            not output
                                            or output[-1].get("type") != "reasoning"
                                        ):
                                            reasoning_item = {
                                                "type": "reasoning",
                                                "id": output_id("r"),
                                                "status": "in_progress",
                                                "start_tag": "<think>",
                                                "end_tag": "</think>",
                                                "attributes": {
                                                    "type": "reasoning_content"
                                                },
                                                "content": [],
                                                "summary": None,
                                                "started_at": time.time(),
                                            }
                                            output.append(reasoning_item)
                                        else:
                                            reasoning_item = output[-1]

                                        # Append to reasoning content
                                        parts = reasoning_item.get("content", [])
                                        if (
                                            parts
                                            and parts[-1].get("type") == "output_text"
                                        ):
                                            parts[-1]["text"] += reasoning_content
                                        else:
                                            reasoning_item["content"] = [
                                                {
                                                    "type": "output_text",
                                                    "text": reasoning_content,
                                                }
                                            ]

                                        data = {"content": serialize_output(output)}

                                    if value:
                                        if (
                                            output
                                            and output[-1].get("type") == "reasoning"
                                            and output[-1]
                                            .get("attributes", {})
                                            .get("type")
                                            == "reasoning_content"
                                        ):
                                            reasoning_item = output[-1]
                                            _finalize_timed_item(
                                                reasoning_item,
                                                ended_at=time.time(),
                                                mark_completed=True,
                                            )

                                            output.append(
                                                {
                                                    "type": "message",
                                                    "id": output_id("msg"),
                                                    "status": "in_progress",
                                                    "role": "assistant",
                                                    "content": [
                                                        {
                                                            "type": "output_text",
                                                            "text": "",
                                                        }
                                                    ],
                                                }
                                            )

                                        if ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION:
                                            value = convert_markdown_base64_images(
                                                request,
                                                value,
                                                {
                                                    "chat_id": metadata.get(
                                                        "chat_id", None
                                                    ),
                                                    "message_id": metadata.get(
                                                        "message_id", None
                                                    ),
                                                },
                                                user,
                                            )

                                        content = f"{content}{value}"

                                        # Check if we're inside a tag-based block
                                        # (reasoning, code_interpreter, or solution).
                                        # If so, append to the existing in-progress
                                        # item instead of creating a new message —
                                        # otherwise tag_output_handler re-detects the
                                        # start tag on every chunk and fragments the
                                        # output.
                                        last_item = output[-1] if output else None
                                        last_item_type = (
                                            last_item.get("type", "")
                                            if last_item
                                            else ""
                                        )
                                        inside_tag_block = (
                                            last_item is not None
                                            and last_item.get("status") == "in_progress"
                                            and last_item.get("attributes", {}).get(
                                                "type"
                                            )
                                            != "reasoning_content"
                                            and (
                                                last_item_type == "reasoning"
                                                or last_item_type
                                                == "open_webui:code_interpreter"
                                                or (
                                                    last_item_type == "message"
                                                    and last_item.get("_tag_type")
                                                    is not None
                                                )
                                            )
                                        )

                                        if inside_tag_block:
                                            # Append to the existing tag-based item
                                            if (
                                                last_item_type
                                                == "open_webui:code_interpreter"
                                            ):
                                                last_item["code"] = (
                                                    last_item.get("code", "") + value
                                                )
                                            elif last_item_type == "reasoning":
                                                parts = last_item.get("content", [])
                                                if (
                                                    parts
                                                    and parts[-1].get("type")
                                                    == "output_text"
                                                ):
                                                    parts[-1]["text"] += value
                                                else:
                                                    last_item["content"] = [
                                                        {
                                                            "type": "output_text",
                                                            "text": value,
                                                        }
                                                    ]
                                            else:
                                                # solution or other _tag_type message
                                                msg_parts = last_item.get("content", [])
                                                if (
                                                    msg_parts
                                                    and msg_parts[-1].get("type")
                                                    == "output_text"
                                                ):
                                                    msg_parts[-1]["text"] += value
                                                else:
                                                    last_item["content"] = [
                                                        {
                                                            "type": "output_text",
                                                            "text": value,
                                                        }
                                                    ]
                                        else:
                                            if (
                                                not output
                                                or output[-1].get("type") != "message"
                                            ):
                                                output.append(
                                                    {
                                                        "type": "message",
                                                        "id": output_id("msg"),
                                                        "status": "in_progress",
                                                        "role": "assistant",
                                                        "content": [
                                                            {
                                                                "type": "output_text",
                                                                "text": "",
                                                            }
                                                        ],
                                                    }
                                                )

                                            # Append value to last message item's text
                                            msg_parts = output[-1].get("content", [])
                                            if (
                                                msg_parts
                                                and msg_parts[-1].get("type")
                                                == "output_text"
                                            ):
                                                msg_parts[-1]["text"] += value
                                            else:
                                                output[-1]["content"] = [
                                                    {
                                                        "type": "output_text",
                                                        "text": value,
                                                    }
                                                ]

                                        if DETECT_REASONING_TAGS:
                                            output, _ = tag_output_handler(
                                                "reasoning",
                                                reasoning_tags,
                                                output,
                                            )

                                            output, _ = tag_output_handler(
                                                "solution",
                                                DEFAULT_SOLUTION_TAGS,
                                                output,
                                            )

                                        if DETECT_CODE_INTERPRETER:
                                            output, end = tag_output_handler(
                                                "code_interpreter",
                                                DEFAULT_CODE_INTERPRETER_TAGS,
                                                output,
                                            )

                                            if end:
                                                break

                                        if ENABLE_REALTIME_CHAT_SAVE:
                                            # Save message in the database
                                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                                metadata["chat_id"],
                                                metadata["message_id"],
                                                {
                                                    "content": serialize_output(output),
                                                    "output": output,
                                                },
                                            )
                                        else:
                                            data = {
                                                "content": serialize_output(output),
                                            }

                                if delta:
                                    delta_count += 1
                                    last_delta_data = data
                                    if delta_count >= delta_chunk_size:
                                        await flush_pending_delta_data(delta_chunk_size)
                                else:
                                    await event_emitter(
                                        {
                                            "type": "chat:completion",
                                            "data": data,
                                        }
                                    )
                        except Exception as e:
                            done = "data: [DONE]" in line
                            if done:
                                pass
                            else:
                                log.debug(f"Error: {e}")
                                continue
                    await flush_pending_delta_data()

                    if output:
                        # Clean up the last message item
                        if output[-1].get("type") == "message":
                            parts = output[-1].get("content", [])
                            if parts and parts[-1].get("type") == "output_text":
                                parts[-1]["text"] = parts[-1]["text"].strip()

                                if not parts[-1]["text"]:
                                    output.pop()

                                    if not output:
                                        output.append(
                                            {
                                                "type": "message",
                                                "id": output_id("msg"),
                                                "status": "in_progress",
                                                "role": "assistant",
                                                "content": [
                                                    {"type": "output_text", "text": ""}
                                                ],
                                            }
                                        )

                        if output[-1].get("type") == "reasoning":
                            reasoning_item = output[-1]
                            if reasoning_item.get("ended_at") is None:
                                _finalize_timed_item(
                                    reasoning_item,
                                    ended_at=time.time(),
                                    mark_completed=True,
                                )

                    if saw_responses_events:
                        output = normalize_reasoning_output_items(output)

                    if response_tool_calls:
                        tool_calls.append(response_tool_calls)

                    if response.background:
                        await response.background()

                await stream_body_handler(response, form_data)

                tool_call_retries = 0
                tool_call_sources = []  # Track citation sources from tool results
                all_tool_call_sources = []  # Accumulated sources across all iterations
                user_message = get_last_user_message(form_data["messages"])

                while (
                    len(tool_calls) > 0
                    and tool_call_retries < CHAT_RESPONSE_MAX_TOOL_CALL_RETRIES
                ):

                    tool_call_retries += 1

                    response_tool_calls = tool_calls.pop(0)

                    # Append function_call items for each tool call
                    for tc in response_tool_calls:
                        call_id = tc.get("id", "")
                        func = tc.get("function", {})
                        tc_thought_sig = (
                            tc.get("thoughtSignature")
                            or tc.get("thought_signature")
                            or func.get("thoughtSignature")
                            or func.get("thought_signature")
                        )
                        output.append(
                            {
                                "type": "function_call",
                                "id": call_id or output_id("fc"),
                                "call_id": call_id,
                                "name": func.get("name", ""),
                                "arguments": func.get("arguments", "{}"),
                                "status": "in_progress",
                                **(
                                    {"thoughtSignature": tc_thought_sig}
                                    if tc_thought_sig
                                    else {}
                                ),
                                **(
                                    {"thought_signature": tc_thought_sig}
                                    if tc_thought_sig
                                    else {}
                                ),
                            }
                        )

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_output(output),
                                "output": output,
                            },
                        }
                    )

                    tools = metadata.get("tools", {})

                    results = []

                    for tool_call in response_tool_calls:
                        tool_call_id = tool_call.get("id", "")
                        tool_function_name = tool_call.get("function", {}).get(
                            "name", ""
                        )
                        tool_args = tool_call.get("function", {}).get("arguments", "{}")

                        tool_function_params = {}
                        try:
                            # json.loads cannot be used because some models do not produce valid JSON
                            tool_function_params = ast.literal_eval(tool_args)
                        except Exception as e:
                            log.debug(e)
                            # Fallback to JSON parsing
                            try:
                                tool_function_params = json.loads(tool_args)
                            except Exception as e:
                                log.error(
                                    f"Error parsing tool call arguments: {tool_args}"
                                )

                        # Ensure arguments are valid JSON for downstream LLM integrations
                        log.debug(
                            f"Parsed args from {tool_args} to {tool_function_params}"
                        )
                        tool_call.setdefault("function", {})["arguments"] = json.dumps(
                            tool_function_params
                        )

                        tool_result = None
                        tool = None
                        tool_type = None
                        direct_tool = False

                        if tool_function_name in tools:
                            tool = tools[tool_function_name]
                            spec = tool.get("spec", {})

                            tool_type = tool.get("type", "")
                            direct_tool = tool.get("direct", False)

                            try:
                                allowed_params = (
                                    spec.get("parameters", {})
                                    .get("properties", {})
                                    .keys()
                                )

                                tool_function_params = {
                                    k: v
                                    for k, v in tool_function_params.items()
                                    if k in allowed_params
                                }

                                if direct_tool:
                                    tool_result = await event_caller(
                                        {
                                            "type": "execute:tool",
                                            "data": {
                                                "id": str(uuid4()),
                                                "name": tool_function_name,
                                                "params": tool_function_params,
                                                "server": tool.get("server", {}),
                                                "session_id": metadata.get(
                                                    "session_id", None
                                                ),
                                            },
                                        }
                                    )

                                else:
                                    tool_function = get_updated_tool_function(
                                        function=tool["callable"],
                                        extra_params={
                                            "__messages__": form_data.get(
                                                "messages", []
                                            ),
                                            "__files__": metadata.get("files", []),
                                        },
                                    )

                                    tool_result = await tool_function(
                                        **tool_function_params
                                    )

                            except Exception as e:
                                tool_result = str(e)

                        tool_result, tool_result_files, tool_result_embeds = (
                            process_tool_result(
                                request,
                                tool_function_name,
                                tool_result,
                                tool_type,
                                direct_tool,
                                metadata,
                                user,
                            )
                        )

                        # Extract citation sources from tool results
                        if (
                            tool_function_name
                            in [
                                "search_web",
                                "fetch_url",
                                "view_knowledge_file",
                                "query_knowledge_files",
                            ]
                            and tool_result
                        ):
                            try:
                                citation_sources = get_citation_source_from_tool_result(
                                    tool_name=tool_function_name,
                                    tool_params=tool_function_params,
                                    tool_result=tool_result,
                                    tool_id=tool.get("tool_id", "") if tool else "",
                                )
                                tool_call_sources.extend(citation_sources)
                            except Exception as e:
                                log.exception(f"Error extracting citation source: {e}")

                        results.append(
                            {
                                "tool_call_id": tool_call_id,
                                "content": tool_result or "",
                                **(
                                    {"files": tool_result_files}
                                    if tool_result_files
                                    else {}
                                ),
                                **(
                                    {"embeds": tool_result_embeds}
                                    if tool_result_embeds
                                    else {}
                                ),
                            }
                        )

                    # Update function_call statuses and append function_call_output items
                    for tc in response_tool_calls:
                        call_id = tc.get("id", "")
                        # Mark function_call as completed
                        for item in output:
                            if (
                                item.get("type") == "function_call"
                                and item.get("call_id") == call_id
                            ):
                                item["status"] = "completed"
                                # Update arguments with parsed/sanitized version
                                item["arguments"] = tc.get("function", {}).get(
                                    "arguments", "{}"
                                )
                                tc_thought_sig = (
                                    tc.get("thoughtSignature")
                                    or tc.get("thought_signature")
                                    or tc.get("function", {}).get("thoughtSignature")
                                    or tc.get("function", {}).get("thought_signature")
                                )
                                if tc_thought_sig:
                                    item["thoughtSignature"] = tc_thought_sig
                                    item["thought_signature"] = tc_thought_sig
                                break

                    for result in results:
                        output.append(
                            {
                                "type": "function_call_output",
                                "id": output_id("fco"),
                                "call_id": result.get("tool_call_id", ""),
                                "output": [
                                    {
                                        "type": "input_text",
                                        "text": result.get("content", ""),
                                    }
                                ],
                                "status": "completed",
                                **(
                                    {"files": result.get("files")}
                                    if result.get("files")
                                    else {}
                                ),
                                **(
                                    {"embeds": result.get("embeds")}
                                    if result.get("embeds")
                                    else {}
                                ),
                            }
                        )

                    # Append a new empty message item for the next response
                    output.append(
                        {
                            "type": "message",
                            "id": output_id("msg"),
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": ""}],
                        }
                    )

                    # Emit citation sources for UI display
                    for source in tool_call_sources:
                        await event_emitter({"type": "source", "data": source})

                    # Apply source context to messages for model
                    # Use metadata_only=True to avoid duplicating content
                    # that is already in the tool result message.
                    all_tool_call_sources.extend(tool_call_sources)
                    if all_tool_call_sources and user_message:
                        # Restore original user message before re-applying to avoid recursive nesting
                        form_data["messages"] = add_or_update_user_message(
                            user_message, form_data["messages"], append=False
                        )
                        form_data["messages"] = apply_source_context_to_messages(
                            request,
                            form_data["messages"],
                            all_tool_call_sources,
                            user_message,
                            include_content=False,
                        )
                    tool_call_sources.clear()

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_output(output),
                                "output": output,
                            },
                        }
                    )

                    try:
                        new_form_data = {
                            **form_data,
                            "model": model_id,
                            "stream": True,
                            "messages": [
                                *form_data["messages"],
                                *convert_output_to_messages(output, raw=True),
                            ],
                        }

                        try:
                            new_form_data, budget_diagnostics = (
                                await apply_context_budget_policy(
                                    request=request,
                                    form_data=new_form_data,
                                    user=user,
                                )
                            )
                            if budget_diagnostics.get("overflow"):
                                log.warning(
                                    "Context budget overflow during tool recursion "
                                    f"(chat_id={metadata.get('chat_id')}, message_id={metadata.get('message_id')})"
                                )
                        except Exception as budget_error:
                            log.debug(
                                f"Context budget policy failed during tool recursion: {budget_error}"
                            )

                        res = await generate_chat_completion(
                            request,
                            new_form_data,
                            user,
                            bypass_system_prompt=True,
                        )

                        if isinstance(res, StreamingResponse):
                            await stream_body_handler(res, new_form_data)
                        else:
                            break
                    except Exception as e:
                        log.debug(e)
                        break

                if DETECT_CODE_INTERPRETER:
                    MAX_RETRIES = 5
                    retries = 0

                    while (
                        output
                        and output[-1].get("type") == "open_webui:code_interpreter"
                        and retries < MAX_RETRIES
                    ):

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_output(output),
                                    "output": output,
                                },
                            }
                        )

                        retries += 1
                        log.debug(f"Attempt count: {retries}")

                        ci_item = output[-1]
                        ci_output = ""
                        try:
                            if ci_item.get("attributes", {}).get("type") == "code":
                                code = ci_item.get("code", "")
                                # Sanitize code (strips ANSI codes and markdown fences)
                                code = sanitize_code(code)

                                if CODE_INTERPRETER_BLOCKED_MODULES:
                                    blocking_code = textwrap.dedent(f"""
                                        import builtins
    
                                        BLOCKED_MODULES = {CODE_INTERPRETER_BLOCKED_MODULES}
    
                                        _real_import = builtins.__import__
                                        def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                                            if name.split('.')[0] in BLOCKED_MODULES:
                                                importer_name = globals.get('__name__') if globals else None
                                                if importer_name == '__main__':
                                                    raise ImportError(
                                                        f"Direct import of module {{name}} is restricted."
                                                    )
                                            return _real_import(name, globals, locals, fromlist, level)
    
                                        builtins.__import__ = restricted_import
                                    """)
                                    code = blocking_code + "\n" + code

                                if (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "pyodide"
                                ):
                                    ci_output = await event_caller(
                                        {
                                            "type": "execute:python",
                                            "data": {
                                                "id": str(uuid4()),
                                                "code": code,
                                                "session_id": metadata.get(
                                                    "session_id", None
                                                ),
                                            },
                                        }
                                    )
                                elif (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "jupyter"
                                ):
                                    ci_output = await execute_code_jupyter(
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_URL,
                                        code,
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "token"
                                            else None
                                        ),
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "password"
                                            else None
                                        ),
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT,
                                    )
                                else:
                                    ci_output = {
                                        "stdout": "Code interpreter engine not configured."
                                    }

                                log.debug(f"Code interpreter output: {ci_output}")

                                if isinstance(ci_output, dict):
                                    stdout = ci_output.get("stdout", "")

                                    if isinstance(stdout, str):
                                        stdoutLines = stdout.split("\n")
                                        for idx, line in enumerate(stdoutLines):

                                            if "data:image/png;base64" in line:
                                                image_url = get_image_url_from_base64(
                                                    request,
                                                    line,
                                                    metadata,
                                                    user,
                                                )
                                                if image_url:
                                                    stdoutLines[idx] = (
                                                        f"![Output Image]({image_url})"
                                                    )

                                        ci_output["stdout"] = "\n".join(stdoutLines)

                                    result = ci_output.get("result", "")

                                    if isinstance(result, str):
                                        resultLines = result.split("\n")
                                        for idx, line in enumerate(resultLines):
                                            if "data:image/png;base64" in line:
                                                image_url = get_image_url_from_base64(
                                                    request,
                                                    line,
                                                    metadata,
                                                    user,
                                                )
                                                resultLines[idx] = (
                                                    f"![Output Image]({image_url})"
                                                )
                                        ci_output["result"] = "\n".join(resultLines)
                        except Exception as e:
                            ci_output = str(e)

                        ci_item["output"] = ci_output
                        ci_item["status"] = "completed"

                        output.append(
                            {
                                "type": "message",
                                "id": output_id("msg"),
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": ""}],
                            }
                        )

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_output(output),
                                    "output": output,
                                },
                            }
                        )

                        try:
                            new_form_data = {
                                **form_data,
                                "model": model_id,
                                "stream": True,
                                "messages": [
                                    *form_data["messages"],
                                    *convert_output_to_messages(output, raw=True),
                                ],
                            }

                            try:
                                new_form_data, budget_diagnostics = (
                                    await apply_context_budget_policy(
                                        request=request,
                                        form_data=new_form_data,
                                        user=user,
                                    )
                                )
                                if budget_diagnostics.get("overflow"):
                                    log.warning(
                                        "Context budget overflow during code interpreter recursion "
                                        f"(chat_id={metadata.get('chat_id')}, message_id={metadata.get('message_id')})"
                                    )
                            except Exception as budget_error:
                                log.debug(
                                    f"Context budget policy failed during code interpreter recursion: {budget_error}"
                                )

                            res = await generate_chat_completion(
                                request,
                                new_form_data,
                                user,
                                bypass_system_prompt=True,
                            )

                            if isinstance(res, StreamingResponse):
                                await stream_body_handler(res, new_form_data)
                            else:
                                break
                        except Exception as e:
                            log.debug(e)
                            break

                # Mark all in-progress items as completed
                for item in output:
                    if item.get("status") == "in_progress":
                        item["status"] = "completed"

                title = Chats.get_chat_title_by_id(metadata["chat_id"])
                data = {
                    "done": True,
                    "content": serialize_output(output),
                    "output": output,
                    "title": title,
                }

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "content": serialize_output(output),
                            "output": output,
                            **({"usage": usage} if usage else {}),
                        },
                    )
                elif usage:
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {"usage": usage},
                    )

                # Send a webhook notification if the user is not active
                if not Users.is_user_active(user.id):
                    webhook_url = Users.get_user_webhook_url_by_id(user.id)
                    if webhook_url:
                        await post_webhook(
                            request.app.state.WEBUI_NAME,
                            webhook_url,
                            f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{content}",
                            {
                                "action": "chat",
                                "message": content,
                                "title": title,
                                "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                            },
                        )

                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": data,
                    }
                )

                await background_tasks_handler(ctx)
            except asyncio.CancelledError:
                log.warning("Task was cancelled!")
                await event_emitter({"type": "chat:tasks:cancel"})

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "content": serialize_output(output),
                            "output": output,
                        },
                    )

            if response.background is not None:
                await response.background()

        return await response_handler(response, events)

    else:
        # Fallback to the original response
        async def stream_wrapper(original_generator, events):
            def wrap_item(item):
                return f"data: {item}\n\n"

            for event in events:
                event, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=event,
                    extra_params=extra_params,
                )

                if event:
                    yield wrap_item(json.dumps(event))

            async for data in original_generator:
                data, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=data,
                    extra_params=extra_params,
                )

                if data:
                    yield data

        return StreamingResponse(
            stream_wrapper(response.body_iterator, events),
            headers=dict(response.headers),
            background=response.background,
        )


async def process_chat_response(response, ctx):
    # Non-streaming response
    if not isinstance(response, StreamingResponse):
        return await non_streaming_chat_response_handler(response, ctx)

    # Non standard response
    if not any(
        content_type in response.headers["Content-Type"]
        for content_type in ["text/event-stream", "application/x-ndjson"]
    ):
        return response

    # Streaming response
    return await streaming_chat_response_handler(response, ctx)
