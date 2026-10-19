"""Deterministic, request-only pruning of completed tool results after idle.

The transcript is the source of truth. Durable masks contain identities and
character offsets, never replacement transcripts or generated summaries.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
from functools import lru_cache

log = logging.getLogger(__name__)
PLACEHOLDER = ''
TRUNCATED = '\n[truncated]'
TERMINAL = {'completed', 'failed', 'rejected'}


@lru_cache(maxsize=128)
def resolve_encoding(model_id: str):
    """Use a real tokenizer, explicitly distinguishing unknown provider models."""
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model_id)
    except KeyError:
        log.warning(
            'Idle tool pruning: no tokenizer mapping for %s; using cl100k_base. '
            'This is a local token budget, not an exact provider token count.',
            model_id,
        )
        return tiktoken.get_encoding('cl100k_base')


def text_prefix(text: str, budget: int, encoding) -> tuple[str, int]:
    if budget <= 0:
        return '', 0
    # We only need a prefix. Avoid tokenizing megabytes of already-discarded
    # source text, especially pathological unbroken tool dumps.
    end = min(len(text), max(256, budget * 16))
    tokens = encoding.encode(text[:end], disallowed_special=())
    while end < len(text) and len(tokens) <= budget:
        end = min(len(text), end * 2)
        tokens = encoding.encode(text[:end], disallowed_special=())
    if end == len(text) and len(tokens) <= budget:
        return text, len(tokens)
    # A token may end partway through a UTF-8 codepoint. Never insert U+FFFD.
    prefix = encoding.decode_bytes(tokens[:budget]).decode('utf-8', errors='ignore') if budget else ''
    while prefix and len(encoding.encode(prefix, disallowed_special=())) > budget:
        prefix = prefix[:-1]
    return prefix, len(encoding.encode(prefix, disallowed_special=()))


def result_text(item: dict) -> tuple[str, bool]:
    """Match replay text; old non-text result parts cannot be token-budgeted safely."""
    value = item.get('output')
    if item.get('type') == 'open_webui:code_interpreter':
        if isinstance(value, dict):
            value = value.get('stdout') or value.get('result') or ''
        return str(value or ''), False
    if isinstance(value, str):
        return value, False
    if isinstance(value, list):
        text = ''.join(
            str(part.get('text', '')) for part in value if isinstance(part, dict) and part.get('type') == 'input_text'
        )
        nontext = any(not isinstance(part, dict) or part.get('type') != 'input_text' for part in value)
        return text, nontext
    return '', value is not None


def result_key(message: dict, item: dict, index: int) -> str:
    # Stable identities survive commentary insertions. Position is only a
    # fallback for legacy items without an ID. Edited results are new evidence.
    identity = item.get('id') or item.get('call_id') or index
    value = [message['id'], item.get('type'), identity, item.get('output')]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def has_pending_assistant(messages: list[dict]) -> bool:
    for message in messages:
        if message.get('role') != 'assistant':
            continue
        if message.get('done') is not True:
            return True
        items = message.get('output') or []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get('status') in {
                'in_progress',
                'pending',
                'queued',
                'requires_approval',
            }:
                return True
    return False


def completed_results(messages: list[dict]):
    for message in messages:
        if message.get('role') != 'assistant' or message.get('done') is not True or not message.get('id'):
            continue
        output = message.get('output')
        if not isinstance(output, list):
            continue
        items = [item for item in output if isinstance(item, dict)]
        calls = {item.get('call_id'): item for item in items if item.get('type') == 'function_call'}
        results = {item.get('call_id') for item in items if item.get('type') == 'function_call_output'}
        # Protect a whole unfinished/approval batch, even if another result in
        # that batch has already arrived, or a stale done flag says otherwise.
        if any(call.get('status') not in TERMINAL or call_id not in results for call_id, call in calls.items()):
            continue
        if any(item.get('status') in {'in_progress', 'pending', 'queued', 'requires_approval'} for item in items):
            continue
        for index, item in enumerate(output):
            if not isinstance(item, dict):
                continue
            kind = item.get('type')
            if kind == 'function_call_output':
                if item.get('call_id') not in calls or item.get('status') not in (None, *TERMINAL):
                    continue
            elif kind == 'open_webui:code_interpreter':
                if item.get('status') != 'completed' or 'output' not in item:
                    continue
            else:
                continue
            yield message, item, index


def plan_masks(messages: list[dict], masks: dict, budget: int, model_id: str) -> tuple[dict, str]:
    """Allocate one aggregate budget newest-first; masks can only shrink."""
    try:
        encoding = resolve_encoding(model_id)
    except Exception:
        # Never use character heuristics or fail open with the original payload
        # when tokenizer data is unavailable (for example an offline first run).
        log.exception('Idle tool pruning tokenizer unavailable; omitting eligible historical result bodies')
        encoding = None
    planned = copy.deepcopy(masks)
    remaining = budget
    for message, item, index in reversed(list(completed_results(messages))):
        key = result_key(message, item, index)
        text, nontext = result_text(item)
        previous = masks.get(key)
        visible = text[: previous['chars']] if previous else text
        prefix, used = text_prefix(visible, remaining, encoding) if encoding else ('', 0)
        remaining = remaining - used if prefix == visible else 0
        if previous or nontext or len(prefix) < len(text):
            planned[key] = {'chars': len(prefix)}
    return planned, encoding.name if encoding else 'unavailable:omit-results'


def pruning_signature(messages: list[dict], masks: dict) -> str | None:
    """Bind text summaries to masks on this branch, not unrelated siblings.

    Compute before replacing result bodies: their identities hash original text.
    A missing signature denotes the legacy, unpruned transcript.
    """
    if not masks:
        return None
    relevant = {}
    for message in messages:
        if not message.get('id'):
            continue
        for index, item in enumerate(message.get('output') or []):
            if isinstance(item, dict) and item.get('type') in {'function_call_output', 'open_webui:code_interpreter'}:
                key = result_key(message, item, index)
                if key in masks:
                    relevant[key] = masks[key]
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest() if relevant else None


def _prepare_checkpoint_replay(message: dict, signature: str | None) -> None:
    if (message.get('meta') or {}).get('context_summary_pruning') != signature:
        message.pop('contextSummary', None)
        message.pop('context_summary', None)
    if signature:
        # Saved usage observed the unpruned window, not this replay. Text
        # compaction must budget the masked copy instead of that old total.
        message.pop('usage', None)
        if isinstance(message.get('info'), dict):
            message['info'].pop('usage', None)


def apply_masks(messages: list[dict], masks: dict, *, signature: str | None = None) -> list[dict]:
    """Mask a deep request copy, retaining only compatible text checkpoints.

    Native windows independently validate the exact masked provider prefix.
    Never discard them merely because pruning is enabled.
    """
    signature = signature or pruning_signature(messages, masks)
    messages = copy.deepcopy(messages)
    for message in messages:
        _prepare_checkpoint_replay(message, signature)
        if message.get('role') != 'assistant' or not isinstance(message.get('output'), list):
            continue
        for index, item in enumerate(message['output']):
            if not isinstance(item, dict) or not message.get('id'):
                continue
            if item.get('type') not in {'function_call_output', 'open_webui:code_interpreter'}:
                continue
            mask = masks.get(result_key(message, item, index))
            if mask is None:
                continue
            text, nontext = result_text(item)
            prefix = text[: mask['chars']]
            replacement = prefix + (TRUNCATED if len(prefix) < len(text) else '') if prefix else PLACEHOLDER
            if prefix == text and not nontext:
                continue
            item['output'] = (
                replacement
                if item.get('type') == 'open_webui:code_interpreter'
                else [{'type': 'input_text', 'text': replacement}]
            )
        message['output'] = [
            item
            for item in message['output']
            if not isinstance(item, dict)
            or item.get('type') != 'open_webui:context_compaction'
            or item.get('pruning') == signature
        ]
    return messages


async def begin_request(
    chat_id: str,
    branch_id: str | None,
    model_id: str,
    *,
    parent_id: str | None = None,
    active: bool = False,
    resume: bool = False,
) -> bool:
    """Run before inserting the new turn, serialized with all chat writes."""
    from open_webui.models.chats import Chats
    from open_webui.models.chat_messages import ChatMessages
    from open_webui.models.config import Config
    from open_webui.utils.misc import get_message_list

    config = await Config.get_many(
        'chat.idle_tool_pruning.enable', 'chat.idle_tool_pruning.idle_seconds', 'chat.idle_tool_pruning.token_budget'
    )
    enabled = bool(config.get('chat.idle_tool_pruning.enable', True))
    idle_seconds = max(1, int(config.get('chat.idle_tool_pruning.idle_seconds', 1560)))
    budget = max(0, int(config.get('chat.idle_tool_pruning.token_budget', 4000)))
    async with Chats._chat_transaction(chat_id) as (session, chat):
        if chat is None:
            return enabled
        now = time.time()
        state = copy.deepcopy(chat.tool_pruning or {})
        # Bootstrap only once from the pre-request server timestamp. Thereafter
        # title, read/unread and UI updates cannot reset the activity clock.
        last_activity = state.get('last_activity', chat.updated_at or now)
        if enabled and not active and not resume and now - last_activity > idle_seconds and branch_id:
            messages_map = await ChatMessages.get_messages_map_by_chat_id(chat_id, db=session)
            legacy = ((chat.chat or {}).get('history') or {}).get('messages') or {}
            messages_map = {**legacy, **(messages_map or {})}
            branch = get_message_list(messages_map, branch_id if branch_id in messages_map else parent_id)
            if not has_pending_assistant(branch):
                masks, encoding = await asyncio.to_thread(plan_masks, branch, state.get('masks', {}), budget, model_id)
                state.update(masks=masks, last_pruned_at=now, tokenizer=encoding)
        # Also track disabled traffic, so enabling later cannot use a stale gap.
        state['last_activity'] = max(now, last_activity)
        chat.tool_pruning = state
    return enabled


async def finish_request(chat_id: str) -> None:
    from open_webui.models.chats import Chats

    async with Chats._chat_transaction(chat_id) as (_, chat):
        if chat is not None:
            state = dict(chat.tool_pruning or {})
            state['last_activity'] = max(time.time(), state.get('last_activity', 0))
            chat.tool_pruning = state


def mask_current_output(output: list[dict], metadata: dict) -> list[dict]:
    if not metadata.get('idle_tool_output_pruning'):
        return output
    message = {
        'id': metadata.get('assistant_message_id') or metadata.get('message_id'),
        'role': 'assistant',
        'output': output,
    }
    return apply_masks(
        [message],
        metadata.get('_idle_tool_pruning_masks', {}),
        signature=metadata.get('_idle_tool_pruning_signature'),
    )[0]['output']


async def replay_messages(chat_id: str, messages: list[dict], metadata: dict | None = None) -> list[dict]:
    from open_webui.internal.db import get_async_db
    from open_webui.models.chats import Chat
    from sqlalchemy import select

    async with get_async_db() as session:
        state = await session.scalar(select(Chat.tool_pruning).where(Chat.id == chat_id))
    masks = (state or {}).get('masks', {})
    signature = pruning_signature(messages, masks)
    if metadata is not None:
        metadata['_idle_tool_pruning_masks'] = masks
        metadata['_idle_tool_pruning_signature'] = signature
    return apply_masks(messages, masks, signature=signature)
