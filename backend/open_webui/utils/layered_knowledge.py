import asyncio
import logging
import re
from types import SimpleNamespace
from typing import Optional

import tiktoken
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.knowledge_layers import (
    LAYER_TYPE_ALIASES,
    LAYER_TYPES,
    KnowledgeFileLayerModel,
    KnowledgeFileLayerQueryRow,
    KnowledgeFileLayerUpsertForm,
    KnowledgeLayers,
)
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.knowledge_layer_embeddings import (
    delete_layer_embeddings_by_file_id,
    delete_layer_embeddings_by_knowledge_id,
    sync_file_layer_embeddings,
)
from open_webui.utils.misc import calculate_sha256_string
from open_webui.utils.task import get_task_model_id

log = logging.getLogger(__name__)

ACTIVE_LAYER_TYPES = ("abstract",)
DEFAULT_MAX_CHUNK_TOKENS = 24000
DEFAULT_MIN_TAIL_TOKENS = 1000
DEFAULT_LAYER_GENERATION_PROMPT_ABSTRACT = """### Task:
Generate a concise abstract for the provided document chunk.

### Guidelines:
- Summarize only the provided text.
- Focus on the core subject, scope, and major conclusions.
- Keep the answer compact and readable.
- Do not invent facts that are not present in the text.

### Document Chunk:
{{DOCUMENT_TEXT}}
"""


def _normalize_runtime_layer_type(layer_type: str) -> Optional[str]:
    normalized = (layer_type or "").strip().lower()
    normalized = LAYER_TYPE_ALIASES.get(normalized, normalized)
    return normalized if normalized in LAYER_TYPES else None


def _extract_file_text(file_obj) -> str:
    data = getattr(file_obj, "data", None)
    if not isinstance(data, dict):
        return ""
    content = data.get("content")
    return content if isinstance(content, str) else ""


def _current_file_content_hash(file_obj) -> str:
    file_hash = getattr(file_obj, "hash", None)
    if isinstance(file_hash, str) and file_hash.strip():
        return file_hash
    return calculate_sha256_string(_extract_file_text(file_obj) or getattr(file_obj, "id", ""))


def _get_tiktoken_encoding():
    return tiktoken.get_encoding("cl100k_base")


def estimate_text_tokens(text: str) -> int:
    return len(_get_tiktoken_encoding().encode(text or ""))


def _split_text_by_token_limit(text: str, max_tokens: int) -> list[dict]:
    encoding = _get_tiktoken_encoding()
    tokens = encoding.encode(text or "")
    chunks: list[dict] = []
    for start in range(0, len(tokens), max_tokens):
        token_slice = tokens[start : start + max_tokens]
        if not token_slice:
            continue
        content = encoding.decode(token_slice).strip()
        if not content:
            continue
        chunks.append({"content": content, "token_count": len(token_slice)})
    return chunks


def plan_text_chunks(
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    min_tail_tokens: int = DEFAULT_MIN_TAIL_TOKENS,
) -> list[dict]:
    normalized_text = (text or "").strip()
    if not normalized_text:
        return []

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n+", normalized_text)
        if paragraph and paragraph.strip()
    ]
    if not paragraphs:
        paragraphs = [normalized_text]

    chunk_rows: list[dict] = []
    current_content = ""
    current_tokens = 0

    def flush_current():
        nonlocal current_content, current_tokens
        if not current_content:
            return
        chunk_rows.append(
            {
                "content": current_content,
                "token_count": current_tokens or estimate_text_tokens(current_content),
            }
        )
        current_content = ""
        current_tokens = 0

    for paragraph in paragraphs:
        paragraph_tokens = estimate_text_tokens(paragraph)
        if paragraph_tokens > max_tokens:
            flush_current()
            chunk_rows.extend(_split_text_by_token_limit(paragraph, max_tokens))
            continue

        if not current_content:
            current_content = paragraph
            current_tokens = paragraph_tokens
            continue

        candidate = f"{current_content}\n\n{paragraph}"
        candidate_tokens = estimate_text_tokens(candidate)
        if candidate_tokens <= max_tokens:
            current_content = candidate
            current_tokens = candidate_tokens
            continue

        flush_current()
        current_content = paragraph
        current_tokens = paragraph_tokens

    flush_current()

    if len(chunk_rows) >= 2 and chunk_rows[-1]["token_count"] < min_tail_tokens:
        merged = f'{chunk_rows[-2]["content"]}\n\n{chunk_rows[-1]["content"]}'
        merged_tokens = estimate_text_tokens(merged)
        if merged_tokens <= max_tokens:
            chunk_rows = [
                *chunk_rows[:-2],
                {"content": merged, "token_count": merged_tokens},
            ]

    for index, row in enumerate(chunk_rows, start=1):
        row["part_index"] = index
        row["part_total"] = len(chunk_rows)
    return chunk_rows


