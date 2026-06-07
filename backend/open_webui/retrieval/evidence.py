from __future__ import annotations

import json
from dataclasses import dataclass, field
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any


EVIDENCE_TOOL_ERROR_CODES = frozenset(
    {
        'unsupported_image_query',
        'forbidden_image_ref',
        'evidence_not_found',
        'image_budget_exceeded',
        'vector_space_unavailable',
    }
)

_DEFAULT_TOP_K = 8
_DEFAULT_IMAGE_QUERY_BUDGET = 4
_EVIDENCE_MODE_VALUES = {'evidence', 'evidence_dual_write', 'evidence_primary'}
_TRUTHY = {'1', 'true', 'yes', 'on'}
_FALSEY = {'0', 'false', 'no', 'off', ''}
_ALLOWED_QUERY_IMAGE_REF_PREFIXES = (
    'chat:file:',
    'chat:image:',
    'ka:',
    'ke:',
    'asset:',
    'evidence:',
)


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        if value is None:
            continue
        value = str(value).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {'none', 'null'}:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return [stripped]
        if isinstance(parsed, list):
            return _dedupe_preserve_order(parsed)
        if parsed is None:
            return []
        return [str(parsed)]
    if isinstance(value, (list, tuple, set)):
        return _dedupe_preserve_order([str(item) for item in value if item is not None])
    return [str(value)]


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(1, value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {'none', 'null'}:
            return default
        try:
            return max(1, int(stripped))
        except ValueError:
            return default
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in _TRUTHY:
            return True
        if stripped in _FALSEY:
            return False
    return default


def _coerce_modalities(value: Any) -> list[str]:
    modalities = []
    for item in _coerce_string_list(value):
        normalized = item.strip().lower()
        if normalized in {'text', 'image'}:
            modalities.append(normalized)
    return _dedupe_preserve_order(modalities)


def _is_allowlisted_query_image_ref(ref: Any) -> bool:
    if not isinstance(ref, str):
        return False

    candidate = ref.strip()
    if not candidate:
        return False

    lowered = candidate.lower()
    if lowered.startswith(('http://', 'https://', 'data:', 'file://')):
        return False
    if candidate.startswith(('/', '.', '~')):
        return False

    return any(candidate.startswith(prefix) for prefix in _ALLOWED_QUERY_IMAGE_REF_PREFIXES)


@dataclass(slots=True)
class NormalizedQueryKnowledgeEvidence:
    evidence_refs: list[str] = field(default_factory=list)
    query_text: str | None = None
    query_image_refs: list[str] = field(default_factory=list)
    knowledge_ids: list[str] = field(default_factory=list)
    collection_ids: list[str] = field(default_factory=list)
    modalities: list[str] = field(default_factory=list)
    count: int = _DEFAULT_TOP_K
    top_k: int = _DEFAULT_TOP_K
    rerank: bool = True
    include_images: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            'evidence_refs': self.evidence_refs,
            'query_text': self.query_text,
            'query_image_refs': self.query_image_refs,
            'knowledge_ids': self.knowledge_ids,
            'collection_ids': self.collection_ids,
            'modalities': self.modalities,
            'count': self.count,
            'top_k': self.top_k,
            'rerank': self.rerank,
            'include_images': self.include_images,
        }


class EvidenceToolError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_payload(self, *, query: NormalizedQueryKnowledgeEvidence | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'ok': False,
            'results': [],
            'model_only_files': [],
            'error': {
                'code': self.code,
                'message': self.message,
            },
        }
        if self.details:
            payload['error']['details'] = self.details
        if query is not None:
            payload['query'] = query.to_payload()
        return payload


def has_evidence_enabled_knowledge_scope(scope_items: Iterable[Mapping[str, Any]] | None) -> bool:
    if not scope_items:
        return False

    for item in scope_items:
        if not isinstance(item, Mapping):
            continue
        candidate_values = (
            item.get('evidence_mode'),
            item.get('retrieval_mode'),
            item.get('knowledge_mode'),
            (item.get('meta') or {}).get('evidence_mode') if isinstance(item.get('meta'), Mapping) else None,
            (item.get('meta') or {}).get('retrieval_mode') if isinstance(item.get('meta'), Mapping) else None,
            (item.get('meta') or {}).get('knowledge_mode') if isinstance(item.get('meta'), Mapping) else None,
        )
        if any(str(value).strip().lower() in _EVIDENCE_MODE_VALUES for value in candidate_values if value is not None):
            return True

        top_level_flags = (
            item.get('evidence_enabled'),
            item.get('evidence'),
            (item.get('meta') or {}).get('evidence_enabled') if isinstance(item.get('meta'), Mapping) else None,
            (item.get('meta') or {}).get('evidence') if isinstance(item.get('meta'), Mapping) else None,
        )
        if any(_coerce_bool(flag, False) for flag in top_level_flags if flag is not None):
            return True

    return False


