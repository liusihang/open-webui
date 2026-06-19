import os
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import pytest
from open_webui.agent.artifacts import AgentRunArtifactRegistrar
from open_webui.agent.resources import AgentRunResourceManager
from open_webui.agent.tool_authority import (
    AgentToolAuthority,
    ToolNotAllowed,
    ToolCallRequest,
    build_tool_access_envelope,
    normalize_tool_result,
)
from open_webui.routers.agent_service import get_agent_tool_authority


class FakeOperationStore:
    def __init__(self):
        self.claims = {}
        self.claim_count = 0
        self.successes = {}

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
            AgentRunOperationConflict,
            AgentRunOperationModel,
        )

        key = (run_id, operation_type, idempotency_key)
        existing = self.claims.get(key)
        if existing:
            if existing.request_hash != request_hash:
                raise AgentRunOperationConflict('idempotency key was reused with a different request hash')
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
                updated = operation.model_copy(update={'status': 'succeeded', 'response': response})
                self.claims[key] = updated
                self.successes[operation_id] = response
                return updated
        raise AssertionError(f'unknown operation {operation_id}')

    async def finish_operation_error(self, operation_id, error):
        for key, operation in list(self.claims.items()):
            if operation.id == operation_id:
                updated = operation.model_copy(update={'status': 'failed', 'error': error})
                self.claims[key] = updated
                return updated
        raise AssertionError(f'unknown operation {operation_id}')


class FakeRunStore(FakeOperationStore):
    def __init__(self, run):
        super().__init__()
        self.run = run

    async def get_run(self, run_id):
        if self.run.id == run_id:
            return self.run
        return None


class FakeArtifactStore:
    def __init__(self):
        self.rows = []
        self._by_path = {}

    async def register_artifact(self, **kwargs):
        path_key = (kwargs['run_id'], kwargs['path'], kwargs['kind'])
        existing = self._by_path.get(path_key)
        if existing is not None:
            return existing
        row = {
            'id': f'artifact-{len(self.rows) + 1}',
            **kwargs,
            'created_at': len(self.rows) + 1,
        }
        self.rows.append(row)
        self._by_path[path_key] = row
        return row


def test_tool_access_envelope_exposes_schema_and_opaque_ids_without_callables():
    async def read_file(path: str):
        return f'read {path}'

    envelope, registry = build_tool_access_envelope(
        {
            'read_file': {
                'tool_id': 'terminal:main',
                'callable': read_file,
                'spec': {
                    'name': 'read_file',
                    'description': 'Read a file.',
                    'parameters': {
                        'type': 'object',
                        'properties': {'path': {'type': 'string'}},
                        'required': ['path'],
                    },
                },
                'type': 'terminal',
            }
        }
    )

    assert envelope == {
        'tools': [
            {
                'id': 'tool:terminal:main:read_file',
                'name': 'read_file',
                'type': 'terminal',
                'schema': {
                    'name': 'read_file',
                    'description': 'Read a file.',
                    'parameters': {
                        'type': 'object',
                        'properties': {'path': {'type': 'string'}},
                        'required': ['path'],
                    },
                },
            }
        ]
    }
    assert 'callable' not in envelope['tools'][0]
    assert registry['tool:terminal:main:read_file']['callable'] is read_file


@pytest.mark.asyncio
async def test_service_tool_authority_prefers_run_scoped_registry():
    async def scoped_tool():
        return 'scoped'

    async def global_tool():
        return 'global'

    _scoped_envelope, scoped_registry = build_tool_access_envelope(
        {
            'lookup_fact': {
                'tool_id': 'builtin:lookup_fact',
                'callable': scoped_tool,
                'spec': {'name': 'lookup_fact'},
                'type': 'builtin',
            }
        }
    )
    _global_envelope, global_registry = build_tool_access_envelope(
        {
            'lookup_fact': {
                'tool_id': 'builtin:lookup_fact',
                'callable': global_tool,
                'spec': {'name': 'lookup_fact'},
                'type': 'builtin',
            }
        }
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                AGENT_EVENT_STORE=FakeOperationStore(),
                AGENT_TOOL_REGISTRIES={'run-1': scoped_registry},
                AGENT_TOOL_REGISTRY=global_registry,
            )
        )
    )

    authority = await get_agent_tool_authority(request, run_id='run-1')

    tool_id = 'tool:builtin:lookup_fact:lookup_fact'
    assert authority.registry[tool_id]['callable'] is scoped_tool


