import re
from typing import Any


def _is_banned_reasoning_token(token: Any) -> bool:
    if not isinstance(token, str):
        return False
    normalized = re.sub(r'[^A-Z0-9]', '', token.strip().upper())
    return normalized == 'REASONINGENCRYPTEDCONTENT'


def _prune_banned_reasoning_tokens(value: Any) -> tuple[Any, int]:
    removed = 0

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            if _is_banned_reasoning_token(key):
                removed += 1
                continue

            if key == 'required' and isinstance(nested, list):
                filtered_required = []
                for required_item in nested:
                    if _is_banned_reasoning_token(required_item):
                        removed += 1
                        continue
                    filtered_required.append(required_item)
                sanitized[key] = filtered_required
                continue

            cleaned_nested, nested_removed = _prune_banned_reasoning_tokens(nested)
            removed += nested_removed
            sanitized[key] = cleaned_nested
        return sanitized, removed

    if isinstance(value, list):
        sanitized_list = []
        for item in value:
            if _is_banned_reasoning_token(item):
                removed += 1
                continue
            cleaned_item, nested_removed = _prune_banned_reasoning_tokens(item)
            removed += nested_removed
            sanitized_list.append(cleaned_item)
        return sanitized_list, removed

    return value, 0


def sanitize_openai_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    sanitized, removed = _prune_banned_reasoning_tokens(payload)
    if isinstance(sanitized, dict):
        return sanitized, removed
    return payload, removed


def dedupe_system_messages(messages: Any) -> tuple[Any, int]:
    if not isinstance(messages, list):
        return messages, 0

    deduped: list[Any] = []
    has_system = False
    removed = 0

    for message in messages:
        if isinstance(message, dict) and message.get('role') == 'system':
            if has_system:
                removed += 1
                continue
            has_system = True
        deduped.append(message)

    return deduped, removed
