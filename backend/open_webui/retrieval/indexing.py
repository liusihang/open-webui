from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
import time
from typing import Any, Iterable, Protocol

from open_webui.models.retrieval_chunks import (
    RetrievalChunk,
    compute_chunk_uid,
    compute_chunker_config_hash,
    compute_content_hash,
)
from open_webui.retrieval.lexical.opensearch import OpenSearchLexicalClient


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
    metadata_patched: int = 0
    lexical_indexed: int = 0
    failed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    index_version: int = 1
    alias_promoted: bool = False
    chunk_uids: list[str] = field(default_factory=list)
    unsupported: bool = False

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

    def count_chunks(self) -> dict[str, int]:
        ...


class SqlAlchemyVectorChunkStore:
    def __init__(self, *, session: Any | None = None) -> None:
        from open_webui.retrieval.vector.dbs.pgvector import DocumentChunk, PgvectorClient

        self._document_chunk = DocumentChunk
        self._client = PgvectorClient() if session is None else None
        self.session = session if session is not None else self._client.session

    def iter_chunks(self, collection_ids: list[str] | None = None) -> list[VectorChunkRecord]:
        query = self.session.query(self._document_chunk)
        if collection_ids:
            query = query.filter(self._document_chunk.collection_name.in_(collection_ids))
        query = query.order_by(self._document_chunk.collection_name.asc(), self._document_chunk.id.asc())

        records = []
        for row in query.all():
            metadata = row.vmetadata if isinstance(row.vmetadata, dict) else {}
            records.append(
                VectorChunkRecord(
                    id=row.id,
                    collection_name=row.collection_name,
                    text=row.text,
                    metadata=dict(metadata),
                )
            )
        return records

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
    ) -> None:
        self.vector_store = vector_store
        self.manifest_store = manifest_store
        self.lexical_client = lexical_client
        self.pgcrypto_enabled = pgcrypto_enabled
        self.now_fn = now_fn or (lambda: int(time.time()))
        self.failure_limit = failure_limit

    def reindex_lexical(
        self,
        *,
        collection_ids: list[str] | None = None,
        index_version: int = 1,
        promote_alias: bool = True,
        batch_size: int = 500,
    ) -> ReindexResult:
        result = ReindexResult(index_version=index_version)
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
        if not chunks:
            return result

        try:
            result.manifest_upserted = self.manifest_store.upsert_chunks(chunks)
        except Exception as exc:
            result.failed += 1
            self._record_failure(result, {"error": str(exc), "stage": "manifest_upsert"})
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
        result.chunk_uids = [chunk["chunk_uid"] for chunk in chunks]
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


def build_default_indexing_service() -> RetrievalIndexingService:
    from open_webui.config import PGVECTOR_PGCRYPTO

    pgcrypto_enabled = bool(PGVECTOR_PGCRYPTO)
    return RetrievalIndexingService(
        vector_store=_NoopVectorChunkStore() if pgcrypto_enabled else SqlAlchemyVectorChunkStore(),
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
