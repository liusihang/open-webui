import os
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import pytest
from fastapi import HTTPException
from open_webui.agent import subagents as subagent_module
from open_webui.agent.model_catalog import (
    AgentModelCatalog,
    ModelSelectionNotAllowed,
)
from open_webui.agent.protocol import AgentEventType, AgentRunState
from open_webui.agent.subagents import (
    AgentSubagentCoordinator,
    SubagentBudgetExceeded,
    SubagentCapExceeded,
    SubagentCreateRequest,
    SubagentFailureRequest,
    SubagentModelSelectionRequest,
)
from open_webui.routers import agent_service
from open_webui.routers.agent_service import execute_agent_run_model_selection


@pytest.mark.asyncio
async def test_creating_subagent_persists_participant_and_event_attribution():
    store = FakeSubagentStore(
        budget={
            'team': {'max_subagents': 5, 'max_steps': 20, 'used_steps': 0},
            'subagent_default': {'max_steps': 4},
        }
    )
    coordinator = AgentSubagentCoordinator(
        store=store,
        model_catalog=_catalog_for_models([_model('research-model', 'Research Model')]),
    )

    result = await coordinator.create_subagent(
        _request(),
        SubagentCreateRequest(
            run_id='run-1',
            parent_participant_id='leader',
            participant_id='subagent-research',
            role='researcher',
            task='Find supporting papers',
            model_request={'requested_model_id': 'research-model'},
            requested_budget={'max_steps': 3},
            idempotency_key='subagent:leader:subagent-research:create',
        ),
    )

    assert result['participant'] == {
        'id': 'subagent-research',
        'parent_id': 'leader',
        'type': 'subagent',
        'role': 'researcher',
        'state': 'running',
        'task': 'Find supporting papers',
        'model_id': 'research-model',
        'budget': {'max_steps': 3, 'used_steps': 0, 'remaining_steps': 3},
    }
    assert store.runs['run-1'].participants == [
        {'id': 'leader', 'type': 'leader', 'state': 'running'},
        result['participant'],
    ]
    assert store.runs['run-1'].budget['team'] == {
        'max_subagents': 5,
        'max_steps': 20,
        'used_steps': 3,
        'remaining_steps': 17,
    }
    assert store.events == [
        {
            'run_id': 'run-1',
            'seq': 1,
            'event_type': 'model.selection.requested',
            'participant_id': 'subagent-research',
            'phase': 'running',
            'summary': 'Selecting model for subagent-research',
            'payload': {
                'parent_participant_id': 'leader',
                'participant_id': 'subagent-research',
                'requested_model_id': 'research-model',
                'fuzzy_request': None,
            },
        },
        {
            'run_id': 'run-1',
            'seq': 2,
            'event_type': 'model.selection.completed',
            'participant_id': 'subagent-research',
            'phase': 'running',
            'summary': 'Selected research-model for subagent-research',
            'payload': {
                'choices': [
                    {
                        'id': 'research-model',
                        'name': 'Research Model',
                        'object': 'model',
                        'owned_by': 'openai',
                        'meta': {'agent_selection': {}},
                    }
                ],
                'selected_model_id': 'research-model',
                'meta': {
                    'agent_selection': {
                        'reason': 'explicit_model_match',
                        'source_request': {'requested_model_id': 'research-model'},
                        'selected_model_id': 'research-model',
                    }
                },
                'warnings': [],
            },
        },
        {
            'run_id': 'run-1',
            'seq': 3,
            'event_type': 'subagent.created',
            'participant_id': 'subagent-research',
            'phase': 'running',
            'summary': 'Started subagent researcher',
            'payload': {
                'participant': result['participant'],
                'parent_participant_id': 'leader',
                'budget': {
                    'team': {'max_steps': 20, 'used_steps': 3, 'remaining_steps': 17},
                    'subagent': {'max_steps': 3, 'used_steps': 0, 'remaining_steps': 3},
                },
            },
        },
    ]


@pytest.mark.asyncio
async def test_more_than_five_subagents_is_rejected_by_default():
    store = FakeSubagentStore(
        participants=[
            {'id': 'leader', 'type': 'leader', 'state': 'running'},
            *[
                {
                    'id': f'subagent-{index}',
                    'parent_id': 'leader',
                    'type': 'subagent',
                    'state': 'completed',
                }
                for index in range(5)
            ],
        ],
        budget={'team': {'max_steps': 20, 'used_steps': 10}},
    )
    coordinator = AgentSubagentCoordinator(
        store=store,
        model_catalog=_catalog_for_models([_model('general-model', 'General Model')]),
    )

    with pytest.raises(SubagentCapExceeded, match='default cap of 5'):
        await coordinator.create_subagent(
            _request(),
            SubagentCreateRequest(
                run_id='run-1',
                parent_participant_id='leader',
                participant_id='subagent-over-cap',
                role='researcher',
                task='One too many',
                idempotency_key='subagent:leader:subagent-over-cap:create',
            ),
        )

    assert store.events == []
    assert [participant['id'] for participant in store.runs['run-1'].participants] == [
        'leader',
        'subagent-0',
        'subagent-1',
        'subagent-2',
        'subagent-3',
        'subagent-4',
    ]


