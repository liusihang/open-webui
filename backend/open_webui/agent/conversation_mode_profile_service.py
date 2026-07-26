"""Administrator validation and cached reads for conversation mode profiles."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.agent.conversation_mode import ConversationMode
from open_webui.agent.conversation_mode_profiles import (
    ALLOWED_FEATURE_IDS,
    INHERIT,
    ConversationModeProfile,
    ProfileDefaults,
    arbitrate_profile_defaults,
    resolve_profile_defaults,
)
from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.internal.db import get_async_db_context
from open_webui.models.chats import ChatForm, ChatModel, Chats
from open_webui.models.config import Config
from open_webui.models.conversation_mode_profiles import (
    ConversationModeProfileHead,
    ConversationModeProfileHistorySnapshotModel,
    ConversationModeProfileIntegrityError,
    ConversationModeProfileRevision,
    ConversationModeProfileRevisionModel,
    ConversationModeProfiles,
)
from open_webui.models.functions import Function, Functions
from open_webui.models.skills import Skill, Skills
from open_webui.models.tools import Tool, Tools
from open_webui.utils.access_control import (
    has_access,
    has_connection_access,
    has_permission,
)
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
PROFILE_CACHE_VERSION_TIMEOUT_SECONDS = 0.1


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


class ModeProfileRevisionHintConflictError(ValueError):
    code = 'mode_profile_revision_conflict'

    def __init__(
        self,
        *,
        hinted_revision_id: str,
        authoritative_revision_id: str,
        bound: bool,
    ) -> None:
        self.hinted_revision_id = hinted_revision_id
        self.authoritative_revision_id = authoritative_revision_id
        self.bound = bound
        super().__init__('Conversation mode profile revision hint is stale or mismatched')


class ModeProfileCapabilityRequestError(ValueError):
    code = 'invalid_mode_profile_capability_request'

    def __init__(self, *, reason: str, field: str) -> None:
        self.reason = reason
        self.field = field
        super().__init__('Conversation capability request is invalid')


class ModeProfileWarning(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    field: str
    resource_ids: list[str] = Field(default_factory=list)
    model_ids: list[str] = Field(default_factory=list)


class ModeProfileRuntimeWarning(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str = 'mode_profile_capability_omitted'
    category: str
    reason: str
    resource_ids: list[str] = Field(default_factory=list)


class ModeProfileCapabilityResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    terminal_id: str | None = None
    tool_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    filter_ids: list[str] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)
    warnings: list[ModeProfileRuntimeWarning] = Field(default_factory=list)


class BoundModeProfileChatCreation(BaseModel):
    model_config = ConfigDict(frozen=True)

    chat: ChatModel
    revision: ConversationModeProfileRevisionModel


def redact_mode_profile_administrator_prompt(
    value: Any,
    revision: ConversationModeProfileRevisionModel | None,
) -> Any:
    if revision is None or not revision.system_prompt:
        return value
    secrets = sorted(
        {
            secret
            for secret in (
                revision.system_prompt,
                revision.system_prompt.strip(),
            )
            if secret
        },
        key=len,
        reverse=True,
    )
    return _redact_mode_profile_value(value, secrets)


def _redact_mode_profile_value(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[administrator prompt redacted]')
        return value
    if isinstance(value, Mapping):
        return {
            _redact_mode_profile_value(key, secrets): _redact_mode_profile_value(
                nested,
                secrets,
            )
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_mode_profile_value(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_mode_profile_value(item, secrets) for item in value)
    if isinstance(value, set):
        return {_redact_mode_profile_value(item, secrets) for item in value}
    return value


def verify_conversation_mode_profile_revision(
    revision: ConversationModeProfileRevisionModel,
    *,
    expected_mode: ConversationMode | str,
) -> ConversationModeProfileRevisionModel:
    normalized_mode = _normalized_mode(expected_mode)
    try:
        profile = ConversationModeProfile.from_mapping(
            revision.mode,
            revision.content,
        )
    except Exception as exc:
        raise ConversationModeProfileIntegrityError(
            revision.id,
            f'Conversation mode profile revision {revision.id} has invalid persisted data',
        ) from exc
    if profile.mode.value != normalized_mode:
        raise ConversationModeProfileIntegrityError(
            revision.id,
            f'Conversation mode profile revision {revision.id} has mode '
            f'{profile.mode.value}, expected {normalized_mode}',
        )
    if profile.content_hash != revision.content_hash:
        raise ConversationModeProfileIntegrityError(
            revision.id,
            f'Conversation mode profile revision {revision.id} failed content hash verification',
        )
    return revision


async def insert_new_chat_with_current_mode_profile(
    app,
    *,
    mode: ConversationMode | str,
    revision_hint: str | None,
    chat_id: str,
    user_id: str,
    form_data: ChatForm,
) -> BoundModeProfileChatCreation:
    normalized_mode = _normalized_mode(mode)
    chat = None
    revision = None
    async with get_async_db_context() as session:
        try:
            dialect_name = session.get_bind().dialect.name
            if dialect_name == 'sqlite':
                await session.execute(text('BEGIN IMMEDIATE'))
            else:
                await session.begin()

            head_statement = (
                select(ConversationModeProfileHead)
                .where(ConversationModeProfileHead.mode == normalized_mode)
                .execution_options(populate_existing=True)
            )
            if dialect_name != 'sqlite':
                head_statement = head_statement.with_for_update()
            head = (await session.execute(head_statement)).scalars().first()
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

            revision_row = await session.get(
                ConversationModeProfileRevision,
                head.current_revision_id,
            )
            if revision_row is None:
                raise ModeProfileServiceUnavailableError(
                    'read_current_revision',
                    mode=normalized_mode,
                )
            revision = _locked_revision_model(
                revision_row,
                expected_mode=normalized_mode,
            )
            verify_conversation_mode_profile_revision(
                revision,
                expected_mode=normalized_mode,
            )
            if revision_hint is not None and revision_hint != revision.id:
                raise ModeProfileRevisionHintConflictError(
                    hinted_revision_id=revision_hint,
                    authoritative_revision_id=revision.id,
                    bound=False,
                )

            chat = await Chats.insert_new_chat(
                chat_id,
                user_id,
                form_data,
                db=session,
                mode_profile_revision_id=revision.id,
                commit=False,
            )
            if chat is None:
                raise ModeProfileServiceUnavailableError(
                    'insert_bound_chat',
                    mode=normalized_mode,
                )
            await session.commit()
        except BaseException:
            if session.in_transaction():
                await session.rollback()
            raise

    cache_profile_revision(app, revision)
    await Chats.dual_write_initial_messages(chat)
    return BoundModeProfileChatCreation(chat=chat, revision=revision)


def _locked_revision_model(
    row: ConversationModeProfileRevision,
    *,
    expected_mode: str,
) -> ConversationModeProfileRevisionModel:
    revision_id = row.id if isinstance(row.id, str) else 'conversation-mode-profile-revision'
    try:
        profile = ConversationModeProfile.from_mapping(
            row.mode,
            {
                'schema_version': row.schema_version,
                'system_prompt': row.system_prompt,
                'defaults': row.defaults,
            },
        )
        revision = ConversationModeProfileRevisionModel(
            id=row.id,
            mode=profile.mode.value,
            revision_number=row.revision_number,
            schema_version=profile.schema_version,
            system_prompt=profile.system_prompt,
            defaults=profile.defaults,
            content_hash=row.content_hash,
            created_at=row.created_at,
            created_by=row.created_by,
            restored_from_revision_id=row.restored_from_revision_id,
        )
        return verify_conversation_mode_profile_revision(
            revision,
            expected_mode=expected_mode,
        )
    except ConversationModeProfileIntegrityError:
        raise
    except Exception as exc:
        raise ConversationModeProfileIntegrityError(
            revision_id,
            f'Conversation mode profile revision {revision_id} has invalid persisted data',
        ) from exc


async def resolve_mode_profile_capabilities(
    app,
    *,
    profile_defaults: ProfileDefaults,
    model: Mapping[str, Any],
    user,
    request_values: Mapping[str, Any],
) -> ModeProfileCapabilityResolution:
    del app
    model_defaults = _runtime_model_defaults(model)
    resolved_defaults = resolve_profile_defaults(profile_defaults, model_defaults)

    tool_ids = _requested_identifier_list(
        request_values,
        'tool_ids',
        resolved_defaults.get('tool_ids'),
    )
    skill_ids = _requested_identifier_list(
        request_values,
        'skill_ids',
        resolved_defaults.get('skill_ids'),
    )
    filter_ids = _requested_identifier_list(
        request_values,
        'filter_ids',
        resolved_defaults.get('filter_ids'),
    )
    terminal_id = _requested_terminal_id(
        request_values,
        resolved_defaults.get('terminal_id'),
    )
    feature_ids = _requested_feature_ids(
        request_values,
        resolved_defaults.get('feature_ids'),
    )
    if (
        request_values.get('terminal_id') is not None
        and isinstance(request_values.get('features'), Mapping)
        and request_values['features'].get('code_interpreter') is True
    ):
        raise ModeProfileCapabilityRequestError(
            reason='terminal_code_interpreter_conflict',
            field='features',
        )

    warnings: list[ModeProfileRuntimeWarning] = []
    capabilities = _model_capabilities(model)
    function_calling_supported = capabilities.get('function_calling') is not False

    filtered_tool_ids = await _filter_runtime_tools(
        tool_ids,
        user=user,
        supported=function_calling_supported,
        warnings=warnings,
    )
    filtered_skill_ids = await _filter_runtime_skills(
        skill_ids,
        user=user,
        supported=function_calling_supported,
        warnings=warnings,
    )
    filtered_filter_ids = await _filter_runtime_filters(
        filter_ids,
        model=model,
        supported=function_calling_supported,
        warnings=warnings,
    )
    filtered_terminal_id = await _filter_runtime_terminal(
        terminal_id,
        user=user,
        supported=(function_calling_supported and capabilities.get('terminal') is not False),
        warnings=warnings,
    )
    filtered_feature_ids = await _filter_runtime_features(
        feature_ids,
        capabilities=capabilities,
        user=user,
        warnings=warnings,
    )

    arbitrated = arbitrate_profile_defaults(
        {
            'terminal_id': filtered_terminal_id,
            'tool_ids': filtered_tool_ids,
            'skill_ids': filtered_skill_ids,
            'filter_ids': filtered_filter_ids,
            'feature_ids': filtered_feature_ids,
        }
    )
    if 'code_interpreter' in filtered_feature_ids and 'code_interpreter' not in arbitrated['feature_ids']:
        _append_runtime_warning(
            warnings,
            category='features',
            reason='terminal_conflict',
            resource_ids=['code_interpreter'],
        )

    return ModeProfileCapabilityResolution(
        terminal_id=arbitrated.get('terminal_id'),
        tool_ids=arbitrated['tool_ids'],
        skill_ids=arbitrated['skill_ids'],
        filter_ids=arbitrated['filter_ids'],
        feature_ids=arbitrated['feature_ids'],
        warnings=warnings,
    )


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


async def get_cached_current_revision(
    app,
    mode: ConversationMode | str,
) -> ConversationModeProfileRevisionModel:
    normalized_mode = _normalized_mode(mode)
    await _refresh_profile_cache_version(app, normalized_mode)
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
    cache_profile_revision(app, revision)
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
    cache_profile_revision(app, current)
    return snapshot


async def save_mode_profile_revision(
    *,
    mode: ConversationMode | str,
    content: dict[str, Any],
    expected_current_revision_id: str,
    created_by: str | None,
) -> ConversationModeProfileRevisionModel:
    normalized_mode = _normalized_mode(mode)
    try:
        return await ConversationModeProfiles.save_revision(
            mode=normalized_mode,
            content=content,
            expected_current_revision_id=expected_current_revision_id,
            created_by=created_by,
            precommit_validator=validate_conversation_mode_profile_precommit,
        )
    except SQLAlchemyError as exc:
        raise ModeProfileServiceUnavailableError(
            'save_revision',
            mode=normalized_mode,
        ) from exc


async def restore_mode_profile_revision(
    *,
    mode: ConversationMode | str,
    source_revision_id: str,
    expected_current_revision_id: str,
    created_by: str | None,
) -> ConversationModeProfileRevisionModel:
    normalized_mode = _normalized_mode(mode)
    try:
        return await ConversationModeProfiles.restore_revision(
            mode=normalized_mode,
            source_revision_id=source_revision_id,
            expected_current_revision_id=expected_current_revision_id,
            created_by=created_by,
            precommit_validator=validate_conversation_mode_profile_precommit,
        )
    except SQLAlchemyError as exc:
        raise ModeProfileServiceUnavailableError(
            'restore_revision',
            mode=normalized_mode,
        ) from exc


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


async def _refresh_profile_cache_version(app, mode: str) -> None:
    try:
        await asyncio.wait_for(
            ensure_cache_fresh(
                app,
                CACHE_NAMESPACE_CONVERSATION_MODE_PROFILE_HEADS,
                mode,
            ),
            timeout=PROFILE_CACHE_VERSION_TIMEOUT_SECONDS,
        )
    except Exception:
        return


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


def _model_meta(model: Any) -> Mapping[str, Any]:
    if not isinstance(model, Mapping):
        return {}
    info = model.get('info') if isinstance(model.get('info'), Mapping) else {}
    meta = info.get('meta') if isinstance(info.get('meta'), Mapping) else {}
    if meta:
        return meta
    direct_meta = model.get('meta')
    return direct_meta if isinstance(direct_meta, Mapping) else {}


def _runtime_model_defaults(model: Any) -> dict[str, Any]:
    meta = _model_meta(model)
    return {
        'terminal_id': _safe_model_terminal_id(meta.get('terminalId')),
        'tool_ids': _safe_model_identifier_list(meta.get('toolIds')),
        'skill_ids': _safe_model_identifier_list(meta.get('skillIds')),
        'filter_ids': _safe_model_identifier_list(meta.get('defaultFilterIds')),
        'feature_ids': [
            feature_id
            for feature_id in _safe_model_identifier_list(meta.get('defaultFeatureIds'))
            if feature_id in ALLOWED_FEATURE_IDS
        ],
    }


def _safe_model_terminal_id(value: Any) -> str | None:
    if isinstance(value, str) and value and value.strip() == value:
        return value
    return None


def _safe_model_identifier_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        if not isinstance(item, str) or not item or item.strip() != item:
            continue
        if item not in result:
            result.append(item)
    return result


def _requested_identifier_list(
    request_values: Mapping[str, Any],
    field: str,
    default: Any,
) -> list[str]:
    if field not in request_values:
        return list(default or [])
    value = request_values[field]
    if not isinstance(value, (list, tuple)):
        raise ModeProfileCapabilityRequestError(
            reason='invalid_identifier_collection',
            field=field,
        )
    normalized = []
    for item in value:
        if not isinstance(item, str) or not item or item.strip() != item:
            raise ModeProfileCapabilityRequestError(
                reason='invalid_identifier',
                field=field,
            )
        if item in normalized:
            raise ModeProfileCapabilityRequestError(
                reason='duplicate_identifier',
                field=field,
            )
        normalized.append(item)
    return normalized


def _requested_terminal_id(
    request_values: Mapping[str, Any],
    default: Any,
) -> str | None:
    if 'terminal_id' not in request_values:
        return default if isinstance(default, str) and default else None
    value = request_values['terminal_id']
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ModeProfileCapabilityRequestError(
            reason='invalid_identifier',
            field='terminal_id',
        )
    return value


def _requested_feature_ids(
    request_values: Mapping[str, Any],
    default: Any,
) -> list[str]:
    feature_ids = [feature_id for feature_id in list(default or []) if feature_id in ALLOWED_FEATURE_IDS]
    if 'features' not in request_values:
        return feature_ids
    features = request_values['features']
    if not isinstance(features, Mapping):
        raise ModeProfileCapabilityRequestError(
            reason='invalid_feature_mapping',
            field='features',
        )
    for feature_id in ('web_search', 'code_interpreter', 'image_generation'):
        if feature_id not in features:
            continue
        enabled = features[feature_id]
        if not isinstance(enabled, bool):
            raise ModeProfileCapabilityRequestError(
                reason='invalid_feature_value',
                field='features',
            )
        if enabled and feature_id not in feature_ids:
            feature_ids.append(feature_id)
        elif not enabled and feature_id in feature_ids:
            feature_ids.remove(feature_id)
    return feature_ids


async def _filter_runtime_tools(
    tool_ids: list[str],
    *,
    user,
    supported: bool,
    warnings: list[ModeProfileRuntimeWarning],
) -> list[str]:
    if not tool_ids:
        return []
    if not supported:
        _append_runtime_warning(
            warnings,
            category='tools',
            reason='model_unsupported',
            resource_ids=tool_ids,
        )
        return []
    local_tool_ids = [tool_id for tool_id in tool_ids if not tool_id.startswith('server:')]
    tools = await Tools.get_tools_by_ids(local_tool_ids)
    allowed_ids = set()
    omitted = []
    for tool_id in tool_ids:
        if tool_id.startswith('server:'):
            if await _runtime_server_tool_access(tool_id, user=user):
                allowed_ids.add(tool_id)
            else:
                omitted.append(tool_id)
            continue
        tool = tools.get(tool_id)
        if tool is not None and await _runtime_resource_access(
            user,
            owner_id=tool.user_id,
            access_grants=tool.access_grants,
        ):
            allowed_ids.add(tool_id)
        else:
            omitted.append(tool_id)
    _append_runtime_warning(
        warnings,
        category='tools',
        reason='unavailable',
        resource_ids=omitted,
    )
    return [tool_id for tool_id in tool_ids if tool_id in allowed_ids]


async def _runtime_server_tool_access(tool_id: str, *, user) -> bool:
    parts = tool_id.split(':')
    if len(parts) == 2:
        server_type = 'openapi'
        server_reference = parts[1]
    elif len(parts) == 3:
        server_type = parts[1]
        server_reference = parts[2]
    else:
        raise ModeProfileCapabilityRequestError(
            reason='invalid_identifier',
            field='tool_ids',
        )
    server_id = server_reference.split('|', 1)[0]
    if server_type != 'openapi' or not server_id:
        raise ModeProfileCapabilityRequestError(
            reason='invalid_identifier',
            field='tool_ids',
        )
    connections = await Config.get('tool_server.connections', []) or []
    connection = next(
        (
            item
            for item in connections
            if isinstance(item, Mapping)
            and (item.get('info') or {}).get('id') == server_id
            and item.get('type', 'openapi') == server_type
        ),
        None,
    )
    if connection is None or not connection.get('enabled', True):
        return False
    return await has_connection_access(user, connection)


async def _filter_runtime_skills(
    skill_ids: list[str],
    *,
    user,
    supported: bool,
    warnings: list[ModeProfileRuntimeWarning],
) -> list[str]:
    if not skill_ids:
        return []
    if not supported:
        _append_runtime_warning(
            warnings,
            category='skills',
            reason='model_unsupported',
            resource_ids=skill_ids,
        )
        return []
    accessible = {skill.id: skill for skill in await Skills.get_skills_by_user_id(user.id, 'read')}
    allowed = []
    unavailable = []
    inactive = []
    for skill_id in skill_ids:
        skill = accessible.get(skill_id)
        if skill is None:
            unavailable.append(skill_id)
        elif not skill.is_active:
            inactive.append(skill_id)
        else:
            allowed.append(skill_id)
    _append_runtime_warning(
        warnings,
        category='skills',
        reason='unavailable',
        resource_ids=unavailable,
    )
    _append_runtime_warning(
        warnings,
        category='skills',
        reason='inactive',
        resource_ids=inactive,
    )
    return allowed


async def _filter_runtime_filters(
    filter_ids: list[str],
    *,
    model: Mapping[str, Any],
    supported: bool,
    warnings: list[ModeProfileRuntimeWarning],
) -> list[str]:
    if not filter_ids:
        return []
    if not supported:
        _append_runtime_warning(
            warnings,
            category='filters',
            reason='model_unsupported',
            resource_ids=filter_ids,
        )
        return []
    functions = {function.id: function for function in await Functions.get_functions_by_ids(filter_ids)}
    supported_ids = set(_safe_model_identifier_list(_model_meta(model).get('filterIds')))
    supported_ids.update(function.id for function in await Functions.get_global_filter_functions())
    allowed = []
    unavailable = []
    inactive = []
    unsupported = []
    for filter_id in filter_ids:
        function = functions.get(filter_id)
        if function is None or function.type != 'filter':
            unavailable.append(filter_id)
        elif not function.is_active:
            inactive.append(filter_id)
        elif filter_id not in supported_ids:
            unsupported.append(filter_id)
        else:
            allowed.append(filter_id)
    for reason, resource_ids in (
        ('unavailable', unavailable),
        ('inactive', inactive),
        ('model_unsupported', unsupported),
    ):
        _append_runtime_warning(
            warnings,
            category='filters',
            reason=reason,
            resource_ids=resource_ids,
        )
    return allowed


async def _filter_runtime_terminal(
    terminal_id: str | None,
    *,
    user,
    supported: bool,
    warnings: list[ModeProfileRuntimeWarning],
) -> str | None:
    if terminal_id is None:
        return None
    if not supported:
        _append_runtime_warning(
            warnings,
            category='terminal',
            reason='model_unsupported',
            resource_ids=[terminal_id],
        )
        return None
    connections = await Config.get('terminal_server.connections', []) or []
    connection = next(
        (item for item in connections if isinstance(item, Mapping) and item.get('id') == terminal_id),
        None,
    )
    if connection is None:
        reason = 'unavailable'
    elif not connection.get('enabled', True):
        reason = 'inactive'
    elif not await has_connection_access(user, connection):
        reason = 'unavailable'
    else:
        return terminal_id
    _append_runtime_warning(
        warnings,
        category='terminal',
        reason=reason,
        resource_ids=[terminal_id],
    )
    return None


async def _filter_runtime_features(
    feature_ids: list[str],
    *,
    capabilities: Mapping[str, Any],
    user,
    warnings: list[ModeProfileRuntimeWarning],
) -> list[str]:
    if not feature_ids:
        return []
    config = await Config.get_many(*(FEATURE_CONFIG_KEYS[feature_id] for feature_id in feature_ids))
    default_permissions = await Config.get('user.permissions', {}) or {}
    allowed = []
    omitted: dict[str, list[str]] = {}
    for feature_id in feature_ids:
        if capabilities.get(feature_id) is False:
            reason = 'model_unsupported'
        elif not config.get(FEATURE_CONFIG_KEYS[feature_id]):
            reason = 'globally_disabled'
        elif user.role != 'admin' and not await has_permission(
            user.id,
            f'features.{feature_id}',
            default_permissions,
        ):
            reason = 'forbidden'
        else:
            allowed.append(feature_id)
            continue
        omitted.setdefault(reason, []).append(feature_id)
    for reason, resource_ids in omitted.items():
        _append_runtime_warning(
            warnings,
            category='features',
            reason=reason,
            resource_ids=resource_ids,
        )
    return allowed


async def _runtime_resource_access(
    user,
    *,
    owner_id: str,
    access_grants: list | None,
) -> bool:
    if user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL:
        return True
    if owner_id == user.id:
        return True
    if not access_grants:
        return False
    return await has_access(
        user.id,
        'read',
        access_grants,
    )


def _append_runtime_warning(
    warnings: list[ModeProfileRuntimeWarning],
    *,
    category: str,
    reason: str,
    resource_ids: list[str],
) -> None:
    if resource_ids:
        warnings.append(
            ModeProfileRuntimeWarning(
                category=category,
                reason=reason,
                resource_ids=list(resource_ids),
            )
        )


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
