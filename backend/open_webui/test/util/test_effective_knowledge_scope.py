from open_webui.utils.tools import (
    build_effective_knowledge_query_enabled,
    build_effective_knowledge_scope,
)


def test_build_effective_knowledge_scope_merges_model_and_attached() -> None:
    metadata = {
        "attached_knowledge_scope": [{"id": "kb-2", "type": "collection"}],
    }
    model_knowledge = [{"id": "kb-1", "type": "collection"}]

    scope = build_effective_knowledge_scope(metadata, model_knowledge)

    assert scope == [
        {"id": "kb-1", "type": "collection"},
        {"id": "kb-2", "type": "collection"},
    ]


def test_build_effective_knowledge_scope_treats_missing_inputs_as_empty() -> None:
    assert build_effective_knowledge_scope({}, None) == []
    assert build_effective_knowledge_scope({}, False) == []


def test_build_effective_knowledge_query_enabled_auto_enables_for_scope() -> None:
    assert build_effective_knowledge_query_enabled(False, [], []) is False
    assert (
        build_effective_knowledge_query_enabled(
            False,
            [{"id": "kb-1", "type": "collection"}],
            [],
        )
        is True
    )
    assert (
        build_effective_knowledge_query_enabled(
            False,
            [],
            [{"id": "kb-2", "type": "collection"}],
        )
        is True
    )
    assert build_effective_knowledge_query_enabled(True, [], []) is True
