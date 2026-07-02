from __future__ import annotations

import hashlib
import inspect
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.config import RAG_EMBEDDING_QUERY_PREFIX, TIKTOKEN_ENCODING_NAME
from open_webui.models.agent_memories import (
    AGENT_MEMORY_ARTIFACT_PATHS,
    AgentMemoryArtifactModel,
    AgentMemoryArtifacts,
)
from open_webui.models.chats import Chats
from open_webui.models.folders import Folders
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.retrieval.vector.main import SearchResult, VectorItem

AGENT_MEMORY_COLLECTION_PREFIX = "agent-memory-"
AGENT_MEMORY_POLICY = (
    "Agent Memory is read-only context from prior work. Use it when the request may depend on prior work, "
    "preferences, repository context, or project decisions. Skip it for clearly self-contained requests. "
    "Treat memory as fallible context and verify against current files or runtime when the user asks about "
    "live state. Use Agent Memory search/read tools for details. Never attempt to write Agent Memory from chat."
)
AGENT_MEMORY_TOOL_LIMIT_CAP = 20
AGENT_MEMORY_VECTOR_CANDIDATE_LIMIT_CAP = 100
AGENT_MEMORY_READ_DEFAULT_MAX_CHARS = 4000
AGENT_MEMORY_READ_MAX_CHARS_CAP = 12000


@dataclass(frozen=True)
class AgentMemoryScope:
    scope_type: str
    scope_id: str
    label: str


@dataclass(frozen=True)
class AgentMemoryChunk:
    id: str
    text: str
    metadata: dict[str, Any]


def is_agent_memory_disabled(meta: dict | None) -> bool:
    agent_memory = (meta or {}).get("agent_memory") or {}
    return bool(agent_memory.get("disabled"))


def agent_memory_collection_name(user_id: str, scope_type: str, scope_id: str = "") -> str:
    if scope_type == "global":
        return f"{AGENT_MEMORY_COLLECTION_PREFIX}{user_id}-global"
    if scope_type == "folder" and scope_id:
        return f"{AGENT_MEMORY_COLLECTION_PREFIX}{user_id}-folder-{scope_id}"
    raise ValueError("Agent Memory collection scope must be global or folder with scope_id")


def parse_agent_memory_collection_name(collection_name: str) -> dict[str, str] | None:
    if not isinstance(collection_name, str) or not collection_name.startswith(AGENT_MEMORY_COLLECTION_PREFIX):
        return None

    remainder = collection_name[len(AGENT_MEMORY_COLLECTION_PREFIX) :]
    if remainder.endswith("-global"):
        user_id = remainder[: -len("-global")]
        return {"user_id": user_id, "scope_type": "global", "scope_id": ""} if user_id else None

    user_id, separator, folder_id = remainder.rpartition("-folder-")
    if separator and user_id and folder_id:
        return {"user_id": user_id, "scope_type": "folder", "scope_id": folder_id}

    return None


async def can_access_agent_memory_collection(
    collection_name: str,
    user: Any,
    access_type: str = "read",
    db: AsyncSession | None = None,
) -> bool:
    if access_type != "read":
        return False

    user_id = getattr(user, "id", None)
    if not user_id:
        return False

    if collection_name == agent_memory_collection_name(user_id, "global"):
        return True

    folder_prefix = f"{AGENT_MEMORY_COLLECTION_PREFIX}{user_id}-folder-"
    if not collection_name.startswith(folder_prefix):
        return False

    folder_id = collection_name[len(folder_prefix) :]
    if not folder_id:
        return False

    folder = await Folders.get_folder_by_id_and_user_id(folder_id, user_id, db=db)
    return bool(folder and not is_agent_memory_disabled(folder.meta))


