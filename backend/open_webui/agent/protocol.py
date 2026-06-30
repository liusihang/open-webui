from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    USER_INPUT_REQUESTED = 'user_input.requested'
    USER_INPUT_COMPLETED = 'user_input.completed'
    USER_INPUT_DECLINED = 'user_input.declined'
    USER_INPUT_CANCELLED = 'user_input.cancelled'
    USER_INPUT_EXPIRED = 'user_input.expired'
    ARTIFACT_REGISTERED = 'artifact.registered'
    SUBAGENT_CREATED = 'subagent.created'
    SUBAGENT_UPDATED = 'subagent.updated'
    SUBAGENT_COMPLETED = 'subagent.completed'
    SUBAGENT_FAILED = 'subagent.failed'
    MODEL_SELECTION_REQUESTED = 'model.selection.requested'
    MODEL_SELECTION_COMPLETED = 'model.selection.completed'
    FINAL_STARTED = 'final.started'
    FINAL_DELTA = 'final.delta'
    TEXT_DELTA = 'text.delta'
    RUN_COMPLETED = 'run.completed'
    RUN_FAILED = 'run.failed'
    RUN_CANCELLED = 'run.cancelled'
    RUN_BUDGET_EXCEEDED = 'run.budget_exceeded'


class TextBlockKind(StrEnum):
    ASSISTANT_NOTE = 'assistant_note'
    ACTION_SUMMARY = 'action_summary'


UNSAFE_REPLAY_FIELD_NAMES = {
    'chain_of_thought',
    'debug',
    'private',
    'raw',
    'raw_reasoning',
    'reasoning',
    'thought',
}


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


class TextDeltaAppend(BaseModel):
    """Public transcript text delta emitted during a run.

    `block_id` identifies a contiguous text segment within a participant's
    turn (one model call may produce multiple text blocks interleaved with
    tool calls). `delta_index` is the per-block monotonic index. Each
    (block_id, delta_index) pair is idempotent — duplicates return the
    previously stored event. Text deltas are replayable public transcript
    text only; they do not write AgentRun.final_text or the final assistant
    message content. Final answers must use final.delta.
    """

    run_id: str
    block_id: str
    block_kind: TextBlockKind
    delta_index: int = Field(ge=0)
    delta: str
    participant_id: str | None = None
    phase: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None

    @field_validator('payload')
    @classmethod
    def reject_unsafe_replay_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        blocked = _find_unsafe_replay_fields(payload)
        if blocked:
            fields = ', '.join(sorted(blocked))
            raise ValueError(f'raw/private/reasoning/debug fields are not accepted in text.delta payload: {fields}')
        return payload


class AgentStateTransitionAppend(BaseModel):
    run_id: str
    from_states: list[AgentRunState]
    to_state: AgentRunState
    reason: str
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


def _find_unsafe_replay_fields(value: Any, path: str = '') -> set[str]:
    blocked: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            next_path = f'{path}.{key_text}' if path else key_text
            if key_text in UNSAFE_REPLAY_FIELD_NAMES:
                blocked.add(next_path)
            blocked.update(_find_unsafe_replay_fields(nested, next_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            next_path = f'{path}[{index}]'
            blocked.update(_find_unsafe_replay_fields(nested, next_path))
    return blocked
