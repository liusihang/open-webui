import hashlib
import json
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any


log = logging.getLogger(__name__)

TOOL_ROUTING_COLLECTION = "tool-manifests-v1"
SUPPORTED_TOOL_ROUTING_MODES = {"hybrid", "chat", "search", "analyze"}


@dataclass
class ToolRoutingConfig:
    enable: bool
    mode: str
    semantic_top_n: int
    final_top_k: int
    min_confidence: float
    lexical_weight: float
    rule_weight: float
    max_injected_tools: int
    max_schema_chars: int
    bm25_weight: float
    intent_query_enable: bool
    intent_max_clauses: int
    intent_max_chars: int
    chat_floor_enable: bool
    chat_floor_min_keep: int
    keyword_fallback_enable: bool
    mode_patterns: dict[str, list[str]]


@dataclass
class ToolRoutingDecision:
    selected_keys: list[str]
    dropped_keys: list[str]
    scores: dict[str, float]
    score_breakdown: dict[str, dict[str, float]]
    mode: str
    fallback_reason: str | None
    routing_query: str
    clause_debug: dict[str, Any]


@dataclass
class ToolManifest:
    key: str
    manifest_id: str
    source_type: str
    source_id: str
    function_name: str
    text: str
    fingerprint: str
    metadata: dict[str, Any]


def get_manifest_id(scope_id: str, key: str) -> str:
    safe_key = re.sub(r"[^a-zA-Z0-9_\-:.]", "_", key)
    return f"tool::{scope_id}::{safe_key}"


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, parsed))


def _clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return max(minimum, min(maximum, parsed))


def _normalize_routing_mode(value: Any) -> str:
    mode = _normalize_text(value)
    if mode in SUPPORTED_TOOL_ROUTING_MODES:
        return mode
    return "hybrid"


def build_tool_routing_config(app_config: Any) -> ToolRoutingConfig:
    mode_patterns = app_config.TOOL_ROUTING_KEYWORD_FALLBACK_MODE_PATTERNS
    if not isinstance(mode_patterns, dict):
        mode_patterns = default_mode_patterns()

    return ToolRoutingConfig(
        enable=bool(app_config.TOOL_ROUTING_ENABLE),
        mode=_normalize_routing_mode(app_config.TOOL_ROUTING_MODE or "hybrid"),
        semantic_top_n=_clamp_int(app_config.TOOL_ROUTING_SEMANTIC_TOP_N, 12, 1, 200),
        final_top_k=_clamp_int(app_config.TOOL_ROUTING_FINAL_TOP_K, 6, 1, 100),
        min_confidence=_clamp_float(
            app_config.TOOL_ROUTING_MIN_CONFIDENCE, 0.12, 0.0, 1.0
        ),
        lexical_weight=_clamp_float(
            app_config.TOOL_ROUTING_LEXICAL_WEIGHT, 0.7, 0.0, 1.0
        ),
        rule_weight=_clamp_float(app_config.TOOL_ROUTING_RULE_WEIGHT, 0.3, 0.0, 1.0),
        max_injected_tools=_clamp_int(
            app_config.TOOL_ROUTING_MAX_INJECTED_TOOLS, 6, 1, 100
        ),
        max_schema_chars=_clamp_int(
            app_config.TOOL_ROUTING_MAX_SCHEMA_CHARS, 200000, 2000, 2000000
        ),
        bm25_weight=_clamp_float(
            getattr(app_config, "TOOL_ROUTING_BM25_WEIGHT", 0.45),
            0.45,
            0.0,
            1.0,
        ),
        intent_query_enable=bool(
            getattr(app_config, "TOOL_ROUTING_INTENT_QUERY_ENABLE", True)
        ),
        intent_max_clauses=_clamp_int(
            getattr(app_config, "TOOL_ROUTING_INTENT_MAX_CLAUSES", 2),
            2,
            1,
            6,
        ),
        intent_max_chars=_clamp_int(
            getattr(app_config, "TOOL_ROUTING_INTENT_MAX_CHARS", 256),
            256,
            64,
            2048,
        ),
        chat_floor_enable=bool(
            getattr(app_config, "TOOL_ROUTING_CHAT_FLOOR_ENABLE", True)
        ),
        chat_floor_min_keep=_clamp_int(
            getattr(app_config, "TOOL_ROUTING_CHAT_FLOOR_MIN_KEEP", 1),
            1,
            1,
            5,
        ),
        keyword_fallback_enable=bool(app_config.TOOL_ROUTING_KEYWORD_FALLBACK_ENABLE),
        mode_patterns=mode_patterns,
    )


