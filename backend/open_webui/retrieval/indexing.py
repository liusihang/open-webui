from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
import time
import uuid
from typing import Any, Iterable, Protocol

from open_webui.internal.db import get_async_db
from open_webui.models.evidence import KnowledgeEvidences, KnowledgeVectorSpaces
from open_webui.models.retrieval_chunks import (
    RetrievalChunk,
    compute_chunk_uid,
    compute_chunker_config_hash,
    compute_content_hash,
)
from open_webui.models.retrieval_indexes import (
    RetrievalIndexJob,
    RetrievalIndexJobs,
    RetrievalIndexState,
    RetrievalIndexStates,
    compute_index_state_id,
    compute_target_config_hash,
)
from open_webui.retrieval.evidence_projector import (
    finalize_projected_evidence_from_job_payload,
    project_evidence_from_job_payload,
)
from open_webui.retrieval.lexical.opensearch import OpenSearchLexicalClient
from open_webui.retrieval.vector.multimodal import (
    MultimodalVectorSpaceError,
    MultimodalVectorSpaceSelection,
    resolve_multimodal_vector_space,
    upsert_multimodal_evidence_embedding,
)
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class VectorChunkRecord:
    id: str
    collection_name: str
    text: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReindexResult:
    scanned: int = 0
    manifest_upserted: int = 0
    manifest_deactivated: int = 0
    metadata_patched: int = 0
    lexical_indexed: int = 0
    failed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    index_version: int = 1
    alias_promoted: bool = False
    chunk_uids: list[str] = field(default_factory=list)
    chunk_uid_sample: list[str] = field(default_factory=list)
    chunk_uid_sample_truncated: bool = False
    unsupported: bool = False

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ManifestDeactivationResult:
    deactivated: int = 0
    lexical_delete_enqueued: int = 0
    lexical_delete_executed: int = 0
    chunk_uids: list[str] = field(default_factory=list)
    delete_job_id: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceEmbeddingProjectionResult:
    written: int = 0
    skipped: int = 0
    failed: int = 0
    evidence_refs: list[str] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class VectorChunkStore(Protocol):
    def iter_chunks(self, collection_ids: list[str] | None = None) -> Iterable[VectorChunkRecord]:
        ...

    def patch_chunk_metadata(self, row_id: str, metadata: dict[str, Any]) -> None:
        ...


class ManifestChunkStore(Protocol):
    def upsert_chunks(self, chunks: Iterable[dict[str, Any]]) -> int:
        ...

    def deactivate_absent_chunks(
        self,
        *,
        active_chunk_uids: Iterable[str],
        collection_ids: list[str] | None,
        deleted_at: int,
    ) -> int:
        ...

    def count_chunks(self) -> dict[str, int]:
        ...


class SqlAlchemyVectorChunkStore:
    def __init__(
        self,
        *,
        session: Any,
        yield_per: int = 1000,
        document_chunk_model: Any | None = None,
    ) -> None:
        if document_chunk_model is None:
            from open_webui.retrieval.vector.dbs.pgvector import DocumentChunk

            document_chunk_model = DocumentChunk

        self._document_chunk = document_chunk_model
        self.session = session
        self.yield_per = yield_per

    @classmethod
    def from_existing_or_lightweight_session(
        cls,
        *,
        document_chunk_model: Any | None = None,
    ) -> "SqlAlchemyVectorChunkStore":
        session = _existing_pgvector_session()
        if session is None:
            session = _lightweight_pgvector_session()
        return cls(session=session, document_chunk_model=document_chunk_model)

    def iter_chunks(self, collection_ids: list[str] | None = None) -> Iterable[VectorChunkRecord]:
        query = self.session.query(self._document_chunk)
        if collection_ids:
            query = query.filter(self._document_chunk.collection_name.in_(collection_ids))
        query = query.order_by(self._document_chunk.collection_name.asc(), self._document_chunk.id.asc())

        for row in query.yield_per(self.yield_per):
            metadata = row.vmetadata if isinstance(row.vmetadata, dict) else {}
            yield VectorChunkRecord(
                id=row.id,
                collection_name=row.collection_name,
                text=row.text,
                metadata=dict(metadata),
            )

    def patch_chunk_metadata(self, row_id: str, metadata: dict[str, Any]) -> None:
        row = self.session.query(self._document_chunk).filter(self._document_chunk.id == row_id).first()
        if row is None:
            raise RuntimeError(f"document_chunk row {row_id!r} disappeared during reindex")
        row.vmetadata = dict(metadata)
        self.session.commit()


