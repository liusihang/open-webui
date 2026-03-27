import logging
import re
import time
import asyncio
from types import SimpleNamespace
from typing import Optional

import requests
import tiktoken
from requests import RequestException
from sqlalchemy.orm import Session

from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.misc import calculate_sha256_string
from open_webui.utils.task import get_task_model_id
from open_webui.utils.knowledge_layer_embeddings import (
    delete_layer_embeddings_by_file_id,
    delete_layer_embeddings_by_knowledge_id,
    sync_file_layer_embeddings,
)
from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.knowledge_layers import (
    LAYER_TYPES,
    KnowledgeFileLayerModel,
    KnowledgeFileLayerQueryRow,
    KnowledgeFileLayerUpsertForm,
    KnowledgeLayers,
)

log = logging.getLogger(__name__)

OPEN_NOTEBOOK_SOURCE_ID_KEY = "open_notebook_source_id"
OPEN_NOTEBOOK_SOURCE_IDS_KEY = "open_notebook_source_ids"
OPEN_NOTEBOOK_SYNC_STATUS_KEY = "open_notebook_sync_status"
OPEN_NOTEBOOK_LAST_SYNCED_AT_KEY = "open_notebook_last_synced_at"
OPEN_NOTEBOOK_IS_LARGE_FILE_KEY = "open_notebook_is_large_file"
OPEN_NOTEBOOK_PART_COUNT_KEY = "open_notebook_part_count"
OPEN_NOTEBOOK_SOURCE_CONTENT_HASH_KEY = "open_notebook_source_content_hash"
DEFAULT_MAX_CHUNK_TOKENS = 24000
DEFAULT_MIN_TAIL_TOKENS = 1000
REFERENCE_SECTION_TITLES = (
    "references",
    "bibliography",
    "works cited",
    "参考文献",
)
ACTIVE_LAYER_TYPES = ("abstract",)
COMPAT_LAYER_ALIASES = {"key_findings": "abstract", "key_data": "abstract"}
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


def get_file_open_notebook_mapping(file_obj) -> dict:
    meta = getattr(file_obj, "meta", None)
    if not isinstance(meta, dict):
        meta = {}

    source_id = meta.get(OPEN_NOTEBOOK_SOURCE_ID_KEY)
    if source_id is not None:
        source_id = str(source_id)

    source_ids = meta.get(OPEN_NOTEBOOK_SOURCE_IDS_KEY)
    if isinstance(source_ids, str):
        source_ids = [source_ids]
    elif isinstance(source_ids, list):
        source_ids = [str(item) for item in source_ids if item]
    else:
        source_ids = []

    return {
        OPEN_NOTEBOOK_SOURCE_ID_KEY: source_id,
        OPEN_NOTEBOOK_SOURCE_IDS_KEY: source_ids,
        OPEN_NOTEBOOK_SYNC_STATUS_KEY: meta.get(OPEN_NOTEBOOK_SYNC_STATUS_KEY),
        OPEN_NOTEBOOK_LAST_SYNCED_AT_KEY: meta.get(OPEN_NOTEBOOK_LAST_SYNCED_AT_KEY),
        OPEN_NOTEBOOK_IS_LARGE_FILE_KEY: meta.get(OPEN_NOTEBOOK_IS_LARGE_FILE_KEY),
        OPEN_NOTEBOOK_PART_COUNT_KEY: meta.get(OPEN_NOTEBOOK_PART_COUNT_KEY),
        OPEN_NOTEBOOK_SOURCE_CONTENT_HASH_KEY: meta.get(
            OPEN_NOTEBOOK_SOURCE_CONTENT_HASH_KEY
        ),
    }


