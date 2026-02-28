import json
import logging
import re
from typing import Any, Literal, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from open_webui.models.memory_action_logs import MemoryActionLogs
from open_webui.models.users import UserModel
from open_webui.routers.memories import (
    AddMemoryForm,
    MemoryUpdateModel,
    QueryMemoryForm,
    add_memory,
    delete_memory_by_id,
    query_memory,
    update_memory_by_id,
)
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.misc import get_content_from_message, get_last_user_message
from open_webui.utils.task import get_task_model_id

log = logging.getLogger(__name__)

AUTO_MEMORY_SYSTEM_PROMPT = """You are a memory manager for a chat assistant.

Decide memory actions from the conversation and related memories.

Rules:
1. Focus on the latest user message.
2. Keep memories as atomic facts.
3. Prefer UPDATE over creating conflicting memories.
4. Use DELETE for explicit forget requests, exact duplicates, or direct conflicts.
5. Ignore temporary/ephemeral facts, jokes, sarcasm, and one-off task content.
6. Preserve self-contained phrasing (avoid unresolved pronouns).
7. You may do moderate maintenance on obvious duplicate/conflicting related memories.

Return strict JSON with this shape only:
{
  "actions": [
    {"action": "add", "content": "string"},
    {"action": "update", "id": "memory-id", "new_content": "string"},
    {"action": "delete", "id": "memory-id"}
  ]
}
"""


class RelatedMemory(BaseModel):
    mem_id: str
    content: str
    created_at: int
    updated_at: int
    similarity_score: Optional[float] = None


class MemoryAddAction(BaseModel):
    action: Literal["add"]
    content: str


class MemoryUpdateAction(BaseModel):
    action: Literal["update"]
    id: str
    new_content: str


class MemoryDeleteAction(BaseModel):
    action: Literal["delete"]
    id: str


class MemoryActionRequest(BaseModel):
    actions: list[MemoryAddAction | MemoryUpdateAction | MemoryDeleteAction] = Field(
        default_factory=list,
        max_length=30,
    )


async def emit_memory_writeback_status(
    event_emitter: Any,
    description: str,
    *,
    done: bool,
    error: bool = False,
    extra_data: Optional[dict[str, Any]] = None,
):
    if not event_emitter:
        return

    data = {
        "action": "memory_writeback",
        "description": description,
        "done": done,
        "error": error,
    }
    if extra_data:
        data.update(extra_data)

    await event_emitter({"type": "status", "data": data})


