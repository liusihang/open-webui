from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager


class AgentToolExecutionCancelled(RuntimeError):
    code = 'tool_cancelled'


class AgentToolExecutionRegistry:
    """Process-local ownership of in-flight tool request tasks, scoped by run."""

    def __init__(self) -> None:
        self._tasks: dict[str, set[asyncio.Task]] = {}
        self._cancellation_holds: dict[str, int] = {}

    @contextmanager
    def track_current(self, run_id: str) -> Iterator[None]:
        if self._cancellation_holds.get(run_id, 0):
            raise AgentToolExecutionCancelled(f'Agent run {run_id} is cancelling tool execution')
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError('Tool execution must run inside an asyncio task')
        tasks = self._tasks.setdefault(run_id, set())
        tasks.add(task)
        try:
            yield
        finally:
            tasks.discard(task)
            if not tasks:
                self._tasks.pop(run_id, None)

    @asynccontextmanager
    async def cancelling_run(self, run_id: str) -> AsyncIterator[int]:
        self._cancellation_holds[run_id] = self._cancellation_holds.get(run_id, 0) + 1
        try:
            tasks = tuple(self._tasks.get(run_id, ()))
            for task in tasks:
                if not task.done() and task.cancelling() == 0:
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            yield len(tasks)
        finally:
            remaining = self._cancellation_holds[run_id] - 1
            if remaining:
                self._cancellation_holds[run_id] = remaining
            else:
                self._cancellation_holds.pop(run_id, None)

    def active_count(self, run_id: str) -> int:
        return len(self._tasks.get(run_id, ()))


def get_agent_tool_execution_registry(app) -> AgentToolExecutionRegistry:
    registry = getattr(app.state, 'AGENT_TOOL_EXECUTION_REGISTRY', None)
    if registry is None:
        registry = AgentToolExecutionRegistry()
        app.state.AGENT_TOOL_EXECUTION_REGISTRY = registry
    return registry
