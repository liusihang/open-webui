from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentRunState(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    WAITING_APPROVAL = 'waiting_approval'
    FINALIZING = 'finalizing'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    BUDGET_EXCEEDED = 'budget_exceeded'


class AgentEventType(StrEnum):
    RUN_QUEUED = 'run.queued'
    RUN_RUNNING = 'run.running'
    ACTION_SUMMARY = 'action.summary'
    TOOL_REQUESTED = 'tool.requested'
    TOOL_STARTED = 'tool.started'
    TOOL_COMPLETED = 'tool.completed'
    TOOL_FAILED = 'tool.failed'
    APPROVAL_REQUESTED = 'approval.requested'
    APPROVAL_COMPLETED = 'approval.completed'
    ARTIFACT_REGISTERED = 'artifact.registered'
    SUBAGENT_CREATED = 'subagent.created'
    SUBAGENT_UPDATED = 'subagent.updated'
    SUBAGENT_COMPLETED = 'subagent.completed'
    SUBAGENT_FAILED = 'subagent.failed'
    MODEL_SELECTION_REQUESTED = 'model.selection.requested'
    MODEL_SELECTION_COMPLETED = 'model.selection.completed'
    FINAL_STARTED = 'final.started'
    FINAL_DELTA = 'final.delta'
    RUN_COMPLETED = 'run.completed'
    RUN_FAILED = 'run.failed'
    RUN_CANCELLED = 'run.cancelled'
    RUN_BUDGET_EXCEEDED = 'run.budget_exceeded'


class AgentRunEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    run_id: str
    seq: int = Field(ge=1)
    event_type: AgentEventType
    participant_id: str | None = None
    phase: str | None = None
    summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: int


class AgentEventAppend(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    run_id: str
    event_type: AgentEventType
    participant_id: str | None = None
    phase: str | None = None
    summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class FinalDeltaAppend(BaseModel):
    run_id: str
    final_stream_id: str
    delta_index: int = Field(ge=0)
    delta: str
    participant_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class AgentEventListResponse(BaseModel):
    events: list[AgentRunEvent]
    last_seq: int


class AgentRunDetailResponse(BaseModel):
    id: str
    state: AgentRunState
    state_version: int | None = None
    chat_id: str | None = None
    assistant_message_id: str | None = None
    summary: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
