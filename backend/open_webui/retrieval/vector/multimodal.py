from __future__ import annotations

import asyncio
import inspect
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from langchain_core.documents import Document
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.evidence import (
    KnowledgeEvidenceAssetModel,
    KnowledgeEvidenceAssets,
    KnowledgeEvidenceEmbeddingModel,
    KnowledgeEvidenceEmbeddings,
    KnowledgeEvidenceModel,
    KnowledgeEvidences,
    KnowledgeVectorSpaceModel,
    KnowledgeVectorSpaces,
    compute_knowledge_evidence_embedding_id,
)
from open_webui.models.files import FileModel, Files
from open_webui.storage.provider import Storage
from open_webui.retrieval.vector.main import VectorItem

_MODALITIES = {"text", "image"}
_VECTOR_ROLES = {
    "text": "text_chunk_dense",
    "image": "image_dense",
}
_UNSAFE_IMAGE_DESCRIPTOR_KEYS = {
    "base64",
    "bytes",
    "content_bytes",
    "data_url",
    "file_path",
    "image_bytes",
    "image_url",
    "path",
    "raw_bytes",
    "raw_image",
    "source_url",
    "storage_uri",
    "url",
}
RAG_EMBEDDING_QUERY_PREFIX = os.getenv("RAG_EMBEDDING_QUERY_PREFIX", None)
RAG_EMBEDDING_CONTENT_PREFIX = os.getenv("RAG_EMBEDDING_CONTENT_PREFIX", None)
_DATA_IMAGE_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=_-]+")
_IMAGE_BYTES_RE = re.compile(r"['\"]?image_bytes['\"]?\s*:\s*b?(['\"]).*?\1")


@dataclass(slots=True)
class MultimodalVectorSpaceSelection:
    vector_space: KnowledgeVectorSpaceModel
    collection_name: str


@dataclass(slots=True)
class MultimodalEvidenceEmbeddingWriteResult:
    embedding: KnowledgeEvidenceEmbeddingModel | None = None
    vector_item: VectorItem | None = None
    error: str | None = None


@dataclass(slots=True)
class ResolvedQueryImageInput:
    ref: str
    file_id: str
    mime_type: str
    image_bytes: bytes
    filename: str | None = None

    def to_embedding_payload(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "file_id": self.file_id,
            "mime_type": self.mime_type,
            "image_bytes": self.image_bytes,
            **({"filename": self.filename} if self.filename else {}),
        }


@dataclass(slots=True)
class NormalizedMultimodalEvidenceInput:
    knowledge_id: str
    file_id: str
    modality: Literal["text", "image"]
    evidence_kind: str
    source_name: str
    content_hash: str
    projection_config_hash: str
    chunk_index: int = 1
    chunk_total: int = 1
    evidence_ref: str | None = None
    text: str | None = None
    preview_text: str | None = None
    title: str | None = None
    asset_ref: str | None = None
    retrieval_chunk_uid: str | None = None
    retrieval_chunk_row_id: int | None = None
    page_index: int | None = None
    vector_space_id: str | None = None
    retrieval_profile: str | None = None

    @property
    def vector_text(self) -> str:
        candidates = (
            self.text,
            self.preview_text,
            self.title,
            self.source_name,
            self.evidence_ref,
        )
        for candidate in candidates:
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        return ""


class MultimodalVectorSpaceError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def sanitize_embedding_error(error: Any) -> str:
    message = str(error)
    message = _DATA_IMAGE_RE.sub("[redacted-image-payload]", message)
    message = _IMAGE_BYTES_RE.sub("[redacted-image-payload]", message)
    return message


def _normalize_modality(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _MODALITIES:
        raise ValueError(f"Unsupported multimodal descriptor modality: {value}")
    return normalized


def _coerce_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value).strip() or None


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _reject_unsafe_image_descriptor_fields(descriptor: Mapping[str, Any]) -> None:
    unsafe_fields = []
    for key in _UNSAFE_IMAGE_DESCRIPTOR_KEYS:
        if key not in descriptor:
            continue
        value = descriptor.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        unsafe_fields.append(key)

    if unsafe_fields:
        raise MultimodalVectorSpaceError(
            "unsafe_image_descriptor",
            "image evidence descriptors must reference stored evidence, not raw paths, URLs, bytes, or data URLs",
            details={"fields": sorted(unsafe_fields)},
        )


