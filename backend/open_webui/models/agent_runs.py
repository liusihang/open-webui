from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from open_webui.agent.canonical import canonical_sha256
from open_webui.agent.compaction import build_compacted_run_summary
from open_webui.agent.decision_status import DecisionExecutionStatus
from open_webui.env import DATABASE_ENABLE_SESSION_SHARING
from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    and_,
    case,
    or_,
    select,
    update,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class AgentRunState(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    WAITING_APPROVAL = 'waiting_approval'
    WAITING_USER_INPUT = 'waiting_user_input'
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
    (AgentRunState.RUNNING, AgentRunState.WAITING_USER_INPUT),
    (AgentRunState.RUNNING, AgentRunState.FINALIZING),
    (AgentRunState.RUNNING, AgentRunState.FAILED),
    (AgentRunState.RUNNING, AgentRunState.CANCELLED),
    (AgentRunState.RUNNING, AgentRunState.BUDGET_EXCEEDED),
    (AgentRunState.WAITING_APPROVAL, AgentRunState.RUNNING),
    (AgentRunState.WAITING_APPROVAL, AgentRunState.FAILED),
    (AgentRunState.WAITING_APPROVAL, AgentRunState.CANCELLED),
    (AgentRunState.WAITING_APPROVAL, AgentRunState.BUDGET_EXCEEDED),
    (AgentRunState.WAITING_USER_INPUT, AgentRunState.RUNNING),
    (AgentRunState.WAITING_USER_INPUT, AgentRunState.FAILED),
    (AgentRunState.WAITING_USER_INPUT, AgentRunState.CANCELLED),
    (AgentRunState.WAITING_USER_INPUT, AgentRunState.BUDGET_EXCEEDED),
    (AgentRunState.FINALIZING, AgentRunState.COMPLETED),
    (AgentRunState.FINALIZING, AgentRunState.FAILED),
    (AgentRunState.FINALIZING, AgentRunState.CANCELLED),
    (AgentRunState.FINALIZING, AgentRunState.BUDGET_EXCEEDED),
}

_ACTIVE_RUN_STATES = frozenset(
    {
        AgentRunState.QUEUED,
        AgentRunState.RUNNING,
        AgentRunState.WAITING_APPROVAL,
        AgentRunState.WAITING_USER_INPUT,
        AgentRunState.FINALIZING,
    }
)
_STARTED_ACTIVE_RUN_STATES = _ACTIVE_RUN_STATES - {AgentRunState.QUEUED}

_LIFECYCLE_EVENT_TRANSITIONS: dict[
    str,
    tuple[AgentRunState, frozenset[AgentRunState]],
] = {
    'run.running': (
        AgentRunState.RUNNING,
        frozenset({AgentRunState.QUEUED}),
    ),
    'approval.requested': (
        AgentRunState.WAITING_APPROVAL,
        frozenset({AgentRunState.RUNNING}),
    ),
    'user_input.requested': (
        AgentRunState.WAITING_USER_INPUT,
        frozenset({AgentRunState.RUNNING}),
    ),
    'final.started': (
        AgentRunState.FINALIZING,
        frozenset({AgentRunState.RUNNING}),
    ),
    'run.completed': (
        AgentRunState.COMPLETED,
        frozenset({AgentRunState.FINALIZING}),
    ),
    'run.failed': (
        AgentRunState.FAILED,
        _ACTIVE_RUN_STATES,
    ),
    'run.cancelled': (
        AgentRunState.CANCELLED,
        _ACTIVE_RUN_STATES,
    ),
    'run.budget_exceeded': (
        AgentRunState.BUDGET_EXCEEDED,
        _STARTED_ACTIVE_RUN_STATES,
    ),
    'approval.completed': (
        AgentRunState.RUNNING,
        frozenset({AgentRunState.WAITING_APPROVAL}),
    ),
    'user_input.completed': (
        AgentRunState.RUNNING,
        frozenset({AgentRunState.WAITING_USER_INPUT}),
    ),
    'user_input.declined': (
        AgentRunState.RUNNING,
        frozenset({AgentRunState.WAITING_USER_INPUT}),
    ),
    'user_input.cancelled': (
        AgentRunState.RUNNING,
        frozenset({AgentRunState.WAITING_USER_INPUT}),
    ),
    'user_input.expired': (
        AgentRunState.RUNNING,
        frozenset({AgentRunState.WAITING_USER_INPUT}),
    ),
}

_RUN_TERMINAL_EVENT_TYPES = frozenset(
    {
        'run.completed',
        'run.failed',
        'run.cancelled',
        'run.budget_exceeded',
    }
)
_USER_INPUT_RESULT_EVENT_TYPES = frozenset(
    {
        'user_input.completed',
        'user_input.declined',
        'user_input.cancelled',
        'user_input.expired',
    }
)
_DECISION_RESULT_EVENT_TYPES = frozenset(
    {'approval.completed', *_USER_INPUT_RESULT_EVENT_TYPES}
)
_LIFECYCLE_EVENT_GROUPS: dict[str, frozenset[str]] = {
    'run.running': frozenset({'run.running'}),
    'final.started': frozenset({'final.started'}),
    'run.completed': _RUN_TERMINAL_EVENT_TYPES,
    'run.failed': _RUN_TERMINAL_EVENT_TYPES,
    'run.cancelled': _RUN_TERMINAL_EVENT_TYPES,
    'run.budget_exceeded': _RUN_TERMINAL_EVENT_TYPES,
    'approval.requested': frozenset({'approval.requested'}),
    'approval.completed': frozenset({'approval.completed'}),
    'user_input.requested': frozenset({'user_input.requested'}),
    'user_input.completed': _USER_INPUT_RESULT_EVENT_TYPES,
    'user_input.declined': _USER_INPUT_RESULT_EVENT_TYPES,
    'user_input.cancelled': _USER_INPUT_RESULT_EVENT_TYPES,
    'user_input.expired': _USER_INPUT_RESULT_EVENT_TYPES,
}
_LIFECYCLE_EVENT_IDENTITY_FIELDS = {
    'approval.requested': 'approval_id',
    'approval.completed': 'approval_id',
    'user_input.requested': 'user_input_id',
    'user_input.completed': 'user_input_id',
    'user_input.declined': 'user_input_id',
    'user_input.cancelled': 'user_input_id',
    'user_input.expired': 'user_input_id',
}

EVENT_APPEND_MAX_ATTEMPTS = 10
DECISION_RETRY_BASE_SECONDS = 1.0
DECISION_RETRY_MAX_SECONDS = 60.0
DECISION_RETRY_JITTER_RATIO = 0.2


class AgentRunError(ValueError):
    code = 'agent_run_error'


class AgentRunStateError(AgentRunError):
    code = 'invalid_state_transition'


class AgentRunOperationConflict(AgentRunError):
    code = 'idempotency_conflict'


class AgentRunDecisionConflict(AgentRunError):
    code = 'decision_conflict'


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


class AgentRunDecisionExecution(Base):
    __tablename__ = 'agent_run_decision_execution'

    id = Column(Text, primary_key=True)
    run_id = Column(Text, nullable=False, index=True)
    resource_type = Column(Text, nullable=False)
    resource_id = Column(Text, nullable=False)
    decision = Column(Text, nullable=False)
    command_type = Column(Text, nullable=False)
    command_payload = Column(JSON, nullable=False)
    fingerprint = Column(Text, nullable=False)
    runtime_session_id = Column(Text, nullable=False)
    expected_checkpoint_version = Column(Integer, nullable=False)
    expected_run_state_version = Column(Integer, nullable=False)
    request_event_seq = Column(Integer, nullable=False)
    tool_arguments_fingerprint = Column(Text, nullable=True)
    tool_call_idempotency_key = Column(Text, nullable=True)
    status = Column(Text, nullable=False, index=True)
    claim_owner = Column(Text, nullable=True)
    claim_token = Column(Text, nullable=True)
    claimed_at = Column(BigInteger, nullable=True)
    claim_expires_at = Column(BigInteger, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(BigInteger, nullable=True)
    prepare_response = Column(JSON, nullable=True)
    prepared_at = Column(BigInteger, nullable=True)
    backend_committed_at = Column(BigInteger, nullable=True)
    completion_event_id = Column(Text, nullable=True)
    completion_event_seq = Column(Integer, nullable=True)
    activate_response = Column(JSON, nullable=True)
    activated_at = Column(BigInteger, nullable=True)
    runtime_outcome = Column(JSON, nullable=True)
    outcome_at = Column(BigInteger, nullable=True)
    last_error = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            'run_id',
            'resource_type',
            'resource_id',
            name='uq_agent_run_decision_resource',
        ),
        Index('ix_agent_run_decision_status_retry', 'status', 'next_attempt_at'),
        Index('ix_agent_run_decision_run_status', 'run_id', 'status'),
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


class AgentRunDecisionExecutionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    resource_type: str
    resource_id: str
    decision: str
    command_type: str
    command_payload: dict[str, Any]
    fingerprint: str
    runtime_session_id: str
    expected_checkpoint_version: int
    expected_run_state_version: int
    request_event_seq: int
    tool_arguments_fingerprint: str | None = None
    tool_call_idempotency_key: str | None = None
    status: str
    claim_owner: str | None = None
    claim_token: str | None = None
    claimed_at: int | None = None
    claim_expires_at: int | None = None
    attempt_count: int = 0
    next_attempt_at: int | None = None
    prepare_response: dict[str, Any] | None = None
    prepared_at: int | None = None
    backend_committed_at: int | None = None
    completion_event_id: str | None = None
    completion_event_seq: int | None = None
    activate_response: dict[str, Any] | None = None
    activated_at: int | None = None
    runtime_outcome: dict[str, Any] | None = None
    outcome_at: int | None = None
    last_error: dict[str, Any] | None = None
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class AgentRunOperationClaim:
    operation: AgentRunOperationModel
    created: bool


@dataclass(frozen=True)
class AgentRunEventAppendResult:
    event: AgentRunEventModel
    created: bool


@dataclass(frozen=True)
class AgentRunDecisionRecordResult:
    execution: AgentRunDecisionExecutionModel | None
    created: bool
    historical_event: AgentRunEventModel | None = None


@dataclass(frozen=True)
class AgentRunDecisionExecutionClaim:
    execution: AgentRunDecisionExecutionModel


def _now_ns() -> int:
    return int(time.time_ns())


async def _database_now_ns(db: AsyncSession) -> int:
    dialect = db.get_bind().dialect.name
    if dialect == 'sqlite':
        statement = sql_text(
            "SELECT CAST((julianday('now') - 2440587.5) * 86400000000000 AS INTEGER)"
        )
    elif dialect == 'postgresql':
        statement = sql_text(
            'SELECT CAST(EXTRACT(EPOCH FROM clock_timestamp()) * 1000000000 AS BIGINT)'
        )
    elif dialect in {'mysql', 'mariadb'}:
        statement = sql_text(
            'SELECT CAST(UNIX_TIMESTAMP(CURRENT_TIMESTAMP(6)) * 1000000000 AS SIGNED)'
        )
    else:
        raise AgentRunError(
            f'Database-backed agent decision leases do not support dialect: {dialect}'
        )
    return int((await db.execute(statement)).scalar_one())


def _decision_retry_delay_ns(
    attempt_count: int,
    jitter_fraction: float,
) -> int:
    exponent = max(int(attempt_count) - 1, 0)
    base_delay = min(
        DECISION_RETRY_BASE_SECONDS * (2**exponent),
        DECISION_RETRY_MAX_SECONDS,
    )
    jitter = min(max(float(jitter_fraction), 0.0), 1.0)
    multiplier = 1.0 + ((jitter - 0.5) * 2 * DECISION_RETRY_JITTER_RATIO)
    return int(base_delay * multiplier * 1_000_000_000)


def _state(value: str | AgentRunState) -> AgentRunState:
    return value if isinstance(value, AgentRunState) else AgentRunState(value)


def _require_decision_claim(
    execution: AgentRunDecisionExecution,
    claim_token: str,
) -> None:
    if not claim_token or execution.claim_token != claim_token:
        raise AgentRunDecisionConflict(
            f'Execution {execution.id} claim token is stale or invalid'
        )


def _resolve_prepared_commit_gate_conflict(
    execution: AgentRunDecisionExecution,
    claim_token: str,
) -> AgentRunDecisionExecutionModel:
    _require_decision_claim(execution, claim_token)
    if execution.status in {
        'backend_committed',
        'activating',
        'activated',
        'succeeded',
        'failed',
        'cancelled',
    }:
        return AgentRunDecisionExecutionModel.model_validate(execution)
    raise AgentRunDecisionConflict(
        f'Execution {execution.id} claim is expired or state is {execution.status}'
    )


def _uses_external_session(db: AsyncSession | None) -> bool:
    return isinstance(db, AsyncSession) and DATABASE_ENABLE_SESSION_SHARING


async def _finish_session_write(
    db: AsyncSession,
    *,
    external_session: bool,
) -> None:
    if external_session:
        await db.flush()
    else:
        await db.commit()


async def _rollback_owned_session(
    db: AsyncSession,
    *,
    external_session: bool,
) -> None:
    if not external_session:
        await db.rollback()


async def _load_decision_execution_fresh(
    db: AsyncSession,
    execution_id: str,
) -> AgentRunDecisionExecution | None:
    result = await db.execute(
        select(AgentRunDecisionExecution)
        .where(AgentRunDecisionExecution.id == execution_id)
        .execution_options(populate_existing=True)
    )
    return result.scalars().first()


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


def _apply_lifecycle_event_transition(
    row: AgentRun,
    *,
    event_type: str,
    now: int,
    payload: dict[str, Any] | None,
) -> AgentRunState | None:
    transition = _LIFECYCLE_EVENT_TRANSITIONS.get(event_type)
    if transition is None:
        return None

    target, allowed_from = transition
    current = _state(row.state)
    if current == target:
        changed = False
        if payload:
            for field in ('runtime_session_id', 'summary', 'error', 'process_refs'):
                if field in payload and getattr(row, field) != payload[field]:
                    setattr(row, field, payload[field])
                    changed = True
        if changed:
            row.updated_at = now
        return target

    _ensure_transition_allowed(
        row.id,
        current,
        target,
        set(allowed_from),
        f'{event_type} event persistence',
    )
    _apply_transition_fields(row, target, now, payload)
    return target


def is_lifecycle_event_type(event_type: str) -> bool:
    return event_type in _LIFECYCLE_EVENT_TRANSITIONS


def _lifecycle_identity_value(
    event_type: str,
    payload: dict[str, Any] | None,
) -> tuple[str | None, Any]:
    field = _LIFECYCLE_EVENT_IDENTITY_FIELDS.get(event_type)
    if field is None:
        return None, None
    value = (payload or {}).get(field)
    if value is None:
        raise AgentRunError(f'{event_type} requires payload.{field}')
    return field, value


async def _find_canonical_lifecycle_event(
    db: AsyncSession,
    *,
    run_id: str,
    event_type: str,
    payload: dict[str, Any] | None,
) -> AgentRunEvent | None:
    event_types = _LIFECYCLE_EVENT_GROUPS.get(event_type)
    if event_types is None:
        return None
    identity_field, identity_value = _lifecycle_identity_value(event_type, payload)
    result = await db.execute(
        select(AgentRunEvent)
        .filter(
            AgentRunEvent.run_id == run_id,
            AgentRunEvent.event_type.in_(event_types),
        )
        .order_by(AgentRunEvent.seq.asc())
    )
    for event in result.scalars().all():
        if identity_field is None:
            return event
        if (event.payload or {}).get(identity_field) == identity_value:
            return event
    return None


async def _finish_operation_success_in_session(
    db: AsyncSession,
    *,
    operation_id: str,
    response: dict[str, Any],
    now: int,
) -> None:
    operation = await db.get(AgentRunOperation, operation_id)
    if operation is None:
        raise AgentRunNotFound(operation_id)
    if operation.status == 'failed':
        raise AgentRunOperationConflict('cannot complete a failed agent operation')
    if operation.status == 'succeeded':
        return
    operation.status = 'succeeded'
    operation.response = response
    operation.error = None
    operation.updated_at = now


