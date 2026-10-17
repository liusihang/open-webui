"""Stable, missing-only cache routing keys for saved native GPT chats."""

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from open_webui.utils.chat_id import is_saved_chat_id


def apply_default_prompt_cache_key(
    payload: dict,
    metadata: dict | None,
    *,
    api_config: dict,
    user_id: str | None,
    connection_index: int,
    base_url: str,
) -> dict:
    """Preserve explicit values; derive no fallback without durable chat identity.

    The caller supplies the verified user's ID and final provider model after
    parameter merging. This hash is a routing hint, not an authorization boundary.
    """
    if 'prompt_cache_key' in payload:
        return payload
    metadata = metadata or {}
    chat_id = metadata.get('chat_id')
    model = payload.get('model')
    if (
        api_config.get('api_type') != 'responses'
        or metadata.get('task')
        or metadata.get('internal')
        or metadata.get('direct')
        or not isinstance(user_id, str)
        or not user_id.strip()
        or not isinstance(chat_id, str)
        or not chat_id.strip()
        or not is_saved_chat_id(chat_id)
        or not isinstance(model, str)
        or not isinstance(connection_index, int)
        or connection_index < 0
        or not isinstance(base_url, str)
    ):
        return payload
    # Recognize actual GPT IDs, not display names or guessed provider aliases.
    match = re.match(r'^gpt-(\d+)', model.lower())
    if not match or int(match.group(1)) < 4:
        return payload
    parts = urlsplit(base_url)
    if parts.scheme.lower() not in ('http', 'https') or not parts.netloc:
        return payload
    # Legacy Azure deployment URLs have a different parameter schema. Azure v1
    # uses the ordinary Responses schema and retains the actual model in body.
    if (api_config.get('azure') or api_config.get('provider') == 'azure') and not re.search(
        r'/openai/v1(?:/|$)', parts.path
    ):
        return payload
    normalized_url = urlunsplit(
        parts._replace(
            scheme=parts.scheme.lower(), netloc=parts.netloc.lower(), path=parts.path.rstrip('/'), fragment=''
        )
    )
    scope = [user_id, chat_id, connection_index, normalized_url, model]
    digest = hashlib.sha256(json.dumps(scope, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    namespace = 'owui:pc:v1:'
    return {**payload, 'prompt_cache_key': namespace + digest[: 64 - len(namespace)]}