@pytest.mark.asyncio
async def test_each_subagent_budget_is_capped_under_aggregate_team_budget():
    store = FakeSubagentStore(
        budget={
            'team': {'max_subagents': 5, 'max_steps': 6, 'used_steps': 4},
            'subagent_default': {'max_steps': 5},
        }
    )
    coordinator = AgentSubagentCoordinator(
        store=store,
        model_catalog=_catalog_for_models([_model('general-model', 'General Model')]),
    )

    result = await coordinator.create_subagent(
        _request(),
        SubagentCreateRequest(
            run_id='run-1',
            parent_participant_id='leader',
            participant_id='subagent-small',
            role='analyst',
            task='Use remaining budget only',
            requested_budget={'max_steps': 5},
            idempotency_key='subagent:leader:subagent-small:create',
        ),
    )

    assert result['participant']['budget'] == {
        'max_steps': 2,
        'used_steps': 0,
        'remaining_steps': 2,
    }
    assert store.runs['run-1'].budget['team'] == {
        'max_subagents': 5,
        'max_steps': 6,
        'used_steps': 6,
        'remaining_steps': 0,
    }

    with pytest.raises(SubagentBudgetExceeded, match='aggregate team budget'):
        await coordinator.create_subagent(
            _request(),
            SubagentCreateRequest(
                run_id='run-1',
                parent_participant_id='leader',
                participant_id='subagent-no-budget',
                role='analyst',
                task='No budget left',
                idempotency_key='subagent:leader:subagent-no-budget:create',
            ),
        )


@pytest.mark.asyncio
async def test_model_selection_callback_uses_w9a_catalog_and_rejects_unauthorized_explicit_model():
    store = FakeSubagentStore()
    coordinator = AgentSubagentCoordinator(
        store=store,
        model_catalog=_catalog_for_models(
            [
                _model('allowed-model', 'Allowed Model'),
                _model('private-model', 'Private Model'),
            ],
            reject_ids={'private-model'},
        ),
    )

    response = await coordinator.select_subagent_model(
        _request(),
        SubagentModelSelectionRequest(
            run_id='run-1',
            participant_id='subagent-a',
            selection_id='sel-1',
            fuzzy_request='general helper',
            source_request={'request': 'general helper'},
            idempotency_key='modelsel:subagent-a:sel-1:1',
        ),
    )

    assert response['selected_model_id'] == 'allowed-model'
    assert response['meta']['agent_selection']['source_request'] == {
        'request': 'general helper'
    }
    assert [event['event_type'] for event in store.events] == [
        'model.selection.requested',
        'model.selection.completed',
    ]

    with pytest.raises(ModelSelectionNotAllowed) as exc_info:
        await coordinator.select_subagent_model(
            _request(),
            SubagentModelSelectionRequest(
                run_id='run-1',
                participant_id='subagent-a',
                selection_id='sel-2',
                requested_model_id='private-model',
                source_request={'model': 'private-model'},
                idempotency_key='modelsel:subagent-a:sel-2:1',
            ),
        )

    assert exc_info.value.warnings == [
        {
            'code': 'explicit_model_not_allowed',
            'message': 'Requested model is not available for this run: private-model',
            'requested_model_id': 'private-model',
        }
    ]
    assert store.events[-1] == {
        'run_id': 'run-1',
        'seq': 3,
        'event_type': 'model.selection.requested',
        'participant_id': 'subagent-a',
        'phase': 'running',
        'summary': 'Selecting model for subagent-a',
        'payload': {
            'parent_participant_id': None,
            'participant_id': 'subagent-a',
            'requested_model_id': 'private-model',
            'fuzzy_request': None,
        },
    }


@pytest.mark.asyncio
async def test_model_selection_service_callback_requires_authority_and_delegates_to_coordinator():
    coordinator = FakeSubagentCoordinator()

    response = await execute_agent_run_model_selection(
        request=_service_request(),
        run_id='run-1',
        form_data=SubagentModelSelectionRequest(
            run_id='runtime-supplied-run',
            participant_id='subagent-a',
            selection_id='sel-1',
            fuzzy_request='research helper',
            idempotency_key=None,
        ),
        idempotency_key='modelsel:subagent-a:sel-1:1',
        authorization='Bearer service-secret',
        coordinator=coordinator,
    )

    assert response == {'selected_model_id': 'allowed-model'}
    assert coordinator.calls == [
        {
            'run_id': 'run-1',
            'participant_id': 'subagent-a',
            'selection_id': 'sel-1',
            'fuzzy_request': 'research helper',
            'idempotency_key': 'modelsel:subagent-a:sel-1:1',
        }
    ]