def normalize_multimodal_evidence_input(descriptor: Mapping[str, Any]) -> NormalizedMultimodalEvidenceInput:
    modality = _normalize_modality(descriptor.get("modality") or descriptor.get("evidence_modality") or descriptor.get("kind"))
    if modality == "image":
        _reject_unsafe_image_descriptor_fields(descriptor)

    knowledge_id = _coerce_optional_text(descriptor.get("knowledge_id"))
    file_id = _coerce_optional_text(descriptor.get("file_id"))
    evidence_kind = _coerce_optional_text(descriptor.get("evidence_kind")) or (
        "text_chunk" if modality == "text" else "standalone_image"
    )
    source_name = _coerce_optional_text(
        descriptor.get("source_name")
        or descriptor.get("filename")
        or descriptor.get("file_name")
        or descriptor.get("title")
        or file_id
        or descriptor.get("evidence_ref")
    )
    content_hash = _coerce_optional_text(descriptor.get("content_hash") or descriptor.get("sha256") or descriptor.get("hash"))
    projection_config_hash = _coerce_optional_text(descriptor.get("projection_config_hash") or descriptor.get("profile_hash"))

    if not knowledge_id:
        raise MultimodalVectorSpaceError("missing_knowledge_id", "knowledge_id is required for multimodal evidence inputs")
    if not file_id:
        raise MultimodalVectorSpaceError("missing_file_id", "file_id is required for multimodal evidence inputs")
    if not content_hash:
        raise MultimodalVectorSpaceError(
            "missing_content_hash",
            "content_hash/sha256/hash is required for multimodal evidence inputs",
        )
    if not projection_config_hash:
        raise MultimodalVectorSpaceError(
            "missing_projection_config_hash",
            "projection_config_hash is required for multimodal evidence inputs",
        )
    if not source_name:
        raise MultimodalVectorSpaceError("missing_source_name", "source_name is required for multimodal evidence inputs")

    return NormalizedMultimodalEvidenceInput(
        knowledge_id=knowledge_id,
        file_id=file_id,
        modality=modality,  # type: ignore[arg-type]
        evidence_kind=evidence_kind,
        source_name=source_name,
        content_hash=content_hash,
        projection_config_hash=projection_config_hash,
        chunk_index=_coerce_int(descriptor.get("chunk_index"), 1),
        chunk_total=_coerce_int(descriptor.get("chunk_total"), 1),
        evidence_ref=_coerce_optional_text(descriptor.get("evidence_ref")),
        text=_coerce_optional_text(descriptor.get("text") or descriptor.get("content_text") or descriptor.get("ocr_text")),
        preview_text=_coerce_optional_text(descriptor.get("preview_text") or descriptor.get("caption")),
        title=_coerce_optional_text(descriptor.get("title")),
        asset_ref=_coerce_optional_text(descriptor.get("asset_ref")),
        retrieval_chunk_uid=_coerce_optional_text(descriptor.get("retrieval_chunk_uid")),
        retrieval_chunk_row_id=(
            int(descriptor["retrieval_chunk_row_id"])
            if descriptor.get("retrieval_chunk_row_id") is not None
            else None
        ),
        page_index=(
            int(descriptor["page_index"])
            if descriptor.get("page_index") is not None
            else None
        ),
        vector_space_id=_coerce_optional_text(descriptor.get("vector_space_id")),
        retrieval_profile=_coerce_optional_text(descriptor.get("retrieval_profile")),
    )


async def resolve_multimodal_vector_space(
    *,
    knowledge_id: str,
    vector_space_id: str | None = None,
    retrieval_profile: str | None = None,
    query_modality: str | None = None,
    evidence_modality: str | None = None,
    db: AsyncSession | None = None,
) -> MultimodalVectorSpaceSelection:
    selection: KnowledgeVectorSpaceModel | None
    if vector_space_id:
        selection = await KnowledgeVectorSpaces.get_vector_space_by_id(vector_space_id, db=db)
        if selection is None or selection.knowledge_id != knowledge_id:
            raise MultimodalVectorSpaceError(
                "vector_space_unavailable",
                "Requested vector space is not available for the supplied knowledge_id",
                details={"knowledge_id": knowledge_id, "vector_space_id": vector_space_id},
            )
        if retrieval_profile and selection.retrieval_profile != retrieval_profile:
            raise MultimodalVectorSpaceError(
                "vector_space_unavailable",
                "Requested vector space does not match the supplied retrieval_profile",
                details={
                    "knowledge_id": knowledge_id,
                    "vector_space_id": vector_space_id,
                    "retrieval_profile": retrieval_profile,
                    "resolved_profile": selection.retrieval_profile,
                },
            )
    else:
        selection = await KnowledgeVectorSpaces.get_active_vector_space(
            knowledge_id=knowledge_id,
            retrieval_profile=retrieval_profile,
            db=db,
        )
        if selection is None:
            raise MultimodalVectorSpaceError(
                "vector_space_unavailable",
                "No active vector space is available for the supplied knowledge_id/profile",
                details={
                    "knowledge_id": knowledge_id,
                    "retrieval_profile": retrieval_profile,
                },
            )

    for modality, kind in (("query", query_modality), ("evidence", evidence_modality)):
        if kind is None:
            continue
        normalized_kind = str(kind).strip().lower()
        if normalized_kind not in _MODALITIES:
            raise ValueError(f"Unsupported {modality} modality: {kind}")
        capability_attr = f"supports_{normalized_kind}_{modality}"
        if not getattr(selection, capability_attr):
            raise MultimodalVectorSpaceError(
                f"unsupported_{normalized_kind}_{modality}",
                f"Vector space does not support {normalized_kind} {modality}s",
                details={
                    "knowledge_id": knowledge_id,
                    "vector_space_id": selection.id,
                    "retrieval_profile": selection.retrieval_profile,
                    "modality": normalized_kind,
                    "kind": modality,
                },
            )

    return MultimodalVectorSpaceSelection(
        vector_space=selection,
        collection_name=f"{knowledge_id}:{selection.id}",
    )


def get_multimodal_vector_role(modality: str) -> str:
    normalized = _normalize_modality(modality)
    return _VECTOR_ROLES[normalized]


