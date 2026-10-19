"""Same-model native compaction. The transcript remains the recovery source.

Checkpoints contain the entire canonical output window, not a text summary. A
hash of the original input prefix prevents reuse after edits or on other branches.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
from urllib.parse import urlsplit, urlunsplit

from open_webui.models.chats import Chats
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.misc import get_message_list
from open_webui.utils.model_ids import strip_provider_model_prefix

log = logging.getLogger(__name__)
CHECKPOINT_KEY = 'native_compaction'
FAILURE_RETRY_SECONDS = 300
PROTOCOLS = ('responses_compact', 'codex_v2')
USAGE_KEY = 'native_token_usage'
# Budget heuristics, not model billing constants. Ported from OpenAI Codex
# context_manager/history.rs at 8c076942cb70fba8e5c4732c23f1faabc4aed484.
RESIZED_IMAGE_TOKENS = 1844
UNKNOWN_MEDIA_TOKENS = 2**63 - 1


def _estimate_native_tokens(value) -> int:
    """Budget structured content, never tokenize binary transport as prose.

    Codex uses 1844 for resized images and a separate encrypted-state heuristic.
    Neither is an exact Luna tokenizer. A bound observed usage supersedes these
    estimates in _native_request_size. Only the local counting projection is
    changed; original text, media, and opaque bytes are never edited.
    """
    import tiktoken

    if value is None:
        return 0
    media_tokens = 0

    def project(part):
        nonlocal media_tokens
        if isinstance(part, list):
            return [project(entry) for entry in part]
        if not isinstance(part, dict):
            return part
        kind = part.get('type', '')
        if kind in ('input_image', 'image_url'):
            media_tokens += _native_image_tokens(part)
            return {'type': kind}
        if kind in ('input_file', 'file'):
            media_tokens += _native_file_tokens(part.get('file', part))
            return {'type': kind}
        if kind in ('input_audio', 'audio', 'input_video', 'video'):
            # No verified local budget for this modality. Fail conservatively
            # at the trigger only; no fetch, omission, or payload mutation.
            media_tokens += UNKNOWN_MEDIA_TOKENS
            return {'type': kind}
        projected = {}
        for key, entry in part.items():
            if key == 'encrypted_content' and isinstance(entry, str):
                # Separate Codex heuristics for compaction, reasoning and
                # encrypted tool content. None recovers semantic token counts.
                encoded = len(entry)
                if kind in ('compaction', 'context_compaction'):
                    budget = (encoded + 11) // 12
                elif kind == 'reasoning':
                    budget = (max(0, encoded * 3 // 4 - 650) + 3) // 4
                else:
                    budget = (encoded * 9 + 63) // 64
                media_tokens += max(1, budget) if entry else 0
            elif kind == 'image_generation_call' and key == 'result' and isinstance(entry, str):
                media_tokens += RESIZED_IMAGE_TOKENS if entry else 0
            else:
                projected[key] = project(entry)
        return projected

    projection = project(value)
    text = projection if isinstance(projection, str) else json.dumps(projection, ensure_ascii=False)
    if not text:
        return 0
    # Bound tokenizer work for long unbroken text on the single-worker path.
    # Chunk boundaries can change the estimate slightly; no content is pruned.
    encoding = tiktoken.get_encoding('o200k_base')
    tokens = sum(len(encoding.encode_ordinary(text[start : start + 4096])) for start in range(0, len(text), 4096))
    return max(1, len(text) // 4, tokens) + media_tokens


def _native_image_tokens(part: dict) -> int:
    """Codex resized/original budgets. Never fetch URLs or inspect pixel data."""
    import base64
    import io
    import math

    from PIL import Image

    url = part.get('image_url', '')
    detail = part.get('detail', 'auto')
    if isinstance(url, dict):
        detail, url = url.get('detail', detail), url.get('url', '')
    if detail != 'original':
        return RESIZED_IMAGE_TOKENS
    try:
        if not isinstance(url, str) or not url.startswith('data:image/') or ';base64,' not in url:
            return 10000  # Original patch cap when dimensions cannot be read.
        raw = base64.b64decode(url.split(',', 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
        return min(10000, math.ceil(width / 32) * math.ceil(height / 32))
    except Exception:
        return 10000


def _native_file_tokens(part: dict) -> int:
    """Native PDFs contain extracted text AND a page image (OpenAI file guide).

    Existing pypdf only; no URL fetch/upload/parser fallback. Unknown, encrypted,
    oversized or unreadable documents have no finite verified local budget.
    An observed bound usage can still handle them on subsequent valid replays.
    """
    import base64
    import io

    from pypdf import PdfReader

    data = part.get('file_data', '') if isinstance(part, dict) else ''
    if not isinstance(data, str) or not data.startswith('data:application/pdf;base64,'):
        return UNKNOWN_MEDIA_TOKENS
    # Matches the existing authorized native-PDF upload limit, not a new limit
    # on what is sent. The provider still receives the entire original object.
    if len(data) > (25 * 1024 * 1024 + 2) // 3 * 4 + 64:
        return UNKNOWN_MEDIA_TOKENS
    try:
        raw = base64.b64decode(data.split(',', 1)[1], validate=True)
        if not raw.startswith(b'%PDF-'):
            return UNKNOWN_MEDIA_TOKENS
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            return UNKNOWN_MEDIA_TOKENS
        return sum(RESIZED_IMAGE_TOKENS + _estimate_native_tokens(page.extract_text() or '') for page in reader.pages)
    except Exception:
        return UNKNOWN_MEDIA_TOKENS


def native_compaction_enabled(config: dict, provider_model_id: str) -> bool:
    """Only an administrator's exact connection allowlist enables this protocol."""
    allowed = config.get('native_compaction_models')
    return (
        config.get('api_type') == 'responses'
        and config.get('native_compaction_protocol', 'responses_compact') in PROTOCOLS
        and isinstance(allowed, list)
        and provider_model_id in allowed
    )


