from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from open_webui.agent.model_catalog import (
    AgentModelCatalog,
    ModelSelectionRequest,
)
from open_webui.agent.protocol import AgentEventType, AgentRunState
from open_webui.internal.db import get_async_db_context
from open_webui.models.agent_runs import AgentRun, AgentRunModel, AgentRuns

DEFAULT_MAX_SUBAGENTS = 5


class SubagentError(ValueError):
    code = 'subagent_error'


class SubagentRunRejected(SubagentError):
    code = 'subagent_run_rejected'


class SubagentCapExceeded(SubagentError):
    code = 'subagent_cap_exceeded'


class SubagentBudgetExceeded(SubagentError):
    code = 'subagent_budget_exceeded'


class SubagentModelSelectionRequest(ModelSelectionRequest):
    parent_participant_id: str | None = None


class SubagentCreateRequest(BaseModel):
    model_config = ConfigDict(extra='allow')

    run_id: str
    parent_participant_id: str = 'leader'
    participant_id: str
    role: str
    task: str
    model_request: dict[str, Any] = Field(default_factory=dict)
    requested_budget: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class SubagentFailureRequest(BaseModel):
    model_config = ConfigDict(extra='allow')

    run_id: str
    participant_id: str
    error: dict[str, Any]
    summary: str | None = None
    idempotency_key: str | None = None


class AgentRunSubagentStore:
    def __init__(self, run_store=AgentRuns) -> None:
        self.run_store = run_store

    async def get_run(self, run_id: str):
        return await self.run_store.get_run(run_id)

    async def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        participant_id: str | None = None,
        phase: str | None = None,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
    ):
        return await self.run_store.append_event(
            run_id,
            event_type=event_type,
            participant_id=participant_id,
            phase=phase,
            summary=summary,
            payload=payload,
        )

    async def update_participants_and_budget(
        self,
        run_id: str,
        *,
        participants: list[dict[str, Any]],
        budget: dict[str, Any],
    ) -> AgentRunModel:
        async with get_async_db_context(None) as db:
            row = await db.get(AgentRun, run_id)
            if row is None:
                raise SubagentRunRejected(f'Agent run not found: {run_id}')

            row.participants = participants
            row.budget = budget
            row.updated_at = int(time.time_ns())
            await db.commit()
            await db.refresh(row)
            return AgentRunModel.model_validate(row)


