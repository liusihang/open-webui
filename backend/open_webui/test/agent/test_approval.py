import os
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import pytest
from open_webui.agent.approval import (
    AgentApprovalCoordinator,
    ApprovalNotFound,
    ApprovalDecisionRequest,
)
from open_webui.agent.destructive import classify_destructive_tool_call
from open_webui.agent.tool_authority import (
    AgentToolAuthority,
    ToolCallRequest,
    build_tool_access_envelope,
)
from open_webui.models.agent_runs import AgentRunOperationConflict
from open_webui.routers.agent_service import execute_agent_run_tool_call


def _service_request(authority=None):
    registry = getattr(authority, 'registry', None)
    operation_store = getattr(authority, 'operation_store', None)
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(AGENT_RUNTIME_SERVICE_TOKEN='service-secret'),
                AGENT_EVENT_STORE=operation_store,
                AGENT_TOOL_AUTHORITY=authority,
                AGENT_TOOL_REGISTRIES={'run-1': registry} if registry is not None else {},
            )
        )
    )


class FakeApprovalStore:
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
        }
        self.events.append(event)
        return event

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


def test_destructive_classifier_bypasses_read_only_and_flags_delete_overwrite_actions():
    read_assessment = classify_destructive_tool_call(
        tool_name='read_file',
        tool_id='terminal:main',
        tool_type='terminal',
        arguments={'path': '/workspace/report.txt'},
    )
    delete_assessment = classify_destructive_tool_call(
        tool_name='delete_entry',
        tool_id='terminal:main',
        tool_type='terminal',
        arguments={'path': '/workspace/report.txt'},
    )
    overwrite_assessment = classify_destructive_tool_call(
        tool_name='write_file',
        tool_id='terminal:main',
        tool_type='terminal',
        arguments={'path': '/workspace/report.txt', 'content': 'replacement'},
    )
    command_assessment = classify_destructive_tool_call(
        tool_name='run_command',
        tool_id='terminal:main',
        tool_type='terminal',
        arguments={'command': 'rm -rf /workspace/agent-runs/run-1/outputs'},
    )

    assert read_assessment.requires_approval is False
    assert delete_assessment.requires_approval is True
    assert delete_assessment.category == 'delete'
    assert overwrite_assessment.requires_approval is True
    assert overwrite_assessment.category == 'overwrite'
    assert command_assessment.requires_approval is True
    assert command_assessment.category == 'delete'
    assert 'rm' in command_assessment.matched


@pytest.mark.asyncio
async def test_read_only_tool_call_endpoint_bypasses_approval_and_executes_once():
    calls = []

    async def read_file(path: str):
        calls.append(path)
        return f'contents of {path}'

    _envelope, registry = build_tool_access_envelope(
        {
            'read_file': {
                'tool_id': 'terminal:main',
                'callable': read_file,
                'spec': {'name': 'read_file', 'parameters': {'type': 'object'}},
                'type': 'terminal',
            }
        }
    )
    store = FakeApprovalStore()
    authority = AgentToolAuthority(operation_store=store, registry=registry)
    coordinator = AgentApprovalCoordinator(store)

    result = await execute_agent_run_tool_call(
        request=_service_request(authority),
        run_id='run-1',
        form_data=ToolCallRequest(
            run_id='run-1',
            participant_id='leader',
            tool_call_id='call-1',
            tool_id='tool:terminal:main:read_file',
            arguments={'path': '/workspace/report.txt'},
            idempotency_key='tool:leader:call-1:1',
        ),
        idempotency_key='tool:leader:call-1:1',
        authorization='Bearer service-secret',
        approval_coordinator=coordinator,
    )

    assert result['status'] == 'success'
    assert result['content'] == 'contents of /workspace/report.txt'
    assert calls == ['/workspace/report.txt']
    assert store.state['run-1'] == 'running'
    assert store.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'target_path',
    [
        '/workspace/agent-runs/run-1/outputs/report.md',
        '/workspace/agent-runs/run-1/tmp/scratch.json',
    ],
)
async def test_run_command_creating_current_run_artifact_bypasses_approval(target_path):
    calls = []

    async def run_command(command: str):
        calls.append(command)
        return {'exit_code': 0, 'stdout': '', 'stderr': ''}

    _envelope, registry = build_tool_access_envelope(
        {
            'run_command': {
                'tool_id': 'terminal:main',
                'callable': run_command,
                'spec': {'name': 'run_command', 'parameters': {'type': 'object'}},
                'type': 'terminal',
            }
        }
    )
    store = FakeApprovalStore()
    authority = AgentToolAuthority(operation_store=store, registry=registry)
    coordinator = AgentApprovalCoordinator(store)
    command = f'printf "artifact" > {target_path}'

    result = await execute_agent_run_tool_call(
        request=_service_request(authority),
        run_id='run-1',
        form_data=ToolCallRequest(
            run_id='run-1',
            participant_id='leader',
            tool_call_id='call-1',
            tool_id='tool:terminal:main:run_command',
            arguments={'command': command},
            idempotency_key='tool:leader:call-1:1',
        ),
        idempotency_key='tool:leader:call-1:1',
        authorization='Bearer service-secret',
        approval_coordinator=coordinator,
    )

    assert result['status'] == 'success'
    assert calls == [command]
    assert store.state['run-1'] == 'running'
    assert store.events == []


