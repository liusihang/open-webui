from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from open_webui.internal.db import get_async_db_context
from open_webui.models.agent_memories import (
    AgentMemoryArtifact,
    AgentMemoryArtifactModel,
    AgentMemoryArtifacts,
    AgentMemoryConsolidationJob,
    AgentMemoryConsolidationJobModel,
    AgentMemoryExtractionCache,
)
from open_webui.models.chats import Chat
from open_webui.models.folders import Folder
from open_webui.models.notes import Note
from open_webui.utils.access_control import has_permission
from open_webui.utils.agent_memory_extraction import sanitize_agent_memory_text
from open_webui.utils.agent_memory_index import rebuild_agent_memory_index_for_scope
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.task import get_task_model_id
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

CONSOLIDATION_RESPONSE_KEYS = {"memory_summary_md", "memory_md"}
DEFAULT_MAX_RETRIES = 3
MAX_ERROR_CHARS = 240
DEFAULT_CONSOLIDATION_INPUT_CHARS = 60000


class AgentMemoryConsolidationContractError(ValueError):
    pass


@dataclass(frozen=True)
class ConsolidationInput:
    user_id: str
    scope_type: str
    scope_id: str
    cache_records: list[dict[str, Any]]
    human_revisions: list[dict[str, str]]
    existing_artifacts: list[dict[str, Any]]
    input_hash: str
    expected_note_hashes: dict[str, str]


def _config_value(config: Any, key: str, default: Any) -> Any:
    value = getattr(config, key, default)
    return default if value in (None, "") else value


def _is_agent_memory_disabled(meta: dict | None) -> bool:
    agent_memory = (meta or {}).get("agent_memory") or {}
    return bool(agent_memory.get("disabled"))