def _layer_generation_chunk_limits(request) -> tuple[int, int]:
    config = request.app.state.config
    max_tokens = getattr(config, "LAYER_GENERATION_MAX_CHUNK_TOKENS", DEFAULT_MAX_CHUNK_TOKENS)
    min_tail_tokens = getattr(config, "LAYER_GENERATION_MIN_TAIL_TOKENS", DEFAULT_MIN_TAIL_TOKENS)
    return max(int(max_tokens or DEFAULT_MAX_CHUNK_TOKENS), 1000), max(
        int(min_tail_tokens or DEFAULT_MIN_TAIL_TOKENS), 100
    )


def _get_layer_generation_model_id(request, layer_type: str) -> Optional[str]:
    models = request.app.state.MODELS
    config = request.app.state.config

    explicit_model_id = (getattr(config, "LAYER_GENERATION_MODEL", "") or "").strip()
    if explicit_model_id and explicit_model_id in models:
        return explicit_model_id

    default_models = (getattr(config, "DEFAULT_MODELS", "") or "").split(",")
    default_model_id = next(
        (
            candidate.strip()
            for candidate in default_models
            if candidate.strip() in models and models[candidate.strip()].get("owned_by") != "arena"
        ),
        None,
    )
    if not default_model_id:
        non_arena_model_ids = sorted(
            model_id for model_id, model in models.items() if model.get("owned_by") != "arena"
        )
        default_model_id = non_arena_model_ids[0] if non_arena_model_ids else None
    if not default_model_id:
        return None

    return get_task_model_id(
        default_model_id=default_model_id,
        task_model=(getattr(config, "TASK_MODEL", "") or "").strip(),
        task_model_external=(getattr(config, "TASK_MODEL_EXTERNAL", "") or "").strip(),
        models=models,
    )


def _get_layer_generation_prompt(request, layer_type: str) -> str:
    configured_prompt = (getattr(request.app.state.config, "LAYER_GENERATION_PROMPT_ABSTRACT", "") or "").strip()
    if configured_prompt:
        return configured_prompt
    return DEFAULT_LAYER_GENERATION_PROMPT_ABSTRACT


def _render_layer_generation_prompt(
    template: str,
    *,
    layer_type: str,
    document_text: str,
    part_index: int,
    part_total: int,
) -> str:
    return (
        (template or DEFAULT_LAYER_GENERATION_PROMPT_ABSTRACT)
        .replace("{{DOCUMENT_TEXT}}", document_text)
        .replace("{{LAYER_TYPE}}", layer_type)
        .replace("{{PART_INDEX}}", str(part_index))
        .replace("{{PART_TOTAL}}", str(part_total))
    )


def _extract_chat_completion_content(response) -> str:
    data = response
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return ""

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        if isinstance(message, dict):
            content = message.get("content") or message.get("reasoning_content")
            if isinstance(content, str):
                return content.strip()

    output = data.get("output")
    if isinstance(output, list):
        text_blocks: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content", []) or []:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_blocks.append(text.strip())
        return "\n".join(text_blocks).strip()

    return ""


def _get_layer_generation_user():
    return SimpleNamespace(
        id="layer-generation",
        email="layer-generation@openwebui.local",
        role="admin",
    )


