import asyncio
import os

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
        wait_for_response=False,
    )

    assert response == {'status': 'requested', 'user_input_id': 'input-1'}
    assert store.state['run-1'] == 'waiting_user_input'
    assert [event['event_type'] for event in store.events] == ['user_input.requested']
    assert store.events[0]['phase'] == 'waiting_user_input'
    assert store.events[0]['payload']['message'] == 'Which file should I update?'
    assert store.events[0]['payload']['requested_schema']['properties']['file']['title'] == 'Target file'


@pytest.mark.asyncio
async def test_user_input_request_waits_for_cross_coordinator_completion_then_returns_content():
    store = FakeUserInputStore()
    request_coordinator = AgentUserInputCoordinator(store)
    completion_coordinator = AgentUserInputCoordinator(store)

    pending_request = asyncio.create_task(
        request_coordinator.request_user_input(
            _user_input_request(user_input_id='input-cross'),
            wait_for_response=True,
            response_timeout_seconds=1,
        )
    )
    try:
        for _ in range(20):
            if any(event['event_type'] == 'user_input.requested' for event in store.events):
                break
            await asyncio.sleep(0.01)

        assert pending_request.done() is False
        assert store.state['run-1'] == 'waiting_user_input'

        submitted = await completion_coordinator.complete(
            UserInputCompletionRequest(
                run_id='run-1',
                user_input_id='input-cross',
                status='accepted',
                content={'file': 'README.md'},
                idempotency_key='user-input-result:input-cross:1',
            )
        )
        result = await asyncio.wait_for(pending_request, timeout=1)
    finally:
        if not pending_request.done():
            pending_request.cancel()

    assert submitted == {
        'status': 'accepted',
        'content': {'file': 'README.md'},
        'user_input_id': 'input-cross',
    }
    assert result == submitted
    assert store.state['run-1'] == 'running'
    assert [event['event_type'] for event in store.events] == [
        'user_input.requested',
        'user_input.completed',
    ]
    assert store.events[-1]['payload']['status'] == 'accepted'
    assert store.events[-1]['payload']['content'] == {'file': 'README.md'}


@pytest.mark.asyncio
async def test_duplicate_user_input_request_waits_for_response_instead_of_returning_requested():
    store = FakeUserInputStore()
    first_coordinator = AgentUserInputCoordinator(store)
    retry_coordinator = AgentUserInputCoordinator(store)
    completion_coordinator = AgentUserInputCoordinator(store)

    first = await first_coordinator.request_user_input(
        _user_input_request(user_input_id='input-retry'),
        wait_for_response=False,
    )
    assert first == {'status': 'requested', 'user_input_id': 'input-retry'}

    pending_retry = asyncio.create_task(
        retry_coordinator.request_user_input(
            _user_input_request(user_input_id='input-retry'),
            wait_for_response=True,
            response_timeout_seconds=1,
        )
    )
    try:
        await asyncio.sleep(0)
        assert pending_retry.done() is False

        submitted = await completion_coordinator.complete(
            UserInputCompletionRequest(
                run_id='run-1',
                user_input_id='input-retry',
                status='accepted',
                content={'file': 'README.md'},
                idempotency_key='user-input-result:input-retry:1',
            )
        )
        result = await asyncio.wait_for(pending_retry, timeout=1)
    finally:
        if not pending_retry.done():
            pending_retry.cancel()

    assert result == submitted
    assert [event['event_type'] for event in store.events] == [
        'user_input.requested',
        'user_input.completed',
    ]


@pytest.mark.asyncio
async def test_user_input_completion_replays_idempotently_and_rejects_conflicts():
    store = FakeUserInputStore()
    coordinator = AgentUserInputCoordinator(store)
    await coordinator.request_user_input(_user_input_request(), wait_for_response=False)

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
    assert [event['event_type'] for event in store.events] == [
        'user_input.requested',
        'user_input.completed',
    ]

    with pytest.raises(AgentRunOperationConflict):
        await coordinator.complete(
            completion.model_copy(update={'content': {'file': 'CHANGELOG.md'}})
        )
