import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[4]
MIDDLEWARE_PATH = ROOT / "backend" / "open_webui" / "utils" / "middleware.py"


def _load_anchor_functions():
    source = MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "deep_merge",
            "merge_output_item_preserve_content",
            "merge_final_output_preserve_content",
            "handle_responses_streaming_event",
            "extract_anchor_state_from_response_payload",
        }:
            wanted[node.name] = node

    required = {
        "deep_merge",
        "merge_output_item_preserve_content",
        "merge_final_output_preserve_content",
        "handle_responses_streaming_event",
        "extract_anchor_state_from_response_payload",
    }
    if not required.issubset(wanted):
        raise RuntimeError("Required middleware anchor functions not found")

    module_ast = ast.Module(
        body=[
            wanted["deep_merge"],
            wanted["merge_output_item_preserve_content"],
            wanted["merge_final_output_preserve_content"],
            wanted["extract_anchor_state_from_response_payload"],
            wanted["handle_responses_streaming_event"],
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module_ast)

    namespace = {
        "Any": object,
        "_normalize_reasoning_item": lambda item: item,
        "_clone_output_item": lambda item: dict(item) if isinstance(item, dict) else item,
        "normalize_reasoning_output_items": lambda output: output,
    }
    exec(compile(module_ast, str(MIDDLEWARE_PATH), "exec"), namespace)
    return (
        namespace["extract_anchor_state_from_response_payload"],
        namespace["handle_responses_streaming_event"],
    )


def _load_non_streaming_anchor_handler():
    source = MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "extract_anchor_state_from_response_payload",
            "non_streaming_chat_response_handler",
        }:
            wanted[node.name] = node

    required = {
        "extract_anchor_state_from_response_payload",
        "non_streaming_chat_response_handler",
    }
    if not required.issubset(wanted):
        raise RuntimeError("Required middleware non-stream anchor functions not found")

    module_ast = ast.Module(
        body=[
            wanted["extract_anchor_state_from_response_payload"],
            wanted["non_streaming_chat_response_handler"],
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module_ast)

    async def _background_tasks_handler(_ctx):
        return None

    async def _post_webhook(*_args, **_kwargs):
        return None

    namespace = {
        "extract_reasoning_text": lambda _payload: "",
        "get_response_data": lambda response: (response, response),
        "merge_events_into_response": lambda response_data, _events: response_data,
        "build_response_object": lambda _response, response_data: response_data,
        "serialize_output": lambda output: f"serialized:{len(output)}",
        "normalize_usage": lambda usage: usage,
        "background_tasks_handler": _background_tasks_handler,
        "post_webhook": _post_webhook,
    }
    exec(compile(module_ast, str(MIDDLEWARE_PATH), "exec"), namespace)
    return namespace["non_streaming_chat_response_handler"]


def test_non_streaming_responses_anchor_state_is_extracted() -> None:
    extract_anchor_state, _ = _load_anchor_functions()

    anchor_state = extract_anchor_state(
        {
            "id": "resp_non_stream_123",
            "object": "response",
            "model": "gpt-5.4-mini",
        },
        provider_route="responses",
    )

    assert anchor_state == {
        "provider_response_id": "resp_non_stream_123",
        "provider_route": "responses",
        "anchor_valid": True,
        "anchor_model_id": "gpt-5.4-mini",
    }


def test_streaming_response_completed_exposes_anchor_state() -> None:
    _, handle_event = _load_anchor_functions()

    current_output = [
        {
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "partial"}],
        }
    ]

    event = {
        "type": "response.completed",
        "response": {
            "id": "resp_stream_456",
            "model": "gpt-5.4-mini",
            "output": [],
            "usage": {"total_tokens": 9},
        },
    }

    new_output, metadata = handle_event(event, current_output)

    assert new_output == current_output
    assert metadata == {
        "usage": {"total_tokens": 9},
        "done": True,
        "provider_response_id": "resp_stream_456",
        "provider_route": "responses",
        "anchor_valid": True,
        "anchor_model_id": "gpt-5.4-mini",
    }


@pytest.mark.asyncio
async def test_non_streaming_responses_output_without_choices_persists_anchor_state() -> None:
    handler = _load_non_streaming_anchor_handler()
    emitted_events = []
    upserts = []

    async def _event_emitter(event):
        emitted_events.append(event)

    class _ChatsStub:
        @staticmethod
        def upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, data):
            upserts.append((chat_id, message_id, data))

        @staticmethod
        def get_chat_title_by_id(_chat_id):
            return "Anchor Chat"

    class _UsersStub:
        @staticmethod
        def is_user_active(_user_id):
            return True

        @staticmethod
        def get_user_webhook_url_by_id(_user_id):
            return None

    handler.__globals__["Chats"] = _ChatsStub
    handler.__globals__["Users"] = _UsersStub

    response = {
        "id": "resp_non_stream_123",
        "object": "response",
        "model": "gpt-5.4-mini",
        "output": [
            {
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hello from output"}],
            }
        ],
        "usage": {"total_tokens": 9},
    }

    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="OpenWebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.invalid"),
                )
            )
        ),
        "form_data": {"model": "openai/gpt-5.4-mini"},
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "assistant-1",
            "provider_route": "responses",
        },
        "events": [],
        "event_emitter": _event_emitter,
    }

    result = await handler(response, ctx)

    assert result == response
    assert [event["type"] for event in emitted_events] == [
        "chat:completion",
        "chat:completion",
    ]
    assert len(upserts) == 1

    chat_id, message_id, stored_message = upserts[0]
    assert (chat_id, message_id) == ("chat-1", "assistant-1")
    assert stored_message["role"] == "assistant"
    assert stored_message["content"] == "serialized:1"
    assert stored_message["output"] == response["output"]
    assert stored_message["usage"] == {"total_tokens": 9}
    assert stored_message["provider_response_id"] == "resp_non_stream_123"
    assert stored_message["provider_route"] == "responses"
    assert stored_message["anchor_valid"] is True
    assert stored_message["anchor_model_id"] == "gpt-5.4-mini"