def build_multimodal_vector_metadata(
    *,
    descriptor: NormalizedMultimodalEvidenceInput,
    selection: MultimodalVectorSpaceSelection,
    vector_role: str | None = None,
    embedding_format: str = "single_dense",
) -> dict[str, Any]:
    role = vector_role or get_multimodal_vector_role(descriptor.modality)
    return {
        "knowledge_id": descriptor.knowledge_id,
        "file_id": descriptor.file_id,
        "evidence_ref": descriptor.evidence_ref,
        "modality": descriptor.modality,
        "evidence_kind": descriptor.evidence_kind,
        "vector_space_id": selection.vector_space.id,
        "retrieval_profile": selection.vector_space.retrieval_profile,
        "vector_backend_collection": selection.collection_name,
        "vector_role": role,
        "embedding_format": embedding_format,
        "projection_config_hash": selection.vector_space.projection_config_hash,
        "source_name": descriptor.source_name,
        "title": descriptor.title,
        "asset_ref": descriptor.asset_ref,
        "retrieval_chunk_uid": descriptor.retrieval_chunk_uid,
        "retrieval_chunk_row_id": descriptor.retrieval_chunk_row_id,
        "chunk_index": descriptor.chunk_index,
        "chunk_total": descriptor.chunk_total,
        "page_index": descriptor.page_index,
        "content_hash": descriptor.content_hash,
    }


def build_multimodal_vector_item(
    *,
    vector: Sequence[float | int],
    descriptor: NormalizedMultimodalEvidenceInput,
    selection: MultimodalVectorSpaceSelection,
    vector_role: str | None = None,
    embedding_format: str = "single_dense",
) -> VectorItem:
    metadata = build_multimodal_vector_metadata(
        descriptor=descriptor,
        selection=selection,
        vector_role=vector_role,
        embedding_format=embedding_format,
    )
    role = metadata["vector_role"]
    embedding_id = compute_knowledge_evidence_embedding_id(
        evidence_ref=descriptor.evidence_ref or descriptor.source_name,
        vector_space_id=selection.vector_space.id,
        vector_role=role,
        vector_backend_collection=selection.collection_name,
    )
    return VectorItem(
        id=embedding_id,
        text=descriptor.vector_text,
        vector=list(vector),
        metadata=metadata,
    )


def build_multimodal_query_embedding_input(
    *,
    query_text: str | None,
    query_images: Sequence[ResolvedQueryImageInput | Mapping[str, Any]] | None,
    vector_space: KnowledgeVectorSpaceModel,
) -> str | dict[str, Any]:
    if query_images:
        images = [
            image.to_embedding_payload() if isinstance(image, ResolvedQueryImageInput) else dict(image)
            for image in query_images
        ]
        return {
            "query_text": query_text,
            "query_images": images,
            "query_modality": "mixed" if query_text else "image",
            "knowledge_id": vector_space.knowledge_id,
            "vector_space_id": vector_space.id,
            "retrieval_profile": vector_space.retrieval_profile,
            "embedding_model": vector_space.embedding_model,
        }
    return query_text or ""


def _extract_file_content_type(file: FileModel) -> str | None:
    meta = file.meta if isinstance(file.meta, Mapping) else {}
    content_type = meta.get("content_type")
    if isinstance(content_type, list):
        content_type = next((item for item in content_type if isinstance(item, str) and item), None)
    if isinstance(content_type, str) and content_type.strip():
        return content_type.strip().split(";")[0].lower()
    guessed, _ = mimetypes.guess_type(file.filename or file.path or "")
    return guessed.lower() if guessed else None


