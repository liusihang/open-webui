from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from open_webui.agent.protocol import AgentRunState

TERMINAL_STATES = {
    AgentRunState.COMPLETED,
    AgentRunState.FAILED,
    AgentRunState.CANCELLED,
    AgentRunState.BUDGET_EXCEEDED,
}

NON_TERMINAL_STATES = [
    AgentRunState.QUEUED,
    AgentRunState.RUNNING,
    AgentRunState.WAITING_APPROVAL,
    AgentRunState.FINALIZING,
]


class AgentRunLifecycleStore(Protocol):
    def get_run_state(self, run_id: str) -> AgentRunState: ...

    def transition_state(
        self,
        run_id: str,
        *,
        from_states: list[str],
        to_state: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class _ManagedResource:
    resource_type: str
    resource_key: str
    close: Callable[[], Any]
    participant_id: str | None = None

    @property
    def label(self) -> str:
        return f'{self.resource_type}:{self.resource_key}'


@dataclass(frozen=True)
class _ApprovalWait:
    approval_id: str
    resolve: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class _SseTail:
    subscriber_id: str
    stop: Callable[[], Any]


@dataclass(frozen=True)
class _TerminalProcess:
    process_ref: dict[str, Any]
    kill: Callable[[], Any] | None = None


@dataclass
class AgentRunCleanupResult:
    run_id: str
    terminal_state: str
    cleaned: bool
    closed_resources: list[str] = field(default_factory=list)
    resolved_approval_waits: list[str] = field(default_factory=list)
    stopped_sse_tails: list[str] = field(default_factory=list)
    retained_process_refs: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] | None = None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _state(value: str | AgentRunState) -> AgentRunState:
    return value if isinstance(value, AgentRunState) else AgentRunState(value)


class AgentRunResourceManager:
    def __init__(self):
        self._resources: dict[str, dict[tuple[str, str], _ManagedResource]] = {}
        self._approval_waits: dict[str, dict[str, _ApprovalWait]] = {}
        self._sse_tails: dict[str, dict[str, _SseTail]] = {}
        self._terminal_processes: dict[str, list[_TerminalProcess]] = {}
        self._runtime_heartbeats: dict[str, int] = {}
        self._cleaned_runs: set[str] = set()
        self._compacted_runs: set[str] = set()

    def register_resource(
        self,
        run_id: str,
        *,
        resource_type: str,
        resource_key: str,
        close: Callable[[], Any],
        participant_id: str | None = None,
    ) -> None:
        resources = self._resources.setdefault(run_id, {})
        resources[(resource_type, resource_key)] = _ManagedResource(
            resource_type=resource_type,
            resource_key=resource_key,
            close=close,
            participant_id=participant_id,
        )

    def register_approval_wait(
        self,
        run_id: str,
        approval_id: str,
        resolve: Callable[[dict[str, Any]], Any],
    ) -> None:
        waits = self._approval_waits.setdefault(run_id, {})
        waits[approval_id] = _ApprovalWait(approval_id=approval_id, resolve=resolve)

    def register_sse_tail(
        self,
        run_id: str,
        subscriber_id: str,
        stop: Callable[[], Any],
    ) -> None:
        tails = self._sse_tails.setdefault(run_id, {})
        tails[subscriber_id] = _SseTail(subscriber_id=subscriber_id, stop=stop)

    def register_terminal_process(
        self,
        run_id: str,
        process_ref: dict[str, Any],
        *,
        kill: Callable[[], Any] | None = None,
    ) -> None:
        processes = self._terminal_processes.setdefault(run_id, [])
        processes.append(_TerminalProcess(process_ref=dict(process_ref), kill=kill))

    def record_runtime_heartbeat(
        self,
        run_id: str,
        *,
        heartbeat_at_ns: int | None = None,
    ) -> None:
        self._runtime_heartbeats[run_id] = heartbeat_at_ns or time.time_ns()

    def process_refs_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(process.process_ref)
            for process in self._terminal_processes.get(run_id, [])
        ]

    async def cleanup_terminal_state(
        self,
        run_id: str,
        terminal_state: str | AgentRunState,
        *,
        compact: Callable[[], Any] | None = None,
    ) -> AgentRunCleanupResult:
        state = _state(terminal_state)
        if state not in TERMINAL_STATES:
            raise ValueError(f'{state.value} is not a terminal Agent Run state')

        retained_process_refs = self.process_refs_for_run(run_id)
        if run_id in self._cleaned_runs:
            return AgentRunCleanupResult(
                run_id=run_id,
                terminal_state=state.value,
                cleaned=False,
                retained_process_refs=retained_process_refs,
            )

        closed_resources = []
        for resource in list(self._resources.pop(run_id, {}).values()):
            await _maybe_await(resource.close())
            closed_resources.append(resource.label)

        terminal_result = {
            'status': state.value,
            'run_id': run_id,
            'terminal_state': state.value,
        }
        resolved_approval_waits = []
        for wait in list(self._approval_waits.pop(run_id, {}).values()):
            await _maybe_await(wait.resolve(terminal_result))
            resolved_approval_waits.append(wait.approval_id)

        stopped_sse_tails = []
        for tail in list(self._sse_tails.pop(run_id, {}).values()):
            await _maybe_await(tail.stop())
            stopped_sse_tails.append(tail.subscriber_id)

        summary = None
        if compact is not None and run_id not in self._compacted_runs:
            summary = await _maybe_await(compact())
            self._compacted_runs.add(run_id)

        self._runtime_heartbeats.pop(run_id, None)
        self._cleaned_runs.add(run_id)

        return AgentRunCleanupResult(
            run_id=run_id,
            terminal_state=state.value,
            cleaned=True,
            closed_resources=closed_resources,
            resolved_approval_waits=resolved_approval_waits,
            stopped_sse_tails=stopped_sse_tails,
            retained_process_refs=retained_process_refs,
            summary=summary,
        )

    async def fail_stale_heartbeats(
        self,
        store: AgentRunLifecycleStore,
        *,
        now_ns: int | None = None,
        timeout_seconds: int,
    ) -> list[str]:
        now = now_ns or time.time_ns()
        timeout_ns = timeout_seconds * 1_000_000_000
        failed_run_ids = []

        for run_id, heartbeat_at_ns in list(self._runtime_heartbeats.items()):
            if now - heartbeat_at_ns <= timeout_ns:
                continue
            state = _state(await _maybe_await(store.get_run_state(run_id)))
            if state in TERMINAL_STATES:
                continue

            await _maybe_await(
                store.transition_state(
                    run_id,
                    from_states=[state.value for state in NON_TERMINAL_STATES],
                    to_state=AgentRunState.FAILED.value,
                    reason='runtime heartbeat stale',
                    payload={
                        'error': {
                            'code': 'agent_runtime_lost',
                            'message': 'Agent runtime heartbeat is stale.',
                            'details': {
                                'heartbeat_at_ns': heartbeat_at_ns,
                                'timeout_seconds': timeout_seconds,
                            },
                        }
                    },
                )
            )
            await self.cleanup_terminal_state(run_id, AgentRunState.FAILED)
            failed_run_ids.append(run_id)

        return failed_run_ids
