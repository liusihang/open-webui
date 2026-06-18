from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from open_webui.agent.compaction import build_compacted_run_summary
from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, BigInteger, Column, Index, Integer, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession


class AgentRunState(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    WAITING_APPROVAL = 'waiting_approval'
    FINALIZING = 'finalizing'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    BUDGET_EXCEEDED = 'budget_exceeded'


TERMINAL_STATES = {
    AgentRunState.COMPLETED,
    AgentRunState.FAILED,
    AgentRunState.CANCELLED,
    AgentRunState.BUDGET_EXCEEDED,
}

LEGAL_TRANSITIONS: set[tuple[AgentRunState, AgentRunState]] = {
    (AgentRunState.QUEUED, AgentRunState.RUNNING),
    (AgentRunState.QUEUED, AgentRunState.FAILED),
    (AgentRunState.QUEUED, AgentRunState.CANCELLED),
    (AgentRunState.RUNNING, AgentRunState.WAITING_APPROVAL),
    (AgentRunState.RUNNING, AgentRunState.FINALIZING),
    (AgentRunState.RUNNING, AgentRunState.FAILED),
    (AgentRunState.RUNNING, AgentRunState.CANCELLED),
    (AgentRunState.RUNNING, AgentRunState.BUDGET_EXCEEDED),
    (AgentRunState.WAITING_APPROVAL, AgentRunState.RUNNING),
    (AgentRunState.WAITING_APPROVAL, AgentRunState.FAILED),
    (AgentRunState.WAITING_APPROVAL, AgentRunState.CANCELLED),
    (AgentRunState.WAITING_APPROVAL, AgentRunState.BUDGET_EXCEEDED),
    (AgentRunState.FINALIZING, AgentRunState.COMPLETED),
    (AgentRunState.FINALIZING, AgentRunState.FAILED),
    (AgentRunState.FINALIZING, AgentRunState.CANCELLED),
    (AgentRunState.FINALIZING, AgentRunState.BUDGET_EXCEEDED),
}


class AgentRunError(ValueError):
    code = 'agent_run_error'


class AgentRunStateError(AgentRunError):
    code = 'invalid_state_transition'


class AgentRunOperationConflict(AgentRunError):
    code = 'idempotency_conflict'


class AgentRunNotFound(AgentRunError):
    code = 'agent_run_not_found'


class AgentRun(Base):
    __tablename__ = 'agent_run'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False, index=True)
    chat_id = Column(Text, nullable=False, index=True)
    user_message_id = Column(Text, nullable=False)
    assistant_message_id = Column(Text, nullable=False)
    state = Column(Text, nullable=False, index=True)
    state_version = Column(Integer, nullable=False, default=0)
    leader_model_id = Column(Text, nullable=False)
    runtime_session_id = Column(Text, nullable=True)
    budget = Column(JSON, nullable=True)
    participants = Column(JSON, nullable=True)
    tool_access_snapshot = Column(JSON, nullable=True)
    model_catalog_snapshot = Column(JSON, nullable=True)
    process_refs = Column(JSON, nullable=True)
    summary = Column(JSON, nullable=True)
    error = Column(JSON, nullable=True)
    final_text = Column(Text, nullable=False, default='')
    final_delta_state = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index('ix_agent_run_chat_created', 'chat_id', 'created_at'),
        Index('ix_agent_run_user_created', 'user_id', 'created_at'),
        Index('ix_agent_run_state_updated', 'state', 'updated_at'),
    )


class AgentRunEvent(Base):
    __tablename__ = 'agent_run_event'

    id = Column(Text, primary_key=True)
    run_id = Column(Text, nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    event_type = Column(Text, nullable=False, index=True)
    participant_id = Column(Text, nullable=True)
    phase = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('run_id', 'seq', name='uq_agent_run_event_run_seq'),
        Index('ix_agent_run_event_run_seq', 'run_id', 'seq'),
        Index('ix_agent_run_event_type', 'event_type'),
    )


class AgentArtifact(Base):
    __tablename__ = 'agent_artifact'

    id = Column(Text, primary_key=True)
    run_id = Column(Text, nullable=False, index=True)
    user_id = Column(Text, nullable=False, index=True)
    kind = Column(Text, nullable=False)
    terminal_server_id = Column(Text, nullable=True)
    path = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    mime_type = Column(Text, nullable=True)
    size = Column(BigInteger, nullable=True)
    meta = Column('metadata', JSON, nullable=True)
    idempotency_key = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('run_id', 'path', 'kind', name='uq_agent_artifact_run_path_kind'),
        UniqueConstraint('run_id', 'idempotency_key', name='uq_agent_artifact_run_idempotency'),
        Index('ix_agent_artifact_run_path_kind', 'run_id', 'path', 'kind'),
    )