@pytest.mark.asyncio
async def test_service_tool_authority_rebuilds_missing_builtin_registry_from_run_snapshot(monkeypatch):
    calls = []

    async def write_note(title: str, content: str):
        calls.append((title, content))
        return {'title': title, 'content': content}

    async def fake_get_builtin_tools(request, extra_params, features=None, model=None):
        assert extra_params['__user__']['id'] == 'user-1'
        assert extra_params['__metadata__']['chat_id'] == 'chat-1'
        return {
            'write_note': {
                'tool_id': 'builtin:write_note',
                'callable': write_note,
                'spec': {'name': 'write_note', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            },
            'search_web': {
                'tool_id': 'builtin:search_web',
                'callable': lambda query: {'query': query},
                'spec': {'name': 'search_web', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            },
        }

    user = SimpleNamespace(
        id='user-1',
        model_dump=lambda mode=None: {'id': 'user-1', 'role': 'admin', 'name': 'Test User'},
    )
    run = SimpleNamespace(
        id='run-1',
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-1',
        tool_access_snapshot={
            'tools': [
                {
                    'id': 'tool:builtin:write_note:write_note',
                    'name': 'write_note',
                    'type': 'builtin',
                    'schema': {'name': 'write_note', 'parameters': {'type': 'object'}},
                }
            ]
        },
    )

    from open_webui.routers import agent_service

    monkeypatch.setattr(agent_service, 'get_builtin_tools', fake_get_builtin_tools, raising=False)
    monkeypatch.setattr(
        agent_service,
        'Users',
        SimpleNamespace(get_user_by_id=lambda user_id: user),
        raising=False,
    )

    request = SimpleNamespace(
        cookies={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(),
                AGENT_EVENT_STORE=FakeRunStore(run),
                AGENT_TOOL_REGISTRIES={},
            )
        ),
    )

    authority = await get_agent_tool_authority(request, run_id='run-1')

    assert set(authority.registry) == {'tool:builtin:write_note:write_note'}
    assert request.app.state.AGENT_TOOL_REGISTRIES['run-1'] is authority.registry

    result = await authority.execute_tool_call(
        ToolCallRequest(
            run_id='run-1',
            user_id='user-1',
            participant_id='leader',
            tool_call_id='call-1',
            tool_id='tool:builtin:write_note:write_note',
            arguments={'title': 'Plan', 'content': 'Ship it'},
            idempotency_key='tool:leader:call-1:1',
        )
    )

    assert calls == [('Plan', 'Ship it')]
    assert result['status'] == 'success'


@pytest.mark.asyncio
async def test_service_tool_authority_rebuilds_available_builtin_when_snapshot_has_unavailable_builtin(
    monkeypatch,
):
    calls = []

    async def search_web(query: str):
        calls.append(query)
        return {'query': query, 'results': []}

    async def fake_get_builtin_tools(request, extra_params, features=None, model=None):
        assert features['web_search'] is True
        assert model['info']['meta']['builtinTools']['web_search'] is True
        assert model['info']['meta']['builtinTools']['skills'] is True
        return {
            'search_web': {
                'tool_id': 'builtin:search_web',
                'callable': search_web,
                'spec': {'name': 'search_web', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            }
        }

    user = SimpleNamespace(
        id='user-1',
        model_dump=lambda mode=None: {'id': 'user-1', 'role': 'admin', 'name': 'Test User'},
    )
    run = SimpleNamespace(
        id='run-1',
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-1',
        tool_access_snapshot={
            'tools': [
                {
                    'id': 'tool:builtin:search_web:search_web',
                    'name': 'search_web',
                    'type': 'builtin',
                    'schema': {'name': 'search_web', 'parameters': {'type': 'object'}},
                },
                {
                    'id': 'tool:builtin:install_skill:install_skill',
                    'name': 'install_skill',
                    'type': 'builtin',
                    'schema': {'name': 'install_skill', 'parameters': {'type': 'object'}},
                },
                {
                    'id': 'tool:builtin:read_skill:read_skill',
                    'name': 'read_skill',
                    'type': 'builtin',
                    'schema': {'name': 'read_skill', 'parameters': {'type': 'object'}},
                },
                {
                    'id': 'tool:builtin:update_skill:update_skill',
                    'name': 'update_skill',
                    'type': 'builtin',
                    'schema': {'name': 'update_skill', 'parameters': {'type': 'object'}},
                },
            ]
        },
    )

    from open_webui.routers import agent_service

    monkeypatch.setattr(agent_service, 'get_builtin_tools', fake_get_builtin_tools, raising=False)
    monkeypatch.setattr(
        agent_service,
        'Users',
        SimpleNamespace(get_user_by_id=lambda user_id: user),
        raising=False,
    )

    request = SimpleNamespace(
        cookies={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(),
                AGENT_EVENT_STORE=FakeRunStore(run),
                AGENT_TOOL_REGISTRIES={},
            )
        ),
    )

    authority = await get_agent_tool_authority(request, run_id='run-1')

    assert set(authority.registry) == {'tool:builtin:search_web:search_web'}
    result = await authority.execute_tool_call(
        ToolCallRequest(
            run_id='run-1',
            user_id='user-1',
            participant_id='leader',
            tool_call_id='call-1',
            tool_id='tool:builtin:search_web:search_web',
            arguments={'query': 'pr7 registry rebuild'},
            idempotency_key='tool:leader:call-1:1',
        )
    )

    assert calls == ['pr7 registry rebuild']
    assert result['status'] == 'success'


@pytest.mark.asyncio
async def test_service_tool_authority_reports_requested_builtin_unavailable_after_partial_rebuild(
    monkeypatch,
):
    async def search_web(query: str):
        return {'query': query, 'results': []}

    async def fake_get_builtin_tools(request, extra_params, features=None, model=None):
        return {
            'search_web': {
                'tool_id': 'builtin:search_web',
                'callable': search_web,
                'spec': {'name': 'search_web', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            }
        }

    user = SimpleNamespace(
        id='user-1',
        model_dump=lambda mode=None: {'id': 'user-1', 'role': 'admin', 'name': 'Test User'},
    )
    run = SimpleNamespace(
        id='run-1',
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-1',
        tool_access_snapshot={
            'tools': [
                {
                    'id': 'tool:builtin:search_web:search_web',
                    'name': 'search_web',
                    'type': 'builtin',
                    'schema': {'name': 'search_web', 'parameters': {'type': 'object'}},
                },
                {
                    'id': 'tool:builtin:install_skill:install_skill',
                    'name': 'install_skill',
                    'type': 'builtin',
                    'schema': {'name': 'install_skill', 'parameters': {'type': 'object'}},
                },
            ]
        },
    )

    from open_webui.routers import agent_service

    monkeypatch.setattr(agent_service, 'get_builtin_tools', fake_get_builtin_tools, raising=False)
    monkeypatch.setattr(
        agent_service,
        'Users',
        SimpleNamespace(get_user_by_id=lambda user_id: user),
        raising=False,
    )

    request = SimpleNamespace(
        cookies={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(),
                AGENT_EVENT_STORE=FakeRunStore(run),
                AGENT_TOOL_REGISTRIES={},
            )
        ),
    )

    authority = await get_agent_tool_authority(request, run_id='run-1')

    with pytest.raises(ToolNotAllowed, match='install_skill'):
        await authority.execute_tool_call(
            ToolCallRequest(
                run_id='run-1',
                user_id='user-1',
                participant_id='leader',
                tool_call_id='call-1',
                tool_id='tool:builtin:install_skill:install_skill',
                arguments={},
                idempotency_key='tool:leader:call-1:1',
            )
        )


@pytest.mark.asyncio
async def test_service_tool_authority_rebuilds_missing_terminal_registry_from_run_snapshot(monkeypatch):
    calls = []

    async def run_command(command: str):
        calls.append(command)
        return {'process_id': 'proc-1', 'status': 'completed', 'exit_code': 0}

    async def fake_get_terminal_tools(request, terminal_id, user, extra_params):
        assert terminal_id == 'term-1'
        assert user.id == 'user-1'
        assert extra_params['__metadata__']['chat_id'] == 'chat-1'
        return (
            {
                'run_command': {
                    'tool_id': 'terminal:term-1',
                    'callable': run_command,
                    'spec': {'name': 'run_command', 'parameters': {'type': 'object'}},
                    'type': 'terminal',
                }
            },
            None,
        )

    user = SimpleNamespace(
        id='user-1',
        role='admin',
        model_dump=lambda mode=None: {'id': 'user-1', 'role': 'admin', 'name': 'Test User'},
    )
    run = SimpleNamespace(
        id='run-1',
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='msg-user',
        assistant_message_id='msg-assistant',
        leader_model_id='model-1',
        tool_access_snapshot={
            'tools': [
                {
                    'id': 'tool:terminal:term-1:run_command',
                    'name': 'run_command',
                    'type': 'terminal',
                    'schema': {'name': 'run_command', 'parameters': {'type': 'object'}},
                }
            ]
        },
    )

    from open_webui.routers import agent_service

    monkeypatch.setattr(agent_service, 'get_terminal_tools', fake_get_terminal_tools)
    monkeypatch.setattr(
        agent_service,
        'Users',
        SimpleNamespace(get_user_by_id=lambda user_id: user),
    )

    request = SimpleNamespace(
        cookies={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(),
                AGENT_EVENT_STORE=FakeRunStore(run),
                AGENT_TOOL_REGISTRIES={},
            )
        ),
    )

    authority = await get_agent_tool_authority(request, run_id='run-1')

    assert set(authority.registry) == {'tool:terminal:term-1:run_command'}
    result = await authority.execute_tool_call(
        ToolCallRequest(
            run_id='run-1',
            user_id='user-1',
            participant_id='leader',
            tool_call_id='call-1',
            tool_id='tool:terminal:term-1:run_command',
            arguments={'command': 'echo ok'},
            idempotency_key='tool:leader:call-1:1',
        )
    )

    assert calls == ['echo ok']
    assert result['status'] == 'success'
    assert result['process_refs'][0]['terminal_server_id'] == 'term-1'


