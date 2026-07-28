import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import pytest
from open_webui.agent.user_input import (
    AgentUserInputCoordinator,
    UserInputCompletionRequest,
    UserInputRequest,
)
from open_webui.models.agent_runs import AgentRunOperationConflict


class FakeUserInputStore:
    def __init__(self):
        self.state = {'run-1': 'running'}
        self.events = []
        self.claims = {}
        self.claim_count = 0
        self.transitions = []
        self.decision_executions = {}

    async def transition_state(self, run_id, *, from_states, to_state, reason, payload=None):
        current = self.state[run_id]
        if current not in from_states:
            raise AssertionError(f'cannot transition from {current} via {from_states}')
        self.transitions.append(
            {
                'run_id': run_id,
                'from_states': from_states,
                'to_state': to_state,
                'reason': reason,
                'payload': payload,
            }
        )
        self.state[run_id] = to_state
        return {'id': run_id, 'state': to_state}

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
        target_state = {
            'user_input.requested': 'waiting_user_input',
            'user_input.completed': 'running',
            'user_input.declined': 'running',
            'user_input.cancelled': 'running',
            'user_input.expired': 'running',
        }.get(event_type)
        if target_state is not None:
            self.state[run_id] = target_state
        event = {
            'run_id': run_id,
            'seq': len(self.events) + 1,
            'event_type': event_type,
            'participant_id': participant_id,
            'phase': phase,
            'summary': summary,
            'payload': payload or {},
            'created_at': len(self.events) + 1,
        }
        self.events.append(event)
        return event

    async def list_events_after(self, run_id, after_seq=0):
        return [
            event
            for event in self.events
            if event['run_id'] == run_id and event['seq'] > after_seq
        ]

    async def claim_operation(
        self,
        run_id,
        *,
        operation_type,
        idempotency_key,
        request_hash,
    ):
        from open_webui.models.agent_runs import (
            AgentRunOperationClaim,
            AgentRunOperationModel,
        )

        key = (run_id, operation_type, idempotency_key)
        existing = self.claims.get(key)
        if existing:
            if existing.request_hash != request_hash:
                raise AgentRunOperationConflict(
                    'idempotency key was reused with a different request hash'
                )
            return AgentRunOperationClaim(operation=existing, created=False)

        self.claim_count += 1
        operation = AgentRunOperationModel(
            id=f'op-{self.claim_count}',
            run_id=run_id,
            operation_type=operation_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status='in_progress',
            created_at=1,
            updated_at=1,
        )
        self.claims[key] = operation
        return AgentRunOperationClaim(operation=operation, created=True)

    async def finish_operation_success(self, operation_id, response):
        for key, operation in list(self.claims.items()):
            if operation.id == operation_id:
                updated = operation.model_copy(
                    update={'status': 'succeeded', 'response': response}
                )
                self.claims[key] = updated
                return updated
        raise AssertionError(f'unknown operation {operation_id}')

    async def finish_operation_error(self, operation_id, error):
        for key, operation in list(self.claims.items()):
            if operation.id == operation_id:
                updated = operation.model_copy(update={'status': 'failed', 'error': error})
                self.claims[key] = updated
                return updated
        raise AssertionError(f'unknown operation {operation_id}')

    async def record_decision_execution(
        self,
        run_id,
        *,
        resource_type,
        resource_id,
        decision,
        payload,
        operation_type,
        idempotency_key,
        request_hash,
    ):
        from open_webui.models.agent_runs import AgentRunDecisionConflict

        for event in self.events:
            if (
                event['event_type'] in {
                    'user_input.completed',
                    'user_input.declined',
                    'user_input.cancelled',
                    'user_input.expired',
                }
                and event['payload'].get('user_input_id') == resource_id
            ):
                return SimpleNamespace(
                    execution=None,
                    created=False,
                    historical_event=SimpleNamespace(**event),
                )
        claim = await self.claim_operation(
            run_id,
            operation_type=operation_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        existing = self.decision_executions.get((run_id, resource_type, resource_id))
        if existing is not None and (
            existing.decision != decision or existing.payload != payload
        ):
            raise AgentRunDecisionConflict('user input already has a different decision')
        created = existing is None
        execution = existing or SimpleNamespace(
            id=f'execution-{len(self.decision_executions) + 1}',
            decision=decision,
            payload=payload,
            status='pending',
        )
        self.decision_executions[(run_id, resource_type, resource_id)] = execution
        await self.finish_operation_success(
            claim.operation.id,
            {'execution_id': execution.id, 'execution_status': execution.status},
        )
        return SimpleNamespace(
            execution=execution,
            created=created,
            historical_event=None,
        )


def _user_input_request(**overrides):
    data = {
        'run_id': 'run-1',
        'participant_id': 'leader',
        'user_input_id': 'input-1',
        'tool_call_id': 'tool-call-1',
        'message': 'Which file should I update?',
        'requested_schema': {
            'type': 'object',
            'properties': {'file': {'type': 'string', 'title': 'Target file'}},
            'required': ['file'],
        },
        'timeout_seconds': 300,
        'allow_cancel': True,
        'checkpoint_version': 9,
        'idempotency_key': 'user-input:leader:tool-call-1:1',
    }
    data.update(overrides)
    return UserInputRequest(**data)


@pytest.mark.asyncio
async def test_user_input_request_marks_run_waiting_user_input_and_emits_requested_event():
    store = FakeUserInputStore()
    coordinator = AgentUserInputCoordinator(store)

    response = await coordinator.request_user_input(
        _user_input_request(),
    )

    assert response == {'status': 'requested', 'user_input_id': 'input-1'}
    assert store.state['run-1'] == 'waiting_user_input'
    assert [event['event_type'] for event in store.events] == ['user_input.requested']
    assert store.events[0]['phase'] == 'waiting_user_input'
    assert store.events[0]['payload']['message'] == 'Which file should I update?'
    assert store.events[0]['payload']['checkpoint_version'] == 9
    assert store.events[0]['payload']['requested_schema']['properties']['file']['title'] == 'Target file'


@pytest.mark.asyncio
async def test_user_input_request_returns_immediately_and_completion_records_outbox():
    store = FakeUserInputStore()
    request_coordinator = AgentUserInputCoordinator(store)
    completion_coordinator = AgentUserInputCoordinator(store)

    requested = await request_coordinator.request_user_input(
        _user_input_request(user_input_id='input-cross')
    )
    submitted = await completion_coordinator.complete(
        UserInputCompletionRequest(
            run_id='run-1',
            user_input_id='input-cross',
            status='accepted',
            content={'file': 'README.md'},
            idempotency_key='user-input-result:input-cross:1',
        )
    )

    assert requested == {'status': 'requested', 'user_input_id': 'input-cross'}
    assert submitted['status'] == 'accepted'
    assert submitted['execution_status'] == 'pending'
    assert store.state['run-1'] == 'waiting_user_input'
    assert [event['event_type'] for event in store.events] == ['user_input.requested']
    assert store.transitions == []


@pytest.mark.asyncio
async def test_duplicate_user_input_request_returns_same_requested_response_immediately():
    store = FakeUserInputStore()
    first_coordinator = AgentUserInputCoordinator(store)
    retry_coordinator = AgentUserInputCoordinator(store)
    completion_coordinator = AgentUserInputCoordinator(store)

    first = await first_coordinator.request_user_input(
        _user_input_request(user_input_id='input-retry'),
    )
    assert first == {'status': 'requested', 'user_input_id': 'input-retry'}

    retry = await retry_coordinator.request_user_input(
        _user_input_request(user_input_id='input-retry')
    )
    submitted = await completion_coordinator.complete(
        UserInputCompletionRequest(
            run_id='run-1',
            user_input_id='input-retry',
            status='accepted',
            content={'file': 'README.md'},
            idempotency_key='user-input-result:input-retry:1',
        )
    )

    assert retry == first
    assert submitted['execution_status'] == 'pending'
    assert [event['event_type'] for event in store.events] == ['user_input.requested']


@pytest.mark.asyncio
async def test_user_input_completion_replays_idempotently_and_rejects_conflicts():
    store = FakeUserInputStore()
    coordinator = AgentUserInputCoordinator(store)
    await coordinator.request_user_input(_user_input_request())

    completion = UserInputCompletionRequest(
        run_id='run-1',
        user_input_id='input-1',
        status='accepted',
        content={'file': 'README.md'},
        idempotency_key='user-input-result:input-1:1',
    )
    first = await coordinator.complete(completion)
    duplicate = await coordinator.complete(completion)

    assert first == duplicate
    assert first['execution_status'] == 'pending'
    assert [event['event_type'] for event in store.events] == ['user_input.requested']

    with pytest.raises(AgentRunOperationConflict):
        await coordinator.complete(
            completion.model_copy(update={'content': {'file': 'CHANGELOG.md'}})
        )


@pytest.mark.asyncio
async def test_concurrent_user_input_completion_does_not_take_over_live_operation_owner():
    store = FakeUserInputStore()
    coordinator = AgentUserInputCoordinator(store)
    await coordinator.request_user_input(_user_input_request())
    completion = UserInputCompletionRequest(
        run_id='run-1',
        user_input_id='input-1',
        status='accepted',
        content={'file': 'README.md'},
        idempotency_key='user-input-result:input-1:concurrent',
    )

    owner = asyncio.create_task(coordinator.complete(completion))
    contender = asyncio.create_task(
        coordinator.complete(
            completion.model_copy(
                update={'idempotency_key': 'user-input-result:input-1:second'}
            )
        )
    )
    owner_result, contender_result = await asyncio.gather(
        owner,
        contender,
        return_exceptions=True,
    )

    assert owner_result['execution_id'] == contender_result['execution_id']
    assert [event['event_type'] for event in store.events] == ['user_input.requested']


@pytest.mark.asyncio
async def test_user_input_completion_recovers_in_progress_operation_from_event():
    store = FakeUserInputStore()
    coordinator = AgentUserInputCoordinator(store)
    await coordinator.request_user_input(_user_input_request())
    completion = UserInputCompletionRequest(
        run_id='run-1',
        user_input_id='input-1',
        status='accepted',
        content={'file': 'README.md'},
        idempotency_key='user-input-result:input-1:recover',
    )
    expected = await coordinator.complete(completion)
    operation_key = next(
        key for key in store.claims if key[1] == 'user_input.result'
    )
    operation = store.claims[operation_key]
    store.claims[operation_key] = operation.model_copy(
        update={'status': 'in_progress', 'response': None}
    )

    recovered = await AgentUserInputCoordinator(store).complete(completion)

    assert recovered == expected
    assert store.claims[operation_key].status == 'succeeded'
    assert [event['event_type'] for event in store.events] == ['user_input.requested']
