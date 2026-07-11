#!/usr/bin/env python3
"""Safely migrate the installed Anthropic manifold to native async discovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_FUNCTION_ID = 'anthropic_pipe'
DEFAULT_EXPECTED_MD5 = 'd430ec14a77e03bd45fd408c2442d0c6'
OLD_REQUIREMENTS = 'requirements: pydantic>=2.0.0, anthropic>=0.75.0\n'
NEW_REQUIREMENTS = 'requirements: pydantic>=2.0.0, anthropic>=0.75.0, httpx>=0.27.0\n'


class MigrationError(RuntimeError):
    """Raised when the source or API state is outside the migration contract."""


class _RejectRedirects(HTTPRedirectHandler):
    """Keep the administrator bearer token on the configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_RejectRedirects())


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise MigrationError(f'expected exactly one {label}; found {count}')
    return source.replace(old, new, 1)


def _replace_blocking_request(source: str) -> str:
    pattern = re.compile(
        r'^(?P<indent>[ \t]*)response = requests\.get\('
        r'model_url, headers=headers, timeout=15\)\n'
        r'(?P=indent)response\.raise_for_status\(\)\n'
        r'(?P=indent)payload = response\.json\(\)$',
        re.MULTILINE,
    )
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise MigrationError(f'expected exactly one blocking model request; found {len(matches)}')

    indent = matches[0].group('indent')
    nested = f'{indent}    '
    replacement = (
        f'{indent}async with httpx.AsyncClient(\n'
        f'{nested}timeout=15,\n'
        f'{nested}trust_env=True,\n'
        f'{nested}follow_redirects=True,\n'
        f'{indent}) as client:\n'
        f'{nested}response = await client.get(model_url, headers=headers)\n'
        f'{nested}response.raise_for_status()\n'
        f'{nested}payload = response.json()'
    )
    return pattern.sub(replacement, source, count=1)


def patch_source(source: str, expected_md5: str | None = None) -> str:
    """Return the exact source migration, refusing drift or partial reapplication."""

    actual_md5 = hashlib.md5(source.encode()).hexdigest()
    if expected_md5 is not None and actual_md5 != expected_md5:
        raise MigrationError(f'source hash mismatch: expected {expected_md5}, found {actual_md5}')

    patched = _replace_once(
        source,
        OLD_REQUIREMENTS,
        NEW_REQUIREMENTS,
        'requirements declaration',
    )
    patched = _replace_once(
        patched,
        'import requests\n',
        'import httpx\n',
        'requests import',
    )
    patched = _replace_blocking_request(patched)
    compile(patched, '<patched-anthropic-pipe>', 'exec')
    return patched


