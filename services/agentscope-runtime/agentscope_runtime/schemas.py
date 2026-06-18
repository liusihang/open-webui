from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


RAW_CREDENTIAL_FIELD_NAMES = {
    "user_jwt",
    "provider_key",
    "provider_api_key",
    "mcp_credential",
    "mcp_credentials",
    "terminal_key",
    "terminal_token",
    "tool_server_secret",
    "tool_server_credentials",
    "oauth_token",
    "raw_credentials",
}


class RunStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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

    @model_validator(mode="after")
    def reject_raw_credential_payloads(self) -> "RunStartRequest":
        blocked = _find_raw_credential_fields(
            {
                "user_ref": self.user_ref,
                "budget": self.budget,
                "default_paths": self.default_paths,
                "tool_access_envelope": self.tool_access_envelope,
                "model_catalog": self.model_catalog,
                "messages": self.messages,
                "metadata": self.metadata,
            }
        )
        if blocked:
            fields = ", ".join(sorted(blocked))
            raise ValueError(f"raw credential fields are not accepted by runtime: {fields}")
        return self


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
    run_id: str
    event_type: str
    summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    participant_id: str | None = None
    phase: str | None = None


class SubagentRegisterRequest(BaseModel):
    idempotency_key: str
    run_id: str
    parent_participant_id: str
    participant_id: str
    name: str
    description: str
    task: str
    budget: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelSelectionRequest(BaseModel):
    idempotency_key: str
    run_id: str
    participant_id: str
    selection_id: str
    requested_model_id: str | None = None
    fuzzy_request: str | None = None
    source_request: dict[str, Any] = Field(default_factory=dict)


class ModelCallRequest(BaseModel):
    idempotency_key: str
    run_id: str
    participant_id: str
    model_call_id: str
    model: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    idempotency_key: str
    run_id: str
    participant_id: str
    tool_call_id: str
    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def _find_raw_credential_fields(value: Any, path: str = "") -> set[str]:
    blocked: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}" if path else key_text
            if key_text in RAW_CREDENTIAL_FIELD_NAMES:
                blocked.add(next_path)
            blocked.update(_find_raw_credential_fields(nested, next_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            next_path = f"{path}[{index}]"
            blocked.update(_find_raw_credential_fields(nested, next_path))
    return blocked
