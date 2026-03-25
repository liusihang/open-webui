import logging
from typing import Optional

import requests
from requests import RequestException
from sqlalchemy.orm import Session

from open_webui.models.knowledge_layers import (
    LAYER_TYPES,
    KnowledgeFileLayerModel,
    KnowledgeFileLayerQueryRow,
    KnowledgeFileLayerUpsertForm,
    KnowledgeLayers,
)

log = logging.getLogger(__name__)


def _normalize_layer_type(layer_type: str) -> Optional[str]:
    normalized = (layer_type or "").strip().lower()
    return normalized if normalized in LAYER_TYPES else None


def get_layer_transformation_id(request, layer_type: str) -> Optional[str]:
    normalized = _normalize_layer_type(layer_type)
    if not normalized:
        return None

    config = request.app.state.config
    if normalized == "abstract":
        return (config.OPEN_NOTEBOOK_TRANSFORMATION_ABSTRACT or "").strip() or None
    if normalized == "key_findings":
        return (
            (config.OPEN_NOTEBOOK_TRANSFORMATION_KEY_FINDINGS or "").strip() or None
        )
    if normalized == "key_data":
        return (config.OPEN_NOTEBOOK_TRANSFORMATION_KEY_DATA or "").strip() or None
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
    source_ref_id: Optional[str] = None,
    transformation_ref_id: Optional[str] = None,
    db: Optional[Session] = None,
) -> KnowledgeFileLayerModel:
    return KnowledgeLayers.upsert_layer(
        KnowledgeFileLayerUpsertForm(
            knowledge_id=knowledge_id,
            file_id=file_id,
            layer_type=layer_type,
            status=status,
            content=content,
            source_system="open_notebook",
            source_ref_id=source_ref_id,
            transformation_ref_id=transformation_ref_id,
        ),
        db=db,
    )


def sync_layers_for_file(
    request, knowledge_id: str, file_id: str, db: Optional[Session] = None
) -> list[KnowledgeFileLayerModel]:
    base_url, password, timeout = _open_notebook_config(request)
    if not base_url or not password:
        return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    trigger_errors: dict[str, str] = {}
    for layer_type in LAYER_TYPES:
        transformation_id = get_layer_transformation_id(request, layer_type)
        if not transformation_id:
            continue

        _upsert_status(
            knowledge_id,
            file_id,
            layer_type,
            status="pending",
            transformation_ref_id=transformation_id,
            db=db,
        )
        try:
            _request_json(
                "POST",
                f"{base_url}/api/sources/{file_id}/insights",
                password,
                timeout,
                payload={"transformation_id": transformation_id},
            )
        except RuntimeError as exc:
            trigger_errors[layer_type] = str(exc)
            _upsert_status(
                knowledge_id,
                file_id,
                layer_type,
                status="failed",
                content=str(exc),
                transformation_ref_id=transformation_id,
                db=db,
            )

    try:
        insights = _request_json(
            "GET",
            f"{base_url}/api/sources/{file_id}/insights",
            password,
            timeout,
        )
    except RuntimeError as exc:
        log.warning(
            "Open Notebook insight list failed for knowledge=%s file=%s: %s",
            knowledge_id,
            file_id,
            exc,
        )
        return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    if not isinstance(insights, list):
        return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    for insight in insights:
        layer_type = _normalize_layer_type(str(insight.get("insight_type", "")))
        if not layer_type:
            continue

        content = insight.get("content")
        if not isinstance(content, str) or not content.strip():
            continue

        transformation_id = get_layer_transformation_id(request, layer_type)
        _upsert_status(
            knowledge_id,
            file_id,
            layer_type,
            status="ready",
            content=content,
            source_ref_id=str(insight.get("id", "")) or None,
            transformation_ref_id=transformation_id,
            db=db,
        )

    for layer_type, message in trigger_errors.items():
        # Preserve visibility into trigger failures when no successful insight was found.
        existing = [
            row
            for row in KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)
            if row.layer_type == layer_type
        ]
        if existing and existing[0].status == "ready":
            continue
        _upsert_status(
            knowledge_id,
            file_id,
            layer_type,
            status="failed",
            content=message,
            transformation_ref_id=get_layer_transformation_id(request, layer_type),
            db=db,
        )

    return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)


def regenerate_layer_for_file(
    request,
    knowledge_id: str,
    file_id: str,
    layer_type: str,
    db: Optional[Session] = None,
) -> list[KnowledgeFileLayerModel]:
    normalized_layer = _normalize_layer_type(layer_type)
    if not normalized_layer:
        raise ValueError(f"Unsupported layer_type: {layer_type}")

    base_url, password, timeout = _open_notebook_config(request)
    transformation_id = get_layer_transformation_id(request, normalized_layer)
    if not transformation_id:
        raise ValueError(
            f"Missing transformation id for layer_type={normalized_layer}"
        )

    _upsert_status(
        knowledge_id,
        file_id,
        normalized_layer,
        status="pending",
        transformation_ref_id=transformation_id,
        db=db,
    )

    if not base_url or not password:
        return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    try:
        _request_json(
            "POST",
            f"{base_url}/api/sources/{file_id}/insights",
            password,
            timeout,
            payload={"transformation_id": transformation_id},
        )
        insights = _request_json(
            "GET",
            f"{base_url}/api/sources/{file_id}/insights",
            password,
            timeout,
        )
    except RuntimeError as exc:
        _upsert_status(
            knowledge_id,
            file_id,
            normalized_layer,
            status="failed",
            content=str(exc),
            transformation_ref_id=transformation_id,
            db=db,
        )
        return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)

    if isinstance(insights, list):
        for insight in insights:
            if _normalize_layer_type(str(insight.get("insight_type", ""))) != normalized_layer:
                continue
            content = insight.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            _upsert_status(
                knowledge_id,
                file_id,
                normalized_layer,
                status="ready",
                content=content,
                source_ref_id=str(insight.get("id", "")) or None,
                transformation_ref_id=transformation_id,
                db=db,
            )
            break

    return KnowledgeLayers.get_layers_by_file(knowledge_id, file_id, db=db)


def mark_layers_for_file_stale(
    knowledge_id: str, file_id: str, db: Optional[Session] = None
) -> int:
    return KnowledgeLayers.mark_layers_stale_for_file(knowledge_id, file_id, db=db)


def mark_layers_for_knowledge_stale(
    knowledge_id: str, db: Optional[Session] = None
) -> int:
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


def query_layers(
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
    rows = KnowledgeLayers.query_layer_rows(
        layer_type=layer,
        query=query,
        knowledge_ids=knowledge_ids or None,
        file_ids=file_ids or None,
        limit=count,
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
        payload["layers"][row.layer_type] = {
            "content": row.content or "",
            "status": row.status,
            "source": row.title or row.file_id,
            "file_id": row.file_id,
            "knowledge_id": row.knowledge_id,
            "updated_at": row.updated_at,
        }
    return payload
