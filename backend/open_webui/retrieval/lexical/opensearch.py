from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk


@dataclass(frozen=True)
class LexicalSearchHit:
    chunk_uid: str
    score: float
    metadata: dict[str, Any] | None = None


class OpenSearchLexicalClient:
    hit_type = LexicalSearchHit

    def __init__(
        self,
        *,
        client: Any | None = None,
        index_prefix: str = "retrieval_lexical",
        alias: str = "retrieval_lexical_current",
        bulk_helper: Any | None = None,
    ) -> None:
        self.client = client or self._build_default_client()
        self.index_prefix = index_prefix
        self.alias = alias
        self._bulk = bulk_helper or bulk

    def index_name_for_version(self, version: int) -> str:
        return f"{self.index_prefix}_v{version}"

    def build_index_body(self, use_icu: bool = True) -> dict[str, Any]:
        analysis = {
            "filter": {
                "lexical_edge_ngram_filter": {
                    "type": "edge_ngram",
                    "min_gram": 2,
                    "max_gram": 20,
                }
            },
            "analyzer": {
                "lexical_en": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                },
                "lexical_cjk": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["cjk_width", "lowercase", "cjk_bigram"],
                },
                "lexical_ngram": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": [
                        "lowercase",
                        "asciifolding",
                        "lexical_edge_ngram_filter",
                    ],
                },
            },
        }

        icu_analyzer = "lexical_cjk"
        if use_icu:
            analysis["analyzer"]["lexical_icu"] = {
                "type": "custom",
                "tokenizer": "icu_tokenizer",
                "filter": ["lowercase", "icu_folding"],
            }
            icu_analyzer = "lexical_icu"

        def text_field(*, include_ngram: bool = False) -> dict[str, Any]:
            fields = {
                "icu": {"type": "text", "analyzer": icu_analyzer},
                "cjk": {"type": "text", "analyzer": "lexical_cjk"},
                "en": {"type": "text", "analyzer": "lexical_en"},
            }
            if include_ngram:
                fields["ngram"] = {
                    "type": "text",
                    "analyzer": "lexical_ngram",
                    "search_analyzer": "lexical_en",
                }

            return {
                "type": "text",
                "analyzer": icu_analyzer,
                "fields": fields,
            }

        return {
            "settings": {
                "index": {"max_ngram_diff": 20},
                "analysis": analysis,
            },
            "mappings": {
                "_source": {"excludes": ["text"]},
                "properties": {
                    "chunk_uid": {"type": "keyword"},
                    "collection_id": {"type": "keyword"},
                    "knowledge_id": {"type": "keyword"},
                    "collection_name": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "file_id": {"type": "keyword"},
                    "file_version": {"type": "integer"},
                    "chunk_version": {"type": "integer"},
                    "content_hash": {"type": "keyword"},
                    "chunker_config_hash": {"type": "keyword"},
                    "is_active": {"type": "boolean"},
                    "text": text_field(),
                    "title": text_field(),
                    "name": text_field(include_ngram=True),
                    "source": text_field(include_ngram=True),
                    "metadata_headings": text_field(),
                    "metadata": {"type": "object", "enabled": False},
                }
            },
        }

    def ensure_index(self, version: int = 1, use_icu: bool = True) -> str:
        index_name = self.index_name_for_version(version)

        if not self.client.indices.exists(index=index_name):
            body = self.build_index_body(use_icu=use_icu)
            try:
                self.client.indices.create(index=index_name, body=body)
            except Exception as exc:
                if not use_icu or not self._is_icu_unavailable_error(exc):
                    raise

                fallback_body = self.build_index_body(use_icu=False)
                self.client.indices.create(index=index_name, body=fallback_body)

        self._ensure_alias(index_name)
        return index_name

    def bulk_upsert(self, chunks: Iterable[Any], *, batch_size: int = 500) -> int:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")

        count = 0
        batch: list[dict[str, Any]] = []
        for chunk in chunks:
            source = self._source_from_chunk(chunk)
            batch.append(
                {
                    "_op_type": "index",
                    "_index": self.alias,
                    "_id": source["chunk_uid"],
                    "_source": source,
                }
            )
            if len(batch) >= batch_size:
                self._bulk(self.client, batch)
                count += len(batch)
                batch = []

        if batch:
            self._bulk(self.client, batch)
            count += len(batch)

        if count == 0:
            return 0

        self.client.indices.refresh(index=self.alias)
        return count

    def search(
        self,
        query: str,
        *,
        collection_ids: Iterable[str] | str | None = None,
        knowledge_ids: Iterable[str] | str | None = None,
        file_ids: Iterable[str] | str | None = None,
        k: int = 10,
    ) -> list[LexicalSearchHit]:
        query = query.strip()
        if not query:
            return []

        filters: list[dict[str, Any]] = [{"term": {"is_active": True}}]
        for field, values in (
            ("collection_id", self._as_list(collection_ids)),
            ("knowledge_id", self._as_list(knowledge_ids)),
            ("file_id", self._as_list(file_ids)),
        ):
            if values:
                filters.append({"terms": {field: values}})

        body = {
            "size": k,
            "_source": ["chunk_uid", "metadata"],
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "type": "best_fields",
                                "fields": self._search_fields(),
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
        }
        result = self.client.search(index=self.alias, body=body)

        hits = []
        for hit in result.get("hits", {}).get("hits", []):
            source = hit.get("_source") or {}
            chunk_uid = source.get("chunk_uid") or hit.get("_id")
            if not chunk_uid:
                continue

            metadata = source.get("metadata")
            hits.append(
                LexicalSearchHit(
                    chunk_uid=chunk_uid,
                    score=float(hit.get("_score") or 0.0),
                    metadata=metadata if isinstance(metadata, dict) else None,
                )
            )

        return hits

    def delete_chunks(self, chunk_uids: list[str]) -> int:
        actions = [
            {
                "_op_type": "delete",
                "_index": self.alias,
                "_id": chunk_uid,
            }
            for chunk_uid in chunk_uids
        ]
        if not actions:
            return 0

        self._bulk(self.client, actions)
        self.client.indices.refresh(index=self.alias)
        return len(actions)

    def _ensure_alias(self, index_name: str) -> None:
        actions = [
            {"remove": {"index": existing_index, "alias": self.alias}}
            for existing_index in self._existing_owned_alias_indices()
        ]
        actions.append({"add": {"index": index_name, "alias": self.alias}})

        self.client.indices.update_aliases(
            body={"actions": actions}
        )

    def _existing_owned_alias_indices(self) -> list[str]:
        try:
            aliases = self.client.indices.get_alias(name=self.alias)
        except Exception as exc:
            if self._is_alias_not_found_error(exc):
                return []
            raise

        return [
            index_name
            for index_name in aliases.keys()
            if self._is_owned_index_name(index_name)
        ]

    def _is_owned_index_name(self, index_name: str) -> bool:
        return re.fullmatch(rf"{re.escape(self.index_prefix)}_v\d+", index_name) is not None

    @staticmethod
    def _build_default_client() -> OpenSearch:
        from open_webui.config import (
            OPENSEARCH_CERT_VERIFY,
            OPENSEARCH_PASSWORD,
            OPENSEARCH_SSL,
            OPENSEARCH_URI,
            OPENSEARCH_USERNAME,
        )

        return OpenSearch(
            hosts=[OPENSEARCH_URI],
            use_ssl=OPENSEARCH_SSL,
            verify_certs=OPENSEARCH_CERT_VERIFY,
            http_auth=(OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD),
        )

    def _source_from_chunk(self, chunk: Any) -> dict[str, Any]:
        metadata = self._value(chunk, "metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        source = {
            "chunk_uid": self._value(chunk, "chunk_uid"),
            "collection_id": self._value(chunk, "collection_id"),
            "knowledge_id": self._value(chunk, "knowledge_id"),
            "collection_name": self._value(chunk, "collection_name"),
            "file_id": self._value(chunk, "file_id"),
            "file_version": self._value(chunk, "file_version"),
            "chunk_version": self._value(chunk, "chunk_version"),
            "content_hash": self._value(chunk, "content_hash"),
            "chunker_config_hash": self._value(chunk, "chunker_config_hash"),
            "is_active": self._value(chunk, "is_active", True),
            "text": self._value(chunk, "text"),
            "title": self._value(chunk, "title", metadata.get("title")),
            "name": self._value(chunk, "name", metadata.get("name")),
            "source": self._value(chunk, "source", metadata.get("source")),
            "metadata_headings": self._metadata_headings(chunk, metadata),
            "metadata": metadata,
        }

        missing = [field for field in ("chunk_uid",) if not source[field]]
        if missing:
            raise ValueError(f"missing required lexical chunk field: {', '.join(missing)}")

        return source

    def _metadata_headings(self, chunk: Any, metadata: dict[str, Any]) -> Any:
        direct = self._value(chunk, "metadata_headings")
        if direct is not None:
            return direct
        return metadata.get("metadata_headings", metadata.get("headings"))

    @staticmethod
    def _value(chunk: Any, key: str, default: Any = None) -> Any:
        if isinstance(chunk, Mapping):
            if key == "metadata":
                return chunk.get("metadata", chunk.get("metadata_", default))
            return chunk.get(key, default)

        if key == "metadata":
            metadata = getattr(chunk, "metadata_", default)
            if metadata is not default:
                return metadata
            return getattr(chunk, "metadata", default)
        return getattr(chunk, key, default)

    @staticmethod
    def _as_list(value: Iterable[str] | str | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    @staticmethod
    def _is_icu_unavailable_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "icu" in message and any(
            token in message
            for token in (
                "unknown",
                "not found",
                "failed",
                "missing",
                "plugin",
                "analyzer",
                "tokenizer",
            )
        )

    @staticmethod
    def _is_alias_not_found_error(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code == 404:
            return True

        message = str(exc).lower()
        return "alias" in message and any(
            token in message for token in ("missing", "not found", "not_found")
        )

    @staticmethod
    def _search_fields() -> list[str]:
        return [
            "title.icu^5",
            "title.cjk^4",
            "title.en^4",
            "text.icu^4",
            "text.cjk^4",
            "text.en^3",
            "metadata_headings.icu^3",
            "metadata_headings.cjk^2",
            "metadata_headings.en^2",
            "name.ngram^1.5",
            "source.ngram^1.2",
        ]
