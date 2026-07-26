from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

PROMPT_REDACTION_STATE_ATTRIBUTE = 'prompt_redaction_secrets'
PROMPT_REDACTION_REPLACEMENT = '[administrator prompt redacted]'


def register_request_redaction_secrets(request: Any, *secrets: Any) -> None:
    state = getattr(request, 'state', None)
    if state is None:
        return

    current = list(getattr(state, PROMPT_REDACTION_STATE_ATTRIBUTE, ()) or ())
    for secret in secrets:
        if not isinstance(secret, str) or not secret:
            continue
        if secret not in current:
            current.append(secret)
    setattr(state, PROMPT_REDACTION_STATE_ATTRIBUTE, tuple(current))


def get_request_redaction_secrets(request: Any) -> tuple[str, ...]:
    state = getattr(request, 'state', None)
    if state is None:
        return ()
    return tuple(getattr(state, PROMPT_REDACTION_STATE_ATTRIBUTE, ()) or ())


def redact_request_secrets(request: Any, value: Any) -> Any:
    return redact_secrets(value, get_request_redaction_secrets(request))


def redact_secrets(value: Any, secrets: Any) -> Any:
    variants = _secret_variants(secrets)
    if not variants:
        return value
    return _redact_value(value, variants)


def _secret_variants(secrets: Any) -> list[str]:
    variants = set()
    for secret in secrets or ():
        if not isinstance(secret, str) or not secret:
            continue
        candidates = {secret, secret.strip()}
        for _ in range(2):
            candidates.update(
                json.dumps(candidate, ensure_ascii=False)[1:-1] for candidate in tuple(candidates) if candidate
            )
        variants.update(candidate for candidate in candidates if candidate)
    return sorted(variants, key=len, reverse=True)


def _redact_value(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, PROMPT_REDACTION_REPLACEMENT)
        return value
    if isinstance(value, Mapping):
        return {_redact_value(key, secrets): _redact_value(nested, secrets) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, secrets) for item in value)
    if isinstance(value, set):
        return {_redact_value(item, secrets) for item in value}
    return value