def save_file_open_notebook_mapping(
    file_id: str,
    *,
    source_id: Optional[str] = None,
    source_ids: Optional[list[str]] = None,
    sync_status: Optional[str] = None,
    last_synced_at: Optional[int] = None,
    is_large_file: Optional[bool] = None,
    part_count: Optional[int] = None,
    source_content_hash: Optional[str] = None,
    db: Optional[Session] = None,
) -> dict:
    file_obj = Files.get_file_by_id(file_id, db=db)
    if not file_obj:
        return {}

    meta_update: dict = {}
    if source_id is not None:
        meta_update[OPEN_NOTEBOOK_SOURCE_ID_KEY] = str(source_id)
    if source_ids is not None:
        meta_update[OPEN_NOTEBOOK_SOURCE_IDS_KEY] = [
            str(item) for item in source_ids if item
        ]
    if sync_status is not None:
        meta_update[OPEN_NOTEBOOK_SYNC_STATUS_KEY] = sync_status
    if last_synced_at is not None:
        meta_update[OPEN_NOTEBOOK_LAST_SYNCED_AT_KEY] = int(last_synced_at)
    if is_large_file is not None:
        meta_update[OPEN_NOTEBOOK_IS_LARGE_FILE_KEY] = bool(is_large_file)
    if part_count is not None:
        meta_update[OPEN_NOTEBOOK_PART_COUNT_KEY] = int(part_count)
    if source_content_hash is not None:
        meta_update[OPEN_NOTEBOOK_SOURCE_CONTENT_HASH_KEY] = source_content_hash

    if meta_update:
        updated = Files.update_file_metadata_by_id(file_id, meta_update, db=db)
        if updated:
            file_obj = updated

    return get_file_open_notebook_mapping(file_obj)


def _get_tiktoken_encoding():
    return tiktoken.get_encoding("cl100k_base")


def estimate_text_tokens(text: str) -> int:
    encoding = _get_tiktoken_encoding()
    return len(encoding.encode(text or ""))


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


def is_reference_like_chunk(text: str) -> bool:
    normalized_text = (text or "").strip()
    if not normalized_text:
        return False

    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    if not lines:
        return False

    first_line = lines[0].lower().rstrip(":")
    has_reference_heading = any(
        first_line == title or first_line.startswith(f"{title} ")
        for title in REFERENCE_SECTION_TITLES
    )

    numbered_entry_count = sum(
        1 for line in lines if re.match(r"^\s*(\[\d+\]|\d+\.)\s+", line)
    )
    doi_count = len(
        re.findall(r"(?:\bdoi:\s*10\.\S+)|(?:\b10\.\d{4,9}/\S+)", normalized_text, re.I)
    )
    year_count = len(re.findall(r"\b(?:19|20)\d{2}[a-z]?\b", normalized_text))
    et_al_count = len(re.findall(r"\bet al\.?\b", normalized_text, re.I))

    citation_signal_count = 0
    if numbered_entry_count >= 1:
        citation_signal_count += 1
    if doi_count >= 1:
        citation_signal_count += 1
    if year_count >= 2:
        citation_signal_count += 1
    if et_al_count >= 1:
        citation_signal_count += 1

    if has_reference_heading and citation_signal_count >= 1:
        return True

    return numbered_entry_count >= 2 and citation_signal_count >= 2


def _drop_trailing_reference_like_chunks(chunk_rows: list[dict]) -> list[dict]:
    if len(chunk_rows) <= 1:
        return chunk_rows

    cutoff = len(chunk_rows)
    while cutoff > 1 and is_reference_like_chunk(chunk_rows[cutoff - 1]["content"]):
        cutoff -= 1

    return chunk_rows[:cutoff]


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

    # Product requirement: discard the trailing remainder if it is too small
    # to justify a separate Open Notebook source/insight generation pass.
    if len(chunk_rows) > 1 and chunk_rows[-1]["token_count"] < min_tail_tokens:
        chunk_rows.pop()

    chunk_rows = _drop_trailing_reference_like_chunks(chunk_rows)

    total_parts = len(chunk_rows)
    for index, chunk in enumerate(chunk_rows, start=1):
        chunk["part_index"] = index
        chunk["part_total"] = total_parts

    return chunk_rows


def _build_source_create_payload(file_obj) -> dict:
    filename = getattr(file_obj, "filename", None) or getattr(file_obj, "id", "file")
    data = getattr(file_obj, "data", None)
    content = None
    if isinstance(data, dict):
        raw_content = data.get("content") or data.get("text")
        if isinstance(raw_content, str) and raw_content.strip():
            content = raw_content

    payload = {"title": filename}
    if content:
        payload.update({"type": "text", "content": content})
    else:
        file_path = getattr(file_obj, "path", None)
        if file_path:
            payload.update({"type": "upload", "file_path": file_path})
        else:
            raise ValueError("File has no extracted text content or usable file_path")
    return payload


