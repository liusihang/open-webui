from __future__ import annotations

import json
import logging
import re
import time
from types import SimpleNamespace
from typing import Any

from open_webui.internal.db import get_async_db_context
from open_webui.models.agent_memories import (
    AgentMemoryConsolidationJob,
    AgentMemoryConsolidationJobs,
    AgentMemoryExtractionCache,
    AgentMemoryExtractionCacheModel,
    AgentMemoryExtractionCaches,
    AgentMemoryExtractionJob,
    AgentMemoryExtractionJobModel,
    AgentMemoryExtractionJobs,
)
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat
from open_webui.models.folders import Folder
from open_webui.utils.access_control import has_permission
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.task import get_task_model_id
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

EXTRACTION_RESPONSE_KEYS = {"raw_memory", "rollout_summary", "rollout_slug"}
MAX_ERROR_CHARS = 240
DEFAULT_MAX_RETRIES = 3
DEFAULT_SANITIZED_INPUT_CHARS = 12000


class AgentMemoryExtractionContractError(ValueError):
    pass


def _config_value(config: Any, key: str, default: Any) -> Any:
    value = getattr(config, key, default)
    return default if value in (None, "") else value


def _is_agent_memory_disabled(meta: dict | None) -> bool:
    agent_memory = (meta or {}).get("agent_memory") or {}
    return bool(agent_memory.get("disabled"))


def _is_persistent_chat_id(chat_id: str | None) -> bool:
    return bool(chat_id) and not chat_id.startswith("local:") and not chat_id.startswith("channel:")


def _is_copied_chat(chat: Chat) -> bool:
    chat_data = chat.chat or {}
    return bool(chat_data.get("originalChatId") or chat_data.get("branchPointMessageId"))


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    return str(content)


def _source_updated_at(chat: Chat, messages: list[ChatMessage]) -> int:
    timestamps = [chat.updated_at or 0]
    for message in messages:
        timestamps.append(message.updated_at or message.created_at or 0)
    return max(timestamps)


def _has_completed_user_assistant_exchange(messages: list[ChatMessage]) -> bool:
    has_user = any(message.role == "user" and _content_text(message.content).strip() for message in messages)
    completed_assistant = [
        message
        for message in messages
        if message.role == "assistant"
        and bool(message.done)
        and not message.error
        and (_content_text(message.content).strip() or message.output)
    ]
    return has_user and bool(completed_assistant)


def _latest_assistant_is_completed(messages: list[ChatMessage]) -> bool:
    assistant_messages = [message for message in messages if message.role == "assistant"]
    if not assistant_messages:
        return False
    latest = max(assistant_messages, key=lambda message: message.created_at or 0)
    return bool(latest.done) and not latest.error


async def _load_chat_messages(
    chat_id: str,
    db: AsyncSession,
) -> tuple[Chat | None, list[ChatMessage]]:
    chat = await db.get(Chat, chat_id)
    if chat is None:
        return None, []
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.chat_id == chat_id).order_by(ChatMessage.created_at.asc())
    )
    return chat, list(result.scalars().all())


async def _folder_for_chat(chat: Chat, db: AsyncSession) -> Folder | None:
    if not chat.folder_id:
        return None
    result = await db.execute(
        select(Folder).where(Folder.id == chat.folder_id).where(Folder.user_id == chat.user_id)
    )
    return result.scalars().first()


async def _user_can_use_agent_memory(user_id: str, config: Any, db: AsyncSession) -> bool:
    if not bool(_config_value(config, "ENABLE_AGENT_MEMORY", False)):
        return False
    return await has_permission(
        user_id,
        "features.agent_memory",
        _config_value(config, "USER_PERMISSIONS", {}),
        db=db,
    )


