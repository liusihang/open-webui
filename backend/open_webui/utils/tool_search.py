import asyncio
import hashlib
import json
import logging
import math
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Sequence

from open_webui.models.access_grants import AccessGrants
from open_webui.models.functions import FunctionModel, Functions
from open_webui.models.groups import Groups
from open_webui.models.tools import ToolModel, Tools
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.utils.mcp.client import MCPClient
from open_webui.utils.misc import is_string_allowed
from open_webui.utils.plugin import load_function_module_by_id


log = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]", re.IGNORECASE)
_SOURCE_TYPES = {"local_tool", "function_pipe", "mcp"}
_MAX_TOOL_SEARCH_TOP_K = 20
_DEFAULT_TOOL_SEARCH_COLLECTION = "tool-search-catalog-v1"
_DEFAULT_MCP_REBUILD_JITTER_MAX_SECONDS = 300


@dataclass
class CatalogDoc:
    doc_id: str
    source_type: Literal["local_tool", "function_pipe", "mcp"]
    spec_snapshot: dict
    search_text: str
    text_hash: str
    metadata: dict


class ToolSearchService:
    def __init__(self, app):
        self.app = app
        self.collection_name = _DEFAULT_TOOL_SEARCH_COLLECTION

        self._docs: dict[str, CatalogDoc] = {}
        self._bm25_index = BM25Index()
        self._cache_loaded = False

        self._rebuild_lock = asyncio.Lock()
        self._scheduler_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        self._status: dict[str, Any] = {
            "last_rebuild_started_at": None,
            "last_rebuild_completed_at": None,
            "last_mcp_rebuild_at": None,
            "last_error": None,
            "last_error_at": None,
            "oauth_skipped_servers": [],
            "errors": [],
        }

    async def start(self) -> None:
        await self._load_cache()

        # Keep local sources fresh on startup.
        await self.rebuild(scope="local")

        if resolve_bool(self.app.state.config.TOOL_SEARCH_MCP_REBUILD_ON_STARTUP, True):
            await self.rebuild(scope="mcp")

        if resolve_bool(self.app.state.config.TOOL_SEARCH_MCP_REBUILD_ENABLED, True):
            self._start_scheduler()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.debug(f"tool_search scheduler stop: {e}")
            self._scheduler_task = None

    async def get_status(self) -> dict:
        await self._load_cache()

        counts = {"local_tool": 0, "function_pipe": 0, "mcp": 0}
        for doc in self._docs.values():
            counts[doc.source_type] = counts.get(doc.source_type, 0) + 1

        return {
            "total_docs": len(self._docs),
            "source_counts": counts,
            "last_mcp_rebuild_at": self._status.get("last_mcp_rebuild_at"),
            "last_rebuild_started_at": self._status.get("last_rebuild_started_at"),
            "last_rebuild_completed_at": self._status.get("last_rebuild_completed_at"),
            "oauth_skipped_servers": self._status.get("oauth_skipped_servers", []),
            "recent_errors": self._status.get("errors", [])[-10:],
        }

    async def rebuild(self, scope: str = "all") -> dict:
        scope = (scope or "all").lower()
        if scope not in {"all", "local", "mcp"}:
            raise ValueError(f"Invalid rebuild scope: {scope}")

        await self._load_cache()

        started_at = now_ts()
        self._status["last_rebuild_started_at"] = started_at

        async with self._rebuild_lock:
            try:
                if scope in {"all", "local"}:
                    await self._rebuild_local_docs()
                if scope in {"all", "mcp"}:
                    await self._rebuild_mcp_docs()

                self._status["last_rebuild_completed_at"] = now_ts()
                return {
                    "ok": True,
                    "scope": scope,
                    "total_docs": len(self._docs),
                    "source_counts": self._source_counts(),
                }
            except Exception as e:
                self._record_error(f"Rebuild failed(scope={scope}): {e}")
                raise

    async def upsert_local_tool_documents(self, tool_id: str) -> None:
        await self._load_cache()

        tool = Tools.get_tool_by_id(tool_id)
        if not tool:
            await self.delete_local_tool_documents(tool_id)
            return

        docs = build_catalog_docs_from_tool(tool)
        await self._sync_resource_docs(
            resource_selector=lambda d: d.source_type == "local_tool"
            and d.metadata.get("resource_id") == tool_id,
            next_docs=docs,
        )

    async def delete_local_tool_documents(self, tool_id: str) -> None:
        await self._load_cache()
        ids_to_delete = [
            doc_id
            for doc_id, doc in self._docs.items()
            if doc.source_type == "local_tool" and doc.metadata.get("resource_id") == tool_id
        ]
        self._delete_docs(ids_to_delete)

    async def upsert_function_documents(self, function_id: str) -> None:
        await self._load_cache()

        function = Functions.get_function_by_id(function_id)
        if not function:
            await self.delete_function_documents(function_id)
            return

        docs = await self._build_catalog_docs_from_function(function)
        await self._sync_resource_docs(
            resource_selector=lambda d: d.source_type == "function_pipe"
            and d.metadata.get("resource_id") == function_id,
            next_docs=docs,
        )

    async def delete_function_documents(self, function_id: str) -> None:
        await self._load_cache()
        ids_to_delete = [
            doc_id
            for doc_id, doc in self._docs.items()
            if doc.source_type == "function_pipe" and doc.metadata.get("resource_id") == function_id
        ]
        self._delete_docs(ids_to_delete)

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        source_filter: Optional[list[str]] = None,
        vector_candidates: int = 40,
        bm25_candidates: int = 60,
        bm25_weight: float = 0.35,
    ) -> list[dict]:
        await self._load_cache()

        normalized_query = normalize_query(query)
        if not normalized_query:
            return []

        allowed_sources = set(source_filter or [])
        if allowed_sources:
            allowed_sources = {s for s in allowed_sources if s in _SOURCE_TYPES}
        if not allowed_sources:
            allowed_sources = set(_SOURCE_TYPES)

        vector_scores = await self._vector_search(
            normalized_query,
            limit=resolve_int(vector_candidates, 40, minimum=1, maximum=200),
            allowed_sources=allowed_sources,
        )
        bm25_scores = self._bm25_search(
            normalized_query,
            limit=resolve_int(bm25_candidates, 60, minimum=1, maximum=500),
            allowed_sources=allowed_sources,
        )

        ranked = hybrid_rank(
            vector_scores=vector_scores,
            bm25_scores=bm25_scores,
            bm25_weight=max(0.0, min(1.0, float(bm25_weight))),
        )

        max_top_k = resolve_int(top_k, 5, minimum=1, maximum=_MAX_TOOL_SEARCH_TOP_K)

        output: list[dict] = []
        for doc_id, score in ranked:
            doc = self._docs.get(doc_id)
            if not doc or doc.source_type not in allowed_sources:
                continue

            output.append(
                {
                    "doc_id": doc.doc_id,
                    "source_type": doc.source_type,
                    "score": score,
                    "spec_snapshot": doc.spec_snapshot,
                    "metadata": doc.metadata,
                }
            )

            if len(output) >= max_top_k:
                break

        return output

    async def _rebuild_local_docs(self) -> None:
        local_docs: dict[str, CatalogDoc] = {}

        for tool in Tools.get_tools(defer_content=True):
            for doc in build_catalog_docs_from_tool(tool):
                local_docs[doc.doc_id] = doc

        pipe_functions = Functions.get_functions_by_type("pipe", active_only=True)
        for function in pipe_functions:
            docs = await self._build_catalog_docs_from_function(function)
            for doc in docs:
                local_docs[doc.doc_id] = doc

        await self._sync_source_docs({"local_tool", "function_pipe"}, list(local_docs.values()))

    async def _rebuild_mcp_docs(self) -> None:
        connections = self.app.state.config.TOOL_SERVER_CONNECTIONS or []

        oauth_skipped: list[str] = []
        active_server_ids: set[str] = set()
        next_docs: dict[str, CatalogDoc] = {}

        for conn in connections:
            if conn.get("type", "openapi") != "mcp":
                continue
            if not conn.get("config", {}).get("enable", False):
                continue

            server_info = conn.get("info", {})
            server_id = server_info.get("id", "")
            if not server_id:
                continue

            active_server_ids.add(server_id)
            auth_type = conn.get("auth_type", "none")
            if auth_type == "oauth_2.1":
                oauth_skipped.append(server_id)
                self._record_error(
                    f"MCP server '{server_id}' skipped for offline index rebuild: auth_type=oauth_2.1",
                    include_trace=False,
                )
                continue

            try:
                headers = build_mcp_rebuild_headers(conn)
                client = MCPClient()
                await client.connect(url=conn.get("url", ""), headers=headers or None)

                function_name_filter_list = conn.get("config", {}).get(
                    "function_name_filter_list", ""
                )
                if isinstance(function_name_filter_list, str):
                    function_name_filter_list = function_name_filter_list.split(",")

                tool_specs = await client.list_tool_specs()
                for tool_spec in tool_specs or []:
                    tool_name = str(tool_spec.get("name") or "").strip()
                    if not tool_name:
                        continue

                    if function_name_filter_list and not is_string_allowed(
                        tool_name, function_name_filter_list
                    ):
                        continue

                    doc = build_catalog_doc_from_mcp_tool(
                        server_id=server_id,
                        server_name=server_info.get("name", server_id),
                        server_description=server_info.get("description", ""),
                        auth_type=auth_type,
                        tool_spec=tool_spec,
                    )
                    next_docs[doc.doc_id] = doc

                try:
                    await client.disconnect()
                except Exception:
                    pass
            except Exception as e:
                self._record_error(f"MCP rebuild failed for '{server_id}': {e}")

        await self._sync_source_docs({"mcp"}, list(next_docs.values()))

        stale_server_doc_ids = [
            doc_id
            for doc_id, doc in self._docs.items()
            if doc.source_type == "mcp"
            and doc.metadata.get("server_id") not in active_server_ids
        ]
        self._delete_docs(stale_server_doc_ids)

        self._status["last_mcp_rebuild_at"] = now_ts()
        self._status["oauth_skipped_servers"] = oauth_skipped

    async def _build_catalog_docs_from_function(self, function: FunctionModel) -> list[CatalogDoc]:
        if function.type != "pipe" or not function.is_active:
            return []

        meta = function.meta.model_dump() if function.meta else {}
        if meta.get("search_enabled", True) is False:
            return []

        docs: list[CatalogDoc] = []

        function_module = self.app.state.FUNCTIONS.get(function.id)
        if function_module is None:
            try:
                function_module, function_type, _ = load_function_module_by_id(
                    function.id, content=function.content
                )
                if function_type != "pipe":
                    return []
            except Exception as e:
                self._record_error(f"Failed loading function '{function.id}' for indexing: {e}")
                return []

        pipe_variants = await resolve_pipe_variants(function, function_module)
        for variant in pipe_variants:
            doc = build_catalog_doc_from_function_pipe(
                function=function,
                model_id=variant["model_id"],
                display_name=variant["display_name"],
                is_manifold=variant["is_manifold"],
                subpipe_id=variant.get("subpipe_id"),
            )
            docs.append(doc)

        return docs

    async def _load_cache(self) -> None:
        if self._cache_loaded:
            return

        try:
            docs = load_catalog_docs_from_vector_store(self.collection_name)
        except Exception as e:
            log.debug(f"tool_search cache bootstrap from vector db failed: {e}")
            docs = []
        self._docs = {doc.doc_id: doc for doc in docs}
        self._bm25_index.rebuild(self._docs)
        self._cache_loaded = True

    async def _sync_source_docs(
        self, source_types: set[str], next_docs: list[CatalogDoc]
    ) -> None:
        next_map = {doc.doc_id: doc for doc in next_docs if doc.source_type in source_types}

        existing_ids = {
            doc_id for doc_id, doc in self._docs.items() if doc.source_type in source_types
        }
        next_ids = set(next_map.keys())

        stale_ids = sorted(existing_ids - next_ids)
        self._delete_docs(stale_ids)

        await self._upsert_docs(list(next_map.values()))

    async def _sync_resource_docs(self, resource_selector, next_docs: list[CatalogDoc]) -> None:
        next_map = {doc.doc_id: doc for doc in next_docs}
        existing_ids = {
            doc_id for doc_id, doc in self._docs.items() if resource_selector(doc)
        }

        stale_ids = sorted(existing_ids - set(next_map.keys()))
        self._delete_docs(stale_ids)

        await self._upsert_docs(list(next_map.values()))

    async def _upsert_docs(self, docs: list[CatalogDoc]) -> None:
        if not docs:
            return

        now = now_ts()
        changed_docs: list[CatalogDoc] = []

        for doc in docs:
            prev_doc = self._docs.get(doc.doc_id)
            if prev_doc and prev_doc.text_hash == doc.text_hash:
                merged_meta = {**prev_doc.metadata, **doc.metadata}
                merged_meta["last_indexed_at"] = prev_doc.metadata.get("last_indexed_at")
                self._docs[doc.doc_id] = CatalogDoc(
                    doc_id=doc.doc_id,
                    source_type=doc.source_type,
                    spec_snapshot=doc.spec_snapshot,
                    search_text=doc.search_text,
                    text_hash=doc.text_hash,
                    metadata=merged_meta,
                )
                continue

            doc.metadata["last_indexed_at"] = now
            changed_docs.append(doc)

        if not changed_docs:
            self._bm25_index.rebuild(self._docs)
            return

        embeddings = await self._embed_texts([doc.search_text for doc in changed_docs])
        items = []

        for idx, doc in enumerate(changed_docs):
            metadata = {
                **doc.metadata,
                "doc_id": doc.doc_id,
                "source_type": doc.source_type,
                "spec_snapshot": doc.spec_snapshot,
                "text_hash": doc.text_hash,
            }
            items.append(
                {
                    "id": doc.doc_id,
                    "text": doc.search_text,
                    "vector": embeddings[idx],
                    "metadata": metadata,
                }
            )

        try:
            VECTOR_DB_CLIENT.upsert(collection_name=self.collection_name, items=items)
        except Exception as e:
            self._record_error(f"tool_search upsert failed: {e}", include_trace=False)
            return

        for doc in changed_docs:
            self._docs[doc.doc_id] = doc

        self._bm25_index.rebuild(self._docs)

    def _delete_docs(self, doc_ids: Sequence[str]) -> None:
        if not doc_ids:
            return

        try:
            VECTOR_DB_CLIENT.delete(collection_name=self.collection_name, ids=list(doc_ids))
        except Exception as e:
            log.debug(f"tool_search delete failed for ids={doc_ids}: {e}")
        for doc_id in doc_ids:
            self._docs.pop(doc_id, None)

        self._bm25_index.rebuild(self._docs)

    async def _vector_search(
        self, query: str, *, limit: int, allowed_sources: set[str]
    ) -> dict[str, float]:
        if not self._docs:
            return {}

        query_vec = await self.app.state.EMBEDDING_FUNCTION(query)
        try:
            result = VECTOR_DB_CLIENT.search(
                collection_name=self.collection_name,
                vectors=[query_vec],
                limit=limit,
            )
        except Exception as e:
            log.debug(f"tool_search vector search failed: {e}")
            return {}

        if not result or not result.ids or len(result.ids) == 0:
            return {}

        ids = result.ids[0] if result.ids else []
        distances = result.distances[0] if result.distances else []

        output = {}
        for idx, doc_id in enumerate(ids):
            if not doc_id:
                continue
            doc = self._docs.get(doc_id)
            if not doc or doc.source_type not in allowed_sources:
                continue
            score = float(distances[idx]) if idx < len(distances) else 0.0
            output[doc_id] = score

        return output

    def _bm25_search(
        self, query: str, *, limit: int, allowed_sources: set[str]
    ) -> dict[str, float]:
        if not self._docs:
            return {}

        scores = self._bm25_index.score(query)
        filtered = []
        for doc_id, score in scores.items():
            doc = self._docs.get(doc_id)
            if not doc or doc.source_type not in allowed_sources:
                continue
            filtered.append((doc_id, score))

        filtered.sort(key=lambda item: item[1], reverse=True)
        return {doc_id: score for doc_id, score in filtered[:limit]}

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            embedding = await self.app.state.EMBEDDING_FUNCTION(text)
            vectors.append(embedding)
        return vectors

    def _source_counts(self) -> dict[str, int]:
        counts = {"local_tool": 0, "function_pipe": 0, "mcp": 0}
        for doc in self._docs.values():
            counts[doc.source_type] += 1
        return counts

    def _record_error(self, message: str, include_trace: bool = True) -> None:
        if include_trace:
            log.exception(message)
        else:
            log.warning(message)

        self._status["last_error"] = message
        self._status["last_error_at"] = now_ts()
        self._status.setdefault("errors", []).append(
            {
                "message": message,
                "at": now_ts(),
            }
        )

    def _start_scheduler(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            return

        self._stop_event.clear()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            hours = resolve_int(
                self.app.state.config.TOOL_SEARCH_MCP_REBUILD_INTERVAL_HOURS,
                default=24,
                minimum=1,
                maximum=24 * 30,
            )
            interval_seconds = int(hours * 3600)
            jitter_seconds = random.randint(0, _DEFAULT_MCP_REBUILD_JITTER_MAX_SECONDS)
            wait_seconds = interval_seconds + jitter_seconds

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                break
            except asyncio.TimeoutError:
                pass

            try:
                await self.rebuild(scope="mcp")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._record_error(f"Scheduled MCP rebuild failed: {e}")


class BM25Index:
    def __init__(self):
        self.docs_tokens: dict[str, list[str]] = {}
        self.doc_freq: dict[str, int] = {}
        self.term_freqs: dict[str, dict[str, int]] = {}
        self.doc_len: dict[str, int] = {}
        self.avg_doc_len = 0.0
        self.k1 = 1.2
        self.b = 0.75

    def rebuild(self, docs: dict[str, CatalogDoc]) -> None:
        self.docs_tokens = {}
        self.doc_freq = {}
        self.term_freqs = {}
        self.doc_len = {}

        for doc_id, doc in docs.items():
            tokens = tokenize(doc.search_text)
            self.docs_tokens[doc_id] = tokens
            self.doc_len[doc_id] = len(tokens)

            tf: dict[str, int] = {}
            seen_terms = set()
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
                if token not in seen_terms:
                    self.doc_freq[token] = self.doc_freq.get(token, 0) + 1
                    seen_terms.add(token)
            self.term_freqs[doc_id] = tf

        total_len = sum(self.doc_len.values())
        self.avg_doc_len = (total_len / len(self.doc_len)) if self.doc_len else 0.0

    def score(self, query: str) -> dict[str, float]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return {}

        N = len(self.docs_tokens)
        if N == 0:
            return {}

        query_terms = set(query_tokens)
        scores: dict[str, float] = {}

        for doc_id, tf in self.term_freqs.items():
            doc_len = self.doc_len.get(doc_id, 0)
            score = 0.0

            for term in query_terms:
                freq = tf.get(term, 0)
                if freq <= 0:
                    continue

                df = self.doc_freq.get(term, 0)
                idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)

                denom = freq + self.k1 * (
                    1.0 - self.b + self.b * (doc_len / (self.avg_doc_len or 1.0))
                )
                score += idf * ((freq * (self.k1 + 1.0)) / (denom or 1.0))

            if score > 0:
                scores[doc_id] = score

        return scores