async def resolve_agent_memory_scopes(
    user_id: str,
    chat_id: str | None = None,
    db: AsyncSession | None = None,
) -> list[AgentMemoryScope]:
    if chat_id and (chat_id.startswith("local:") or chat_id.startswith("channel:")):
        return []

    scopes: list[AgentMemoryScope] = []
    if chat_id:
        chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id, db=db)
        if chat:
            if is_agent_memory_disabled(chat.meta):
                return []
            if chat.folder_id:
                folder = await Folders.get_folder_by_id_and_user_id(chat.folder_id, user_id, db=db)
                if folder and is_agent_memory_disabled(folder.meta):
                    return []
                if folder:
                    scopes.append(AgentMemoryScope("folder", chat.folder_id, "current_folder"))

    scopes.append(AgentMemoryScope("global", "", "global"))
    return scopes


def _section_heading(line: str) -> str | None:
    match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    return match.group(2).strip().strip("#").strip() or None


def _markdown_sections(content: str, fallback_heading: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in (content or "").splitlines():
        heading = _section_heading(line)
        if heading:
            if current_heading is not None or current_lines:
                sections.append((current_heading or fallback_heading, current_lines))
            current_heading = heading
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_heading is not None or current_lines:
        sections.append((current_heading or fallback_heading, current_lines))

    return [(heading, "\n".join(lines).strip()) for heading, lines in sections if "\n".join(lines).strip()]


def _chunk_id(
    user_id: str,
    scope_type: str,
    scope_id: str,
    path: str,
    revision: int,
    heading: str,
    chunk_index: int,
) -> str:
    raw = "\0".join([user_id, scope_type, scope_id, path, str(revision), heading, str(chunk_index)])
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest[:16], version=5))


def chunk_agent_memory_artifact(artifact: AgentMemoryArtifactModel | Any) -> list[AgentMemoryChunk]:
    fallback_heading = getattr(artifact, "path", "Agent Memory")
    sections = _markdown_sections(getattr(artifact, "content", ""), fallback_heading)
    chunks: list[AgentMemoryChunk] = []

    for index, (heading, text) in enumerate(sections):
        metadata = {
            "user_id": artifact.user_id,
            "scope_type": artifact.scope_type,
            "scope_id": artifact.scope_id,
            "path": artifact.path,
            "revision": artifact.revision,
            "heading": heading,
            "chunk_index": index,
        }
        chunks.append(
            AgentMemoryChunk(
                id=_chunk_id(
                    artifact.user_id,
                    artifact.scope_type,
                    artifact.scope_id,
                    artifact.path,
                    artifact.revision,
                    heading,
                    index,
                ),
                text=text,
                metadata=metadata,
            )
        )

    return chunks


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _embed_texts(embedding_function: Any, texts: list[str]) -> list[list[float | int]]:
    if not texts:
        return []
    try:
        vectors = await _maybe_await(embedding_function(texts, prefix=RAG_EMBEDDING_QUERY_PREFIX))
    except TypeError:
        vectors = [await _maybe_await(embedding_function(text)) for text in texts]
    if vectors and isinstance(vectors[0], (int, float)):
        return [vectors]
    return vectors


async def _embed_query(embedding_function: Any, query: str) -> list[float | int]:
    vectors = await _embed_texts(embedding_function, [query])
    return vectors[0] if vectors else []


def _stale_artifact_vector_filter(artifact: AgentMemoryArtifactModel | Any) -> dict[str, Any]:
    return {
        "user_id": artifact.user_id,
        "scope_type": artifact.scope_type,
        "scope_id": artifact.scope_id,
        "path": artifact.path,
        "revision": artifact.revision,
    }


async def _scope_is_current_and_enabled(
    user_id: str,
    scope_type: str,
    scope_id: str,
    db: AsyncSession | None = None,
) -> bool:
    if scope_type == "global":
        return True
    if scope_type != "folder" or not scope_id:
        return False

    folder = await Folders.get_folder_by_id_and_user_id(scope_id, user_id, db=db)
    return bool(folder and not is_agent_memory_disabled(folder.meta))