class AgentSubagentCoordinator:
    def __init__(
        self,
        *,
        store: Any | None = None,
        model_catalog: AgentModelCatalog | None = None,
        default_max_subagents: int = DEFAULT_MAX_SUBAGENTS,
    ) -> None:
        self.store = store or AgentRunSubagentStore()
        self.model_catalog = model_catalog or AgentModelCatalog(run_store=self.store)
        self.default_max_subagents = default_max_subagents

    async def create_subagent(
        self,
        request,
        creation: SubagentCreateRequest,
    ) -> dict[str, Any]:
        run = await self._running_run(creation.run_id)
        participants = _participants(run)
        self._ensure_under_subagent_cap(run, participants)

        budget = _budget(run)
        subagent_budget, budget = self._allocate_subagent_budget(
            budget,
            creation.requested_budget,
        )

        model_selection = await self.select_subagent_model(
            request,
            SubagentModelSelectionRequest(
                run_id=creation.run_id,
                parent_participant_id=creation.parent_participant_id,
                participant_id=creation.participant_id,
                selection_id=creation.model_request.get('selection_id')
                or f'{creation.participant_id}:model',
                requested_model_id=creation.model_request.get('requested_model_id'),
                fuzzy_request=creation.model_request.get('fuzzy_request')
                or creation.model_request.get('request'),
                source_request=_model_source_request(creation.model_request),
                idempotency_key=creation.idempotency_key,
            ),
        )

        participant = {
            'id': creation.participant_id,
            'parent_id': creation.parent_participant_id,
            'type': 'subagent',
            'role': creation.role,
            'state': AgentRunState.RUNNING.value,
            'task': creation.task,
            'model_id': model_selection['selected_model_id'],
            'budget': subagent_budget,
        }
        participants.append(participant)
        await self.store.update_participants_and_budget(
            creation.run_id,
            participants=participants,
            budget=budget,
        )

        event = await self.store.append_event(
            creation.run_id,
            event_type=AgentEventType.SUBAGENT_CREATED.value,
            participant_id=creation.participant_id,
            phase=AgentRunState.RUNNING.value,
            summary=f'Started subagent {creation.role}',
            payload={
                'participant': participant,
                'parent_participant_id': creation.parent_participant_id,
                'budget': {
                    'team': _team_event_budget(budget),
                    'subagent': subagent_budget,
                },
            },
        )

        return {
            'participant': participant,
            'event': event,
            'model_selection': model_selection,
        }

    async def select_subagent_model(
        self,
        request,
        selection: SubagentModelSelectionRequest,
    ) -> dict[str, Any]:
        await self._running_run(selection.run_id)
        await self.store.append_event(
            selection.run_id,
            event_type=AgentEventType.MODEL_SELECTION_REQUESTED.value,
            participant_id=selection.participant_id,
            phase=AgentRunState.RUNNING.value,
            summary=f'Selecting model for {selection.participant_id}',
            payload={
                'parent_participant_id': selection.parent_participant_id,
                'participant_id': selection.participant_id,
                'requested_model_id': selection.requested_model_id,
                'fuzzy_request': selection.fuzzy_request,
            },
        )

        response = await self.model_catalog.select_model(
            request,
            ModelSelectionRequest(
                run_id=selection.run_id,
                participant_id=selection.participant_id,
                selection_id=selection.selection_id,
                requested_model_id=selection.requested_model_id,
                fuzzy_request=selection.fuzzy_request,
                source_request=selection.source_request,
                idempotency_key=selection.idempotency_key,
            ),
        )

        await self.store.append_event(
            selection.run_id,
            event_type=AgentEventType.MODEL_SELECTION_COMPLETED.value,
            participant_id=selection.participant_id,
            phase=AgentRunState.RUNNING.value,
            summary=f"Selected {response['selected_model_id']} for {selection.participant_id}",
            payload=response,
        )
        return response

    async def record_subagent_failure(
        self,
        failure: SubagentFailureRequest,
    ):
        run = await self._running_run(failure.run_id)
        participants = _participants(run)
        updated_participants = [
            _failed_participant(participant, failure)
            if participant.get('id') == failure.participant_id
            else participant
            for participant in participants
        ]
        await self.store.update_participants_and_budget(
            failure.run_id,
            participants=updated_participants,
            budget=_budget(run),
        )

        return await self.store.append_event(
            failure.run_id,
            event_type=AgentEventType.SUBAGENT_FAILED.value,
            participant_id=failure.participant_id,
            phase=AgentRunState.RUNNING.value,
            summary=failure.summary or f'Subagent {failure.participant_id} failed',
            payload={
                'participant_id': failure.participant_id,
                'state': AgentRunState.FAILED.value,
                'error': failure.error,
                'parent_run_state': AgentRunState.RUNNING.value,
            },
        )

    def subagent_event_fixture(
        self,
        *,
        run_id: str,
        parent_participant_id: str,
        participant_id: str,
    ) -> list[dict[str, Any]]:
        del run_id
        participant = {
            'id': participant_id,
            'parent_id': parent_participant_id,
            'type': 'subagent',
            'role': 'researcher',
            'state': AgentRunState.RUNNING.value,
            'task': 'Collect evidence',
            'model_id': 'model-fixture',
            'budget': {'max_steps': 3, 'used_steps': 0, 'remaining_steps': 3},
        }
        return [
            {
                'event_type': AgentEventType.SUBAGENT_CREATED.value,
                'participant_id': participant_id,
                'phase': AgentRunState.RUNNING.value,
                'summary': 'Started subagent researcher',
                'payload': {
                    'participant': participant,
                    'parent_participant_id': parent_participant_id,
                    'budget': {
                        'team': {'max_steps': 10, 'used_steps': 3, 'remaining_steps': 7},
                        'subagent': participant['budget'],
                    },
                },
            },
            {
                'event_type': AgentEventType.SUBAGENT_COMPLETED.value,
                'participant_id': participant_id,
                'phase': AgentRunState.RUNNING.value,
                'summary': 'Completed subagent researcher',
                'payload': {
                    'participant_id': participant_id,
                    'state': AgentRunState.COMPLETED.value,
                    'result': {'content': 'Evidence collected.'},
                },
            },
            {
                'event_type': AgentEventType.SUBAGENT_FAILED.value,
                'participant_id': participant_id,
                'phase': AgentRunState.RUNNING.value,
                'summary': 'Subagent researcher failed',
                'payload': {
                    'participant_id': participant_id,
                    'state': AgentRunState.FAILED.value,
                    'error': {
                        'code': 'subagent_error',
                        'message': 'Subagent failed.',
                        'retryable': True,
                    },
                    'parent_run_state': AgentRunState.RUNNING.value,
                },
            },
        ]

    async def _running_run(self, run_id: str):
        run = await self.store.get_run(run_id)
        if run is None:
            raise SubagentRunRejected(f'Agent run not found: {run_id}')
        if run.state != AgentRunState.RUNNING.value:
            raise SubagentRunRejected(
                f'Agent run {run_id} cannot manage subagents while {run.state}'
            )
        return run

    def _ensure_under_subagent_cap(self, run, participants: list[dict[str, Any]]) -> None:
        max_subagents = _team_budget_value(
            _budget(run),
            'max_subagents',
            self.default_max_subagents,
        )
        current_subagents = sum(
            1 for participant in participants if participant.get('type') == 'subagent'
        )
        if current_subagents >= max_subagents:
            raise SubagentCapExceeded(
                f'Agent run already has {current_subagents} subagents; '
                f'default cap of {max_subagents} would be exceeded'
            )

    def _allocate_subagent_budget(
        self,
        budget: dict[str, Any],
        requested_budget: Mapping[str, Any],
    ) -> tuple[dict[str, int], dict[str, Any]]:
        team = dict(budget.get('team') or {})
        team_max_steps = _optional_int(team.get('max_steps'))
        team_used_steps = _optional_int(team.get('used_steps'), default=0) or 0
        if team_max_steps is not None:
            remaining_team_steps = max(team_max_steps - team_used_steps, 0)
            if remaining_team_steps <= 0:
                raise SubagentBudgetExceeded(
                    'Cannot create subagent because aggregate team budget is exhausted'
                )
        else:
            remaining_team_steps = None

        default_budget = dict(budget.get('subagent_default') or {})
        requested_steps = _optional_int(requested_budget.get('max_steps'))
        default_steps = _optional_int(default_budget.get('max_steps'), default=1) or 1
        desired_steps = requested_steps if requested_steps is not None else default_steps
        if desired_steps <= 0:
            raise SubagentBudgetExceeded('Subagent budget must be greater than zero')

        allocated_steps = desired_steps
        if remaining_team_steps is not None:
            allocated_steps = min(desired_steps, remaining_team_steps)

        subagent_budget = {
            'max_steps': allocated_steps,
            'used_steps': 0,
            'remaining_steps': allocated_steps,
        }
        team['used_steps'] = team_used_steps + allocated_steps
        if team_max_steps is not None:
            team['remaining_steps'] = max(team_max_steps - team['used_steps'], 0)
        if 'max_subagents' not in team:
            team['max_subagents'] = self.default_max_subagents
        budget = {**budget, 'team': team}
        return subagent_budget, budget