@pytest.mark.asyncio
async def test_subagent_service_callback_requires_authority_and_delegates_registration():
    coordinator = FakeSubagentRegistrationCoordinator()
    handler = getattr(agent_service, 'execute_agent_run_subagent_registration', None)
    assert handler is not None
    form_model = getattr(subagent_module, 'SubagentRegisterRequest', None)
    assert form_model is not None

    response = await handler(
        request=_service_request(),
        run_id='run-1',
        form_data=form_model(
            run_id='runtime-supplied-run',
            parent_participant_id='leader',
            participant_id='subagent-a',
            name='researcher',
            description='Researches facts.',
            task='Find facts',
            budget={'max_model_calls': 2},
            metadata={'team_cap': 5, 'single_level': True},
            idempotency_key=None,
        ),
        idempotency_key='subagent:leader:subagent-a:create',
        authorization='Bearer service-secret',
        coordinator=coordinator,
    )

    assert response == {
        'status': 'accepted',
        'participant_id': 'subagent-a',
        'team_cap': 5,
        'remaining_slots': 4,
        'warnings': [],
    }
    assert coordinator.calls == [
        {
            'run_id': 'run-1',
            'participant_id': 'subagent-a',
            'name': 'researcher',
            'budget': {'max_model_calls': 2},
            'metadata': {'team_cap': 5, 'single_level': True},
            'idempotency_key': 'subagent:leader:subagent-a:create',
        }
    ]


@pytest.mark.asyncio
async def test_model_selection_service_callback_maps_unauthorized_model_to_structured_error():
    coordinator = FakeRejectingSubagentCoordinator()

    with pytest.raises(HTTPException) as exc_info:
        await execute_agent_run_model_selection(
            request=_service_request(),
            run_id='run-1',
            form_data=SubagentModelSelectionRequest(
                run_id='run-1',
                participant_id='subagent-a',
                selection_id='sel-2',
                requested_model_id='private-model',
                idempotency_key='modelsel:subagent-a:sel-2:1',
            ),
            idempotency_key='modelsel:subagent-a:sel-2:1',
            authorization='Bearer service-secret',
            coordinator=coordinator,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        'code': 'model_selection_not_allowed',
        'message': 'Requested model is not available for this run: private-model',
        'warnings': [
            {
                'code': 'explicit_model_not_allowed',
                'message': 'Requested model is not available for this run: private-model',
                'requested_model_id': 'private-model',
            }
        ],
    }


@pytest.mark.asyncio
async def test_subagent_failure_event_does_not_fail_parent_run():
    store = FakeSubagentStore(
        participants=[
            {'id': 'leader', 'type': 'leader', 'state': 'running'},
            {
                'id': 'subagent-a',
                'parent_id': 'leader',
                'type': 'subagent',
                'state': 'running',
                'budget': {'max_steps': 2, 'used_steps': 1, 'remaining_steps': 1},
            },
        ]
    )
    coordinator = AgentSubagentCoordinator(store=store)

    event = await coordinator.record_subagent_failure(
        SubagentFailureRequest(
            run_id='run-1',
            participant_id='subagent-a',
            error={
                'code': 'tool_timeout',
                'message': 'Research helper timed out.',
                'retryable': True,
            },
            summary='Research helper timed out',
            idempotency_key='subagent:subagent-a:failed:1',
        )
    )

    assert event['event_type'] == AgentEventType.SUBAGENT_FAILED.value
    assert event['participant_id'] == 'subagent-a'
    assert store.runs['run-1'].participants[1]['state'] == 'failed'
    assert store.runs['run-1'].state == AgentRunState.RUNNING.value
    assert store.transitions == []


