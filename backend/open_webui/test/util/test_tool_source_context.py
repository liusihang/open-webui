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