def native_compaction_headers(headers: dict, config: dict, provider_model_id: str) -> dict:
    """Advertise v2 on compaction and normal opaque-state continuation alike."""
    if config.get('native_compaction_protocol') != 'codex_v2' or not native_compaction_enabled(
        config, provider_model_id
    ):
        return headers
    result = dict(headers)
    beta_keys = [key for key in result if key.lower() == 'x-codex-beta-features']
    tokens = [token.strip() for key in beta_keys for token in result.pop(key).split(',') if token.strip()]
    if 'remote_compaction_v2' not in tokens:
        tokens.append('remote_compaction_v2')
    result['x-codex-beta-features'] = ','.join(tokens)
    return result


async def uses_native_compaction(request, model: dict) -> bool:
    if getattr(request.state, 'direct', False) or model.get('pipe') or model.get('owned_by') == 'ollama':
        return False
    from open_webui.routers.openai import get_openai_connection

    base = getattr(request, 'base_model_id', None) or (model.get('info') or {}).get('base_model_id') or model['id']
    provider = request.app.state.OPENAI_MODELS.get(base)
    if not provider or 'urlIdx' not in provider:
        return False
    _, _, config = await get_openai_connection(provider['urlIdx'])
    return native_compaction_enabled(config, strip_provider_model_prefix(base, config.get('prefix_id')))


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()


def _matches_prefix(state: dict, original: list[dict], binding: str) -> bool:
    count = state.get('prefix_count')
    return (
        state.get('version') == 2
        and state.get('binding') == binding
        and isinstance(count, int)
        and 0 < count <= len(original)
        and state.get('prefix_hash') == _digest(original[:count])
    )


def _has_pending_calls(items: list[dict]) -> bool:
    pending = set()
    for item in items:
        if item.get('type') == 'function_call':
            pending.add(item.get('call_id'))
        elif item.get('type') == 'function_call_output':
            pending.discard(item.get('call_id'))
    return bool(pending)


def _valid_output(output) -> bool:
    return (
        isinstance(output, list)
        and bool(output)
        and all(isinstance(item, dict) for item in output)
        and any(
            item.get('type') == 'compaction'
            and isinstance(item.get('encrypted_content'), str)
            and item['encrypted_content']
            for item in output
        )
    )


