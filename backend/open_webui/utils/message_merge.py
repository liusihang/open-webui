import json
from typing import Any


def _message_signature(message: Any) -> str:
    if not isinstance(message, dict):
        return ""

    message_id = message.get("id")
    if isinstance(message_id, str) and message_id:
        return f"id:{message_id}"

    role = message.get("role")
    content = message.get("content")
    try:
        content_blob = json.dumps(content, sort_keys=True, ensure_ascii=True)
    except TypeError:
        content_blob = str(content)

    return f"role:{role}|content:{content_blob}"


def merge_messages_preserving_incoming_tail(
    db_messages: list[dict[str, Any]] | None,
    incoming_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge DB chain with incoming request while preserving newest incoming turns.

    DB history is authoritative for previous turns, but in-flight incoming turns may
    not be persisted yet (especially multimodal user content). This function appends
    only the missing incoming tail after the latest matching DB message.
    """

    db_chain = [msg for msg in (db_messages or []) if isinstance(msg, dict)]
    incoming_chain = [msg for msg in (incoming_messages or []) if isinstance(msg, dict)]

    if not db_chain:
        return incoming_chain
    if not incoming_chain:
        return db_chain

    db_last_sig = _message_signature(db_chain[-1])
    if not db_last_sig:
        return db_chain

    last_match_index = -1
    for idx, message in enumerate(incoming_chain):
        if _message_signature(message) == db_last_sig:
            last_match_index = idx

    tail = incoming_chain[last_match_index + 1 :] if last_match_index >= 0 else incoming_chain
    if not tail:
        return db_chain

    merged = [*db_chain]
    existing_signatures = {_message_signature(msg) for msg in db_chain}
    for message in tail:
        signature = _message_signature(message)
        if signature and signature not in existing_signatures:
            merged.append(message)
            existing_signatures.add(signature)

    return merged