async def enqueue_chat_extraction_if_needed(
    chat_id: str,
    config: Any,
    now: int | None = None,
    db: AsyncSession | None = None,
    require_idle: bool = True,
) -> bool:
    now = int(now or time.time())
    if not _is_persistent_chat_id(chat_id):
        return False

    async with get_async_db_context(db) as session:
        chat, messages = await _load_chat_messages(chat_id, session)
        if chat is None or not await _is_chat_eligible(
            chat,
            messages,
            config,
            now,
            session,
            require_idle=require_idle,
        ):
            return False

        source_updated_at = _source_updated_at(chat, messages)
        cache = await AgentMemoryExtractionCaches.get_cache(chat.user_id, chat.id, db=session)
        if cache and cache.source_updated_at >= source_updated_at and cache.status in {
            "succeeded",
            "succeeded_no_output",
        }:
            return False

        if cache and cache.status in {"succeeded", "succeeded_no_output"}:
            await AgentMemoryExtractionCaches.upsert_cache(
                user_id=cache.user_id,
                chat_id=cache.chat_id,
                source_updated_at=cache.source_updated_at,
                raw_memory=cache.raw_memory,
                rollout_summary=cache.rollout_summary,
                rollout_slug=cache.rollout_slug,
                generated_at=cache.generated_at,
                status="stale",
                db=session,
            )

        idle_seconds = int(_config_value(config, "AGENT_MEMORY_IDLE_THRESHOLD_SECONDS", 900))
        retry_at = None
        if not require_idle and chat.updated_at and chat.updated_at > now - idle_seconds:
            retry_at = chat.updated_at + idle_seconds

        existing_job = await AgentMemoryExtractionJobs.get_job(chat.user_id, chat.id, db=session)
        if existing_job and not _job_can_be_requeued(existing_job, now):
            if existing_job.status == "failed":
                return False
            if existing_job.status == "queued" and existing_job.retry_at != retry_at:
                await AgentMemoryExtractionJobs.upsert_job(
                    user_id=chat.user_id,
                    chat_id=chat.id,
                    status="queued",
                    lease_until=None,
                    retry_at=retry_at,
                    retry_count=existing_job.retry_count,
                    last_error=existing_job.last_error,
                    updated_at=now,
                    db=session,
                )
            return True

        await AgentMemoryExtractionJobs.upsert_job(
            user_id=chat.user_id,
            chat_id=chat.id,
            status="queued",
            lease_until=None,
            retry_at=retry_at,
            retry_count=0,
            last_error=None,
            updated_at=now,
            db=session,
        )
        return True


async def enqueue_idle_chats_for_extraction(
    config: Any,
    now: int | None = None,
    limit: int = 10,
    db: AsyncSession | None = None,
) -> list[str]:
    now = int(now or time.time())
    if limit <= 0:
        return []

    async with get_async_db_context(db) as session:
        idle_seconds = int(_config_value(config, "AGENT_MEMORY_IDLE_THRESHOLD_SECONDS", 900))
        candidate_limit = max(limit * 3, limit)
        result = await session.execute(
            select(Chat.id)
            .where(Chat.updated_at <= now - idle_seconds)
            .where(~Chat.id.like("local:%"))
            .where(~Chat.id.like("channel:%"))
            .order_by(Chat.updated_at.asc(), Chat.id.asc())
            .limit(candidate_limit)
        )
        candidate_ids = list(result.scalars().all())

        enqueued: list[str] = []
        for chat_id in candidate_ids:
            if len(enqueued) >= limit:
                break
            if await enqueue_chat_extraction_if_needed(chat_id, config=config, now=now, db=session):
                enqueued.append(chat_id)
        return enqueued


async def enqueue_startup_agent_memory_backlog(
    app: Any,
    now: int | None = None,
    db: AsyncSession | None = None,
) -> list[str]:
    config = app.state.config
    if not bool(_config_value(config, "ENABLE_AGENT_MEMORY", False)):
        return []
    limit = int(_config_value(config, "AGENT_MEMORY_STARTUP_CLAIM_LIMIT", 0))
    if limit <= 0:
        return []
    try:
        return await enqueue_idle_chats_for_extraction(config=config, now=now, limit=limit, db=db)
    except Exception:
        log.exception("Failed to enqueue Agent Memory startup backlog")
        return []