class SqlAlchemyManifestChunkStore:
    def __init__(self, *, session: Any | None = None) -> None:
        if session is None:
            from open_webui.internal.db import ScopedSession

            session = ScopedSession
        self.session = session

    def upsert_chunks(self, chunks: Iterable[dict[str, Any]]) -> int:
        count = 0
        try:
            for chunk in chunks:
                existing = (
                    self.session.query(RetrievalChunk)
                    .filter(RetrievalChunk.chunk_uid == chunk["chunk_uid"])
                    .first()
                )
                values = self._model_values(chunk)
                if existing:
                    values.pop("created_at", None)
                    for key, value in values.items():
                        setattr(existing, key, value)
                else:
                    self.session.add(RetrievalChunk(**values))
                count += 1
            self.session.commit()
            return count
        except Exception:
            self.session.rollback()
            raise

    def count_chunks(self) -> dict[str, int]:
        total = self.session.query(RetrievalChunk).count()
        active = self.session.query(RetrievalChunk).filter(RetrievalChunk.is_active.is_(True)).count()
        return {"total": int(total), "active": int(active)}

    def deactivate_absent_chunks(
        self,
        *,
        active_chunk_uids: Iterable[str],
        collection_ids: list[str] | None,
        deleted_at: int,
    ) -> int:
        active_chunk_uids = set(active_chunk_uids)
        try:
            query = self.session.query(RetrievalChunk).filter(RetrievalChunk.is_active.is_(True))
            if collection_ids:
                query = query.filter(
                    or_(
                        RetrievalChunk.collection_id.in_(collection_ids),
                        RetrievalChunk.collection_name.in_(collection_ids),
                    )
                )
            if active_chunk_uids:
                query = query.filter(~RetrievalChunk.chunk_uid.in_(active_chunk_uids))
            count = query.update(
                {
                    RetrievalChunk.is_active: False,
                    RetrievalChunk.deleted_at: deleted_at,
                    RetrievalChunk.updated_at: deleted_at,
                },
                synchronize_session=False,
            )
            self.session.commit()
            return int(count or 0)
        except Exception:
            self.session.rollback()
            raise

    @staticmethod
    def _model_values(chunk: dict[str, Any]) -> dict[str, Any]:
        return {
            "chunk_uid": chunk["chunk_uid"],
            "collection_id": chunk.get("collection_id"),
            "knowledge_id": chunk.get("knowledge_id"),
            "collection_name": chunk.get("collection_name"),
            "file_id": chunk.get("file_id"),
            "file_version": chunk.get("file_version", 1),
            "chunk_version": chunk.get("chunk_version", 1),
            "chunk_index": chunk.get("chunk_index"),
            "start_index": chunk.get("start_index"),
            "content_hash": chunk["content_hash"],
            "chunker_config_hash": chunk["chunker_config_hash"],
            "text": chunk.get("text"),
            "metadata_": chunk.get("metadata") or {},
            "is_active": chunk.get("is_active", True),
            "deleted_at": chunk.get("deleted_at"),
            "created_at": chunk.get("created_at"),
            "updated_at": chunk.get("updated_at"),
        }


