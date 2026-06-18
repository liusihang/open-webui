from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Request

from open_webui.utils.access_control import has_permission
from open_webui.utils.agent_memory_index import (
    list_agent_memory_artifacts,
    read_agent_memory_artifact,
    search_agent_memory_for_chat,
)


def _user_id(user: dict | None) -> str | None:
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def _user_role(user: dict | None) -> str | None:
    if isinstance(user, dict):
        return user.get("role")
    return getattr(user, "role", None)


def _chat_id(metadata: dict | None) -> str:
    value = (metadata or {}).get("chat_id")
    return value if isinstance(value, str) else ""


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def _is_agent_memory_allowed(request: Request | None, user: dict | None, db: Any = None) -> bool:
    if request is None:
        return False
    if not getattr(request.app.state.config, "ENABLE_AGENT_MEMORY", False):
        return False
    user_id = _user_id(user)
    if not user_id:
        return False
    if _user_role(user) == "admin":
        return True
    return await has_permission(
        user_id,
        "features.agent_memory",
        getattr(request.app.state.config, "USER_PERMISSIONS", {}),
        db=db,
    )


async def agent_memory_search(
    query: str,
    limit: Optional[int] = 5,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __db__: Any = None,
) -> str:
    """
    Search read-only Agent Memory for prior work, preferences, repository context, and project decisions.
    """
    if __request__ is None:
        return _json({"error": "Request context not available"})
    user_id = _user_id(__user__)
    if not user_id:
        return _json({"error": "User context not available"})
    if not await _is_agent_memory_allowed(__request__, __user__, db=__db__):
        return _json({"results": []})

    results = await search_agent_memory_for_chat(
        __request__,
        user_id,
        _chat_id(__metadata__),
        query,
        limit=limit,
        db=__db__,
    )
    return _json({"results": results})


async def agent_memory_read(
    path: str,
    scope: Optional[str] = None,
    offset: Optional[int] = 0,
    max_chars: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __db__: Any = None,
) -> str:
    """
    Read one Agent Memory artifact from the current accessible scope. Path must be memory_summary.md or MEMORY.md.
    """
    user_id = _user_id(__user__)
    if not user_id:
        return _json({"error": "User context not available"})
    if not await _is_agent_memory_allowed(__request__, __user__, db=__db__):
        return _json({"error": "Agent Memory is disabled or not permitted"})

    try:
        artifact = await read_agent_memory_artifact(
            user_id,
            _chat_id(__metadata__),
            path,
            scope=scope,
            offset=offset,
            max_chars=max_chars,
            db=__db__,
        )
    except ValueError as exc:
        return _json({"error": str(exc)})

    if not artifact:
        return _json({"error": "Agent Memory artifact not found"})
    return _json(artifact)


async def agent_memory_list(
    scope: Optional[str] = "all_current",
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __db__: Any = None,
) -> str:
    """
    List read-only Agent Memory artifacts available to this chat.
    """
    user_id = _user_id(__user__)
    if not user_id:
        return _json({"error": "User context not available"})
    if not await _is_agent_memory_allowed(__request__, __user__, db=__db__):
        return _json({"artifacts": []})

    try:
        artifacts = await list_agent_memory_artifacts(
            user_id,
            _chat_id(__metadata__),
            scope=scope,
            db=__db__,
        )
    except ValueError as exc:
        return _json({"error": str(exc)})
    return _json({"artifacts": artifacts})