@pytest.mark.asyncio
async def test_run_command_write_outside_current_run_artifact_dirs_requires_approval():
    calls = []

    async def run_command(command: str):
        calls.append(command)
        return {'exit_code': 0, 'stdout': '', 'stderr': ''}

    _envelope, registry = build_tool_access_envelope(
        {
            'run_command': {
                'tool_id': 'terminal:main',
                'callable': run_command,
                'spec': {'name': 'run_command', 'parameters': {'type': 'object'}},
                'type': 'terminal',
            }
        }
    )
    store = FakeApprovalStore()
    authority = AgentToolAuthority(operation_store=store, registry=registry)
    coordinator = AgentApprovalCoordinator(store)

    approval_required = await execute_agent_run_tool_call(
        request=_service_request(authority),
        run_id='run-1',
        form_data=ToolCallRequest(
            run_id='run-1',
            participant_id='leader',
            tool_call_id='call-1',
            tool_id='tool:terminal:main:run_command',
            arguments={
                'command': (
                    'printf "artifact" > '
                    '/workspace/agent-runs/run-2/outputs/report.md'
                )
            },
            idempotency_key='tool:leader:call-1:1',
        ),
        idempotency_key='tool:leader:call-1:1',
        authorization='Bearer service-secret',
        approval_coordinator=coordinator,
    )

    assert approval_required['status'] == 'approval_required'
    assert calls == []
    assert store.state['run-1'] == 'waiting_approval'
    assert [event['event_type'] for event in store.events] == ['approval.requested']


@pytest.mark.asyncio
async def test_destructive_tool_call_waits_for_approval_then_resumes_with_tool_result():
    calls = []

    async def write_file(path: str, content: str):
        calls.append((path, content))
        return {'written': path}

    _envelope, registry = build_tool_access_envelope(
        {
            'write_file': {
                'tool_id': 'terminal:main',
                'callable': write_file,
                'spec': {'name': 'write_file', 'parameters': {'type': 'object'}},
                'type': 'terminal',
            }
        }
    )
    store = FakeApprovalStore()
    authority = AgentToolAuthority(operation_store=store, registry=registry)
    coordinator = AgentApprovalCoordinator(store)
    request = ToolCallRequest(
        run_id='run-1',
        participant_id='leader',
        tool_call_id='call-1',
        tool_id='tool:terminal:main:write_file',
        arguments={'path': '/workspace/report.txt', 'content': 'replacement'},
        idempotency_key='tool:leader:call-1:1',
    )

    approval_required = await execute_agent_run_tool_call(
        request=_service_request(authority),
        run_id='run-1',
        form_data=request,
        idempotency_key='tool:leader:call-1:1',
        authorization='Bearer service-secret',
        approval_coordinator=coordinator,
    )

    assert approval_required['status'] == 'approval_required'
    assert approval_required['structured_error'] is None
    assert approval_required['raw']['approval_id'] == 'approval:run-1:call-1'
    assert calls == []
    assert store.state['run-1'] == 'waiting_approval'
    assert [event['event_type'] for event in store.events] == ['approval.requested']

    resumed = await coordinator.decide(
        ApprovalDecisionRequest(
            run_id='run-1',
            approval_id='approval:run-1:call-1',
            decision='approved',
            idempotency_key='approval:run-1:approval:run-1:call-1:1',
        )
    )

    assert resumed['status'] == 'success'
    assert resumed['content'] == '{"written":"/workspace/report.txt"}'
    assert calls == [('/workspace/report.txt', 'replacement')]
    assert store.state['run-1'] == 'running'
    assert [event['event_type'] for event in store.events] == [
        'approval.requested',
        'approval.completed',
    ]
    assert store.events[-1]['payload']['decision'] == 'approved'


