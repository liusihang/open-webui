import importlib


def _load_middleware_module():
    return importlib.import_module("backend.open_webui.utils.middleware")


def test_build_recursive_rag_source_context_omits_tool_only_sources():
    middleware = _load_middleware_module()

    file_sources = [
        {
            "source": {"id": "file-1", "name": "knowledge.txt", "type": "file"},
            "document": ["useful file context"],
            "metadata": [{"source": "knowledge.txt", "name": "knowledge.txt"}],
        }
    ]
    tool_sources = [
        {
            "source": {"name": "search_web", "id": "search_web"},
            "document": ["Title\nSnippet from tool output"],
            "metadata": [
                {
                    "source": "https://example.com/paper",
                    "name": "Example Paper",
                    "url": "https://example.com/paper",
                }
            ],
        }
    ]

    context = middleware.build_recursive_rag_source_context(
        file_sources=file_sources,
        tool_sources=tool_sources,
    )

    assert "useful file context" in context
    assert context.count("<source") == 1
    assert 'name="knowledge.txt"' in context
    assert 'name="search_web"' not in context


def test_build_source_event_payload_batches_multiple_sources():
    middleware = _load_middleware_module()

    sources = [
        {"source": {"id": "a", "name": "A"}, "document": ["doc-a"], "metadata": [{"source": "a"}]},
        {"source": {"id": "b", "name": "B"}, "document": ["doc-b"], "metadata": [{"source": "b"}]},
    ]

    payload = middleware.build_source_event_payload(sources)

    assert payload == {
        "type": "source",
        "data": {
            "sources": sources,
        },
    }


def test_build_source_event_payload_keeps_single_source_shape():
    middleware = _load_middleware_module()

    source = {
        "source": {"id": "a", "name": "A"},
        "document": ["doc-a"],
        "metadata": [{"source": "a"}],
    }

    payload = middleware.build_source_event_payload([source])

    assert payload == {
        "type": "source",
        "data": source,
    }


def test_extract_embedded_sources_from_tool_result_reads_top_level_sources():
    middleware = _load_middleware_module()

    payload = {
        "citation_passthrough": True,
        "completed": True,
        "result": "summary",
        "sources": [
            {
                "source": {"id": "https://example.com/paper", "name": "Example Paper"},
                "document": ["Example Paper\nsnippet"],
                "metadata": [
                    {
                        "source": "https://example.com/paper",
                        "name": "Example Paper",
                        "url": "https://example.com/paper",
                    }
                ],
            }
        ],
    }

    extracted = middleware.extract_embedded_sources_from_tool_result(
        middleware.json.dumps(payload, ensure_ascii=False)
    )

    assert extracted == payload["sources"]


def test_extract_embedded_sources_from_tool_result_reads_nested_parallel_sources():
    middleware = _load_middleware_module()

    payload = {
        "citation_passthrough": True,
        "completed": True,
        "results": [
            {
                "description": "task 1",
                "result": "summary",
                "sources": [
                    {
                        "source": {"id": "https://example.com/a", "name": "Paper A"},
                        "document": ["Paper A\nsnippet"],
                        "metadata": [
                            {
                                "source": "https://example.com/a",
                                "name": "Paper A",
                                "url": "https://example.com/a",
                            }
                        ],
                    }
                ],
            }
        ],
    }

    extracted = middleware.extract_embedded_sources_from_tool_result(
        middleware.json.dumps(payload, ensure_ascii=False)
    )

    assert extracted == payload["results"][0]["sources"]


def test_extract_embedded_sources_from_tool_result_requires_protocol_marker():
    middleware = _load_middleware_module()

    payload = {
        "completed": True,
        "result": "summary",
        "sources": [
            {
                "source": {"id": "https://example.com/paper", "name": "Example Paper"},
                "document": ["Example Paper\nsnippet"],
                "metadata": [
                    {
                        "source": "https://example.com/paper",
                        "name": "Example Paper",
                        "url": "https://example.com/paper",
                    }
                ],
            }
        ],
    }

    extracted = middleware.extract_embedded_sources_from_tool_result(
        middleware.json.dumps(payload, ensure_ascii=False)
    )

    assert extracted == []


def test_build_chat_completion_event_data_can_skip_serialized_content():
    middleware = _load_middleware_module()

    output = [{"type": "function_call", "call_id": "fc-1", "name": "search_web"}]

    payload = middleware.build_chat_completion_event_data(output, include_content=False)

    assert payload == {
        "output": output,
    }


def test_build_chat_completion_event_data_includes_content_by_default():
    middleware = _load_middleware_module()

    output = [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]

    payload = middleware.build_chat_completion_event_data(output)

    assert payload["output"] == output
    assert payload["content"] == "hello"