async def _compact_terminal_summary_if_needed(
    row: AgentRun,
    db: AsyncSession,
    now: int,
    *,
    replace: bool = False,
) -> None:
    if row.summary is not None and not replace:
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


async def _prepare_canonical_lifecycle_event(
    db: AsyncSession,
    *,
    run: AgentRun,
    canonical: AgentRunEvent,
    event_type: str,
    operation_id: str | None,
) -> AgentRunEventAppendResult:
    if canonical.event_type != event_type:
        raise AgentRunStateError(
            f'Lifecycle event {event_type} conflicts with '
            f'persisted {canonical.event_type} for agent run {run.id}'
        )
    event = AgentRunEventModel.model_validate(canonical)
    now = _now_ns()
    transition = _LIFECYCLE_EVENT_TRANSITIONS[canonical.event_type]
    transition_target = transition[0]
    last_seq = ((run.summary or {}).get('audit') or {}).get('last_seq')
    if (
        transition_target in TERMINAL_STATES
        and _state(run.state) == transition_target
        and last_seq != canonical.seq
    ):
        await _compact_terminal_summary_if_needed(
            run,
            db,
            now,
            replace=True,
        )
    if operation_id is not None:
        await _finish_operation_success_in_session(
            db,
            operation_id=operation_id,
            response=event.model_dump(mode='json', exclude={'id'}),
            now=now,
        )
    return AgentRunEventAppendResult(event=event, created=False)


async def _prepare_new_event(
    db: AsyncSession,
    *,
    run: AgentRun,
    event_type: str,
    participant_id: str | None,
    phase: str | None,
    summary: str | None,
    payload: dict[str, Any] | None,
    operation_id: str | None,
) -> AgentRunEventAppendResult:
    result = await db.execute(
        select(AgentRunEvent.seq)
        .filter_by(run_id=run.id)
        .order_by(AgentRunEvent.seq.desc())
        .limit(1)
    )
    now = _now_ns()
    event = AgentRunEventModel(
        id=str(uuid4()),
        run_id=run.id,
        seq=(result.scalar() or 0) + 1,
        event_type=event_type,
        participant_id=participant_id,
        phase=phase,
        summary=summary,
        payload=payload or {},
        created_at=now,
    )
    db.add(AgentRunEvent(**event.model_dump()))
    transition_target = _apply_lifecycle_event_transition(
        run,
        event_type=event_type,
        now=now,
        payload=payload,
    )
    if transition_target in TERMINAL_STATES:
        await _compact_terminal_summary_if_needed(
            run,
            db,
            now,
            replace=True,
        )
    if operation_id is not None:
        await _finish_operation_success_in_session(
            db,
            operation_id=operation_id,
            response=event.model_dump(mode='json', exclude={'id'}),
            now=now,
        )
    return AgentRunEventAppendResult(event=event, created=True)


def _decision_command_type(resource_type: str) -> str:
    if resource_type == 'approval':
        return 'resume_approval'
    if resource_type == 'user_input':
        return 'resume_user_input'
    raise AgentRunDecisionConflict(f'Unsupported decision resource type: {resource_type}')


def _decision_request_event_type(resource_type: str) -> str:
    return {
        'approval': 'approval.requested',
        'user_input': 'user_input.requested',
    }.get(resource_type) or _raise_decision_resource_type(resource_type)


def _raise_decision_resource_type(resource_type: str):
    raise AgentRunDecisionConflict(f'Unsupported decision resource type: {resource_type}')


def _decision_waiting_state(resource_type: str) -> AgentRunState:
    return {
        'approval': AgentRunState.WAITING_APPROVAL,
        'user_input': AgentRunState.WAITING_USER_INPUT,
    }.get(resource_type) or _raise_decision_resource_type(resource_type)


def _decision_resource_field(resource_type: str) -> str:
    return {
        'approval': 'approval_id',
        'user_input': 'user_input_id',
    }.get(resource_type) or _raise_decision_resource_type(resource_type)


def _decision_result_event_types(resource_type: str) -> frozenset[str]:
    if resource_type == 'approval':
        return frozenset({'approval.completed'})
    if resource_type == 'user_input':
        return _USER_INPUT_RESULT_EVENT_TYPES
    return _raise_decision_resource_type(resource_type)


def _decision_result_event_type(resource_type: str, decision: str) -> str:
    if resource_type == 'approval':
        return 'approval.completed'
    return {
        'accepted': 'user_input.completed',
        'declined': 'user_input.declined',
        'cancelled': 'user_input.cancelled',
        'timeout': 'user_input.expired',
    }.get(decision) or _raise_decision_value(resource_type, decision)