def _participants(run) -> list[dict[str, Any]]:
    return [dict(participant) for participant in (getattr(run, 'participants', None) or [])]


def _budget(run) -> dict[str, Any]:
    return dict(getattr(run, 'budget', None) or {})


def _team_budget_value(
    budget: dict[str, Any],
    key: str,
    default: int,
) -> int:
    value = (budget.get('team') or {}).get(key, default)
    parsed = _optional_int(value, default=default)
    return parsed if parsed is not None else default


def _optional_int(value: Any, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _model_source_request(model_request: dict[str, Any]) -> dict[str, Any]:
    if source_request := model_request.get('source_request'):
        return dict(source_request)
    if requested_model_id := model_request.get('requested_model_id'):
        return {'requested_model_id': requested_model_id}
    if fuzzy_request := model_request.get('fuzzy_request') or model_request.get('request'):
        return {'request': fuzzy_request}
    return {}


def _team_event_budget(budget: dict[str, Any]) -> dict[str, int]:
    team = budget.get('team') or {}
    event_budget = {
        'max_steps': _optional_int(team.get('max_steps'), default=0) or 0,
        'used_steps': _optional_int(team.get('used_steps'), default=0) or 0,
    }
    event_budget['remaining_steps'] = _optional_int(
        team.get('remaining_steps'),
        default=max(event_budget['max_steps'] - event_budget['used_steps'], 0),
    ) or 0
    return event_budget


def _failed_participant(
    participant: dict[str, Any],
    failure: SubagentFailureRequest,
) -> dict[str, Any]:
    updated = dict(participant)
    updated['state'] = AgentRunState.FAILED.value
    updated['error'] = failure.error
    return updated