def resolve_int(
    value: Any,
    default: int,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    try:
        parsed = int(str(_config_value(value)).strip())
    except (TypeError, ValueError):
        parsed = default

    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def resolve_bool(value: Any, default: bool = False) -> bool:
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


def resolve_float(
    value: Any,
    default: float,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    try:
        parsed = float(str(_config_value(value)).strip())
    except (TypeError, ValueError):
        parsed = default

    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _config_value(value: Any) -> Any:
    return getattr(value, "value", value)


def now_ts() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def tokenize(value: str) -> list[str]:
    if not value:
        return []
    return [token.lower() for token in _TOKEN_PATTERN.findall(value)]


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip())


def normalize_json(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_text_hash(search_text: str, spec_snapshot: dict) -> str:
    payload = f"{search_text}\n{normalize_json(spec_snapshot)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sanitize_tool_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", (value or "").strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        normalized = "tool"
    if normalized[0].isdigit():
        normalized = f"tool_{normalized}"
    return normalized[:63]


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        output = []
        for item in value:
            text = str(item or "").strip()
            if text:
                output.append(text)
        return output
    return []


def _safe_meta(meta: Any) -> dict:
    if hasattr(meta, "model_dump"):
        meta = meta.model_dump()
    return meta if isinstance(meta, dict) else {}


def _build_search_text(
    *,
    display_name: str,
    search_description: str,
    keywords: list[str],
    examples: list[str],
    spec_snapshot: dict,
    source_tags: list[str],
) -> str:
    segments = [display_name, search_description, " ".join(keywords), "\n".join(examples)]

    segments.append(str(spec_snapshot.get("description") or ""))
    parameters = spec_snapshot.get("parameters", {})
    properties = parameters.get("properties", {}) if isinstance(parameters, dict) else {}

    if isinstance(properties, dict):
        for key, value in properties.items():
            key_text = str(key or "")
            description = ""
            if isinstance(value, dict):
                description = str(value.get("description") or "")
            segments.append(f"{key_text} {description}".strip())

    segments.append(" ".join(source_tags))

    return "\n".join(part for part in segments if part).strip()


def build_catalog_docs_from_tool(tool: ToolModel) -> list[CatalogDoc]:
    meta = _safe_meta(tool.meta)
    if meta.get("search_enabled", True) is False:
        return []

    search_description = str(meta.get("search_description") or meta.get("description") or "")
    keywords = _normalize_list(meta.get("search_keywords"))
    examples = _normalize_list(meta.get("search_examples"))

    docs: list[CatalogDoc] = []

    for spec in tool.specs or []:
        function_name = str(spec.get("name") or "").strip()
        if not function_name:
            continue

        doc_id = f"tool:{tool.id}:fn:{function_name}"
        spec_snapshot = {
            "name": function_name,
            "description": str(spec.get("description") or ""),
            "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
        }

        display_name = f"{tool.name} {function_name}".strip()
        search_text = _build_search_text(
            display_name=display_name,
            search_description=search_description,
            keywords=keywords,
            examples=examples,
            spec_snapshot=spec_snapshot,
            source_tags=["local_tool", tool.id],
        )

        metadata = {
            "resource_id": tool.id,
            "function_name": function_name,
            "enabled": True,
            "updated_at": tool.updated_at,
        }

        docs.append(
            CatalogDoc(
                doc_id=doc_id,
                source_type="local_tool",
                spec_snapshot=spec_snapshot,
                search_text=search_text,
                text_hash=compute_text_hash(search_text, spec_snapshot),
                metadata=metadata,
            )
        )

    return docs


def build_function_pipe_spec(model_id: str, display_name: str, description: str) -> dict:
    tool_name = f"fnpipe_{sanitize_tool_name(model_id)}"
    return {
        "name": tool_name,
        "description": description
        or f"Run function pipe '{display_name or model_id}' with a focused prompt.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The task prompt sent to this function pipe.",
                },
                "context": {
                    "type": "object",
                    "description": "Optional structured context to prepend to the prompt.",
                },
            },
            "required": ["prompt"],
        },
    }