def _request_json(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {'Authorization': f'Bearer {token}'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=30) as response:
            decoded = json.loads(response.read().decode())
    except HTTPError as exc:
        detail = exc.read().decode(errors='replace')
        raise MigrationError(f'OpenWebUI HTTP {exc.code}: {detail}') from exc
    except (URLError, TimeoutError) as exc:
        raise MigrationError(f'OpenWebUI request failed: {exc}') from exc
    if not isinstance(decoded, dict):
        raise MigrationError('OpenWebUI returned a non-object JSON response')
    return decoded


def _function_url(base_url: str, function_id: str) -> str:
    return f'{base_url.rstrip("/")}/api/v1/functions/id/{quote(function_id, safe="")}'


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _write_private_backup(path: Path, payload: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW

    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            fd = -1
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)

    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _classify_source(readback: dict[str, Any], source: str, patched: str) -> str:
    content = readback.get('content')
    if content == patched:
        return 'patched'
    if content == source:
        return 'original'
    return 'unknown'


def _reconcile_post_error(
    post_error: MigrationError,
    function_url: str,
    token: str,
    source: str,
    patched: str,
    backup_path: Path,
    result: dict[str, Any],
) -> None:
    try:
        readback = _request_json('GET', function_url, token)
    except MigrationError as readback_error:
        raise MigrationError(
            'unable to reconcile uncertain POST state; do not retry or '
            f'overwrite automatically. Inspect the Function and backup {backup_path}: '
            f'{readback_error}'
        ) from post_error

    state = _classify_source(readback, source, patched)
    if state == 'patched':
        result['post_reconciled_after_error'] = True
        return
    if state == 'original':
        raise MigrationError(
            'update was not applied after POST error; the original source '
            f'is still present and the backup is {backup_path}'
        ) from post_error
    raise MigrationError(
        'unknown post-update state after POST error; do not overwrite '
        f'automatically. Inspect the Function and backup {backup_path}'
    ) from post_error


def _verify_post(
    function_url: str,
    token: str,
    source: str,
    patched: str,
    backup_path: Path,
) -> None:
    try:
        readback = _request_json('GET', function_url, token)
    except MigrationError as readback_error:
        raise MigrationError(
            'unable to verify the completed POST; the post-update state is '
            f'unknown. Do not retry or overwrite automatically. Inspect '
            f'the Function and backup {backup_path}: {readback_error}'
        ) from readback_error

    state = _classify_source(readback, source, patched)
    if state == 'original':
        raise MigrationError(
            f'update response completed but the original source remains; inspect the Function and backup {backup_path}'
        )
    if state == 'unknown':
        raise MigrationError(
            f'unknown post-update state; do not overwrite automatically. Inspect the Function and backup {backup_path}'
        )


def _apply_patch(
    args: argparse.Namespace,
    function_url: str,
    token: str,
    current: dict[str, Any],
    source: str,
    patched: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    preflight = _request_json('GET', function_url, token)
    if preflight.get('content') != source or preflight.get('updated_at') != current.get('updated_at'):
        raise MigrationError(
            'function changed during preflight; no update was sent. '
            'Re-run from a fresh dry-run during an exclusive maintenance window.'
        )

    function_name = preflight.get('name')
    if not isinstance(function_name, str):
        raise MigrationError('function readback is missing string name')

    backup_path = Path(args.backup).expanduser().resolve()
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    _write_private_backup(backup_path, preflight)
    result['backup'] = str(backup_path)
    result['exclusive_maintenance_confirmed'] = True

    update_payload = {
        'id': preflight.get('id', args.function_id),
        'name': function_name,
        'content': patched,
        'meta': preflight.get('meta') or {},
    }
    try:
        _request_json('POST', f'{function_url}/update', token, update_payload)
    except MigrationError as post_error:
        _reconcile_post_error(
            post_error,
            function_url,
            token,
            source,
            patched,
            backup_path,
            result,
        )
    else:
        _verify_post(function_url, token, source, patched, backup_path)

    result['verified_readback'] = True
    return result


def migrate(args: argparse.Namespace) -> dict[str, Any]:
    if args.apply and not args.confirm_exclusive_maintenance:
        raise MigrationError(
            '--confirm-exclusive-maintenance is required with --apply to '
            'confirm an exclusive maintenance window because the Function API '
            'has no conditional-update primitive'
        )
    if args.apply and args.backup is None:
        raise MigrationError('--backup is required with --apply')

    token = os.environ.get('OPENWEBUI_TOKEN')
    if not token:
        raise MigrationError('OPENWEBUI_TOKEN is required')

    function_url = _function_url(args.base_url, args.function_id)
    current = _request_json('GET', function_url, token)
    source = current.get('content')
    if not isinstance(source, str):
        raise MigrationError('function readback is missing string content')

    patched = patch_source(source, expected_md5=args.expected_md5)
    result: dict[str, Any] = {
        'function_id': args.function_id,
        'mode': 'apply' if args.apply else 'dry-run',
        'before_md5': hashlib.md5(source.encode()).hexdigest(),
        'before_sha256': _sha256(source),
        'after_md5': hashlib.md5(patched.encode()).hexdigest(),
        'after_sha256': _sha256(patched),
        'changed': source != patched,
    }
    if not args.apply:
        return result
    return _apply_patch(args, function_url, token, current, source, patched, result)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--function-id', default=DEFAULT_FUNCTION_ID)
    parser.add_argument('--expected-md5', default=DEFAULT_EXPECTED_MD5)
    parser.add_argument('--backup')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-exclusive-maintenance', action='store_true')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        result = migrate(_parse_args(sys.argv[1:] if argv is None else argv))
    except MigrationError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({'ok': True, **result}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
