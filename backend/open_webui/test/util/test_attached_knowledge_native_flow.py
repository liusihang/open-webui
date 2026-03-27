import types

from open_webui.utils.tools import get_builtin_tools


class _FakeRequest:
    def __init__(self):
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=types.SimpleNamespace(
                    ENABLE_WEB_SEARCH=False,
                    ENABLE_IMAGE_GENERATION=False,
                    ENABLE_IMAGE_EDIT=False,
                    ENABLE_CODE_INTERPRETER=False,
                    ENABLE_NOTES=False,
                    ENABLE_CHANNELS=False,
                )
            )
        )


def test_native_builtin_tools_append_knowledge_tools_only_for_effective_scope() -> None:
    request = _FakeRequest()
    extra_params = {
        "__metadata__": {
            "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
            "effective_knowledge_query_enabled": True,
        }
    }
    model = {"info": {"meta": {"knowledge": [{"id": "kb-1", "type": "collection"}]}}}

    tools = get_builtin_tools(
        request,
        extra_params,
        features={"attached_knowledge_query": True},
        model=model,
    )

    assert "query_knowledge_abstract" in tools
    assert "query_knowledge_full_text" in tools
    assert "view_knowledge_layers" in tools
    assert "query_knowledge_files" not in tools
    assert "view_file" in tools
    assert "view_note" not in tools
    assert "list_knowledge_bases" not in tools
    assert "search_knowledge_bases" not in tools


def test_native_builtin_tools_skip_scoped_knowledge_tools_without_scope() -> None:
    request = _FakeRequest()
    extra_params = {
        "__metadata__": {
            "effective_knowledge_scope": [],
            "effective_knowledge_query_enabled": True,
        }
    }

    tools = get_builtin_tools(
        request,
        extra_params,
        features={"attached_knowledge_query": True},
        model={"info": {"meta": {}}},
    )

    assert "query_knowledge_files" not in tools
    assert "query_knowledge_abstract" not in tools
    assert "query_knowledge_full_text" not in tools
    assert "view_knowledge_layers" not in tools
    assert "view_file" not in tools
    assert "view_note" not in tools
    assert "list_knowledge_bases" in tools
    assert "search_knowledge_bases" in tools
    assert "query_knowledge_bases" in tools
    assert "search_knowledge_files" in tools
    assert "view_knowledge_file" in tools