async def _is_chat_eligible(
    chat: Chat,
    messages: list[ChatMessage],
    config: Any,
    now: int,
    db: AsyncSession,
    require_idle: bool = True,
) -> bool:
    if not _is_persistent_chat_id(chat.id):
        return False
    if _is_copied_chat(chat):
        return False
    if _is_agent_memory_disabled(chat.meta):
        return False
    if not await _user_can_use_agent_memory(chat.user_id, config, db):
        return False
    folder = await _folder_for_chat(chat, db)
    if folder and _is_agent_memory_disabled(folder.meta):
        return False
    idle_seconds = int(_config_value(config, "AGENT_MEMORY_IDLE_THRESHOLD_SECONDS", 900))
    if require_idle and chat.updated_at and chat.updated_at > now - idle_seconds:
        return False
    if not _has_completed_user_assistant_exchange(messages):
        return False
    return _latest_assistant_is_completed(messages)


def _job_can_be_requeued(job: AgentMemoryExtractionJobModel, now: int) -> bool:
    if job.status == "failed":
        return False
    if job.status == "retry" and job.retry_at is not None and job.retry_at <= now:
        return True
    if job.status == "leased" and job.lease_until is not None and job.lease_until <= now:
        return True
    return False


def _cache_is_fresh(cache: AgentMemoryExtractionCacheModel | None, source_updated_at: int) -> bool:
    return bool(
        cache
        and cache.status in {"succeeded", "succeeded_no_output"}
        and cache.source_updated_at >= source_updated_at
    )


async def claim_extraction_jobs(
    now: int | None = None,
    limit: int = 5,
    lease_seconds: int = 300,
    db: AsyncSession | None = None,
) -> list[AgentMemoryExtractionJobModel]:
    now = int(now or time.time())
    if limit <= 0:
        return []

    async with get_async_db_context(db) as session:
        availability = or_(
            (AgentMemoryExtractionJob.status == "queued")
            & or_(AgentMemoryExtractionJob.retry_at.is_(None), AgentMemoryExtractionJob.retry_at <= now),
            (AgentMemoryExtractionJob.status == "retry")
            & or_(AgentMemoryExtractionJob.retry_at.is_(None), AgentMemoryExtractionJob.retry_at <= now),
            (AgentMemoryExtractionJob.status == "leased") & (AgentMemoryExtractionJob.lease_until <= now),
        )
        status_order = case(
            (AgentMemoryExtractionJob.status == "queued", 0),
            (AgentMemoryExtractionJob.status == "retry", 1),
            else_=2,
        )
        stmt = (
            select(AgentMemoryExtractionJob)
            .where(availability)
            .order_by(
                status_order,
                func.coalesce(AgentMemoryExtractionJob.lease_until, 0).desc(),
                AgentMemoryExtractionJob.updated_at.asc(),
                AgentMemoryExtractionJob.chat_id.asc(),
            )
            .limit(limit)
        )
        if session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        claimed_keys: list[tuple[str, str]] = []
        for row in rows:
            claim_result = await session.execute(
                update(AgentMemoryExtractionJob)
                .where(AgentMemoryExtractionJob.user_id == row.user_id)
                .where(AgentMemoryExtractionJob.chat_id == row.chat_id)
                .where(availability)
                .values(
                    status="leased",
                    lease_until=now + lease_seconds,
                    retry_at=None,
                    updated_at=now,
                )
            )
            if claim_result.rowcount:
                claimed_keys.append((row.user_id, row.chat_id))

        await session.commit()
        claimed_rows = []
        for key in claimed_keys:
            claimed_row = await session.get(AgentMemoryExtractionJob, key)
            if claimed_row is not None:
                claimed_rows.append(AgentMemoryExtractionJobModel.model_validate(claimed_row))
        return claimed_rows


