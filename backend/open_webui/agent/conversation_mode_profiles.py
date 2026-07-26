"""Pure contract for administrator-managed conversation mode profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from open_webui.agent.canonical import canonical_sha256
from open_webui.agent.conversation_mode import ConversationMode

PROFILE_SCHEMA_VERSION: Final = 1
ALLOWED_MODES: Final = frozenset(mode.value for mode in ConversationMode)
ALLOWED_FEATURE_IDS: Final = frozenset(
    {'web_search', 'code_interpreter', 'image_generation'}
)

_DEFAULT_FIELDS: Final = (
    'terminal_id',
    'tool_ids',
    'skill_ids',
    'filter_ids',
    'feature_ids',
)
_COLLECTION_DEFAULT_FIELDS: Final = (
    'tool_ids',
    'skill_ids',
    'filter_ids',
    'feature_ids',
)
_ALLOWED_CONTENT_FIELDS: Final = frozenset(
    {'schema_version', 'system_prompt', 'defaults'}
)
_FORBIDDEN_PROFILE_FIELDS: Final = frozenset(
    {
        'model',
        'model_id',
        'model_ids',
        'modelId',
        'modelIds',
        'reasoning_depth',
        'reasoningDepth',
        'reasoning_effort',
        'reasoningEffort',
    }
)


class ProfileInheritance(StrEnum):
    INHERIT = 'inherit'


INHERIT: Final = ProfileInheritance.INHERIT


class ModeProfileError(ValueError):
    code = 'mode_profile_error'
    reason = 'mode_profile_error'

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        field: str | None = None,
    ) -> None:
        if reason is not None:
            self.reason = reason
        self.field = field
        super().__init__(message)


class ModeProfileValidationError(ModeProfileError):
    code = 'invalid_mode_profile'
    reason = 'invalid_mode_profile'


class ModeProfileConflictError(ModeProfileValidationError):
    reason = 'mode_profile_conflict'


TerminalDefault = ProfileInheritance | str | None
CollectionDefault = ProfileInheritance | tuple[str, ...]


@dataclass(frozen=True)
class ProfileDefaults:
    terminal_id: TerminalDefault = INHERIT
    tool_ids: CollectionDefault = INHERIT
    skill_ids: CollectionDefault = INHERIT
    filter_ids: CollectionDefault = INHERIT
    feature_ids: CollectionDefault = INHERIT

    def to_dict(self) -> dict[str, Any]:
        return {
            'terminal_id': _serialize_default(self.terminal_id),
            'tool_ids': _serialize_default(self.tool_ids),
            'skill_ids': _serialize_default(self.skill_ids),
            'filter_ids': _serialize_default(self.filter_ids),
            'feature_ids': _serialize_default(self.feature_ids),
        }


@dataclass(frozen=True)
class ConversationModeProfile:
    mode: ConversationMode
    schema_version: int
    system_prompt: str
    defaults: ProfileDefaults

    @classmethod
    def from_mapping(
        cls,
        mode: ConversationMode | str,
        content: Mapping[str, Any],
    ) -> ConversationModeProfile:
        normalized_mode = _normalize_mode(mode)
        if not isinstance(content, Mapping):
            raise ModeProfileValidationError(
                'Mode profile content must be a mapping',
                reason='invalid_profile_content',
            )

        _validate_content_fields(content)
        schema_version = content.get('schema_version')
        if (
            type(schema_version) is not int
            or schema_version != PROFILE_SCHEMA_VERSION
        ):
            raise ModeProfileValidationError(
                f'Unsupported mode profile schema version: {schema_version!r}',
                reason='unsupported_schema_version',
                field='schema_version',
            )

        if 'system_prompt' not in content:
            raise ModeProfileValidationError(
                'Mode profile system_prompt is required',
                reason='missing_system_prompt',
                field='system_prompt',
            )
        system_prompt = content['system_prompt']
        if not isinstance(system_prompt, str):
            raise ModeProfileValidationError(
                'Mode profile system_prompt must be a string',
                reason='invalid_system_prompt',
                field='system_prompt',
            )

        if 'defaults' not in content:
            raise ModeProfileValidationError(
                'Mode profile defaults are required',
                reason='missing_defaults',
                field='defaults',
            )
        defaults = _normalize_defaults(content['defaults'])
        _validate_known_conflicts(defaults)

        return cls(
            mode=normalized_mode,
            schema_version=schema_version,
            system_prompt=system_prompt,
            defaults=defaults,
        )

    def to_content_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'system_prompt': self.system_prompt,
            'defaults': self.defaults.to_dict(),
        }

    @property
    def content_hash(self) -> str:
        return canonical_sha256(self.to_content_dict())

    def to_public_dict(self, *, current_revision_id: str) -> dict[str, Any]:
        return {
            'mode': self.mode.value,
            'current_revision_id': current_revision_id,
            'schema_version': self.schema_version,
            'defaults': self.defaults.to_dict(),
        }


def resolve_profile_defaults(
    profile_defaults: ProfileDefaults,
    model_defaults: Mapping[str, Any],
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for field in _DEFAULT_FIELDS:
        profile_value = getattr(profile_defaults, field)
        if profile_value is INHERIT:
            model_value = model_defaults.get(field)
            if field in _COLLECTION_DEFAULT_FIELDS:
                resolved[field] = list(model_value or [])
            else:
                resolved[field] = model_value
            continue

        resolved[field] = _serialize_default(profile_value)

    return resolved


def compose_prompt_layers(
    *,
    administrator: str,
    model: str | None = None,
    user: str | None = None,
) -> tuple[str, ...]:
    if not isinstance(administrator, str):
        raise ModeProfileValidationError(
            'Administrator prompt must be a string',
            reason='invalid_system_prompt',
            field='system_prompt',
        )
    for field, value in (('model_prompt', model), ('user_prompt', user)):
        if value is not None and not isinstance(value, str):
            raise ModeProfileValidationError(
                f'{field} must be a string or None',
                reason='invalid_prompt_layer',
                field=field,
            )
    return tuple(
        prompt for prompt in (administrator, model, user) if prompt is not None
    )


def _normalize_mode(mode: ConversationMode | str) -> ConversationMode:
    if isinstance(mode, ConversationMode):
        return mode
    if isinstance(mode, str):
        try:
            return ConversationMode(mode)
        except ValueError:
            pass
    raise ModeProfileValidationError(
        f'Unsupported mode profile mode: {mode!r}',
        reason='unsupported_mode',
        field='mode',
    )


def _validate_content_fields(content: Mapping[str, Any]) -> None:
    for field in content:
        if field in _FORBIDDEN_PROFILE_FIELDS:
            raise ModeProfileValidationError(
                f'{field} is not a mode profile field',
                reason='forbidden_profile_field',
                field=field,
            )
        if field not in _ALLOWED_CONTENT_FIELDS:
            raise ModeProfileValidationError(
                f'Unknown mode profile field: {field}',
                reason='unknown_profile_field',
                field=str(field),
            )


def _normalize_defaults(raw_defaults: Any) -> ProfileDefaults:
    if not isinstance(raw_defaults, Mapping):
        raise ModeProfileValidationError(
            'Mode profile defaults must be a mapping',
            reason='invalid_defaults',
            field='defaults',
        )

    for field in raw_defaults:
        if field in _FORBIDDEN_PROFILE_FIELDS:
            raise ModeProfileValidationError(
                f'{field} is not a mode profile default',
                reason='forbidden_profile_field',
                field=field,
            )
        if field not in _DEFAULT_FIELDS:
            raise ModeProfileValidationError(
                f'Unknown mode profile default: {field}',
                reason='unknown_default_field',
                field=str(field),
            )

    return ProfileDefaults(
        terminal_id=_normalize_terminal_default(raw_defaults),
        tool_ids=_normalize_collection_default(raw_defaults, 'tool_ids'),
        skill_ids=_normalize_collection_default(raw_defaults, 'skill_ids'),
        filter_ids=_normalize_collection_default(raw_defaults, 'filter_ids'),
        feature_ids=_normalize_collection_default(
            raw_defaults,
            'feature_ids',
            allowed_values=ALLOWED_FEATURE_IDS,
        ),
    )


def _normalize_terminal_default(
    raw_defaults: Mapping[str, Any],
) -> TerminalDefault:
    if 'terminal_id' not in raw_defaults or raw_defaults['terminal_id'] == 'inherit':
        return INHERIT
    value = raw_defaults['terminal_id']
    if value is None:
        return None
    return _normalize_identifier(value, field='terminal_id')


def _normalize_collection_default(
    raw_defaults: Mapping[str, Any],
    field: str,
    *,
    allowed_values: frozenset[str] | None = None,
) -> CollectionDefault:
    if field not in raw_defaults or raw_defaults[field] == 'inherit':
        return INHERIT

    value = raw_defaults[field]
    if not isinstance(value, list):
        raise ModeProfileValidationError(
            f'{field} must be inherit or a list of identifiers',
            reason='invalid_default_value',
            field=field,
        )

    normalized = tuple(
        _normalize_identifier(item, field=field) for item in value
    )
    if len(normalized) != len(set(normalized)):
        raise ModeProfileValidationError(
            f'{field} contains duplicate identifiers',
            reason='duplicate_default_identifier',
            field=field,
        )
    if allowed_values is not None:
        unsupported = next(
            (item for item in normalized if item not in allowed_values),
            None,
        )
        if unsupported is not None:
            raise ModeProfileValidationError(
                f'Unsupported {field} value: {unsupported!r}',
                reason='unsupported_feature',
                field=field,
            )
    return normalized


def _normalize_identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ModeProfileValidationError(
            f'{field} identifiers must be non-empty strings without padding',
            reason='invalid_default_identifier',
            field=field,
        )
    return value


def _validate_known_conflicts(defaults: ProfileDefaults) -> None:
    if (
        defaults.terminal_id is not INHERIT
        and defaults.terminal_id is not None
        and defaults.feature_ids is not INHERIT
        and 'code_interpreter' in defaults.feature_ids
    ):
        raise ModeProfileConflictError(
            'Terminal and Code Interpreter cannot both be profile defaults',
            reason='terminal_code_interpreter_conflict',
            field='defaults',
        )


def _serialize_default(value: TerminalDefault | CollectionDefault) -> Any:
    if value is INHERIT:
        return INHERIT.value
    if isinstance(value, tuple):
        return list(value)
    return value