async def _generate_layer_content_async(
    request,
    *,
    layer_type: str,
    document_text: str,
    part_index: int,
    part_total: int,
) -> tuple[str, str]:
    model_id = _get_layer_generation_model_id(request, layer_type)
    if not model_id:
        raise RuntimeError(f"Layer generation model is not configured for {layer_type}")

    prompt = _render_layer_generation_prompt(
        _get_layer_generation_prompt(request, layer_type),
        layer_type=layer_type,
        document_text=document_text,
        part_index=part_index,
        part_total=part_total,
    )
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    response = await generate_chat_completion(
        request,
        form_data=payload,
        user=_get_layer_generation_user(),
        bypass_filter=True,
    )
    content = _extract_chat_completion_content(response)
    if not content:
        raise RuntimeError(f"Layer generation returned empty content for {layer_type}")
    return content, model_id


def _layer_display_title(layer_type: str, part_index: int, part_total: int) -> Optional[str]:
    if part_total <= 1:
        return None
    label = "Abstract" if layer_type == "abstract" else layer_type.replace("_", " ").title()
    return f"{label} {part_index}/{part_total}"


async def _upsert_status(
    knowledge_id: str,
    file_id: str,
    layer_type: str,
    *,
    status: str,
    content: Optional[str] = None,
    source_system: str = "open_webui",
    source_ref_id: Optional[str] = None,
    transformation_ref_id: Optional[str] = None,
    content_hash: Optional[str] = None,
    part_index: int = 1,
    part_total: int = 1,
    display_title: Optional[str] = None,
    db: Optional[AsyncSession] = None,
) -> KnowledgeFileLayerModel:
    return await KnowledgeLayers.upsert_layer(
        KnowledgeFileLayerUpsertForm(
            knowledge_id=knowledge_id,
            file_id=file_id,
            layer_type=layer_type,
            status=status,
            content=content,
            source_system=source_system,
            source_ref_id=source_ref_id,
            transformation_ref_id=transformation_ref_id,
            content_hash=content_hash,
            part_index=part_index,
            part_total=part_total,
            display_title=display_title,
        ),
        db=db,
    )


async def _sync_selected_layers_for_file_async(
    request,
    knowledge_id: str,
    file_id: str,
    *,
    selected_layer_types: list[str],
    db: Optional[AsyncSession] = None,
) -> list[KnowledgeFileLayerModel]:
    file_obj = await Files.get_file_by_id(file_id, db=db)
    if not file_obj:
        return await KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    file_text = _extract_file_text(file_obj)
    current_content_hash = _current_file_content_hash(file_obj)
    if not file_text:
        await KnowledgeLayers.delete_layers_by_file(knowledge_id, file_id, layer_types=selected_layer_types, db=db)
        for layer_type in selected_layer_types:
            await _upsert_status(
                knowledge_id,
                file_id,
                layer_type,
                status="failed",
                content="Layer generation failed: file has no extracted text content available.",
                content_hash=current_content_hash,
                db=db,
            )
        return await KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    max_tokens, min_tail_tokens = _layer_generation_chunk_limits(request)
    chunk_specs = plan_text_chunks(file_text, max_tokens=max_tokens, min_tail_tokens=min_tail_tokens)
    if not chunk_specs:
        await KnowledgeLayers.delete_layers_by_file(knowledge_id, file_id, layer_types=selected_layer_types, db=db)
        return await KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    await KnowledgeLayers.delete_layers_by_file(knowledge_id, file_id, layer_types=selected_layer_types, db=db)
    for layer_type in selected_layer_types:
        for spec in chunk_specs:
            part_index = spec["part_index"]
            part_total = spec["part_total"]
            display_title = _layer_display_title(layer_type, part_index, part_total)
            await _upsert_status(
                knowledge_id,
                file_id,
                layer_type,
                status="pending",
                source_ref_id=f"chunk:{part_index}",
                transformation_ref_id=_get_layer_generation_model_id(request, layer_type),
                content_hash=current_content_hash,
                part_index=part_index,
                part_total=part_total,
                display_title=display_title,
                db=db,
            )
            try:
                content, model_id = await _generate_layer_content_async(
                    request,
                    layer_type=layer_type,
                    document_text=spec.get("content", ""),
                    part_index=part_index,
                    part_total=part_total,
                )
                await _upsert_status(
                    knowledge_id,
                    file_id,
                    layer_type,
                    status="ready",
                    content=content,
                    source_ref_id=f"chunk:{part_index}",
                    transformation_ref_id=model_id,
                    content_hash=current_content_hash,
                    part_index=part_index,
                    part_total=part_total,
                    display_title=display_title,
                    db=db,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _upsert_status(
                    knowledge_id,
                    file_id,
                    layer_type,
                    status="failed",
                    content=str(exc),
                    source_ref_id=f"chunk:{part_index}",
                    transformation_ref_id=_get_layer_generation_model_id(request, layer_type),
                    content_hash=current_content_hash,
                    part_index=part_index,
                    part_total=part_total,
                    display_title=display_title,
                    db=db,
                )

    if getattr(request.app.state, "EMBEDDING_FUNCTION", None):
        try:
            await sync_file_layer_embeddings(request, knowledge_id, file_id, db=db)
        except Exception as exc:
            log.warning(
                "Layer embedding sync failed for knowledge=%s file=%s: %s",
                knowledge_id,
                file_id,
                exc,
            )
    return await KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)


