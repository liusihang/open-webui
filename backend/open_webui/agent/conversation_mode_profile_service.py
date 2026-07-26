"""Administrator validation and cached reads for conversation mode profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from open_webui.agent.conversation_mode import ConversationMode
from open_webui.agent.conversation_mode_profiles import (
    INHERIT,
    ConversationModeProfile,
    ProfileDefaults,
)
from open_webui.models.config import Config
from open_webui.models.conversation_mode_profiles import (
    ConversationModeProfileIntegrityError,
    ConversationModeProfileRevisionModel,
    ConversationModeProfiles,
)
from open_webui.models.functions import Functions
from open_webui.models.skills import Skills
from open_webui.models.tools import Tools
from open_webui.utils.cache_invalidation import (
    CACHE_NAMESPACE_CONVERSATION_MODE_PROFILE_HEADS,
    ensure_cache_fresh,
)

FEATURE_CONFIG_KEYS = {
    'web_search': 'web.search.enable',
    'code_interpreter': 'code_interpreter.enable',
    'image_generation': 'image_generation.enable',
}


class ModeProfileResourceIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_type: str
    resource_id: str
    reason: str


class ModeProfileResourceValidationError(ValueError):
    code = 'invalid_mode_profile_resource'

    def __init__(self, issues: list[ModeProfileResourceIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__('Conversation mode profile references unavailable resources')


class ModeProfileWarning(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    field: str
    resource_ids: list[str] = Field(default_factory=list)
    model_ids: list[str] = Field(default_factory=list)


def get_profile_head_cache(app) -> dict[str, ConversationModeProfileRevisionModel]:
    cache = getattr(app.state, 'CONVERSATION_MODE_PROFILE_HEADS', None)
    if cache is None:
        cache = {}
        app.state.CONVERSATION_MODE_PROFILE_HEADS = cache
    return cache


def get_profile_revision_cache(app) -> dict[str, ConversationModeProfileRevisionModel]:
    cache = getattr(app.state, 'CONVERSATION_MODE_PROFILE_REVISIONS', None)
    if cache is None:
        cache = {}
        app.state.CONVERSATION_MODE_PROFILE_REVISIONS = cache
    return cache


def cache_profile_revision(app, revision: ConversationModeProfileRevisionModel) -> None:
    get_profile_revision_cache(app)[revision.id] = revision


def cache_current_profile_revision(app, revision: ConversationModeProfileRevisionModel) -> None:
    cache_profile_revision(app, revision)
    get_profile_head_cache(app)[revision.mode] = revision


async def get_cached_current_revision(
    app,
    mode: ConversationMode | str,
) -> ConversationModeProfileRevisionModel | None:
    normalized_mode = _normalized_mode(mode)
    await ensure_cache_fresh(
        app,
        CACHE_NAMESPACE_CONVERSATION_MODE_PROFILE_HEADS,
        normalized_mode,
    )
    head_cache = get_profile_head_cache(app)
    cached = head_cache.get(normalized_mode)
    if cached is not None:
        return cached

    revision = await ConversationModeProfiles.get_current_revision(normalized_mode)
    if revision is None:
        return None
    cache_current_profile_revision(app, revision)
    return revision


async def get_cached_revision(
    app,
    revision_id: str,
    *,
    expected_mode: ConversationMode | str | None = None,
) -> ConversationModeProfileRevisionModel | None:
    normalized_mode = _normalized_mode(expected_mode) if expected_mode is not None else None
    revision_cache = get_profile_revision_cache(app)
    cached = revision_cache.get(revision_id)
    if cached is not None:
        if normalized_mode is not None and cached.mode != normalized_mode:
            raise ConversationModeProfileIntegrityError(
                revision_id,
                f'Conversation mode profile revision {revision_id} has mode {cached.mode}, expected {normalized_mode}',
            )
        return cached

    revision = await ConversationModeProfiles.get_revision(
        revision_id,
        expected_mode=normalized_mode,
    )
    if revision is not None:
        cache_profile_revision(app, revision)
    return revision


async def get_public_conversation_mode_profiles(app) -> list[dict[str, Any]]:
    profiles = []
    for mode in ('agent', 'chat'):
        revision = await get_cached_current_revision(app, mode)
        if revision is None:
            continue
        profile = ConversationModeProfile(
            mode=revision.mode,
            schema_version=revision.schema_version,
            system_prompt=revision.system_prompt,
            defaults=revision.defaults,
        )
        profiles.append(profile.to_public_dict(current_revision_id=revision.id))
    return profiles


async def validate_conversation_mode_profile(
    app,
    mode: ConversationMode | str,
    content: Mapping[str, Any],
) -> tuple[ConversationModeProfile, list[ModeProfileWarning]]:
    profile = ConversationModeProfile.from_mapping(mode, content)
    issues = await _resource_issues(profile.defaults)
    if issues:
        raise ModeProfileResourceValidationError(issues)

    warnings = await _feature_warnings(profile.defaults)
    warnings.extend(_model_compatibility_warnings(app, profile.defaults))
    return profile, warnings


def profile_default_counts(profile: ConversationModeProfile) -> dict[str, int]:
    defaults = profile.defaults
    return {
        'terminal': int(defaults.terminal_id is not INHERIT and defaults.terminal_id is not None),
        'tools': _default_count(defaults.tool_ids),
        'skills': _default_count(defaults.skill_ids),
        'filters': _default_count(defaults.filter_ids),
        'features': _default_count(defaults.feature_ids),
    }


async def _resource_issues(defaults: ProfileDefaults) -> list[ModeProfileResourceIssue]:
    issues = await _tool_issues(defaults)
    issues.extend(await _skill_issues(defaults))
    issues.extend(await _filter_issues(defaults))
    issues.extend(await _terminal_issues(defaults))
    return issues


async def _tool_issues(defaults: ProfileDefaults) -> list[ModeProfileResourceIssue]:
    issues: list[ModeProfileResourceIssue] = []
    if defaults.tool_ids is not INHERIT:
        tool_ids = list(defaults.tool_ids)
        tools = await Tools.get_tools_by_ids(tool_ids)
        for tool_id in tool_ids:
            tool = tools.get(tool_id)
            if tool is None:
                issues.append(_issue('tool', tool_id, 'missing'))
            elif getattr(tool, 'is_active', True) is False:
                issues.append(_issue('tool', tool_id, 'inactive'))
    return issues


async def _skill_issues(defaults: ProfileDefaults) -> list[ModeProfileResourceIssue]:
    issues: list[ModeProfileResourceIssue] = []
    if defaults.skill_ids is not INHERIT:
        for skill_id in defaults.skill_ids:
            skill = await Skills.get_skill_by_id(skill_id)
            if skill is None:
                issues.append(_issue('skill', skill_id, 'missing'))
            elif not skill.is_active:
                issues.append(_issue('skill', skill_id, 'inactive'))
    return issues


async def _filter_issues(defaults: ProfileDefaults) -> list[ModeProfileResourceIssue]:
    issues: list[ModeProfileResourceIssue] = []
    if defaults.filter_ids is not INHERIT:
        filter_ids = list(defaults.filter_ids)
        functions = {function.id: function for function in await Functions.get_functions_by_ids(filter_ids)}
        for filter_id in filter_ids:
            function = functions.get(filter_id)
            if function is None:
                issues.append(_issue('filter', filter_id, 'missing'))
            elif function.type != 'filter':
                issues.append(_issue('filter', filter_id, 'wrong_type'))
            elif not function.is_active:
                issues.append(_issue('filter', filter_id, 'inactive'))
    return issues


async def _terminal_issues(defaults: ProfileDefaults) -> list[ModeProfileResourceIssue]:
    issues: list[ModeProfileResourceIssue] = []
    if defaults.terminal_id is not INHERIT and defaults.terminal_id is not None:
        connections = await Config.get('terminal_server.connections', []) or []
        connection = next(
            (candidate for candidate in connections if candidate.get('id') == defaults.terminal_id),
            None,
        )
        if connection is None:
            issues.append(_issue('terminal', defaults.terminal_id, 'missing'))
        elif not connection.get('enabled', True):
            issues.append(_issue('terminal', defaults.terminal_id, 'inactive'))
    return issues


async def _feature_warnings(defaults: ProfileDefaults) -> list[ModeProfileWarning]:
    if defaults.feature_ids is INHERIT or not defaults.feature_ids:
        return []
    feature_ids = list(defaults.feature_ids)
    config = await Config.get_many(*(FEATURE_CONFIG_KEYS[feature_id] for feature_id in feature_ids))
    disabled = [feature_id for feature_id in feature_ids if config.get(FEATURE_CONFIG_KEYS[feature_id]) is False]
    if not disabled:
        return []
    return [
        ModeProfileWarning(
            code='feature_globally_disabled',
            field='feature_ids',
            resource_ids=disabled,
        )
    ]


def _model_compatibility_warnings(app, defaults: ProfileDefaults) -> list[ModeProfileWarning]:
    models = getattr(app.state, 'MODELS', None)
    if not isinstance(models, Mapping) or not models:
        return []

    warnings: list[ModeProfileWarning] = []
    if defaults.feature_ids is not INHERIT:
        for feature_id in defaults.feature_ids:
            unsupported = [
                str(model_id)
                for model_id, model in models.items()
                if _model_capabilities(model).get(feature_id) is False
            ]
            if unsupported:
                warnings.append(
                    ModeProfileWarning(
                        code='model_compatibility_warning',
                        field='feature_ids',
                        resource_ids=[feature_id],
                        model_ids=unsupported,
                    )
                )

    model_sensitive_defaults = (defaults.terminal_id is not INHERIT and defaults.terminal_id is not None) or any(
        value is not INHERIT and bool(value) for value in (defaults.tool_ids, defaults.skill_ids, defaults.filter_ids)
    )
    if model_sensitive_defaults:
        unsupported = [
            str(model_id)
            for model_id, model in models.items()
            if _model_capabilities(model).get('function_calling') is False
        ]
        if unsupported:
            warnings.append(
                ModeProfileWarning(
                    code='model_compatibility_warning',
                    field='defaults',
                    model_ids=unsupported,
                )
            )
    return warnings


def _model_capabilities(model: Any) -> Mapping[str, Any]:
    if not isinstance(model, Mapping):
        return {}
    info = model.get('info') if isinstance(model.get('info'), Mapping) else {}
    meta = info.get('meta') if isinstance(info.get('meta'), Mapping) else {}
    capabilities = meta.get('capabilities')
    if not isinstance(capabilities, Mapping):
        direct_meta = model.get('meta') if isinstance(model.get('meta'), Mapping) else {}
        capabilities = direct_meta.get('capabilities')
    return capabilities if isinstance(capabilities, Mapping) else {}


def _normalized_mode(mode: ConversationMode | str) -> str:
    return mode.value if isinstance(mode, ConversationMode) else ConversationMode(mode).value


def _issue(resource_type: str, resource_id: str, reason: str) -> ModeProfileResourceIssue:
    return ModeProfileResourceIssue(
        resource_type=resource_type,
        resource_id=resource_id,
        reason=reason,
    )


def _default_count(value) -> int:
    return 0 if value is INHERIT else len(value)
