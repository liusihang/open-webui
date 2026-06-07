from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Request
from sqlalchemy.exc import OperationalError

from open_webui.models.evidence import (
    KnowledgeEvidenceAssets,
    KnowledgeEvidenceModel,
    KnowledgeEvidences,
    KnowledgeVectorSpaceModel,
    KnowledgeVectorSpaces,
)
from open_webui.retrieval.vector.multimodal import (
    MultimodalVectorSpaceError,
    resolve_multimodal_vector_space,
    search_multimodal_evidence,
)
from open_webui.storage.provider import Storage


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
_DEFAULT_MODEL_IMAGE_BUDGET = 4
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


def derive_knowledge_ids_from_scope(scope_items: Iterable[Mapping[str, Any]] | None) -> list[str]:
    knowledge_ids: list[str] = []
    for item in scope_items or []:
        if not isinstance(item, Mapping):
            continue
        item_type = str(item.get('type') or '').strip().lower()
        item_id = str(item.get('id') or '').strip()
        if item_id and item_type in {'collection', 'knowledge'}:
            knowledge_ids.append(item_id)
    return _dedupe_preserve_order(knowledge_ids)


def resolve_scoped_knowledge_ids(
    query: NormalizedQueryKnowledgeEvidence,
    *,
    effective_scope: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    allowed_ids = derive_knowledge_ids_from_scope(effective_scope)
    if not allowed_ids:
        query.knowledge_ids = []
        query.collection_ids = []
        return []

    if query.knowledge_ids:
        allowed_set = set(allowed_ids)
        scoped_ids = [knowledge_id for knowledge_id in query.knowledge_ids if knowledge_id in allowed_set]
    else:
        scoped_ids = allowed_ids

    scoped_ids = _dedupe_preserve_order(scoped_ids)
    query.knowledge_ids = scoped_ids
    query.collection_ids = scoped_ids
    return scoped_ids


def _safe_evidence_url_part(evidence_ref: str) -> str:
    return quote(evidence_ref, safe='')


def _build_evidence_source(evidence: KnowledgeEvidenceModel) -> dict[str, Any]:
    ref_path = _safe_evidence_url_part(evidence.evidence_ref)
    content_url = f'/api/v1/knowledge/{evidence.knowledge_id}/evidence/{ref_path}/content'
    source: dict[str, Any] = {
        'id': evidence.evidence_ref,
        'name': evidence.source_name,
        'type': 'evidence',
        'file_id': evidence.file_id,
        'knowledge_id': evidence.knowledge_id,
        'evidence_ref': evidence.evidence_ref,
        'modality': evidence.modality,
        'evidence_kind': evidence.evidence_kind,
        'content_url': content_url,
    }
    if evidence.modality == 'image':
        source['thumbnail_url'] = f'/api/v1/knowledge/{evidence.knowledge_id}/evidence/{ref_path}/thumbnail'
    if evidence.page_index is not None:
        source['page_index'] = evidence.page_index
    if evidence.title:
        source['title'] = evidence.title
    return source


def _build_evidence_preview(evidence: KnowledgeEvidenceModel, source: Mapping[str, Any]) -> dict[str, Any]:
    if evidence.modality == 'image':
        preview: dict[str, Any] = {
            'type': 'image',
            'caption': evidence.preview_text or evidence.content_text or evidence.title or '',
            'source': evidence.source_name,
            'thumbnail_url': source.get('thumbnail_url'),
            'content_url': source.get('content_url'),
        }
        if evidence.content_text:
            preview['text'] = evidence.content_text
        if evidence.page_index is not None:
            preview['page'] = evidence.page_index
        return {key: value for key, value in preview.items() if value is not None}

    preview = {
        'type': 'text',
        'text': evidence.preview_text or evidence.content_text or evidence.title or '',
        'source': evidence.source_name,
        'content_url': source.get('content_url'),
    }
    if evidence.page_index is not None:
        preview['page'] = evidence.page_index
    return {key: value for key, value in preview.items() if value is not None}


def _build_evidence_metadata(
    evidence: KnowledgeEvidenceModel,
    *,
    source: Mapping[str, Any],
    score: float | int | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        'source': evidence.source_name,
        'name': evidence.source_name,
        'file_id': evidence.file_id,
        'knowledge_id': evidence.knowledge_id,
        'evidence_ref': evidence.evidence_ref,
        'modality': evidence.modality,
        'evidence_kind': evidence.evidence_kind,
        'content_hash': evidence.content_hash,
        'projection_profile': evidence.projection_profile,
        'projection_config_hash': evidence.projection_config_hash,
        'chunk_index': evidence.chunk_index,
        'chunk_total': evidence.chunk_total,
        'content_url': source.get('content_url'),
        'preview': _build_evidence_preview(evidence, source),
    }
    if source.get('thumbnail_url'):
        metadata['thumbnail_url'] = source['thumbnail_url']
    if evidence.retrieval_chunk_uid:
        metadata['retrieval_chunk_uid'] = evidence.retrieval_chunk_uid
    if evidence.retrieval_chunk_row_id is not None:
        metadata['retrieval_chunk_row_id'] = evidence.retrieval_chunk_row_id
    if evidence.page_index is not None:
        metadata['page_index'] = evidence.page_index
    if evidence.anchor_json:
        metadata['anchor'] = evidence.anchor_json
    if score is not None:
        metadata['score'] = score
    return metadata


def _compact_evidence_content(evidence: KnowledgeEvidenceModel) -> str:
    for candidate in (evidence.content_text, evidence.preview_text, evidence.title, evidence.source_name):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ''


def _is_missing_vector_space_schema_error(error: OperationalError) -> bool:
    message = str(getattr(error, 'orig', error)).lower()
    return 'no such table' in message and 'knowledge_vector_space' in message


def _serialize_evidence_result(
    evidence: KnowledgeEvidenceModel,
    *,
    score: float | int | None = None,
) -> dict[str, Any]:
    source = _build_evidence_source(evidence)
    result = {
        'evidence_ref': evidence.evidence_ref,
        'modality': evidence.modality,
        'evidence_kind': evidence.evidence_kind,
        'title': evidence.title,
        'content': _compact_evidence_content(evidence),
        'preview_text': evidence.preview_text,
        'source': source,
        'metadata': _build_evidence_metadata(evidence, source=source, score=score),
    }
    if score is not None:
        result['score'] = score
    return result


async def _read_model_image_data_url(evidence: KnowledgeEvidenceModel) -> dict[str, Any] | None:
    if evidence.modality != 'image' or not evidence.asset_id:
        return None

    asset = await KnowledgeEvidenceAssets.get_asset_by_id(evidence.asset_id)
    if asset is None or asset.status != 'ready':
        return None
    if not str(asset.mime_type or '').lower().startswith('image/'):
        return None

    file_path = await asyncio.to_thread(Storage.get_file, asset.storage_uri)
    data = await asyncio.to_thread(Path(file_path).read_bytes)
    data_url = f'{asset.mime_type};base64,{base64.b64encode(data).decode("ascii")}'
    return {
        'type': 'image',
        'evidence_ref': evidence.evidence_ref,
        'file_id': evidence.file_id,
        'knowledge_id': evidence.knowledge_id,
        'mime_type': asset.mime_type,
        'width': asset.width,
        'height': asset.height,
        'url': f'data:{data_url}',
    }


async def _hydrate_evidence_results(
    search_hits: Sequence[str | Mapping[str, Any]],
    *,
    allowed_knowledge_ids: set[str],
    include_images: bool,
    model_image_budget: int = _DEFAULT_MODEL_IMAGE_BUDGET,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    model_only_files: list[dict[str, Any]] = []
    missing_refs: list[str] = []
    seen_refs: set[str] = set()

    for hit in search_hits:
        if isinstance(hit, str):
            evidence_ref = hit
            score = None
        elif isinstance(hit, Mapping):
            evidence_ref = str(hit.get('evidence_ref') or '').strip()
            score = hit.get('score', hit.get('distance'))
        else:
            continue
        if not evidence_ref or evidence_ref in seen_refs:
            continue
        seen_refs.add(evidence_ref)

        evidence = await KnowledgeEvidences.get_evidence_by_ref(evidence_ref)
        if evidence is None or not evidence.is_active:
            missing_refs.append(evidence_ref)
            continue
        if allowed_knowledge_ids and evidence.knowledge_id not in allowed_knowledge_ids:
            missing_refs.append(evidence_ref)
            continue

        results.append(_serialize_evidence_result(evidence, score=score))
        if include_images and len(model_only_files) < model_image_budget:
            image_file = await _read_model_image_data_url(evidence)
            if image_file is not None:
                model_only_files.append(image_file)

    return results, model_only_files, missing_refs


async def _resolve_query_vector_spaces(
    query: NormalizedQueryKnowledgeEvidence,
    *,
    knowledge_ids: list[str],
) -> list[KnowledgeVectorSpaceModel]:
    if not knowledge_ids:
        raise EvidenceToolError(
            'vector_space_unavailable',
            'Evidence retrieval requires a scoped knowledge base',
        )

    query_modality = 'image' if query.query_image_refs else 'text'
    vector_spaces: list[KnowledgeVectorSpaceModel] = []
    for knowledge_id in knowledge_ids:
        try:
            selection = await resolve_multimodal_vector_space(
                knowledge_id=knowledge_id,
                query_modality=query_modality,
            )
        except MultimodalVectorSpaceError as e:
            code = 'unsupported_image_query' if e.code == 'unsupported_image_query' else 'vector_space_unavailable'
            raise EvidenceToolError(code, e.message, details=e.details) from e
        except OperationalError as e:
            if not _is_missing_vector_space_schema_error(e):
                raise
            raise EvidenceToolError(
                'vector_space_unavailable',
                'No active vector space is available for the supplied knowledge_id/profile',
                details={'knowledge_id': knowledge_id},
            ) from e
        vector_spaces.append(selection.vector_space)
    return vector_spaces


async def query_knowledge_evidence_runtime(
    *,
    query: NormalizedQueryKnowledgeEvidence,
    request: Request | None = None,
    user: Mapping[str, Any] | None = None,
    effective_scope: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    scoped_knowledge_ids = resolve_scoped_knowledge_ids(query, effective_scope=effective_scope)
    allowed_knowledge_ids = set(scoped_knowledge_ids)

    if query.evidence_refs:
        if not allowed_knowledge_ids:
            return build_query_knowledge_evidence_response(
                query=query,
                error=EvidenceToolError(
                    'vector_space_unavailable',
                    'Evidence retrieval requires a scoped knowledge base',
                ),
            )
        results, model_only_files, missing_refs = await _hydrate_evidence_results(
            query.evidence_refs,
            allowed_knowledge_ids=allowed_knowledge_ids,
            include_images=query.include_images,
        )
        if missing_refs and not results:
            return build_query_knowledge_evidence_response(
                query=query,
                error=EvidenceToolError(
                    'evidence_not_found',
                    'No matching active evidence rows were found',
                    details={'evidence_refs': missing_refs},
                ),
            )
        return build_query_knowledge_evidence_response(
            query=query,
            results=results,
            model_only_files=model_only_files,
        )

    if not query.query_text and not query.query_image_refs:
        return build_query_knowledge_evidence_response(
            query=query,
            error=EvidenceToolError(
                'vector_space_unavailable',
                'query_knowledge_evidence requires evidence_refs, query_text, or query_image_refs',
            ),
        )

    search_adapter = getattr(getattr(getattr(request, 'app', None), 'state', None), 'EVIDENCE_RETRIEVAL_SEARCH', None)
    if not callable(search_adapter):
        search_adapter = search_multimodal_evidence

    vector_spaces = await _resolve_query_vector_spaces(query, knowledge_ids=scoped_knowledge_ids)
    try:
        hits = await search_adapter(
            query=query,
            vector_spaces=vector_spaces,
            user=user,
            request=request,
        )
    except MultimodalVectorSpaceError as e:
        code = e.code if e.code in EVIDENCE_TOOL_ERROR_CODES else 'vector_space_unavailable'
        raise EvidenceToolError(code, e.message, details=e.details) from e
    results, model_only_files, _ = await _hydrate_evidence_results(
        hits or [],
        allowed_knowledge_ids=allowed_knowledge_ids,
        include_images=query.include_images,
    )
    return build_query_knowledge_evidence_response(
        query=query,
        results=results[: query.top_k],
        model_only_files=model_only_files,
    )