@pytest.mark.asyncio
async def test_destructive_tool_call_rejection_returns_normalized_rejection_result():
    store = FakeApprovalStore()
    coordinator = AgentApprovalCoordinator(store)
    request = ToolCallRequest(
        run_id='run-1',
        participant_id='leader',
        tool_call_id='call-2',
        tool_id='tool:terminal:main:delete_entry',
        arguments={'path': '/workspace/report.txt'},
        idempotency_key='tool:leader:call-2:1',
    )
    resume_called = False

    async def resume():
        nonlocal resume_called
        resume_called = True
        return {'status': 'success', 'content': 'deleted'}

    approval_required = await coordinator.request_tool_approval(
        request,
        {
            'name': 'delete_entry',
            'tool_id': 'terminal:main',
            'type': 'terminal',
        },
        resume=resume,
    )

    assert approval_required is not None
    rejected = await coordinator.decide(
        ApprovalDecisionRequest(
            run_id='run-1',
            approval_id='approval:run-1:call-2',
            decision='rejected',
            idempotency_key='approval:run-1:approval:run-1:call-2:1',
        )
    )

    assert rejected['status'] == 'approval_rejected'
    assert rejected['content'] == 'User rejected approval for delete_entry.'
    assert rejected['structured_error'] == {
        'code': 'approval_rejected',
        'message': 'User rejected approval for delete_entry.',
        'retryable': False,
        'details': {
            'approval_id': 'approval:run-1:call-2',
            'tool_call_id': 'call-2',
            'tool_id': 'tool:terminal:main:delete_entry',
        },
    }
    assert resume_called is False
    assert store.state['run-1'] == 'running'
    assert store.events[-1]['payload']['decision'] == 'rejected'


@pytest.mark.asyncio
async def test_approval_decision_requires_same_coordinator_that_holds_pending_resume():
    store = FakeApprovalStore()
    request_coordinator = AgentApprovalCoordinator(store)
    decision_coordinator = AgentApprovalCoordinator(store)
    request = ToolCallRequest(
        run_id='run-1',
        participant_id='leader',
        tool_call_id='call-4',
        tool_id='tool:terminal:main:delete_entry',
        arguments={'path': '/workspace/report.txt'},
        idempotency_key='tool:leader:call-4:1',
    )

    approval_required = await request_coordinator.request_tool_approval(
        request,
        {
            'name': 'delete_entry',
            'tool_id': 'terminal:main',
            'type': 'terminal',
        },
        resume=lambda: {'status': 'success', 'content': 'deleted'},
    )

    assert approval_required is not None
    assert store.state['run-1'] == 'waiting_approval'

    with pytest.raises(ApprovalNotFound):
        await decision_coordinator.decide(
            ApprovalDecisionRequest(
                run_id='run-1',
                approval_id='approval:run-1:call-4',
                decision='approved',
                idempotency_key='approval:run-1:approval:run-1:call-4:1',
            )
        )


@pytest.mark.asyncio
async def test_approval_decisions_replay_duplicates_and_reject_idempotency_conflicts():
    calls = 0
    store = FakeApprovalStore()
    coordinator = AgentApprovalCoordinator(store)
    request = ToolCallRequest(
        run_id='run-1',
        participant_id='leader',
        tool_call_id='call-3',
        tool_id='tool:terminal:main:write_file',
        arguments={'path': '/workspace/report.txt', 'content': 'replacement'},
        idempotency_key='tool:leader:call-3:1',
    )

    async def resume():
        nonlocal calls
        calls += 1
        return {'status': 'success', 'content': 'written'}

    await coordinator.request_tool_approval(
        request,
        {
            'name': 'write_file',
            'tool_id': 'terminal:main',
            'type': 'terminal',
        },
        resume=resume,
    )
    decision = ApprovalDecisionRequest(
        run_id='run-1',
        approval_id='approval:run-1:call-3',
        decision='approved',
        idempotency_key='approval:run-1:approval:run-1:call-3:1',
    )

    first = await coordinator.decide(decision)
    duplicate = await coordinator.decide(decision)

    assert first == duplicate
    assert calls == 1
    assert [event['event_type'] for event in store.events] == [
        'approval.requested',
        'approval.completed',
    ]

    with pytest.raises(AgentRunOperationConflict):
        await coordinator.decide(
            decision.model_copy(update={'decision': 'rejected'})
        )