def test_subagent_event_fixture_shape_is_stable_for_runtime_and_frontend():
    coordinator = AgentSubagentCoordinator(store=FakeSubagentStore())

    fixture = coordinator.subagent_event_fixture(
        run_id='run-fixture',
        parent_participant_id='leader',
        participant_id='subagent-fixture',
    )

    assert fixture == [
        {
            'event_type': 'subagent.created',
            'participant_id': 'subagent-fixture',
            'phase': 'running',
            'summary': 'Started subagent researcher',
            'payload': {
                'participant': {
                    'id': 'subagent-fixture',
                    'parent_id': 'leader',
                    'type': 'subagent',
                    'role': 'researcher',
                    'state': 'running',
                    'task': 'Collect evidence',
                    'model_id': 'model-fixture',
                    'budget': {'max_steps': 3, 'used_steps': 0, 'remaining_steps': 3},
                },
                'parent_participant_id': 'leader',
                'budget': {
                    'team': {'max_steps': 10, 'used_steps': 3, 'remaining_steps': 7},
                    'subagent': {'max_steps': 3, 'used_steps': 0, 'remaining_steps': 3},
                },
            },
        },
        {
            'event_type': 'subagent.completed',
            'participant_id': 'subagent-fixture',
            'phase': 'running',
            'summary': 'Completed subagent researcher',
            'payload': {
                'participant_id': 'subagent-fixture',
                'state': 'completed',
                'result': {'content': 'Evidence collected.'},
            },
        },
        {
            'event_type': 'subagent.failed',
            'participant_id': 'subagent-fixture',
            'phase': 'running',
            'summary': 'Subagent researcher failed',
            'payload': {
                'participant_id': 'subagent-fixture',
                'state': 'failed',
                'error': {
                    'code': 'subagent_error',
                    'message': 'Subagent failed.',
                    'retryable': True,
                },
                'parent_run_state': 'running',
            },
        },
    ]


class FakeRun:
    def __init__(
        self,
        *,
        run_id='run-1',
        user_id='user-1',
        state=AgentRunState.RUNNING.value,
        participants=None,
        budget=None,
    ):
        self.id = run_id
        self.user_id = user_id
        self.state = state
        self.participants = participants or [
            {'id': 'leader', 'type': 'leader', 'state': 'running'}
        ]
        self.budget = budget or {'team': {'max_subagents': 5, 'max_steps': 20, 'used_steps': 0}}


class FakeSubagentStore:
    def __init__(self, *, participants=None, budget=None):
        self.runs = {
            'run-1': FakeRun(participants=participants, budget=budget),
        }
        self.events = []
        self.transitions = []

    async def get_run(self, run_id):
        return self.runs.get(run_id)

    async def update_participants_and_budget(self, run_id, *, participants, budget):
        run = self.runs[run_id]
        run.participants = participants
        run.budget = budget
        return run

    async def append_event(
        self,
        run_id,
        *,
        event_type,
        participant_id=None,
        phase=None,
        summary=None,
        payload=None,
    ):
        event = {
            'run_id': run_id,
            'seq': len(self.events) + 1,
            'event_type': event_type,
            'participant_id': participant_id,
            'phase': phase,
            'summary': summary,
            'payload': payload or {},
        }
        self.events.append(event)
        return event

    async def transition_state(self, *args, **kwargs):
        self.transitions.append({'args': args, 'kwargs': kwargs})
        raise AssertionError('subagent failure must not transition parent run')


class FakeSubagentCoordinator:
    def __init__(self):
        self.calls = []

    async def select_subagent_model(self, request, selection):
        self.calls.append(
            {
                'run_id': selection.run_id,
                'participant_id': selection.participant_id,
                'selection_id': selection.selection_id,
                'fuzzy_request': selection.fuzzy_request,
                'idempotency_key': selection.idempotency_key,
            }
        )
        return {'selected_model_id': 'allowed-model'}


class FakeSubagentRegistrationCoordinator:
    def __init__(self):
        self.calls = []

    async def register_subagent(self, request, creation):
        self.calls.append(
            {
                'run_id': creation.run_id,
                'participant_id': creation.participant_id,
                'name': creation.name,
                'budget': creation.budget,
                'metadata': creation.metadata,
                'idempotency_key': creation.idempotency_key,
            }
        )
        return {
            'status': 'accepted',
            'participant_id': creation.participant_id,
            'team_cap': 5,
            'remaining_slots': 4,
            'warnings': [],
        }


class FakeRejectingSubagentCoordinator:
    async def select_subagent_model(self, request, selection):
        warning = {
            'code': 'explicit_model_not_allowed',
            'message': 'Requested model is not available for this run: private-model',
            'requested_model_id': 'private-model',
        }
        raise ModelSelectionNotAllowed(warning['message'], warnings=[warning])


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def _service_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(AGENT_RUNTIME_SERVICE_TOKEN='service-secret')
            )
        )
    )


def _catalog_for_models(models, *, reject_ids=None):
    async def user_loader(user_id):
        return SimpleNamespace(id=user_id, role='user')

    async def model_loader(request, user):
        return models

    async def access_checker(user, model):
        if model['id'] in (reject_ids or set()):
            raise Exception('Model not found')

    return AgentModelCatalog(
        run_store=FakeSubagentStore(),
        user_loader=user_loader,
        model_loader=model_loader,
        model_access_checker=access_checker,
    )


def _model(model_id, name):
    return {
        'id': model_id,
        'name': name,
        'object': 'model',
        'owned_by': 'openai',
        'info': {'meta': {'agent_selection': {}}},
    }