def build_catalog_doc_from_function_pipe(
    *,
    function: FunctionModel,
    model_id: str,
    display_name: str,
    is_manifold: bool,
    subpipe_id: Optional[str],
) -> CatalogDoc:
    meta = _safe_meta(function.meta)

    search_description = str(meta.get("search_description") or meta.get("description") or "")
    keywords = _normalize_list(meta.get("search_keywords"))
    examples = _normalize_list(meta.get("search_examples"))

    spec_snapshot = build_function_pipe_spec(model_id, display_name, search_description)

    search_text = _build_search_text(
        display_name=f"{display_name} {model_id}",
        search_description=search_description,
        keywords=keywords,
        examples=examples,
        spec_snapshot=spec_snapshot,
        source_tags=["function_pipe", function.id, "manifold" if is_manifold else "single"],
    )

    metadata = {
        "resource_id": function.id,
        "function_id": function.id,
        "function_model_id": model_id,
        "display_name": display_name,
        "is_manifold": is_manifold,
        "subpipe_id": subpipe_id,
        "enabled": bool(function.is_active),
        "updated_at": function.updated_at,
    }

    return CatalogDoc(
        doc_id=f"function:{model_id}",
        source_type="function_pipe",
        spec_snapshot=spec_snapshot,
        search_text=search_text,
        text_hash=compute_text_hash(search_text, spec_snapshot),
        metadata=metadata,
    )


