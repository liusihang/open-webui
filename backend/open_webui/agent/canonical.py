from __future__ import annotations

import hashlib
import json
from typing import Any


class CanonicalJSONError(ValueError):
    code = 'invalid_canonical_json'


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(',', ':'),
            sort_keys=True,
        )
    except ValueError as exc:
        raise CanonicalJSONError(
            'Canonical JSON requires finite numeric values'
        ) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()
