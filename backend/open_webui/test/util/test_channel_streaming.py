import ast
import json
import types
from pathlib import Path

import pytest
from fastapi.responses import StreamingResponse


ROOT = Path(__file__).resolve().parents[4]
CHANNELS_PATH = ROOT / "backend" / "open_webui" / "routers" / "channels.py"


class MessageForm:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

        self.temp_id = kwargs.get("temp_id")
        self.content = kwargs.get("content", "")
        self.reply_to_id = kwargs.get("reply_to_id")
        self.parent_id = kwargs.get("parent_id")
        self.data = kwargs.get("data")
        self.meta = kwargs.get("meta")


def _load_channel_namespace():
    source = CHANNELS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "build_stream_meta",
            "update_streaming_channel_message",
            "extract_stream_text",
            "stream_native_channel_completion",
            "model_response_handler",
        }:
            wanted[node.name] = node

    if "model_response_handler" not in wanted:
        raise RuntimeError("model_response_handler not found in channels.py")

    ordered_nodes = [
        wanted[name]
        for name in [
            "build_stream_meta",
            "update_streaming_channel_message",
            "extract_stream_text",
            "stream_native_channel_completion",
            "model_response_handler",
        ]
        if name in wanted
    ]

    module_ast = ast.Module(body=ordered_nodes, type_ignores=[])
    ast.fix_missing_locations(module_ast)

    noop_logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )
    namespace = {
        "json": json,
        "time": __import__("time"),
        "Request": object,
        "StreamingResponse": StreamingResponse,
        "MessageForm": MessageForm,
        "get_all_models": None,
        "get_filtered_models": None,
        "extract_mentions": None,
        "replace_mentions": None,
        "Messages": types.SimpleNamespace(),
        "Users": types.SimpleNamespace(),
        "new_message_handler": None,
        "update_message_by_id": None,
        "generate_chat_completion": None,
        "get_image_base64_from_file_id": lambda file_id: None,
        "log": noop_logger,
    }
    exec(compile(module_ast, str(CHANNELS_PATH), "exec"), namespace)
    return namespace


def _make_request():
    return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace()))


def _make_user():
    return types.SimpleNamespace(id="user-1", name="Alice", role="user")


def _make_channel():
    return types.SimpleNamespace(id="channel-1", type="group")


def _make_inbound_message(content="@model-1 hello", *, parent_id=None, reply_to_message=None):
    return types.SimpleNamespace(
        id="user-msg-1",
        channel_id="channel-1",
        user_id="user-1",
        parent_id=parent_id,
        reply_to_id=None,
        reply_to_message=reply_to_message,
        content=content,
        data={},
        meta={},
    )


def _install_common_patches(ns, *, mentions=None):
    model = {"id": "model-1", "name": "Model One"}
    mention_list = mentions if mentions is not None else [{"id": "model-1", "id_type": "M"}]

    async def fake_get_all_models(request, user=None):
        return [model]

    def fake_get_filtered_models(models, user):
        return models

    def fake_extract_mentions(content):
        return mention_list

    def fake_replace_mentions(content):
        return content.replace("@model-1", "").strip()

    ns["get_all_models"] = fake_get_all_models
    ns["get_filtered_models"] = fake_get_filtered_models
    ns["extract_mentions"] = fake_extract_mentions
    ns["replace_mentions"] = fake_replace_mentions
    ns["Messages"] = types.SimpleNamespace(
        get_messages_by_parent_id=lambda channel_id, parent_id, db=None: []
    )
    ns["Users"] = types.SimpleNamespace(
        get_user_by_id=lambda user_id, db=None: types.SimpleNamespace(name="Alice")
    )