class RetrievalIndexingService:
    def __init__(
        self,
        *,
        vector_store: VectorChunkStore,
        manifest_store: ManifestChunkStore,
        lexical_client: OpenSearchLexicalClient,
        pgcrypto_enabled: bool = False,
        now_fn: Any | None = None,
        failure_limit: int = 50,
        chunk_uid_sample_limit: int = 50,
    ) -> None:
        self.vector_store = vector_store
        self.manifest_store = manifest_store
        self.lexical_client = lexical_client
        self.pgcrypto_enabled = pgcrypto_enabled
        self.now_fn = now_fn or (lambda: int(time.time()))
        self.failure_limit = failure_limit
        self.chunk_uid_sample_limit = chunk_uid_sample_limit

    def reindex_lexical(
        self,
        *,
        collection_ids: list[str] | None = None,
        index_version: int = 1,
        promote_alias: bool = True,
        batch_size: int = 500,
    ) -> ReindexResult:
        result = ReindexResult(index_version=index_version)
        validation_error = self._validation_error(
            collection_ids=collection_ids,
            promote_alias=promote_alias,
            batch_size=batch_size,
            index_version=index_version,
        )
        if validation_error:
            result.failed = 1
            self._record_failure(result, validation_error)
            return result

        if self.pgcrypto_enabled:
            result.failed = 1
            result.unsupported = True
            self._record_failure(
                result,
                {
                    "error": "PGVECTOR_PGCRYPTO document_chunk backfill is not supported by the lexical reindex service",
                },
            )
            return result

        rows = list(self.vector_store.iter_chunks(collection_ids=collection_ids))
        result.scanned = len(rows)
        chunks, patch_plan = self._derive_manifest_chunks(rows, result)
        active_chunk_uids = [chunk["chunk_uid"] for chunk in chunks]
        if not chunks:
            result.manifest_deactivated = self.manifest_store.deactivate_absent_chunks(
                active_chunk_uids=[],
                collection_ids=collection_ids,
                deleted_at=int(self.now_fn()),
            )
            return result

        try:
            result.manifest_upserted = self.manifest_store.upsert_chunks(chunks)
        except Exception as exc:
            result.failed += 1
            self._record_failure(result, {"error": str(exc), "stage": "manifest_upsert"})
            return result

        try:
            result.manifest_deactivated = self.manifest_store.deactivate_absent_chunks(
                active_chunk_uids=active_chunk_uids,
                collection_ids=collection_ids,
                deleted_at=int(self.now_fn()),
            )
        except Exception as exc:
            result.failed += 1
            self._record_failure(result, {"error": str(exc), "stage": "manifest_deactivate_absent"})
            return result

        for row_id, metadata in patch_plan:
            try:
                self.vector_store.patch_chunk_metadata(row_id, metadata)
                result.metadata_patched += 1
            except Exception as exc:
                result.failed += 1
                self._record_failure(
                    result,
                    {
                        "vector_id": row_id,
                        "error": str(exc),
                        "stage": "metadata_patch",
                    },
                )
                return result

        try:
            target_index = self.lexical_client.ensure_index(version=index_version)
        except Exception as exc:
            result.failed += 1
            self._record_failure(result, {"error": str(exc), "stage": "lexical_ensure_index"})
            return result

        original_alias = self.lexical_client.alias
        try:
            self.lexical_client.alias = target_index
            result.lexical_indexed = self.lexical_client.bulk_upsert(chunks, batch_size=batch_size)
        except Exception as exc:
            result.lexical_indexed = 0
            result.failed += 1
            self._record_failure(result, {"error": str(exc), "stage": "lexical_bulk_upsert"})
            return result
        finally:
            self.lexical_client.alias = original_alias

        if promote_alias:
            try:
                self.lexical_client.promote_index(version=index_version)
                result.alias_promoted = True
            except Exception as exc:
                result.failed += 1
                self._record_failure(result, {"error": str(exc), "stage": "lexical_promote_index"})
        self._set_chunk_uid_sample(result, chunks)
        return result

    def get_status(self) -> dict[str, Any]:
        status = {"manifest": self.manifest_store.count_chunks(), "lexical": {}}
        try:
            if hasattr(self.lexical_client, "status"):
                status["lexical"] = self.lexical_client.status()
            else:
                status["lexical"] = self._default_lexical_status()
        except Exception as exc:
            status["lexical"] = {"error": str(exc)}
        return status

    def _derive_manifest_chunks(
        self,
        rows: list[VectorChunkRecord],
        result: ReindexResult,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
        prepared = []
        for row in rows:
            metadata = dict(row.metadata or {})
            if row.text is None:
                result.failed += 1
                self._record_failure(
                    result,
                    {
                        "vector_id": row.id,
                        "collection_name": row.collection_name,
                        "error": "document_chunk.text is None",
                    },
                )
                continue

            start_index = _int_or_none(metadata.get("start_index"))
            prepared.append(
                {
                    "row": row,
                    "metadata": metadata,
                    "start_index": start_index,
                    "content_hash": compute_content_hash(row.text),
                    "explicit_chunk_index": _int_or_none(metadata.get("chunk_index")),
                }
            )

        ordinal_by_row_id = self._assign_missing_chunk_indexes(prepared)
        chunks = []
        patch_plan = []
        now = int(self.now_fn())
        for item in prepared:
            row = item["row"]
            metadata = item["metadata"]
            chunk_index = item["explicit_chunk_index"]
            if chunk_index is None:
                chunk_index = ordinal_by_row_id[row.id]

            collection_id = metadata.get("collection_id") or row.collection_name
            knowledge_id = metadata.get("knowledge_id") or row.collection_name
            file_id = metadata.get("file_id")
            file_version = _int_or_default(metadata.get("file_version"), 1)
            chunk_version = _int_or_default(metadata.get("chunk_version"), 1)
            chunker_config_hash = metadata.get("chunker_config_hash") or self._legacy_chunker_config_hash(metadata)
            chunk_uid = compute_chunk_uid(
                collection_id=collection_id,
                knowledge_id=knowledge_id,
                collection_name=row.collection_name,
                file_id=file_id,
                file_version=file_version,
                chunker_config_hash=chunker_config_hash,
                chunk_index=chunk_index,
                content_hash=item["content_hash"],
            )
            manifest_metadata = {
                **metadata,
                "chunk_uid": chunk_uid,
                "vector_id": row.id,
                "collection_id": collection_id,
                "knowledge_id": knowledge_id,
                "collection_name": row.collection_name,
                "file_id": file_id,
                "file_version": file_version,
                "chunk_version": chunk_version,
                "chunk_index": chunk_index,
                "content_hash": item["content_hash"],
                "chunker_config_hash": chunker_config_hash,
            }
            if item["start_index"] is not None:
                manifest_metadata["start_index"] = item["start_index"]

            chunks.append(
                {
                    "chunk_uid": chunk_uid,
                    "collection_id": collection_id,
                    "knowledge_id": knowledge_id,
                    "collection_name": row.collection_name,
                    "file_id": file_id,
                    "file_version": file_version,
                    "chunk_version": chunk_version,
                    "chunk_index": chunk_index,
                    "start_index": item["start_index"],
                    "content_hash": item["content_hash"],
                    "chunker_config_hash": chunker_config_hash,
                    "text": row.text,
                    "metadata": manifest_metadata,
                    "is_active": True,
                    "deleted_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            vector_metadata = dict(metadata)
            vector_metadata["chunk_uid"] = chunk_uid
            patch_plan.append((row.id, vector_metadata))

        return chunks, patch_plan

    def _assign_missing_chunk_indexes(self, prepared: list[dict[str, Any]]) -> dict[str, int]:
        grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for item in prepared:
            if item["explicit_chunk_index"] is not None:
                continue
            row = item["row"]
            metadata = item["metadata"]
            group_id = (
                metadata.get("file_id")
                or metadata.get("hash")
                or metadata.get("source")
                or metadata.get("name")
                or "__collection__"
            )
            grouped[(row.collection_name, group_id)].append(item)

        ordinals = {}
        for items in grouped.values():
            sorted_items = sorted(
                items,
                key=lambda item: (
                    item["start_index"] is None,
                    item["start_index"] if item["start_index"] is not None else 0,
                    item["row"].id,
                ),
            )
            for index, item in enumerate(sorted_items):
                ordinals[item["row"].id] = index
        return ordinals

    @staticmethod
    def _legacy_chunker_config_hash(metadata: dict[str, Any]) -> str:
        splitter_keys = (
            "chunk_size",
            "chunk_overlap",
            "overlap",
            "separators",
            "splitter",
            "splitter_name",
            "length_function",
            "chunker",
            "chunker_config",
        )
        splitter_metadata = {key: metadata[key] for key in splitter_keys if key in metadata}
        return compute_chunker_config_hash(
            {
                "source": "document_chunk",
                "version": 1,
                "splitter_metadata": splitter_metadata,
            }
        )

    def _default_lexical_status(self) -> dict[str, Any]:
        bindings = getattr(self.lexical_client, "_alias_bindings", None)
        if callable(bindings):
            owned, non_owned = bindings()
            return {
                "alias": self.lexical_client.alias,
                "owned_indices": owned,
                "non_owned_indices": non_owned,
            }
        return {"alias": self.lexical_client.alias}

    def _record_failure(self, result: ReindexResult, failure: dict[str, Any]) -> None:
        if len(result.failures) < self.failure_limit:
            result.failures.append(failure)

    @staticmethod
    def _validation_error(
        *,
        collection_ids: list[str] | None,
        promote_alias: bool,
        batch_size: int,
        index_version: int,
    ) -> dict[str, Any] | None:
        if batch_size < 1:
            return {
                "error": "batch_size must be at least 1",
                "stage": "validation",
            }
        if index_version < 1:
            return {
                "error": "index_version must be at least 1",
                "stage": "validation",
            }
        if collection_ids and promote_alias:
            return {
                "error": "promote_alias=True is not allowed for scoped lexical reindex",
                "stage": "validation",
            }
        return None

    def _set_chunk_uid_sample(self, result: ReindexResult, chunks: list[dict[str, Any]]) -> None:
        chunk_uids = [chunk["chunk_uid"] for chunk in chunks]
        result.chunk_uid_sample = chunk_uids[: self.chunk_uid_sample_limit]
        result.chunk_uid_sample_truncated = len(chunk_uids) > len(result.chunk_uid_sample)
        result.chunk_uids = list(result.chunk_uid_sample)


def reindex_lexical_from_current_vector_store(
    *,
    collection_ids: list[str] | None = None,
    index_version: int = 1,
    promote_alias: bool = True,
    batch_size: int = 500,
) -> ReindexResult:
    service = build_default_indexing_service()
    return service.reindex_lexical(
        collection_ids=collection_ids,
        index_version=index_version,
        promote_alias=promote_alias,
        batch_size=batch_size,
    )


def get_retrieval_index_status() -> dict[str, Any]:
    service = RetrievalIndexingService(
        vector_store=_NoopVectorChunkStore(),
        manifest_store=SqlAlchemyManifestChunkStore(),
        lexical_client=OpenSearchLexicalClient(),
    )
    return service.get_status()


async def get_retrieval_index_status_async() -> dict[str, Any]:
    status = await asyncio.to_thread(get_retrieval_index_status)
    try:
        status["jobs"] = [
            job.model_dump()
            for job in await RetrievalIndexJobs.list_jobs(limit=20)
        ]
    except Exception as exc:
        status["jobs"] = {"error": str(exc)}
    try:
        status["states"] = [
            state.model_dump()
            for state in await RetrievalIndexStates.list_states(limit=50)
        ]
    except Exception as exc:
        status["states"] = {"error": str(exc)}
    return status


def lexical_target_config_hash(*, index_version: int) -> str:
    return compute_target_config_hash(
        {
            "index_kind": "lexical",
            "engine": "opensearch",
            "index_prefix": "retrieval_lexical",
            "alias": "retrieval_lexical_current",
            "index_version": index_version,
        }
    )


def lexical_current_alias_config_hash() -> str:
    return compute_target_config_hash(
        {
            "index_kind": "lexical",
            "engine": "opensearch",
            "alias": "retrieval_lexical_current",
            "target": "current_alias",
        }
    )


def evidence_target_config_hash(
    *,
    knowledge_id: str | None = None,
    file_ids: list[str] | None = None,
    project_document_images: bool = False,
) -> str:
    return compute_target_config_hash(
        {
            "index_kind": "project",
            "workflow": "evidence_projection",
            "knowledge_id": knowledge_id,
            "file_ids": list(dict.fromkeys(file_ids or [])),
            "project_document_images": bool(project_document_images),
        }
    )


async def enqueue_retrieval_index_job(
    *,
    index_kind: str,
    collection_ids: list[str] | None = None,
    index_version: int = 1,
    promote_alias: bool = True,
    batch_size: int = 500,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_config_hash = lexical_target_config_hash(index_version=index_version)
    payload = {
        "collection_ids": collection_ids,
        "index_version": index_version,
        "promote_alias": promote_alias,
        "batch_size": batch_size,
        **(payload or {}),
    }
    scope_id = collection_ids[0] if collection_ids and len(collection_ids) == 1 else None
    job = await RetrievalIndexJobs.enqueue_job(
        index_kind=index_kind,
        collection_id=scope_id,
        knowledge_id=scope_id,
        collection_name=scope_id,
        target_config_hash=target_config_hash,
        payload=payload,
    )
    state = await RetrievalIndexStates.upsert_state(
        index_kind="lexical" if index_kind in {"lexical", "full"} else index_kind,
        status="pending",
        collection_id=scope_id,
        knowledge_id=scope_id,
        collection_name=scope_id,
        target_config_hash=target_config_hash,
        last_job_id=job.job_id,
    )
    return {"job": job.model_dump(), "state": state.model_dump()}


async def enqueue_evidence_projection_job(
    *,
    knowledge_id: str,
    file_ids: list[str] | None = None,
    project_document_images: bool = False,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_config_hash = evidence_target_config_hash(
        knowledge_id=knowledge_id,
        file_ids=file_ids,
        project_document_images=project_document_images,
    )
    job_payload = {
        "workflow": "evidence_projection",
        "knowledge_id": knowledge_id,
        "file_ids": list(dict.fromkeys(file_ids or [])),
        "project_document_images": project_document_images,
        **(payload or {}),
    }
    job = await RetrievalIndexJobs.enqueue_job(
        index_kind="project",
        collection_id=knowledge_id,
        knowledge_id=knowledge_id,
        collection_name=knowledge_id,
        target_config_hash=target_config_hash,
        payload=job_payload,
    )
    state = await RetrievalIndexStates.upsert_state(
        index_kind="project",
        status="pending",
        collection_id=knowledge_id,
        knowledge_id=knowledge_id,
        collection_name=knowledge_id,
        target_config_hash=target_config_hash,
        last_job_id=job.job_id,
    )
    return {"job": job.model_dump(), "state": state.model_dump()}


def _get_evidence_embedding_runtime() -> tuple[Any | None, Any | None]:
    try:
        from open_webui.main import app as webui_app
    except Exception:
        return None, None

    state = getattr(webui_app, "state", None)
    if state is None:
        return None, None

    embedding_function = getattr(state, "EVIDENCE_RETRIEVAL_EMBEDDING", None) or getattr(
        state, "EMBEDDING_FUNCTION", None
    )
    vector_client = getattr(state, "EVIDENCE_RETRIEVAL_VECTOR_CLIENT", None)
    if vector_client is None:
        from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

        vector_client = ASYNC_VECTOR_DB_CLIENT
    return embedding_function, vector_client


def _embedding_function_supports_image_payloads(embedding_function: Any) -> bool:
    if bool(getattr(embedding_function, "_supports_image_payloads", False)):
        return True
    return callable(getattr(embedding_function, "_has_image_payload", None))


def _get_evidence_vector_space_defaults(embedding_function: Any) -> dict[str, Any]:
    embedding_model = getattr(embedding_function, "_model", None)
    vector_backend = None

    try:
        from open_webui import config as webui_config

        config_embedding_model = getattr(webui_config, "RAG_EMBEDDING_MODEL", None)
        embedding_model = embedding_model or getattr(config_embedding_model, "value", config_embedding_model)
        vector_backend = getattr(webui_config, "VECTOR_DB", None) or vector_backend
    except Exception:
        pass

    if not vector_backend:
        try:
            from open_webui.config import VECTOR_DB

            vector_backend = VECTOR_DB
        except Exception:
            vector_backend = "pgvector"

    supports_images = _embedding_function_supports_image_payloads(embedding_function)
    return {
        "embedding_model": str(embedding_model or "").strip(),
        "vector_backend": str(vector_backend or "pgvector").strip() or "pgvector",
        "supports_image_query": supports_images,
        "supports_image_evidence": supports_images,
    }


async def _resolve_or_create_evidence_vector_space(
    *,
    evidence,
    embedding_function: Any,
    db: AsyncSession | None = None,
) -> MultimodalVectorSpaceSelection:
    try:
        return await resolve_multimodal_vector_space(
            knowledge_id=evidence.knowledge_id,
            retrieval_profile=evidence.projection_profile,
            evidence_modality=evidence.modality,
            db=db,
        )
    except MultimodalVectorSpaceError as exc:
        if exc.code != "vector_space_unavailable":
            raise

    defaults = _get_evidence_vector_space_defaults(embedding_function)
    embedding_model = defaults["embedding_model"]
    if not embedding_model:
        raise MultimodalVectorSpaceError(
            "vector_space_unavailable",
            "Cannot create evidence vector space without an embedding model",
            details={
                "knowledge_id": evidence.knowledge_id,
                "retrieval_profile": evidence.projection_profile,
            },
        )
    if evidence.modality == "image" and not defaults["supports_image_evidence"]:
        raise MultimodalVectorSpaceError(
            "unsupported_image_evidence",
            "Configured evidence embedding runtime does not support image evidence",
            details={
                "knowledge_id": evidence.knowledge_id,
                "retrieval_profile": evidence.projection_profile,
            },
        )

    vector_space = await KnowledgeVectorSpaces.create_vector_space(
        knowledge_id=evidence.knowledge_id,
        retrieval_profile=evidence.projection_profile,
        embedding_model=embedding_model,
        vector_backend=defaults["vector_backend"],
        supports_text_query=True,
        supports_text_evidence=True,
        supports_image_query=defaults["supports_image_query"],
        supports_image_evidence=defaults["supports_image_evidence"],
        active=True,
        db=db,
    )
    return MultimodalVectorSpaceSelection(
        vector_space=vector_space,
        collection_name=f"{evidence.knowledge_id}:{vector_space.id}",
    )


async def write_projected_evidence_embeddings(
    evidence_refs: list[str],
    *,
    db: AsyncSession | None = None,
) -> EvidenceEmbeddingProjectionResult:
    result = EvidenceEmbeddingProjectionResult()
    if not evidence_refs:
        return result

    embedding_function, vector_client = _get_evidence_embedding_runtime()
    if embedding_function is None:
        result.skipped = len(evidence_refs)
        return result

    for evidence_ref in evidence_refs:
        evidence = await KnowledgeEvidences.get_evidence_by_ref(evidence_ref, db=db)
        if evidence is None:
            result.skipped += 1
            continue

        try:
            vector_space = await _resolve_or_create_evidence_vector_space(
                evidence=evidence,
                embedding_function=embedding_function,
                db=db,
            )
        except MultimodalVectorSpaceError as exc:
            if exc.code == "vector_space_unavailable":
                result.skipped += 1
                continue
            result.failed += 1
            result.failures.append(
                {
                    "evidence_ref": evidence_ref,
                    "error": str(exc),
                }
            )
            continue

        try:
            write_result = await upsert_multimodal_evidence_embedding(
                evidence=evidence,
                vector_space=vector_space.vector_space,
                embedding_function=embedding_function,
                vector_client=vector_client,
                db=db,
            )
            if write_result.embedding and write_result.embedding.embedding_status == "ready":
                result.written += 1
                result.evidence_refs.append(evidence_ref)
            else:
                result.failed += 1
                result.failures.append(
                    {
                        "evidence_ref": evidence_ref,
                        "error": write_result.error or "embedding write failed",
                    }
                )
        except Exception as exc:
            result.failed += 1
            result.failures.append(
                {
                    "evidence_ref": evidence_ref,
                    "error": str(exc),
                }
            )

    return result


async def run_retrieval_index_job(job_id: str) -> dict[str, Any]:
    job = await RetrievalIndexJobs.get_job_by_id(job_id)
    if job is None:
        raise ValueError(f"retrieval index job {job_id!r} not found")

    await RetrievalIndexJobs.update_job_status(job_id, status="running")
    try:
        if job.index_kind in {"lexical", "full"}:
            result = await asyncio.to_thread(
                reindex_lexical_from_current_vector_store,
                collection_ids=(job.payload or {}).get("collection_ids"),
                index_version=int((job.payload or {}).get("index_version", 1)),
                promote_alias=bool((job.payload or {}).get("promote_alias", True)),
                batch_size=int((job.payload or {}).get("batch_size", 500)),
            )
            payload = (
                {
                    "embedding_reindexed": False,
                    "lexical": result.model_dump(),
                }
                if job.index_kind == "full"
                else result.model_dump()
            )
            final_status = "succeeded" if result.failed == 0 else "failed"
            state_status = "ready" if result.failed == 0 else "failed"
            await RetrievalIndexStates.upsert_state(
                index_kind="lexical",
                status=state_status,
                collection_id=job.collection_id,
                knowledge_id=job.knowledge_id,
                collection_name=job.collection_name,
                target_config_hash=job.target_config_hash,
                active_chunk_count=result.manifest_upserted,
                indexed_chunk_count=result.lexical_indexed,
                last_job_id=job.job_id,
                error=(result.failures[0]["error"] if result.failures else None),
            )
            updated = await RetrievalIndexJobs.update_job_status(
                job_id,
                status=final_status,
                result=payload,
                error=(result.failures[0]["error"] if result.failures else None),
            )
            return {"job": updated.model_dump() if updated else None, "result": payload}

        if job.index_kind == "project":
            evidence_result = await project_evidence_from_job_payload(job.payload or {}, activate=False)
            embedding_result = await write_projected_evidence_embeddings(evidence_result.evidence_refs)
            embedding_error = None
            if embedding_result.failures:
                embedding_error = embedding_result.failures[0]["error"]
            elif evidence_result.evidence_refs and embedding_result.skipped:
                embedding_error = (
                    f"evidence embedding write skipped {embedding_result.skipped} projected evidence rows"
                )
            elif evidence_result.evidence_refs and embedding_result.written < len(evidence_result.evidence_refs):
                embedding_error = (
                    f"evidence embedding write produced {embedding_result.written} of "
                    f"{len(evidence_result.evidence_refs)} projected evidence rows"
                )
            payload = {
                "evidence": evidence_result.model_dump(),
                "evidence_embeddings": embedding_result.model_dump(),
            }
            final_status = (
                "succeeded"
                if evidence_result.failed == 0 and embedding_result.failed == 0 and embedding_error is None
                else "failed"
            )
            activation_error = None
            if final_status == "succeeded":
                try:
                    await finalize_projected_evidence_from_job_payload(
                        job.payload or {},
                        evidence_result.evidence_refs,
                    )
                except Exception as exc:
                    activation_error = str(exc)
                    final_status = "failed"
            state_status = "ready" if final_status == "succeeded" else "failed"
            state_count = max(
                evidence_result.scanned_chunks,
                evidence_result.image_assets_upserted,
                evidence_result.document_image_placeholders,
            )
            await RetrievalIndexStates.upsert_state(
                index_kind="project",
                status=state_status,
                collection_id=job.collection_id,
                knowledge_id=job.knowledge_id,
                collection_name=job.collection_name,
                file_id=job.file_id,
                target_config_hash=job.target_config_hash,
                active_chunk_count=state_count,
                indexed_chunk_count=evidence_result.text_evidence_upserted + evidence_result.image_evidence_upserted,
                last_job_id=job.job_id,
                error=(
                    evidence_result.failures[0]["error"]
                    if evidence_result.failures
                    else embedding_error or activation_error
                ),
            )
            updated = await RetrievalIndexJobs.update_job_status(
                job_id,
                status=final_status,
                result=payload,
                error=(
                    evidence_result.failures[0]["error"]
                    if evidence_result.failures
                    else embedding_error or activation_error
                ),
            )
            return {"job": updated.model_dump() if updated else None, "result": payload}

        if job.index_kind == "delete":
            chunk_uids = list(dict.fromkeys((job.payload or {}).get("chunk_uids") or []))
            deleted = await asyncio.to_thread(OpenSearchLexicalClient().delete_chunks, chunk_uids)
            payload = {
                "lexical_deleted": deleted,
                "chunk_uid_count": len(chunk_uids),
                "chunk_uids": chunk_uids[:50],
                "chunk_uids_truncated": len(chunk_uids) > 50,
            }
            await RetrievalIndexStates.upsert_state(
                index_kind="lexical",
                status="deleted",
                collection_id=job.collection_id,
                knowledge_id=job.knowledge_id,
                collection_name=job.collection_name,
                file_id=job.file_id,
                target_config_hash=job.target_config_hash,
                indexed_chunk_count=deleted,
                last_job_id=job.job_id,
            )
            updated = await RetrievalIndexJobs.update_job_status(job_id, status="succeeded", result=payload)
            return {"job": updated.model_dump() if updated else None, "result": payload}

        raise ValueError(f"retrieval index job kind {job.index_kind!r} is not executable yet")
    except Exception as exc:
        await RetrievalIndexJobs.update_job_status(job_id, status="failed", error=str(exc))
        await RetrievalIndexStates.upsert_state(
            index_kind="lexical" if job.index_kind in {"lexical", "full", "delete"} else job.index_kind,
            status="failed",
            collection_id=job.collection_id,
            knowledge_id=job.knowledge_id,
            collection_name=job.collection_name,
            file_id=job.file_id,
            target_config_hash=job.target_config_hash,
            last_job_id=job.job_id,
            error=str(exc),
        )
        raise


@asynccontextmanager
async def _atomic_async_session(db: Any | None):
    if isinstance(db, AsyncSession):
        yield db
    else:
        async with get_async_db() as session:
            yield session


def _chunk_scope_conditions(
    *,
    collection_id: str | None = None,
    collection_name: str | None = None,
    file_id: str | None = None,
) -> list[Any]:
    if not any((collection_id, collection_name, file_id)):
        raise ValueError("retrieval chunk scope requires collection_id, collection_name, or file_id")

    conditions: list[Any] = []
    collection_conditions = []
    if collection_id:
        collection_conditions.append(RetrievalChunk.collection_id == collection_id)
    if collection_name:
        collection_conditions.append(RetrievalChunk.collection_name == collection_name)
    if collection_conditions:
        conditions.append(or_(*collection_conditions))
    if file_id:
        conditions.append(RetrievalChunk.file_id == file_id)
    return conditions


async def _current_lexical_target_config_hash(session: AsyncSession) -> str:
    result = await session.execute(
        select(RetrievalIndexState.target_config_hash)
        .where(
            RetrievalIndexState.index_kind == "lexical",
            RetrievalIndexState.status == "ready",
            RetrievalIndexState.target_config_hash.isnot(None),
        )
        .order_by(RetrievalIndexState.updated_at.desc())
        .limit(1)
    )
    target_config_hash = result.scalars().first()
    return target_config_hash or lexical_current_alias_config_hash()


async def _upsert_delete_state_in_session(
    session: AsyncSession,
    *,
    collection_id: str | None,
    collection_name: str | None,
    file_id: str | None,
    target_config_hash: str,
    active_chunk_count: int,
    indexed_chunk_count: int,
    last_job_id: str,
) -> None:
    state_id = compute_index_state_id(
        index_kind="lexical",
        collection_id=collection_id,
        knowledge_id=collection_id,
        collection_name=collection_name,
        file_id=file_id,
        target_config_hash=target_config_hash,
    )
    result = await session.execute(select(RetrievalIndexState).filter_by(state_id=state_id))
    row = result.scalars().first()
    now = int(time.time())
    if row is None:
        row = RetrievalIndexState(
            state_id=state_id,
            index_kind="lexical",
            collection_id=collection_id,
            knowledge_id=collection_id,
            collection_name=collection_name,
            file_id=file_id,
            target_config_hash=target_config_hash,
            created_at=now,
        )
        session.add(row)

    row.status = "stale"
    row.active_chunk_count = active_chunk_count
    row.indexed_chunk_count = indexed_chunk_count
    row.last_job_id = last_job_id
    row.error = None
    row.updated_at = now


async def _deactivate_and_enqueue_delete_job(
    *,
    collection_id: str | None = None,
    collection_name: str | None = None,
    file_id: str | None = None,
    all_chunks: bool = False,
    db: Any | None = None,
    enqueue_lexical_delete: bool = True,
) -> ManifestDeactivationResult:
    now = int(time.time())
    async with _atomic_async_session(db) as session:
        try:
            conditions = [RetrievalChunk.is_active.is_(True)]
            if not all_chunks:
                conditions.extend(
                    _chunk_scope_conditions(
                        collection_id=collection_id,
                        collection_name=collection_name,
                        file_id=file_id,
                    )
                )

            chunk_result = await session.execute(
                select(RetrievalChunk.chunk_uid)
                .where(*conditions)
                .order_by(RetrievalChunk.row_id.asc())
            )
            chunk_uids = list(dict.fromkeys(chunk_result.scalars().all()))

            update_result = await session.execute(
                update(RetrievalChunk)
                .where(*conditions)
                .values(
                    is_active=False,
                    deleted_at=now,
                    updated_at=now,
                )
            )
            deactivated = int(update_result.rowcount or 0)

            delete_job_id = None
            lexical_delete_enqueued = 0
            if enqueue_lexical_delete and chunk_uids:
                target_config_hash = await _current_lexical_target_config_hash(session)
                delete_job_id = str(uuid.uuid4())
                job_payload = {"chunk_uids": chunk_uids}
                if all_chunks:
                    job_payload["scope"] = "all"
                session.add(
                    RetrievalIndexJob(
                        job_id=delete_job_id,
                        index_kind="delete",
                        collection_id=collection_id,
                        knowledge_id=collection_id,
                        collection_name=collection_name,
                        file_id=file_id,
                        target_config_hash=target_config_hash,
                        status="pending",
                        payload=job_payload,
                        result=None,
                        error=None,
                        retry_count=0,
                        max_retries=3,
                        created_at=now,
                        updated_at=now,
                    )
                )
                lexical_delete_enqueued = len(chunk_uids)
                await _upsert_delete_state_in_session(
                    session,
                    collection_id=collection_id,
                    collection_name=collection_name,
                    file_id=file_id,
                    target_config_hash=target_config_hash,
                    active_chunk_count=max(0, len(chunk_uids) - deactivated),
                    indexed_chunk_count=len(chunk_uids),
                    last_job_id=delete_job_id,
                )

            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return ManifestDeactivationResult(
        deactivated=deactivated,
        lexical_delete_enqueued=lexical_delete_enqueued,
        chunk_uids=chunk_uids,
        delete_job_id=delete_job_id,
    )


async def deactivate_chunks_for_scope(
    *,
    collection_id: str | None = None,
    collection_name: str | None = None,
    file_id: str | None = None,
    db: Any | None = None,
    enqueue_lexical_delete: bool = True,
    run_lexical_delete: bool = True,
) -> ManifestDeactivationResult:
    result = await _deactivate_and_enqueue_delete_job(
        collection_id=collection_id,
        collection_name=collection_name,
        file_id=file_id,
        db=db,
        enqueue_lexical_delete=enqueue_lexical_delete,
    )
    if run_lexical_delete and result.delete_job_id:
        delete_execution = await run_retrieval_index_job(result.delete_job_id)
        result.lexical_delete_executed = int(
            (delete_execution.get("result") or {}).get("lexical_deleted") or 0
        )
    return result


async def deactivate_all_chunks_for_reset(
    *,
    db: Any | None = None,
    enqueue_lexical_delete: bool = True,
    run_lexical_delete: bool = True,
) -> ManifestDeactivationResult:
    result = await _deactivate_and_enqueue_delete_job(
        all_chunks=True,
        db=db,
        enqueue_lexical_delete=enqueue_lexical_delete,
    )
    if run_lexical_delete and result.delete_job_id:
        delete_execution = await run_retrieval_index_job(result.delete_job_id)
        result.lexical_delete_executed = int(
            (delete_execution.get("result") or {}).get("lexical_deleted") or 0
        )
    return result


def build_default_indexing_service() -> RetrievalIndexingService:
    from open_webui.config import PGVECTOR_PGCRYPTO

    pgcrypto_enabled = bool(PGVECTOR_PGCRYPTO)
    return RetrievalIndexingService(
        vector_store=(
            _NoopVectorChunkStore()
            if pgcrypto_enabled
            else SqlAlchemyVectorChunkStore.from_existing_or_lightweight_session()
        ),
        manifest_store=SqlAlchemyManifestChunkStore(),
        lexical_client=OpenSearchLexicalClient(),
        pgcrypto_enabled=pgcrypto_enabled,
    )


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_default(value: Any, default: int) -> int:
    parsed = _int_or_none(value)
    return default if parsed is None else parsed


class _NoopVectorChunkStore:
    def iter_chunks(self, collection_ids: list[str] | None = None) -> list[VectorChunkRecord]:
        return []

    def patch_chunk_metadata(self, row_id: str, metadata: dict[str, Any]) -> None:
        raise RuntimeError("vector chunk metadata patch is unavailable")


def _existing_pgvector_session() -> Any | None:
    from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

    sync_client = ASYNC_VECTOR_DB_CLIENT.sync
    if sync_client.__class__.__name__ != "PgvectorClient":
        return None
    return getattr(sync_client, "session", None)


def _lightweight_pgvector_session() -> Any:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import scoped_session, sessionmaker

    from open_webui.config import PGVECTOR_DB_URL

    if not PGVECTOR_DB_URL:
        from open_webui.internal.db import ScopedSession

        return ScopedSession

    engine = create_engine(PGVECTOR_DB_URL, pool_pre_ping=True)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
    return scoped_session(session_factory)
