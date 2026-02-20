import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

def _load_config_fallbacks() -> tuple[object, object, object]:
    try:
        from open_webui.config import (
            ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE,
            ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE,
            ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST,
        )

        return (
            ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE,
            ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE,
            ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST,
        )
    except ModuleNotFoundError:
        return ("retrieval", 8000, 32000)


(
    _DEFAULT_MODE_FALLBACK,
    _MAX_TOKENS_PER_FILE_FALLBACK,
    _MAX_TOKENS_PER_REQUEST_FALLBACK,
) = _load_config_fallbacks()


FULL_MODE = "full"
RETRIEVAL_MODE = "retrieval"
DEFAULT_REASON = "ambiguous_default"
MALFORMED_ITEM_REASON = "malformed_item"


@dataclass
class _DecisionRow:
    index: int
    mode: str
    reason: str
    tokens: int
    is_manual: bool


@dataclass
class AdaptiveDecision:
    index: int
    mode: str
    reason: str
    estimated_tokens: int
    is_manual: bool
    applied: bool


def _safe_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _config_value(value: object) -> object:
    return getattr(value, "value", value)


def _resolve_config(config: Mapping[str, object] | None) -> dict[str, int | str]:
    config = config or {}

    default_mode = str(
        config.get(
            "default_mode",
            _config_value(_DEFAULT_MODE_FALLBACK),
        )
    ).lower()
    if default_mode not in {FULL_MODE, RETRIEVAL_MODE}:
        default_mode = RETRIEVAL_MODE

    max_tokens_per_file = _safe_int(
        config.get(
            "max_tokens_per_file",
            _config_value(_MAX_TOKENS_PER_FILE_FALLBACK),
        ),
        8000,
    )
    max_tokens_per_request = _safe_int(
        config.get(
            "max_tokens_per_request",
            _config_value(_MAX_TOKENS_PER_REQUEST_FALLBACK),
        ),
        32000,
    )
    small_file_tokens = _safe_int(
        config.get("small_file_tokens", min(1200, max_tokens_per_file // 4 or 1)),
        min(1200, max_tokens_per_file // 4 or 1),
    )

    return {
        "default_mode": default_mode,
        "max_tokens_per_file": max_tokens_per_file,
        "max_tokens_per_request": max_tokens_per_request,
        "small_file_tokens": max(1, small_file_tokens),
    }


def resolve_adaptive_config(config: Mapping[str, object] | None) -> dict[str, int | str]:
    """Public wrapper for middleware/tests."""
    return _resolve_config(config)


def classify_query_intent(query: str) -> str:
    normalized = (query or "").strip().lower()
    if not normalized:
        return "ambiguous"

    full_bias_patterns = (
        r"\bsummarize\b",
        r"\boverview\b",
        r"\bmain\s+points?\b",
        r"\bhigh\s*level\b",
        r"\bbig\s+picture\b",
    )
    retrieval_bias_patterns = (
        r"\bfind\b",
        r"\blocate\b",
        r"\bpage\b",
        r"\bsection\b",
        r"\bspecific\b",
        r"\bwhere\b",
    )

    if any(re.search(pattern, normalized) for pattern in retrieval_bias_patterns):
        return "retrieval_bias"
    if any(re.search(pattern, normalized) for pattern in full_bias_patterns):
        return "full_bias"
    return "ambiguous"


def _read_metadata(file_item: Mapping[str, object]) -> dict[str, object]:
    meta_candidate = file_item.get("meta")
    if isinstance(meta_candidate, Mapping):
        return {str(key): value for key, value in meta_candidate.items()}

    metadata_candidate = file_item.get("metadata")
    if isinstance(metadata_candidate, Mapping):
        return {str(key): value for key, value in metadata_candidate.items()}

    return {}


def estimate_file_tokens(file_item: Mapping[str, object] | None) -> int:
    if file_item is None:
        return 2000

    metadata = _read_metadata(file_item)

    for key in ("token_count", "tokens", "num_tokens", "estimated_tokens"):
        value = file_item.get(key)
        if value is None:
            value = metadata.get(key)
        parsed = _safe_int(value, -1)
        if parsed > 0:
            return parsed

    byte_value = None
    for key in ("size", "file_size", "bytes", "content_length"):
        if file_item.get(key) is not None:
            byte_value = file_item.get(key)
            break
        if metadata.get(key) is not None:
            byte_value = metadata.get(key)
            break
    parsed_bytes = _safe_int(byte_value, -1)
    if parsed_bytes > 0:
        return max(1, int(math.ceil(parsed_bytes / 3)))

    chars = None
    for key in ("char_count", "characters", "text_length"):
        if file_item.get(key) is not None:
            chars = file_item.get(key)
            break
        if metadata.get(key) is not None:
            chars = metadata.get(key)
            break
    parsed_chars = _safe_int(chars, -1)
    if parsed_chars > 0:
        return max(1, int(math.ceil(parsed_chars / 3)))

    pages = _safe_int(file_item.get("pages", metadata.get("pages")), -1)
    if pages > 0:
        return pages * 800

    return 2000


def decide_file_context_mode(
    query: str,
    file_item: Mapping[str, object],
    config: Mapping[str, object],
) -> tuple[str, str]:
    resolved = _resolve_config(config)
    max_tokens_per_file = int(resolved["max_tokens_per_file"])
    small_file_tokens = int(resolved["small_file_tokens"])

    explicit_context = str(file_item.get("context", "")).lower()
    if explicit_context in {FULL_MODE, RETRIEVAL_MODE}:
        return explicit_context, "manual_override"

    estimated_tokens = estimate_file_tokens(file_item)
    if estimated_tokens > max_tokens_per_file:
        return RETRIEVAL_MODE, "budget_cap"

    intent = classify_query_intent(query)
    if intent == "retrieval_bias":
        return RETRIEVAL_MODE, "targeted_query"

    if estimated_tokens <= small_file_tokens:
        return FULL_MODE, "small_file"

    if intent == "full_bias":
        return RETRIEVAL_MODE, "large_file"

    default_mode = str(resolved["default_mode"])
    return default_mode, "ambiguous_default"


def decide_file_contexts(
    query: str,
    items: Sequence[object],
    config: Mapping[str, object] | None,
) -> list[AdaptiveDecision]:
    """Return deterministic per-item decisions with request-level arbitration."""
    resolved = _resolve_config(config)
    normalized_items: list[Mapping[str, object]] = []
    initial: list[tuple[str, str]] = []

    for index, candidate in enumerate(items):
        if not isinstance(candidate, Mapping):
            normalized_items.append({"type": "file"})
            initial.append((RETRIEVAL_MODE, MALFORMED_ITEM_REASON))
            continue

        item = {str(key): value for key, value in candidate.items()}
        normalized_items.append(item)

        if item.get("_adaptive_excluded") is True:
            reason = str(item.get("_adaptive_reason") or "scope_denied")
            initial.append((RETRIEVAL_MODE, reason))
            continue

        item_type = str(item.get("type") or "file").lower()
        if item_type == "folder":
            initial.append((RETRIEVAL_MODE, "unsupported_item_type"))
            continue

        initial.append(decide_file_context_mode(query, item, resolved))

    arbitrated = apply_budget_arbitration(
        normalized_items,
        initial,
        max_tokens=int(resolved["max_tokens_per_request"]),
    )

    decisions: list[AdaptiveDecision] = []
    for index, item in enumerate(normalized_items):
        mode, reason = arbitrated[index] if index < len(arbitrated) else (RETRIEVAL_MODE, DEFAULT_REASON)
        explicit_context = str(item.get("context", "")).lower()
        decisions.append(
            AdaptiveDecision(
                index=index,
                mode=mode if mode in {FULL_MODE, RETRIEVAL_MODE} else RETRIEVAL_MODE,
                reason=reason,
                estimated_tokens=estimate_file_tokens(item),
                is_manual=explicit_context in {FULL_MODE, RETRIEVAL_MODE}
                or reason == "manual_override",
                applied=item.get("_adaptive_excluded") is not True,
            )
        )

    return decisions


def apply_adaptive_context_to_items(
    query: str,
    items: Sequence[object],
    config: Mapping[str, object] | None,
) -> tuple[list[dict[str, object]], list[AdaptiveDecision]]:
    """Apply deterministic context routing to metadata files payload."""
    decisions = decide_file_contexts(query=query, items=items, config=config)

    updated_items: list[dict[str, object]] = []
    for index, candidate in enumerate(items):
        item = dict(candidate) if isinstance(candidate, Mapping) else {}
        decision = decisions[index]

        if decision.applied:
            item["context"] = decision.mode
            item["_adaptive_reason"] = decision.reason
            item["_adaptive_tokens"] = decision.estimated_tokens
        else:
            item["context"] = RETRIEVAL_MODE
            item["_adaptive_reason"] = decision.reason

        updated_items.append(item)

    return updated_items, decisions


def _normalize_decision(
    decision: tuple[str, str] | list[str] | Mapping[str, object],
) -> tuple[str, str]:
    if isinstance(decision, tuple) and len(decision) == 2:
        return str(decision[0]), str(decision[1])
    if isinstance(decision, list) and len(decision) == 2:
        return str(decision[0]), str(decision[1])
    if isinstance(decision, Mapping):
        return (
            str(decision.get("mode", RETRIEVAL_MODE)),
            str(decision.get("reason", DEFAULT_REASON)),
        )
    return RETRIEVAL_MODE, DEFAULT_REASON


def apply_budget_arbitration(
    files: Sequence[Mapping[str, object]],
    decisions: Sequence[tuple[str, str] | list[str] | Mapping[str, object]],
    max_tokens: int,
) -> list[tuple[str, str]]:
    hard_cap = _safe_int(max_tokens, 0)

    normalized_decisions = [_normalize_decision(decision) for decision in decisions]

    rows: list[_DecisionRow] = []
    for idx, file_item in enumerate(files):
        mode, reason = (
            normalized_decisions[idx]
            if idx < len(normalized_decisions)
            else (RETRIEVAL_MODE, DEFAULT_REASON)
        )
        tokens = estimate_file_tokens(file_item)
        context = str(file_item.get("context", "")).lower()
        is_manual = reason == "manual_override" or context in {
            FULL_MODE,
            RETRIEVAL_MODE,
        }
        rows.append(
            _DecisionRow(
                index=idx,
                mode=mode if mode in {FULL_MODE, RETRIEVAL_MODE} else RETRIEVAL_MODE,
                reason=reason,
                tokens=tokens,
                is_manual=is_manual,
            )
        )

    total_tokens = sum(row.tokens for row in rows if row.mode == FULL_MODE)
    if total_tokens <= hard_cap:
        return [(row.mode, row.reason) for row in rows]

    def downgrade_row(row: _DecisionRow) -> None:
        nonlocal total_tokens
        if row.mode == FULL_MODE:
            row.mode = RETRIEVAL_MODE
            row.reason = "budget_cap"
            total_tokens -= row.tokens

    non_manual_candidates = sorted(
        [row for row in rows if row.mode == FULL_MODE and not row.is_manual],
        key=lambda row: (-row.tokens, row.index),
    )
    for row in non_manual_candidates:
        if total_tokens <= hard_cap:
            break
        downgrade_row(row)

    manual_candidates = sorted(
        [row for row in rows if row.mode == FULL_MODE and row.is_manual],
        key=lambda row: (-row.tokens, row.index),
    )
    for row in manual_candidates:
        if total_tokens <= hard_cap:
            break
        downgrade_row(row)

    return [(row.mode, row.reason) for row in rows]