async def sync_layers_for_file_async(
    request, knowledge_id: str, file_id: str, db: Optional[AsyncSession] = None
) -> list[KnowledgeFileLayerModel]:
    return await _sync_selected_layers_for_file_async(
        request,
        knowledge_id,
        file_id,
        selected_layer_types=list(ACTIVE_LAYER_TYPES),
        db=db,
    )


def sync_layers_for_file(request, knowledge_id: str, file_id: str, db: Optional[AsyncSession] = None):
    return asyncio.run(sync_layers_for_file_async(request, knowledge_id, file_id, db=db))


async def regenerate_layers_for_file_async(
    request,
    knowledge_id: str,
    file_id: str,
    *,
    layer_types: Optional[list[str]] = None,
    force: bool = False,
    db: Optional[AsyncSession] = None,
) -> list[KnowledgeFileLayerModel]:
    normalized_layer_types: list[str] = []
    if layer_types:
        for layer_type in layer_types:
            normalized_layer = _normalize_runtime_layer_type(layer_type)
            if not normalized_layer:
                raise ValueError(f"Unsupported layer_type: {layer_type}")
            if normalized_layer not in normalized_layer_types:
                normalized_layer_types.append(normalized_layer)
    else:
        normalized_layer_types = list(ACTIVE_LAYER_TYPES)

    if force or set(normalized_layer_types) == set(ACTIVE_LAYER_TYPES):
        return await _sync_selected_layers_for_file_async(
            request,
            knowledge_id,
            file_id,
            selected_layer_types=list(ACTIVE_LAYER_TYPES),
            db=db,
        )

    for normalized_layer in normalized_layer_types:
        await _sync_selected_layers_for_file_async(
            request,
            knowledge_id,
            file_id,
            selected_layer_types=[normalized_layer],
            db=db,
        )
    return await KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)


def _file_needs_backfill(rows: list[KnowledgeFileLayerModel], layer_types: list[str]) -> bool:
    rows_by_layer: dict[str, list[KnowledgeFileLayerModel]] = {}
    for row in rows:
        rows_by_layer.setdefault(row.layer_type, []).append(row)
    for layer_type in layer_types:
        layer_rows = rows_by_layer.get(layer_type, [])
        if not layer_rows:
            return True
        if any(row.status in {"failed", "stale"} for row in layer_rows):
            return True
        if any(getattr(row, "embedding_status", "ready") != "ready" for row in layer_rows):
            return True
    return False