async def record_extraction_failure(
    user_id: str,
    chat_id: str,
    error: Exception,
    now: int | None = None,
    max_retries: int = 3,
    retry_backoff_seconds: int = 600,
    expected_lease_until: int | None = None,
    db: AsyncSession | None = None,
) -> AgentMemoryExtractionJobModel | None:
    now = int(now or time.time())
    async with get_async_db_context(db) as session:
        row = await session.get(AgentMemoryExtractionJob, (user_id, chat_id))
        if row is None:
            return None
        if row.status != "leased":
            return None
        if expected_lease_until is not None and row.lease_until != expected_lease_until:
            return None

        retry_count = int(row.retry_count or 0) + 1
        if retry_count >= max_retries:
            status = "failed"
            retry_at = None
        else:
            status = "retry"
            retry_at = now + retry_backoff_seconds

        stmt = (
            update(AgentMemoryExtractionJob)
            .where(AgentMemoryExtractionJob.user_id == user_id)
            .where(AgentMemoryExtractionJob.chat_id == chat_id)
            .where(AgentMemoryExtractionJob.status == "leased")
            .values(
                status=status,
                retry_count=retry_count,
                last_error=str(error)[:MAX_ERROR_CHARS],
                lease_until=None,
                retry_at=retry_at,
                updated_at=now,
            )
        )
        if expected_lease_until is not None:
            stmt = stmt.where(AgentMemoryExtractionJob.lease_until == expected_lease_until)
        result = await session.execute(stmt)
        if not result.rowcount:
            await session.rollback()
            return None
        await session.commit()
        updated_row = await session.get(AgentMemoryExtractionJob, (user_id, chat_id))
        return AgentMemoryExtractionJobModel.model_validate(updated_row) if updated_row else None


def parse_extraction_response(value: str | dict[str, Any]) -> dict[str, str | None]:
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except Exception as exc:
            raise AgentMemoryExtractionContractError("extraction response must be valid JSON") from exc
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        raise AgentMemoryExtractionContractError("extraction response must be a JSON object")

    if not isinstance(payload, dict):
        raise AgentMemoryExtractionContractError("extraction response must be a JSON object")
    if set(payload.keys()) != EXTRACTION_RESPONSE_KEYS:
        raise AgentMemoryExtractionContractError("extraction response keys must match the contract exactly")
    if not isinstance(payload["raw_memory"], str):
        raise AgentMemoryExtractionContractError("raw_memory must be a string")
    if not isinstance(payload["rollout_summary"], str):
        raise AgentMemoryExtractionContractError("rollout_summary must be a string")
    if payload["rollout_slug"] is not None and not isinstance(payload["rollout_slug"], str):
        raise AgentMemoryExtractionContractError("rollout_slug must be a string or null")
    return {
        "raw_memory": payload["raw_memory"],
        "rollout_summary": payload["rollout_summary"],
        "rollout_slug": payload["rollout_slug"],
    }


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\b(password|passwd|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"),
]
DETAILS_PATTERN = re.compile(r"<details\b[^>]*>.*?</details>", re.S | re.I)
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*]\([^)]*\)")
DATA_URL_PATTERN = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+")
URL_PATTERN = re.compile(r"https?://[^\s)]+")
TEMP_PATH_PATTERN = re.compile(r"(?:(?:/private)?/tmp|/var/folders)/[^\s]+")
BASE64_BLOB_PATTERN = re.compile(r"\b[A-Za-z0-9+/]{120,}={0,2}\b")


def redact_agent_memory_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def sanitize_agent_memory_text(text: str) -> str:
    return _sanitize_text(text)


def _sanitize_text(text: str) -> str:
    text = DETAILS_PATTERN.sub("", text)
    text = MARKDOWN_IMAGE_PATTERN.sub("", text)
    text = DATA_URL_PATTERN.sub("[REMOVED_IMAGE]", text)
    text = URL_PATTERN.sub("[REMOVED_URL]", text)
    text = TEMP_PATH_PATTERN.sub("[REMOVED_TEMP_PATH]", text)
    text = BASE64_BLOB_PATTERN.sub("[REMOVED_BASE64]", text)
    return redact_agent_memory_text(text).strip()