def normalize_text(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def normalize_similarity(raw_similarity: Any) -> float:
    try:
        score = float(raw_similarity)
    except (TypeError, ValueError):
        return 0.0

    if score <= 0:
        return 0.0
    if score <= 1:
        return score
    if score <= 2:
        return score / 2.0
    return 1.0


def get_message_text(message: dict) -> str:
    return normalize_text(get_content_from_message(message))


def stringify_conversation(messages: list[dict], messages_to_consider: int) -> str:
    if messages_to_consider <= 0:
        return ""

    filtered = [
        {"role": msg.get("role", "assistant"), "content": get_message_text(msg)}
        for msg in messages
        if msg.get("role") in {"user", "assistant", "system"}
    ]
    filtered = [msg for msg in filtered if msg["content"]]
    window = filtered[-messages_to_consider:]

    lines: list[str] = []
    base = len(window)
    for idx, message in enumerate(window, start=1):
        rel_index = idx - (base + 1)
        content = message["content"].replace("```", "'''")
        lines.append(f"{rel_index}. {message['role']}: ```{content}```")

    return "\n".join(lines)


def build_memory_query(messages: list[dict]) -> str:
    query_parts: list[str] = []

    last_user_idx = None
    last_user_message = None
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get("role") == "user":
            last_user_idx = idx
            last_user_message = get_message_text(messages[idx])
            break

    if not last_user_message or last_user_idx is None:
        return ""

    if last_user_idx + 1 < len(messages):
        last_assistant_message = get_message_text(messages[last_user_idx + 1])
        if last_assistant_message:
            query_parts.append(f"Assistant: {last_assistant_message}")

    query_parts.append(f"User: {last_user_message}")

    if len(last_user_message.split()) <= 8 and last_user_idx > 0:
        prev_message = messages[last_user_idx - 1]
        if prev_message.get("role") == "assistant":
            prev_assistant_message = get_message_text(prev_message)
            if prev_assistant_message:
                query_parts.append(f"Assistant: {prev_assistant_message}")

    query_parts.reverse()
    return "\n".join(query_parts)


def parse_related_memories(results: Any) -> list[RelatedMemory]:
    if results is None:
        return []

    if hasattr(results, "model_dump"):
        results = results.model_dump()
    elif hasattr(results, "dict"):
        results = results.dict()

    if not isinstance(results, dict):
        return []

    ids_batches = results.get("ids") or []
    docs_batches = results.get("documents") or []
    metas_batches = results.get("metadatas") or []
    distances_batches = results.get("distances") or []

    memories: list[RelatedMemory] = []
    seen_ids: set[str] = set()

    for batch_idx, ids_batch in enumerate(ids_batches):
        docs_batch = docs_batches[batch_idx] if batch_idx < len(docs_batches) else []
        metas_batch = metas_batches[batch_idx] if batch_idx < len(metas_batches) else []
        distances_batch = (
            distances_batches[batch_idx] if batch_idx < len(distances_batches) else []
        )

        for idx, memory_id in enumerate(ids_batch):
            if memory_id in seen_ids:
                continue

            content = (
                normalize_text(docs_batch[idx])
                if idx < len(docs_batch) and docs_batch[idx] is not None
                else ""
            )
            if not content:
                continue

            metadata = (
                metas_batch[idx]
                if idx < len(metas_batch) and isinstance(metas_batch[idx], dict)
                else {}
            )
            created_at = int(metadata.get("created_at") or 0)
            updated_at = int(metadata.get("updated_at") or created_at or 0)
            similarity_score = normalize_similarity(
                distances_batch[idx] if idx < len(distances_batch) else None
            )

            memories.append(
                RelatedMemory(
                    mem_id=memory_id,
                    content=content,
                    created_at=created_at,
                    updated_at=updated_at,
                    similarity_score=similarity_score,
                )
            )
            seen_ids.add(memory_id)

    memories.sort(
        key=lambda mem: (
            mem.similarity_score if mem.similarity_score is not None else 0.0,
            mem.updated_at or mem.created_at,
        ),
        reverse=True,
    )

    return memories


async def get_related_memories(
    request: Request,
    user: UserModel,
    messages: list[dict],
    related_memories_n: int,
    minimum_similarity: Optional[float],
) -> list[RelatedMemory]:
    query = build_memory_query(messages)
    if not query:
        return []

    try:
        results = await query_memory(
            request=request,
            form_data=QueryMemoryForm(content=query, k=max(1, related_memories_n)),
            user=user,
        )
    except HTTPException as e:
        if e.status_code == 404:
            return []
        raise

    memories = parse_related_memories(results)
    if minimum_similarity is None:
        return memories

    return [
        memory
        for memory in memories
        if memory.similarity_score is not None
        and memory.similarity_score >= minimum_similarity
    ]


def extract_content_from_response(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            return normalize_text(
                message.get("content") or message.get("reasoning_content") or ""
            )
        return ""

    if hasattr(response, "body") and response.body:
        body = response.body
        if isinstance(body, bytes):
            try:
                payload = json.loads(body.decode("utf-8", "replace"))
                return extract_content_from_response(payload)
            except json.JSONDecodeError:
                return ""

    return ""


def extract_json_payload(raw_content: str) -> dict[str, Any]:
    content = normalize_text(raw_content)
    if not content:
        raise ValueError("empty planner response")

    try:
        payload = json.loads(content)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", raw_content, flags=re.I)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in planner response")

    return json.loads(content[start : end + 1])


def resolve_planner_model_id(
    request: Request, default_model_id: Optional[str], override_model: Optional[str]
) -> str:
    if default_model_id and getattr(request.state, "direct", False):
        return default_model_id

    models = request.app.state.MODELS
    if not models:
        raise ValueError("no available models to run auto memory planner")

    if override_model and override_model in models:
        return override_model

    if default_model_id and default_model_id in models:
        return get_task_model_id(
            default_model_id,
            request.app.state.config.TASK_MODEL,
            request.app.state.config.TASK_MODEL_EXTERNAL,
            models,
        )

    for fallback_model in (
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
    ):
        if fallback_model and fallback_model in models:
            return fallback_model

    return next(iter(models.keys()))


def sanitize_actions(
    actions: list[MemoryAddAction | MemoryUpdateAction | MemoryDeleteAction],
    valid_memory_ids: set[str],
    max_actions: int,
) -> list[dict[str, str]]:
    sanitized_actions: list[dict[str, str]] = []

    for action in actions:
        if len(sanitized_actions) >= max_actions:
            break

        if action.action == "add":
            content = normalize_text(action.content)
            if content:
                sanitized_actions.append({"action": "add", "content": content})
            continue

        if action.action == "update":
            if action.id not in valid_memory_ids:
                continue
            new_content = normalize_text(action.new_content)
            if new_content:
                sanitized_actions.append(
                    {"action": "update", "id": action.id, "new_content": new_content}
                )
            continue

        if action.action == "delete":
            if action.id in valid_memory_ids:
                sanitized_actions.append({"action": "delete", "id": action.id})

    return sanitized_actions


def summarize_action_counts(counts: dict[str, int], planned_count: int) -> str:
    applied_total = counts["add"] + counts["update"] + counts["delete"]
    if planned_count == 0:
        return "No memory updates needed"
    if applied_total == 0:
        return "No memory actions were applied"

    chunks: list[str] = []
    if counts["add"] > 0:
        chunks.append(f"saved {counts['add']}")
    if counts["update"] > 0:
        chunks.append(f"updated {counts['update']}")
    if counts["delete"] > 0:
        chunks.append(f"deleted {counts['delete']}")

    return "Memory writeback: " + ", ".join(chunks)


async def apply_memory_actions(
    request: Request,
    user: UserModel,
    actions: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    executed: list[dict[str, Any]] = []
    counts = {"add": 0, "update": 0, "delete": 0}

    ordered_actions = [
        *[action for action in actions if action["action"] == "delete"],
        *[action for action in actions if action["action"] == "update"],
        *[action for action in actions if action["action"] == "add"],
    ]

    for action in ordered_actions:
        try:
            if action["action"] == "delete":
                await delete_memory_by_id(
                    memory_id=action["id"],
                    request=request,
                    user=user,
                    db=None,
                )
                counts["delete"] += 1
                executed.append({**action, "status": "applied"})
                continue

            if action["action"] == "update":
                await update_memory_by_id(
                    memory_id=action["id"],
                    request=request,
                    form_data=MemoryUpdateModel(content=action["new_content"]),
                    user=user,
                )
                counts["update"] += 1
                executed.append({**action, "status": "applied"})
                continue

            if action["action"] == "add":
                memory = await add_memory(
                    request=request,
                    form_data=AddMemoryForm(content=action["content"]),
                    user=user,
                )
                counts["add"] += 1
                executed.append(
                    {
                        **action,
                        "status": "applied",
                        "id": memory.id if memory else None,
                    }
                )
        except Exception as e:
            executed.append({**action, "status": "failed", "error": str(e)})

    return executed, counts


async def run_auto_memory_writeback(
    *,
    request: Request,
    user: UserModel,
    metadata: dict,
    form_data: dict,
    messages: list[dict],
    event_emitter: Any = None,
) -> None:
    config = request.app.state.config

    if not config.ENABLE_MEMORIES:
        return
    if not getattr(config, "MEMORY_AUTO_WRITEBACK_ENABLED", False):
        return
    if not messages:
        return
    if str(metadata.get("chat_id", "")).startswith("local:"):
        return

    features = metadata.get("features")
    if not isinstance(features, dict):
        return

    if not features.get("memory"):
        return

    auto_memory_feature = features.get("auto_memory")
    if auto_memory_feature is False:
        return

    latest_user_message = normalize_text(get_last_user_message(messages))
    if not latest_user_message:
        return

    show_status = bool(getattr(config, "MEMORY_AUTO_WRITEBACK_SHOW_STATUS", True))
    min_user_message_chars = max(
        1, int(getattr(config, "MEMORY_AUTO_WRITEBACK_MIN_USER_MESSAGE_CHARS", 6))
    )
    if len(latest_user_message) < min_user_message_chars:
        MemoryActionLogs.insert_log(
            user_id=user.id,
            chat_id=metadata.get("chat_id"),
            message_id=metadata.get("message_id"),
            status="skipped",
            trigger_message=latest_user_message,
            error=(
                "latest user message is shorter than "
                f"min chars ({min_user_message_chars})"
            ),
        )
        return

    related_memories_n = max(
        1, int(getattr(config, "MEMORY_AUTO_WRITEBACK_RELATED_MEMORIES_N", 5))
    )
    messages_to_consider = max(
        2, int(getattr(config, "MEMORY_AUTO_WRITEBACK_MESSAGES_TO_CONSIDER", 6))
    )
    max_actions = max(1, int(getattr(config, "MEMORY_AUTO_WRITEBACK_MAX_ACTIONS", 6)))
    raw_minimum_similarity = getattr(
        config, "MEMORY_AUTO_WRITEBACK_MIN_SIMILARITY", None
    )
    try:
        minimum_similarity = (
            float(raw_minimum_similarity)
            if raw_minimum_similarity not in [None, ""]
            else None
        )
    except (TypeError, ValueError):
        minimum_similarity = None
    if minimum_similarity is not None:
        minimum_similarity = max(0.0, min(1.0, minimum_similarity))
    planner_model_override = normalize_text(
        getattr(config, "MEMORY_AUTO_WRITEBACK_MODEL", "")
    )

    default_model_id = form_data.get("model") or metadata.get("model_id")
    planner_model_id = resolve_planner_model_id(
        request, default_model_id, planner_model_override or None
    )

    if show_status:
        await emit_memory_writeback_status(
            event_emitter,
            "Planning memory updates",
            done=False,
        )

    related_memories = await get_related_memories(
        request=request,
        user=user,
        messages=messages,
        related_memories_n=related_memories_n,
        minimum_similarity=minimum_similarity,
    )

    related_payload = [
        {
            "mem_id": memory.mem_id,
            "created_at": memory.created_at,
            "updated_at": memory.updated_at,
            "content": memory.content,
            "similarity_score": memory.similarity_score,
        }
        for memory in related_memories
    ]
    conversation_snippet = stringify_conversation(messages, messages_to_consider)

    planner_input = (
        f"Conversation snippet:\n{conversation_snippet}\n\n"
        f"Related memories:\n{json.dumps(related_payload, ensure_ascii=False)}\n\n"
        f"Hard limit: at most {max_actions} actions."
    )

    planned_actions: list[dict[str, str]] = []
    executed_actions: list[dict[str, Any]] = []
    counts = {"add": 0, "update": 0, "delete": 0}

    try:
        planner_payload = {
            "model": planner_model_id,
            "messages": [
                {"role": "system", "content": AUTO_MEMORY_SYSTEM_PROMPT},
                {"role": "user", "content": planner_input},
            ],
            "stream": False,
            "metadata": {
                "task": "memory_auto_writeback",
                "chat_id": metadata.get("chat_id"),
                "message_id": metadata.get("message_id"),
            },
        }

        response = await generate_chat_completion(
            request,
            form_data=planner_payload,
            user=user,
            bypass_system_prompt=True,
        )
        planner_response_text = extract_content_from_response(response)
        planner_response_payload = extract_json_payload(planner_response_text)
        action_plan = MemoryActionRequest.model_validate(planner_response_payload)

        planned_actions = sanitize_actions(
            action_plan.actions,
            valid_memory_ids={memory.mem_id for memory in related_memories},
            max_actions=max_actions,
        )

        executed_actions, counts = await apply_memory_actions(
            request=request,
            user=user,
            actions=planned_actions,
        )

        status_description = summarize_action_counts(counts, len(planned_actions))
        if show_status:
            await emit_memory_writeback_status(
                event_emitter,
                status_description,
                done=True,
                extra_data={
                    "planned_count": len(planned_actions),
                    "applied_count": counts["add"] + counts["update"] + counts["delete"],
                    "added_count": counts["add"],
                    "updated_count": counts["update"],
                    "deleted_count": counts["delete"],
                },
            )

        MemoryActionLogs.insert_log(
            user_id=user.id,
            chat_id=metadata.get("chat_id"),
            message_id=metadata.get("message_id"),
            status="completed",
            planner_model=planner_model_id,
            trigger_message=latest_user_message,
            planned_actions=planned_actions,
            executed_actions=executed_actions,
        )
    except (ValidationError, ValueError, json.JSONDecodeError) as e:
        log.warning(f"auto memory planner parsing failed: {e}")
        if show_status:
            await emit_memory_writeback_status(
                event_emitter,
                "Memory writeback skipped due to invalid planner response",
                done=True,
                error=True,
            )

        MemoryActionLogs.insert_log(
            user_id=user.id,
            chat_id=metadata.get("chat_id"),
            message_id=metadata.get("message_id"),
            status="failed",
            planner_model=planner_model_id,
            trigger_message=latest_user_message,
            planned_actions=planned_actions,
            executed_actions=executed_actions,
            error=str(e),
        )
    except Exception as e:
        log.exception(f"auto memory writeback failed: {e}")
        if show_status:
            await emit_memory_writeback_status(
                event_emitter,
                "Memory writeback failed",
                done=True,
                error=True,
            )

        MemoryActionLogs.insert_log(
            user_id=user.id,
            chat_id=metadata.get("chat_id"),
            message_id=metadata.get("message_id"),
            status="failed",
            planner_model=planner_model_id,
            trigger_message=latest_user_message,
            planned_actions=planned_actions,
            executed_actions=executed_actions,
            error=str(e),
        )
