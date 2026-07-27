"""Typed persistence for immutable administrator conversation mode profiles."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from open_webui.agent.conversation_mode import ConversationMode, resolve_conversation_mode
from open_webui.agent.conversation_mode_profiles import (
    ConversationModeProfile,
    ModeProfileValidationError,
    ProfileDefaults,
)
from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

if TYPE_CHECKING:
    from open_webui.models.chats import Chat, ChatModel


def _baseline_revision_id(mode: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f'open-webui:conversation-mode-profile:{mode}:baseline:v1',
        )
    )


CHAT_BASELINE_REVISION_ID = _baseline_revision_id('chat')
AGENT_BASELINE_REVISION_ID = _baseline_revision_id('agent')


class ConversationModeProfileRevision(Base):
    __tablename__ = 'conversation_mode_profile_revision'

    id = Column(String(36), primary_key=True)
    mode = Column(String(16), nullable=False)
    revision_number = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False)
    system_prompt = Column(Text, nullable=False)
    defaults = Column(JSON, nullable=False)
    content_hash = Column(String(64), nullable=False)
    created_at = Column(BigInteger, nullable=False)
    created_by = Column(Text, nullable=True)
    restored_from_revision_id = Column(
        String(36),
        ForeignKey(
            'conversation_mode_profile_revision.id',
            name='fk_conversation_mode_profile_revision_restored_from',
            ondelete='RESTRICT',
        ),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            'mode',
            'revision_number',
            name='uq_conversation_mode_profile_revision_mode_number',
        ),
        Index(
            'ix_conversation_mode_profile_revision_mode_created_at',
            'mode',
            'created_at',
        ),
    )


class ConversationModeProfileHead(Base):
    __tablename__ = 'conversation_mode_profile_head'

    mode = Column(String(16), primary_key=True)
    current_revision_id = Column(
        String(36),
        ForeignKey(
            'conversation_mode_profile_revision.id',
            name='fk_conversation_mode_profile_head_current_revision',
            ondelete='RESTRICT',
        ),
        nullable=False,
    )
    baseline_revision_id = Column(
        String(36),
        ForeignKey(
            'conversation_mode_profile_revision.id',
            name='fk_conversation_mode_profile_head_baseline_revision',
            ondelete='RESTRICT',
        ),
        nullable=False,
    )
    cutover_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    updated_by = Column(Text, nullable=True)


class ConversationModeProfileTemporaryBinding(Base):
    __tablename__ = 'conversation_mode_profile_temporary_binding'

    id = Column(String(36), primary_key=True)
    user_id = Column(Text, nullable=False)
    temporary_conversation_id = Column(Text, nullable=False)
    mode = Column(String(16), nullable=False)
    mode_profile_revision_id = Column(
        String(36),
        ForeignKey(
            'conversation_mode_profile_revision.id',
            name='fk_conversation_mode_profile_temporary_binding_revision',
            ondelete='RESTRICT',
        ),
        nullable=False,
    )
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    expires_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            'user_id',
            'temporary_conversation_id',
            name='uq_conv_mode_profile_temp_user_conversation',
        ),
        Index(
            'ix_conversation_mode_profile_temporary_binding_expires_at',
            'expires_at',
        ),
    )


class ConversationModeProfileStoreError(ValueError):
    code = 'mode_profile_store_error'


class ConversationModeProfileRevisionConflict(ConversationModeProfileStoreError):
    code = 'mode_profile_revision_conflict'

    def __init__(
        self,
        *,
        mode: str,
        expected_revision_id: str,
        actual_revision_id: str | None,
    ) -> None:
        self.mode = mode
        self.expected_revision_id = expected_revision_id
        self.actual_revision_id = actual_revision_id
        super().__init__(
            f'Conversation mode profile head for {mode} changed from {expected_revision_id} to {actual_revision_id}'
        )


class ConversationModeProfileIntegrityError(ConversationModeProfileStoreError):
    code = 'mode_profile_integrity_error'

    def __init__(self, revision_id: str, message: str) -> None:
        self.revision_id = revision_id
        super().__init__(message)


class ConversationModeProfileTransactionStateError(ConversationModeProfileStoreError):
    code = 'mode_profile_transaction_state_error'

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f'Caller-owned database session has incompatible state: {reason}')


class ConversationModeProfileBindingConflict(ConversationModeProfileStoreError):
    code = 'mode_profile_binding_mismatch'

    def __init__(
        self,
        *,
        binding_id: str,
        expected_revision_id: str,
        actual_revision_id: str,
    ) -> None:
        self.binding_id = binding_id
        self.expected_revision_id = expected_revision_id
        self.actual_revision_id = actual_revision_id
        super().__init__(
            f'Conversation mode profile binding {binding_id} already points to '
            f'{actual_revision_id}, not {expected_revision_id}'
        )


class ConversationModeProfileBindingIntegrityError(ConversationModeProfileStoreError):
    code = 'mode_profile_binding_integrity_error'

    def __init__(
        self,
        *,
        chat_id: str,
        chat_mode: str,
        revision_id: str,
        revision_mode: str,
    ) -> None:
        self.chat_id = chat_id
        self.chat_mode = chat_mode
        self.revision_id = revision_id
        self.revision_mode = revision_mode
        super().__init__(
            f'Chat {chat_id} has explicit mode {chat_mode}, which does not match '
            f'revision {revision_id} mode {revision_mode}'
        )


class ConversationModeProfileLegacyBindingError(ConversationModeProfileStoreError):
    code = 'mode_profile_unbound_conversation'

    def __init__(self, *, chat_id: str) -> None:
        self.chat_id = chat_id
        super().__init__('Conversation mode profile binding is required for this conversation')


class ConversationModeProfileHeadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True, strict=True)

    mode: str
    current_revision_id: str
    baseline_revision_id: str
    cutover_at: int
    updated_at: int
    updated_by: str | None = None


class ConversationModeProfileRevisionModel(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, strict=True)

    id: str
    mode: str
    revision_number: int
    schema_version: int
    system_prompt: str = Field(repr=False)
    defaults: ProfileDefaults
    content_hash: str
    created_at: int
    created_by: str | None = None
    restored_from_revision_id: str | None = None

    @property
    def content(self) -> Mapping[str, Any]:
        defaults = MappingProxyType(
            {
                field: tuple(value) if isinstance(value, list) else value
                for field, value in self.defaults.to_dict().items()
            }
        )
        return MappingProxyType(
            {
                'schema_version': self.schema_version,
                'system_prompt': self.system_prompt,
                'defaults': defaults,
            }
        )


class ConversationModeProfileHistorySnapshotModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    head: ConversationModeProfileHeadModel
    revisions: tuple[ConversationModeProfileRevisionModel, ...]


class ConversationModeProfileTemporaryBindingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True, strict=True)

    id: str
    user_id: str
    temporary_conversation_id: str
    mode: str
    mode_profile_revision_id: str
    created_at: int
    updated_at: int
    expires_at: int


class ConversationModeProfileChatBindingModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    chat_id: str
    user_id: str
    mode_profile_revision_id: str


class ConversationModeProfilePersistedChatResolutionModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    chat_id: str
    user_id: str
    mode: str
    mode_profile_revision_id: str
    binding: ConversationModeProfileChatBindingModel


def _normalized_mode(mode: ConversationMode | str) -> str:
    try:
        return ConversationMode(mode).value
    except ValueError as exc:
        raise ModeProfileValidationError(
            f'Unsupported mode profile mode: {mode!r}',
            reason='unsupported_mode',
            field='mode',
        ) from exc


def _now_seconds() -> int:
    return int(time.time())


async def _begin_write_transaction(session: AsyncSession) -> str:
    dialect_name = session.get_bind().dialect.name
    if dialect_name == 'sqlite':
        await session.execute(text('BEGIN IMMEDIATE'))
    else:
        await session.begin()
    return dialect_name


def _ensure_clean_caller_session(session: AsyncSession) -> None:
    if session.new or session.dirty or session.deleted:
        raise ConversationModeProfileTransactionStateError('pending_work')
    if session.in_transaction():
        raise ConversationModeProfileTransactionStateError('active_transaction')


@asynccontextmanager
async def _managed_write_session(
    session: AsyncSession,
    *,
    repository_owned: bool,
) -> AsyncIterator[tuple[AsyncSession, str]]:
    try:
        dialect_name = await _begin_write_transaction(session)
        yield session, dialect_name
        await session.flush()
        if repository_owned:
            await session.commit()
    except BaseException:
        if session.in_transaction():
            await session.rollback()
        raise


@asynccontextmanager
async def _write_session(
    db: AsyncSession | None,
) -> AsyncIterator[tuple[AsyncSession, str]]:
    if db is not None:
        _ensure_clean_caller_session(db)
        async with _managed_write_session(db, repository_owned=False) as write_session:
            yield write_session
        return

    async with get_async_db_context() as session:
        async with _managed_write_session(session, repository_owned=True) as write_session:
            yield write_session


def _revision_to_model(
    row: ConversationModeProfileRevision,
    *,
    expected_mode: str | None = None,
) -> ConversationModeProfileRevisionModel:
    revision_id = row.id if isinstance(row.id, str) else 'conversation-mode-profile-revision'
    try:
        content = {
            'schema_version': row.schema_version,
            'system_prompt': row.system_prompt,
            'defaults': row.defaults,
        }
        profile = ConversationModeProfile.from_mapping(row.mode, content)
        if expected_mode is not None and profile.mode.value != expected_mode:
            raise ConversationModeProfileIntegrityError(
                revision_id,
                f'Conversation mode profile revision {revision_id} has mode '
                f'{profile.mode.value}, expected {expected_mode}',
            )
        if profile.content_hash != row.content_hash:
            raise ConversationModeProfileIntegrityError(
                revision_id,
                f'Conversation mode profile revision {revision_id} failed content hash verification',
            )
        return ConversationModeProfileRevisionModel(
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
    except ConversationModeProfileIntegrityError:
        raise
    except (ModeProfileValidationError, ValidationError, TypeError):
        raise ConversationModeProfileIntegrityError(
            revision_id,
            f'Conversation mode profile revision {revision_id} has invalid persisted data',
        ) from None


def _head_to_model(
    row: ConversationModeProfileHead,
) -> ConversationModeProfileHeadModel:
    revision_id = (
        row.current_revision_id if isinstance(row.current_revision_id, str) else 'conversation-mode-profile-head'
    )
    try:
        return ConversationModeProfileHeadModel.model_validate(row)
    except (ValidationError, TypeError):
        raise ConversationModeProfileIntegrityError(
            revision_id,
            f'Conversation mode profile head for revision {revision_id} has invalid persisted data',
        ) from None


def _assert_chat_mode_agreement(
    chat: Chat,
    *,
    revision_id: str,
    revision_mode: str,
) -> None:
    chat_content = chat.chat if isinstance(chat.chat, Mapping) else {}
    explicit_mode = chat_content.get('mode')
    if explicit_mode is None or explicit_mode == revision_mode:
        return
    raise ConversationModeProfileBindingIntegrityError(
        chat_id=chat.id,
        chat_mode=str(explicit_mode),
        revision_id=revision_id,
        revision_mode=revision_mode,
    )


class ConversationModeProfileTable:
    async def get_heads(
        self,
        db: AsyncSession | None = None,
    ) -> list[ConversationModeProfileHeadModel]:
        async with get_async_db_context(db) as session:
            result = await session.execute(
                select(ConversationModeProfileHead).order_by(ConversationModeProfileHead.mode.asc())
            )
            return [_head_to_model(row) for row in result.scalars().all()]

    async def get_head(
        self,
        mode: ConversationMode | str,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileHeadModel | None:
        normalized_mode = _normalized_mode(mode)
        async with get_async_db_context(db) as session:
            row = await session.get(ConversationModeProfileHead, normalized_mode)
            return _head_to_model(row) if row is not None else None

    async def get_revision(
        self,
        revision_id: str,
        *,
        expected_mode: ConversationMode | str | None = None,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileRevisionModel | None:
        normalized_mode = _normalized_mode(expected_mode) if expected_mode is not None else None
        async with get_async_db_context(db) as session:
            row = await session.get(ConversationModeProfileRevision, revision_id)
            return _revision_to_model(row, expected_mode=normalized_mode) if row is not None else None

    async def get_current_revision(
        self,
        mode: ConversationMode | str,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileRevisionModel | None:
        normalized_mode = _normalized_mode(mode)
        async with get_async_db_context(db) as session:
            head = await session.get(ConversationModeProfileHead, normalized_mode)
            if head is None:
                return None
            head_model = _head_to_model(head)
            row = await session.get(
                ConversationModeProfileRevision,
                head_model.current_revision_id,
            )
            if row is None:
                raise ConversationModeProfileIntegrityError(
                    head_model.current_revision_id,
                    f'Conversation mode profile head {normalized_mode} references a missing revision',
                )
            return _revision_to_model(row, expected_mode=normalized_mode)

    async def get_baseline_revision(
        self,
        mode: ConversationMode | str,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileRevisionModel | None:
        normalized_mode = _normalized_mode(mode)
        async with get_async_db_context(db) as session:
            head = await session.get(ConversationModeProfileHead, normalized_mode)
            if head is None:
                return None
            head_model = _head_to_model(head)
            row = await session.get(
                ConversationModeProfileRevision,
                head_model.baseline_revision_id,
            )
            if row is None:
                raise ConversationModeProfileIntegrityError(
                    head_model.baseline_revision_id,
                    f'Conversation mode profile head {normalized_mode} references a missing baseline',
                )
            return _revision_to_model(row, expected_mode=normalized_mode)

    async def list_history(
        self,
        mode: ConversationMode | str,
        db: AsyncSession | None = None,
    ) -> list[ConversationModeProfileRevisionModel]:
        normalized_mode = _normalized_mode(mode)
        async with get_async_db_context(db) as session:
            result = await session.execute(
                select(ConversationModeProfileRevision)
                .where(ConversationModeProfileRevision.mode == normalized_mode)
                .order_by(ConversationModeProfileRevision.revision_number.desc())
            )
            return [_revision_to_model(row, expected_mode=normalized_mode) for row in result.scalars().all()]

    async def get_history_snapshot(
        self,
        mode: ConversationMode | str,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileHistorySnapshotModel | None:
        normalized_mode = _normalized_mode(mode)
        async with get_async_db_context(db) as session:
            head_statement = (
                select(ConversationModeProfileHead)
                .where(ConversationModeProfileHead.mode == normalized_mode)
                .execution_options(populate_existing=True)
            )
            if session.get_bind().dialect.name == 'postgresql':
                head_statement = head_statement.with_for_update(read=True)
            head = (await session.execute(head_statement)).scalars().first()
            if head is None:
                return None
            head_model = _head_to_model(head)

            revision_rows = (
                (
                    await session.execute(
                        select(ConversationModeProfileRevision)
                        .where(ConversationModeProfileRevision.mode == normalized_mode)
                        .order_by(ConversationModeProfileRevision.revision_number.desc())
                    )
                )
                .scalars()
                .all()
            )
            revisions = tuple(_revision_to_model(row, expected_mode=normalized_mode) for row in revision_rows)
            if not any(revision.id == head_model.current_revision_id for revision in revisions):
                raise ConversationModeProfileIntegrityError(
                    head_model.current_revision_id,
                    f'Conversation mode profile head {normalized_mode} references a missing revision',
                )
            return ConversationModeProfileHistorySnapshotModel(
                head=head_model,
                revisions=revisions,
            )

    async def _lock_head(
        self,
        session: AsyncSession,
        mode: str,
        dialect_name: str,
    ) -> ConversationModeProfileHead | None:
        statement = (
            select(ConversationModeProfileHead)
            .where(ConversationModeProfileHead.mode == mode)
            .execution_options(populate_existing=True)
        )
        if dialect_name != 'sqlite':
            statement = statement.with_for_update()
        return (await session.execute(statement)).scalars().first()

    async def _save_revision(
        self,
        *,
        mode: ConversationMode | str,
        content: dict[str, Any],
        expected_current_revision_id: str,
        created_by: str | None,
        restored_from_revision_id: str | None,
        now: int | None,
        db: AsyncSession | None,
        precommit_validator: Callable[[AsyncSession, ConversationModeProfile], Awaitable[None]] | None,
    ) -> ConversationModeProfileRevisionModel:
        profile = ConversationModeProfile.from_mapping(mode, content)
        normalized_mode = profile.mode.value
        timestamp = _now_seconds() if now is None else now

        async with _write_session(db) as (session, dialect_name):
            row = await self._insert_revision_and_switch_head(
                session,
                profile=profile,
                expected_current_revision_id=expected_current_revision_id,
                created_by=created_by,
                restored_from_revision_id=restored_from_revision_id,
                timestamp=timestamp,
                dialect_name=dialect_name,
                precommit_validator=precommit_validator,
            )
            return _revision_to_model(row, expected_mode=normalized_mode)

    async def _insert_revision_and_switch_head(
        self,
        session: AsyncSession,
        *,
        profile: ConversationModeProfile,
        expected_current_revision_id: str,
        created_by: str | None,
        restored_from_revision_id: str | None,
        timestamp: int,
        dialect_name: str,
        precommit_validator: Callable[[AsyncSession, ConversationModeProfile], Awaitable[None]] | None = None,
    ) -> ConversationModeProfileRevision:
        normalized_mode = profile.mode.value
        head = await self._lock_head(session, normalized_mode, dialect_name)
        if head is None:
            raise ConversationModeProfileIntegrityError(
                expected_current_revision_id,
                f'Conversation mode profile head {normalized_mode} is unavailable',
            )
        if head.current_revision_id != expected_current_revision_id:
            actual_revision_id = head.current_revision_id
            raise ConversationModeProfileRevisionConflict(
                mode=normalized_mode,
                expected_revision_id=expected_current_revision_id,
                actual_revision_id=actual_revision_id,
            )

        if precommit_validator is not None:
            await precommit_validator(session, profile)

        revision_number = (
            await session.scalar(
                select(func.max(ConversationModeProfileRevision.revision_number)).where(
                    ConversationModeProfileRevision.mode == normalized_mode
                )
            )
            or 0
        ) + 1
        normalized_content = profile.to_content_dict()
        row = ConversationModeProfileRevision(
            id=str(uuid.uuid4()),
            mode=normalized_mode,
            revision_number=revision_number,
            schema_version=profile.schema_version,
            system_prompt=profile.system_prompt,
            defaults=normalized_content['defaults'],
            content_hash=profile.content_hash,
            created_at=timestamp,
            created_by=created_by,
            restored_from_revision_id=restored_from_revision_id,
        )
        session.add(row)
        await session.flush()
        head.current_revision_id = row.id
        head.updated_at = timestamp
        head.updated_by = created_by
        return row

    async def save_revision(
        self,
        *,
        mode: ConversationMode | str,
        content: dict[str, Any],
        expected_current_revision_id: str,
        created_by: str | None,
        now: int | None = None,
        db: AsyncSession | None = None,
        precommit_validator: Callable[[AsyncSession, ConversationModeProfile], Awaitable[None]] | None = None,
    ) -> ConversationModeProfileRevisionModel:
        return await self._save_revision(
            mode=mode,
            content=content,
            expected_current_revision_id=expected_current_revision_id,
            created_by=created_by,
            restored_from_revision_id=None,
            now=now,
            db=db,
            precommit_validator=precommit_validator,
        )

    async def restore_revision(
        self,
        *,
        mode: ConversationMode | str,
        source_revision_id: str,
        expected_current_revision_id: str,
        created_by: str | None,
        now: int | None = None,
        db: AsyncSession | None = None,
        precommit_validator: Callable[[AsyncSession, ConversationModeProfile], Awaitable[None]] | None = None,
    ) -> ConversationModeProfileRevisionModel:
        normalized_mode = _normalized_mode(mode)
        timestamp = _now_seconds() if now is None else now
        async with _write_session(db) as (session, dialect_name):
            source_row = await session.get(
                ConversationModeProfileRevision,
                source_revision_id,
            )
            if source_row is None:
                raise ConversationModeProfileIntegrityError(
                    source_revision_id,
                    f'Conversation mode profile revision {source_revision_id} is unavailable',
                )
            source = _revision_to_model(source_row, expected_mode=normalized_mode)
            profile = ConversationModeProfile.from_mapping(normalized_mode, source.content)
            row = await self._insert_revision_and_switch_head(
                session,
                profile=profile,
                expected_current_revision_id=expected_current_revision_id,
                created_by=created_by,
                restored_from_revision_id=source_revision_id,
                timestamp=timestamp,
                dialect_name=dialect_name,
                precommit_validator=precommit_validator,
            )
            return _revision_to_model(row, expected_mode=normalized_mode)

    async def get_chat_binding(
        self,
        *,
        chat_id: str,
        user_id: str | None = None,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileChatBindingModel | None:
        from open_webui.models.chats import Chat

        async with get_async_db_context(db) as session:
            statement = select(Chat.id, Chat.user_id, Chat.mode_profile_revision_id).where(Chat.id == chat_id)
            if user_id is not None:
                statement = statement.where(Chat.user_id == user_id)
            row = (await session.execute(statement)).first()
            if row is None or row.mode_profile_revision_id is None:
                return None
            return ConversationModeProfileChatBindingModel(
                chat_id=row.id,
                user_id=row.user_id,
                mode_profile_revision_id=row.mode_profile_revision_id,
            )

    async def _lock_chat(
        self,
        session: AsyncSession,
        *,
        chat_id: str,
        user_id: str | None,
        dialect_name: str,
    ) -> Chat | None:
        from open_webui.models.chats import Chat

        statement = select(Chat).where(Chat.id == chat_id)
        if user_id is not None:
            statement = statement.where(Chat.user_id == user_id)
        statement = statement.execution_options(populate_existing=True)
        if dialect_name != 'sqlite':
            statement = statement.with_for_update()
        return (await session.execute(statement)).scalars().first()

    async def _claim_chat_binding_in_session(
        self,
        session: AsyncSession,
        *,
        chat_id: str,
        user_id: str | None,
        revision: ConversationModeProfileRevisionModel,
        dialect_name: str,
    ) -> ConversationModeProfileChatBindingModel | None:
        chat = await self._lock_chat(
            session,
            chat_id=chat_id,
            user_id=user_id,
            dialect_name=dialect_name,
        )
        if chat is None:
            return None
        _assert_chat_mode_agreement(
            chat,
            revision_id=revision.id,
            revision_mode=revision.mode,
        )
        if chat.mode_profile_revision_id is None:
            chat.mode_profile_revision_id = revision.id
        elif chat.mode_profile_revision_id != revision.id:
            raise ConversationModeProfileBindingConflict(
                binding_id=chat_id,
                expected_revision_id=revision.id,
                actual_revision_id=chat.mode_profile_revision_id,
            )
        return ConversationModeProfileChatBindingModel(
            chat_id=chat.id,
            user_id=chat.user_id,
            mode_profile_revision_id=chat.mode_profile_revision_id,
        )

    async def claim_chat_binding(
        self,
        *,
        chat_id: str,
        revision_id: str,
        user_id: str | None = None,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileChatBindingModel | None:
        async with _write_session(db) as (session, dialect_name):
            revision_row = await session.get(
                ConversationModeProfileRevision,
                revision_id,
            )
            if revision_row is None:
                raise ConversationModeProfileIntegrityError(
                    revision_id,
                    f'Conversation mode profile revision {revision_id} is unavailable',
                )
            revision = _revision_to_model(revision_row)
            return await self._claim_chat_binding_in_session(
                session,
                chat_id=chat_id,
                user_id=user_id,
                revision=revision,
                dialect_name=dialect_name,
            )

    async def resolve_persisted_chat_binding(
        self,
        *,
        chat_id: str,
        user_id: str | None,
        requested_mode: str | None,
        has_agent_run: bool,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfilePersistedChatResolutionModel | None:
        """Resolve a persisted chat's immutable profile binding without consulting a later head.

        Unbound chats are only a migration-compatibility case: they must have
        existed at or before the mode head's cutover and bind to that mode's
        immutable baseline revision.  The whole decision and write occur in a
        single write transaction so concurrent workers converge.
        """
        async with _write_session(db) as (session, dialect_name):
            chat = await self._lock_chat(
                session,
                chat_id=chat_id,
                user_id=user_id,
                dialect_name=dialect_name,
            )
            if chat is None:
                return None

            return await self._resolve_locked_persisted_chat_binding(
                session,
                chat=chat,
                requested_mode=requested_mode,
                has_agent_run=has_agent_run,
                dialect_name=dialect_name,
            )

    async def resolve_and_update_persisted_chat(
        self,
        *,
        chat_id: str,
        user_id: str | None,
        update_patch: Mapping[str, Any],
        requested_mode: str | None,
        has_agent_run: bool | None = None,
        db: AsyncSession | None = None,
    ) -> ChatModel | None:
        """Atomically resolve a persisted binding and save the sanitized chat payload.

        The Chat row lock is acquired before its mode-profile head, matching the
        resolver's existing lock order.  A caller-owned session remains
        uncommitted; repository-owned calls commit exactly once on success.
        """
        from open_webui.agent.conversation_mode import chat_has_agent_mode_evidence
        from open_webui.models.chats import ChatModel, Chats

        async with _write_session(db) as (session, dialect_name):
            chat = await self._lock_chat(
                session,
                chat_id=chat_id,
                user_id=user_id,
                dialect_name=dialect_name,
            )
            if chat is None:
                return None

            chat_content = dict(chat.chat) if isinstance(chat.chat, Mapping) else {}
            resolved_has_agent_run = has_agent_run
            if resolved_has_agent_run is None:
                resolved_has_agent_run = chat_has_agent_mode_evidence(chat_content)
                if not resolved_has_agent_run and chat_content.get('mode') is None:
                    from open_webui.models.agent_runs import AgentRuns

                    resolved_has_agent_run = await AgentRuns.has_runs_by_chat(
                        chat.id,
                        chat.user_id,
                        db=session,
                    )

            resolution = await self._resolve_locked_persisted_chat_binding(
                session,
                chat=chat,
                requested_mode=requested_mode,
                has_agent_run=resolved_has_agent_run,
                dialect_name=dialect_name,
            )

            patch = dict(update_patch)
            patch.pop('mode_profile_revision_id', None)
            updated_chat = {**chat_content, **patch}
            updated_chat['mode'] = resolution.mode
            if 'history' in patch:
                updated_chat['history'] = Chats.merge_history(
                    chat_content.get('history'),
                    patch.get('history'),
                )
            updated_chat.pop('mode_profile_revision_id', None)

            chat.chat = Chats._clean_null_bytes(updated_chat)
            title = updated_chat['title'] if 'title' in updated_chat else 'New Chat'
            chat.title = Chats._clean_null_bytes(title)
            chat.updated_at = _now_seconds()
            await session.flush()
            return ChatModel.model_validate(chat)

    async def _resolve_locked_persisted_chat_binding(
        self,
        session: AsyncSession,
        *,
        chat: Chat,
        requested_mode: str | None,
        has_agent_run: bool,
        dialect_name: str,
    ) -> ConversationModeProfilePersistedChatResolutionModel:
        """Resolve one already-locked Chat row without starting another transaction."""
        chat_content = dict(chat.chat) if isinstance(chat.chat, Mapping) else {}
        resolution = resolve_conversation_mode(
            requested=requested_mode,
            persisted=chat_content.get('mode'),
            is_new=False,
            has_agent_run=has_agent_run,
        )
        mode = resolution.mode.value

        if chat.mode_profile_revision_id is not None:
            revision_row = await session.get(
                ConversationModeProfileRevision,
                chat.mode_profile_revision_id,
            )
            if revision_row is None:
                raise ConversationModeProfileIntegrityError(
                    chat.mode_profile_revision_id,
                    f'Conversation mode profile revision {chat.mode_profile_revision_id} is unavailable',
                )
            revision = _revision_to_model(revision_row, expected_mode=mode)
            binding = ConversationModeProfileChatBindingModel(
                chat_id=chat.id,
                user_id=chat.user_id,
                mode_profile_revision_id=revision.id,
            )
            return ConversationModeProfilePersistedChatResolutionModel(
                chat_id=chat.id,
                user_id=chat.user_id,
                mode=mode,
                mode_profile_revision_id=revision.id,
                binding=binding,
            )

        head = await self._lock_head(session, mode, dialect_name)
        if head is None:
            raise ConversationModeProfileIntegrityError(
                '',
                f'Conversation mode profile head {mode} is unavailable',
            )
        if chat.created_at > head.cutover_at:
            raise ConversationModeProfileLegacyBindingError(chat_id=chat.id)

        baseline_row = await session.get(
            ConversationModeProfileRevision,
            head.baseline_revision_id,
        )
        if baseline_row is None:
            raise ConversationModeProfileIntegrityError(
                head.baseline_revision_id,
                f'Conversation mode profile baseline revision for {mode} is unavailable',
            )
        baseline = _revision_to_model(baseline_row, expected_mode=mode)
        if resolution.should_persist:
            chat_content['mode'] = mode
            chat.chat = chat_content
        chat.mode_profile_revision_id = baseline.id
        binding = ConversationModeProfileChatBindingModel(
            chat_id=chat.id,
            user_id=chat.user_id,
            mode_profile_revision_id=baseline.id,
        )
        return ConversationModeProfilePersistedChatResolutionModel(
            chat_id=chat.id,
            user_id=chat.user_id,
            mode=mode,
            mode_profile_revision_id=baseline.id,
            binding=binding,
        )

    async def _recover_temporary_binding_insert_conflict(
        self,
        session: AsyncSession,
        *,
        binding_statement: Select[tuple[ConversationModeProfileTemporaryBinding]],
        inserted_row: ConversationModeProfileTemporaryBinding,
        user_id: str,
        temporary_conversation_id: str,
        normalized_mode: str,
        expected_revision_id: str,
        expires_at: int,
        timestamp: int,
        integrity_error: IntegrityError,
    ) -> ConversationModeProfileTemporaryBindingModel:
        winner = (await session.execute(binding_statement)).scalars().first()
        if winner is None:
            raise ConversationModeProfileIntegrityError(
                inserted_row.id,
                'Temporary conversation mode profile binding insert failed without a winning row',
            ) from integrity_error
        winner_revision = await session.get(
            ConversationModeProfileRevision,
            winner.mode_profile_revision_id,
            populate_existing=True,
        )
        if winner_revision is None:
            raise ConversationModeProfileIntegrityError(
                winner.mode_profile_revision_id,
                f'Temporary binding {winner.id} references a missing revision',
            ) from integrity_error
        _revision_to_model(winner_revision, expected_mode=winner.mode)
        if winner.mode != normalized_mode:
            raise ConversationModeProfileBindingConflict(
                binding_id=f'{user_id}:{temporary_conversation_id}',
                expected_revision_id=expected_revision_id,
                actual_revision_id=winner.mode_profile_revision_id,
            ) from integrity_error
        winner.expires_at = max(winner.expires_at, expires_at)
        winner.updated_at = timestamp
        return ConversationModeProfileTemporaryBindingModel.model_validate(winner)

    async def create_temporary_binding(
        self,
        *,
        user_id: str,
        temporary_conversation_id: str,
        mode: ConversationMode | str,
        expires_at: int,
        now: int | None = None,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileTemporaryBindingModel:
        normalized_mode = _normalized_mode(mode)
        timestamp = _now_seconds() if now is None else now
        if expires_at <= timestamp:
            raise ValueError('Temporary conversation mode profile binding must expire in the future')

        async with _write_session(db) as (session, dialect_name):
            head = await self._lock_head(session, normalized_mode, dialect_name)
            if head is None:
                raise ConversationModeProfileIntegrityError(
                    '',
                    f'Conversation mode profile head {normalized_mode} is unavailable',
                )

            binding_statement = (
                select(ConversationModeProfileTemporaryBinding)
                .where(
                    ConversationModeProfileTemporaryBinding.user_id == user_id,
                    ConversationModeProfileTemporaryBinding.temporary_conversation_id == temporary_conversation_id,
                )
                .execution_options(populate_existing=True)
            )
            if dialect_name != 'sqlite':
                binding_statement = binding_statement.with_for_update()
            row = (await session.execute(binding_statement)).scalars().first()
            if row is not None and row.expires_at <= timestamp:
                await session.delete(row)
                await session.flush()
                row = None
            if row is not None:
                if row.mode != normalized_mode:
                    expected_revision_id = head.current_revision_id
                    actual_revision_id = row.mode_profile_revision_id
                    raise ConversationModeProfileBindingConflict(
                        binding_id=f'{user_id}:{temporary_conversation_id}',
                        expected_revision_id=expected_revision_id,
                        actual_revision_id=actual_revision_id,
                    )
                row.expires_at = max(row.expires_at, expires_at)
                row.updated_at = timestamp
                return ConversationModeProfileTemporaryBindingModel.model_validate(row)

            revision = await session.get(
                ConversationModeProfileRevision,
                head.current_revision_id,
            )
            if revision is None:
                raise ConversationModeProfileIntegrityError(
                    head.current_revision_id,
                    f'Conversation mode profile head {normalized_mode} references a missing revision',
                )
            _revision_to_model(revision, expected_mode=normalized_mode)
            row = ConversationModeProfileTemporaryBinding(
                id=str(uuid.uuid4()),
                user_id=user_id,
                temporary_conversation_id=temporary_conversation_id,
                mode=normalized_mode,
                mode_profile_revision_id=revision.id,
                created_at=timestamp,
                updated_at=timestamp,
                expires_at=expires_at,
            )
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError as exc:
                return await self._recover_temporary_binding_insert_conflict(
                    session,
                    binding_statement=binding_statement,
                    inserted_row=row,
                    user_id=user_id,
                    temporary_conversation_id=temporary_conversation_id,
                    normalized_mode=normalized_mode,
                    expected_revision_id=head.current_revision_id,
                    expires_at=expires_at,
                    timestamp=timestamp,
                    integrity_error=exc,
                )
            return ConversationModeProfileTemporaryBindingModel.model_validate(row)

    async def get_temporary_binding(
        self,
        *,
        user_id: str,
        temporary_conversation_id: str,
        now: int | None = None,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileTemporaryBindingModel | None:
        timestamp = _now_seconds() if now is None else now
        async with get_async_db_context(db) as session:
            row = (
                (
                    await session.execute(
                        select(ConversationModeProfileTemporaryBinding).where(
                            ConversationModeProfileTemporaryBinding.user_id == user_id,
                            ConversationModeProfileTemporaryBinding.temporary_conversation_id
                            == temporary_conversation_id,
                            ConversationModeProfileTemporaryBinding.expires_at > timestamp,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            revision = await session.get(
                ConversationModeProfileRevision,
                row.mode_profile_revision_id,
            )
            if revision is None:
                raise ConversationModeProfileIntegrityError(
                    row.mode_profile_revision_id,
                    f'Temporary binding {row.id} references a missing revision',
                )
            _revision_to_model(revision, expected_mode=row.mode)
            return ConversationModeProfileTemporaryBindingModel.model_validate(row)

    async def transfer_temporary_binding(
        self,
        *,
        user_id: str,
        temporary_conversation_id: str,
        chat_id: str,
        now: int | None = None,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileChatBindingModel | None:
        timestamp = _now_seconds() if now is None else now
        async with _write_session(db) as (session, dialect_name):
            statement = (
                select(ConversationModeProfileTemporaryBinding)
                .where(
                    ConversationModeProfileTemporaryBinding.user_id == user_id,
                    ConversationModeProfileTemporaryBinding.temporary_conversation_id == temporary_conversation_id,
                )
                .execution_options(populate_existing=True)
            )
            if dialect_name != 'sqlite':
                statement = statement.with_for_update()
            temporary = (await session.execute(statement)).scalars().first()
            if temporary is None:
                return None
            if temporary.expires_at <= timestamp:
                await session.delete(temporary)
                return None

            revision = await session.get(
                ConversationModeProfileRevision,
                temporary.mode_profile_revision_id,
            )
            if revision is None:
                raise ConversationModeProfileIntegrityError(
                    temporary.mode_profile_revision_id,
                    f'Temporary binding {temporary.id} references a missing revision',
                )
            revision_model = _revision_to_model(revision, expected_mode=temporary.mode)
            binding = await self._claim_chat_binding_in_session(
                session,
                chat_id=chat_id,
                user_id=user_id,
                revision=revision_model,
                dialect_name=dialect_name,
            )
            if binding is None:
                return None
            await session.delete(temporary)
            return binding

    async def cleanup_expired_temporary_bindings(
        self,
        *,
        now: int | None = None,
        db: AsyncSession | None = None,
    ) -> int:
        timestamp = _now_seconds() if now is None else now
        async with _write_session(db) as (session, _dialect_name):
            result = await session.execute(
                delete(ConversationModeProfileTemporaryBinding).where(
                    ConversationModeProfileTemporaryBinding.expires_at <= timestamp
                )
            )
            return int(result.rowcount or 0)


ConversationModeProfiles = ConversationModeProfileTable()