async def backfill_layers_for_knowledge_async(
    request,
    knowledge_id: str,
    *,
    layer_types: Optional[list[str]] = None,
    force: bool = False,
    db: Optional[AsyncSession] = None,
) -> dict:
    normalized_layer_types: list[str] = []
    if layer_types:
        for layer_type in layer_types:
            normalized_layer = _normalize_runtime_layer_type(layer_type)
            if not normalized_layer:
                raise ValueError(f"Unsupported layer_type: {layer_type}")
            if normalized_layer not in normalized_layer_types:
                normalized_layer_types.append(normalized_layer)
    else:
        normalized_layer_types = list(ACTIVE_LAYER_TYPES)

    files = await Knowledges.get_files_by_id(knowledge_id, db=db) or []
    total_files = len(files)
    scheduled_files = 0
    for file in files:
        rows = await KnowledgeLayers.get_layers_by_file(knowledge_id, file.id, db=db)
        if not force and not _file_needs_backfill(rows, normalized_layer_types):
            continue
        await regenerate_layers_for_file_async(
            request,
            knowledge_id,
            file.id,
            layer_types=normalized_layer_types,
            force=force,
            db=db,
        )
        scheduled_files += 1
    return {
        "total_files": total_files,
        "scheduled_files": scheduled_files,
        "skipped_files": total_files - scheduled_files,
    }


async def mark_layers_for_file_stale(
    knowledge_id: str, file_id: str, db: Optional[AsyncSession] = None
) -> int:
    await delete_layer_embeddings_by_file_id(file_id)
    return await KnowledgeLayers.mark_layers_stale_for_file(knowledge_id, file_id, db=db)


async def mark_layers_for_knowledge_stale(
    knowledge_id: str, db: Optional[AsyncSession] = None
) -> int:
    await delete_layer_embeddings_by_knowledge_id(knowledge_id)
    return await KnowledgeLayers.mark_layers_stale_for_knowledge(knowledge_id, db=db)


def _scope_ids(scope_items: list[dict]) -> tuple[list[str], list[str]]:
    knowledge_ids: list[str] = []
    file_ids: list[str] = []
    for item in scope_items or []:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        item_type = item.get("type")
        if not item_id or not item_type:
            continue
        if item_type == "collection":
            knowledge_ids.append(str(item_id))
        elif item_type == "file":
            file_ids.append(str(item_id))
    return knowledge_ids, file_ids


async def query_layers(
    *,
    layer: str,
    query: str,
    scope_items: list[dict],
    count: int = 5,
    request=None,
    user=None,
    db: Optional[AsyncSession] = None,
) -> list[dict]:
    knowledge_ids, file_ids = _scope_ids(scope_items)
    runtime_layer = _normalize_runtime_layer_type(layer)
    if not runtime_layer:
        raise ValueError(f"Unsupported layer_type: {layer}")
    rows = await KnowledgeLayers.query_layer_rows(
        layer_type=runtime_layer,
        query=query,
        knowledge_ids=knowledge_ids or None,
        file_ids=file_ids or None,
        limit=count,
        request=request,
        db=db,
    )
    return [KnowledgeFileLayerQueryRow.model_validate(row).model_dump() for row in rows]


async def get_file_layers(
    *,
    file_id: str,
    scope_items: list[dict],
    request=None,
    user=None,
    db: Optional[AsyncSession] = None,
) -> dict:
    knowledge_ids, _ = _scope_ids(scope_items)
    rows = await KnowledgeLayers.get_layers_for_scope_file(
        file_id=file_id,
        knowledge_ids=knowledge_ids or None,
        db=db,
    )
    payload = {"file_id": file_id, "layers": {}}
    for row in rows:
        if row.layer_type not in ACTIVE_LAYER_TYPES:
            continue
        base_source = row.title or row.file_id
        source = f"{row.display_title}: {base_source}" if row.display_title else base_source
        layer_payload = {
            "content": row.content or "",
            "status": row.status,
            "source": source,
            "file_id": row.file_id,
            "knowledge_id": row.knowledge_id,
            "updated_at": row.updated_at,
            "part_index": row.part_index,
            "part_total": row.part_total,
            "display_title": row.display_title,
        }
        if row.part_total > 1:
            existing = payload["layers"].get(row.layer_type)
            if not isinstance(existing, list):
                payload["layers"][row.layer_type] = []
            payload["layers"][row.layer_type].append(layer_payload)
        else:
            payload["layers"][row.layer_type] = layer_payload
    return payload


query_layer_rows = query_layers
view_file_layers = get_file_layers
