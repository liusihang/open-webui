"""Typed persistence for immutable administrator conversation mode profiles."""

from __future__ import annotations

import time
import uuid
from typing import Any

from open_webui.agent.conversation_mode import ConversationMode
from open_webui.agent.conversation_mode_profiles import (
    ConversationModeProfile,
    ModeProfileValidationError,
)
from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
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
from sqlalchemy.ext.asyncio import AsyncSession


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
            name='uq_conversation_mode_profile_temporary_binding_user_conversation',
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


class ConversationModeProfileHeadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    mode: str
    current_revision_id: str
    baseline_revision_id: str
    cutover_at: int
    updated_at: int
    updated_by: str | None = None


class ConversationModeProfileRevisionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: str
    mode: str
    revision_number: int
    schema_version: int
    system_prompt: str
    defaults: dict[str, Any]
    content_hash: str
    created_at: int
    created_by: str | None = None
    restored_from_revision_id: str | None = None

    @property
    def content(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'system_prompt': self.system_prompt,
            'defaults': dict(self.defaults),
        }


class ConversationModeProfileTemporaryBindingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: str
    user_id: str
    temporary_conversation_id: str
    mode: str
    mode_profile_revision_id: str
    created_at: int
    updated_at: int
    expires_at: int


class ConversationModeProfileChatBindingModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    chat_id: str
    user_id: str
    mode_profile_revision_id: str


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
    if dialect_name == 'sqlite' and not session.in_transaction():
        await session.execute(text('BEGIN IMMEDIATE'))
    return dialect_name


def _revision_to_model(
    row: ConversationModeProfileRevision,
    *,
    expected_mode: str | None = None,
) -> ConversationModeProfileRevisionModel:
    content = {
        'schema_version': row.schema_version,
        'system_prompt': row.system_prompt,
        'defaults': row.defaults,
    }
    try:
        profile = ConversationModeProfile.from_mapping(row.mode, content)
    except ModeProfileValidationError as exc:
        raise ConversationModeProfileIntegrityError(
            row.id,
            f'Conversation mode profile revision {row.id} is invalid: {exc}',
        ) from exc
    if expected_mode is not None and profile.mode.value != expected_mode:
        raise ConversationModeProfileIntegrityError(
            row.id,
            f'Conversation mode profile revision {row.id} has mode {profile.mode.value}, expected {expected_mode}',
        )
    if profile.content_hash != row.content_hash:
        raise ConversationModeProfileIntegrityError(
            row.id,
            f'Conversation mode profile revision {row.id} failed content hash verification',
        )
    return ConversationModeProfileRevisionModel.model_validate(row)


