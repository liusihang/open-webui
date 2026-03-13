from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable


log = logging.getLogger(__name__)


async def emit_knowledge_search_status(
    callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    description: str,
    done: bool = False,
    **extra: Any,
) -> None:
    if callback is None:
        return

    event = {
        "type": "status",
        "data": {
            "action": "knowledge_search",
            "description": description,
            "done": done,
            **extra,
        },
    }

    try:
        result = callback(event)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        log.debug("Ignoring retrieval status callback failure: %s", exc)
