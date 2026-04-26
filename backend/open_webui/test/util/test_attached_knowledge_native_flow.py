import types

from open_webui.utils import middleware


class _FakeRequest:
    def __init__(self):
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(config=types.SimpleNamespace())
        )


def _file_context_request(bypass_enabled: bool) -> _FakeRequest:
    request = _FakeRequest()
    request.app.state.config.NATIVE_ATTACHED_KNOWLEDGE_BYPASS_LEGACY_FILE_RETRIEVAL = (
        bypass_enabled
    )
    return request


def _model() -> dict:
    return {
        "info": {
            "meta": {
                "capabilities": {"file_context": True, "builtin_tools": True},
                "builtinTools": {"knowledge": True},
            }
        }
    }


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
            model=_model(),
            metadata={
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
                "files": [
                    {
                        "id": "kb-1",
                        "type": "collection",
                        "source": "knowledge_attachment",
                    }
                ],
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
            model=_model(),
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


def test_file_retrieval_helper_keeps_regular_files_when_scoped_bypass_is_active(
    monkeypatch,
) -> None:
    captured = {}

    async def fake_files_handler(request, form_data, extra_params, user):
        captured["metadata_files"] = form_data.get("metadata", {}).get("files", [])
        return {"messages": ["legacy"]}, {"sources": [{"source": {"id": "legacy"}}]}

    monkeypatch.setattr(middleware, "chat_completion_files_handler", fake_files_handler)

    form_data, sources = middleware.asyncio.run(
        middleware.apply_legacy_file_retrieval_if_needed(
            request=_file_context_request(True),
            form_data={"messages": ["original"], "metadata": {}},
            extra_params={},
            user=types.SimpleNamespace(id="user-1"),
            model=_model(),
            metadata={
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
                "files": [
                    {
                        "id": "kb-1",
                        "type": "collection",
                        "source": "knowledge_attachment",
                    },
                    {
                        "id": "file-1",
                        "type": "file",
                        "name": "notes.txt",
                    },
                ],
            },
        )
    )

    assert captured["metadata_files"] == [{"id": "file-1", "type": "file", "name": "notes.txt"}]
    assert form_data["messages"] == ["legacy"]
    assert sources == [{"source": {"id": "legacy"}}]
