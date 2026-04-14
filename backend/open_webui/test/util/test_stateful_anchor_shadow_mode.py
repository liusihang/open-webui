import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MIDDLEWARE_PATH = ROOT / "backend" / "open_webui" / "utils" / "middleware.py"


def _load_stateful_anchor_helpers():
    source = MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_compute_stateful_anchor_shadow_decision",
            "_build_stateful_shadow_messages",
        }:
            wanted[node.name] = node

    required = {
        "_compute_stateful_anchor_shadow_decision",
        "_build_stateful_shadow_messages",
    }
    if not required.issubset(wanted):
        raise RuntimeError("Required stateful anchor helper functions not found")

    module_ast = ast.Module(
        body=[
            wanted["_compute_stateful_anchor_shadow_decision"],
            wanted["_build_stateful_shadow_messages"],
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module_ast)

    namespace = {}
    exec(compile(module_ast, str(MIDDLEWARE_PATH), "exec"), namespace)
    return (
        namespace["_compute_stateful_anchor_shadow_decision"],
        namespace["_build_stateful_shadow_messages"],
    )


def test_shadow_mode_eligible_on_linear_append_with_real_capture_model_id() -> None:
    compute_decision, _ = _load_stateful_anchor_helpers()

    messages_map = {
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
            "content": "follow up",
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

    assert decision == {
        "eligible": True,
        "reason": "eligible",
        "previous_response_id": "resp_prev_001",
        "anchor_message_id": "a1",
    }


def test_shadow_mode_payload_uses_system_and_latest_user_only() -> None:
    _, build_shadow_messages = _load_stateful_anchor_helpers()

    source_messages = [
        {"role": "system", "content": "you are concise"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "latest follow up"},
    ]

    assert build_shadow_messages(source_messages) == [
        {"role": "system", "content": "you are concise"},
        {"role": "user", "content": "latest follow up"},
    ]