def _consume_v2_event(data: list[str], output: dict) -> bool:
    raw = '\n'.join(data)
    data.clear()
    if not raw or raw == '[DONE]':
        return False
    event = json.loads(raw)
    kind = event.get('type')
    if kind in ('error', 'response.failed', 'response.incomplete'):
        raise RuntimeError('Native compaction stream failed')
    if kind == 'response.output_item.done':
        index, item = event.get('output_index'), event.get('item')
        if not isinstance(index, int) or not isinstance(item, dict):
            raise RuntimeError('Invalid native compaction output item')
        if index in output and output[index] != item:
            raise RuntimeError('Conflicting native compaction output item')
        output[index] = item
    return kind == 'response.completed'


async def _read_v2_output(response) -> list[dict]:
    """Only complete done items are reusable; added items can contain partial state."""
    output, data = {}, []
    completed = False

    async for raw in response.content:
        line = raw.decode('utf-8').rstrip('\r\n')
        if not line:
            completed = _consume_v2_event(data, output) or completed
        elif line.startswith('data:'):
            data.append(line[5:].lstrip())
    completed = _consume_v2_event(data, output) or completed
    items = [output[index] for index in sorted(output)]
    if not completed or sum(item.get('type') == 'compaction' for item in items) != 1 or not _valid_output(items):
        raise RuntimeError('Native compaction requires completed stream and exactly one opaque item')
    return items


def _compaction_prefix_count(original: list[dict], protocol: str) -> int:
    if protocol != 'codex_v2':
        return len(original)
    return next((index for index in range(len(original) - 1, -1, -1) if original[index].get('role') == 'user'), 0)


async def _compact_upstream(
    payload: dict, request_url: str, headers: dict, cookies, *, protocol: str = 'responses_compact'
) -> list[dict]:
    from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
    from open_webui.utils.json_codec import JSONCodec
    from open_webui.utils.session_pool import get_client_timeout, get_session

    if protocol == 'codex_v2':
        compact_url = request_url
        # Codex remote_compaction_v2 uses normal Responses streaming, not the
        # public context_management option or legacy unary /compact endpoint.
        fields = (
            'model',
            'input',
            'instructions',
            'tools',
            'parallel_tool_calls',
            'reasoning',
            'include',
            'prompt_cache_key',
        )
        body = {key: copy.deepcopy(payload[key]) for key in fields if key in payload}
        body.update(stream=True, store=False)
        body['input'].append({'type': 'compaction_trigger'})
        headers = native_compaction_headers(
            headers,
            {
                'api_type': 'responses',
                'native_compaction_models': [payload['model']],
                'native_compaction_protocol': 'codex_v2',
            },
            payload['model'],
        )
    elif protocol == 'responses_compact':
        parts = urlsplit(request_url)
        compact_url = urlunsplit(parts._replace(path=parts.path.rstrip('/') + '/compact'))
        # The standalone endpoint has a smaller schema than responses.create.
        body = {key: payload[key] for key in ('model', 'input', 'instructions') if key in payload}
    else:
        raise ValueError('Unknown native compaction protocol')
    session = await get_session()
    response = await session.request(
        method='POST',
        url=compact_url,
        data=JSONCodec.dumps(body),
        headers=headers,
        cookies=cookies,
        ssl=AIOHTTP_CLIENT_SESSION_SSL,
        timeout=get_client_timeout(stream=protocol == 'codex_v2'),
    )
    try:
        if response.status >= 400:
            raise RuntimeError(f'Native compaction provider HTTP {response.status}')
        if protocol == 'codex_v2':
            return await _read_v2_output(response)
        result = await response.json(loads=JSONCodec.loads)
        output = result.get('output') if isinstance(result, dict) else None
        if not _valid_output(output):
            raise RuntimeError('Native compaction returned no canonical compaction window')
        return output
    finally:
        response.release()


