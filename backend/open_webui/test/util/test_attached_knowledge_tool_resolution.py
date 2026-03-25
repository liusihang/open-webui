from open_webui.utils.tools import resolve_effective_knowledge_builtin_tools


def test_resolve_native_knowledge_tools_requires_enabled_query_and_scope() -> None:
    assert (
        resolve_effective_knowledge_builtin_tools(
            False, [{"id": "kb-1", "type": "collection"}]
        )
        == []
    )
    assert resolve_effective_knowledge_builtin_tools(True, []) == []
    assert (
        resolve_effective_knowledge_builtin_tools(
            True, [{"id": "kb-1", "type": "collection"}]
        )
        == [
            "query_knowledge_abstract",
            "query_knowledge_key_findings",
            "query_knowledge_key_data",
            "query_knowledge_full_text",
            "view_knowledge_layers",
            "view_file",
        ]
    )


def test_resolve_native_knowledge_tools_includes_note_viewer_only_when_needed() -> None:
    assert resolve_effective_knowledge_builtin_tools(
        True,
        [
            {"id": "kb-1", "type": "collection"},
            {"id": "note-1", "type": "note"},
        ],
    ) == [
        "query_knowledge_abstract",
        "query_knowledge_key_findings",
        "query_knowledge_key_data",
        "query_knowledge_full_text",
        "view_knowledge_layers",
        "view_file",
        "view_note",
    ]
