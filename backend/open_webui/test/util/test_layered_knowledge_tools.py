import asyncio
import json
import types

from open_webui.tools import builtin
from open_webui.tools.builtin import (
    query_knowledge_abstract,
    query_knowledge_full_text,
    view_knowledge_layers,
)


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


def test_layered_query_tools_return_scope_error_without_effective_scope() -> None:
    request = _FakeRequest()

    result_abstract = asyncio.run(
        query_knowledge_abstract(
            query="topic",
            __request__=request,
            __user__={"id": "u1"},
            __metadata__={},
        )
    )
    result_layers = asyncio.run(
        view_knowledge_layers(
            file_id="file-1",
            __request__=request,
            __user__={"id": "u1"},
            __metadata__={},
        )
    )

    expected = json.dumps({"error": "No effective knowledge scope"})
    assert result_abstract == expected
    assert result_layers == expected


def test_query_knowledge_abstract_uses_layer_query_and_prefixes_source(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    async def fake_query_layer_rows(**kwargs):
        calls.update(kwargs)
        return [
            {
                "content": "A concise summary",
                "source": "paper.pdf",
                "file_id": "file-1",
                "distance": 0.9,
            }
        ]

    monkeypatch.setattr(builtin, "_query_layer_rows", fake_query_layer_rows)

    result = asyncio.run(
        query_knowledge_abstract(
            query="what is this document about",
            __request__=_FakeRequest(),
            __user__={"id": "u1"},
            __metadata__={
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}]
            },
        )
    )

    payload = json.loads(result)
    assert calls["layer"] == "abstract"
    assert calls["query"] == "what is this document about"
    assert calls["scope_items"] == [{"id": "kb-1", "type": "collection"}]
    assert payload == [
        {
            "layer": "abstract",
            "content": "A concise summary",
            "source": "Abstract: paper.pdf",
            "file_id": "file-1",
            "distance": 0.9,
        }
    ]


def test_query_knowledge_full_text_prefixes_full_text_sources(monkeypatch) -> None:
    async def fake_query_knowledge_files(**kwargs):
        return json.dumps(
            [
                {
                    "content": "Original chunk",
                    "source": "paper.pdf",
                    "file_id": "file-1",
                }
            ]
        )

    monkeypatch.setattr(builtin, "query_knowledge_files", fake_query_knowledge_files)

    result = asyncio.run(
        query_knowledge_full_text(
            query="show me evidence",
            __request__=_FakeRequest(),
            __user__={"id": "u1"},
            __metadata__={
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}]
            },
        )
    )

    payload = json.loads(result)
    assert payload == [
        {
            "layer": "full_text",
            "content": "Original chunk",
            "source": "Full Text: paper.pdf",
            "file_id": "file-1",
        }
    ]


def test_view_knowledge_layers_returns_layer_payload_with_prefixed_sources(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    async def fake_view_layer_rows(**kwargs):
        calls.update(kwargs)
        return {
            "file_id": "file-1",
            "layers": {
                "abstract": {"content": "Summary", "source": "paper.pdf"},
            },
        }

    monkeypatch.setattr(builtin, "_view_layer_rows", fake_view_layer_rows)

    result = asyncio.run(
        view_knowledge_layers(
            file_id="file-1",
            __request__=_FakeRequest(),
            __user__={"id": "u1"},
            __metadata__={
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}]
            },
        )
    )

    payload = json.loads(result)
    assert calls["file_id"] == "file-1"
    assert calls["scope_items"] == [{"id": "kb-1", "type": "collection"}]
    assert payload["layers"]["abstract"]["source"] == "Abstract: paper.pdf"


def test_query_knowledge_abstract_keeps_chunk_source_labels(monkeypatch) -> None:
    async def fake_query_layer_rows(**kwargs):
        return [
            {
                "content": "chunk summary",
                "source": "Abstract 1/3: paper.pdf",
                "file_id": "file-1",
            }
        ]

    monkeypatch.setattr(builtin, "_query_layer_rows", fake_query_layer_rows)

    result = asyncio.run(
        query_knowledge_abstract(
            query="summary",
            __request__=_FakeRequest(),
            __user__={"id": "u1"},
            __metadata__={
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}]
            },
        )
    )

    payload = json.loads(result)
    assert payload[0]["source"] == "Abstract 1/3: paper.pdf"


def test_view_knowledge_layers_supports_chunked_layer_payload(monkeypatch) -> None:
    async def fake_view_layer_rows(**kwargs):
        return {
            "file_id": "file-1",
            "layers": {
                "abstract": [
                    {"content": "part1", "source": "Abstract 1/2: paper.pdf"},
                    {"content": "part2", "source": "Abstract 2/2: paper.pdf"},
                ]
            },
        }

    monkeypatch.setattr(builtin, "_view_layer_rows", fake_view_layer_rows)

    result = asyncio.run(
        view_knowledge_layers(
            file_id="file-1",
            __request__=_FakeRequest(),
            __user__={"id": "u1"},
            __metadata__={
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}]
            },
        )
    )

    payload = json.loads(result)
    assert isinstance(payload["layers"]["abstract"], list)
    assert payload["layers"]["abstract"][0]["source"] == "Abstract 1/2: paper.pdf"
    assert payload["layers"]["abstract"][1]["source"] == "Abstract 2/2: paper.pdf"