class AgentRunOperation(Base):
    __tablename__ = 'agent_run_operation'

    id = Column(Text, primary_key=True)
    run_id = Column(Text, nullable=False, index=True)
    operation_type = Column(Text, nullable=False, index=True)
    idempotency_key = Column(Text, nullable=False)
    request_hash = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    response = Column(JSON, nullable=True)
    error = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('run_id', 'operation_type', 'idempotency_key', name='uq_agent_run_operation_key'),
        Index('ix_agent_run_operation_run_type', 'run_id', 'operation_type'),
    )


class AgentRunModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    chat_id: str
    user_message_id: str
    assistant_message_id: str
    state: str
    state_version: int
    leader_model_id: str
    runtime_session_id: str | None = None
    budget: dict[str, Any] | None = None
    participants: list[dict[str, Any]] | None = None
    tool_access_snapshot: dict[str, Any] | None = None
    model_catalog_snapshot: dict[str, Any] | None = None
    process_refs: list[dict[str, Any]] | None = None
    summary: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    final_text: str = ''
    final_delta_state: dict[str, Any] | None = None
    created_at: int
    updated_at: int
    started_at: int | None = None
    ended_at: int | None = None


class AgentRunEventModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    seq: int
    event_type: str
    participant_id: str | None = None
    phase: str | None = None
    summary: str | None = None
    payload: dict[str, Any] | None = None
    created_at: int


class AgentArtifactModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    user_id: str
    kind: str
    terminal_server_id: str | None = None
    path: str
    url: str | None = None
    mime_type: str | None = None
    size: int | None = None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias='meta')
    idempotency_key: str | None = None
    created_at: int


class AgentRunOperationModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    operation_type: str
    idempotency_key: str
    request_hash: str
    status: str
    response: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class AgentRunOperationClaim:
    operation: AgentRunOperationModel
    created: bool


def _now_ns() -> int:
    return int(time.time_ns())


def _state(value: str | AgentRunState) -> AgentRunState:
    return value if isinstance(value, AgentRunState) else AgentRunState(value)


def _is_legal_transition(current: AgentRunState, target: AgentRunState) -> bool:
    if current == target and current in TERMINAL_STATES:
        return True
    return (current, target) in LEGAL_TRANSITIONS


def _ensure_transition_allowed(
    run_id: str,
    current: AgentRunState,
    target: AgentRunState,
    allowed_from: set[AgentRunState],
    reason: str,
) -> None:
    if current in allowed_from and _is_legal_transition(current, target):
        return
    raise AgentRunStateError(f'Cannot transition agent run {run_id} from {current} to {target}: {reason}')


def _apply_transition_fields(
    row: AgentRun,
    target: AgentRunState,
    now: int,
    payload: dict[str, Any] | None,
) -> None:
    row.state = target.value
    row.state_version = int(row.state_version or 0) + 1
    row.updated_at = now

    if target == AgentRunState.RUNNING and row.started_at is None:
        row.started_at = now
    if target in TERMINAL_STATES:
        row.ended_at = now

    if not payload:
        return

    for field in ('runtime_session_id', 'summary', 'error', 'process_refs'):
        if field in payload:
            setattr(row, field, payload[field])


async def _compact_terminal_summary_if_needed(
    row: AgentRun,
    db: AsyncSession,
    now: int,
) -> None:
    if row.summary is not None:
        return

    events_result = await db.execute(
        select(AgentRunEvent)
        .filter_by(run_id=row.id)
        .order_by(AgentRunEvent.seq.asc())
    )
    artifacts_result = await db.execute(
        select(AgentArtifact)
        .filter_by(run_id=row.id)
        .order_by(AgentArtifact.created_at.asc())
    )
    row.summary = build_compacted_run_summary(
        run=row,
        events=list(events_result.scalars().all()),
        artifacts=list(artifacts_result.scalars().all()),
        now_ns=now,
    )


