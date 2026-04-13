import ast
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[4]
MIDDLEWARE_PATH = ROOT / "backend" / "open_webui" / "utils" / "middleware.py"


def _load_shadow_mode_orchestrator():
    source = MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    ordered_names = [
        "_is_openai_new_model_for_stateful",
        "_resolve_provider_route_for_chat_request",
        "_compute_stateful_anchor_shadow_decision",
        "_build_stateful_shadow_messages",
        "_apply_stateful_anchor_shadow_mode",
    ]
    wanted = {}

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ordered_names:
            wanted[node.name] = node

    missing = [name for name in ordered_names if name not in wanted]
    if missing:
        raise RuntimeError(f"Required middleware shadow functions not found: {missing}")

    module_ast = ast.Module(body=[wanted[name] for name in ordered_names], type_ignores=[])
    ast.fix_missing_locations(module_ast)

    namespace = {"re": re}
    exec(compile(module_ast, str(MIDDLEWARE_PATH), "exec"), namespace)
    return namespace


def _make_request(api_type: str = "responses"):
    config = SimpleNamespace(
        OPENAI_API_CONFIGS={"0": {"api_type": api_type}},
        OPENAI_API_BASE_URLS=["https://api.openai.com/v1"],
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def test_apply_stateful_anchor_shadow_mode_integration_rewrite_and_fallback() -> None:
    namespace = _load_shadow_mode_orchestrator()
    apply_shadow_mode = namespace["_apply_stateful_anchor_shadow_mode"]

    messages_map = {
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
            "content": "latest follow up",
        },
    }

    chat = SimpleNamespace(chat={"history": {"messages": messages_map, "currentId": "u2"}})

    class _ChatsStub:
        @staticmethod
        def get_chat_by_id(chat_id):
            return chat if chat_id == "chat-1" else None

    apply_shadow_mode.__globals__["Chats"] = _ChatsStub
    apply_shadow_mode.__globals__["ENABLE_RESPONSES_API_STATEFUL"] = True

    request = _make_request()
    model = {"owned_by": "openai", "urlIdx": 0}

    base_form_data = {
        "model": "openai/gpt-5.4-mini",
        "messages": [
            {"role": "system", "content": "you are concise"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "latest follow up"},
        ],
        "previous_response_id": "stale_prev_resp",
    }

    eligible_form_data = deepcopy(base_form_data)
    eligible_metadata = {
        "chat_id": "chat-1",
        "parent_message_id": "u2",
        "params": {},
        "features": {},
    }
    eligible_result = apply_shadow_mode(request, eligible_form_data, eligible_metadata, model)

    assert eligible_result["messages"] == [
        {"role": "system", "content": "you are concise"},
        {"role": "user", "content": "latest follow up"},
    ]
    assert eligible_result["previous_response_id"] == "resp_prev_001"
    assert eligible_metadata["provider_route"] == "responses"
    assert eligible_metadata["stateful_anchor_reason"] == "eligible"
    assert eligible_metadata["stateful_anchor_anchor_message_id"] == "a1"
    assert eligible_metadata["stateful_anchor_previous_response_id"] == "resp_prev_001"

    fallback_form_data = deepcopy(base_form_data)
    fallback_messages_before = deepcopy(fallback_form_data["messages"])
    fallback_metadata = {
        "chat_id": "chat-1",
        "parent_message_id": "u1",
        "params": {},
        "features": {},
    }
    fallback_result = apply_shadow_mode(request, fallback_form_data, fallback_metadata, model)

    assert fallback_result["messages"] == fallback_messages_before
    assert "previous_response_id" not in fallback_result
    assert fallback_metadata["provider_route"] == "responses"
    assert fallback_metadata["stateful_anchor_reason"] == "non_linear_append"
    assert "stateful_anchor_previous_response_id" not in fallback_metadata


def test_apply_stateful_anchor_shadow_mode_infers_responses_route_for_prefixed_models() -> None:
    namespace = _load_shadow_mode_orchestrator()
    apply_shadow_mode = namespace["_apply_stateful_anchor_shadow_mode"]

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
            "content": "latest follow up",
        },
    }

    chat = SimpleNamespace(chat={"history": {"messages": messages_map, "currentId": "u2"}})

    class _ChatsStub:
        @staticmethod
        def get_chat_by_id(chat_id):
            return chat if chat_id == "chat-1" else None

    apply_shadow_mode.__globals__["Chats"] = _ChatsStub
    apply_shadow_mode.__globals__["ENABLE_RESPONSES_API_STATEFUL"] = True

    request = _make_request(api_type="")
    model = {"owned_by": "openai", "urlIdx": 0}

    for model_id in ("openai/gpt-5.4-mini", "openai/o3"):
        form_data = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": "you are concise"},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "latest follow up"},
            ],
        }
        metadata = {
            "chat_id": "chat-1",
            "parent_message_id": "u2",
            "params": {},
            "features": {},
        }

        result = apply_shadow_mode(request, form_data, metadata, model)

        assert metadata["provider_route"] == "responses"
        if model_id == "openai/gpt-5.4-mini":
            assert result["previous_response_id"] == "resp_prev_001"
            assert metadata["stateful_anchor_reason"] == "eligible"
        else:
            assert "previous_response_id" not in result
            assert metadata["stateful_anchor_reason"] == "model_changed"


def test_apply_stateful_anchor_shadow_mode_clears_stale_previous_response_id_on_early_returns() -> None:
    namespace = _load_shadow_mode_orchestrator()
    apply_shadow_mode = namespace["_apply_stateful_anchor_shadow_mode"]

    class _ChatsStub:
        @staticmethod
        def get_chat_by_id(_chat_id):
            return None

    apply_shadow_mode.__globals__["Chats"] = _ChatsStub
    apply_shadow_mode.__globals__["ENABLE_RESPONSES_API_STATEFUL"] = True

    request = _make_request(api_type="")
    model = {"owned_by": "openai", "urlIdx": 0}

    local_form_data = {
        "model": "openai/gpt-5.4-mini",
        "messages": [{"role": "user", "content": "latest follow up"}],
        "previous_response_id": "stale_prev_resp",
    }
    local_metadata = {
        "chat_id": "local:chat-1",
        "parent_message_id": "u2",
        "params": {},
        "features": {},
    }

    local_result = apply_shadow_mode(request, deepcopy(local_form_data), local_metadata, model)

    assert "previous_response_id" not in local_result
    assert local_metadata["provider_route"] == "responses"
    assert "stateful_anchor_reason" not in local_metadata

    missing_chat_metadata = {
        "chat_id": "chat-missing",
        "parent_message_id": "u2",
        "params": {},
        "features": {},
    }
    missing_chat_result = apply_shadow_mode(
        request,
        deepcopy(local_form_data),
        missing_chat_metadata,
        model,
    )

    assert "previous_response_id" not in missing_chat_result
    assert missing_chat_metadata["provider_route"] == "responses"
    assert "stateful_anchor_reason" not in missing_chat_metadata
