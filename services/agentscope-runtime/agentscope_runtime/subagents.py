from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


DEFAULT_TEAM_CAP = 5
LEADER_PARTICIPANT_ID = "leader"


class SubagentRejected(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "subagent_rejected",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class OpenWebUISubagentCallbacks(Protocol):
    async def append_event(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        event_type: str,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        participant_id: str | None = None,
        phase: str | None = None,
    ) -> dict[str, Any]:
        ...

    async def register_subagent(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        parent_participant_id: str,
        participant_id: str,
        name: str,
        description: str,
        task: str,
        budget: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    async def select_model(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        selection_id: str,
        requested_model_id: str | None = None,
        fuzzy_request: str | None = None,
        source_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class SubagentSpec:
    name: str
    description: str
    task: str
    requested_model_id: str | None = None
    fuzzy_model_request: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentExecutionContext:
    run_id: str
    runtime_session_id: str
    parent_participant_id: str
    participant_id: str
    name: str
    description: str
    task: str
    model_id: str
    budget: dict[str, Any]
    model_selection: dict[str, Any]
    openwebui_credentials: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentResult:
    participant_id: str
    status: str
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


SubagentExecutor = Callable[
    [SubagentExecutionContext],
    Awaitable[dict[str, Any]] | dict[str, Any],
]


class AgentScopeSubagentAdapter:
    """Map AgentScope leader subagent intents to OpenWebUI callbacks.

    The adapter keeps AgentScope on the orchestration side only. OpenWebUI is
    still the authority for participant records, model selection, and events.
    """

    def __init__(
        self,
        *,
        run_id: str,
        runtime_session_id: str,
        callback_client: OpenWebUISubagentCallbacks,
        team_cap: int = DEFAULT_TEAM_CAP,
        is_cancelled: Callable[[], bool] | None = None,
        kill_terminal_process: Callable[[str], None] | None = None,
    ) -> None:
        self.run_id = run_id
        self.runtime_session_id = runtime_session_id
        self.callback_client = callback_client
        self.team_cap = min(team_cap, DEFAULT_TEAM_CAP)
        self._is_cancelled = is_cancelled or (lambda: False)
        self._kill_terminal_process = kill_terminal_process
        self._next_subagent_index = 1
        self._next_selection_index = 1
        self._cancel_event_sent = False

    async def run_subagent(
        self,
        *,
        parent_participant_id: str,
        spec: SubagentSpec,
        executor: SubagentExecutor,
    ) -> SubagentResult:
        if parent_participant_id != LEADER_PARTICIPANT_ID:
            raise SubagentRejected(
                "Only the leader can create subagents in the MVP runtime.",
                code="nested_subagent_not_allowed",
                details={"parent_participant_id": parent_participant_id},
            )

        participant_id = self._allocate_participant_id()
        try:
            await self._register_subagent(
                parent_participant_id=parent_participant_id,
                participant_id=participant_id,
                spec=spec,
            )
        except SubagentRejected as exc:
            await self._emit_subagent_failed(participant_id, spec, exc)
            raise
        except Exception as exc:
            rejected = SubagentRejected(
                str(exc),
                code="subagent_registration_failed",
                details={"type": exc.__class__.__name__},
            )
            await self._emit_subagent_failed(participant_id, spec, rejected)
            raise rejected from exc

        model_selection = await self._select_subagent_model(participant_id, spec)
        model_id = str(model_selection["selected_model_id"])
        await self._emit_subagent_created(
            participant_id,
            parent_participant_id,
            spec,
            model_selection,
        )

        context = SubagentExecutionContext(
            run_id=self.run_id,
            runtime_session_id=self.runtime_session_id,
            parent_participant_id=parent_participant_id,
            participant_id=participant_id,
            name=spec.name,
            description=spec.description,
            task=spec.task,
            model_id=model_id,
            budget=dict(spec.budget),
            model_selection=model_selection,
            openwebui_credentials={},
        )

        try:
            raw_result = await _maybe_await(executor(context))
        except Exception as exc:
            rejected = SubagentRejected(
                str(exc),
                code="subagent_execution_failed",
                details={"type": exc.__class__.__name__},
            )
            await self._emit_subagent_failed(participant_id, spec, rejected)
            return SubagentResult(
                participant_id=participant_id,
                status="failed",
                error=_error_payload(rejected),
            )

        result = SubagentResult(
            participant_id=participant_id,
            status="completed",
            content=_result_content(raw_result),
            metadata=_result_metadata(raw_result),
        )
        await self._emit_subagent_completed(participant_id, spec, result)
        return result

    async def run_subagent_plan(
        self,
        specs: list[SubagentSpec],
        *,
        executor: SubagentExecutor,
    ) -> list[SubagentResult]:
        results: list[SubagentResult] = []
        for spec in specs:
            if self._is_cancelled():
                await self._emit_cancelled()
                break

            result = await self.run_subagent(
                parent_participant_id=LEADER_PARTICIPANT_ID,
                spec=spec,
                executor=executor,
            )
            results.append(result)

            if self._is_cancelled():
                await self._emit_cancelled()
                break

        return results

    def _allocate_participant_id(self) -> str:
        participant_id = f"subagent:{self.run_id}:{self._next_subagent_index}"
        self._next_subagent_index += 1
        return participant_id

    def _allocate_selection_id(self) -> str:
        selection_id = f"selection-{self._next_selection_index}"
        self._next_selection_index += 1
        return selection_id

    async def _register_subagent(
        self,
        *,
        parent_participant_id: str,
        participant_id: str,
        spec: SubagentSpec,
    ) -> dict[str, Any]:
        idempotency_key = f"subagent:{self.run_id}:{participant_id}:create"
        metadata = {
            **spec.metadata,
            "team_cap": self.team_cap,
            "single_level": True,
        }
        return await self.callback_client.register_subagent(
            run_id=self.run_id,
            idempotency_key=idempotency_key,
            parent_participant_id=parent_participant_id,
            participant_id=participant_id,
            name=spec.name,
            description=spec.description,
            task=spec.task,
            budget=dict(spec.budget),
            metadata=metadata,
        )

    async def _select_subagent_model(
        self,
        participant_id: str,
        spec: SubagentSpec,
    ) -> dict[str, Any]:
        selection_id = self._allocate_selection_id()
        source_request = {
            "name": spec.name,
            "task": spec.task,
        }
        if spec.fuzzy_model_request:
            source_request["request"] = spec.fuzzy_model_request
        if spec.requested_model_id:
            source_request["requested_model_id"] = spec.requested_model_id

        return await self.callback_client.select_model(
            run_id=self.run_id,
            idempotency_key=f"modelsel:{participant_id}:{selection_id}:1",
            participant_id=participant_id,
            selection_id=selection_id,
            requested_model_id=spec.requested_model_id,
            fuzzy_request=spec.fuzzy_model_request,
            source_request=source_request,
        )

    async def _emit_subagent_created(
        self,
        participant_id: str,
        parent_participant_id: str,
        spec: SubagentSpec,
        model_selection: dict[str, Any],
    ) -> None:
        await self.callback_client.append_event(
            run_id=self.run_id,
            idempotency_key=f"evt:{self.runtime_session_id}:{participant_id}:created",
            event_type="subagent.created",
            summary=f"Subagent {spec.name} started.",
            payload={
                "parent_participant_id": parent_participant_id,
                "participant_id": participant_id,
                "name": spec.name,
                "description": spec.description,
                "task": spec.task,
                "model_id": model_selection.get("selected_model_id"),
                "model_selection": model_selection,
            },
            participant_id=participant_id,
            phase="running",
        )

    async def _emit_subagent_completed(
        self,
        participant_id: str,
        spec: SubagentSpec,
        result: SubagentResult,
    ) -> None:
        await self.callback_client.append_event(
            run_id=self.run_id,
            idempotency_key=f"evt:{self.runtime_session_id}:{participant_id}:completed",
            event_type="subagent.completed",
            summary=f"Subagent {spec.name} completed.",
            payload={
                "participant_id": participant_id,
                "name": spec.name,
                "status": result.status,
                "content": result.content,
                "metadata": result.metadata,
            },
            participant_id=participant_id,
            phase="running",
        )

    async def _emit_subagent_failed(
        self,
        participant_id: str,
        spec: SubagentSpec,
        exc: SubagentRejected,
    ) -> None:
        await self.callback_client.append_event(
            run_id=self.run_id,
            idempotency_key=f"evt:{self.runtime_session_id}:{participant_id}:failed",
            event_type="subagent.failed",
            summary=f"Subagent {spec.name} failed.",
            payload={
                "participant_id": participant_id,
                "name": spec.name,
                "status": "failed",
                "error": _error_payload(exc),
            },
            participant_id=participant_id,
            phase="running",
        )

    async def _emit_cancelled(self) -> None:
        if self._cancel_event_sent:
            return
        self._cancel_event_sent = True
        await self.callback_client.append_event(
            run_id=self.run_id,
            idempotency_key=f"evt:{self.runtime_session_id}:run-cancelled",
            event_type="run.cancelled",
            summary="Agent runtime loop stopped after cancellation.",
            payload={"runtime_session_id": self.runtime_session_id},
            participant_id=LEADER_PARTICIPANT_ID,
            phase="cancelled",
        )


async def _maybe_await(value: Awaitable[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    if inspect.isawaitable(value):
        return await value
    return value


def _result_content(result: dict[str, Any]) -> str | None:
    content = result.get("content")
    return str(content) if content is not None else None


def _result_metadata(result: dict[str, Any]) -> dict[str, Any]:
    metadata = result.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _error_payload(exc: SubagentRejected) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": str(exc),
        "retryable": False,
        "details": exc.details,
    }