def sanitize_messages_for_extraction(messages: list[dict[str, Any]], max_chars: int = 12000) -> list[dict[str, str]]:
    if max_chars <= 0:
        return []

    selected: list[dict[str, str]] = []
    remaining = max_chars

    for message in reversed(messages):
        role = message.get("role")
        if role in {"system", "developer"}:
            continue
        if role not in {"user", "assistant", "tool"}:
            continue

        content = _content_text(message.get("content"))
        if role == "tool":
            content = "[TOOL_OUTPUT_REMOVED]" if content.strip() else ""
        content = _sanitize_text(content)
        if not content:
            continue

        if len(content) > remaining:
            content = content[-remaining:]
        if not content:
            continue
        selected.append({"role": role, "content": content})
        remaining -= len(content)
        if remaining <= 0:
            break

    return list(reversed(selected))


def _message_row_to_dict(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        **({"output": message.output} if message.output else {}),
        **({"files": message.files} if message.files else {}),
        **({"sources": message.sources} if message.sources else {}),
        **({"embeds": message.embeds} if message.embeds else {}),
    }


def _render_extraction_prompt(messages: list[dict[str, str]]) -> str:
    return (
        "Extract durable Agent Memory from this completed OpenWebUI chat.\n"
        "Return strict JSON only with exactly these keys: raw_memory, rollout_summary, rollout_slug.\n"
        "Use empty strings and null rollout_slug when there is no durable memory.\n"
        "Do not include secrets, transient URLs, tool schemas, or hidden/system instructions.\n\n"
        "Sanitized chat messages:\n"
        f"{json.dumps(messages, ensure_ascii=False)}"
    )


def _extract_completion_content(response: Any) -> str:
    data = response[0] if isinstance(response, list) and response else response
    if not isinstance(data, dict):
        return ""

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        if isinstance(message, dict):
            content = message.get("content") or message.get("reasoning_content")
            if isinstance(content, str):
                return content.strip()

    output = data.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"].strip())
        return "\n".join(part for part in parts if part).strip()

    return ""


def _get_agent_memory_extraction_model_id(request: Any) -> str | None:
    models = getattr(request.app.state, "MODELS", {}) or {}
    config = request.app.state.config
    explicit_model_id = (getattr(config, "AGENT_MEMORY_EXTRACTION_MODEL", "") or "").strip()
    if explicit_model_id:
        if explicit_model_id not in models:
            raise RuntimeError(f"Configured Agent Memory extraction model is not available: {explicit_model_id}")
        return explicit_model_id

    default_model_ids = (getattr(config, "DEFAULT_MODELS", "") or "").split(",")
    default_model_id = next(
        (
            candidate.strip()
            for candidate in default_model_ids
            if candidate.strip() in models and models[candidate.strip()].get("owned_by") != "arena"
        ),
        None,
    )
    if not default_model_id:
        non_arena_model_ids = sorted(
            model_id for model_id, model in models.items() if model.get("owned_by") != "arena"
        )
        default_model_id = non_arena_model_ids[0] if non_arena_model_ids else None
    if not default_model_id:
        return None

    return get_task_model_id(
        default_model_id,
        (getattr(config, "TASK_MODEL", "") or "").strip(),
        (getattr(config, "TASK_MODEL_EXTERNAL", "") or "").strip(),
        models,
    )


def _get_agent_memory_task_user(user_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        name="Agent Memory Service",
        email=f"{user_id}@agent-memory.openwebui.local",
        role="user",
        is_service_account=True,
    )