async def _emit(metadata: dict, description: str, *, done: bool, error: bool = False):
    from open_webui.socket.main import get_event_emitter

    emitter = await get_event_emitter(metadata)
    if emitter:
        await emitter(
            {
                'type': 'context_compaction',
                'data': {
                    'action': 'context_compaction',
                    'mode': 'native',
                    'description': description,
                    'done': done,
                    **({'error': True} if error else {}),
                },
            }
        )


async def _branch_for_request(metadata: dict, user):
    chat_id, user_message_id = metadata.get('chat_id'), metadata.get('user_message_id')
    if not is_saved_chat_id(chat_id) or not user_message_id:
        return {}, []
    chat = await Chats.get_chat_by_id(chat_id)
    if not chat or chat.user_id != user.id:
        return {}, []
    message_map = await Chats.get_messages_map_by_chat_id(chat_id) or {}
    branch = get_message_list(message_map, user_message_id)
    # Approval/Continue checkpoints can be on the current assistant rather
    # than an ancestor of the current user message.
    for current_id in (metadata.get('assistant_message_id'), metadata.get('message_id')):
        current = message_map.get(current_id)
        if current and current.get('parentId') == user_message_id and current not in branch:
            branch.append(current)
    return message_map, branch


def _restore_window(branch: list[dict], original: list[dict], binding: str):
    states = [(message.get('meta') or {}).get(CHECKPOINT_KEY) for message in branch]
    states = [state for state in states if isinstance(state, dict) and _matches_prefix(state, original, binding)]
    checkpoint = next((state for state in reversed(states) if _valid_output(state.get('output'))), None)
    if checkpoint:
        active = copy.deepcopy(checkpoint['output']) + copy.deepcopy(original[checkpoint['prefix_count'] :])
    else:
        active = copy.deepcopy(original)
    return active, checkpoint, states


async def _save_failure(chat_id: str, anchor: str, message_map: dict, state: dict, states: list[dict]):
    # A failed repeat on the same anchor must not destroy an earlier good window.
    previous = (message_map[anchor].get('meta') or {}).get(CHECKPOINT_KEY) or {}
    failure = {
        **{key: value for key, value in state.items() if key != 'output'},
        'status': 'failed',
        'retry_after': time.time() + FAILURE_RETRY_SECONDS,
    }
    if previous in states and _valid_output(previous.get('output')):
        failure = {**previous, 'status': 'failed', 'retry_after': failure['retry_after']}
    try:
        await Chats.upsert_message_to_chat_by_id_and_message_id(
            chat_id,
            anchor,
            {'meta': {**(message_map[anchor].get('meta') or {}), CHECKPOINT_KEY: failure}},
            touch=False,
        )
    except Exception:
        log.warning('Could not save native compaction failure state for chat=%s', chat_id)


def _native_usage_settings(payload: dict) -> str:
    return _digest({key: value for key, value in payload.items() if key not in ('input', 'stream', 'stream_options')})


def _native_request_size(active, original, payload, metadata, branch, binding) -> int:
    """Prefer single-call observed input + suffix, only on an exact replay.

    Normal message.usage is cumulative across tool calls, so is NOT an anchor.
    Cached input is already included in input_tokens; it is never subtracted.
    Old dynamic retrieval remains in the observed baseline; add this turn's
    retrieval in full, a conservative allowance rather than a subtraction.
    """
    dynamic = _estimate_native_tokens(metadata.get('_dynamic_memory_context'))
    settings = _native_usage_settings(payload)
    for message in reversed(branch):
        anchor = (message.get('meta') or {}).get(USAGE_KEY)
        if not isinstance(anchor, dict) or message.get('done') is not True or message.get('error'):
            continue
        count, tokens = anchor.get('active_count'), anchor.get('input_tokens')
        if (
            _matches_prefix(anchor, original, binding)
            and anchor.get('settings') == settings
            and isinstance(count, int)
            and 0 < count <= len(active)
            and anchor.get('active_hash') == _digest(active[:count])
            and isinstance(tokens, int)
            and not isinstance(tokens, bool)
            and tokens > 0
        ):
            suffix = active[count:]
            # The previous reply was not in its own input. Its actual output
            # usage bounds retained reasoning/opaque more reliably than a
            # ciphertext heuristic. Everything from the next user onward is
            # new input and is counted separately, including tools/media.
            next_user = next((index for index, item in enumerate(suffix) if item.get('role') == 'user'), len(suffix))
            output_tokens = anchor.get('output_tokens', 0)
            if not isinstance(output_tokens, int) or isinstance(output_tokens, bool) or output_tokens < 0:
                output_tokens = 0
            return (
                tokens
                + max(output_tokens if next_user else 0, _estimate_native_tokens(suffix[:next_user]))
                + _estimate_native_tokens(suffix[next_user:])
                + dynamic
            )
    return (
        _estimate_native_tokens(active)
        + _estimate_native_tokens(payload.get('instructions'))
        + _estimate_native_tokens(payload.get('tools'))
        + _estimate_native_tokens(payload.get('text'))
        + dynamic
    )