def build_catalog_doc_from_mcp_tool(
    *,
    server_id: str,
    server_name: str,
    server_description: str,
    auth_type: str,
    tool_spec: dict,
) -> CatalogDoc:
    tool_name = str(tool_spec.get("name") or "").strip()
    runtime_name = f"{server_id}_{tool_name}"

    spec_snapshot = {
        "name": runtime_name,
        "description": str(tool_spec.get("description") or ""),
        "parameters": tool_spec.get("parameters") or {"type": "object", "properties": {}},
    }

    search_text = _build_search_text(
        display_name=f"{server_name} {tool_name}".strip(),
        search_description=str(server_description or ""),
        keywords=[server_name, server_id, tool_name],
        examples=[],
        spec_snapshot=spec_snapshot,
        source_tags=["mcp", server_id, auth_type],
    )

    metadata = {
        "resource_id": f"{server_id}:{tool_name}",
        "server_id": server_id,
        "tool_name": tool_name,
        "auth_type": auth_type,
        "enabled": True,
        "updated_at": now_ts(),
    }

    return CatalogDoc(
        doc_id=f"mcp:{server_id}:fn:{tool_name}",
        source_type="mcp",
        spec_snapshot=spec_snapshot,
        search_text=search_text,
        text_hash=compute_text_hash(search_text, spec_snapshot),
        metadata=metadata,
    )