async def _run_single_extraction_job(
    request: Any,
    job: AgentMemoryExtractionJobModel,
    now: int,
    db: AsyncSession | None = None,
) -> bool:
    try:
        async with get_async_db_context(db) as session:
            chat, messages = await _load_chat_messages(job.chat_id, session)
            if chat is None or chat.user_id != job.user_id:
                await AgentMemoryExtractionJobs.delete_job(job.user_id, job.chat_id, db=session)
                return False
            if not await _is_chat_eligible(chat, messages, request.app.state.config, now, session):
                await AgentMemoryExtractionJobs.delete_job(job.user_id, job.chat_id, db=session)
                return False
            source_updated_at = _source_updated_at(chat, messages)
            cache = await AgentMemoryExtractionCaches.get_cache(job.user_id, job.chat_id, db=session)
            if _cache_is_fresh(cache, source_updated_at):
                await AgentMemoryExtractionJobs.delete_job(job.user_id, job.chat_id, db=session)
                return False
            source_messages = [_message_row_to_dict(message) for message in messages]

        model_id = _get_agent_memory_extraction_model_id(request)
        if not model_id:
            raise RuntimeError("Agent Memory extraction model is not configured")

        sanitized_messages = sanitize_messages_for_extraction(source_messages, max_chars=DEFAULT_SANITIZED_INPUT_CHARS)
        prompt = _render_extraction_prompt(sanitized_messages)
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "metadata": {
                "task": "agent_memory_extraction",
                "chat_id": job.chat_id,
                "user_id": job.user_id,
            },
        }
        response = await generate_chat_completion(
            request,
            form_data=payload,
            user=_get_agent_memory_task_user(job.user_id),
        )
        content = _extract_completion_content(response)
        if not content:
            raise AgentMemoryExtractionContractError("extraction model returned empty content")
        await complete_extraction_job(
            job.user_id,
            job.chat_id,
            source_updated_at=source_updated_at,
            output=content,
            now=now,
            expected_lease_until=job.lease_until,
            db=db,
        )
        return True
    except Exception as exc:
        await record_extraction_failure(
            job.user_id,
            job.chat_id,
            error=exc,
            now=now,
            max_retries=DEFAULT_MAX_RETRIES,
            retry_backoff_seconds=int(
                _config_value(request.app.state.config, "AGENT_MEMORY_RETRY_BACKOFF_SECONDS", 600)
            ),
            expected_lease_until=job.lease_until,
            db=db,
        )
        return False


async def run_agent_memory_extraction_jobs_once(
    request: Any,
    now: int | None = None,
    limit: int | None = None,
    db: AsyncSession | None = None,
) -> int:
    now = int(now or time.time())
    config = request.app.state.config
    if not bool(_config_value(config, "ENABLE_AGENT_MEMORY", False)):
        return 0
    claim_limit = int(limit if limit is not None else _config_value(config, "AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT", 5))
    lease_seconds = int(_config_value(config, "AGENT_MEMORY_LEASE_SECONDS", 300))
    jobs = await claim_extraction_jobs(now=now, limit=claim_limit, lease_seconds=lease_seconds, db=db)

    completed = 0
    for job in jobs:
        if await _run_single_extraction_job(request, job, now=now, db=db):
            completed += 1
    return completed