async def _artifact_snapshot_is_current(
    artifact: AgentMemoryArtifactModel | Any,
    db: AsyncSession | None = None,
) -> bool:
    if not await _scope_is_current_and_enabled(artifact.user_id, artifact.scope_type, artifact.scope_id, db=db):
        return False

    current = await AgentMemoryArtifacts.get_artifact(
        artifact.user_id,
        artifact.scope_type,
        artifact.scope_id,
        artifact.path,
        db=db,
    )
    return bool(current and current.revision == artifact.revision)


async def _delete_stale_artifact_vectors(collection_name: str, artifact: AgentMemoryArtifactModel | Any) -> None:
    await ASYNC_VECTOR_DB_CLIENT.delete(
        collection_name=collection_name,
        filter=_stale_artifact_vector_filter(artifact),
    )


async def rebuild_agent_memory_index_for_scope(
    request: Any,
    user_id: str,
    scope_type: str,
    scope_id: str = "",
    db: AsyncSession | None = None,
) -> None:
    collection_name = agent_memory_collection_name(user_id, scope_type, scope_id)
    artifacts = await AgentMemoryArtifacts.list_artifacts(user_id, scope_type, scope_id, db=db)
    embedding_function = getattr(request.app.state, "EMBEDDING_FUNCTION", None)
    if not embedding_function:
        return

    for artifact in artifacts:
        chunks = chunk_agent_memory_artifact(artifact)
        await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=collection_name, filter={"path": artifact.path})
        if not await _artifact_snapshot_is_current(artifact, db=db):
            await _delete_stale_artifact_vectors(collection_name, artifact)
            continue
        if not chunks:
            continue
        vectors = await _embed_texts(embedding_function, [chunk.text for chunk in chunks])
        if not await _artifact_snapshot_is_current(artifact, db=db):
            await _delete_stale_artifact_vectors(collection_name, artifact)
            continue
        items = [
            VectorItem(id=chunk.id, text=chunk.text, vector=vector, metadata=chunk.metadata)
            for chunk, vector in zip(chunks, vectors)
        ]
        if items:
            await ASYNC_VECTOR_DB_CLIENT.upsert(collection_name=collection_name, items=items)
            if not await _artifact_snapshot_is_current(artifact, db=db):
                await _delete_stale_artifact_vectors(collection_name, artifact)


def _coerce_limit(limit: int | None, default: int = 5) -> int:
    try:
        value = int(limit if limit is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(AGENT_MEMORY_TOOL_LIMIT_CAP, value))


def _candidate_limit(result_limit: int) -> int:
    expanded = max(result_limit * 4, result_limit + 10)
    return min(expanded, AGENT_MEMORY_VECTOR_CANDIDATE_LIMIT_CAP)


def _coerce_offset(offset: int | None) -> int:
    try:
        value = int(offset or 0)
    except (TypeError, ValueError):
        raise ValueError("offset must be a non-negative integer")
    if value < 0:
        raise ValueError("offset must be a non-negative integer")
    return value


def _coerce_max_chars(max_chars: int | None) -> int:
    try:
        value = int(max_chars if max_chars is not None else AGENT_MEMORY_READ_DEFAULT_MAX_CHARS)
    except (TypeError, ValueError):
        raise ValueError("max_chars must be a positive integer")
    if value <= 0:
        raise ValueError("max_chars must be a positive integer")
    return min(value, AGENT_MEMORY_READ_MAX_CHARS_CAP)


