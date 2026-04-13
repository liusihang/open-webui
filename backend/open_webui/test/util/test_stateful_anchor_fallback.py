import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
MIDDLEWARE_PATH = ROOT / "backend" / "open_webui" / "utils" / "middleware.py"


def _load_stateful_anchor_helpers():
    source = MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_compute_stateful_anchor_shadow_decision":
            wanted[node.name] = node

    if "_compute_stateful_anchor_shadow_decision" not in wanted:
        raise RuntimeError("Required stateful anchor helper functions not found")

    module_ast = ast.Module(
        body=[wanted["_compute_stateful_anchor_shadow_decision"]],
        type_ignores=[],
    )
    ast.fix_missing_locations(module_ast)

    namespace = {}
    exec(compile(module_ast, str(MIDDLEWARE_PATH), "exec"), namespace)
    return namespace["_compute_stateful_anchor_shadow_decision"]


@pytest.mark.parametrize(
    "kwargs,expected_reason",
    [
        ({"feature_enabled": False}, "feature_disabled"),
        ({"provider_route": "chat_completions"}, "provider_route_not_responses"),
        ({"parent_message_id": "u1", "current_message_id": "u2"}, "non_linear_append"),
        ({"tools_present": True}, "tools_present"),
        ({"function_calling_mode": "native"}, "function_calling_not_supported"),
        ({"code_interpreter_enabled": True}, "code_interpreter_not_supported"),
        ({"requested_model_id": "openai/gpt-5.4"}, "model_changed"),
    ],
)
def test_shadow_mode_falls_back_for_unsafe_conditions(kwargs, expected_reason) -> None:
    compute_decision = _load_stateful_anchor_helpers()

    base_kwargs = {
        "feature_enabled": True,
        "provider_route": "responses",
        "current_message_id": "u2",
        "parent_message_id": "u2",
        "messages_map": {
            "a1": {
                "id": "a1",
                "role": "assistant",
                "provider_response_id": "resp_prev_001",
                "provider_route": "responses",
                "anchor_valid": True,
                "anchor_model_id": "openai/gpt-5.4-mini",
            },
            "u2": {
                "id": "u2",
                "role": "user",
                "parentId": "a1",
            },
        },
        "requested_model_id": "openai/gpt-5.4-mini",
        "tools_present": False,
        "function_calling_mode": "default",
        "code_interpreter_enabled": False,
    }
    base_kwargs.update(kwargs)

    decision = compute_decision(**base_kwargs)

    assert decision["eligible"] is False
    assert decision["reason"] == expected_reason
    assert decision.get("previous_response_id") is None


def test_shadow_mode_falls_back_when_anchor_is_missing_or_invalid() -> None:
    compute_decision = _load_stateful_anchor_helpers()

    messages_map = {
        "a1": {
            "id": "a1",
            "role": "assistant",
            "provider_response_id": "",
            "provider_route": "responses",
            "anchor_valid": False,
            "anchor_model_id": "openai/gpt-5.4-mini",
        },
        "u2": {
            "id": "u2",
            "role": "user",
            "parentId": "a1",
        },
    }

    decision = compute_decision(
        feature_enabled=True,
        provider_route="responses",
        current_message_id="u2",
        parent_message_id="u2",
        messages_map=messages_map,
        requested_model_id="openai/gpt-5.4-mini",
        tools_present=False,
        function_calling_mode="default",
        code_interpreter_enabled=False,
    )

    assert decision["eligible"] is False
    assert decision["reason"] == "anchor_invalid"


def test_shadow_mode_accepts_bare_provider_model_id_against_prefixed_request_model() -> None:
    compute_decision = _load_stateful_anchor_helpers()

    decision = compute_decision(
        feature_enabled=True,
        provider_route="responses",
        current_message_id="u2",
        parent_message_id="u2",
        messages_map={
            "a1": {
                "id": "a1",
                "role": "assistant",
                "provider_response_id": "resp_prev_001",
                "provider_route": "responses",
                "anchor_valid": True,
                "anchor_model_id": "gpt-5.4-mini",
            },
            "u2": {
                "id": "u2",
                "role": "user",
                "parentId": "a1",
            },
        },
        requested_model_id="openai/gpt-5.4-mini",
        tools_present=False,
        function_calling_mode="default",
        code_interpreter_enabled=False,
    )

    assert decision == {
        "eligible": True,
        "reason": "eligible",
        "previous_response_id": "resp_prev_001",
        "anchor_message_id": "a1",
    }
