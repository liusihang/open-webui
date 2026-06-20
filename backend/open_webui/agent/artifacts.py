from __future__ import annotations

import hashlib
import posixpath
from typing import Any

AGENT_RUN_WORKSPACE_ROOT = '/workspace/agent-runs'
DEFAULT_ARTIFACT_KIND = 'file'


def agent_run_output_dir(
    run_id: str,
    requested_output_dir: str | None = None,
) -> str:
    if requested_output_dir:
        return _strip_trailing_slash(requested_output_dir)
    return f'{AGENT_RUN_WORKSPACE_ROOT}/{run_id}/outputs'


def agent_run_tmp_dir(run_id: str) -> str:
    return f'{AGENT_RUN_WORKSPACE_ROOT}/{run_id}/tmp'


def artifact_metadata_for_path(
    path: str,
    *,
    run_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(metadata or {})
    if _is_run_scoped_path(path, run_id, 'tmp'):
        result.update(
            {
                'cleanup_eligible': True,
                'retention': 'temporary_debug',
            }
        )
    elif _is_run_scoped_path(path, run_id, 'outputs'):
        result.update(
            {
                'cleanup_eligible': False,
                'retention': 'user_visible_output',
            }
        )
    else:
        result.setdefault('cleanup_eligible', False)
        result.setdefault('retention', 'external_or_user_selected')
    return result


def collect_terminal_output_paths(
    *,
    arguments: dict[str, Any] | None = None,
    result: Any = None,
) -> list[str]:
    paths: list[str] = []
    _collect_explicit_paths(paths, arguments or {})
    _collect_shell_redirect_paths(paths, arguments or {})
    if isinstance(result, dict):
        _collect_explicit_paths(paths, result)
        _collect_shell_redirect_paths(paths, result)
    return _dedupe(paths)


class AgentRunArtifactRegistrar:
    def __init__(self, artifact_store):
        self.artifact_store = artifact_store

    async def register_terminal_output_artifacts(
        self,
        *,
        run_id: str,
        user_id: str,
        participant_id: str,
        terminal_server_id: str | None,
        output_paths: list[str],
        output_dir: str | None = None,
    ) -> list[dict[str, Any]]:
        artifacts = []
        for raw_path in output_paths:
            path = resolve_terminal_artifact_path(
                run_id,
                raw_path,
                output_dir=output_dir,
            )
            metadata = artifact_metadata_for_path(
                path,
                run_id=run_id,
                metadata={'participant_id': participant_id},
            )
            artifact = await self.artifact_store.register_artifact(
                run_id=run_id,
                user_id=user_id,
                kind=DEFAULT_ARTIFACT_KIND,
                path=path,
                idempotency_key=_artifact_idempotency_key(
                    run_id=run_id,
                    participant_id=participant_id,
                    terminal_server_id=terminal_server_id,
                    kind=DEFAULT_ARTIFACT_KIND,
                    path=path,
                ),
                terminal_server_id=terminal_server_id,
                metadata=metadata,
            )
            artifacts.append(_artifact_payload(artifact))
        return artifacts


def resolve_terminal_artifact_path(
    run_id: str,
    path: str,
    *,
    output_dir: str | None = None,
) -> str:
    if path.startswith('/'):
        return posixpath.normpath(path)
    return posixpath.normpath(
        posixpath.join(agent_run_output_dir(run_id, output_dir), path)
    )


def _collect_explicit_paths(paths: list[str], source: dict[str, Any]) -> None:
    for key in ('output_path', 'artifact_path'):
        _append_path(paths, source.get(key))
    for key in ('output_paths', 'artifact_paths'):
        _append_path(paths, source.get(key))
    for artifact in source.get('artifacts') or []:
        if isinstance(artifact, dict):
            _append_path(paths, artifact.get('path'))


def _collect_shell_redirect_paths(paths: list[str], source: dict[str, Any]) -> None:
    command = source.get('command')
    if not isinstance(command, str) or not command:
        return
    for path in _shell_redirect_output_paths(command):
        paths.append(path)


def _shell_redirect_output_paths(command: str) -> list[str]:
    paths: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if quote:
            if char == '\\' and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == '\\':
            escaped = True
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == '>':
            target_start = index + 2 if _has_at(command, index + 1, '>') else index + 1
            target, target_end = _read_shell_word(command, target_start)
            if target:
                _append_safe_shell_redirect_path(paths, target)
            index = target_end
            continue
        index += 1
    return paths


def _read_shell_word(command: str, index: int) -> tuple[str | None, int]:
    while index < len(command) and command[index].isspace():
        index += 1

    chars: list[str] = []
    quote: str | None = None
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            chars.append(char)
            escaped = False
            index += 1
            continue
        if quote:
            if char == '\\' and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            else:
                chars.append(char)
            index += 1
            continue
        if char == '\\':
            escaped = True
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char.isspace() or char in {';', '|', '&', '<', '(', ')', '>'}:
            break
        chars.append(char)
        index += 1

    return (''.join(chars) or None, index)


def _has_at(value: str, index: int, char: str) -> bool:
    return index < len(value) and value[index] == char


def _append_safe_shell_redirect_path(paths: list[str], path: str) -> None:
    normalized = posixpath.normpath(path)
    if _is_agent_run_artifact_path(normalized):
        paths.append(normalized)


def _is_agent_run_artifact_path(path: str) -> bool:
    if not path.startswith(f'{AGENT_RUN_WORKSPACE_ROOT}/'):
        return False
    parts = path.removeprefix(f'{AGENT_RUN_WORKSPACE_ROOT}/').split('/')
    return len(parts) >= 3 and parts[1] in {'outputs', 'tmp'} and bool(parts[2])


def _append_path(paths: list[str], value: Any) -> None:
    if isinstance(value, str) and value:
        paths.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _append_path(paths, item)


def _artifact_payload(artifact: Any) -> dict[str, Any]:
    return {
        'artifact_id': _value(artifact, 'id'),
        'kind': _value(artifact, 'kind'),
        'path': _value(artifact, 'path'),
        'url': _value(artifact, 'url'),
        'mime_type': _value(artifact, 'mime_type'),
        'size': _value(artifact, 'size'),
        'metadata': _value(artifact, 'metadata', None)
        or _value(artifact, 'meta', None)
        or {},
    }


def _artifact_idempotency_key(
    *,
    run_id: str,
    participant_id: str,
    terminal_server_id: str | None,
    kind: str,
    path: str,
) -> str:
    terminal_id = terminal_server_id or 'terminal'
    path_identity = _path_identity(run_id, path)
    return f'artifact:{participant_id}:{kind}:{terminal_id}:{path_identity}'


def _path_identity(run_id: str, path: str) -> str:
    run_prefix = f'{AGENT_RUN_WORKSPACE_ROOT}/{run_id}/'
    if path.startswith(run_prefix):
        return f'{run_id}:{path.removeprefix(run_prefix).replace("/", ":")}'
    digest = hashlib.sha256(path.encode('utf-8')).hexdigest()[:16]
    return f'{run_id}:external:{digest}'


def _is_run_scoped_path(path: str, run_id: str, folder: str) -> bool:
    return path.startswith(f'{AGENT_RUN_WORKSPACE_ROOT}/{run_id}/{folder}/')


def _dedupe(paths: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _strip_trailing_slash(path: str) -> str:
    if path == '/':
        return path
    return path.rstrip('/')


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