def load_catalog_docs_from_vector_store(collection_name: str) -> list[CatalogDoc]:
    result = VECTOR_DB_CLIENT.get(collection_name)
    if not result or not result.ids:
        return []

    ids = result.ids[0] if result.ids else []
    documents = result.documents[0] if result.documents else []
    metadatas = result.metadatas[0] if result.metadatas else []

    docs: list[CatalogDoc] = []
    for idx, doc_id in enumerate(ids):
        metadata = metadatas[idx] if idx < len(metadatas) and isinstance(metadatas[idx], dict) else {}
        source_type = metadata.get("source_type")

        if source_type not in _SOURCE_TYPES:
            continue

        spec_snapshot = metadata.get("spec_snapshot") or {}
        search_text = documents[idx] if idx < len(documents) else ""

        docs.append(
            CatalogDoc(
                doc_id=doc_id,
                source_type=source_type,
                spec_snapshot=spec_snapshot,
                search_text=search_text,
                text_hash=str(metadata.get("text_hash") or compute_text_hash(search_text, spec_snapshot)),
                metadata=metadata,
            )
        )

    return docs


def hybrid_rank(
    *,
    vector_scores: dict[str, float],
    bm25_scores: dict[str, float],
    bm25_weight: float,
) -> list[tuple[str, float]]:
    vector_norm = normalize_scores(vector_scores)
    bm25_norm = normalize_scores(bm25_scores)

    all_ids = set(vector_norm.keys()) | set(bm25_norm.keys())
    output: list[tuple[str, float]] = []

    for doc_id in all_ids:
        score = bm25_weight * bm25_norm.get(doc_id, 0.0) + (1.0 - bm25_weight) * vector_norm.get(doc_id, 0.0)
        output.append((doc_id, score))

    output.sort(key=lambda item: item[1], reverse=True)
    return output


def normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}

    values = list(scores.values())
    min_v = min(values)
    max_v = max(values)

    if max_v <= min_v:
        return {k: (1.0 if v > 0 else 0.0) for k, v in scores.items()}

    denominator = max_v - min_v
    return {k: (v - min_v) / denominator for k, v in scores.items()}


def build_mcp_rebuild_headers(connection: dict) -> dict:
    headers = {}
    auth_type = connection.get("auth_type", "none")

    if auth_type in {"bearer", "session", "system_oauth"}:
        key = str(connection.get("key") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"

    connection_headers = connection.get("headers")
    if isinstance(connection_headers, dict):
        headers.update(connection_headers)

    return headers


async def resolve_pipe_variants(function: FunctionModel, function_module) -> list[dict]:
    variants: list[dict] = []

    if hasattr(function_module, "pipes"):
        sub_pipes = []
        try:
            if callable(function_module.pipes):
                if asyncio.iscoroutinefunction(function_module.pipes):
                    sub_pipes = await function_module.pipes()
                else:
                    sub_pipes = function_module.pipes()
            else:
                sub_pipes = function_module.pipes
        except Exception as e:
            log.debug(f"resolve_pipe_variants error ({function.id}): {e}")
            sub_pipes = []

        for p in sub_pipes or []:
            sub_id = str((p or {}).get("id") or "").strip()
            if not sub_id:
                continue

            sub_name = str((p or {}).get("name") or sub_id)
            prefix = getattr(function_module, "name", None) or function.name or ""
            display_name = f"{prefix}{sub_name}".strip() if prefix else sub_name

            variants.append(
                {
                    "model_id": f"{function.id}.{sub_id}",
                    "display_name": display_name,
                    "is_manifold": True,
                    "subpipe_id": sub_id,
                }
            )

    if not variants:
        variants.append(
            {
                "model_id": function.id,
                "display_name": function.name,
                "is_manifold": False,
                "subpipe_id": None,
            }
        )

    return variants


def build_tool_search_spec(default_top_k: int = 5) -> dict:
    safe_default_top_k = resolve_int(default_top_k, default=5, minimum=1, maximum=_MAX_TOOL_SEARCH_TOP_K)

    return {
        "name": "tool_search",
        "description": (
            "Search tools by capability and load the most relevant ones into the current native tool pool. "
            "Use this when currently loaded tools are insufficient."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Capability or task intent to search for.",
                },
                "top_k": {
                    "type": "integer",
                    "description": f"How many tools to load (1-{_MAX_TOOL_SEARCH_TOP_K}).",
                    "minimum": 1,
                    "maximum": _MAX_TOOL_SEARCH_TOP_K,
                    "default": safe_default_top_k,
                },
                "source_filter": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["local_tool", "function_pipe", "mcp"],
                    },
                    "description": "Optional source type filter.",
                },
            },
            "required": ["query"],
        },
    }


