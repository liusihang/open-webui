import asyncio
import json
import types

import open_webui.models.groups as groups_module
import open_webui.models.knowledge as knowledge_module
import open_webui.retrieval.utils as retrieval_utils
from open_webui.tools import builtin
from open_webui.tools.builtin import query_knowledge_files


class _FakeRequest:
    def __init__(self):
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=types.SimpleNamespace(
                    TOP_K=4,
                    TOP_K_RERANKER=2,
                    RELEVANCE_THRESHOLD=0.15,
                    HYBRID_BM25_WEIGHT=0.5,
                    ENABLE_RAG_HYBRID_SEARCH=True,
                ),
                RERANKING_FUNCTION=object(),
                EMBEDDING_FUNCTION=object(),
            )
        )


def test_query_knowledge_files_returns_scope_error_without_effective_scope(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        groups_module.Groups, "get_groups_by_member_id", lambda user_id: []
    )
    monkeypatch.setattr(
        knowledge_module.Knowledges,
        "search_knowledge_bases",
        lambda *args, **kwargs: types.SimpleNamespace(items=[]),
    )
    monkeypatch.setattr(
        retrieval_utils,
        "query_collection",
        lambda *args, **kwargs: None,
    )

    result = asyncio.run(
        query_knowledge_files(
            query="claim",
            __request__=_FakeRequest(),
            __user__={"id": "u1"},
            __metadata__={},
        )
    )

    assert result == json.dumps({"error": "No effective knowledge scope"})


def test_query_knowledge_files_uses_effective_scope_with_configured_retrieval(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    async def fake_get_sources_from_items(**kwargs):
        calls.update(kwargs)
        return []

    monkeypatch.setattr(builtin, "get_sources_from_items", fake_get_sources_from_items)
    monkeypatch.setattr(
        groups_module.Groups, "get_groups_by_member_id", lambda user_id: []
    )
    monkeypatch.setattr(
        knowledge_module.Knowledges,
        "search_knowledge_bases",
        lambda *args, **kwargs: types.SimpleNamespace(items=[]),
    )
    monkeypatch.setattr(
        retrieval_utils,
        "query_collection",
        lambda *args, **kwargs: None,
    )

    result = asyncio.run(
        query_knowledge_files(
            query="claim",
            __request__=_FakeRequest(),
            __user__={"id": "u1"},
            __metadata__={
                "effective_knowledge_scope": [
                    {"id": "kb-1", "type": "collection"},
                    {"id": "note-1", "type": "note"},
                ]
            },
        )
    )

    assert calls["items"] == [
        {"id": "kb-1", "type": "collection"},
        {"id": "note-1", "type": "note"},
    ]
    assert calls["queries"] == ["claim"]
    assert calls["full_context"] is False
    assert calls["k"] == 4
    assert calls["k_reranker"] == 2
    assert calls["r"] == 0.15
    assert calls["hybrid_bm25_weight"] == 0.5
    assert calls["hybrid_search"] is True
    assert result == "[]"
