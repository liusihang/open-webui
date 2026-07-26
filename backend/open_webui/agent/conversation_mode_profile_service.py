"""Administrator validation and cached reads for conversation mode profiles."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.agent.conversation_mode import ConversationMode
from open_webui.agent.conversation_mode_profiles import (
    INHERIT,
    ConversationModeProfile,
    ProfileDefaults,
)
from open_webui.internal.db import get_async_db_context
from open_webui.models.config import Config
from open_webui.models.conversation_mode_profiles import (
    ConversationModeProfileHistorySnapshotModel,
    ConversationModeProfileIntegrityError,
    ConversationModeProfileRevisionModel,
    ConversationModeProfiles,
)
from open_webui.models.functions import Function
from open_webui.models.skills import Skill
from open_webui.models.tools import Tool
from open_webui.utils.cache_invalidation import (
    CACHE_NAMESPACE_CONVERSATION_MODE_PROFILE_HEADS,
    ensure_cache_fresh,
)

FEATURE_CONFIG_KEYS = {
    'web_search': 'web.search.enable',
    'code_interpreter': 'code_interpreter.enable',
    'image_generation': 'image_generation.enable',
}
PROFILE_REVISION_CACHE_MAX_SIZE = 64


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


class ModeProfileServiceUnavailableError(RuntimeError):
    code = 'mode_profile_service_unavailable'

    def __init__(self, operation: str, *, mode: str | None = None) -> None:
        self.operation = operation
        self.mode = mode
        super().__init__('Conversation mode profile service is unavailable')


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


def get_profile_revision_cache(app) -> OrderedDict[str, ConversationModeProfileRevisionModel]:
    cache = getattr(app.state, 'CONVERSATION_MODE_PROFILE_REVISIONS', None)
    if not isinstance(cache, OrderedDict):
        cache = OrderedDict(cache or {})
        app.state.CONVERSATION_MODE_PROFILE_REVISIONS = cache
    while len(cache) > PROFILE_REVISION_CACHE_MAX_SIZE:
        cache.popitem(last=False)
    return cache


def cache_profile_revision(app, revision: ConversationModeProfileRevisionModel) -> None:
    cache = get_profile_revision_cache(app)
    cache.pop(revision.id, None)
    cache[revision.id] = revision
    while len(cache) > PROFILE_REVISION_CACHE_MAX_SIZE:
        cache.popitem(last=False)


def cache_current_profile_revision(app, revision: ConversationModeProfileRevisionModel) -> None:
    cache_profile_revision(app, revision)
    get_profile_head_cache(app)[revision.mode] = revision


async def get_cached_current_revision(
    app,
    mode: ConversationMode | str,
) -> ConversationModeProfileRevisionModel:
    normalized_mode = _normalized_mode(mode)
    await ensure_cache_fresh(
        app,
        CACHE_NAMESPACE_CONVERSATION_MODE_PROFILE_HEADS,
        normalized_mode,
    )
    try:
        head = await ConversationModeProfiles.get_head(normalized_mode)
    except SQLAlchemyError as exc:
        raise ModeProfileServiceUnavailableError(
            'read_current_head',
            mode=normalized_mode,
        ) from exc
    if head is None:
        raise ModeProfileServiceUnavailableError(
            'read_current_head',
            mode=normalized_mode,
        )
    if head.mode != normalized_mode:
        raise ConversationModeProfileIntegrityError(
            head.current_revision_id,
            f'Conversation mode profile head {head.mode} does not match {normalized_mode}',
        )

    revision = await get_cached_revision(
        app,
        head.current_revision_id,
        expected_mode=normalized_mode,
    )
    if revision is None:
        raise ModeProfileServiceUnavailableError(
            'read_current_revision',
            mode=normalized_mode,
        )
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
        revision_cache.move_to_end(revision_id)
        return cached

    try:
        revision = await ConversationModeProfiles.get_revision(
            revision_id,
            expected_mode=normalized_mode,
        )
    except SQLAlchemyError as exc:
        raise ModeProfileServiceUnavailableError(
            'read_revision',
            mode=normalized_mode,
        ) from exc
    if revision is not None:
        if normalized_mode is not None and revision.mode != normalized_mode:
            raise ConversationModeProfileIntegrityError(
                revision_id,
                f'Conversation mode profile revision {revision_id} has mode '
                f'{revision.mode}, expected {normalized_mode}',
            )
        cache_profile_revision(app, revision)
    return revision


async def get_public_conversation_mode_profiles(app) -> list[dict[str, Any]]:
    profiles = []
    for mode in ('agent', 'chat'):
        revision = await get_cached_current_revision(app, mode)
        if revision is None:
            raise ModeProfileServiceUnavailableError('public_projection', mode=mode)
        profile = ConversationModeProfile(
            mode=revision.mode,
            schema_version=revision.schema_version,
            system_prompt=revision.system_prompt,
            defaults=revision.defaults,
        )
        profiles.append(profile.to_public_dict(current_revision_id=revision.id))
    if [profile['mode'] for profile in profiles] != ['agent', 'chat']:
        raise ModeProfileServiceUnavailableError('public_projection')
    return profiles


async def get_cached_conversation_mode_profile_history(
    app,
    mode: ConversationMode | str,
) -> ConversationModeProfileHistorySnapshotModel:
    normalized_mode = _normalized_mode(mode)
    try:
        snapshot = await ConversationModeProfiles.get_history_snapshot(normalized_mode)
    except SQLAlchemyError as exc:
        raise ModeProfileServiceUnavailableError(
            'read_history',
            mode=normalized_mode,
        ) from exc
    if snapshot is None:
        raise ModeProfileServiceUnavailableError(
            'read_history',
            mode=normalized_mode,
        )
    for revision in snapshot.revisions:
        cache_profile_revision(app, revision)
    current = next(
        (revision for revision in snapshot.revisions if revision.id == snapshot.head.current_revision_id),
        None,
    )
    if current is None:
        raise ConversationModeProfileIntegrityError(
            snapshot.head.current_revision_id,
            f'Conversation mode profile head {normalized_mode} references a missing revision',
        )
    cache_current_profile_revision(app, current)
    return snapshot


async def validate_conversation_mode_profile(
    app,
    mode: ConversationMode | str,
    content: Mapping[str, Any],
) -> tuple[ConversationModeProfile, list[ModeProfileWarning]]:
    profile = ConversationModeProfile.from_mapping(mode, content)
    try:
        async with get_async_db_context() as session:
            issues = await _transactional_resource_issues(
                session,
                profile.defaults,
                lock_rows=False,
            )
    except Exception as exc:
        raise ModeProfileServiceUnavailableError(
            'initial_resource_validation',
            mode=profile.mode.value,
        ) from exc
    if issues:
        raise ModeProfileResourceValidationError(issues)

    try:
        warnings = await _feature_warnings(profile.defaults)
    except Exception as exc:
        raise ModeProfileServiceUnavailableError(
            'initial_resource_validation',
            mode=profile.mode.value,
        ) from exc
    warnings.extend(_model_compatibility_warnings(app, profile.defaults))
    return profile, warnings


async def validate_conversation_mode_profile_precommit(
    session: AsyncSession,
    profile: ConversationModeProfile,
) -> None:
    try:
        issues = await _transactional_resource_issues(
            session,
            profile.defaults,
            lock_rows=True,
        )
    except Exception as exc:
        raise ModeProfileServiceUnavailableError(
            'precommit_resource_validation',
            mode=profile.mode.value,
        ) from exc
    if issues:
        raise ModeProfileResourceValidationError(issues)


def profile_default_counts(profile: ConversationModeProfile) -> dict[str, int]:
    defaults = profile.defaults
    return {
        'terminal': int(defaults.terminal_id is not INHERIT and defaults.terminal_id is not None),
        'tools': _default_count(defaults.tool_ids),
        'skills': _default_count(defaults.skill_ids),
        'filters': _default_count(defaults.filter_ids),
        'features': _default_count(defaults.feature_ids),
    }


async def _transactional_resource_issues(
    session: AsyncSession,
    defaults: ProfileDefaults,
    *,
    lock_rows: bool,
) -> list[ModeProfileResourceIssue]:
    issues = await _transactional_tool_issues(session, defaults, lock_rows=lock_rows)
    issues.extend(await _transactional_skill_issues(session, defaults, lock_rows=lock_rows))
    issues.extend(await _transactional_filter_issues(session, defaults, lock_rows=lock_rows))
    issues.extend(await _transactional_terminal_issues(session, defaults, lock_rows=lock_rows))
    return issues


async def _transactional_tool_issues(
    session: AsyncSession,
    defaults: ProfileDefaults,
    *,
    lock_rows: bool,
) -> list[ModeProfileResourceIssue]:
    issues: list[ModeProfileResourceIssue] = []
    if defaults.tool_ids is not INHERIT and defaults.tool_ids:
        tool_ids = list(defaults.tool_ids)
        statement = _locked_resource_statement(
            select(Tool).where(Tool.id.in_(tool_ids)),
            session,
            lock_rows=lock_rows,
        )
        tools = {tool.id: tool for tool in (await session.execute(statement)).scalars().all()}
        issues.extend(_issue('tool', tool_id, 'missing') for tool_id in tool_ids if tool_id not in tools)
    return issues


async def _transactional_skill_issues(
    session: AsyncSession,
    defaults: ProfileDefaults,
    *,
    lock_rows: bool,
) -> list[ModeProfileResourceIssue]:
    issues: list[ModeProfileResourceIssue] = []
    if defaults.skill_ids is not INHERIT and defaults.skill_ids:
        skill_ids = list(defaults.skill_ids)
        statement = _locked_resource_statement(
            select(Skill).where(Skill.id.in_(skill_ids)),
            session,
            lock_rows=lock_rows,
        )
        skills = {skill.id: skill for skill in (await session.execute(statement)).scalars().all()}
        for skill_id in skill_ids:
            skill = skills.get(skill_id)
            if skill is None:
                issues.append(_issue('skill', skill_id, 'missing'))
            elif not skill.is_active:
                issues.append(_issue('skill', skill_id, 'inactive'))
    return issues


async def _transactional_filter_issues(
    session: AsyncSession,
    defaults: ProfileDefaults,
    *,
    lock_rows: bool,
) -> list[ModeProfileResourceIssue]:
    issues: list[ModeProfileResourceIssue] = []
    if defaults.filter_ids is not INHERIT and defaults.filter_ids:
        filter_ids = list(defaults.filter_ids)
        statement = _locked_resource_statement(
            select(Function).where(Function.id.in_(filter_ids)),
            session,
            lock_rows=lock_rows,
        )
        functions = {function.id: function for function in (await session.execute(statement)).scalars().all()}
        for filter_id in filter_ids:
            function = functions.get(filter_id)
            if function is None:
                issues.append(_issue('filter', filter_id, 'missing'))
            elif function.type != 'filter':
                issues.append(_issue('filter', filter_id, 'wrong_type'))
            elif not function.is_active:
                issues.append(_issue('filter', filter_id, 'inactive'))
    return issues


async def _transactional_terminal_issues(
    session: AsyncSession,
    defaults: ProfileDefaults,
    *,
    lock_rows: bool,
) -> list[ModeProfileResourceIssue]:
    issues: list[ModeProfileResourceIssue] = []
    if defaults.terminal_id is not INHERIT and defaults.terminal_id is not None:
        statement = _locked_resource_statement(
            select(Config).where(Config.key == 'terminal_server.connections'),
            session,
            lock_rows=lock_rows,
        ).execution_options(populate_existing=True)
        row = (await session.execute(statement)).scalars().first()
        connections = [] if row is None else row.value
        if not isinstance(connections, list):
            raise ValueError('Terminal configuration truth is malformed')
        connection = next(
            (
                candidate
                for candidate in connections
                if isinstance(candidate, Mapping) and candidate.get('id') == defaults.terminal_id
            ),
            None,
        )
        if connection is None:
            issues.append(_issue('terminal', defaults.terminal_id, 'missing'))
        elif not connection.get('enabled', True):
            issues.append(_issue('terminal', defaults.terminal_id, 'inactive'))
    return issues


def _locked_resource_statement(statement, session: AsyncSession, *, lock_rows: bool):
    if lock_rows and session.get_bind().dialect.name != 'sqlite':
        return statement.with_for_update()
    return statement


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
