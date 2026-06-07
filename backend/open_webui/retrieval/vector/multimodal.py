from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.evidence import KnowledgeVectorSpaceModel, KnowledgeVectorSpaces, compute_knowledge_evidence_embedding_id
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


@dataclass(slots=True)
class MultimodalVectorSpaceSelection:
    vector_space: KnowledgeVectorSpaceModel
    collection_name: str


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
