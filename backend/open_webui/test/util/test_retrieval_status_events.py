import importlib

import pytest


def _load_status_module():
    return importlib.import_module("open_webui.retrieval.status")


@pytest.mark.asyncio
async def test_emit_knowledge_search_status_uses_existing_status_shape() -> None:
    mod = _load_status_module()
    events = []

    async def callback(event):
        events.append(event)

    await mod.emit_knowledge_search_status(
        callback,
        "Preparing BM25 index (first query may be slower)",
        cache="miss",
    )

    assert events == [
        {
            "type": "status",
            "data": {
                "action": "knowledge_search",
                "description": "Preparing BM25 index (first query may be slower)",
                "done": False,
                "cache": "miss",
            },
        }
    ]


@pytest.mark.asyncio
async def test_emit_knowledge_search_status_ignores_callback_failures() -> None:
    mod = _load_status_module()

    async def callback(_event):
        raise RuntimeError("boom")

    await mod.emit_knowledge_search_status(callback, "Reusing BM25 cache", cache="hit")