def _sniff_image_mime(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


async def resolve_query_image_ref_for_embedding(ref: str) -> ResolvedQueryImageInput:
    if not ref.startswith("chat:file:"):
        raise MultimodalVectorSpaceError(
            "unsupported_image_query",
            "Only chat:file image query refs are supported by the default evidence search adapter",
            details={"ref": ref},
        )

    file_id = ref.removeprefix("chat:file:").strip()
    if not file_id:
        raise MultimodalVectorSpaceError(
            "unsupported_image_query",
            "query image ref is missing a file id",
            details={"ref": ref},
        )

    file = await Files.get_file_by_id(file_id)
    if file is None:
        raise MultimodalVectorSpaceError(
            "unsupported_image_query",
            "query image file was not found",
            details={"ref": ref, "file_id": file_id},
        )
    if not file.path:
        raise MultimodalVectorSpaceError(
            "unsupported_image_query",
            "query image file has no storage path",
            details={"ref": ref, "file_id": file_id},
        )

    metadata_mime = _extract_file_content_type(file)
    if metadata_mime and not metadata_mime.startswith("image/"):
        raise MultimodalVectorSpaceError(
            "unsupported_image_query",
            "query image ref does not point to an image file",
            details={"ref": ref, "file_id": file_id, "mime_type": metadata_mime},
        )

    file_path = await asyncio.to_thread(Storage.get_file, file.path)
    image_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
    sniffed_mime = _sniff_image_mime(image_bytes)
    mime_type = metadata_mime or sniffed_mime
    if not mime_type or not mime_type.startswith("image/"):
        raise MultimodalVectorSpaceError(
            "unsupported_image_query",
            "query image bytes could not be validated as an image",
            details={"ref": ref, "file_id": file_id},
        )

    return ResolvedQueryImageInput(
        ref=ref,
        file_id=file_id,
        mime_type=mime_type,
        image_bytes=image_bytes,
        filename=file.filename,
    )


async def resolve_query_images_for_embedding(
    query_image_refs: Sequence[str] | None,
    *,
    request: Any | None = None,
) -> list[ResolvedQueryImageInput | Mapping[str, Any]]:
    refs = [str(ref).strip() for ref in query_image_refs or [] if str(ref).strip()]
    if not refs:
        return []

    resolver = None
    if request is not None:
        state = getattr(getattr(request, "app", None), "state", None)
        resolver = getattr(state, "EVIDENCE_QUERY_IMAGE_RESOLVER", None) if state is not None else None
    if callable(resolver):
        resolved = await _await_maybe(resolver(refs=refs, request=request))
        if not isinstance(resolved, Sequence) or isinstance(resolved, (str, bytes)):
            raise MultimodalVectorSpaceError(
                "unsupported_image_query",
                "custom query image resolver returned an invalid payload",
            )
        return list(resolved)

    resolved_images = []
    for ref in refs:
        resolved_images.append(await resolve_query_image_ref_for_embedding(ref))
    return resolved_images


def build_multimodal_evidence_descriptor(
    *,
    evidence: KnowledgeEvidenceModel,
    vector_space: KnowledgeVectorSpaceModel,
    asset: KnowledgeEvidenceAssetModel | None = None,
) -> NormalizedMultimodalEvidenceInput:
    return normalize_multimodal_evidence_input(
        {
            "modality": evidence.modality,
            "knowledge_id": evidence.knowledge_id,
            "file_id": evidence.file_id,
            "evidence_ref": evidence.evidence_ref,
            "evidence_kind": evidence.evidence_kind,
            "source_name": evidence.source_name,
            "content_hash": evidence.content_hash,
            "projection_config_hash": vector_space.projection_config_hash,
            "projection_profile": vector_space.retrieval_profile,
            "chunk_index": evidence.chunk_index,
            "chunk_total": evidence.chunk_total,
            "text": evidence.content_text,
            "preview_text": evidence.preview_text,
            "title": evidence.title,
            "asset_ref": asset.asset_ref if asset else None,
            "retrieval_chunk_uid": evidence.retrieval_chunk_uid,
            "retrieval_chunk_row_id": evidence.retrieval_chunk_row_id,
            "page_index": evidence.page_index,
            "vector_space_id": vector_space.id,
            "retrieval_profile": vector_space.retrieval_profile,
        }
    )


async def build_multimodal_evidence_embedding_input(
    *,
    evidence: KnowledgeEvidenceModel,
    asset: KnowledgeEvidenceAssetModel | None = None,
) -> str | dict[str, Any]:
    if evidence.modality != "image":
        return evidence.content_text or evidence.preview_text or evidence.title or evidence.source_name

    if asset is None and evidence.asset_id:
        asset = await KnowledgeEvidenceAssets.get_asset_by_id(evidence.asset_id)
    if asset is None:
        raise ValueError(f"image evidence {evidence.evidence_ref!r} is missing an asset row")

    file_path = await asyncio.to_thread(Storage.get_file, asset.storage_uri)
    image_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
    return {
        "modality": evidence.modality,
        "evidence_ref": evidence.evidence_ref,
        "knowledge_id": evidence.knowledge_id,
        "file_id": evidence.file_id,
        "asset_ref": asset.asset_ref,
        "content_text": evidence.content_text,
        "preview_text": evidence.preview_text,
        "title": evidence.title,
        "source_name": evidence.source_name,
        "image_bytes": image_bytes,
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
        "page_index": asset.page_index,
    }


async def _await_maybe(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _normalize_embedding_vector(value: Any) -> list[float | int]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
        if len(value) == 1:
            value = list(value[0])
    if not isinstance(value, list):
        raise ValueError(f"Unexpected embedding return type: {type(value)!r}")
    return [float(item) if isinstance(item, float) else item for item in value]


def _normalize_search_rows(vector_result: Any) -> list[dict[str, Any]]:
    if not vector_result:
        return []

    ids = getattr(vector_result, "ids", None) or []
    metadatas = getattr(vector_result, "metadatas", None) or []
    distances = getattr(vector_result, "distances", None) or []

    rows: list[dict[str, Any]] = []
    if not metadatas:
        return rows

    result_metadatas = metadatas[0] if metadatas else []
    result_ids = ids[0] if ids else []
    result_distances = distances[0] if distances else []
    for index, metadata in enumerate(result_metadatas):
        if not isinstance(metadata, Mapping):
            continue
        evidence_ref = str(metadata.get("evidence_ref") or "").strip()
        if not evidence_ref:
            continue
        distance = result_distances[index] if index < len(result_distances) else None
        score = None
        if isinstance(distance, (int, float)):
            # Vector backends normalize SearchResult.distances into larger-is-better similarity scores.
            score = float(distance)
        elif isinstance(metadata.get("score"), (int, float)):
            score = float(metadata["score"])
        rows.append(
            {
                "evidence_ref": evidence_ref,
                "score": score,
                "distance": distance,
                "vector_backend_id": result_ids[index] if index < len(result_ids) else metadata.get("vector_backend_id"),
                "vector_space_id": metadata.get("vector_space_id"),
                "modality": metadata.get("modality"),
                "evidence_kind": metadata.get("evidence_kind"),
            }
        )
    return rows


def _dedupe_search_hits(hits: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_ref: dict[str, dict[str, Any]] = {}
    ordered_refs: list[str] = []
    for hit in hits:
        evidence_ref = str(hit.get("evidence_ref") or "").strip()
        if not evidence_ref:
            continue
        score = hit.get("score")
        if evidence_ref not in best_by_ref:
            best_by_ref[evidence_ref] = dict(hit)
            ordered_refs.append(evidence_ref)
            continue
        current_score = best_by_ref[evidence_ref].get("score")
        if isinstance(score, (int, float)) and not isinstance(current_score, (int, float)):
            best_by_ref[evidence_ref] = dict(hit)
        elif isinstance(score, (int, float)) and isinstance(current_score, (int, float)) and score > current_score:
            best_by_ref[evidence_ref] = dict(hit)
    return [best_by_ref[evidence_ref] for evidence_ref in ordered_refs if evidence_ref in best_by_ref]


def _supports_evidence_modality(vector_space: Any, modality: str) -> bool:
    return bool(getattr(vector_space, f"supports_{modality}_evidence", False))


@dataclass(frozen=True)
class _SearchBranch:
    name: str
    modality: str
    query_text: str | None = None
    uses_query_images: bool = False
    fusion_weight: float = 1.0


_RRF_K = 40
_BRANCH_CANDIDATE_MULTIPLIER = 3
_MIN_BRANCH_CANDIDATE_LIMIT = 12
_MAX_BRANCH_CANDIDATE_LIMIT = 48


def _resolve_search_branches(
    *,
    requested_modalities: Sequence[str],
    query_text: str | None,
    visual_query: str | None,
    query_has_images: bool,
    vector_space: Any,
) -> list[_SearchBranch]:
    normalized_modalities = [modality for modality in requested_modalities if modality in _MODALITIES]
    branches: list[_SearchBranch] = []

    def add_text_branch(*, weight: float = 1.0) -> None:
        if query_text and _supports_evidence_modality(vector_space, "text"):
            branches.append(
                _SearchBranch(
                    name="text_dense",
                    modality="text",
                    query_text=query_text,
                    fusion_weight=weight,
                )
            )

    def add_image_branch(*, image_query_text: str | None, uses_query_images: bool, weight: float = 1.0) -> None:
        if not _supports_evidence_modality(vector_space, "image"):
            return
        if not image_query_text and not uses_query_images:
            return
        branches.append(
            _SearchBranch(
                name="image_dense",
                modality="image",
                query_text=image_query_text,
                uses_query_images=uses_query_images,
                fusion_weight=weight,
            )
        )

    if normalized_modalities:
        requested_set = set(normalized_modalities)
        image_only_text_query = requested_set == {"image"} and bool(query_text) and not visual_query and not query_has_images
        for modality in normalized_modalities:
            if modality == "text":
                add_text_branch()
            elif modality == "image":
                add_image_branch(
                    image_query_text=visual_query or (query_text if image_only_text_query else None),
                    uses_query_images=query_has_images,
                )
        return branches

    if query_has_images:
        add_image_branch(image_query_text=visual_query, uses_query_images=True)
        add_text_branch(weight=0.5)
        return branches

    add_text_branch()
    add_image_branch(image_query_text=visual_query, uses_query_images=False)
    return branches


def _resolve_branch_candidate_limit(search_limit: int) -> int:
    widened_limit = min(
        _MAX_BRANCH_CANDIDATE_LIMIT,
        max(search_limit * _BRANCH_CANDIDATE_MULTIPLIER, _MIN_BRANCH_CANDIDATE_LIMIT),
    )
    return max(search_limit, widened_limit)


def _rrf_fuse_branch_hits(
    branch_hits: Mapping[str, Sequence[dict[str, Any]]],
    *,
    limit: int,
    rrf_k: int = _RRF_K,
    branch_weights: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    if not branch_hits or limit <= 0:
        return []

    fused_by_ref: dict[str, dict[str, Any]] = {}
    for branch_name, hits in branch_hits.items():
        branch_weight = float((branch_weights or {}).get(branch_name, 1.0) or 1.0)
        for rank, hit in enumerate(hits, start=1):
            evidence_ref = str(hit.get("evidence_ref") or "").strip()
            if not evidence_ref:
                continue
            fused_hit = fused_by_ref.setdefault(evidence_ref, dict(hit))
            branch_ranks = dict(fused_hit.get("branch_ranks") or {})
            branch_ranks[branch_name] = rank
            fused_hit["branch_ranks"] = branch_ranks

            branch_scores = dict(fused_hit.get("branch_scores") or {})
            if isinstance(hit.get("score"), (int, float)):
                branch_scores[branch_name] = float(hit["score"])
            if branch_scores:
                fused_hit["branch_scores"] = branch_scores

            fused_hit["fusion_score"] = float(fused_hit.get("fusion_score") or 0.0) + (
                branch_weight / float(rrf_k + rank)
            )
            if branch_weight != 1.0:
                branch_weight_map = dict(fused_hit.get("branch_weights") or {})
                branch_weight_map[branch_name] = branch_weight
                fused_hit["branch_weights"] = branch_weight_map

            current_score = fused_hit.get("score")
            new_score = hit.get("score")
            if isinstance(new_score, (int, float)) and (
                not isinstance(current_score, (int, float)) or float(new_score) > float(current_score)
            ):
                for key, value in hit.items():
                    fused_hit[key] = value
                fused_hit["branch_ranks"] = branch_ranks
                if branch_scores:
                    fused_hit["branch_scores"] = branch_scores
                fused_hit["fusion_score"] = float(
                    sum(
                        float((branch_weights or {}).get(branch, 1.0) or 1.0) / float(rrf_k + branch_rank)
                        for branch, branch_rank in branch_ranks.items()
                    )
                )

    return sorted(
        fused_by_ref.values(),
        key=lambda item: (
            -(item.get("fusion_score") if isinstance(item.get("fusion_score"), (int, float)) else -1e9),
            -(item.get("score") if isinstance(item.get("score"), (int, float)) else -1e9),
            str(item.get("evidence_ref") or ""),
        ),
    )[:limit]


def _coerce_branch_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _normalize_branch_search_hits(
    hits: Any,
    *,
    branch: _SearchBranch,
    source: str,
) -> list[dict[str, Any]]:
    if hits is None:
        return []
    if not isinstance(hits, Sequence) or isinstance(hits, (str, bytes)):
        raise MultimodalVectorSpaceError(
            "invalid_lexical_branch_payload",
            f"{source} lexical branch {branch.name} returned an invalid payload",
            details={"branch": branch.name, "source": source},
        )

    normalized_hits: list[dict[str, Any]] = []
    unresolved_refs = 0
    for hit in hits:
        metadata: Mapping[str, Any] = {}
        evidence_ref = ""
        modality = branch.modality
        score = None

        if isinstance(hit, Mapping):
            metadata = hit
            evidence_ref = str(hit.get("evidence_ref") or "").strip()
            modality = str(hit.get("modality") or branch.modality).strip().lower() or branch.modality
            score = _coerce_branch_score(hit.get("score"))
        else:
            raw_metadata = getattr(hit, "metadata", None)
            if isinstance(raw_metadata, Mapping):
                metadata = raw_metadata
            raw_chunk_uid = str(getattr(hit, "chunk_uid", "") or "").strip()
            evidence_ref = str(metadata.get("evidence_ref") or "").strip()
            if not evidence_ref and raw_chunk_uid.startswith("ke:"):
                evidence_ref = raw_chunk_uid
            modality = str(
                metadata.get("modality")
                or getattr(hit, "modality", None)
                or branch.modality
            ).strip().lower() or branch.modality
            score = _coerce_branch_score(getattr(hit, "score", None))

        if not evidence_ref:
            unresolved_refs += 1
            continue
        if modality != branch.modality:
            continue

        normalized_hit = dict(metadata)
        normalized_hit["evidence_ref"] = evidence_ref
        normalized_hit["modality"] = modality
        if score is not None:
            normalized_hit["score"] = score
        normalized_hits.append(normalized_hit)

    if unresolved_refs:
        raise MultimodalVectorSpaceError(
            "invalid_lexical_branch_payload",
            f"{source} lexical branch {branch.name} returned hit(s) without evidence_ref mapping",
            details={"branch": branch.name, "source": source, "unresolved_hits": unresolved_refs},
        )

    return normalized_hits


async def _search_lexical_branch(
    *,
    branch: _SearchBranch,
    query_text: str | None,
    vector_space: KnowledgeVectorSpaceModel,
    candidate_limit: int,
    request: Any | None,
    user: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if not query_text or not query_text.strip():
        return []

    request_state = getattr(getattr(request, "app", None), "state", None) if request is not None else None
    lexical_search = (
        getattr(request_state, "EVIDENCE_RETRIEVAL_LEXICAL_SEARCH", None) if request_state is not None else None
    )
    lexical_client = (
        getattr(request_state, "EVIDENCE_RETRIEVAL_LEXICAL_CLIENT", None) if request_state is not None else None
    )

    if callable(lexical_search):
        hits = await _await_maybe(
            lexical_search(
                query_text=query_text.strip(),
                branch={"name": branch.name, "modality": branch.modality},
                vector_space=vector_space,
                limit=candidate_limit,
                request=request,
                user=user,
            )
        )
        return _normalize_branch_search_hits(hits, branch=branch, source="hook")

    if lexical_client is None or not hasattr(lexical_client, "search"):
        return []

    hits = await asyncio.to_thread(
        lexical_client.search,
        query_text.strip(),
        collection_ids=[f"{vector_space.knowledge_id}:{vector_space.id}"],
        knowledge_ids=[vector_space.knowledge_id],
        k=candidate_limit,
    )
    return _normalize_branch_search_hits(hits, branch=branch, source="client")


async def _build_rerank_text(evidence: KnowledgeEvidenceModel) -> str:
    if evidence.modality != "image":
        for candidate in (evidence.content_text, evidence.preview_text, evidence.title, evidence.source_name):
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        return ""

    for candidate in (evidence.content_text, evidence.preview_text):
        if candidate and str(candidate).strip():
            return str(candidate).strip()

    parts: list[str] = []
    seen: set[str] = set()

    def add_part(value: Any, *, prefix: str | None = None) -> None:
        text = str(value or "").strip()
        if not text:
            return
        dedupe_key = f"{prefix}:{text}" if prefix else text
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        parts.append(f"{prefix}: {text}" if prefix else text)

    if evidence.asset_id:
        asset = await KnowledgeEvidenceAssets.get_asset_by_id(evidence.asset_id)
        if asset is not None:
            add_part(asset.caption)
            add_part(asset.surrounding_text, prefix="Context")
            add_part(asset.ocr_text, prefix="OCR")

    add_part(evidence.title)
    add_part(evidence.source_name)
    return "\n".join(parts)


async def _call_reranking_function(reranking_function: Any, query_text: str, documents: list[Document], user: Any) -> Any:
    try:
        return await asyncio.to_thread(reranking_function, query_text, documents, user)
    except TypeError:
        return await asyncio.to_thread(reranking_function, query_text, documents)


async def _rerank_search_hits(
    hits: Sequence[dict[str, Any]],
    *,
    query_text: str | None,
    reranking_function: Any,
    rerank_top_n: int,
    user: Any = None,
) -> list[dict[str, Any]]:
    if not hits or not query_text or not query_text.strip():
        return list(hits)

    rerank_limit = min(len(hits), max(1, int(rerank_top_n or len(hits))))
    rerank_window = [dict(hit) for hit in hits[:rerank_limit]]
    remaining_hits = [dict(hit) for hit in hits[rerank_limit:]]

    documents: list[Document] = []
    rerankable_hits: list[dict[str, Any]] = []
    for hit in rerank_window:
        evidence_ref = str(hit.get("evidence_ref") or "").strip()
        if not evidence_ref:
            continue
        evidence = await KnowledgeEvidences.get_evidence_by_ref(evidence_ref)
        if evidence is None or not evidence.is_active:
            continue
        rerank_text = await _build_rerank_text(evidence)
        if not rerank_text:
            continue
        documents.append(
            Document(
                page_content=rerank_text,
                metadata={"evidence_ref": evidence_ref},
            )
        )
        rerankable_hits.append(dict(hit))

    if len(documents) < 2:
        return list(hits)

    scores = await _call_reranking_function(reranking_function, query_text.strip(), documents, user)
    if scores is None:
        return list(hits)
    if hasattr(scores, "tolist"):
        scores = scores.tolist()
    if not isinstance(scores, list) or len(scores) != len(rerankable_hits):
        return list(hits)

    scored_hits: list[dict[str, Any]] = []
    for hit, score in zip(rerankable_hits, scores):
        if not isinstance(score, (int, float)):
            return list(hits)
        reranked_hit = dict(hit)
        reranked_hit["score"] = float(score)
        scored_hits.append(reranked_hit)

    scored_hits.sort(
        key=lambda item: (
            -(item.get("score") if isinstance(item.get("score"), (int, float)) else -1e9),
            str(item.get("evidence_ref") or ""),
        )
    )
    reranked_refs = {str(hit.get("evidence_ref") or "") for hit in scored_hits}
    non_reranked_window_hits = [
        dict(hit) for hit in rerank_window if str(hit.get("evidence_ref") or "") not in reranked_refs
    ]
    return scored_hits + non_reranked_window_hits + remaining_hits


async def search_multimodal_evidence(
    *,
    query,
    vector_spaces: Sequence[KnowledgeVectorSpaceModel],
    embedding_function: Any | None = None,
    vector_client: Any | None = None,
    user: Mapping[str, Any] | None = None,
    request: Any | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if not vector_spaces:
        return []

    if embedding_function is None and request is not None:
        embedding_function = getattr(getattr(request, "app", None), "state", None)
        embedding_function = getattr(embedding_function, "EVIDENCE_RETRIEVAL_EMBEDDING", None) or getattr(
            embedding_function, "EMBEDDING_FUNCTION", None
        )
    if vector_client is None and request is not None:
        vector_client = getattr(getattr(request, "app", None), "state", None)
        vector_client = getattr(vector_client, "EVIDENCE_RETRIEVAL_VECTOR_CLIENT", None)
    if vector_client is None:
        from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

        vector_client = ASYNC_VECTOR_DB_CLIENT
    if embedding_function is None:
        raise ValueError("No evidence embedding function is configured")

    request_state = getattr(getattr(request, "app", None), "state", None) if request is not None else None
    request_config = getattr(request_state, "config", None) if request_state is not None else None
    reranking_function = getattr(request_state, "RERANKING_FUNCTION", None) if request_state is not None else None
    search_limit = max(1, int(limit or getattr(query, "top_k", 8) or 8))
    candidate_limit = _resolve_branch_candidate_limit(search_limit)
    requested_modalities = [
        modality
        for modality in getattr(query, "modalities", []) or []
        if isinstance(modality, str) and modality in {"text", "image"}
    ]
    branch_hits: dict[str, list[dict[str, Any]]] = {}
    branch_weights: dict[str, float] = {}
    query_image_refs = getattr(query, "query_image_refs", None)
    query_has_images = bool(query_image_refs)
    query_text = getattr(query, "query_text", None)
    visual_query = getattr(query, "visual_query", None)
    query_images = (
        await resolve_query_images_for_embedding(query_image_refs, request=request) if query_has_images else []
    )

    for vector_space in vector_spaces:
        if query_has_images and not vector_space.supports_image_query:
            raise MultimodalVectorSpaceError(
                "unsupported_image_query",
                "Vector space does not support image queries",
                details={
                    "knowledge_id": vector_space.knowledge_id,
                    "vector_space_id": vector_space.id,
                    "retrieval_profile": vector_space.retrieval_profile,
                },
            )
        if (query_text or visual_query) and not vector_space.supports_text_query:
            raise MultimodalVectorSpaceError(
                "unsupported_text_query",
                "Vector space does not support text queries",
                details={
                    "knowledge_id": vector_space.knowledge_id,
                    "vector_space_id": vector_space.id,
                    "retrieval_profile": vector_space.retrieval_profile,
                },
            )

        for branch in _resolve_search_branches(
            requested_modalities=requested_modalities,
            query_text=query_text,
            visual_query=visual_query,
            query_has_images=query_has_images,
            vector_space=vector_space,
        ):
            branch_query_images = query_images if branch.uses_query_images else []
            query_input = build_multimodal_query_embedding_input(
                query_text=branch.query_text,
                query_images=branch_query_images,
                vector_space=vector_space,
            )
            query_vector = _normalize_embedding_vector(
                await _await_maybe(embedding_function(query_input, prefix=RAG_EMBEDDING_QUERY_PREFIX, user=user))
            )
            branch_weights[branch.name] = max(branch_weights.get(branch.name, 0.0), branch.fusion_weight)
            vector_result = await vector_client.search(
                collection_name=f"{vector_space.knowledge_id}:{vector_space.id}",
                vectors=[query_vector],
                filter={"modality": {"$in": [branch.modality]}},
                limit=candidate_limit,
            )
            branch_hits.setdefault(branch.name, []).extend(_normalize_search_rows(vector_result))
            lexical_branch = _SearchBranch(name=f"{branch.modality}_lexical", modality=branch.modality)
            lexical_hits = await _search_lexical_branch(
                branch=lexical_branch,
                query_text=branch.query_text,
                vector_space=vector_space,
                candidate_limit=candidate_limit,
                request=request,
                user=user,
            )
            if lexical_hits:
                branch_weights[lexical_branch.name] = max(branch_weights.get(lexical_branch.name, 0.0), branch.fusion_weight)
                branch_hits.setdefault(lexical_branch.name, []).extend(lexical_hits)

    deduped_branch_hits = {
        modality: _dedupe_search_hits(hits)
        for modality, hits in branch_hits.items()
        if hits
    }
    deduped_hits = _rrf_fuse_branch_hits(
        deduped_branch_hits,
        limit=candidate_limit,
        branch_weights=branch_weights,
    )
    if getattr(query, "rerank", True) and reranking_function is not None and query_text:
        rerank_top_n = getattr(request_config, "TOP_K_RERANKER", search_limit) if request_config is not None else search_limit
        deduped_hits = await _rerank_search_hits(
            deduped_hits,
            query_text=query_text,
            reranking_function=reranking_function,
            rerank_top_n=rerank_top_n,
            user=user,
        )
    return deduped_hits[:search_limit]


async def upsert_multimodal_evidence_embedding(
    *,
    evidence: KnowledgeEvidenceModel,
    vector_space: KnowledgeVectorSpaceModel,
    embedding_function: Any,
    vector_client: Any,
    db: AsyncSession | None = None,
) -> MultimodalEvidenceEmbeddingWriteResult:
    asset = None
    if evidence.asset_id:
        asset = await KnowledgeEvidenceAssets.get_asset_by_id(evidence.asset_id, db=db)
    descriptor = build_multimodal_evidence_descriptor(evidence=evidence, vector_space=vector_space, asset=asset)
    collection_name = f"{vector_space.knowledge_id}:{vector_space.id}"
    selection = MultimodalVectorSpaceSelection(vector_space=vector_space, collection_name=collection_name)
    try:
        embedding_input = await build_multimodal_evidence_embedding_input(evidence=evidence, asset=asset)
        vector = _normalize_embedding_vector(
            await _await_maybe(embedding_function(embedding_input, prefix=RAG_EMBEDDING_CONTENT_PREFIX, user=None))
        )
        vector_item = build_multimodal_vector_item(
            vector=vector,
            descriptor=descriptor,
            selection=selection,
        )
        await vector_client.upsert(collection_name, [vector_item])
        embedding = await KnowledgeEvidenceEmbeddings.create_embedding(
            evidence_id=evidence.id,
            evidence_ref=evidence.evidence_ref,
            vector_space_id=vector_space.id,
            vector_backend_collection=collection_name,
            vector_backend_id=vector_item.id,
            vector_role=vector_item.metadata["vector_role"],
            embedding_format=vector_item.metadata["embedding_format"],
            embedding_status="ready",
            db=db,
        )
        return MultimodalEvidenceEmbeddingWriteResult(embedding=embedding, vector_item=vector_item)
    except Exception as exc:
        sanitized_error = sanitize_embedding_error(exc)
        embedding = await KnowledgeEvidenceEmbeddings.create_embedding(
            evidence_id=evidence.id,
            evidence_ref=evidence.evidence_ref,
            vector_space_id=vector_space.id,
            vector_backend_collection=collection_name,
            vector_backend_id=None,
            vector_role=get_multimodal_vector_role(evidence.modality),
            embedding_format="single_dense",
            embedding_status="failed",
            embedding_error=sanitized_error,
            db=db,
        )
        return MultimodalEvidenceEmbeddingWriteResult(embedding=embedding, vector_item=None, error=sanitized_error)
