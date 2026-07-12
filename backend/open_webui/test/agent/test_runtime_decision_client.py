import pytest
from open_webui.agent import runtime_client as runtime_client_module
from open_webui.agent.runtime_client import (
    AgentRuntimeAuthenticationError,
    AgentRuntimeClient,
    AgentRuntimeRejected,
    AgentRuntimeUnavailable,
)


class CapturingRuntimeClient(AgentRuntimeClient):
    def __init__(self):
        super().__init__('http://runtime.test', service_token='secret')
        self.requests = []

    async def _request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        return {'state': 'prepared'}


@pytest.mark.asyncio
async def test_runtime_decision_execution_client_uses_frozen_wire_paths_and_methods():
    client = CapturingRuntimeClient()
    body = {
        'schema_version': 1,
        'runtime_session_id': 'runtime-session-1',
        'execution_id': 'execution-1',
        'expected_checkpoint_version': 7,
        'subject_id': 'approval-1',
        'command_type': 'resume_approval',
        'payload': {'decision': 'approved'},
        'fingerprint': 'a' * 64,
    }

    await client.prepare_decision_execution('run-1', 'execution-1', body)
    await client.activate_decision_execution('run-1', 'execution-1')
    await client.get_decision_execution('run-1', 'execution-1')

    assert client.requests == [
        ('PUT', '/v1/openwebui/runs/run-1/executions/execution-1', body),
        ('POST', '/v1/openwebui/runs/run-1/executions/execution-1/activate', None),
        ('GET', '/v1/openwebui/runs/run-1/executions/execution-1', None),
    ]
    assert not hasattr(client, 'notify_approval_decision')


class FakeResponse:
    def __init__(self, status, *, headers=None):
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, content_type=None):
        return {'detail': {'code': 'runtime_error', 'message': f'HTTP {self.status}'}}

    async def text(self):
        return f'HTTP {self.status}'


class FakeSession:
    response = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def request(self, *args, **kwargs):
        assert self.response is not None
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status_code', 'expected_error'),
    [
        (408, AgentRuntimeUnavailable),
        (425, AgentRuntimeUnavailable),
        (429, AgentRuntimeUnavailable),
        (400, AgentRuntimeRejected),
        (404, AgentRuntimeRejected),
        (409, AgentRuntimeRejected),
        (401, AgentRuntimeAuthenticationError),
        (403, AgentRuntimeAuthenticationError),
    ],
)
async def test_runtime_client_classifies_http_errors(
    monkeypatch,
    status_code,
    expected_error,
):
    FakeSession.response = FakeResponse(
        status_code,
        headers={'Retry-After': '3'} if status_code in {408, 425, 429} else {},
    )
    monkeypatch.setattr(runtime_client_module.aiohttp, 'ClientSession', FakeSession)

    with pytest.raises(expected_error) as exc_info:
        await AgentRuntimeClient('http://runtime.test').get_decision_execution(
            'run-1',
            'execution-1',
        )

    if expected_error is AgentRuntimeUnavailable:
        assert exc_info.value.retry_after_seconds == 3.0