def test_normalize_tool_result_extracts_terminal_process_refs():
    result = normalize_tool_result(
        {
            'process_id': 'proc-123',
            'status': 'running',
            'exit_code': None,
            'log_path': '/tmp/process.jsonl',
            'next_offset': 8,
        },
        tool_name='run_command',
        tool_id='terminal:main',
        arguments={'command': 'python analysis.py'},
    )

    assert result['status'] == 'success'
    assert result['structured_error'] is None
    assert result['process_refs'] == [
        {
            'terminal_server_id': 'main',
            'process_id': 'proc-123',
            'command': 'python analysis.py',
            'status': 'running',
            'exit_code': None,
            'log_path': '/tmp/process.jsonl',
            'next_offset': 8,
            'metadata': {},
        }
    ]
    assert 'proc-123' in result['content']


@pytest.mark.asyncio
async def test_tool_call_retries_return_cached_response_without_reexecuting_callable():
    calls = []

    async def read_file(path: str):
        calls.append(path)
        return {'text': f'contents of {path}'}

    _envelope, registry = build_tool_access_envelope(
        {
            'read_file': {
                'tool_id': 'builtin:read_file',
                'callable': read_file,
                'spec': {'name': 'read_file', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            }
        }
    )
    store = FakeOperationStore()
    authority = AgentToolAuthority(operation_store=store, registry=registry)
    request = ToolCallRequest(
        run_id='run-1',
        participant_id='leader',
        tool_call_id='call-1',
        tool_id='tool:builtin:read_file:read_file',
        arguments={'path': '/workspace/report.txt'},
        idempotency_key='tool:leader:call-1:1',
    )

    first = await authority.execute_tool_call(request)
    duplicate = await authority.execute_tool_call(request)

    assert calls == ['/workspace/report.txt']
    assert first == duplicate
    assert duplicate['status'] == 'success'
    assert duplicate['content'] == '{"text":"contents of /workspace/report.txt"}'


@pytest.mark.asyncio
async def test_tool_call_same_idempotency_key_with_modified_body_conflicts():
    async def read_file(path: str):
        return f'contents of {path}'

    _envelope, registry = build_tool_access_envelope(
        {
            'read_file': {
                'tool_id': 'builtin:read_file',
                'callable': read_file,
                'spec': {'name': 'read_file', 'parameters': {'type': 'object'}},
                'type': 'builtin',
            }
        }
    )
    authority = AgentToolAuthority(operation_store=FakeOperationStore(), registry=registry)

    await authority.execute_tool_call(
        ToolCallRequest(
            run_id='run-1',
            participant_id='leader',
            tool_call_id='call-1',
            tool_id='tool:builtin:read_file:read_file',
            arguments={'path': '/workspace/a.txt'},
            idempotency_key='tool:leader:call-1:1',
        )
    )

    with pytest.raises(ValueError, match='idempotency key'):
        await authority.execute_tool_call(
            ToolCallRequest(
                run_id='run-1',
                participant_id='leader',
                tool_call_id='call-1',
                tool_id='tool:builtin:read_file:read_file',
                arguments={'path': '/workspace/b.txt'},
                idempotency_key='tool:leader:call-1:1',
            )
        )


@pytest.mark.asyncio
async def test_terminal_run_command_registers_process_refs_and_explicit_output_artifacts():
    async def run_command(command: str, output_paths: list[str]):
        return {
            'process_id': 'proc-123',
            'command': command,
            'status': 'running',
            'exit_code': None,
            'log_path': '/workspace/logs/proc-123.jsonl',
            'next_offset': 7,
            'output_paths': output_paths,
        }

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
    resource_manager = AgentRunResourceManager()
    artifact_store = FakeArtifactStore()
    authority = AgentToolAuthority(
        operation_store=FakeOperationStore(),
        registry=registry,
        resource_manager=resource_manager,
        artifact_registrar=AgentRunArtifactRegistrar(artifact_store),
    )

    result = await authority.execute_tool_call(
        ToolCallRequest(
            run_id='run-1',
            user_id='user-1',
            participant_id='leader',
            tool_call_id='call-1',
            tool_id='tool:terminal:main:run_command',
            arguments={'command': 'python analysis.py', 'output_paths': ['report.csv']},
            idempotency_key='tool:leader:call-1:1',
        )
    )

    assert result['process_refs'] == [
        {
            'terminal_server_id': 'main',
            'process_id': 'proc-123',
            'command': 'python analysis.py',
            'status': 'running',
            'exit_code': None,
            'log_path': '/workspace/logs/proc-123.jsonl',
            'next_offset': 7,
            'metadata': {},
        }
    ]
    assert resource_manager.process_refs_for_run('run-1') == result['process_refs']
    assert result['artifacts'] == [
        {
            'artifact_id': 'artifact-1',
            'kind': 'file',
            'path': '/workspace/agent-runs/run-1/outputs/report.csv',
            'url': None,
            'mime_type': None,
            'size': None,
            'metadata': {
                'cleanup_eligible': False,
                'retention': 'user_visible_output',
                'participant_id': 'leader',
            },
        }
    ]
    assert artifact_store.rows[0]['idempotency_key'] == 'artifact:leader:file:main:run-1:outputs:report.csv'


@pytest.mark.asyncio
async def test_service_default_tool_authority_wires_terminal_process_and_artifact_helpers():
    async def run_command(command: str, output_paths: list[str]):
        return {
            'process_id': 'proc-456',
            'command': command,
            'status': 'completed',
            'exit_code': 0,
            'log_path': '/workspace/logs/proc-456.jsonl',
            'next_offset': 12,
            'output_paths': output_paths,
        }

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
    operation_store = FakeOperationStore()
    resource_manager = AgentRunResourceManager()
    artifact_store = FakeArtifactStore()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                AGENT_EVENT_STORE=operation_store,
                AGENT_TOOL_REGISTRY=registry,
                AGENT_RUN_RESOURCE_MANAGER=resource_manager,
                AGENT_RUN_ARTIFACT_REGISTRAR=AgentRunArtifactRegistrar(artifact_store),
            )
        )
    )
    authority = await get_agent_tool_authority(request)

    result = await authority.execute_tool_call(
        ToolCallRequest(
            run_id='run-1',
            user_id='user-1',
            participant_id='leader',
            tool_call_id='call-1',
            tool_id='tool:terminal:main:run_command',
            arguments={'command': 'python analysis.py', 'output_paths': ['report.csv']},
            idempotency_key='tool:leader:call-1:1',
        )
    )

    assert resource_manager.process_refs_for_run('run-1') == result['process_refs']
    assert result['artifacts'][0]['path'] == '/workspace/agent-runs/run-1/outputs/report.csv'
    assert artifact_store.rows[0]['metadata']['cleanup_eligible'] is False