@pytest.mark.asyncio
async def test_channel_model_streaming_updates_same_message():
    ns = _load_channel_namespace()
    request = _make_request()
    channel = _make_channel()
    user = _make_user()
    inbound_message = _make_inbound_message()

    _install_common_patches(ns)

    placeholders = []
    updates = []

    async def fake_new_message_handler(request, channel_id, form_data, user, db):
        placeholders.append(form_data)
        message = types.SimpleNamespace(
            id="assistant-msg-1",
            channel_id=channel_id,
            parent_id=form_data.parent_id,
            reply_to_id=form_data.reply_to_id,
            content=form_data.content,
            data=form_data.data or {},
            meta=form_data.meta or {},
            user_id=user.id,
        )
        return message, channel

    async def fake_update_message_by_id(request, channel_id, message_id, form_data, user, db):
        updates.append(
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "content": form_data.content,
                "meta": form_data.meta or {},
            }
        )
        return types.SimpleNamespace(id=message_id, content=form_data.content, meta=form_data.meta or {})

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        async def body():
            yield b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"completion_tokens":2}}\n\n'

        return StreamingResponse(body(), media_type="text/event-stream")

    ns["new_message_handler"] = fake_new_message_handler
    ns["update_message_by_id"] = fake_update_message_by_id
    ns["generate_chat_completion"] = fake_generate_chat_completion

    await ns["model_response_handler"](request, channel, inbound_message, user)

    assert placeholders[0].meta["streaming"] is True
    assert placeholders[0].meta["done"] is False
    assert updates[0]["message_id"] == "assistant-msg-1"
    assert updates[0]["meta"]["streaming"] is True
    assert updates[-1]["content"] == "Hello"
    assert updates[-1]["meta"]["done"] is True
    assert updates[-1]["meta"]["stream_source"] == "native-channel-ai"


@pytest.mark.asyncio
async def test_channel_model_streaming_marks_error_on_exception():
    ns = _load_channel_namespace()
    request = _make_request()
    channel = _make_channel()
    user = _make_user()
    inbound_message = _make_inbound_message()

    _install_common_patches(ns)

    updates = []

    async def fake_new_message_handler(request, channel_id, form_data, user, db):
        message = types.SimpleNamespace(
            id="assistant-msg-err",
            channel_id=channel_id,
            parent_id=form_data.parent_id,
            reply_to_id=form_data.reply_to_id,
            content=form_data.content,
            data=form_data.data or {},
            meta=form_data.meta or {},
            user_id=user.id,
        )
        return message, channel

    async def fake_update_message_by_id(request, channel_id, message_id, form_data, user, db):
        updates.append(
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "content": form_data.content,
                "meta": form_data.meta or {},
            }
        )
        return types.SimpleNamespace(id=message_id, content=form_data.content, meta=form_data.meta or {})

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        async def body():
            yield b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            raise RuntimeError("stream exploded")

        return StreamingResponse(body(), media_type="text/event-stream")

    ns["new_message_handler"] = fake_new_message_handler
    ns["update_message_by_id"] = fake_update_message_by_id
    ns["generate_chat_completion"] = fake_generate_chat_completion

    await ns["model_response_handler"](request, channel, inbound_message, user)

    assert updates[-1]["content"] == "Hel"
    assert updates[-1]["meta"]["streaming"] is False
    assert updates[-1]["meta"]["done"] is True
    assert "error" in updates[-1]["meta"]


@pytest.mark.asyncio
async def test_channel_model_streaming_accepts_non_stream_json_response():
    ns = _load_channel_namespace()
    request = _make_request()
    channel = _make_channel()
    user = _make_user()
    inbound_message = _make_inbound_message()

    _install_common_patches(ns)

    placeholders = []
    updates = []

    async def fake_new_message_handler(request, channel_id, form_data, user, db):
        placeholders.append(form_data)
        message = types.SimpleNamespace(
            id="assistant-msg-json",
            channel_id=channel_id,
            parent_id=form_data.parent_id,
            reply_to_id=form_data.reply_to_id,
            content=form_data.content,
            data=form_data.data or {},
            meta=form_data.meta or {},
            user_id=user.id,
        )
        return message, channel

    async def fake_update_message_by_id(request, channel_id, message_id, form_data, user, db):
        updates.append(
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "content": form_data.content,
                "meta": form_data.meta or {},
            }
        )
        return types.SimpleNamespace(id=message_id, content=form_data.content, meta=form_data.meta or {})

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        return {
            "choices": [{"message": {"content": "final answer"}}],
            "usage": {"completion_tokens": 4},
        }

    ns["new_message_handler"] = fake_new_message_handler
    ns["update_message_by_id"] = fake_update_message_by_id
    ns["generate_chat_completion"] = fake_generate_chat_completion

    await ns["model_response_handler"](request, channel, inbound_message, user)

    assert placeholders[0].meta["streaming"] is True
    assert placeholders[0].meta["done"] is False
    assert updates[-1]["content"] == "final answer"
    assert updates[-1]["meta"]["done"] is True
    assert updates[-1]["meta"]["usage"] == {"completion_tokens": 4}