def collect_allowlisted_query_image_refs(metadata: Mapping[str, Any] | None) -> list[str]:
    if not metadata:
        return []

    refs: list[str] = []
    files = metadata.get('files')
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes)):
        return []

    for file_item in files:
        if not isinstance(file_item, Mapping):
            continue

        item_type = str(file_item.get('type') or '').strip().lower()
        if item_type and item_type not in {'image', 'image_url', 'input_image', 'file'}:
            continue

        if file_ref := file_item.get('id'):
            if _is_allowlisted_query_image_ref(file_ref):
                refs.append(str(file_ref))
        if file_ref := file_item.get('file_id'):
            if _is_allowlisted_query_image_ref(file_ref):
                refs.append(str(file_ref))

        image_url = file_item.get('image_url')
        if isinstance(image_url, Mapping):
            if ref := image_url.get('url'):
                if _is_allowlisted_query_image_ref(ref):
                    refs.append(str(ref))
        elif isinstance(image_url, str):
            if _is_allowlisted_query_image_ref(image_url):
                refs.append(image_url)

    return _dedupe_preserve_order(refs)


def resolve_query_image_refs(
    query_image_refs: Sequence[str] | None,
    *,
    allowed_refs: Iterable[str] | None,
    acl_resolver: Callable[[str], bool] | None = None,
    max_refs: int = _DEFAULT_IMAGE_QUERY_BUDGET,
) -> list[str]:
    refs = _dedupe_preserve_order(query_image_refs or [])

    if len(refs) > max_refs:
        raise EvidenceToolError(
            'image_budget_exceeded',
            f'query_image_refs exceeds the allowed budget of {max_refs}',
            details={'max_refs': max_refs, 'requested_refs': len(refs)},
        )

    allowed = {str(ref).strip() for ref in allowed_refs or [] if str(ref).strip()}
    resolved_refs: list[str] = []
    for ref in refs:
        if not _is_allowlisted_query_image_ref(ref):
            raise EvidenceToolError(
                'forbidden_image_ref',
                'query_image_refs must use an allowlisted ref scheme',
                details={'ref': ref},
            )
        if ref not in allowed:
            raise EvidenceToolError(
                'forbidden_image_ref',
                'query_image_refs must reference an allowlisted chat/files ref',
                details={'ref': ref},
            )
        if acl_resolver is not None and not acl_resolver(ref):
            raise EvidenceToolError(
                'forbidden_image_ref',
                'query_image_refs failed the secondary ACL check',
                details={'ref': ref},
            )
        resolved_refs.append(ref)

    return resolved_refs


def normalize_query_knowledge_evidence_args(
    *,
    evidence_refs: Any = None,
    query_text: Any = None,
    query_image_refs: Any = None,
    knowledge_ids: Any = None,
    collection_ids: Any = None,
    modalities: Any = None,
    count: Any = None,
    top_k: Any = None,
    rerank: Any = None,
    include_images: Any = None,
) -> NormalizedQueryKnowledgeEvidence:
    resolved_evidence_refs = _coerce_string_list(evidence_refs)
    resolved_query_text = None
    if isinstance(query_text, str):
        stripped = query_text.strip()
        resolved_query_text = stripped or None
    elif query_text is not None:
        resolved_query_text = str(query_text).strip() or None

    resolved_query_image_refs = _coerce_string_list(query_image_refs)

    resolved_collection_ids = _coerce_string_list(collection_ids)
    resolved_knowledge_ids = _coerce_string_list(knowledge_ids)
    if resolved_collection_ids or resolved_knowledge_ids:
        resolved_scope_ids = _dedupe_preserve_order([*resolved_collection_ids, *resolved_knowledge_ids])
    else:
        resolved_scope_ids = []

    resolved_modalities = _coerce_modalities(modalities)
    resolved_top_k = _coerce_int(top_k if top_k is not None else count, _DEFAULT_TOP_K)

    return NormalizedQueryKnowledgeEvidence(
        evidence_refs=resolved_evidence_refs,
        query_text=resolved_query_text,
        query_image_refs=resolved_query_image_refs,
        knowledge_ids=resolved_scope_ids,
        collection_ids=resolved_scope_ids,
        modalities=resolved_modalities,
        count=resolved_top_k,
        top_k=resolved_top_k,
        rerank=_coerce_bool(rerank, True),
        include_images=_coerce_bool(include_images, True),
    )


def build_query_knowledge_evidence_response(
    *,
    query: NormalizedQueryKnowledgeEvidence,
    error: EvidenceToolError | None = None,
    results: list[dict[str, Any]] | None = None,
    model_only_files: list[dict[str, Any]] | None = None,
) -> str:
    payload: dict[str, Any] = {
        'ok': error is None,
        'query': query.to_payload(),
        'results': results or [],
        'model_only_files': model_only_files or [],
    }
    if error is not None:
        payload['error'] = {
            'code': error.code,
            'message': error.message,
            **({'details': error.details} if error.details else {}),
        }
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