def _stamp_native_usage(metadata, result, original, binding, user):
    # This private, server-only metadata is removed before provider serialization.
    # It contains hashes/counts, not content, media, encrypted state or credentials.
    metadata['_native_token_usage'] = {
        'version': 2,
        'binding': binding,
        'owner': _digest(user.id),
        'model': result['model'],
        'prefix_count': len(original),
        'prefix_hash': _digest(original),
        'active_count': len(result['input']),
        'active_hash': _digest(result['input']),
        'settings': _native_usage_settings(result),
    }
    return result


async def save_native_token_usage(user, metadata: dict, response: dict):
    """Save only this completed Responses call, before cumulative usage merging.

    Missing/failed observations fall back to local budgets. No schema, ledger,
    extra upstream request, or dependence on output/ciphertext token ratios.
    """
    stamp = metadata.pop('_native_token_usage', None)
    if not isinstance(stamp, dict) or stamp.get('owner') != _digest(user.id):
        return
    if not isinstance(response, dict) or not isinstance(response.get('usage'), dict):
        return
    tokens = response['usage'].get('input_tokens')
    if (
        response.get('status', 'completed') != 'completed'
        or response.get('model') != stamp.get('model')
        or not isinstance(tokens, int)
        or isinstance(tokens, bool)
        or tokens <= 0
    ):
        return
    try:
        message_map, branch = await _branch_for_request(metadata, user)
        anchor = metadata.get('assistant_message_id') or metadata.get('message_id')
        if anchor not in message_map or not any(
            message.get('id') == anchor and message.get('role') == 'assistant' for message in branch
        ):
            return
        output_tokens = response['usage'].get('output_tokens', 0)
        if not isinstance(output_tokens, int) or isinstance(output_tokens, bool) or output_tokens < 0:
            output_tokens = 0
        await Chats.upsert_message_to_chat_by_id_and_message_id(
            metadata['chat_id'],
            anchor,
            {
                'meta': {
                    **(message_map[anchor].get('meta') or {}),
                    USAGE_KEY: {**stamp, 'input_tokens': tokens, 'output_tokens': output_tokens},
                }
            },
            touch=False,
        )
    except Exception:
        log.warning('Could not save native token usage for chat=%s', metadata.get('chat_id'))


