import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MESSAGE_MERGE_PATH = ROOT / "backend" / "open_webui" / "utils" / "message_merge.py"


def _load_merge_module():
    spec = importlib.util.spec_from_file_location("message_merge_test", MESSAGE_MERGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {MESSAGE_MERGE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_merge_keeps_incoming_tail_after_last_db_match() -> None:
    mod = _load_merge_module()
    db_messages = [
        {"id": "m1", "role": "user", "content": "old"},
        {"id": "m2", "role": "assistant", "content": "ack"},
    ]
    incoming_messages = [
        {"id": "m1", "role": "user", "content": "old"},
        {"id": "m2", "role": "assistant", "content": "ack"},
        {
            "id": "m3",
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "11111111-1111-1111-1111-111111111111"}},
            ],
        },
    ]

    merged = mod.merge_messages_preserving_incoming_tail(db_messages, incoming_messages)

    assert len(merged) == 3
    assert merged[-1]["id"] == "m3"
    assert isinstance(merged[-1]["content"], list)
    assert merged[-1]["content"][1]["type"] == "image_url"


def test_merge_returns_db_when_incoming_is_prefix_only() -> None:
    mod = _load_merge_module()
    db_messages = [
        {"id": "m1", "role": "user", "content": "old"},
        {"id": "m2", "role": "assistant", "content": "ack"},
    ]
    incoming_messages = [
        {"id": "m1", "role": "user", "content": "old"},
        {"id": "m2", "role": "assistant", "content": "ack"},
    ]

    merged = mod.merge_messages_preserving_incoming_tail(db_messages, incoming_messages)
    assert merged == db_messages


def test_merge_keeps_repeated_user_tail_without_ids() -> None:
    mod = _load_merge_module()
    db_messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ack"},
    ]
    incoming_messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "hi"},
    ]

    merged = mod.merge_messages_preserving_incoming_tail(db_messages, incoming_messages)

    assert len(merged) == 3
    assert merged[-1]["role"] == "user"
    assert merged[-1]["content"] == "hi"