@pytest.mark.asyncio
async def test_channel_thread_streaming_updates_thread_message():
    ns = _load_channel_namespace()
    request = _make_request()
    channel = _make_channel()
    user = _make_user()
    root_message = types.SimpleNamespace(id="root-msg-1")
    inbound_message = _make_inbound_message(parent_id=root_message.id)

    _install_common_patches(ns)

    placeholders = []
    updates = []

    async def fake_new_message_handler(request, channel_id, form_data, user, db):
        placeholders.append(form_data)
        message = types.SimpleNamespace(
            id="assistant-thread-msg-1",
            channel_id=channel_id,
            parent_id=form_data.parent_id,
            reply_to_id=form_data.reply_to_id,
            content=form_data.content,
            data=form_data.data or {},
            meta=form_data.meta or {},
            user_id=user.id,
        )
        return message, channel

    async def fake_update_message_by_id(request, channel_id, message_id, form_data, user, db):
        updates.append(
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "content": form_data.content,
                "meta": form_data.meta or {},
            }
        )
        return types.SimpleNamespace(id=message_id, content=form_data.content, meta=form_data.meta or {})

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        async def body():
            yield b'data: {"choices":[{"delta":{"content":"thread reply"}}]}\n\n'

        return StreamingResponse(body(), media_type="text/event-stream")

    ns["new_message_handler"] = fake_new_message_handler
    ns["update_message_by_id"] = fake_update_message_by_id
    ns["generate_chat_completion"] = fake_generate_chat_completion

    await ns["model_response_handler"](request, channel, inbound_message, user)

    assert placeholders[0].parent_id == root_message.id
    assert updates[-1]["message_id"] == "assistant-thread-msg-1"


@pytest.mark.asyncio
async def test_channel_streaming_ignores_non_json_lines_and_finalizes():
    ns = _load_channel_namespace()
    request = _make_request()
    channel = _make_channel()
    user = _make_user()
    inbound_message = _make_inbound_message()

    _install_common_patches(ns)

    updates = []

    async def fake_new_message_handler(request, channel_id, form_data, user, db):
        message = types.SimpleNamespace(
            id="assistant-malformed-msg-1",
            channel_id=channel_id,
            parent_id=form_data.parent_id,
            reply_to_id=form_data.reply_to_id,
            content=form_data.content,
            data=form_data.data or {},
            meta=form_data.meta or {},
            user_id=user.id,
        )
        return message, channel

    async def fake_update_message_by_id(request, channel_id, message_id, form_data, user, db):
        updates.append(
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "content": form_data.content,
                "meta": form_data.meta or {},
            }
        )
        return types.SimpleNamespace(id=message_id, content=form_data.content, meta=form_data.meta or {})

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        async def body():
            yield "event: ping\n\n"
            yield "data: {not-json}\n\n"
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'

        return StreamingResponse(body(), media_type="text/event-stream")

    ns["new_message_handler"] = fake_new_message_handler
    ns["update_message_by_id"] = fake_update_message_by_id
    ns["generate_chat_completion"] = fake_generate_chat_completion

    await ns["model_response_handler"](request, channel, inbound_message, user)

    assert updates[-1]["content"] == "ok"
    assert updates[-1]["meta"]["done"] is True