async def compact_responses_payload(
    request,
    user,
    payload: dict,
    metadata: dict | None,
    *,
    api_config: dict,
    request_url: str,
    connection_index: int,
    api_key: str,
    headers: dict,
    cookies=None,
) -> dict:
    """Apply/rebuild a branch checkpoint at the final native request boundary.

    This runs after attachments, system resolution and tool conversion, so a
    compact window is never fed through text normalization. No provider output
    item is pruned. Failures retain the last usable window (or full transcript).
    """
    import asyncio

    from open_webui.utils.context_compaction import _load_config, _resolve_token_threshold

    if metadata is None:
        metadata = {}
    metadata.pop('_native_token_usage', None)
    if not native_compaction_enabled(api_config, payload.get('model')) or metadata.get('task'):
        return payload
    config = await _load_config()
    if not config['enable'] or payload.get('previous_response_id'):
        return payload
    chat_id, message_id = metadata.get('chat_id'), metadata.get('message_id')
    user_message_id = metadata.get('user_message_id')
    if not is_saved_chat_id(chat_id) or not user_message_id:
        return payload
    original = payload.get('input')
    if not isinstance(original, list) or not original:
        return payload

    message_map, branch = await _branch_for_request(metadata, user)
    if not branch:
        return payload
    protocol = api_config.get('native_compaction_protocol', 'responses_compact')
    binding_parts = [
        protocol,
        connection_index,
        request_url,
        _digest(api_key),
        payload['model'],
        payload.get('instructions', ''),
    ]
    if protocol == 'codex_v2':
        # Old v2 windows prepended every client message/PDF to provider output.
        # Rebuild those checkpoints once; never mutate their opaque content.
        binding_parts.append('canonical_output_v1')
    binding = _digest(binding_parts)
    active, checkpoint, states = _restore_window(branch, original, binding)
    result = {**payload, 'input': active}

    threshold = _resolve_token_threshold(config['token_threshold'], config['token_cap'], metadata)
    # Existing branch loading validates owner. Also bind observations explicitly
    # to that owner without changing the compaction checkpoint binding contract.
    usage_binding = _digest([binding, user.id])
    # PDF decoding/text extraction and long-text tokenization must not monopolize
    # the API event loop. This only computes a budget; it makes no network call.
    size = await asyncio.to_thread(_native_request_size, active, original, payload, metadata, branch, usage_binding)
    # V2 compacts completed prior turns. Keep the current user turn (and any
    # associated ongoing tool batch) after the new window, never before opaque
    # state or deleted as if it were an old assistant trajectory.
    prefix_count = _compaction_prefix_count(original, protocol)
    no_new_input = checkpoint and checkpoint['prefix_count'] >= prefix_count
    recent_failure = any(state.get('retry_after', 0) > time.time() for state in states)
    if size <= threshold or not prefix_count or no_new_input or recent_failure or _has_pending_calls(active):
        return _stamp_native_usage(metadata, result, original, usage_binding, user)

    # Persist on an existing assistant placeholder, otherwise the current user.
    anchor = next(
        (
            key
            for key in (metadata.get('assistant_message_id'), message_id, user_message_id)
            if key in message_map and any(message.get('id') == key for message in branch)
        ),
        None,
    )
    if not anchor:
        return _stamp_native_usage(metadata, result, original, usage_binding, user)
    state = {
        'version': 2,
        'protocol': protocol,
        'binding': binding,
        'prefix_count': prefix_count,
        'prefix_hash': _digest(original[:prefix_count]),
    }
    await _emit(metadata, 'Compacting context (native Responses)', done=False)
    try:
        suffix = copy.deepcopy(original[prefix_count:])
        compact_input = active[: -len(suffix)] if suffix else active
        output = await _compact_upstream(
            {**result, 'input': compact_input}, request_url, headers, cookies, protocol=protocol
        )
        # Both protocols return the next window. Keep every returned item in
        # order, but do not prepend old client messages/PDFs the provider has
        # compacted. The current, uncompressed user/tool suffix stays below.
        state['output'] = copy.deepcopy(output)
        state['status'] = 'completed'
        # Saving is part of success. A failed save cannot silently shorten a
        # request with a window that the next saved/reopened turn cannot recover.
        saved = await Chats.upsert_message_to_chat_by_id_and_message_id(
            chat_id,
            anchor,
            {'meta': {**(message_map[anchor].get('meta') or {}), CHECKPOINT_KEY: state}},
            touch=False,
        )
        if saved is None:
            raise RuntimeError('Could not save native compaction checkpoint')
    except Exception:
        log.warning('Native context compaction failed for chat=%s; retaining original window', chat_id)
        await _save_failure(chat_id, anchor, message_map, state, states)
        await _emit(metadata, 'Native context compaction failed; previous context retained', done=True, error=True)
        return _stamp_native_usage(metadata, result, original, usage_binding, user)
    await _emit(metadata, 'Context compacted (native Responses)', done=True)
    return _stamp_native_usage(
        metadata, {**payload, 'input': copy.deepcopy(output) + suffix}, original, usage_binding, user
    )