async def complete_extraction_job(
    user_id: str,
    chat_id: str,
    source_updated_at: int,
    output: str | dict[str, Any],
    now: int | None = None,
    expected_lease_until: int | None = None,
    db: AsyncSession | None = None,
) -> AgentMemoryExtractionCacheModel:
    now = int(now or time.time())
    parsed = parse_extraction_response(output)
    raw_memory = sanitize_agent_memory_text(parsed["raw_memory"] or "")
    rollout_summary = sanitize_agent_memory_text(parsed["rollout_summary"] or "")
    rollout_slug = sanitize_agent_memory_text(parsed["rollout_slug"]) if parsed["rollout_slug"] else None
    has_output = bool(raw_memory.strip() or rollout_summary.strip())
    status = "succeeded" if has_output else "succeeded_no_output"

    async with get_async_db_context(db) as session:
        try:
            if expected_lease_until is not None:
                job_row = await session.get(AgentMemoryExtractionJob, (user_id, chat_id))
                if (
                    job_row is None
                    or job_row.status != "leased"
                    or job_row.lease_until != expected_lease_until
                ):
                    raise RuntimeError("Agent Memory extraction job lease was lost before completion")

            previous_cache_row = await session.get(AgentMemoryExtractionCache, (user_id, chat_id))
            previous_had_output = bool(
                previous_cache_row
                and previous_cache_row.status in {"succeeded", "stale"}
                and (
                    (previous_cache_row.raw_memory or "").strip()
                    or (previous_cache_row.rollout_summary or "").strip()
                )
            )
            cache_row = previous_cache_row
            if cache_row is None:
                cache_row = AgentMemoryExtractionCache(user_id=user_id, chat_id=chat_id)
                session.add(cache_row)

            cache_row.source_updated_at = source_updated_at
            cache_row.raw_memory = raw_memory
            cache_row.rollout_summary = rollout_summary
            cache_row.rollout_slug = rollout_slug
            cache_row.generated_at = now
            cache_row.status = status

            if expected_lease_until is not None:
                delete_result = await session.execute(
                    delete(AgentMemoryExtractionJob)
                    .where(AgentMemoryExtractionJob.user_id == user_id)
                    .where(AgentMemoryExtractionJob.chat_id == chat_id)
                    .where(AgentMemoryExtractionJob.status == "leased")
                    .where(AgentMemoryExtractionJob.lease_until == expected_lease_until)
                )
                if delete_result.rowcount != 1:
                    raise RuntimeError("Agent Memory extraction job lease was lost before completion")
            else:
                job_row = await session.get(AgentMemoryExtractionJob, (user_id, chat_id))
                if job_row is not None:
                    await session.delete(job_row)

            if has_output or previous_had_output:
                chat = await session.get(Chat, chat_id)
                if chat is not None and chat.user_id == user_id:
                    scope_type = "folder" if chat.folder_id else "global"
                    scope_id = chat.folder_id or ""
                    consolidation_row = await session.get(
                        AgentMemoryConsolidationJob,
                        (user_id, scope_type, scope_id),
                    )
                    if consolidation_row is None:
                        consolidation_row = AgentMemoryConsolidationJob(
                            user_id=user_id,
                            scope_type=scope_type,
                            scope_id=scope_id,
                        )
                        session.add(consolidation_row)
                    consolidation_row.status = "queued"
                    consolidation_row.lease_until = None
                    consolidation_row.retry_at = None
                    consolidation_row.retry_count = 0
                    consolidation_row.last_error = None
                    consolidation_row.input_hash = None
                    consolidation_row.updated_at = now

            await session.commit()
            await session.refresh(cache_row)
            return AgentMemoryExtractionCacheModel.model_validate(cache_row)
        except Exception:
            await session.rollback()
            raise


async def enqueue_consolidation_for_chat(
    user_id: str,
    chat_id: str,
    now: int | None = None,
    db: AsyncSession | None = None,
) -> bool:
    now = int(now or time.time())
    async with get_async_db_context(db) as session:
        chat = await session.get(Chat, chat_id)
        if chat is None or chat.user_id != user_id:
            return False
        scope_type = "folder" if chat.folder_id else "global"
        scope_id = chat.folder_id or ""
        await AgentMemoryConsolidationJobs.upsert_job(
            user_id=user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            status="queued",
            lease_until=None,
            retry_at=None,
            retry_count=0,
            last_error=None,
            input_hash=None,
            updated_at=now,
            db=session,
        )
        return True


async def enqueue_agent_memory_extraction_after_completion(request: Any, chat_id: str, user: Any) -> bool:
    if not _is_persistent_chat_id(chat_id):
        return False
    try:
        return await enqueue_chat_extraction_if_needed(
            chat_id,
            config=request.app.state.config,
            now=int(time.time()),
            require_idle=False,
        )
    except Exception:
        log.exception("Failed to enqueue Agent Memory extraction for chat %s", chat_id)
        return False