class ConversationModeProfileTable:
    async def get_heads(
        self,
        db: AsyncSession | None = None,
    ) -> list[ConversationModeProfileHeadModel]:
        async with get_async_db_context(db) as session:
            result = await session.execute(
                select(ConversationModeProfileHead).order_by(ConversationModeProfileHead.mode.asc())
            )
            return [ConversationModeProfileHeadModel.model_validate(row) for row in result.scalars().all()]

    async def get_head(
        self,
        mode: ConversationMode | str,
        db: AsyncSession | None = None,
    ) -> ConversationModeProfileHeadModel | None:
        normalized_mode = _normalized_mode(mode)
        async with get_async_db_context(db) as session:
            row = await session.get(ConversationModeProfileHead, normalized_mode)
            return ConversationModeProfileHeadModel.model_validate(row) if row is not None else None

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
            row = await session.get(
                ConversationModeProfileRevision,
                head.current_revision_id,
            )
            if row is None:
                raise ConversationModeProfileIntegrityError(
                    head.current_revision_id,
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
            row = await session.get(
                ConversationModeProfileRevision,
                head.baseline_revision_id,
            )
            if row is None:
                raise ConversationModeProfileIntegrityError(
                    head.baseline_revision_id,
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

    async def _lock_head(
        self,
        session: AsyncSession,
        mode: str,
        dialect_name: str,
    ) -> ConversationModeProfileHead | None:
        statement = select(ConversationModeProfileHead).where(ConversationModeProfileHead.mode == mode)
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
    ) -> ConversationModeProfileRevisionModel:
        profile = ConversationModeProfile.from_mapping(mode, content)
        normalized_mode = profile.mode.value
        timestamp = _now_seconds() if now is None else now

        async with get_async_db_context(db) as session:
            dialect_name = await _begin_write_transaction(session)
            row = await self._insert_revision_and_switch_head(
                session,
                profile=profile,
                expected_current_revision_id=expected_current_revision_id,
                created_by=created_by,
                restored_from_revision_id=restored_from_revision_id,
                timestamp=timestamp,
                dialect_name=dialect_name,
            )
            await session.commit()
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
    ) -> ConversationModeProfileRevision:
        normalized_mode = profile.mode.value
        head = await self._lock_head(session, normalized_mode, dialect_name)
        if head is None:
            await session.rollback()
            raise ConversationModeProfileIntegrityError(
                expected_current_revision_id,
                f'Conversation mode profile head {normalized_mode} is unavailable',
            )
        if head.current_revision_id != expected_current_revision_id:
            actual_revision_id = head.current_revision_id
            await session.rollback()
            raise ConversationModeProfileRevisionConflict(
                mode=normalized_mode,
                expected_revision_id=expected_current_revision_id,
                actual_revision_id=actual_revision_id,
            )

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
    ) -> ConversationModeProfileRevisionModel:
        return await self._save_revision(
            mode=mode,
            content=content,
            expected_current_revision_id=expected_current_revision_id,
            created_by=created_by,
            restored_from_revision_id=None,
            now=now,
            db=db,
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
    ) -> ConversationModeProfileRevisionModel:
        normalized_mode = _normalized_mode(mode)
        timestamp = _now_seconds() if now is None else now
        async with get_async_db_context(db) as session:
            dialect_name = await _begin_write_transaction(session)
            source_row = await session.get(
                ConversationModeProfileRevision,
                source_revision_id,
            )
            if source_row is None:
                await session.rollback()
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
            )
            await session.commit()
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
    ):
        from open_webui.models.chats import Chat

        statement = select(Chat).where(Chat.id == chat_id)
        if user_id is not None:
            statement = statement.where(Chat.user_id == user_id)
        if dialect_name != 'sqlite':
            statement = statement.with_for_update()
        return (await session.execute(statement)).scalars().first()

    async def _claim_chat_binding_in_session(
        self,
        session: AsyncSession,
        *,
        chat_id: str,
        user_id: str | None,
        revision_id: str,
        dialect_name: str,
    ):
        chat = await self._lock_chat(
            session,
            chat_id=chat_id,
            user_id=user_id,
            dialect_name=dialect_name,
        )
        if chat is None:
            return None
        if chat.mode_profile_revision_id is None:
            chat.mode_profile_revision_id = revision_id
        elif chat.mode_profile_revision_id != revision_id:
            raise ConversationModeProfileBindingConflict(
                binding_id=chat_id,
                expected_revision_id=revision_id,
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
        async with get_async_db_context(db) as session:
            dialect_name = await _begin_write_transaction(session)
            revision_row = await session.get(
                ConversationModeProfileRevision,
                revision_id,
            )
            if revision_row is None:
                await session.rollback()
                raise ConversationModeProfileIntegrityError(
                    revision_id,
                    f'Conversation mode profile revision {revision_id} is unavailable',
                )
            revision = _revision_to_model(revision_row)
            try:
                binding = await self._claim_chat_binding_in_session(
                    session,
                    chat_id=chat_id,
                    user_id=user_id,
                    revision_id=revision.id,
                    dialect_name=dialect_name,
                )
            except ConversationModeProfileBindingConflict:
                await session.rollback()
                raise
            if binding is None:
                await session.rollback()
                return None
            await session.commit()
            return binding

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

        async with get_async_db_context(db) as session:
            dialect_name = await _begin_write_transaction(session)
            head_statement = select(ConversationModeProfileHead).order_by(ConversationModeProfileHead.mode.asc())
            if dialect_name != 'sqlite':
                head_statement = head_statement.with_for_update()
            heads = {head.mode: head for head in (await session.execute(head_statement)).scalars().all()}
            head = heads.get(normalized_mode)
            if head is None:
                await session.rollback()
                raise ConversationModeProfileIntegrityError(
                    '',
                    f'Conversation mode profile head {normalized_mode} is unavailable',
                )

            binding_statement = select(ConversationModeProfileTemporaryBinding).where(
                ConversationModeProfileTemporaryBinding.user_id == user_id,
                ConversationModeProfileTemporaryBinding.temporary_conversation_id == temporary_conversation_id,
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
                    await session.rollback()
                    raise ConversationModeProfileBindingConflict(
                        binding_id=f'{user_id}:{temporary_conversation_id}',
                        expected_revision_id=expected_revision_id,
                        actual_revision_id=actual_revision_id,
                    )
                row.expires_at = max(row.expires_at, expires_at)
                row.updated_at = timestamp
                await session.commit()
                return ConversationModeProfileTemporaryBindingModel.model_validate(row)

            revision = await session.get(
                ConversationModeProfileRevision,
                head.current_revision_id,
            )
            if revision is None:
                await session.rollback()
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
            session.add(row)
            await session.commit()
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
        async with get_async_db_context(db) as session:
            dialect_name = await _begin_write_transaction(session)
            statement = select(ConversationModeProfileTemporaryBinding).where(
                ConversationModeProfileTemporaryBinding.user_id == user_id,
                ConversationModeProfileTemporaryBinding.temporary_conversation_id == temporary_conversation_id,
            )
            if dialect_name != 'sqlite':
                statement = statement.with_for_update()
            temporary = (await session.execute(statement)).scalars().first()
            if temporary is None:
                await session.rollback()
                return None
            if temporary.expires_at <= timestamp:
                await session.delete(temporary)
                await session.commit()
                return None

            revision = await session.get(
                ConversationModeProfileRevision,
                temporary.mode_profile_revision_id,
            )
            if revision is None:
                await session.rollback()
                raise ConversationModeProfileIntegrityError(
                    temporary.mode_profile_revision_id,
                    f'Temporary binding {temporary.id} references a missing revision',
                )
            _revision_to_model(revision, expected_mode=temporary.mode)
            try:
                binding = await self._claim_chat_binding_in_session(
                    session,
                    chat_id=chat_id,
                    user_id=user_id,
                    revision_id=temporary.mode_profile_revision_id,
                    dialect_name=dialect_name,
                )
            except ConversationModeProfileBindingConflict:
                await session.rollback()
                raise
            if binding is None:
                await session.rollback()
                return None
            await session.delete(temporary)
            await session.commit()
            return binding

    async def cleanup_expired_temporary_bindings(
        self,
        *,
        now: int | None = None,
        db: AsyncSession | None = None,
    ) -> int:
        timestamp = _now_seconds() if now is None else now
        async with get_async_db_context(db) as session:
            await _begin_write_transaction(session)
            result = await session.execute(
                delete(ConversationModeProfileTemporaryBinding).where(
                    ConversationModeProfileTemporaryBinding.expires_at <= timestamp
                )
            )
            await session.commit()
            return int(result.rowcount or 0)


ConversationModeProfiles = ConversationModeProfileTable()
