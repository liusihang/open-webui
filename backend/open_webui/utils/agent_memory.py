from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_db_context
from open_webui.models.access_grants import AccessGrant
from open_webui.models.agent_memories import (
    AgentMemoryArtifact,
    AgentMemoryArtifacts,
    AgentMemoryConsolidationJob,
    AgentMemoryConsolidationJobs,
    AgentMemoryExtractionCache,
    AgentMemoryExtractionJob,
)
from open_webui.models.chats import Chat, set_agent_memory_disabled as set_chat_memory_disabled_meta
from open_webui.models.folders import Folder, set_agent_memory_disabled as set_folder_memory_disabled_meta
from open_webui.models.notes import Note, PinnedNote
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.utils.agent_memory_index import (
    agent_memory_collection_name,
    rebuild_agent_memory_index_for_scope,
)


def _now(now: int | None = None) -> int:
    return int(now or time.time())


def _scope_for_folder(folder_id: str | None) -> tuple[str, str]:
    if folder_id:
        return "folder", folder_id
    return "global", ""


def _validate_scope(scope_type: str | None, scope_id: str | None) -> tuple[str | None, str | None]:
    if scope_type is None:
        if scope_id:
            raise ValueError("scope_id requires scope_type")
        return None, None
    if scope_type == "global":
        return "global", ""
    if scope_type == "folder" and scope_id:
        return "folder", scope_id
    raise ValueError("scope_type must be global or folder with scope_id")


def _is_agent_memory_disabled(meta: dict | None) -> bool:
    agent_memory = (meta or {}).get("agent_memory") or {}
    return bool(agent_memory.get("disabled"))


def _note_matches_agent_memory_scope(
    note: Note,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> bool:
    linkage = ((note.meta or {}).get("agent_memory") or {})
    if not linkage:
        return False
    if linkage.get("managed") is not True:
        return False
    if scope_type is not None and linkage.get("scope_type") != scope_type:
        return False
    if scope_id is not None and linkage.get("scope_id", "") != scope_id:
        return False
    return True


async def _enqueue_consolidation(
    user_id: str,
    scope_type: str,
    scope_id: str,
    now: int,
    db: AsyncSession | None = None,
) -> None:
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
        db=db,
    )


async def enqueue_consolidation_for_scope(
    user_id: str,
    scope_type: str,
    scope_id: str = "",
    now: int | None = None,
    db: AsyncSession | None = None,
) -> dict[str, int]:
    now = _now(now)
    scope_type, scope_id = _validate_scope(scope_type, scope_id)
    assert scope_type is not None and scope_id is not None
    await _enqueue_consolidation(user_id, scope_type, scope_id, now, db=db)
    return {"consolidation_jobs_queued": 1}


def _is_missing_collection_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None) or getattr(error, "status", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None) or getattr(response, "status", None)
    try:
        if int(status_code) == 404:
            return True
    except (TypeError, ValueError):
        pass

    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "does not exist",
            "doesn't exist",
            "no such index",
            "unknown index",
            "not found in database",
            "not found",
        )
    )


async def _delete_collection(collection_name: str) -> None:
    try:
        await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=collection_name)
    except Exception as e:
        if _is_missing_collection_error(e):
            return
        raise


async def _delete_scope_collection(user_id: str, scope_type: str, scope_id: str) -> None:
    await _delete_collection(agent_memory_collection_name(user_id, scope_type, scope_id))


async def _chat_ids_in_folder(
    session: AsyncSession,
    user_id: str,
    folder_id: str,
) -> list[str]:
    result = await session.execute(
        select(Chat.id).where(Chat.user_id == user_id).where(Chat.folder_id == folder_id)
    )
    return list(result.scalars().all())


async def list_chat_ids_in_folder(
    user_id: str,
    folder_id: str,
    db: AsyncSession | None = None,
) -> list[str]:
    async with get_async_db_context(db) as session:
        return await _chat_ids_in_folder(session, user_id, folder_id)


