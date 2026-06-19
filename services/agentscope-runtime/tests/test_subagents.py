import asyncio

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


class NoAllowedModelChoicesClient(RecordingOpenWebUIRuntimeClient):
    async def select_model(self, **kwargs: object) -> dict:
        await super().select_model(**kwargs)
        raise RuntimeError(
            "OpenWebUI callback failed with status 403: "
            '{"detail":{"code":"model_selection_not_allowed",'
            '"message":"No models are available for this run.",'
            '"warnings":[{"code":"no_permission_valid_models",'
            '"message":"No models are available for this run."}]}}'
        )


class ExplicitModelRejectedClient(RecordingOpenWebUIRuntimeClient):
    async def select_model(self, **kwargs: object) -> dict:
        await super().select_model(**kwargs)
        raise RuntimeError(
            "OpenWebUI callback failed with status 403: "
            '{"detail":{"code":"model_selection_not_allowed",'
            '"message":"Requested model is not available for this run: private-model",'
            '"warnings":[{"code":"explicit_model_not_allowed",'
            '"message":"Requested model is not available for this run: private-model",'
            '"requested_model_id":"private-model"}]}}'
        )


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
async def test_adapter_falls_back_to_leader_model_when_no_allowed_model_choices() -> None:
    callback_client = NoAllowedModelChoicesClient()
    adapter = AgentScopeSubagentAdapter(
        run_id="run-team",
        runtime_session_id="rt-run-team",
        callback_client=callback_client,
        leader_model_id="model-leader",
    )

    async def executor(context: SubagentExecutionContext) -> dict:
        assert context.model_id == "model-leader"
        assert context.model_selection["meta"]["agent_selection"]["reason"] == (
            "leader_model_fallback_no_allowed_choices"
        )
        return {"content": "fallback subagent complete"}

    result = await adapter.run_subagent(
        parent_participant_id="leader",
        spec=SubagentSpec(
            name="researcher",
            description="Researches the topic.",
            task="Find the relevant facts.",
            fuzzy_model_request="small fast model",
            budget={"max_model_calls": 2},
        ),
        executor=executor,
    )

    assert result.status == "completed"
    assert result.content == "fallback subagent complete"
    created_event = callback_client.events[0]
    assert created_event["event_type"] == "subagent.created"
    assert created_event["payload"]["model_id"] == "model-leader"
    assert created_event["payload"]["model_selection"]["fallback"] is True
    assert created_event["payload"]["model_selection"]["warnings"][0]["code"] == (
        "no_permission_valid_models"
    )


@pytest.mark.asyncio
async def test_adapter_does_not_fallback_for_explicit_rejected_model() -> None:
    callback_client = ExplicitModelRejectedClient()
    adapter = AgentScopeSubagentAdapter(
        run_id="run-team",
        runtime_session_id="rt-run-team",
        callback_client=callback_client,
        leader_model_id="model-leader",
    )
    executed = False

    async def executor(context: SubagentExecutionContext) -> dict:
        nonlocal executed
        executed = True
        return {"content": "should not execute"}

    with pytest.raises(RuntimeError, match="explicit_model_not_allowed"):
        await adapter.run_subagent(
            parent_participant_id="leader",
            spec=SubagentSpec(
                name="researcher",
                description="Researches the topic.",
                task="Find the relevant facts.",
                requested_model_id="private-model",
            ),
            executor=executor,
        )

    assert executed is False
    assert callback_client.events == []


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
async def test_plan_runs_subagents_concurrently_up_to_team_cap() -> None:
    callback_client = RecordingOpenWebUIRuntimeClient()
    adapter = AgentScopeSubagentAdapter(
        run_id="run-team",
        runtime_session_id="rt-run-team",
        callback_client=callback_client,
        team_cap=5,
    )
    active = 0
    peak_active = 0
    started: list[str] = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def executor(context: SubagentExecutionContext) -> dict:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        started.append(context.participant_id)
        if len(started) == 5:
            all_started.set()
        await release.wait()
        active -= 1
        return {"content": f"{context.participant_id} complete"}

    plan_task = asyncio.create_task(
        adapter.run_subagent_plan(
            [
                SubagentSpec(name=f"worker-{index}", description="Worker.", task="Run slice.")
                for index in range(5)
            ],
            executor=executor,
        )
    )
    try:
        await asyncio.wait_for(all_started.wait(), timeout=0.5)
        assert peak_active == 5
        assert not plan_task.done()
    finally:
        release.set()

    results = await plan_task

    assert [result.participant_id for result in results] == [
        "subagent:run-team:1",
        "subagent:run-team:2",
        "subagent:run-team:3",
        "subagent:run-team:4",
        "subagent:run-team:5",
    ]
    assert len(callback_client.subagent_registrations) == 5
    assert len(callback_client.model_selections) == 5


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
        max_concurrency=1,
    )

    assert [result.participant_id for result in results] == ["subagent:run-team:1"]
    assert [event["event_type"] for event in callback_client.events] == [
        "subagent.created",
        "subagent.completed",
        "run.cancelled",
    ]
    assert kill_calls == []
