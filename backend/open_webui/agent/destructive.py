from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DestructiveAssessment:
    requires_approval: bool
    category: str | None = None
    reason: str = ''
    matched: str = ''
    action: str = ''
    details: dict[str, Any] = field(default_factory=dict)


_DELETE_TOOL_NAMES = {
    'delete',
    'delete_entry',
    'delete_file',
    'delete_folder',
    'remove',
    'remove_file',
    'remove_folder',
    'rmdir',
    'trash',
    'unlink',
}

_OVERWRITE_TOOL_NAMES = {
    'apply_patch',
    'replace',
    'replace_file',
    'replace_file_content',
    'save_file',
    'upload_file',
    'write',
    'write_file',
}

_DELETE_COMMAND_PATTERNS = (
    re.compile(r'(^|[;&|()]\s*)(rm|rmdir|unlink|shred)\b'),
)

_SINGLE_REDIRECT_PATTERN = re.compile(r'(^|[^>])>(?!>)\s*(?P<target>\S+)')

_OVERWRITE_COMMAND_PATTERNS = (
    re.compile(r'(^|[;&|()]\s*)(mv|truncate)\b'),
    re.compile(r'(^|[;&|()]\s*)cp\s+-(?:[^;&|]*f|[^;&|]*T)\b'),
    re.compile(r'(^|[;&|()]\s*)(sed|perl)\b[^;&|]*\s-i(?:\s|$)'),
    re.compile(r'(^|[;&|()]\s*)dd\b[^;&|]*\bof='),
    re.compile(r'(^|[;&|()]\s*)tee\b'),
    _SINGLE_REDIRECT_PATTERN,
    re.compile(r'(^|[;&|()]\s*)apply_patch\b'),
)


def classify_destructive_tool_call(
    *,
    tool_name: str | None,
    tool_id: str | None,
    tool_type: str | None,
    arguments: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> DestructiveAssessment:
    args = arguments or {}
    meta = metadata or {}

    if meta.get('requires_approval') or meta.get('destructive'):
        category = str(meta.get('destructive_category') or 'destructive')
        return _requires_approval(
            category,
            reason='Tool metadata marks this action as destructive.',
            matched='metadata.requires_approval',
            action=_action_summary(tool_name, args),
            details={'tool_id': tool_id, 'tool_type': tool_type},
        )

    operation = _lower_arg(args, 'operation') or _lower_arg(args, 'action')
    if operation in _DELETE_TOOL_NAMES:
        return _requires_approval(
            'delete',
            reason='Tool arguments request a delete action.',
            matched=f'operation:{operation}',
            action=_action_summary(tool_name, args),
            details={'tool_id': tool_id, 'tool_type': tool_type},
        )
    if operation in _OVERWRITE_TOOL_NAMES:
        return _requires_approval(
            'overwrite',
            reason='Tool arguments request an overwrite action.',
            matched=f'operation:{operation}',
            action=_action_summary(tool_name, args),
            details={'tool_id': tool_id, 'tool_type': tool_type},
        )

    name = (tool_name or '').lower()
    if name in _DELETE_TOOL_NAMES or name.startswith(('delete_', 'remove_')):
        return _requires_approval(
            'delete',
            reason='Tool name is a delete/remove operation.',
            matched=name,
            action=_action_summary(tool_name, args),
            details={'tool_id': tool_id, 'tool_type': tool_type},
        )
    if name in _OVERWRITE_TOOL_NAMES or name.startswith(('write_', 'replace_')):
        return _requires_approval(
            'overwrite',
            reason='Tool name is a write/replace operation.',
            matched=name,
            action=_action_summary(tool_name, args),
            details={'tool_id': tool_id, 'tool_type': tool_type},
        )

    if name == 'run_command':
        return _classify_command(
            args.get('command'),
            run_id=meta.get('run_id') if isinstance(meta.get('run_id'), str) else None,
            tool_id=tool_id,
            tool_type=tool_type,
        )

    return DestructiveAssessment(
        requires_approval=False,
        action=_action_summary(tool_name, args),
        details={'tool_id': tool_id, 'tool_type': tool_type},
    )


def _classify_command(
    command: Any,
    *,
    run_id: str | None = None,
    tool_id: str | None,
    tool_type: str | None,
) -> DestructiveAssessment:
    text = command if isinstance(command, str) else ''
    for pattern in _DELETE_COMMAND_PATTERNS:
        match = pattern.search(text)
        if match:
            return _requires_approval(
                'delete',
                reason='Shell command contains an obvious delete operation.',
                matched=match.group(2),
                action=_command_action_summary(text),
                details={'tool_id': tool_id, 'tool_type': tool_type},
            )

    for pattern in _OVERWRITE_COMMAND_PATTERNS:
        match = pattern.search(text)
        if match:
            if pattern is _SINGLE_REDIRECT_PATTERN and _redirects_only_to_run_artifact_dir(
                text, run_id
            ):
                continue
            matched = (
                match.group(2)
                if match.lastindex and match.lastindex >= 2
                else match.group(0).strip()
            )
            return _requires_approval(
                'overwrite',
                reason='Shell command contains an obvious overwrite operation.',
                matched=matched,
                action=_command_action_summary(text),
                details={'tool_id': tool_id, 'tool_type': tool_type},
            )

    return DestructiveAssessment(
        requires_approval=False,
        action=_command_action_summary(text),
        details={'tool_id': tool_id, 'tool_type': tool_type},
    )


def _requires_approval(
    category: str,
    *,
    reason: str,
    matched: str,
    action: str,
    details: dict[str, Any],
) -> DestructiveAssessment:
    return DestructiveAssessment(
        requires_approval=True,
        category=category,
        reason=reason,
        matched=matched,
        action=action,
        details=details,
    )


def _lower_arg(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    return value.lower() if isinstance(value, str) else None


def _action_summary(tool_name: str | None, arguments: dict[str, Any]) -> str:
    target = (
        arguments.get('path')
        or arguments.get('target_path')
        or arguments.get('file_path')
        or arguments.get('name')
        or arguments.get('id')
    )
    if target:
        return f'{tool_name or "tool"} {target}'
    return tool_name or 'tool call'


def _command_action_summary(command: str) -> str:
    return f'run_command {command[:160]}' if command else 'run_command'


def _redirects_only_to_run_artifact_dir(command: str, run_id: str | None) -> bool:
    if not run_id:
        return False
    matches = list(_SINGLE_REDIRECT_PATTERN.finditer(command))
    if not matches:
        return False
    return all(_is_run_artifact_path(match.group('target'), run_id) for match in matches)


def _is_run_artifact_path(path: str, run_id: str) -> bool:
    cleaned = path.strip('\'"')
    safe_prefixes = (
        f'/workspace/agent-runs/{run_id}/outputs/',
        f'/workspace/agent-runs/{run_id}/tmp/',
    )
    return cleaned.startswith(safe_prefixes)
