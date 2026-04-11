import types

from open_webui.utils import middleware
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


def _file_context_request(bypass_enabled: bool) -> _FakeRequest:
    request = _FakeRequest()
    request.app.state.config.NATIVE_ATTACHED_KNOWLEDGE_BYPASS_LEGACY_FILE_RETRIEVAL = (
        bypass_enabled
    )
    return request


def test_file_retrieval_helper_skips_legacy_handler_for_native_scoped_knowledge(
    monkeypatch,
) -> None:
    called = False

    async def fake_files_handler(*args, **kwargs):
        nonlocal called
        called = True
        return {"messages": ["legacy"]}, {"sources": [{"source": {"id": "legacy"}}]}

    monkeypatch.setattr(middleware, "chat_completion_files_handler", fake_files_handler)

    form_data, sources = middleware.asyncio.run(
        middleware.apply_legacy_file_retrieval_if_needed(
            request=_file_context_request(True),
            form_data={"messages": ["original"]},
            extra_params={},
            user=types.SimpleNamespace(id="user-1"),
            model={"info": {"meta": {"capabilities": {"file_context": True}}}},
            metadata={
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
            },
        )
    )

    assert called is False
    assert form_data == {"messages": ["original"]}
    assert sources == []


def test_file_retrieval_helper_keeps_legacy_handler_for_non_scoped_chats(
    monkeypatch,
) -> None:
    called = False

    async def fake_files_handler(*args, **kwargs):
        nonlocal called
        called = True
        return {"messages": ["legacy"]}, {"sources": [{"source": {"id": "legacy"}}]}

    monkeypatch.setattr(middleware, "chat_completion_files_handler", fake_files_handler)

    form_data, sources = middleware.asyncio.run(
        middleware.apply_legacy_file_retrieval_if_needed(
            request=_file_context_request(True),
            form_data={"messages": ["original"]},
            extra_params={},
            user=types.SimpleNamespace(id="user-1"),
            model={"info": {"meta": {"capabilities": {"file_context": True}}}},
            metadata={
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": False,
                "effective_knowledge_scope": [],
            },
        )
    )

    assert called is True
    assert form_data == {"messages": ["legacy"]}
    assert sources == [{"source": {"id": "legacy"}}]


def test_file_retrieval_helper_keeps_legacy_handler_when_toggle_disabled(
    monkeypatch,
) -> None:
    called = False

    async def fake_files_handler(*args, **kwargs):
        nonlocal called
        called = True
        return {"messages": ["legacy"]}, {"sources": [{"source": {"id": "legacy"}}]}

    monkeypatch.setattr(middleware, "chat_completion_files_handler", fake_files_handler)

    form_data, sources = middleware.asyncio.run(
        middleware.apply_legacy_file_retrieval_if_needed(
            request=_file_context_request(False),
            form_data={"messages": ["original"]},
            extra_params={},
            user=types.SimpleNamespace(id="user-1"),
            model={"info": {"meta": {"capabilities": {"file_context": True}}}},
            metadata={
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
            },
        )
    )

    assert called is True
    assert form_data == {"messages": ["legacy"]}
    assert sources == [{"source": {"id": "legacy"}}]
