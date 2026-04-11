import types

from open_webui.utils import middleware


def _config(enabled: bool):
    return types.SimpleNamespace(
        NATIVE_ATTACHED_KNOWLEDGE_BYPASS_LEGACY_FILE_RETRIEVAL=enabled
    )


def _model(*, builtin_tools: bool = True, knowledge_builtin: bool = True) -> dict:
    return {
        "info": {
            "meta": {
                "capabilities": {"builtin_tools": builtin_tools},
                "builtinTools": {"knowledge": knowledge_builtin},
            }
        }
    }


def test_skip_gate_returns_false_when_admin_toggle_disabled() -> None:
    assert (
        middleware.should_skip_legacy_file_retrieval_for_native_scoped_knowledge(
            _config(False),
            {
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
            },
            _model(),
        )
        is False
    )


def test_skip_gate_returns_false_when_function_calling_is_not_native() -> None:
    assert (
        middleware.should_skip_legacy_file_retrieval_for_native_scoped_knowledge(
            _config(True),
            {
                "params": {"function_calling": "default"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
            },
            _model(),
        )
        is False
    )


def test_skip_gate_returns_false_when_effective_scope_is_empty() -> None:
    assert (
        middleware.should_skip_legacy_file_retrieval_for_native_scoped_knowledge(
            _config(True),
            {
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [],
            },
            _model(),
        )
        is False
    )


def test_skip_gate_returns_true_for_native_scoped_knowledge_when_enabled() -> None:
    assert (
        middleware.should_skip_legacy_file_retrieval_for_native_scoped_knowledge(
            _config(True),
            {
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
            },
            _model(),
        )
        is True
    )


def test_skip_gate_returns_false_when_model_builtin_tools_capability_is_disabled() -> None:
    assert (
        middleware.should_skip_legacy_file_retrieval_for_native_scoped_knowledge(
            _config(True),
            {
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
            },
            _model(builtin_tools=False),
        )
        is False
    )


def test_skip_gate_returns_false_when_knowledge_builtin_category_is_disabled() -> None:
    assert (
        middleware.should_skip_legacy_file_retrieval_for_native_scoped_knowledge(
            _config(True),
            {
                "params": {"function_calling": "native"},
                "effective_knowledge_query_enabled": True,
                "effective_knowledge_scope": [{"id": "kb-1", "type": "collection"}],
            },
            _model(knowledge_builtin=False),
        )
        is False
    )