def select_initial_visible_tools(
    tools: dict[str, dict],
    *,
    always_visible: Sequence[str],
    initial_visible_count: int,
) -> tuple[dict[str, dict], dict[str, dict]]:
    max_visible = resolve_int(initial_visible_count, default=8, minimum=1)
    if len(tools) <= max_visible:
        return dict(tools), {}

    always_visible_set = {name for name in always_visible if name in tools}

    visible_names: list[str] = []
    for name in always_visible:
        if name in tools and name not in visible_names:
            visible_names.append(name)

    for name in sorted(tools.keys()):
        if name in always_visible_set:
            continue
        visible_names.append(name)
        if len(visible_names) >= max_visible:
            break

    visible_set = set(visible_names)
    visible_tools = {name: tools[name] for name in visible_names if name in tools}
    hidden_tools = {name: tool for name, tool in tools.items() if name not in visible_set}
    return visible_tools, hidden_tools


def has_local_tool_access(tool: ToolModel, user, user_group_ids: Optional[set[str]] = None) -> bool:
    if user.role == "admin":
        return True

    if tool.user_id == user.id:
        return True

    return AccessGrants.has_access(
        user_id=user.id,
        resource_type="tool",
        resource_id=tool.id,
        permission="read",
        user_group_ids=user_group_ids,
    )


def has_mcp_access(server_connection: dict, user, user_group_ids: Optional[set[str]] = None) -> bool:
    from open_webui.utils.tools import has_tool_server_access

    return has_tool_server_access(user, server_connection, user_group_ids)


def get_user_group_ids(user_id: str) -> set[str]:
    groups = Groups.get_groups_by_member_id(user_id)
    return {group.id for group in groups}