def _decision_command_payload(
    resource_type: str,
    decision: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if resource_type == 'approval':
        return {'decision': decision}
    if resource_type == 'user_input':
        return {'status': decision, **payload}
    return _raise_decision_resource_type(resource_type)


def _raise_decision_value(resource_type: str, decision: str):
    raise AgentRunDecisionConflict(
        f'Unsupported {resource_type} decision: {decision}'
    )


def _validate_decision(resource_type: str, decision: str) -> None:
    allowed = {
        'approval': {'approved', 'rejected'},
        'user_input': {'accepted', 'declined', 'cancelled', 'timeout'},
    }.get(resource_type)
    if allowed is None or decision not in allowed:
        _raise_decision_value(resource_type, decision)


def _decision_payload_fingerprint(payload: dict[str, Any]) -> str:
    return canonical_sha256(payload)


async def _find_resource_event(
    db: AsyncSession,
    *,
    run_id: str,
    event_types: frozenset[str],
    resource_field: str,
    resource_id: str,
) -> AgentRunEvent | None:
    result = await db.execute(
        select(AgentRunEvent)
        .filter(
            AgentRunEvent.run_id == run_id,
            AgentRunEvent.event_type.in_(event_types),
        )
        .order_by(AgentRunEvent.seq.asc())
    )
    for event in result.scalars().all():
        if (event.payload or {}).get(resource_field) == resource_id:
            return event
    return None


async def _upsert_decision_receipt(
    db: AsyncSession,
    *,
    run_id: str,
    operation_type: str,
    idempotency_key: str,
    request_hash: str,
    response: dict[str, Any],
    now: int,
) -> AgentRunOperation:
    values = {
        'id': str(uuid4()),
        'run_id': run_id,
        'operation_type': operation_type,
        'idempotency_key': idempotency_key,
        'request_hash': request_hash,
        'status': 'succeeded',
        'response': response,
        'error': None,
        'created_at': now,
        'updated_at': now,
    }
    dialect_name = db.get_bind().dialect.name
    if dialect_name == 'sqlite':
        insert_statement = sqlite_insert(AgentRunOperation)
    elif dialect_name == 'postgresql':
        insert_statement = postgresql_insert(AgentRunOperation)
    else:
        raise AgentRunError(
            f'Atomic decision receipts are unsupported for database dialect {dialect_name}'
        )
    await db.execute(
        insert_statement.values(**values).on_conflict_do_nothing(
            index_elements=[
                AgentRunOperation.run_id,
                AgentRunOperation.operation_type,
                AgentRunOperation.idempotency_key,
            ]
        )
    )
    receipt_result = await db.execute(
        select(AgentRunOperation).filter_by(
            run_id=run_id,
            operation_type=operation_type,
            idempotency_key=idempotency_key,
        )
    )
    receipt = receipt_result.scalars().one()
    if receipt.request_hash != request_hash:
        raise AgentRunOperationConflict(
            'idempotency key was reused with a different request hash'
        )
    receipt.status = 'succeeded'
    receipt.response = response
    receipt.error = None
    receipt.updated_at = now
    return receipt


def _historical_decision(event: AgentRunEvent, resource_type: str) -> str | None:
    payload = event.payload or {}
    if resource_type == 'approval':
        value = payload.get('decision')
        return value if value in {'approved', 'rejected'} else None
    value = payload.get('status')
    if value in {'accepted', 'declined', 'cancelled', 'timeout'}:
        return value
    return {
        'user_input.completed': 'accepted',
        'user_input.declined': 'declined',
        'user_input.cancelled': 'cancelled',
        'user_input.expired': 'timeout',
    }.get(event.event_type)


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

    async def attach_runtime_session(
        self,
        run_id: str,
        runtime_session_id: str,
        db: AsyncSession | None = None,
    ) -> AgentRunModel:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentRun, run_id)
            if row is None:
                raise AgentRunNotFound(run_id)

            row.runtime_session_id = runtime_session_id
            row.updated_at = _now_ns()
            await db.commit()
            await db.refresh(row)
            return AgentRunModel.model_validate(row)

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
        result = await self.append_event_with_result(
            run_id,
            event_type=event_type,
            participant_id=participant_id,
            phase=phase,
            summary=summary,
            payload=payload,
            db=db,
        )
        return result.event

    async def append_event_with_result(
        self,
        run_id: str,
        *,
        event_type: str,
        participant_id: str | None = None,
        phase: str | None = None,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        operation_id: str | None = None,
        db: AsyncSession | None = None,
    ) -> AgentRunEventAppendResult:
        if event_type in _DECISION_RESULT_EVENT_TYPES:
            raise AgentRunDecisionConflict(
                f'{event_type} requires a prepared decision execution'
            )
        last_integrity_error: IntegrityError | None = None
        for _attempt in range(EVENT_APPEND_MAX_ATTEMPTS):
            async with get_async_db_context(db) as db:
                run = await db.get(AgentRun, run_id)
                if run is None:
                    raise AgentRunNotFound(run_id)
                canonical = await _find_canonical_lifecycle_event(
                    db,
                    run_id=run_id,
                    event_type=event_type,
                    payload=payload,
                )
                try:
                    if canonical is not None:
                        append_result = await _prepare_canonical_lifecycle_event(
                            db,
                            run=run,
                            canonical=canonical,
                            event_type=event_type,
                            operation_id=operation_id,
                        )
                    else:
                        append_result = await _prepare_new_event(
                            db,
                            run=run,
                            event_type=event_type,
                            participant_id=participant_id,
                            phase=phase,
                            summary=summary,
                            payload=payload,
                            operation_id=operation_id,
                        )
                    await db.commit()
                except IntegrityError as exc:
                    await db.rollback()
                    last_integrity_error = exc
                    continue
                except Exception:
                    await db.rollback()
                    raise
                return append_result

        if last_integrity_error is not None:
            raise last_integrity_error
        raise AgentRunError(f'Failed to append event for agent run {run_id}')

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

    async def record_decision_execution(  # noqa: C901
        self,
        run_id: str,
        *,
        resource_type: str,
        resource_id: str,
        decision: str,
        payload: dict[str, Any],
        operation_type: str,
        idempotency_key: str,
        request_hash: str,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionRecordResult:
        _validate_decision(resource_type, decision)
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            run = await db.get(AgentRun, run_id)
            if run is None:
                raise AgentRunNotFound(run_id)

            receipt_result = await db.execute(
                select(AgentRunOperation).filter_by(
                    run_id=run_id,
                    operation_type=operation_type,
                    idempotency_key=idempotency_key,
                )
            )
            receipt = receipt_result.scalars().first()
            if receipt is not None and receipt.request_hash != request_hash:
                raise AgentRunOperationConflict(
                    'idempotency key was reused with a different request hash'
                )

            resource_field = _decision_resource_field(resource_type)
            historical = await _find_resource_event(
                db,
                run_id=run_id,
                event_types=_decision_result_event_types(resource_type),
                resource_field=resource_field,
                resource_id=resource_id,
            )
            if historical is not None:
                if _historical_decision(historical, resource_type) != decision:
                    raise AgentRunDecisionConflict(
                        f'{resource_type} already has a different decision'
                    )
                response = {
                    'execution_id': None,
                    'resource_type': resource_type,
                    'resource_id': resource_id,
                    'decision': decision,
                    'execution_status': 'historical_completed',
                    'completion_event_seq': historical.seq,
                }
                now = _now_ns()
                await _upsert_decision_receipt(
                    db,
                    run_id=run_id,
                    operation_type=operation_type,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response=response,
                    now=now,
                )
                await _finish_session_write(
                    db,
                    external_session=external_session,
                )
                return AgentRunDecisionRecordResult(
                    execution=None,
                    created=False,
                    historical_event=AgentRunEventModel.model_validate(historical),
                )

            requested = await _find_resource_event(
                db,
                run_id=run_id,
                event_types=frozenset({_decision_request_event_type(resource_type)}),
                resource_field=resource_field,
                resource_id=resource_id,
            )
            if requested is None:
                raise AgentRunDecisionConflict(
                    f'Unknown {resource_type} resource: {resource_id}'
                )
            if (
                resource_type == 'user_input'
                and decision == 'cancelled'
                and not bool((requested.payload or {}).get('allow_cancel', True))
            ):
                raise AgentRunDecisionConflict(
                    f'User input {resource_id} does not allow cancellation'
                )

            waiting_state = _decision_waiting_state(resource_type)
            if _state(run.state) != waiting_state:
                raise AgentRunStateError(
                    f'Agent run {run_id} is {run.state}, not {waiting_state.value}'
                )
            if not run.runtime_session_id:
                raise AgentRunDecisionConflict('runtime_session_id is required')

            requested_payload = requested.payload or {}
            checkpoint_version = requested_payload.get('checkpoint_version')
            if type(checkpoint_version) is not int:
                raise AgentRunDecisionConflict(
                    f'{resource_type} request checkpoint_version must be an integer'
                )
            command_payload = _decision_command_payload(
                resource_type,
                decision,
                payload,
            )
            expected_checkpoint_version = checkpoint_version
            tool_arguments_fingerprint = requested_payload.get(
                'tool_arguments_fingerprint'
            )
            tool_call_idempotency_key = requested_payload.get(
                'tool_call_idempotency_key'
            )
            if resource_type == 'approval' and (
                not tool_arguments_fingerprint or not tool_call_idempotency_key
            ):
                raise AgentRunDecisionConflict(
                    'Approval request is missing replay authorization fingerprints'
                )
            execution_id = str(uuid4())
            wire_without_fingerprint = {
                'schema_version': 1,
                'runtime_session_id': run.runtime_session_id,
                'execution_id': execution_id,
                'expected_checkpoint_version': expected_checkpoint_version,
                'subject_id': resource_id,
                'command_type': _decision_command_type(resource_type),
                'payload': command_payload,
            }
            fingerprint = _decision_payload_fingerprint(wire_without_fingerprint)
            now = _now_ns()
            values = {
                'id': execution_id,
                'run_id': run_id,
                'resource_type': resource_type,
                'resource_id': resource_id,
                'decision': decision,
                'command_type': wire_without_fingerprint['command_type'],
                'command_payload': command_payload,
                'fingerprint': fingerprint,
                'runtime_session_id': run.runtime_session_id,
                'expected_checkpoint_version': expected_checkpoint_version,
                'expected_run_state_version': int(run.state_version),
                'request_event_seq': requested.seq,
                'tool_arguments_fingerprint': tool_arguments_fingerprint,
                'tool_call_idempotency_key': tool_call_idempotency_key,
                'status': 'pending',
                'attempt_count': 0,
                'next_attempt_at': now,
                'created_at': now,
                'updated_at': now,
            }
            dialect_name = db.get_bind().dialect.name
            if dialect_name == 'sqlite':
                insert_statement = sqlite_insert(AgentRunDecisionExecution)
            elif dialect_name == 'postgresql':
                insert_statement = postgresql_insert(AgentRunDecisionExecution)
            else:
                raise AgentRunError(
                    f'Atomic decision execution claims are unsupported for database dialect {dialect_name}'
                )
            inserted = await db.execute(
                insert_statement.values(**values)
                .on_conflict_do_nothing(
                    index_elements=[
                        AgentRunDecisionExecution.run_id,
                        AgentRunDecisionExecution.resource_type,
                        AgentRunDecisionExecution.resource_id,
                    ]
                )
                .returning(AgentRunDecisionExecution.id)
            )
            inserted_id = inserted.scalar_one_or_none()
            canonical_result = await db.execute(
                select(AgentRunDecisionExecution).filter_by(
                    run_id=run_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
            )
            execution = canonical_result.scalars().one()
            canonical_payload = {
                'schema_version': 1,
                'runtime_session_id': execution.runtime_session_id,
                'execution_id': execution.id,
                'expected_checkpoint_version': execution.expected_checkpoint_version,
                'subject_id': execution.resource_id,
                'command_type': execution.command_type,
                'payload': execution.command_payload,
            }
            canonical_fingerprint = _decision_payload_fingerprint(canonical_payload)
            if (
                execution.decision != decision
                or execution.command_payload != command_payload
                or execution.fingerprint != canonical_fingerprint
            ):
                await _rollback_owned_session(
                    db,
                    external_session=external_session,
                )
                raise AgentRunDecisionConflict(
                    f'{resource_type} already has a different decision'
                )

            response = {
                'execution_id': execution.id,
                'resource_type': resource_type,
                'resource_id': resource_id,
                'decision': decision,
                'execution_status': execution.status,
            }
            await _upsert_decision_receipt(
                db,
                run_id=run_id,
                operation_type=operation_type,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response=response,
                now=now,
            )
            try:
                await _finish_session_write(
                    db,
                    external_session=external_session,
                )
            except Exception:
                await _rollback_owned_session(
                    db,
                    external_session=external_session,
                )
                raise
            if not external_session:
                await db.refresh(execution)
            return AgentRunDecisionRecordResult(
                execution=AgentRunDecisionExecutionModel.model_validate(execution),
                created=inserted_id == execution_id,
            )

    async def get_decision_execution(
        self,
        execution_id: str,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionModel | None:
        async with get_async_db_context(db) as db:
            row = await db.get(AgentRunDecisionExecution, execution_id)
            return AgentRunDecisionExecutionModel.model_validate(row) if row else None

    async def validate_approved_tool_replay(
        self,
        run_id: str,
        *,
        execution_id: str,
        tool_call_id: str,
        tool_id: str,
        arguments: dict[str, Any],
        idempotency_key: str,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionModel | None:
        async with get_async_db_context(db) as db:
            execution = await db.get(AgentRunDecisionExecution, execution_id)
            if (
                execution is None
                or execution.run_id != run_id
                or execution.resource_type != 'approval'
                or execution.decision != 'approved'
                or execution.status
                not in {
                    'activating',
                    'activated',
                    'succeeded',
                }
                or not execution.completion_event_id
            ):
                return None

            run = await db.get(AgentRun, run_id)
            if run is None or _state(run.state) != AgentRunState.RUNNING:
                return None

            requested = await db.execute(
                select(AgentRunEvent).filter_by(
                    run_id=run_id,
                    seq=execution.request_event_seq,
                    event_type='approval.requested',
                )
            )
            request_event = requested.scalars().first()
            request_payload = (request_event.payload or {}) if request_event else {}
            arguments_fingerprint = _decision_payload_fingerprint(arguments)
            if (
                request_payload.get('approval_id') != execution.resource_id
                or request_payload.get('tool_call_id') != tool_call_id
                or request_payload.get('tool_id') != tool_id
                or request_payload.get('tool_arguments_fingerprint')
                != arguments_fingerprint
                or request_payload.get('tool_call_idempotency_key')
                != idempotency_key
                or execution.tool_arguments_fingerprint != arguments_fingerprint
                or execution.tool_call_idempotency_key != idempotency_key
            ):
                return None

            completion = await db.get(AgentRunEvent, execution.completion_event_id)
            completion_payload = (completion.payload or {}) if completion else {}
            if (
                completion is None
                or completion.run_id != run_id
                or completion.event_type != 'approval.completed'
                or completion_payload.get('approval_id') != execution.resource_id
                or completion_payload.get('tool_call_id') != tool_call_id
                or completion_payload.get('tool_id') != tool_id
                or completion_payload.get('tool_arguments_fingerprint')
                != arguments_fingerprint
                or completion_payload.get('tool_call_idempotency_key')
                != idempotency_key
                or completion_payload.get('decision') != 'approved'
                or completion_payload.get('execution_id') != execution.id
            ):
                return None
            return AgentRunDecisionExecutionModel.model_validate(execution)

    async def claim_decision_execution(
        self,
        execution_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now_ns: int | None = None,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionClaim | None:
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            lease_now = await _database_now_ns(db)
            retry_now = now_ns if now_ns is not None else lease_now
            token = str(uuid4())
            result = await db.execute(
                update(AgentRunDecisionExecution)
                .where(
                    AgentRunDecisionExecution.id == execution_id,
                    or_(
                        and_(
                            AgentRunDecisionExecution.status == 'pending',
                            or_(
                                AgentRunDecisionExecution.next_attempt_at.is_(None),
                                AgentRunDecisionExecution.next_attempt_at <= retry_now,
                            ),
                        ),
                        and_(
                            AgentRunDecisionExecution.status == 'claimed',
                            AgentRunDecisionExecution.claim_expires_at <= lease_now,
                        ),
                        and_(
                            AgentRunDecisionExecution.status.in_(
                                {
                                    'prepared',
                                    'backend_committed',
                                    'activating',
                                    'activated',
                                }
                            ),
                            or_(
                                AgentRunDecisionExecution.claim_expires_at.is_(None),
                                AgentRunDecisionExecution.claim_expires_at <= lease_now,
                            ),
                            or_(
                                AgentRunDecisionExecution.next_attempt_at.is_(None),
                                AgentRunDecisionExecution.next_attempt_at <= retry_now,
                            ),
                        ),
                    ),
                )
                .values(
                    status=case(
                        (
                            AgentRunDecisionExecution.status == 'pending',
                            'claimed',
                        ),
                        else_=AgentRunDecisionExecution.status,
                    ),
                    claim_owner=worker_id,
                    claim_token=token,
                    claimed_at=lease_now,
                    claim_expires_at=lease_now
                    + int(lease_seconds * 1_000_000_000),
                    attempt_count=AgentRunDecisionExecution.attempt_count + 1,
                    updated_at=lease_now,
                )
                .execution_options(synchronize_session=False)
            )
            await _finish_session_write(db, external_session=external_session)
            if result.rowcount != 1:
                return None
            row = await _load_decision_execution_fresh(db, execution_id)
            if row is None:
                raise AgentRunNotFound(execution_id)
            return AgentRunDecisionExecutionClaim(
                execution=AgentRunDecisionExecutionModel.model_validate(row)
            )

    async def renew_decision_execution_claim(
        self,
        execution_id: str,
        *,
        claim_token: str,
        lease_seconds: float,
        db: AsyncSession | None = None,
    ) -> bool:
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            lease_now = await _database_now_ns(db)
            result = await db.execute(
                update(AgentRunDecisionExecution)
                .where(
                    AgentRunDecisionExecution.id == execution_id,
                    AgentRunDecisionExecution.claim_token == claim_token,
                    AgentRunDecisionExecution.status.in_(
                        {
                            DecisionExecutionStatus.CLAIMED.value,
                            DecisionExecutionStatus.PREPARED.value,
                            DecisionExecutionStatus.BACKEND_COMMITTED.value,
                            DecisionExecutionStatus.ACTIVATING.value,
                            DecisionExecutionStatus.ACTIVATED.value,
                        }
                    ),
                    AgentRunDecisionExecution.claim_expires_at > lease_now,
                )
                .values(
                    claim_expires_at=lease_now
                    + int(lease_seconds * 1_000_000_000),
                    updated_at=lease_now,
                )
                .execution_options(synchronize_session=False)
            )
            await _finish_session_write(db, external_session=external_session)
            return result.rowcount == 1

    async def claim_next_decision_execution(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        now_ns: int | None = None,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionModel | None:
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            lease_now = await _database_now_ns(db)
            retry_now = now_ns if now_ns is not None else lease_now
            result = await db.execute(
                select(AgentRunDecisionExecution.id)
                .filter(
                    or_(
                        and_(
                            AgentRunDecisionExecution.status == 'pending',
                            or_(
                                AgentRunDecisionExecution.next_attempt_at.is_(None),
                                AgentRunDecisionExecution.next_attempt_at <= retry_now,
                            ),
                        ),
                        and_(
                            AgentRunDecisionExecution.status == 'claimed',
                            AgentRunDecisionExecution.claim_expires_at <= lease_now,
                        ),
                        and_(
                            AgentRunDecisionExecution.status.in_(
                                {
                                    'prepared',
                                    'backend_committed',
                                    'activating',
                                    'activated',
                                }
                            ),
                            or_(
                                AgentRunDecisionExecution.claim_expires_at.is_(None),
                                AgentRunDecisionExecution.claim_expires_at <= lease_now,
                            ),
                            or_(
                                AgentRunDecisionExecution.next_attempt_at.is_(None),
                                AgentRunDecisionExecution.next_attempt_at <= retry_now,
                            ),
                        ),
                    )
                )
                .order_by(AgentRunDecisionExecution.created_at.asc())
                .limit(1)
            )
            execution_id = result.scalar_one_or_none()
        if execution_id is None:
            return None
        claim = await self.claim_decision_execution(
            execution_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now_ns=retry_now,
            db=db if external_session else None,
        )
        return claim.execution if claim is not None else None

    async def mark_decision_execution_prepared(
        self,
        execution_id: str,
        response: dict[str, Any],
        *,
        claim_token: str,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionModel:
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            now = await _database_now_ns(db)
            result = await db.execute(
                update(AgentRunDecisionExecution)
                .where(
                    AgentRunDecisionExecution.id == execution_id,
                    AgentRunDecisionExecution.claim_token == claim_token,
                    AgentRunDecisionExecution.status == 'claimed',
                    AgentRunDecisionExecution.claim_expires_at > now,
                )
                .values(
                    status='prepared',
                    prepare_response=response,
                    prepared_at=case(
                        (
                            AgentRunDecisionExecution.prepared_at.is_(None),
                            now,
                        ),
                        else_=AgentRunDecisionExecution.prepared_at,
                    ),
                    last_error=None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            await _finish_session_write(db, external_session=external_session)
            row = await _load_decision_execution_fresh(db, execution_id)
            if row is None:
                raise AgentRunNotFound(execution_id)
            if result.rowcount == 1:
                return AgentRunDecisionExecutionModel.model_validate(row)
            _require_decision_claim(row, claim_token)
            if row.status in {
                'prepared',
                'backend_committed',
                'activating',
                'activated',
                'cancelled',
                'failed',
                'succeeded',
            }:
                return AgentRunDecisionExecutionModel.model_validate(row)
            raise AgentRunDecisionConflict(
                f'Execution {execution_id} claim is expired or state is {row.status}'
            )

    async def commit_prepared_decision_execution(
        self,
        execution_id: str,
        *,
        claim_token: str,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionModel:
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            now = await _database_now_ns(db)
            gate = await db.execute(
                update(AgentRunDecisionExecution)
                .where(
                    AgentRunDecisionExecution.id == execution_id,
                    AgentRunDecisionExecution.claim_token == claim_token,
                    AgentRunDecisionExecution.status == 'prepared',
                    AgentRunDecisionExecution.claim_expires_at > now,
                )
                .values(status='committing', updated_at=now)
                .execution_options(synchronize_session=False)
            )
            execution = await _load_decision_execution_fresh(db, execution_id)
            if execution is None:
                raise AgentRunNotFound(execution_id)
            if gate.rowcount != 1:
                return _resolve_prepared_commit_gate_conflict(
                    execution,
                    claim_token,
                )
            run = await db.get(AgentRun, execution.run_id)
            if run is None:
                raise AgentRunNotFound(execution.run_id)
            waiting_state = _decision_waiting_state(execution.resource_type)
            if _state(run.state) != waiting_state:
                if _state(run.state) in TERMINAL_STATES:
                    execution.status = 'cancelled'
                    execution.updated_at = now
                    await _finish_session_write(
                        db,
                        external_session=external_session,
                    )
                    return AgentRunDecisionExecutionModel.model_validate(execution)
                raise AgentRunStateError(
                    f'Agent run {run.id} is {run.state}, not {waiting_state.value}'
                )
            if int(run.state_version) != execution.expected_run_state_version:
                raise AgentRunStateError(
                    f'Agent run {run.id} state version changed before decision commit'
                )
            requested = await _find_resource_event(
                db,
                run_id=run.id,
                event_types=frozenset({_decision_request_event_type(execution.resource_type)}),
                resource_field=_decision_resource_field(execution.resource_type),
                resource_id=execution.resource_id,
            )
            if requested is None or requested.seq != execution.request_event_seq:
                raise AgentRunDecisionConflict('decision request event changed')
            event_payload = {
                **(requested.payload or {}),
                **execution.command_payload,
                'execution_id': execution.id,
                'fingerprint': execution.fingerprint,
            }
            event_type = _decision_result_event_type(
                execution.resource_type,
                execution.decision,
            )
            event_result = await _prepare_new_event(
                db,
                run=run,
                event_type=event_type,
                participant_id=requested.participant_id,
                phase=AgentRunState.RUNNING.value,
                summary=(
                    f'Approval {execution.decision}.'
                    if execution.resource_type == 'approval'
                    else f'User input {execution.decision}.'
                ),
                payload=event_payload,
                operation_id=None,
            )
            execution.status = 'backend_committed'
            execution.backend_committed_at = now
            execution.completion_event_id = event_result.event.id
            execution.completion_event_seq = event_result.event.seq
            execution.updated_at = now
            try:
                await _finish_session_write(
                    db,
                    external_session=external_session,
                )
            except Exception:
                await _rollback_owned_session(
                    db,
                    external_session=external_session,
                )
                raise
            if not external_session:
                await db.refresh(execution)
            return AgentRunDecisionExecutionModel.model_validate(execution)

    async def begin_decision_activation(
        self,
        execution_id: str,
        *,
        claim_token: str,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionModel:
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            now = await _database_now_ns(db)
            run_is_running = (
                select(AgentRun.id)
                .where(
                    AgentRun.id == AgentRunDecisionExecution.run_id,
                    AgentRun.state == AgentRunState.RUNNING.value,
                )
                .exists()
            )
            result = await db.execute(
                update(AgentRunDecisionExecution)
                .where(
                    AgentRunDecisionExecution.id == execution_id,
                    AgentRunDecisionExecution.claim_token == claim_token,
                    AgentRunDecisionExecution.status.in_(
                        {'backend_committed', 'activating', 'activated'}
                    ),
                    AgentRunDecisionExecution.claim_expires_at > now,
                    run_is_running,
                )
                .values(status='activating', updated_at=now)
                .execution_options(synchronize_session=False)
            )
            await _finish_session_write(db, external_session=external_session)
            row = await _load_decision_execution_fresh(db, execution_id)
            if row is None:
                raise AgentRunNotFound(execution_id)
            if result.rowcount == 1:
                return AgentRunDecisionExecutionModel.model_validate(row)
            _require_decision_claim(row, claim_token)
            run = await db.get(AgentRun, row.run_id)
            if row.status == 'cancelled' or (
                run is not None and _state(run.state) == AgentRunState.CANCELLED
            ):
                if row.status != 'cancelled':
                    row.status = 'cancelled'
                    row.last_error = {'code': 'run_cancelled'}
                    row.updated_at = now
                    await _finish_session_write(
                        db,
                        external_session=external_session,
                    )
                    if not external_session:
                        await db.refresh(row)
                return AgentRunDecisionExecutionModel.model_validate(row)
            raise AgentRunDecisionConflict(
                f'Execution {execution_id} cannot activate from {row.status}'
            )

    async def record_decision_runtime_state(
        self,
        execution_id: str,
        response: dict[str, Any],
        *,
        claim_token: str,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionModel:
        state = str(response.get('state') or '')
        if state in {'activated', 'applying'}:
            target_status = 'activated'
            terminal = False
        elif state in {'applied', 'cancelled'}:
            target_status = 'succeeded' if state == 'applied' else state
            terminal = True
        else:
            raise AgentRunDecisionConflict(
                f'Unsupported runtime decision execution state: {state}'
            )
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            now = await _database_now_ns(db)
            values: dict[str, Any] = {
                'status': target_status,
                'activate_response': response,
                'activated_at': case(
                    (
                        AgentRunDecisionExecution.activated_at.is_(None),
                        now,
                    ),
                    else_=AgentRunDecisionExecution.activated_at,
                ),
                'updated_at': now,
            }
            if terminal:
                values.update(
                    runtime_outcome=response.get('outcome'),
                    outcome_at=now,
                    last_error=response.get('error'),
                )
            result = await db.execute(
                update(AgentRunDecisionExecution)
                .where(
                    AgentRunDecisionExecution.id == execution_id,
                    AgentRunDecisionExecution.claim_token == claim_token,
                    AgentRunDecisionExecution.status.in_(
                        {'activating', 'activated'}
                    ),
                    AgentRunDecisionExecution.claim_expires_at > now,
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            await _finish_session_write(db, external_session=external_session)
            row = await _load_decision_execution_fresh(db, execution_id)
            if row is None:
                raise AgentRunNotFound(execution_id)
            if result.rowcount == 1:
                return AgentRunDecisionExecutionModel.model_validate(row)
            _require_decision_claim(row, claim_token)
            if row.status in {'cancelled', 'failed', 'succeeded'}:
                return AgentRunDecisionExecutionModel.model_validate(row)
            raise AgentRunDecisionConflict(
                f'Execution {execution_id} claim is expired or state is {row.status}'
            )

    async def release_decision_execution(
        self,
        execution_id: str,
        error: dict[str, Any],
        *,
        claim_token: str,
        now_ns: int | None = None,
        jitter_fraction: float = 0.5,
        retry_after_seconds: float | None = None,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionModel:
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            row = await _load_decision_execution_fresh(db, execution_id)
            if row is None:
                raise AgentRunNotFound(execution_id)
            _require_decision_claim(row, claim_token)
            now = (
                now_ns
                if now_ns is not None
                else await _database_now_ns(db)
            )
            backoff_delay_ns = _decision_retry_delay_ns(
                row.attempt_count,
                jitter_fraction,
            )
            retry_after_ns = int(
                max(float(retry_after_seconds or 0.0), 0.0) * 1_000_000_000
            )
            next_attempt_at = now + max(backoff_delay_ns, retry_after_ns)
            result = await db.execute(
                update(AgentRunDecisionExecution)
                .where(
                    AgentRunDecisionExecution.id == execution_id,
                    AgentRunDecisionExecution.claim_token == claim_token,
                    AgentRunDecisionExecution.status.in_(
                        {'claimed', 'activating', 'activated'}
                    ),
                )
                .values(
                    status=case(
                        (
                            AgentRunDecisionExecution.status == 'claimed',
                            'pending',
                        ),
                        else_='backend_committed',
                    ),
                    claim_owner=None,
                    claim_token=None,
                    claimed_at=None,
                    claim_expires_at=None,
                    next_attempt_at=next_attempt_at,
                    last_error=error,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            await _finish_session_write(db, external_session=external_session)
            row = await _load_decision_execution_fresh(db, execution_id)
            if row is None:
                raise AgentRunNotFound(execution_id)
            if result.rowcount != 1:
                _require_decision_claim(row, claim_token)
                if row.status in {'cancelled', 'failed', 'succeeded'}:
                    return AgentRunDecisionExecutionModel.model_validate(row)
                raise AgentRunDecisionConflict(
                    f'Execution {execution_id} cannot be released from {row.status}'
                )
            return AgentRunDecisionExecutionModel.model_validate(row)

    async def cancel_pending_decision_executions(
        self,
        run_id: str,
        db: AsyncSession | None = None,
    ) -> int:
        external_session = _uses_external_session(db)
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(AgentRunDecisionExecution)
                .where(
                    AgentRunDecisionExecution.run_id == run_id,
                    AgentRunDecisionExecution.status.in_(
                        {'pending', 'claimed', 'prepared'}
                        | {'backend_committed', 'activating', 'activated'}
                    ),
                )
                .values(
                    status='cancelled',
                    last_error={'code': 'run_cancelled'},
                    updated_at=_now_ns(),
                )
                .execution_options(synchronize_session=False)
            )
            await _finish_session_write(db, external_session=external_session)
            return int(result.rowcount or 0)

    async def cancel_run_with_decision_executions(
        self,
        run_id: str,
        *,
        runtime_session_id: str | None,
        db: AsyncSession | None = None,
    ) -> AgentRunEventAppendResult:
        external_session = _uses_external_session(db)
        last_integrity_error: IntegrityError | None = None
        for _attempt in range(EVENT_APPEND_MAX_ATTEMPTS):
            async with get_async_db_context(db) as db:
                run = await db.get(AgentRun, run_id)
                if run is None:
                    raise AgentRunNotFound(run_id)
                canonical = await _find_canonical_lifecycle_event(
                    db,
                    run_id=run_id,
                    event_type='run.cancelled',
                    payload={'runtime_session_id': runtime_session_id},
                )
                if canonical is not None:
                    return AgentRunEventAppendResult(
                        event=AgentRunEventModel.model_validate(canonical),
                        created=False,
                    )
                now = _now_ns()
                await db.execute(
                    update(AgentRunDecisionExecution)
                    .where(
                        AgentRunDecisionExecution.run_id == run_id,
                        AgentRunDecisionExecution.status.in_(
                            {
                                'pending',
                                'claimed',
                                'prepared',
                                'backend_committed',
                                'activating',
                                'activated',
                            }
                        ),
                    )
                    .values(
                        status='cancelled',
                        last_error={'code': 'run_cancelled'},
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                try:
                    result = await _prepare_new_event(
                        db,
                        run=run,
                        event_type='run.cancelled',
                        participant_id='leader',
                        phase='cancelled',
                        summary='Agent run cancelled.',
                        payload={'runtime_session_id': runtime_session_id},
                        operation_id=None,
                    )
                    await _finish_session_write(
                        db,
                        external_session=external_session,
                    )
                except IntegrityError as exc:
                    await _rollback_owned_session(
                        db,
                        external_session=external_session,
                    )
                    if external_session:
                        raise
                    last_integrity_error = exc
                    continue
                except Exception:
                    await _rollback_owned_session(
                        db,
                        external_session=external_session,
                    )
                    raise
                return result
        if last_integrity_error is not None:
            raise last_integrity_error
        raise AgentRunError(f'Failed to cancel agent run {run_id}')

    async def fail_decision_execution(  # noqa: C901
        self,
        execution_id: str,
        *,
        error: dict[str, Any],
        claim_token: str,
        db: AsyncSession | None = None,
    ) -> AgentRunDecisionExecutionModel:
        external_session = _uses_external_session(db)
        last_integrity_error: IntegrityError | None = None
        for _attempt in range(EVENT_APPEND_MAX_ATTEMPTS):
            async with get_async_db_context(db) as db:
                now = await _database_now_ns(db)
                gate = await db.execute(
                    update(AgentRunDecisionExecution)
                    .where(
                        AgentRunDecisionExecution.id == execution_id,
                        AgentRunDecisionExecution.claim_token == claim_token,
                        AgentRunDecisionExecution.status.in_(
                            {
                                DecisionExecutionStatus.CLAIMED.value,
                                DecisionExecutionStatus.PREPARED.value,
                                DecisionExecutionStatus.BACKEND_COMMITTED.value,
                                DecisionExecutionStatus.ACTIVATING.value,
                                DecisionExecutionStatus.ACTIVATED.value,
                            }
                        ),
                        AgentRunDecisionExecution.claim_expires_at > now,
                    )
                    .values(
                        status=DecisionExecutionStatus.FAILING.value,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                execution = await _load_decision_execution_fresh(db, execution_id)
                if execution is None:
                    raise AgentRunNotFound(execution_id)
                if gate.rowcount != 1:
                    _require_decision_claim(execution, claim_token)
                    if execution.status in {'cancelled', 'failed', 'succeeded'}:
                        return AgentRunDecisionExecutionModel.model_validate(execution)
                    raise AgentRunDecisionConflict(
                        f'Execution {execution_id} claim is expired or state is {execution.status}'
                    )
                run = await db.get(AgentRun, execution.run_id)
                if run is None:
                    raise AgentRunNotFound(execution.run_id)
                if _state(run.state) == AgentRunState.CANCELLED:
                    execution.status = 'cancelled'
                    execution.last_error = {'code': 'run_cancelled'}
                    execution.updated_at = now
                    await _finish_session_write(
                        db,
                        external_session=external_session,
                    )
                    if not external_session:
                        await db.refresh(execution)
                    return AgentRunDecisionExecutionModel.model_validate(execution)
                try:
                    if _state(run.state) not in TERMINAL_STATES:
                        await _prepare_new_event(
                            db,
                            run=run,
                            event_type='run.failed',
                            participant_id='leader',
                            phase='failed',
                            summary='Agent decision execution failed.',
                            payload={
                                'execution_id': execution.id,
                                'error': error,
                            },
                            operation_id=None,
                        )
                    execution.status = 'failed'
                    execution.last_error = error
                    execution.outcome_at = now
                    execution.updated_at = now
                    await _finish_session_write(
                        db,
                        external_session=external_session,
                    )
                except IntegrityError as exc:
                    await _rollback_owned_session(
                        db,
                        external_session=external_session,
                    )
                    if external_session:
                        raise
                    last_integrity_error = exc
                    continue
                except Exception:
                    await _rollback_owned_session(
                        db,
                        external_session=external_session,
                    )
                    raise
                if not external_session:
                    await db.refresh(execution)
                return AgentRunDecisionExecutionModel.model_validate(execution)
        if last_integrity_error is not None:
            raise last_integrity_error
        raise AgentRunError(f'Failed decision execution {execution_id}')

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

    async def append_final_delta_event(
        self,
        delta,
        db: AsyncSession | None = None,
    ) -> AgentRunEventModel:
        last_integrity_error: IntegrityError | None = None
        for _attempt in range(EVENT_APPEND_MAX_ATTEMPTS):
            async with get_async_db_context(db) as db:
                row = await db.get(AgentRun, delta.run_id)
                if row is None:
                    raise AgentRunNotFound(delta.run_id)

                state = dict(row.final_delta_state or {})
                stream_state = dict(state.get(delta.final_stream_id) or {})
                seen = {int(index) for index in stream_state.get('seen', [])}
                event_ids = {
                    str(index): event_id
                    for index, event_id in dict(stream_state.get('events') or {}).items()
                }

                if delta.delta_index in seen:
                    stored = await self._get_final_delta_event(
                        db,
                        delta.run_id,
                        delta.final_stream_id,
                        delta.delta_index,
                        event_id=event_ids.get(str(delta.delta_index)),
                    )
                    if stored is None:
                        raise AgentRunError('final delta text was stored without an event')
                    return AgentRunEventModel.model_validate(stored)

                expected = len(seen)
                if delta.delta_index != expected:
                    raise ValueError(f'expected delta_index {expected}, got {delta.delta_index}')

                result = await db.execute(
                    select(AgentRunEvent.seq)
                    .filter_by(run_id=delta.run_id)
                    .order_by(AgentRunEvent.seq.desc())
                    .limit(1)
                )
                next_seq = (result.scalar() or 0) + 1
                now = _now_ns()
                text_after_delta = (row.final_text or '') + delta.delta
                event = AgentRunEvent(
                    id=str(uuid4()),
                    run_id=delta.run_id,
                    seq=next_seq,
                    event_type='final.delta',
                    participant_id=delta.participant_id,
                    phase=AgentRunState.FINALIZING.value,
                    summary=None,
                    payload={
                        **delta.payload,
                        'final_stream_id': delta.final_stream_id,
                        'delta_index': delta.delta_index,
                        'delta': delta.delta,
                        'text': text_after_delta,
                    },
                    created_at=now,
                )
                seen.add(delta.delta_index)
                event_ids[str(delta.delta_index)] = event.id
                row.final_text = text_after_delta
                state[delta.final_stream_id] = {
                    'seen': sorted(seen),
                    'events': event_ids,
                }
                row.final_delta_state = state
                row.updated_at = now
                db.add(event)
                try:
                    await db.commit()
                except IntegrityError as exc:
                    await db.rollback()
                    last_integrity_error = exc
                    continue
                return AgentRunEventModel.model_validate(event)

        if last_integrity_error is not None:
            raise last_integrity_error
        raise AgentRunError(f'Failed to append final delta for agent run {delta.run_id}')

    async def _get_final_delta_event(
        self,
        db: AsyncSession,
        run_id: str,
        final_stream_id: str,
        delta_index: int,
        *,
        event_id: str | None = None,
    ) -> AgentRunEvent | None:
        if event_id is not None:
            row = await db.get(AgentRunEvent, event_id)
            if row is not None:
                return row
        result = await db.execute(
            select(AgentRunEvent)
            .filter(
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.event_type == 'final.delta',
                AgentRunEvent.payload['final_stream_id'].as_string() == final_stream_id,
                AgentRunEvent.payload['delta_index'].as_integer() == delta_index,
            )
            .limit(1)
        )
        return result.scalars().first()

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
            operation_id = str(uuid4())
            operation_values = {
                'id': operation_id,
                'run_id': run_id,
                'operation_type': operation_type,
                'idempotency_key': idempotency_key,
                'request_hash': request_hash,
                'status': 'in_progress',
                'created_at': now,
                'updated_at': now,
            }
            dialect_name = db.get_bind().dialect.name
            if dialect_name == 'sqlite':
                insert_statement = sqlite_insert(AgentRunOperation)
            elif dialect_name == 'postgresql':
                insert_statement = postgresql_insert(AgentRunOperation)
            else:
                raise AgentRunError(
                    f'Atomic agent operation claims are unsupported for database dialect {dialect_name}'
                )

            inserted = await db.execute(
                insert_statement.values(**operation_values)
                .on_conflict_do_nothing(
                    index_elements=[
                        AgentRunOperation.run_id,
                        AgentRunOperation.operation_type,
                        AgentRunOperation.idempotency_key,
                    ]
                )
                .returning(AgentRunOperation.id)
            )
            inserted_id = inserted.scalar_one_or_none()
            await db.commit()

            result = await db.execute(
                select(AgentRunOperation).filter_by(
                    run_id=run_id,
                    operation_type=operation_type,
                    idempotency_key=idempotency_key,
                )
            )
            row = result.scalars().one()
            if row.request_hash != request_hash:
                raise AgentRunOperationConflict(
                    'idempotency key was reused with a different request hash'
                )
            return AgentRunOperationClaim(
                operation=AgentRunOperationModel.model_validate(row),
                created=inserted_id == operation_id,
            )

    async def find_operation_by_idempotency_key(
        self,
        run_id: str,
        *,
        operation_type: str,
        idempotency_key: str,
        db: AsyncSession | None = None,
    ) -> AgentRunOperationModel | None:
        """Return the existing operation row for an idempotency key, or None.

        Used to recover from AgentRunOperationConflict on event.append: when
        the same key was already used with a different request_hash, callers
        can resolve to the previously stored event instead of failing the run.
        """
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AgentRunOperation).filter_by(
                    run_id=run_id,
                    operation_type=operation_type,
                    idempotency_key=idempotency_key,
                )
            )
            row = result.scalars().first()
            if row is None:
                return None
            return AgentRunOperationModel.model_validate(row)

    async def finish_operation_success(
        self,
        operation_id: str,
        response: dict[str, Any],
        db: AsyncSession | None = None,
    ) -> AgentRunOperationModel:
        async with get_async_db_context(db) as db:
            await db.execute(
                update(AgentRunOperation)
                .where(
                    AgentRunOperation.id == operation_id,
                    AgentRunOperation.status == 'in_progress',
                )
                .values(
                    status='succeeded',
                    response=response,
                    error=None,
                    updated_at=_now_ns(),
                )
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            db.expire_all()
            row = await db.get(AgentRunOperation, operation_id)
            if row is None:
                raise AgentRunNotFound(operation_id)
            await db.refresh(row)
            return AgentRunOperationModel.model_validate(row)

    async def finish_operation_error(
        self,
        operation_id: str,
        error: dict[str, Any],
        db: AsyncSession | None = None,
    ) -> AgentRunOperationModel:
        async with get_async_db_context(db) as db:
            await db.execute(
                update(AgentRunOperation)
                .where(
                    AgentRunOperation.id == operation_id,
                    AgentRunOperation.status == 'in_progress',
                )
                .values(
                    status='failed',
                    response=None,
                    error=error,
                    updated_at=_now_ns(),
                )
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            db.expire_all()
            row = await db.get(AgentRunOperation, operation_id)
            if row is None:
                raise AgentRunNotFound(operation_id)
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
