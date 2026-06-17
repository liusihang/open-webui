import pytest

from agentscope_runtime.subagents import (
    AgentScopeSubagentAdapter,
    SubagentExecutionContext,
    SubagentRejected,
    SubagentSpec,
)


class RecordingOpenWebUIRuntimeClient:
    def __init__(self, *, reject_after: int | None = None) -> None:
        self.events: list[dict] = []
        self.subagent_registrations: list[dict] = []
        self.model_selections: list[dict] = []
        self.reject_after = reject_after

    async def append_event(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        event_type: str,
        summary: str | None = None,
        payload: dict | None = None,
        participant_id: str | None = None,
        phase: str | None = None,
    ) -> dict:
        event = {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "event_type": event_type,
            "summary": summary,
            "payload": payload or {},
            "participant_id": participant_id,
            "phase": phase,
        }
        self.events.append(event)
        return {"seq": len(self.events), "event_type": event_type}

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
        budget: dict,
        metadata: dict,
    ) -> dict:
        self.subagent_registrations.append(
            {
                "run_id": run_id,
                "idempotency_key": idempotency_key,
                "parent_participant_id": parent_participant_id,
                "participant_id": participant_id,
                "name": name,
                "description": description,
                "task": task,
                "budget": budget,
                "metadata": metadata,
            }
        )
        if self.reject_after is not None and len(self.subagent_registrations) > self.reject_after:
            raise SubagentRejected("subagent cap exceeded", code="subagent_cap_exceeded")
        return {
            "status": "accepted",
            "participant_id": participant_id,
            "team_cap": 5,
            "remaining_slots": max(0, 5 - len(self.subagent_registrations)),
            "warnings": [],
        }

    async def select_model(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        selection_id: str,
        requested_model_id: str | None = None,
        fuzzy_request: str | None = None,
        source_request: dict | None = None,
    ) -> dict:
        self.model_selections.append(
            {
                "run_id": run_id,
                "idempotency_key": idempotency_key,
                "participant_id": participant_id,
                "selection_id": selection_id,
                "requested_model_id": requested_model_id,
                "fuzzy_request": fuzzy_request,
                "source_request": source_request or {},
            }
        )
        return {
            "selected_model_id": requested_model_id or "model-research",
            "choices": [{"id": requested_model_id or "model-research"}],
            "meta": {"agent_selection": {"reason": "test"}},
            "warnings": [],
        }


@pytest.mark.asyncio
async def test_adapter_registers_subagent_selects_model_and_emits_completion_events() -> None:
    callback_client = RecordingOpenWebUIRuntimeClient()
    adapter = AgentScopeSubagentAdapter(
        run_id="run-team",
        runtime_session_id="rt-run-team",
        callback_client=callback_client,
    )

    async def executor(context: SubagentExecutionContext) -> dict:
        assert context.run_id == "run-team"
        assert context.parent_participant_id == "leader"
        assert context.model_id == "model-research"
        assert context.openwebui_credentials == {}
        return {"content": "research complete", "metadata": {"confidence": "high"}}

    result = await adapter.run_subagent(
        parent_participant_id="leader",
        spec=SubagentSpec(
            name="researcher",
            description="Researches the topic.",
            task="Find the relevant facts.",
            fuzzy_model_request="research long context",
            budget={"max_model_calls": 2},
        ),
        executor=executor,
    )

    assert result.status == "completed"
    assert result.participant_id == "subagent:run-team:1"
    assert callback_client.subagent_registrations == [
        {
            "run_id": "run-team",
            "idempotency_key": "subagent:run-team:subagent:run-team:1:create",
            "parent_participant_id": "leader",
            "participant_id": "subagent:run-team:1",
            "name": "researcher",
            "description": "Researches the topic.",
            "task": "Find the relevant facts.",
            "budget": {"max_model_calls": 2},
            "metadata": {"team_cap": 5, "single_level": True},
        }
    ]
    assert callback_client.model_selections == [
        {
            "run_id": "run-team",
            "idempotency_key": "modelsel:subagent:run-team:1:selection-1:1",
            "participant_id": "subagent:run-team:1",
            "selection_id": "selection-1",
            "requested_model_id": None,
            "fuzzy_request": "research long context",
            "source_request": {
                "name": "researcher",
                "task": "Find the relevant facts.",
                "request": "research long context",
            },
        }
    ]
    assert [event["event_type"] for event in callback_client.events] == [
        "subagent.created",
        "subagent.completed",
    ]
    assert all(event["participant_id"] == "subagent:run-team:1" for event in callback_client.events)