def _extract_file_text(file_obj) -> str:
    data = getattr(file_obj, "data", None)
    if isinstance(data, dict):
        for key in ("content", "text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _layer_display_label(layer_type: str) -> str:
    if layer_type == "abstract":
        return "Abstract"
    if layer_type == "key_findings":
        return "Key Findings"
    if layer_type == "key_data":
        return "Key Data"
    return layer_type


def _layer_display_title(layer_type: str, part_index: int, part_total: int) -> str:
    return f"{_layer_display_label(layer_type)} {part_index}/{part_total}"


def _layer_generation_chunk_limits(request) -> tuple[int, int]:
    config = request.app.state.config
    max_tokens = int(
        getattr(config, "LAYER_GENERATION_MAX_CHUNK_TOKENS", DEFAULT_MAX_CHUNK_TOKENS)
        or DEFAULT_MAX_CHUNK_TOKENS
    )
    min_tail_tokens = int(
        getattr(config, "LAYER_GENERATION_MIN_TAIL_TOKENS", DEFAULT_MIN_TAIL_TOKENS)
        or DEFAULT_MIN_TAIL_TOKENS
    )
    return max_tokens, min_tail_tokens


def _resolve_generation_models(request) -> dict:
    if getattr(request.state, "direct", False) and hasattr(request.state, "model"):
        model = request.state.model
        if isinstance(model, dict) and model.get("id"):
            return {model["id"]: model}

    return getattr(request.app.state, "MODELS", {}) or {}


def _get_layer_generation_model_id(request, layer_type: str) -> Optional[str]:
    config = request.app.state.config
    models = _resolve_generation_models(request)
    if not models:
        return None

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
            model_id
            for model_id, model in models.items()
            if model.get("owned_by") != "arena"
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
    configured_prompt = (
        getattr(request.app.state.config, "LAYER_GENERATION_PROMPT_ABSTRACT", "") or ""
    ).strip()
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


def _run_coroutine_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "Synchronous layered knowledge generation cannot run inside an active event loop"
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
            content_parts = item.get("content", [])
            if not isinstance(content_parts, list):
                continue
            for part in content_parts:
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


def _current_file_content_hash(file_obj) -> str:
    file_hash = getattr(file_obj, "hash", None)
    if isinstance(file_hash, str) and file_hash.strip():
        return file_hash

    file_text = _extract_file_text(file_obj)
    if file_text:
        return calculate_sha256_string(file_text)

    file_path = getattr(file_obj, "path", None)
    if isinstance(file_path, str) and file_path:
        return calculate_sha256_string(file_path)

    return ""


def _ensure_open_notebook_source_id(
    *,
    file_id: str,
    base_url: str,
    password: str,
    timeout: int,
    db: Optional[Session] = None,
) -> Optional[str]:
    file_obj = Files.get_file_by_id(file_id, db=db)
    if not file_obj:
        return None

    mapping = get_file_open_notebook_mapping(file_obj)
    source_id = mapping.get(OPEN_NOTEBOOK_SOURCE_ID_KEY)
    current_content_hash = _current_file_content_hash(file_obj)
    if source_id and (
        not current_content_hash
        or mapping.get(OPEN_NOTEBOOK_SOURCE_CONTENT_HASH_KEY) == current_content_hash
    ):
        return source_id

    source = _request_json(
        "POST",
        f"{base_url}/api/sources",
        password,
        timeout,
        payload=_build_source_create_payload(file_obj),
    )
    if not isinstance(source, dict):
        return None
    source_id = str(source.get("id", "")).strip()
    if not source_id:
        return None

    save_file_open_notebook_mapping(
        file_id,
        source_id=source_id,
        source_ids=[source_id],
        sync_status="created",
        last_synced_at=int(time.time()),
        is_large_file=False,
        part_count=1,
        source_content_hash=current_content_hash,
        db=db,
    )
    return source_id


def _create_open_notebook_source(
    *,
    base_url: str,
    password: str,
    timeout: int,
    payload: dict,
) -> Optional[str]:
    source = _request_json(
        "POST",
        f"{base_url}/api/sources",
        password,
        timeout,
        payload=payload,
    )
    if not isinstance(source, dict):
        return None
    source_id = str(source.get("id", "")).strip()
    return source_id or None


def _normalize_layer_type(layer_type: str) -> Optional[str]:
    normalized = (layer_type or "").strip().lower()
    return normalized if normalized in LAYER_TYPES else None


def _normalize_runtime_layer_type(layer_type: str) -> Optional[str]:
    normalized = _normalize_layer_type(layer_type)
    if not normalized:
        return None
    return COMPAT_LAYER_ALIASES.get(normalized, normalized)


def get_layer_transformation_id(request, layer_type: str) -> Optional[str]:
    normalized = _normalize_runtime_layer_type(layer_type)
    if not normalized:
        return None

    config = request.app.state.config
    if normalized == "abstract":
        return (config.OPEN_NOTEBOOK_TRANSFORMATION_ABSTRACT or "").strip() or None
    return None


def _open_notebook_config(request) -> tuple[Optional[str], Optional[str], int]:
    config = request.app.state.config
    base_url = (config.OPEN_NOTEBOOK_BASE_URL or "").strip().rstrip("/")
    password = (config.OPEN_NOTEBOOK_API_PASSWORD or "").strip()
    timeout = int(config.OPEN_NOTEBOOK_TIMEOUT_SECS or 30)
    return (base_url or None, password or None, timeout)


def _request_json(
    method: str,
    url: str,
    password: str,
    timeout: int,
    payload: Optional[dict] = None,
):
    headers = {
        "Authorization": f"Bearer {password}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
    except RequestException as exc:
        raise RuntimeError(f"Open Notebook request failed: {method} {url} ({exc})")

    if response.status_code == 204:
        return None

    try:
        return response.json()
    except ValueError:
        return None


def _upsert_status(
    knowledge_id: str,
    file_id: str,
    layer_type: str,
    *,
    status: str,
    content: Optional[str] = None,
    source_system: str = "open_notebook",
    source_ref_id: Optional[str] = None,
    transformation_ref_id: Optional[str] = None,
    content_hash: Optional[str] = None,
    part_index: int = 1,
    part_total: int = 1,
    display_title: Optional[str] = None,
    db: Optional[Session] = None,
) -> KnowledgeFileLayerModel:
    return KnowledgeLayers.upsert_layer(
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


def _build_chunk_specs(
    *,
    request,
    file_obj,
    file_id: str,
    base_url: str,
    password: str,
    timeout: int,
    db: Optional[Session] = None,
) -> tuple[list[dict], bool, str]:
    file_text = _extract_file_text(file_obj)
    chunks = plan_text_chunks(file_text) if file_text else []
    is_large_file = len(chunks) > 1
    current_content_hash = _current_file_content_hash(file_obj)

    mapping = get_file_open_notebook_mapping(file_obj)
    mapped_source_ids = mapping.get(OPEN_NOTEBOOK_SOURCE_IDS_KEY) or []
    mapping_hash_matches = (
        mapping.get(OPEN_NOTEBOOK_SOURCE_CONTENT_HASH_KEY) == current_content_hash
    )

    chunk_specs: list[dict] = []
    source_ids: list[str] = []

    if is_large_file:
        should_recreate_sources = (
            not mapped_source_ids
            or len(mapped_source_ids) != len(chunks)
            or not mapping_hash_matches
        )
        if should_recreate_sources:
            for index, chunk in enumerate(chunks, start=1):
                payload = {
                    "type": "text",
                    "title": f"{getattr(file_obj, 'filename', file_id)} {index}/{len(chunks)}",
                    "content": chunk.get("content", ""),
                }
                created_source_id = _create_open_notebook_source(
                    base_url=base_url,
                    password=password,
                    timeout=timeout,
                    payload=payload,
                )
                if not created_source_id:
                    return [], is_large_file, current_content_hash
                source_ids.append(created_source_id)
        else:
            source_ids = mapped_source_ids

        save_file_open_notebook_mapping(
            file_id,
            source_id=source_ids[0] if source_ids else None,
            source_ids=source_ids,
            sync_status="created",
            last_synced_at=int(time.time()),
            is_large_file=True,
            part_count=len(source_ids),
            source_content_hash=current_content_hash,
            db=db,
        )

        for index, source_id in enumerate(source_ids, start=1):
            chunk_specs.append(
                {
                    "source_id": source_id,
                    "part_index": index,
                    "part_total": len(source_ids),
                }
            )
        return chunk_specs, True, current_content_hash

    source_id = _ensure_open_notebook_source_id(
        file_id=file_id,
        base_url=base_url,
        password=password,
        timeout=timeout,
        db=db,
    )
    if not source_id:
        return [], False, current_content_hash

    save_file_open_notebook_mapping(
        file_id,
        source_id=source_id,
        source_ids=[source_id],
        sync_status="created",
        last_synced_at=int(time.time()),
        is_large_file=False,
        part_count=1,
        source_content_hash=current_content_hash,
        db=db,
    )
    return [{"source_id": source_id, "part_index": 1, "part_total": 1}], False, current_content_hash


async def _sync_selected_layers_for_file_async(
    request,
    knowledge_id: str,
    file_id: str,
    *,
    selected_layer_types: list[str],
    db: Optional[Session] = None,
) -> list[KnowledgeFileLayerModel]:
    file_obj = Files.get_file_by_id(file_id, db=db)
    if not file_obj:
        return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    file_text = _extract_file_text(file_obj)
    current_content_hash = _current_file_content_hash(file_obj)
    if not file_text:
        KnowledgeLayers.delete_layers_by_file(
            knowledge_id, file_id, layer_types=selected_layer_types, db=db
        )
        for layer_type in selected_layer_types:
            _upsert_status(
                knowledge_id,
                file_id,
                layer_type,
                status="failed",
                content=(
                    "Layer generation failed: file has no extracted text content "
                    "available for native generation."
                ),
                source_system="open_webui",
                content_hash=current_content_hash,
                part_index=1,
                part_total=1,
                display_title=None,
                db=db,
            )
        return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    max_tokens, min_tail_tokens = _layer_generation_chunk_limits(request)
    chunk_specs = plan_text_chunks(
        file_text,
        max_tokens=max_tokens,
        min_tail_tokens=min_tail_tokens,
    )
    if not chunk_specs:
        KnowledgeLayers.delete_layers_by_file(
            knowledge_id, file_id, layer_types=selected_layer_types, db=db
        )
        for layer_type in selected_layer_types:
            _upsert_status(
                knowledge_id,
                file_id,
                layer_type,
                status="failed",
                content=(
                    "Layer generation failed: file has no extracted text content "
                    "available for native generation."
                ),
                source_system="open_webui",
                content_hash=current_content_hash,
                part_index=1,
                part_total=1,
                display_title=None,
                db=db,
            )
        return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    KnowledgeLayers.delete_layers_by_file(
        knowledge_id, file_id, layer_types=selected_layer_types, db=db
    )
    for layer_type in selected_layer_types:
        for spec in chunk_specs:
            part_index = spec["part_index"]
            part_total = spec["part_total"]
            display_title = (
                _layer_display_title(layer_type, part_index, part_total)
                if part_total > 1
                else None
            )
            _upsert_status(
                knowledge_id,
                file_id,
                layer_type,
                status="pending",
                source_system="open_webui",
                source_ref_id=f"chunk:{part_index}",
                transformation_ref_id=_get_layer_generation_model_id(request, layer_type),
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
                _upsert_status(
                    knowledge_id,
                    file_id,
                    layer_type,
                    status="ready",
                    content=content,
                    source_system="open_webui",
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
                _upsert_status(
                    knowledge_id,
                    file_id,
                    layer_type,
                    status="failed",
                    content=str(exc),
                    source_system="open_webui",
                    source_ref_id=f"chunk:{part_index}",
                    transformation_ref_id=_get_layer_generation_model_id(
                        request, layer_type
                    ),
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

    return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)


async def sync_layers_for_file_async(
    request, knowledge_id: str, file_id: str, db: Optional[Session] = None
) -> list[KnowledgeFileLayerModel]:
    return await _sync_selected_layers_for_file_async(
        request,
        knowledge_id,
        file_id,
        selected_layer_types=list(ACTIVE_LAYER_TYPES),
        db=db,
    )


def sync_layers_for_file(
    request, knowledge_id: str, file_id: str, db: Optional[Session] = None
) -> list[KnowledgeFileLayerModel]:
    return _run_coroutine_sync(
        sync_layers_for_file_async(
            request,
            knowledge_id,
            file_id,
            db=db,
        )
    )


def regenerate_layer_for_file(
    request,
    knowledge_id: str,
    file_id: str,
    layer_type: str,
    db: Optional[Session] = None,
) -> list[KnowledgeFileLayerModel]:
    normalized_layer = _normalize_runtime_layer_type(layer_type)
    if not normalized_layer:
        raise ValueError(f"Unsupported layer_type: {layer_type}")
    return _run_coroutine_sync(
        _sync_selected_layers_for_file_async(
            request,
            knowledge_id,
            file_id,
            selected_layer_types=[normalized_layer],
            db=db,
        )
    )


async def regenerate_layers_for_file_async(
    request,
    knowledge_id: str,
    file_id: str,
    *,
    layer_types: Optional[list[str]] = None,
    force: bool = False,
    db: Optional[Session] = None,
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

    return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)


def regenerate_layers_for_file(
    request,
    knowledge_id: str,
    file_id: str,
    *,
    layer_types: Optional[list[str]] = None,
    force: bool = False,
    db: Optional[Session] = None,
) -> list[KnowledgeFileLayerModel]:
    return _run_coroutine_sync(
        regenerate_layers_for_file_async(
            request,
            knowledge_id,
            file_id,
            layer_types=layer_types,
            force=force,
            db=db,
        )
    )


def _file_needs_backfill(
    rows: list[KnowledgeFileLayerModel],
    layer_types: list[str],
) -> bool:
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
    db: Optional[Session] = None,
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

    files = Knowledges.get_files_by_id(knowledge_id, db=db) or []
    total_files = len(files)
    scheduled_files = 0

    for file in files:
        rows = KnowledgeLayers.get_layers_by_file(knowledge_id, file.id, db=db)
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


def backfill_layers_for_knowledge(
    request,
    knowledge_id: str,
    *,
    layer_types: Optional[list[str]] = None,
    force: bool = False,
    db: Optional[Session] = None,
) -> dict:
    return _run_coroutine_sync(
        backfill_layers_for_knowledge_async(
            request,
            knowledge_id,
            layer_types=layer_types,
            force=force,
            db=db,
        )
    )


def mark_layers_for_file_stale(
    knowledge_id: str, file_id: str, db: Optional[Session] = None
) -> int:
    delete_layer_embeddings_by_file_id(file_id)
    return KnowledgeLayers.mark_layers_stale_for_file(knowledge_id, file_id, db=db)


def mark_layers_for_knowledge_stale(
    knowledge_id: str, db: Optional[Session] = None
) -> int:
    delete_layer_embeddings_by_knowledge_id(knowledge_id)
    return KnowledgeLayers.mark_layers_stale_for_knowledge(knowledge_id, db=db)


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
    db: Optional[Session] = None,
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
    return [
        KnowledgeFileLayerQueryRow.model_validate(row).model_dump() for row in rows
    ]


def get_file_layers(
    *,
    file_id: str,
    scope_items: list[dict],
    request=None,
    user=None,
    db: Optional[Session] = None,
) -> dict:
    knowledge_ids, _ = _scope_ids(scope_items)
    rows = KnowledgeLayers.get_layers_for_scope_file(
        file_id=file_id,
        knowledge_ids=knowledge_ids or None,
        db=db,
    )
    payload = {
        "file_id": file_id,
        "layers": {},
    }
    for row in rows:
        if row.layer_type not in ACTIVE_LAYER_TYPES:
            continue
        base_source = row.title or row.file_id
        source = (
            f"{row.display_title}: {base_source}" if row.display_title else base_source
        )
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