def _coerce_revision(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _artifact_allowlist(artifacts: Iterable[Any]) -> set[tuple[str, int]]:
    allowed: set[tuple[str, int]] = set()
    for artifact in artifacts:
        path = getattr(artifact, "path", None)
        revision = _coerce_revision(getattr(artifact, "revision", None))
        if isinstance(path, str) and path and revision is not None:
            allowed.add((path, revision))
    return allowed


def _result_rows(
    collection_name: str,
    result: SearchResult | None,
    threshold: float,
    include_collection: bool,
    allowed_artifacts: set[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    if not result or not result.documents:
        return []

    documents = result.documents[0] if result.documents else []
    metadatas = result.metadatas[0] if result.metadatas else []
    distances = result.distances[0] if result.distances else []
    rows: list[dict[str, Any]] = []

    for index, document in enumerate(documents):
        distance = distances[index] if index < len(distances) else None
        if distance is None:
            continue
        if distance is not None and threshold > 0 and distance < threshold:
            continue
        metadata = metadatas[index] if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
        if allowed_artifacts is not None:
            path = metadata.get("path")
            revision = _coerce_revision(metadata.get("revision"))
            if not isinstance(path, str) or revision is None or (path, revision) not in allowed_artifacts:
                continue
        scope_type = metadata.get("scope_type") or "global"
        row = {
            "scope": "current_folder" if scope_type == "folder" else "global",
            "path": metadata.get("path") or "",
            "heading": metadata.get("heading") or "",
            "content": document,
            "score": distance,
        }
        if include_collection:
            row["collection"] = collection_name
        rows.append(row)

    return rows


async def search_agent_memory_collections(
    request: Any,
    collection_names: Iterable[str],
    query: str,
    limit: int | None = 5,
    include_collection: bool = False,
    allowed_artifacts_by_collection: dict[str, set[tuple[str, int]]] | None = None,
) -> list[dict[str, Any]]:
    embedding_function = getattr(request.app.state, "EMBEDDING_FUNCTION", None)
    if not embedding_function or not query:
        return []

    query_vector = await _embed_query(embedding_function, query)
    if not query_vector:
        return []

    result_limit = _coerce_limit(limit)
    candidate_limit = _candidate_limit(result_limit)
    threshold = float(getattr(request.app.state.config, "RELEVANCE_THRESHOLD", 0.0) or 0.0)
    rows: list[dict[str, Any]] = []
    for collection_name in collection_names:
        result = await ASYNC_VECTOR_DB_CLIENT.search(
            collection_name=collection_name,
            vectors=[query_vector],
            limit=candidate_limit,
        )
        allowed_artifacts = (
            None
            if allowed_artifacts_by_collection is None
            else allowed_artifacts_by_collection.get(collection_name, set())
        )
        rows.extend(_result_rows(collection_name, result, threshold, include_collection, allowed_artifacts))
        if len(rows) >= result_limit:
            break

    return rows[:result_limit]


async def search_agent_memory_for_chat(
    request: Any,
    user_id: str,
    chat_id: str | None,
    query: str,
    limit: int | None = 5,
    db: AsyncSession | None = None,
    include_collection: bool = False,
) -> list[dict[str, Any]]:
    scopes = await resolve_agent_memory_scopes(user_id, chat_id, db=db)
    collection_names = []
    allowed_artifacts_by_collection: dict[str, set[tuple[str, int]]] = {}
    for scope in scopes:
        artifacts = await AgentMemoryArtifacts.list_artifacts(
            user_id,
            scope.scope_type,
            scope.scope_id,
            db=db,
        )
        allowed_artifacts = _artifact_allowlist(artifacts)
        if allowed_artifacts:
            collection_name = agent_memory_collection_name(user_id, scope.scope_type, scope.scope_id)
            collection_names.append(collection_name)
            allowed_artifacts_by_collection[collection_name] = allowed_artifacts
    if not collection_names:
        return []
    return await search_agent_memory_collections(
        request,
        collection_names,
        query,
        limit=limit,
        include_collection=include_collection,
        allowed_artifacts_by_collection=allowed_artifacts_by_collection,
    )


async def read_agent_memory_artifact(
    user_id: str,
    chat_id: str | None,
    path: str,
    scope: str | None = None,
    offset: int | None = None,
    max_chars: int | None = None,
    db: AsyncSession | None = None,
) -> dict[str, Any] | None:
    if path not in AGENT_MEMORY_ARTIFACT_PATHS:
        raise ValueError("path must be memory_summary.md or MEMORY.md")
    if scope not in {None, "current_folder", "global"}:
        raise ValueError("scope must be current_folder or global")

    resolved_offset = _coerce_offset(offset)
    resolved_max_chars = _coerce_max_chars(max_chars)
    scopes = await resolve_agent_memory_scopes(user_id, chat_id, db=db)
    if scope:
        scopes = [candidate for candidate in scopes if candidate.label == scope]

    for candidate in scopes:
        artifact = await AgentMemoryArtifacts.get_artifact(
            user_id,
            candidate.scope_type,
            candidate.scope_id,
            path,
            db=db,
        )
        if artifact:
            content_length = len(artifact.content)
            window_end = min(content_length, resolved_offset + resolved_max_chars)
            content = artifact.content[resolved_offset:window_end]
            return {
                "scope": candidate.label,
                "path": artifact.path,
                "content": content,
                "revision": artifact.revision,
                "updated_at": artifact.updated_at,
                "offset": resolved_offset,
                "max_chars": resolved_max_chars,
                "content_length": content_length,
                "truncated": resolved_offset > 0 or window_end < content_length,
            }
    return None


async def list_agent_memory_artifacts(
    user_id: str,
    chat_id: str | None,
    scope: str | None = None,
    db: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    if scope not in {None, "all_current", "current_folder", "global"}:
        raise ValueError("scope must be current_folder, global, or all_current")
    scopes = await resolve_agent_memory_scopes(user_id, chat_id, db=db)
    if scope and scope != "all_current":
        scopes = [candidate for candidate in scopes if candidate.label == scope]
    artifacts: list[dict[str, Any]] = []
    for scope in scopes:
        scope_artifacts = await AgentMemoryArtifacts.list_artifacts(
            user_id,
            scope.scope_type,
            scope.scope_id,
            db=db,
        )
        artifacts.extend(
            {"scope": scope.label, "path": artifact.path, "revision": artifact.revision}
            for artifact in scope_artifacts
        )
    return artifacts


def truncate_summary_text(text: str, remaining_budget: int) -> tuple[str, int]:
    if remaining_budget <= 0:
        return "", 0
    token_budget = max(0, int(remaining_budget))
    if token_budget <= 0:
        return "", 0
    text = text or ""
    try:
        import tiktoken

        try:
            encoding = tiktoken.get_encoding(str(TIKTOKEN_ENCODING_NAME))
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")

        tokens = encoding.encode(text)
        if len(tokens) <= token_budget:
            return text, max(0, remaining_budget - len(tokens))

        summary = encoding.decode(tokens[:token_budget])
        while summary and len(encoding.encode(summary)) > token_budget:
            summary = summary[:-1]
        return summary, 0
    except Exception:
        return "", 0


async def build_agent_memory_read_context(
    user_id: str,
    chat_id: str | None,
    token_budget: int,
    db: AsyncSession | None = None,
) -> str:
    scopes = await resolve_agent_memory_scopes(user_id, chat_id, db=db)
    if not scopes:
        return ""
    parts = [AGENT_MEMORY_POLICY]
    remaining = max(0, int(token_budget or 0))

    for scope in scopes:
        artifact = await AgentMemoryArtifacts.get_artifact(
            user_id,
            scope.scope_type,
            scope.scope_id,
            "memory_summary.md",
            db=db,
        )
        if not artifact:
            continue
        summary, remaining = truncate_summary_text(artifact.content, remaining)
        if summary:
            heading = "Current Folder Agent Memory" if scope.label == "current_folder" else "Global Agent Memory"
            parts.append(f"{heading}:\n{summary}")
        if remaining <= 0:
            break

    return "\n\n".join(parts)