@pytest.mark.asyncio
async def test_adapter_rejects_nested_subagent_creation_before_callbacks() -> None:
    callback_client = RecordingOpenWebUIRuntimeClient()
    adapter = AgentScopeSubagentAdapter(
        run_id="run-team",
        runtime_session_id="rt-run-team",
        callback_client=callback_client,
    )

    with pytest.raises(SubagentRejected, match="Only the leader can create subagents") as exc_info:
        await adapter.run_subagent(
            parent_participant_id="subagent:run-team:1",
            spec=SubagentSpec(
                name="nested",
                description="Should not be created.",
                task="Spawn another worker.",
            ),
            executor=lambda context: {"content": "unreachable"},
        )

    assert exc_info.value.code == "nested_subagent_not_allowed"
    assert callback_client.subagent_registrations == []
    assert callback_client.model_selections == []
    assert callback_client.events == []


@pytest.mark.asyncio
async def test_cap_exceeded_comes_from_openwebui_registration_callback() -> None:
    callback_client = RecordingOpenWebUIRuntimeClient(reject_after=5)
    adapter = AgentScopeSubagentAdapter(
        run_id="run-team",
        runtime_session_id="rt-run-team",
        callback_client=callback_client,
    )

    async def executor(context: SubagentExecutionContext) -> dict:
        return {"content": f"{context.participant_id} complete"}

    for index in range(5):
        result = await adapter.run_subagent(
            parent_participant_id="leader",
            spec=SubagentSpec(
                name=f"worker-{index}",
                description="Allowed worker.",
                task="Do one slice.",
            ),
            executor=executor,
        )
        assert result.status == "completed"

    with pytest.raises(SubagentRejected) as exc_info:
        await adapter.run_subagent(
            parent_participant_id="leader",
            spec=SubagentSpec(
                name="worker-5",
                description="Over cap worker.",
                task="Should be rejected by OpenWebUI.",
            ),
            executor=executor,
        )

    assert exc_info.value.code == "subagent_cap_exceeded"
    assert len(callback_client.subagent_registrations) == 6
    assert len(callback_client.model_selections) == 5
    assert callback_client.events[-1]["event_type"] == "subagent.failed"
    assert callback_client.events[-1]["payload"]["error"]["code"] == "subagent_cap_exceeded"


@pytest.mark.asyncio
async def test_cancelled_plan_stops_subagent_loop_without_killing_terminal_processes() -> None:
    callback_client = RecordingOpenWebUIRuntimeClient()
    cancel_requested = False
    kill_calls: list[str] = []
    adapter = AgentScopeSubagentAdapter(
        run_id="run-team",
        runtime_session_id="rt-run-team",
        callback_client=callback_client,
        is_cancelled=lambda: cancel_requested,
        kill_terminal_process=lambda process_id: kill_calls.append(process_id),
    )

    async def executor(context: SubagentExecutionContext) -> dict:
        nonlocal cancel_requested
        cancel_requested = True
        return {"content": "first worker stopped the plan"}

    results = await adapter.run_subagent_plan(
        [
            SubagentSpec(name="worker-1", description="First.", task="Run first."),
            SubagentSpec(name="worker-2", description="Second.", task="Run second."),
        ],
        executor=executor,
    )

    assert [result.participant_id for result in results] == ["subagent:run-team:1"]
    assert [event["event_type"] for event in callback_client.events] == [
        "subagent.created",
        "subagent.completed",
        "run.cancelled",
    ]
    assert kill_calls == []