def default_mode_patterns() -> dict[str, list[str]]:
    return {
        "search": ["search", "find", "lookup", "query", "retrieve", "search_web"],
        "analyze": ["analyze", "analysis", "inspect", "debug", "diagnose"],
        "chat": [],
    }


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _sanitize_metadata_text(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    text = re.sub(r"<[\w\s\-\/:=\"']+>", " ", text)
    text = re.sub(r"[`{}\[\]$]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokenize(value: str) -> set[str]:
    return set(_tokenize_terms(value))


def _tokenize_terms(value: str) -> list[str]:
    if not value:
        return []

    text = value.lower()
    tokens = [
        token
        for token in re.findall(r"[a-z0-9_]+(?:-[a-z0-9_]+)?", text)
        if len(token) > 1
    ]

    for chunk in re.findall(r"[\u3400-\u9fff]+", text):
        if len(chunk) == 1:
            tokens.append(chunk)
        else:
            tokens.extend(chunk[idx : idx + 2] for idx in range(0, len(chunk) - 1))

    return tokens


def _squash_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate_query(value: str, max_chars: int) -> str:
    if max_chars <= 0:
        return _squash_whitespace(value)
    text = _squash_whitespace(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _compute_bm25_scores(
    query_terms: list[str],
    docs: dict[str, list[str]],
    *,
    k1: float = 1.2,
    b: float = 0.75,
) -> dict[str, float]:
    if not docs:
        return {}

    normalized_default = {key: 0.0 for key in docs.keys()}
    filtered_query_terms = [term for term in query_terms if term]
    if not filtered_query_terms:
        return normalized_default

    doc_keys = sorted(docs.keys())
    total_docs = len(doc_keys)
    avg_doc_len = (
        sum(max(1, len(docs[key])) for key in doc_keys) / max(1, total_docs)
    ) or 1.0

    query_term_set = set(filtered_query_terms)
    doc_freq: dict[str, int] = {term: 0 for term in query_term_set}
    doc_counters: dict[str, Counter[str]] = {}
    doc_lengths: dict[str, int] = {}

    for key in doc_keys:
        terms = docs.get(key, [])
        counter = Counter(terms)
        doc_counters[key] = counter
        doc_lengths[key] = max(1, len(terms))
        for term in query_term_set:
            if counter.get(term, 0) > 0:
                doc_freq[term] += 1

    raw_scores: dict[str, float] = {}
    for key in doc_keys:
        score = 0.0
        counter = doc_counters[key]
        doc_len = doc_lengths[key]
        norm = k1 * (1 - b + b * (doc_len / avg_doc_len))

        for term in filtered_query_terms:
            term_freq = counter.get(term, 0)
            if term_freq <= 0:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(((total_docs - df + 0.5) / (df + 0.5)) + 1.0)
            score += idf * ((term_freq * (k1 + 1.0)) / (term_freq + norm))

        raw_scores[key] = score

    if not raw_scores:
        return normalized_default

    min_score = min(raw_scores.values())
    max_score = max(raw_scores.values())
    if max_score <= min_score:
        return normalized_default

    span = max_score - min_score
    return {
        key: round((value - min_score) / span, 6) for key, value in raw_scores.items()
    }


def extract_tool_intent_query(
    prompt: str,
    tools_dict: dict[str, dict[str, Any]],
    cfg: ToolRoutingConfig,
) -> tuple[str, dict[str, Any]]:
    raw_prompt = _squash_whitespace(prompt)
    if not raw_prompt:
        return "", {"enabled": bool(cfg.intent_query_enable), "reason": "empty_prompt"}

    fallback_query = _truncate_query(raw_prompt, cfg.intent_max_chars)
    if not cfg.intent_query_enable:
        return fallback_query, {
            "enabled": False,
            "reason": "disabled",
            "selected_clauses": [],
            "clauses": [],
        }

    clause_candidates = [
        _squash_whitespace(chunk)
        for chunk in re.split(r"[\n\r]+|[。！？!?；;]+|[,，]+", raw_prompt)
    ]
    clauses = [clause for clause in clause_candidates if clause]
    if not clauses:
        return fallback_query, {
            "enabled": True,
            "reason": "no_clauses",
            "selected_clauses": [],
            "clauses": [],
        }

    explicit_patterns: set[str] = set()
    for key, entry in tools_dict.items():
        explicit_patterns.add(_normalize_text(key))
        spec = entry.get("spec", {})
        if isinstance(spec, dict):
            explicit_patterns.add(_normalize_text(spec.get("name", "")))

    explicit_patterns = {pattern for pattern in explicit_patterns if pattern}

    mode_patterns = {
        mode: [
            _normalize_text(pattern)
            for pattern in cfg.mode_patterns.get(mode, [])
            if _normalize_text(pattern)
        ]
        for mode in ("search", "analyze", "chat")
    }

    action_patterns = [
        "search",
        "find",
        "lookup",
        "query",
        "retrieve",
        "inspect",
        "debug",
        "summarize",
        "analyze",
        "analysis",
        "diagnose",
        "use tool",
        "调用",
        "工具",
        "搜索",
        "查询",
        "检索",
        "分析",
        "排查",
        "调试",
        "总结",
        "提取",
        "读取",
        "查看",
        "帮我",
    ]
    noise_patterns = [
        "背景",
        "上下文",
        "补充",
        "顺便",
        "另外",
        "之前",
        "假设",
        "for reference",
        "background",
        "context",
    ]

    scored_clauses: list[dict[str, Any]] = []
    clause_count = len(clauses)
    for idx, clause in enumerate(clauses):
        normalized_clause = _normalize_text(clause)
        if not normalized_clause:
            continue

        explicit_hits = sum(
            1 for pattern in explicit_patterns if pattern in normalized_clause
        )
        action_hits = sum(
            1 for pattern in action_patterns if pattern in normalized_clause
        )
        mode_hits = 0
        for patterns in mode_patterns.values():
            mode_hits += sum(1 for pattern in patterns if pattern in normalized_clause)
        noise_hits = sum(1 for pattern in noise_patterns if pattern in normalized_clause)

        score = 0.0
        if explicit_hits:
            score += 3.0 + min(1.5, 0.5 * max(0, explicit_hits - 1))
        if action_hits:
            score += min(2.0, 0.7 * action_hits)
        if mode_hits:
            score += min(1.2, 0.4 * mode_hits)
        if len(normalized_clause) > 120:
            score -= 1.0
        if noise_hits:
            score -= min(1.5, 0.5 * noise_hits)

        tail_boost = (idx / max(1, clause_count - 1)) * 0.2
        score += tail_boost

        scored_clauses.append(
            {
                "index": idx,
                "clause": clause,
                "score": round(score, 6),
                "signals": {
                    "explicit_hits": explicit_hits,
                    "action_hits": action_hits,
                    "mode_hits": mode_hits,
                    "noise_hits": noise_hits,
                    "tail_boost": round(tail_boost, 6),
                },
            }
        )

    ranked = sorted(scored_clauses, key=lambda item: (-item["score"], item["index"]))
    selected = [
        clause
        for clause in ranked
        if clause["score"] > 0
        and (
            clause["signals"].get("explicit_hits", 0) > 0
            or clause["signals"].get("action_hits", 0) > 0
            or clause["signals"].get("mode_hits", 0) > 0
        )
    ][: cfg.intent_max_clauses]

    if not selected:
        return fallback_query, {
            "enabled": True,
            "reason": "score_below_threshold",
            "selected_clauses": [],
            "clauses": scored_clauses,
        }

    selected_by_order = sorted(selected, key=lambda item: item["index"])
    routing_query = _truncate_query(
        " ".join(item["clause"] for item in selected_by_order),
        cfg.intent_max_chars,
    )
    if not routing_query:
        routing_query = fallback_query

    return routing_query, {
        "enabled": True,
        "reason": "ok",
        "selected_clauses": selected_by_order,
        "clauses": scored_clauses,
    }


def _detect_mode(prompt: str, cfg: ToolRoutingConfig) -> str:
    if not cfg.keyword_fallback_enable:
        return "chat"
    text = prompt.lower()
    for mode in ("search", "analyze"):
        for pattern in cfg.mode_patterns.get(mode, []):
            if pattern and pattern.lower() in text:
                return mode
    return "chat"


def _stable_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _stable_json(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_stable_json(item) for item in value]
    return value


def _infer_source(entry: dict[str, Any]) -> tuple[str, str]:
    tool_id = str(entry.get("tool_id", "") or "")
    if entry.get("direct"):
        server = entry.get("server", {})
        if isinstance(server, dict):
            return "direct", str(server.get("url", "direct"))
        return "direct", "direct"

    tool_type = str(entry.get("type", "") or "")
    if tool_type == "mcp":
        spec = entry.get("spec", {})
        name = spec.get("name", "") if isinstance(spec, dict) else ""
        server_id = name.split("_", 1)[0] if "_" in str(name) else "mcp"
        return "mcp", str(server_id)

    if tool_type == "external" or tool_id.startswith("server:"):
        return "openapi", tool_id or "server"

    return "local", tool_id or "local"


def _build_candidate_text(spec: dict[str, Any]) -> str:
    name = _sanitize_metadata_text(spec.get("name", ""))
    description = _sanitize_metadata_text(spec.get("description", ""))
    param_names = " ".join(
        sorted((spec.get("parameters", {}).get("properties", {}) or {}).keys())
    )
    return f"{name} {description} {_sanitize_metadata_text(param_names)}".strip()


def build_tool_manifests(
    tools_dict: dict[str, dict[str, Any]],
    scope_id: str,
) -> dict[str, ToolManifest]:
    manifests: dict[str, ToolManifest] = {}

    for key in sorted(tools_dict.keys()):
        entry = tools_dict[key]
        spec = entry.get("spec", {})
        if not isinstance(spec, dict):
            continue

        source_type, source_id = _infer_source(entry)
        function_name = str(spec.get("name", key))
        description = _sanitize_metadata_text(spec.get("description", ""))
        parameters = _stable_json(spec.get("parameters", {}))

        canonical = {
            "source_type": source_type,
            "source_id": source_id,
            "function_name": function_name,
            "description": description,
            "parameters": parameters,
        }
        canonical_payload = json.dumps(
            canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        fingerprint = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

        manifest_id = get_manifest_id(scope_id=scope_id, key=key)

        metadata = {
            "key": key,
            "scope_id": scope_id,
            "source_type": source_type,
            "source_id": source_id,
            "function_name": function_name,
            "fingerprint": fingerprint,
        }

        manifests[key] = ToolManifest(
            key=key,
            manifest_id=manifest_id,
            source_type=source_type,
            source_id=source_id,
            function_name=function_name,
            text=_build_candidate_text(spec),
            fingerprint=fingerprint,
            metadata=metadata,
        )

    return manifests


def compute_manifest_version(manifests: dict[str, ToolManifest]) -> str:
    payload = "|".join(
        f"{key}:{manifests[key].fingerprint}" for key in sorted(manifests.keys())
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def sync_manifest_index(
    manifests: dict[str, ToolManifest],
    embedding_function: Any,
    query_prefix: str,
    *,
    vector_client: Any = None,
    collection_name: str = TOOL_ROUTING_COLLECTION,
    upsert_keys: set[str] | None = None,
    delete_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not manifests or embedding_function is None:
        return {"upserted": 0, "deleted": 0, "status": "skipped"}

    if vector_client is None:
        try:
            from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT as vc

            vector_client = vc
        except Exception:
            return {"upserted": 0, "deleted": 0, "status": "no_vector_client"}

    if upsert_keys is None:
        upsert_keys = set(manifests.keys())

    to_upsert = [manifests[key] for key in sorted(upsert_keys) if key in manifests]
    upserted = 0

    if to_upsert:
        texts = [m.text for m in to_upsert]
        embeddings = await embedding_function(texts, prefix=query_prefix)
        if not isinstance(embeddings, list) or len(embeddings) != len(to_upsert):
            return {"upserted": 0, "deleted": 0, "status": "embedding_failed"}

        # Vector DB backends in this codebase operate on dict payloads
        # (item["id"], item["vector"], ...). Keep this format for compatibility.
        vector_items = [
            {
                "id": manifest.manifest_id,
                "text": manifest.text,
                "vector": embedding,
                "metadata": manifest.metadata,
            }
            for manifest, embedding in zip(to_upsert, embeddings)
        ]
        vector_client.upsert(collection_name=collection_name, items=vector_items)
        upserted = len(vector_items)

    deleted = 0
    if delete_ids:
        vector_client.delete(collection_name=collection_name, ids=delete_ids)
        deleted = len(delete_ids)

    return {
        "upserted": upserted,
        "deleted": deleted,
        "status": "ok",
    }


async def semantic_retrieve_candidates(
    prompt: str,
    manifests: dict[str, ToolManifest],
    embedding_function: Any,
    query_prefix: str,
    top_n: int,
    *,
    scope_id: str | None = None,
    vector_client: Any = None,
    collection_name: str = TOOL_ROUTING_COLLECTION,
) -> tuple[list[str], dict[str, float]]:
    if not prompt or not manifests or embedding_function is None:
        return [], {}

    if vector_client is None:
        try:
            from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT as vc

            vector_client = vc
        except Exception:
            return [], {}

    try:
        query_vector = await embedding_function(prompt, prefix=query_prefix)
        result = vector_client.search(
            collection_name=collection_name,
            vectors=[query_vector],
            limit=max(1, top_n),
        )
    except Exception as e:
        log.debug("Tool routing semantic retrieval failed: %s", e)
        return [], {}

    if not result or not result.metadatas or not result.metadatas[0]:
        return [], {}

    candidate_keys: list[str] = []
    semantic_scores: dict[str, float] = {}
    manifest_keys = set(manifests.keys())
    expected_scope = scope_id or (
        str(next(iter(manifests.values())).metadata.get("scope_id", "") or "")
        if manifests
        else ""
    )
    result_ids_attr = getattr(result, "ids", None)
    result_ids = (
        result_ids_attr[0]
        if result_ids_attr
        and isinstance(result_ids_attr, list)
        and result_ids_attr
        and isinstance(result_ids_attr[0], list)
        else []
    )

    for idx, metadata in enumerate(result.metadatas[0]):
        if not isinstance(metadata, dict):
            continue

        metadata_scope = str(metadata.get("scope_id", "") or "")
        result_id = str(result_ids[idx]) if idx < len(result_ids) else ""
        if expected_scope:
            if metadata_scope and metadata_scope != expected_scope:
                continue
            if (
                not metadata_scope
                and result_id
                and not result_id.startswith(f"tool::{expected_scope}::")
            ):
                continue

        key = metadata.get("key", "")
        if key not in manifest_keys or key in semantic_scores:
            continue
        rank_score = 1.0 - (idx / max(1, top_n))
        semantic_scores[key] = round(max(0.0, rank_score), 6)
        candidate_keys.append(key)

    return candidate_keys, semantic_scores


def route_tools(
    prompt: str,
    tools_dict: dict[str, dict[str, Any]],
    cfg: ToolRoutingConfig,
    *,
    semantic_candidates: list[str] | None = None,
    semantic_scores: dict[str, float] | None = None,
    routing_query: str | None = None,
    clause_debug: dict[str, Any] | None = None,
) -> ToolRoutingDecision:
    if not tools_dict:
        return ToolRoutingDecision(
            [],
            [],
            {},
            {},
            "chat",
            "no_candidates",
            _truncate_query(routing_query or prompt, cfg.intent_max_chars),
            clause_debug or {},
        )

    mode = _detect_mode(prompt, cfg) if cfg.mode == "hybrid" else cfg.mode
    routing_query_text = _truncate_query(routing_query or prompt, cfg.intent_max_chars)
    prompt_normalized = _normalize_text(prompt)
    scoring_normalized = _normalize_text(routing_query_text)
    scoring_tokens = _tokenize(scoring_normalized)
    scoring_terms = _tokenize_terms(scoring_normalized)

    key_set = set(tools_dict.keys())
    candidate_keys = sorted(key_set)

    explicit_keys = {
        key
        for key in candidate_keys
        if _normalize_text(key) in prompt_normalized
        or _normalize_text(tools_dict[key].get("spec", {}).get("name", ""))
        in prompt_normalized
    }

    if semantic_candidates:
        scoped = [key for key in semantic_candidates if key in key_set]
        candidate_keys = sorted(set(scoped).union(explicit_keys)) or candidate_keys

    semantic_scores = semantic_scores or {}
    scored: list[tuple[str, float]] = []
    scores: dict[str, float] = {}
    score_breakdown: dict[str, dict[str, float]] = {}
    candidate_terms_map: dict[str, list[str]] = {}
    candidate_token_map: dict[str, set[str]] = {}

    for key in candidate_keys:
        entry = tools_dict[key]
        spec = entry.get("spec", {})
        if not isinstance(spec, dict):
            continue
        candidate_text = _build_candidate_text(spec)
        terms = _tokenize_terms(candidate_text)
        candidate_terms_map[key] = terms
        candidate_token_map[key] = set(terms)

    bm25_scores = _compute_bm25_scores(scoring_terms, candidate_terms_map)

    for key in candidate_keys:
        entry = tools_dict[key]
        spec = entry.get("spec", {})
        if not isinstance(spec, dict):
            continue

        candidate_text = _build_candidate_text(spec)
        candidate_tokens = candidate_token_map.get(key, set())

        lexical_overlap = 0.0
        if scoring_tokens and candidate_tokens:
            lexical_overlap = len(scoring_tokens.intersection(candidate_tokens)) / max(
                1, len(scoring_tokens)
            )
        bm25_score = bm25_scores.get(key, 0.0)
        lexical_mix = ((1.0 - cfg.bm25_weight) * lexical_overlap) + (
            cfg.bm25_weight * bm25_score
        )

        explicit_boost = 0.0
        name = _normalize_text(spec.get("name", ""))
        if name and name in prompt_normalized:
            explicit_boost += 0.35
        if _normalize_text(key) in prompt_normalized:
            explicit_boost += 0.25

        mode_boost = 0.0
        if mode == "search" and any(
            token in candidate_text for token in ["search", "query", "retrieve", "web"]
        ):
            mode_boost += 0.1
        if mode == "analyze" and any(
            token in candidate_text for token in ["analy", "inspect", "debug", "summar"]
        ):
            mode_boost += 0.1

        semantic_score = semantic_scores.get(key, lexical_mix)
        combined_score = (
            (semantic_score * (1.0 - cfg.lexical_weight))
            + (lexical_mix * cfg.lexical_weight)
            + (explicit_boost * cfg.rule_weight)
            + mode_boost
        )

        scores[key] = round(combined_score, 6)
        score_breakdown[key] = {
            "semantic": round(semantic_score, 6),
            "lexical_overlap": round(lexical_overlap, 6),
            "bm25": round(bm25_score, 6),
            "lexical_mix": round(lexical_mix, 6),
            "explicit_boost": round(explicit_boost, 6),
            "mode_boost": round(mode_boost, 6),
            "combined": round(combined_score, 6),
        }
        scored.append((key, combined_score))

    scored.sort(key=lambda item: (-item[1], item[0]))

    final_cap = min(cfg.final_top_k, cfg.max_injected_tools)
    selected = [
        key for key, score in scored if score >= cfg.min_confidence
    ][:final_cap]

    fallback_reason: str | None = None
    if not selected and mode in ("search", "analyze") and scored:
        selected = [scored[0][0]]
        fallback_reason = "keyword_mode_floor"
    elif not selected and mode == "chat" and cfg.chat_floor_enable and scored:
        keep_count = max(
            1,
            min(cfg.chat_floor_min_keep, final_cap, len(scored)),
        )
        selected = [key for key, _ in scored[:keep_count]]
        fallback_reason = "chat_mode_floor"

    if mode == "chat" and selected and fallback_reason != "chat_mode_floor":
        explicit_selected = [key for key in selected if key in explicit_keys]
        if not explicit_selected:
            selected = selected[:1]
            fallback_reason = "chat_mode_conservative"

    selected_set = set(selected)
    dropped = [key for key, _ in scored if key not in selected_set]
    return ToolRoutingDecision(
        selected,
        dropped,
        scores,
        score_breakdown,
        mode,
        fallback_reason,
        routing_query_text,
        clause_debug or {},
    )


def materialize_native_tools(
    selected_tools: dict[str, dict[str, Any]],
    max_schema_chars: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    used_chars = 0
    for selected_tool in selected_tools.values():
        spec = selected_tool.get("spec", {})
        if not isinstance(spec, dict):
            continue
        payload = {"type": "function", "function": spec}
        payload_len = len(json.dumps(payload, ensure_ascii=True))
        if used_chars + payload_len > max_schema_chars:
            continue
        used_chars += payload_len
        out.append(payload)
    return out


def select_expand_candidate(
    unknown_tool_name: str,
    dropped_tools: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    unknown_norm = _normalize_text(unknown_tool_name)
    if not unknown_norm:
        return None

    for key in sorted(dropped_tools.keys()):
        spec = dropped_tools[key].get("spec", {})
        if not isinstance(spec, dict):
            continue
        if _normalize_text(key) == unknown_norm or _normalize_text(
            spec.get("name", "")
        ) == unknown_norm:
            return key, dropped_tools[key]

    return None


async def bump_tool_routing_manifest_marker(request: Any) -> int:
    previous_marker = int(
        getattr(request.app.state, "TOOL_ROUTING_MANIFEST_VERSION", 0) or 0
    )
    marker = max(time.time_ns(), previous_marker + 1)
    request.app.state.TOOL_ROUTING_MANIFEST_VERSION = marker

    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            await redis_client.set("tool_routing:manifest_version", marker)
        except Exception as e:
            log.debug("Failed to persist tool routing marker to Redis: %s", e)

    return marker