class AgentRunTable:
    async def create_run(
        self,
        *,
        user_id: str,
        chat_id: str,
        user_message_id: str,
        assistant_message_id: str,
        leader_model_id: str,
        budget: dict[str, Any] | None = None,
        participants: list[dict[str, Any]] | None = None,
        tool_access_snapshot: dict[str, Any] | None = None,
        model_catalog_snapshot: dict[str, Any] | None = None,
        db: AsyncSession | None = None,
    ) -> AgentRunModel:
        async with get_async_db_context(db) as db:
            now = _now_ns()
            row = AgentRun(
                id=str(uuid4()),
                user_id=user_id,
                chat_id=chat_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                state=AgentRunState.QUEUED.value,
                state_version=0,
                leader_model_id=leader_model_id,
                budget=budget,
                participants=participants,
                tool_access_snapshot=tool_access_snapshot,
                model_catalog_snapshot=model_catalog_snapshot,
                process_refs=[],
                final_text='',
                final_delta_state={},
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return AgentRunModel.model_validate(row)

    async def get_run(self, run_id: str, db: AsyncSession | None = None) -> AgentRunModel | None:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentRun, run_id)
            return AgentRunModel.model_validate(row) if row else None

    async def get_run_state(self, run_id: str, db: AsyncSession | None = None) -> AgentRunState:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentRun, run_id)
            if row is None:
                raise AgentRunNotFound(run_id)
            return _state(row.state)

    async def list_runs_by_chat(
        self,
        chat_id: str,
        user_id: str,
        db: AsyncSession | None = None,
    ) -> list[AgentRunModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AgentRun)
                .filter_by(chat_id=chat_id, user_id=user_id)
                .order_by(AgentRun.created_at.desc())
            )
            return [AgentRunModel.model_validate(row) for row in result.scalars().all()]

    async def list_runs_by_user(
        self,
        user_id: str,
        db: AsyncSession | None = None,
    ) -> list[AgentRunModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AgentRun).filter_by(user_id=user_id).order_by(AgentRun.created_at.desc())
            )
            return [AgentRunModel.model_validate(row) for row in result.scalars().all()]

    async def transition_state(
        self,
        run_id: str,
        *,
        from_states: list[str | AgentRunState],
        to_state: str | AgentRunState,
        reason: str,
        payload: dict[str, Any] | None = None,
        db: AsyncSession | None = None,
    ) -> AgentRunModel:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentRun, run_id)
            if row is None:
                raise AgentRunNotFound(run_id)

            current = _state(row.state)
            target = _state(to_state)
            allowed_from = {_state(state) for state in from_states}

            if current == target and current in TERMINAL_STATES:
                return AgentRunModel.model_validate(row)

            _ensure_transition_allowed(run_id, current, target, allowed_from, reason)
            now = _now_ns()
            _apply_transition_fields(row, target, now, payload)
            if target in TERMINAL_STATES:
                await _compact_terminal_summary_if_needed(row, db, now)

            await db.commit()
            await db.refresh(row)
            return AgentRunModel.model_validate(row)

    async def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        participant_id: str | None = None,
        phase: str | None = None,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        db: AsyncSession | None = None,
    ) -> AgentRunEventModel:
        async with get_async_db_context(db) as db:
            if await db.get(AgentRun, run_id) is None:
                raise AgentRunNotFound(run_id)
            result = await db.execute(
                select(AgentRunEvent.seq).filter_by(run_id=run_id).order_by(AgentRunEvent.seq.desc()).limit(1)
            )
            next_seq = (result.scalar() or 0) + 1
            now = _now_ns()
            row = AgentRunEvent(
                id=str(uuid4()),
                run_id=run_id,
                seq=next_seq,
                event_type=event_type,
                participant_id=participant_id,
                phase=phase,
                summary=summary,
                payload=payload or {},
                created_at=now,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return AgentRunEventModel.model_validate(row)

    async def list_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        db: AsyncSession | None = None,
    ) -> list[AgentRunEventModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AgentRunEvent)
                .filter(AgentRunEvent.run_id == run_id, AgentRunEvent.seq > after_seq)
                .order_by(AgentRunEvent.seq.asc())
            )
            return [AgentRunEventModel.model_validate(row) for row in result.scalars().all()]

    async def list_events_after(
        self,
        run_id: str,
        after_seq: int = 0,
        db: AsyncSession | None = None,
    ) -> list[AgentRunEventModel]:
        return await self.list_events(run_id, after_seq=after_seq, db=db)

    async def has_final_started(self, run_id: str, db: AsyncSession | None = None) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AgentRunEvent.id)
                .filter_by(run_id=run_id, event_type='final.started')
                .limit(1)
            )
            return result.scalar() is not None

    async def append_final_text_delta(
        self,
        run_id: str,
        final_stream_id: str,
        delta_index: int,
        delta: str,
        db: AsyncSession | None = None,
    ) -> str:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentRun, run_id)
            if row is None:
                raise AgentRunNotFound(run_id)

            state = dict(row.final_delta_state or {})
            stream_state = dict(state.get(final_stream_id) or {})
            seen = {int(index) for index in stream_state.get('seen', [])}
            if delta_index in seen:
                return row.final_text or ''

            expected = len(seen)
            if delta_index != expected:
                raise ValueError(f'expected delta_index {expected}, got {delta_index}')

            seen.add(delta_index)
            row.final_text = (row.final_text or '') + delta
            state[final_stream_id] = {'seen': sorted(seen)}
            row.final_delta_state = state
            row.updated_at = _now_ns()
            await db.commit()
            await db.refresh(row)
            return row.final_text

    async def claim_operation(
        self,
        run_id: str,
        *,
        operation_type: str,
        idempotency_key: str,
        request_hash: str,
        db: AsyncSession | None = None,
    ) -> AgentRunOperationClaim:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AgentRunOperation).filter_by(
                    run_id=run_id,
                    operation_type=operation_type,
                    idempotency_key=idempotency_key,
                )
            )
            row = result.scalars().first()
            if row:
                if row.request_hash != request_hash:
                    raise AgentRunOperationConflict('idempotency key was reused with a different request hash')
                return AgentRunOperationClaim(
                    operation=AgentRunOperationModel.model_validate(row),
                    created=False,
                )

            if await db.get(AgentRun, run_id) is None:
                raise AgentRunNotFound(run_id)

            now = _now_ns()
            row = AgentRunOperation(
                id=str(uuid4()),
                run_id=run_id,
                operation_type=operation_type,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status='in_progress',
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return AgentRunOperationClaim(
                operation=AgentRunOperationModel.model_validate(row),
                created=True,
            )

    async def finish_operation_success(
        self,
        operation_id: str,
        response: dict[str, Any],
        db: AsyncSession | None = None,
    ) -> AgentRunOperationModel:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentRunOperation, operation_id)
            if row is None:
                raise AgentRunNotFound(operation_id)
            row.status = 'succeeded'
            row.response = response
            row.updated_at = _now_ns()
            await db.commit()
            await db.refresh(row)
            return AgentRunOperationModel.model_validate(row)

    async def finish_operation_error(
        self,
        operation_id: str,
        error: dict[str, Any],
        db: AsyncSession | None = None,
    ) -> AgentRunOperationModel:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentRunOperation, operation_id)
            if row is None:
                raise AgentRunNotFound(operation_id)
            row.status = 'failed'
            row.error = error
            row.updated_at = _now_ns()
            await db.commit()
            await db.refresh(row)
            return AgentRunOperationModel.model_validate(row)

    async def register_artifact(
        self,
        *,
        run_id: str,
        user_id: str,
        kind: str,
        path: str,
        idempotency_key: str | None = None,
        terminal_server_id: str | None = None,
        url: str | None = None,
        mime_type: str | None = None,
        size: int | None = None,
        metadata: dict[str, Any] | None = None,
        db: AsyncSession | None = None,
    ) -> AgentArtifactModel:
        async with get_async_db_context(db) as db:
            existing = None
            if idempotency_key:
                result = await db.execute(
                    select(AgentArtifact).filter_by(
                        run_id=run_id,
                        idempotency_key=idempotency_key,
                    )
                )
                existing = result.scalars().first()

            if existing is None:
                result = await db.execute(select(AgentArtifact).filter_by(run_id=run_id, path=path, kind=kind))
                existing = result.scalars().first()

            if existing is not None:
                return AgentArtifactModel.model_validate(existing)

            if await db.get(AgentRun, run_id) is None:
                raise AgentRunNotFound(run_id)

            row = AgentArtifact(
                id=str(uuid4()),
                run_id=run_id,
                user_id=user_id,
                kind=kind,
                terminal_server_id=terminal_server_id,
                path=path,
                url=url,
                mime_type=mime_type,
                size=size,
                meta=metadata or {},
                idempotency_key=idempotency_key,
                created_at=_now_ns(),
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return AgentArtifactModel.model_validate(row)


AgentRuns = AgentRunTable()
