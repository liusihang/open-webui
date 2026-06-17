from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunStartRequest(BaseModel):
    run_id: str
    chat_id: str
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    leader_model_id: str | None = None
    user_ref: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    team_cap: int | None = None
    default_paths: dict[str, Any] = Field(default_factory=dict)
    tool_access_envelope: dict[str, Any] = Field(default_factory=dict)
    model_catalog: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunStartResponse(BaseModel):
    runtime_session_id: str
    accepted: bool


class RunStatusResponse(BaseModel):
    run_id: str
    runtime_session_id: str
    state: str
    cancel_requested: bool = False


class AppendEventRequest(BaseModel):
    idempotency_key: str
    event_type: str
    summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    participant_id: str | None = None
    phase: str | None = None