def normalize_memory_markdown(markdown: str) -> str:
    return (markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def hash_note_markdown(markdown: str) -> str:
    return hashlib.sha256(normalize_memory_markdown(markdown).encode("utf-8")).hexdigest()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _note_markdown(note: Note | None) -> str:
    if note is None:
        return ""
    content = ((note.data or {}).get("content") or {}).get("md")
    return normalize_memory_markdown(content if isinstance(content, str) else "")


def _note_linkage(scope_type: str, scope_id: str, path: str) -> dict[str, Any]:
    return {
        "agent_memory": {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "path": path,
            "managed": True,
        }
    }


def _note_title(scope_type: str, scope_id: str, path: str) -> str:
    scope_label = "Global" if scope_type == "global" else f"Folder {scope_id}"
    return f"Agent Memory: {scope_label} {path}"


def _note_matches_linkage(note: Note, user_id: str, scope_type: str, scope_id: str, path: str) -> bool:
    linkage = ((note.meta or {}).get("agent_memory") or {})
    return bool(
        note.user_id == user_id
        and linkage.get("managed") is True
        and linkage.get("scope_type") == scope_type
        and linkage.get("scope_id") == scope_id
        and linkage.get("path") == path
    )


async def _user_can_use_agent_memory(user_id: str, config: Any, db: AsyncSession) -> bool:
    if not bool(_config_value(config, "ENABLE_AGENT_MEMORY", False)):
        return False
    return await has_permission(
        user_id,
        "features.agent_memory",
        _config_value(config, "USER_PERMISSIONS", {}),
        db=db,
    )


def parse_consolidation_response(value: str | dict[str, Any]) -> dict[str, str]:
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except Exception as exc:
            raise AgentMemoryConsolidationContractError("consolidation response must be valid JSON") from exc
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        raise AgentMemoryConsolidationContractError("consolidation response must be a JSON object")

    if not isinstance(payload, dict):
        raise AgentMemoryConsolidationContractError("consolidation response must be a JSON object")
    if set(payload.keys()) != CONSOLIDATION_RESPONSE_KEYS:
        raise AgentMemoryConsolidationContractError("consolidation response keys must match the contract exactly")
    if not isinstance(payload["memory_summary_md"], str):
        raise AgentMemoryConsolidationContractError("memory_summary_md must be a string")
    if not isinstance(payload["memory_md"], str):
        raise AgentMemoryConsolidationContractError("memory_md must be a string")
    return {
        "memory_summary_md": sanitize_agent_memory_text(normalize_memory_markdown(payload["memory_summary_md"])),
        "memory_md": sanitize_agent_memory_text(normalize_memory_markdown(payload["memory_md"])),
    }


async def claim_consolidation_jobs(
    now: int | None = None,
    limit: int = 5,
    lease_seconds: int = 300,
    db: AsyncSession | None = None,
) -> list[AgentMemoryConsolidationJobModel]:
    now = int(now or time.time())
    if limit <= 0:
        return []

    async with get_async_db_context(db) as session:
        availability = or_(
            (AgentMemoryConsolidationJob.status == "queued")
            & or_(AgentMemoryConsolidationJob.retry_at.is_(None), AgentMemoryConsolidationJob.retry_at <= now),
            (AgentMemoryConsolidationJob.status == "retry")
            & or_(AgentMemoryConsolidationJob.retry_at.is_(None), AgentMemoryConsolidationJob.retry_at <= now),
            (AgentMemoryConsolidationJob.status == "leased") & (AgentMemoryConsolidationJob.lease_until <= now),
        )
        status_order = case(
            (AgentMemoryConsolidationJob.status == "queued", 0),
            (AgentMemoryConsolidationJob.status == "retry", 1),
            else_=2,
        )
        stmt = (
            select(AgentMemoryConsolidationJob)
            .where(availability)
            .order_by(
                status_order,
                func.coalesce(AgentMemoryConsolidationJob.lease_until, 0).desc(),
                AgentMemoryConsolidationJob.updated_at.asc(),
                AgentMemoryConsolidationJob.scope_type.asc(),
                AgentMemoryConsolidationJob.scope_id.asc(),
            )
            .limit(limit)
        )
        if session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        claimed_keys: list[tuple[str, str, str]] = []
        for row in rows:
            claim_result = await session.execute(
                update(AgentMemoryConsolidationJob)
                .where(AgentMemoryConsolidationJob.user_id == row.user_id)
                .where(AgentMemoryConsolidationJob.scope_type == row.scope_type)
                .where(AgentMemoryConsolidationJob.scope_id == row.scope_id)
                .where(availability)
                .values(
                    status="leased",
                    lease_until=now + lease_seconds,
                    retry_at=None,
                    updated_at=now,
                )
            )
            if claim_result.rowcount:
                claimed_keys.append((row.user_id, row.scope_type, row.scope_id))

        await session.commit()
        claimed_rows = []
        for key in claimed_keys:
            claimed_row = await session.get(AgentMemoryConsolidationJob, key)
            if claimed_row is not None:
                claimed_rows.append(AgentMemoryConsolidationJobModel.model_validate(claimed_row))
        return claimed_rows


async def record_consolidation_failure(
    user_id: str,
    scope_type: str,
    scope_id: str,
    error: Exception,
    now: int | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: int = 600,
    expected_lease_until: int | None = None,
    input_hash: str | None = None,
    db: AsyncSession | None = None,
) -> AgentMemoryConsolidationJobModel | None:
    now = int(now or time.time())
    async with get_async_db_context(db) as session:
        row = await session.get(AgentMemoryConsolidationJob, (user_id, scope_type, scope_id))
        if row is None or row.status != "leased":
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

        values = {
            "status": status,
            "retry_count": retry_count,
            "last_error": str(error)[:MAX_ERROR_CHARS],
            "lease_until": None,
            "retry_at": retry_at,
            "updated_at": now,
        }
        if input_hash is not None:
            values["input_hash"] = input_hash

        stmt = (
            update(AgentMemoryConsolidationJob)
            .where(AgentMemoryConsolidationJob.user_id == user_id)
            .where(AgentMemoryConsolidationJob.scope_type == scope_type)
            .where(AgentMemoryConsolidationJob.scope_id == scope_id)
            .where(AgentMemoryConsolidationJob.status == "leased")
            .values(**values)
        )
        if expected_lease_until is not None:
            stmt = stmt.where(AgentMemoryConsolidationJob.lease_until == expected_lease_until)
        result = await session.execute(stmt)
        if not result.rowcount:
            await session.rollback()
            return None
        await session.commit()
        updated_row = await session.get(AgentMemoryConsolidationJob, (user_id, scope_type, scope_id))
        return AgentMemoryConsolidationJobModel.model_validate(updated_row) if updated_row else None


async def build_consolidation_input(
    user_id: str,
    scope_type: str,
    scope_id: str,
    db: AsyncSession | None = None,
) -> ConsolidationInput:
    async with get_async_db_context(db) as session:
        cache_records: list[dict[str, Any]] = []
        stmt = (
            select(AgentMemoryExtractionCache, Chat)
            .join(Chat, Chat.id == AgentMemoryExtractionCache.chat_id)
            .where(AgentMemoryExtractionCache.user_id == user_id)
            .where(Chat.user_id == user_id)
            .where(AgentMemoryExtractionCache.status == "succeeded")
            .order_by(Chat.updated_at.asc(), Chat.id.asc())
        )
        result = await session.execute(stmt)
        folder_cache: dict[str, Folder | None] = {}
        for cache, chat in result.all():
            if _is_agent_memory_disabled(chat.meta):
                continue
            derived_scope_type = "folder" if chat.folder_id else "global"
            derived_scope_id = chat.folder_id or ""
            if derived_scope_type != scope_type or derived_scope_id != scope_id:
                continue
            if chat.folder_id:
                if chat.folder_id not in folder_cache:
                    folder_result = await session.execute(
                        select(Folder).where(Folder.id == chat.folder_id).where(Folder.user_id == user_id)
                    )
                    folder_cache[chat.folder_id] = folder_result.scalars().first()
                folder = folder_cache[chat.folder_id]
                if folder is None or _is_agent_memory_disabled(folder.meta):
                    continue
            cache_records.append(
                {
                    "chat_id": cache.chat_id,
                    "source_updated_at": cache.source_updated_at,
                    "raw_memory": cache.raw_memory,
                    "rollout_summary": cache.rollout_summary,
                    "rollout_slug": cache.rollout_slug,
                    "generated_at": cache.generated_at,
                }
            )

        artifacts = await _load_artifact_rows(user_id, scope_type, scope_id, session)
        human_revisions: list[dict[str, str]] = []
        expected_note_hashes: dict[str, str] = {}
        existing_artifacts = []
        for artifact in sorted(artifacts, key=lambda item: item.path):
            existing_artifacts.append(
                {
                    "path": artifact.path,
                    "input_hash": artifact.input_hash,
                    "revision": artifact.revision,
                    "note_id": artifact.note_id,
                    "note_content_hash": artifact.note_content_hash,
                }
            )
            if not artifact.note_id or not artifact.note_content_hash:
                continue
            note = await session.get(Note, artifact.note_id)
            if note is None or not _note_matches_linkage(note, user_id, scope_type, scope_id, artifact.path):
                continue
            current_md = _note_markdown(note)
            current_hash = hash_note_markdown(current_md)
            expected_note_hashes[artifact.note_id] = current_hash
            if note is not None and current_hash != artifact.note_content_hash:
                human_revisions.append(
                    {
                        "path": artifact.path,
                        "note_id": artifact.note_id,
                        "content": sanitize_agent_memory_text(current_md),
                        "expected_note_hash": current_hash,
                    }
                )

        input_payload = {
            "user_id": user_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "cache_records": cache_records,
            "human_revisions": human_revisions,
        }
        return ConsolidationInput(
            user_id=user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            cache_records=cache_records,
            human_revisions=human_revisions,
            existing_artifacts=existing_artifacts,
            input_hash=_canonical_hash(input_payload),
            expected_note_hashes=expected_note_hashes,
        )


async def _load_artifact_rows(
    user_id: str,
    scope_type: str,
    scope_id: str,
    session: AsyncSession,
) -> list[AgentMemoryArtifact]:
    result = await session.execute(
        select(AgentMemoryArtifact)
        .where(AgentMemoryArtifact.user_id == user_id)
        .where(AgentMemoryArtifact.scope_type == scope_type)
        .where(AgentMemoryArtifact.scope_id == scope_id)
    )
    return list(result.scalars().all())


def _artifacts_match_input_hash(artifacts: list[AgentMemoryArtifactModel], input_hash: str) -> bool:
    by_path = {artifact.path: artifact for artifact in artifacts}
    return bool(
        by_path.get("memory_summary.md")
        and by_path.get("MEMORY.md")
        and by_path["memory_summary.md"].input_hash == input_hash
        and by_path["MEMORY.md"].input_hash == input_hash
    )


async def _artifacts_have_synced_notes(
    artifacts: list[AgentMemoryArtifactModel],
    user_id: str,
    scope_type: str,
    scope_id: str,
    db: AsyncSession | None = None,
) -> bool:
    async with get_async_db_context(db) as session:
        for artifact in artifacts:
            if not artifact.note_id or not artifact.note_content_hash:
                return False
            note = await session.get(Note, artifact.note_id)
            if note is None or not _note_matches_linkage(note, user_id, scope_type, scope_id, artifact.path):
                return False
            if hash_note_markdown(_note_markdown(note)) != artifact.note_content_hash:
                return False
        return True


def _render_consolidation_prompt(consolidation_input: ConsolidationInput) -> str:
    payload = {
        "scope": {
            "type": consolidation_input.scope_type,
            "id": consolidation_input.scope_id,
        },
        "extraction_caches": consolidation_input.cache_records,
        "human_revisions": consolidation_input.human_revisions,
    }
    return (
        "Consolidate OpenWebUI Agent Memory for this scope.\n"
        "Return strict JSON only with exactly these keys: memory_summary_md, memory_md.\n"
        "Use only evidence from extraction caches and Human Revisions.\n"
        "Do not include secrets or transient URLs.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
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


def _get_agent_memory_consolidation_model_id(request: Any) -> str | None:
    models = getattr(request.app.state, "MODELS", {}) or {}
    config = request.app.state.config
    explicit_model_id = (getattr(config, "AGENT_MEMORY_CONSOLIDATION_MODEL", "") or "").strip()
    if explicit_model_id:
        if explicit_model_id not in models:
            raise RuntimeError(f"Configured Agent Memory consolidation model is not available: {explicit_model_id}")
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
        email=f"{user_id}@agent-memory.openwebui.local",
        role="admin",
    )


async def complete_consolidation_job(
    user_id: str,
    scope_type: str,
    scope_id: str,
    output: str | dict[str, Any],
    input_hash: str,
    now: int | None = None,
    expected_lease_until: int | None = None,
    expected_note_hashes: dict[str, str] | None = None,
    db: AsyncSession | None = None,
) -> dict[str, AgentMemoryArtifactModel]:
    now = int(now or time.time())
    parsed = parse_consolidation_response(output)
    artifact_contents = {
        "memory_summary.md": parsed["memory_summary_md"],
        "MEMORY.md": parsed["memory_md"],
    }
    expected_note_hashes = expected_note_hashes or {}

    async with get_async_db_context(db) as session:
        try:
            if expected_lease_until is not None:
                job_row = await session.get(AgentMemoryConsolidationJob, (user_id, scope_type, scope_id))
                if (
                    job_row is None
                    or job_row.status != "leased"
                    or job_row.lease_until != expected_lease_until
                ):
                    raise RuntimeError("Agent Memory consolidation job lease was lost before completion")

            result: dict[str, AgentMemoryArtifactModel] = {}
            for path, content in artifact_contents.items():
                artifact = await session.get(AgentMemoryArtifact, (user_id, scope_type, scope_id, path))
                if artifact and artifact.note_id:
                    note = await session.get(Note, artifact.note_id)
                    note_is_managed = bool(
                        note and _note_matches_linkage(note, user_id, scope_type, scope_id, path)
                    )
                    if note_is_managed:
                        expected_hash = expected_note_hashes.get(artifact.note_id) or artifact.note_content_hash
                        if expected_hash and hash_note_markdown(_note_markdown(note)) != expected_hash:
                            raise RuntimeError("Human Revision changed during consolidation")
                    elif note is not None:
                        artifact.note_id = None
                        artifact.note_content_hash = None

                if artifact and artifact.note_id and artifact.note_content_hash and artifact.note_id not in expected_note_hashes:
                    note = await session.get(Note, artifact.note_id)
                    if note is not None and hash_note_markdown(_note_markdown(note)) != artifact.note_content_hash:
                        raise RuntimeError("Human Revision changed during consolidation")

                if artifact is None:
                    artifact = AgentMemoryArtifact(
                        user_id=user_id,
                        scope_type=scope_type,
                        scope_id=scope_id,
                        path=path,
                        revision=1,
                    )
                    session.add(artifact)
                elif artifact.content != content or artifact.input_hash != input_hash:
                    artifact.revision = int(artifact.revision or 0) + 1

                note = await _sync_note_for_artifact(
                    session=session,
                    user_id=user_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    path=path,
                    content=content,
                    artifact=artifact,
                )
                artifact.content = content
                artifact.input_hash = input_hash
                artifact.note_id = note.id
                artifact.note_content_hash = hash_note_markdown(content)
                artifact.updated_at = now
                result[path] = AgentMemoryArtifactModel.model_validate(artifact)

            if expected_lease_until is not None:
                delete_result = await session.execute(
                    delete(AgentMemoryConsolidationJob)
                    .where(AgentMemoryConsolidationJob.user_id == user_id)
                    .where(AgentMemoryConsolidationJob.scope_type == scope_type)
                    .where(AgentMemoryConsolidationJob.scope_id == scope_id)
                    .where(AgentMemoryConsolidationJob.status == "leased")
                    .where(AgentMemoryConsolidationJob.lease_until == expected_lease_until)
                )
                if delete_result.rowcount != 1:
                    raise RuntimeError("Agent Memory consolidation job lease was lost before completion")
            else:
                job_row = await session.get(AgentMemoryConsolidationJob, (user_id, scope_type, scope_id))
                if job_row is not None:
                    await session.delete(job_row)

            await session.commit()
            for path, artifact_model in list(result.items()):
                row = await session.get(AgentMemoryArtifact, (user_id, scope_type, scope_id, path))
                result[path] = AgentMemoryArtifactModel.model_validate(row)
            return result
        except Exception:
            await session.rollback()
            raise


async def _sync_note_for_artifact(
    session: AsyncSession,
    user_id: str,
    scope_type: str,
    scope_id: str,
    path: str,
    content: str,
    artifact: AgentMemoryArtifact,
) -> Note:
    now_ns = int(time.time_ns())
    note = await session.get(Note, artifact.note_id) if artifact.note_id else None
    if note is not None and not _note_matches_linkage(note, user_id, scope_type, scope_id, path):
        note = None
    if note is None:
        note = Note(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=_note_title(scope_type, scope_id, path),
            data={"content": {"md": content}},
            meta=_note_linkage(scope_type, scope_id, path),
            created_at=now_ns,
            updated_at=now_ns,
        )
        session.add(note)
        return note

    note.title = _note_title(scope_type, scope_id, path)
    note.data = {"content": {"md": content}}
    note.meta = {**(note.meta or {}), **_note_linkage(scope_type, scope_id, path)}
    note.updated_at = now_ns
    return note


async def _delete_leased_consolidation_job(
    job: AgentMemoryConsolidationJobModel,
    db: AsyncSession | None = None,
) -> None:
    async with get_async_db_context(db) as session:
        result = await session.execute(
            delete(AgentMemoryConsolidationJob)
            .where(AgentMemoryConsolidationJob.user_id == job.user_id)
            .where(AgentMemoryConsolidationJob.scope_type == job.scope_type)
            .where(AgentMemoryConsolidationJob.scope_id == job.scope_id)
            .where(AgentMemoryConsolidationJob.status == "leased")
            .where(AgentMemoryConsolidationJob.lease_until == job.lease_until)
        )
        if result.rowcount != 1:
            await session.rollback()
            raise RuntimeError("Agent Memory consolidation job lease was lost before no-op completion")
        await session.commit()


async def _run_single_consolidation_job(
    request: Any,
    job: AgentMemoryConsolidationJobModel,
    now: int,
    db: AsyncSession | None = None,
) -> bool:
    attempted_input_hash: str | None = None
    try:
        async with get_async_db_context(db) as session:
            if not await _user_can_use_agent_memory(job.user_id, request.app.state.config, session):
                await _delete_leased_consolidation_job(job, db=session)
                return False

        consolidation_input = await build_consolidation_input(job.user_id, job.scope_type, job.scope_id, db=db)
        attempted_input_hash = consolidation_input.input_hash
        artifacts = await AgentMemoryArtifacts.list_artifacts(job.user_id, job.scope_type, job.scope_id, db=db)
        if not consolidation_input.human_revisions and _artifacts_match_input_hash(
            artifacts,
            consolidation_input.input_hash,
        ):
            if not await _artifacts_have_synced_notes(
                artifacts,
                job.user_id,
                job.scope_type,
                job.scope_id,
                db=db,
            ):
                content_by_path = {artifact.path: artifact.content for artifact in artifacts}
                await complete_consolidation_job(
                    job.user_id,
                    job.scope_type,
                    job.scope_id,
                    output={
                        "memory_summary_md": content_by_path["memory_summary.md"],
                        "memory_md": content_by_path["MEMORY.md"],
                    },
                    input_hash=consolidation_input.input_hash,
                    now=now,
                    expected_lease_until=job.lease_until,
                    expected_note_hashes=consolidation_input.expected_note_hashes,
                    db=db,
                )
                return False
            await _delete_leased_consolidation_job(job, db=db)
            return False

        model_id = _get_agent_memory_consolidation_model_id(request)
        if not model_id:
            raise RuntimeError("Agent Memory consolidation model is not configured")
        prompt = _render_consolidation_prompt(consolidation_input)
        if len(prompt) > DEFAULT_CONSOLIDATION_INPUT_CHARS:
            raise RuntimeError("Agent Memory consolidation input is too large")
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "metadata": {
                "task": "agent_memory_consolidation",
                "user_id": job.user_id,
                "scope_type": job.scope_type,
                "scope_id": job.scope_id,
            },
        }
        response = await generate_chat_completion(
            request,
            form_data=payload,
            user=_get_agent_memory_task_user(job.user_id),
            bypass_filter=True,
            bypass_system_prompt=True,
        )
        content = _extract_completion_content(response)
        if not content:
            raise AgentMemoryConsolidationContractError("consolidation model returned empty content")
        await complete_consolidation_job(
            job.user_id,
            job.scope_type,
            job.scope_id,
            output=content,
            input_hash=consolidation_input.input_hash,
            now=now,
            expected_lease_until=job.lease_until,
            expected_note_hashes=consolidation_input.expected_note_hashes,
            db=db,
        )
        await rebuild_agent_memory_index_for_scope(
            request,
            job.user_id,
            job.scope_type,
            job.scope_id,
            db=db,
        )
        return True
    except Exception as exc:
        await record_consolidation_failure(
            job.user_id,
            job.scope_type,
            job.scope_id,
            exc,
            now=now,
            max_retries=DEFAULT_MAX_RETRIES,
            retry_backoff_seconds=int(_config_value(request.app.state.config, "AGENT_MEMORY_RETRY_BACKOFF_SECONDS", 600)),
            expected_lease_until=job.lease_until,
            input_hash=attempted_input_hash,
            db=db,
        )
        return False


async def run_agent_memory_consolidation_jobs_once(
    request: Any,
    now: int | None = None,
    limit: int | None = None,
    db: AsyncSession | None = None,
) -> int:
    now = int(now or time.time())
    config = request.app.state.config
    if not bool(_config_value(config, "ENABLE_AGENT_MEMORY", False)):
        return 0
    claim_limit = int(limit if limit is not None else _config_value(config, "AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT", 2))
    lease_seconds = int(_config_value(config, "AGENT_MEMORY_LEASE_SECONDS", 300))
    jobs = await claim_consolidation_jobs(now=now, limit=claim_limit, lease_seconds=lease_seconds, db=db)

    completed = 0
    for job in jobs:
        if await _run_single_consolidation_job(request, job, now=now, db=db):
            completed += 1
    return completed