async def _delete_extraction_rows_for_chat_ids(
    session: AsyncSession,
    user_id: str,
    chat_ids: list[str],
) -> tuple[int, int]:
    if not chat_ids:
        return 0, 0

    cache_result = await session.execute(
        delete(AgentMemoryExtractionCache)
        .where(AgentMemoryExtractionCache.user_id == user_id)
        .where(AgentMemoryExtractionCache.chat_id.in_(chat_ids))
    )
    job_result = await session.execute(
        delete(AgentMemoryExtractionJob)
        .where(AgentMemoryExtractionJob.user_id == user_id)
        .where(AgentMemoryExtractionJob.chat_id.in_(chat_ids))
    )
    return int(cache_result.rowcount or 0), int(job_result.rowcount or 0)


async def _linked_notes_for_scope(
    session: AsyncSession,
    user_id: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> list[Note]:
    result = await session.execute(select(Note).where(Note.user_id == user_id))
    notes = list(result.scalars().all())
    return [
        note
        for note in notes
        if _note_matches_agent_memory_scope(note, scope_type=scope_type, scope_id=scope_id)
    ]


async def _handle_linked_notes(
    session: AsyncSession,
    user_id: str,
    note_mode: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> dict[str, int]:
    if note_mode not in {"convert", "delete"}:
        raise ValueError("note_mode must be convert or delete")

    notes = await _linked_notes_for_scope(session, user_id, scope_type=scope_type, scope_id=scope_id)
    converted = 0
    deleted = 0
    for note in notes:
        if note_mode == "delete":
            await session.execute(
                delete(AccessGrant)
                .where(AccessGrant.resource_type == "note")
                .where(AccessGrant.resource_id == note.id)
            )
            await session.execute(delete(PinnedNote).where(PinnedNote.note_id == note.id))
            await session.delete(note)
            deleted += 1
            continue

        next_meta = dict(note.meta or {})
        if "agent_memory" in next_meta:
            next_meta.pop("agent_memory", None)
            note.meta = next_meta
            note.updated_at = _now()
            converted += 1

    return {"notes_converted": converted, "notes_deleted": deleted}


async def _delete_artifacts_for_scope(
    session: AsyncSession,
    user_id: str,
    scope_type: str,
    scope_id: str,
) -> int:
    result = await session.execute(
        delete(AgentMemoryArtifact)
        .where(AgentMemoryArtifact.user_id == user_id)
        .where(AgentMemoryArtifact.scope_type == scope_type)
        .where(AgentMemoryArtifact.scope_id == scope_id)
    )
    return int(result.rowcount or 0)


async def remove_agent_memory_scope_outputs(
    user_id: str,
    scope_type: str,
    scope_id: str,
    note_mode: str = "convert",
    db: AsyncSession | None = None,
) -> dict[str, int]:
    """Remove derived artifacts, linked Note metadata, jobs, and vector collection for one scope."""
    scope_type, scope_id = _validate_scope(scope_type, scope_id)
    assert scope_type is not None and scope_id is not None

    await _delete_scope_collection(user_id, scope_type, scope_id)

    async with get_async_db_context(db) as session:
        note_counts = await _handle_linked_notes(
            session,
            user_id,
            note_mode,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        artifacts_deleted = await _delete_artifacts_for_scope(session, user_id, scope_type, scope_id)
        consolidation_job_result = await session.execute(
            delete(AgentMemoryConsolidationJob)
            .where(AgentMemoryConsolidationJob.user_id == user_id)
            .where(AgentMemoryConsolidationJob.scope_type == scope_type)
            .where(AgentMemoryConsolidationJob.scope_id == scope_id)
        )
        await session.commit()

    return {
        "artifacts_deleted": artifacts_deleted,
        "consolidation_jobs_deleted": int(consolidation_job_result.rowcount or 0),
        **note_counts,
        "vector_collections_deleted": 1,
    }


async def forget_chat_agent_memory(
    user_id: str,
    chat_id: str,
    folder_id: str | None,
    now: int | None = None,
    db: AsyncSession | None = None,
) -> dict[str, int]:
    now = _now(now)
    async with get_async_db_context(db) as session:
        cache_result = await session.execute(
            delete(AgentMemoryExtractionCache)
            .where(AgentMemoryExtractionCache.user_id == user_id)
            .where(AgentMemoryExtractionCache.chat_id == chat_id)
        )
        job_result = await session.execute(
            delete(AgentMemoryExtractionJob)
            .where(AgentMemoryExtractionJob.user_id == user_id)
            .where(AgentMemoryExtractionJob.chat_id == chat_id)
        )
        await session.commit()

        scope_type, scope_id = _scope_for_folder(folder_id)
        await _enqueue_consolidation(user_id, scope_type, scope_id, now, db=session)

    return {
        "extraction_caches_deleted": int(cache_result.rowcount or 0),
        "extraction_jobs_deleted": int(job_result.rowcount or 0),
        "consolidation_jobs_queued": 1,
    }


async def set_chat_agent_memory_disabled(
    user_id: str,
    chat_id: str,
    disabled: bool,
    now: int | None = None,
    db: AsyncSession | None = None,
) -> dict[str, int | bool]:
    now = _now(now)
    async with get_async_db_context(db) as session:
        chat = await session.get(Chat, chat_id)
        if chat is None or chat.user_id != user_id:
            return {"updated": False, "extraction_caches_deleted": 0, "extraction_jobs_deleted": 0}

        folder_id = chat.folder_id
        chat.meta = set_chat_memory_disabled_meta(chat.meta, disabled)
        chat.updated_at = now
        await session.commit()

        result: dict[str, int | bool] = {"updated": True}
        if disabled:
            result.update(
                await forget_chat_agent_memory(
                    user_id=user_id,
                    chat_id=chat_id,
                    folder_id=folder_id,
                    now=now,
                    db=session,
                )
            )
        return result


async def set_folder_agent_memory_disabled(
    user_id: str,
    folder_id: str,
    disabled: bool,
    now: int | None = None,
    db: AsyncSession | None = None,
) -> dict[str, int | bool]:
    now = _now(now)
    async with get_async_db_context(db) as session:
        folder = await session.get(Folder, folder_id)
        if folder is None or folder.user_id != user_id:
            return {
                "updated": False,
                "extraction_caches_deleted": 0,
                "extraction_jobs_deleted": 0,
                "artifacts_deleted": 0,
            }

        folder.meta = set_folder_memory_disabled_meta(folder.meta, disabled)
        folder.updated_at = now
        extraction_caches_deleted = 0
        extraction_jobs_deleted = 0
        if disabled:
            chat_ids = await _chat_ids_in_folder(session, user_id, folder_id)
            extraction_caches_deleted, extraction_jobs_deleted = await _delete_extraction_rows_for_chat_ids(
                session,
                user_id,
                chat_ids,
            )
        await session.commit()

    result: dict[str, int | bool] = {
        "updated": True,
        "extraction_caches_deleted": extraction_caches_deleted,
        "extraction_jobs_deleted": extraction_jobs_deleted,
    }
    if disabled:
        result.update(
            await remove_agent_memory_scope_outputs(
                user_id=user_id,
                scope_type="folder",
                scope_id=folder_id,
                note_mode="convert",
                db=db,
            )
        )
    return result


async def _folder_is_opted_out(
    user_id: str,
    folder_id: str | None,
    db: AsyncSession | None = None,
) -> bool:
    if not folder_id:
        return False
    async with get_async_db_context(db) as session:
        folder = await session.get(Folder, folder_id)
        return bool(folder and folder.user_id == user_id and _is_agent_memory_disabled(folder.meta))


async def enqueue_consolidation_for_folder_move(
    user_id: str,
    chat_id: str,
    old_folder_id: str | None,
    new_folder_id: str | None,
    now: int | None = None,
    db: AsyncSession | None = None,
) -> dict[str, int]:
    now = _now(now)
    queued = 0
    old_scope = _scope_for_folder(old_folder_id)
    new_scope = _scope_for_folder(new_folder_id)

    async with get_async_db_context(db) as session:
        await _enqueue_consolidation(user_id, old_scope[0], old_scope[1], now, db=session)
        queued += 1

        if await _folder_is_opted_out(user_id, new_folder_id, db=session):
            cache_result = await session.execute(
                delete(AgentMemoryExtractionCache)
                .where(AgentMemoryExtractionCache.user_id == user_id)
                .where(AgentMemoryExtractionCache.chat_id == chat_id)
            )
            job_result = await session.execute(
                delete(AgentMemoryExtractionJob)
                .where(AgentMemoryExtractionJob.user_id == user_id)
                .where(AgentMemoryExtractionJob.chat_id == chat_id)
            )
            await session.commit()
            return {
                "consolidation_jobs_queued": queued,
                "extraction_caches_deleted": int(cache_result.rowcount or 0),
                "extraction_jobs_deleted": int(job_result.rowcount or 0),
            }

        if new_scope != old_scope:
            await _enqueue_consolidation(user_id, new_scope[0], new_scope[1], now, db=session)
            queued += 1

    return {
        "consolidation_jobs_queued": queued,
        "extraction_caches_deleted": 0,
        "extraction_jobs_deleted": 0,
    }


async def _scope_chat_ids(
    session: AsyncSession,
    user_id: str,
    scope_type: str | None,
    scope_id: str | None,
) -> list[str] | None:
    if scope_type is None:
        return None
    stmt = select(Chat.id).where(Chat.user_id == user_id)
    if scope_type == "global":
        stmt = stmt.where(Chat.folder_id.is_(None))
    else:
        stmt = stmt.where(Chat.folder_id == scope_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def clear_agent_memory(
    user_id: str,
    note_mode: str = "convert",
    scope_type: str | None = None,
    scope_id: str | None = None,
    now: int | None = None,
    db: AsyncSession | None = None,
) -> dict[str, int]:
    _now(now)
    scope_type, scope_id = _validate_scope(scope_type, scope_id)
    if note_mode not in {"convert", "delete"}:
        raise ValueError("note_mode must be convert or delete")

    async with get_async_db_context(db) as session:
        artifact_scope_stmt = select(AgentMemoryArtifact.scope_type, AgentMemoryArtifact.scope_id).where(
            AgentMemoryArtifact.user_id == user_id
        )
        if scope_type is not None:
            artifact_scope_stmt = artifact_scope_stmt.where(AgentMemoryArtifact.scope_type == scope_type).where(
                AgentMemoryArtifact.scope_id == scope_id
            )
        artifact_scope_rows = await session.execute(artifact_scope_stmt)
        collection_scopes = list(dict.fromkeys(artifact_scope_rows.all()))
        collection_scopes.sort(key=lambda scope: (0 if scope[0] == "global" else 1, scope[1]))
        if scope_type is not None and (scope_type, scope_id) not in collection_scopes:
            collection_scopes.append((scope_type, scope_id))

        chat_ids = await _scope_chat_ids(session, user_id, scope_type, scope_id)

    for collection_scope_type, collection_scope_id in collection_scopes:
        await _delete_scope_collection(user_id, collection_scope_type, collection_scope_id)

    async with get_async_db_context(db) as session:
        if chat_ids is None:
            cache_result = await session.execute(
                delete(AgentMemoryExtractionCache).where(AgentMemoryExtractionCache.user_id == user_id)
            )
            extraction_job_result = await session.execute(
                delete(AgentMemoryExtractionJob).where(AgentMemoryExtractionJob.user_id == user_id)
            )
        else:
            caches_deleted, jobs_deleted = await _delete_extraction_rows_for_chat_ids(session, user_id, chat_ids)
            cache_result = None
            extraction_job_result = None

        consolidation_stmt = delete(AgentMemoryConsolidationJob).where(
            AgentMemoryConsolidationJob.user_id == user_id
        )
        if scope_type is not None:
            consolidation_stmt = consolidation_stmt.where(
                AgentMemoryConsolidationJob.scope_type == scope_type
            ).where(AgentMemoryConsolidationJob.scope_id == scope_id)
        consolidation_result = await session.execute(consolidation_stmt)

        artifact_stmt = delete(AgentMemoryArtifact).where(AgentMemoryArtifact.user_id == user_id)
        if scope_type is not None:
            artifact_stmt = artifact_stmt.where(AgentMemoryArtifact.scope_type == scope_type).where(
                AgentMemoryArtifact.scope_id == scope_id
            )
        artifact_result = await session.execute(artifact_stmt)

        note_counts = await _handle_linked_notes(
            session,
            user_id,
            note_mode,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        await session.commit()

    return {
        "extraction_caches_deleted": (
            caches_deleted if chat_ids is not None else int(cache_result.rowcount or 0)
        ),
        "extraction_jobs_deleted": (
            jobs_deleted if chat_ids is not None else int(extraction_job_result.rowcount or 0)
        ),
        "consolidation_jobs_deleted": int(consolidation_result.rowcount or 0),
        "artifacts_deleted": int(artifact_result.rowcount or 0),
        "vector_collections_deleted": len(collection_scopes),
        **note_counts,
    }


async def retry_failed_agent_memory_jobs(
    user_id: str | None = None,
    now: int | None = None,
    db: AsyncSession | None = None,
) -> dict[str, int]:
    now = _now(now)
    async with get_async_db_context(db) as session:
        extraction_stmt = (
            update(AgentMemoryExtractionJob)
            .where(AgentMemoryExtractionJob.status == "failed")
            .values(
                status="queued",
                lease_until=None,
                retry_at=None,
                retry_count=0,
                last_error=None,
                updated_at=now,
            )
        )
        consolidation_stmt = (
            update(AgentMemoryConsolidationJob)
            .where(AgentMemoryConsolidationJob.status == "failed")
            .values(
                status="queued",
                lease_until=None,
                retry_at=None,
                retry_count=0,
                last_error=None,
                input_hash=None,
                updated_at=now,
            )
        )
        if user_id is not None:
            extraction_stmt = extraction_stmt.where(AgentMemoryExtractionJob.user_id == user_id)
            consolidation_stmt = consolidation_stmt.where(AgentMemoryConsolidationJob.user_id == user_id)

        extraction_result = await session.execute(extraction_stmt)
        consolidation_result = await session.execute(consolidation_stmt)
        await session.commit()

    return {
        "extraction_jobs_retried": int(extraction_result.rowcount or 0),
        "consolidation_jobs_retried": int(consolidation_result.rowcount or 0),
    }


def _extraction_job_payload(job: AgentMemoryExtractionJob) -> dict[str, Any]:
    return {
        "user_id": job.user_id,
        "chat_id": job.chat_id,
        "status": job.status,
        "retry_count": job.retry_count,
        "last_error": job.last_error,
        "updated_at": job.updated_at,
    }


def _consolidation_job_payload(job: AgentMemoryConsolidationJob) -> dict[str, Any]:
    return {
        "user_id": job.user_id,
        "scope_type": job.scope_type,
        "scope_id": job.scope_id,
        "status": job.status,
        "retry_count": job.retry_count,
        "last_error": job.last_error,
        "input_hash": job.input_hash,
        "updated_at": job.updated_at,
    }


async def list_failed_agent_memory_jobs(
    user_id: str | None = None,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    async with get_async_db_context(db) as session:
        extraction_stmt = select(AgentMemoryExtractionJob).where(AgentMemoryExtractionJob.status == "failed")
        consolidation_stmt = select(AgentMemoryConsolidationJob).where(
            AgentMemoryConsolidationJob.status == "failed"
        )
        if user_id is not None:
            extraction_stmt = extraction_stmt.where(AgentMemoryExtractionJob.user_id == user_id)
            consolidation_stmt = consolidation_stmt.where(AgentMemoryConsolidationJob.user_id == user_id)

        extraction_result = await session.execute(extraction_stmt.order_by(AgentMemoryExtractionJob.updated_at.asc()))
        consolidation_result = await session.execute(
            consolidation_stmt.order_by(AgentMemoryConsolidationJob.updated_at.asc())
        )
        extraction_jobs = [_extraction_job_payload(job) for job in extraction_result.scalars().all()]
        consolidation_jobs = [_consolidation_job_payload(job) for job in consolidation_result.scalars().all()]

    return {
        "extraction_jobs_failed": len(extraction_jobs),
        "consolidation_jobs_failed": len(consolidation_jobs),
        "extraction_jobs": extraction_jobs,
        "consolidation_jobs": consolidation_jobs,
    }


async def rebuild_agent_memory_index(
    request: Any,
    user_id: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
    db: AsyncSession | None = None,
) -> dict[str, int]:
    scope_type, scope_id = _validate_scope(scope_type, scope_id)
    async with get_async_db_context(db) as session:
        if scope_type is None:
            result = await session.execute(
                select(AgentMemoryArtifact.scope_type, AgentMemoryArtifact.scope_id)
                .where(AgentMemoryArtifact.user_id == user_id)
                .distinct()
            )
            scopes = list(result.all())
        else:
            scopes = [(scope_type, scope_id)]

    for current_scope_type, current_scope_id in scopes:
        await _delete_scope_collection(user_id, current_scope_type, current_scope_id)
        await rebuild_agent_memory_index_for_scope(
            request,
            user_id,
            current_scope_type,
            current_scope_id,
            db=db,
        )

    return {"collections_rebuilt": len(scopes)}
