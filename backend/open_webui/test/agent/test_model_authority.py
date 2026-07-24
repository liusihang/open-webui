import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from fastapi import HTTPException
from open_webui.agent.model_authority import (
    AgentModelAuthority,
    ModelCallRequest,
    ModelGuardRejected,
    ModelNotAllowed,
)
from open_webui.internal.db import Base
from open_webui.models.agent_runs import (
    AgentArtifact,
    AgentRun,
    AgentRunEvent,
    AgentRunOperation,
    AgentRunOperationConflict,
    AgentRuns,
)
from open_webui.routers.agent_service import execute_agent_run_model_call
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def agent_run_db(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                AgentRun.__table__,
                AgentRunEvent.__table__,
                AgentArtifact.__table__,
                AgentRunOperation.__table__,
            ],
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context(db=None):
        if db is not None:
            yield db
            return

        async with session_factory() as session:
            yield session

    monkeypatch.setattr('open_webui.models.agent_runs.get_async_db_context', session_context)

    yield session_factory

    await engine.dispose()


@pytest.mark.asyncio
async def test_model_call_requires_trusted_request_state_not_forged_metadata(agent_run_db):
    run = await _create_running_run()
    request = _request(enable_agent_mode=True)
    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=_unexpected_completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    with pytest.raises(ModelGuardRejected):
        await authority.execute_model_call(
            request,
            ModelCallRequest(
                run_id=run.id,
                participant_id='leader',
                model_call_id='call-1',
                model='model-a',
                messages=[{'role': 'user', 'content': 'hello'}],
                metadata={
                    'agent_internal_model_call': True,
                    'agent_run_id': run.id,
                },
                idempotency_key='model:leader:call-1:1',
            ),
        )


@pytest.mark.asyncio
async def test_model_call_rejects_newly_unauthorized_model(agent_run_db):
    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)

    async def reject_model_access(user, model):
        raise Exception('Model not found')

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=_unexpected_completion_handler,
        user_loader=_user_loader,
        model_access_checker=reject_model_access,
    )

    with pytest.raises(ModelNotAllowed, match='Model not found'):
        await authority.execute_model_call(
            request,
            ModelCallRequest(
                run_id=run.id,
                participant_id='leader',
                model_call_id='call-1',
                model='model-a',
                messages=[{'role': 'user', 'content': 'hello'}],
                idempotency_key='model:leader:call-1:1',
            ),
        )


@pytest.mark.asyncio
async def test_model_authority_refreshes_nonempty_stale_model_cache_on_miss():
    request = _trusted_request(enable_agent_mode=True, run_id='run-1')
    request.app.state.MODELS = {'stale-model': {'id': 'stale-model'}}
    refreshed = False

    async def refresh_models(request, user):
        nonlocal refreshed
        refreshed = True
        request.app.state.MODELS = {
            'model-a': {
                'id': 'model-a',
                'name': 'Model A',
                'owned_by': 'openai',
                'info': {'meta': {}},
            }
        }

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        model_loader=refresh_models,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    model = await authority._resolve_authorized_model(
        request,
        await _user_loader('user-1'),
        'model-a',
    )

    assert refreshed is True
    assert model['id'] == 'model-a'


@pytest.mark.asyncio
async def test_verified_model_call_uses_provider_path_without_creating_nested_agent_run(
    agent_run_db,
):
    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    captured = {}

    async def completion_handler(request, form_data, user):
        captured['state_guard'] = request.state.agent_internal_model_call
        captured['form_data'] = form_data
        captured['user_id'] = user.id
        return {'id': 'chatcmpl-1', 'choices': [{'message': {'content': 'hello'}}]}

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    response = await authority.execute_model_call(
        request,
        ModelCallRequest(
            run_id=run.id,
            participant_id='leader',
            model_call_id='call-1',
            model='model-a',
            messages=[{'role': 'user', 'content': 'hello'}],
            idempotency_key='model:leader:call-1:1',
        ),
    )

    runs = await AgentRuns.list_runs_by_chat('chat-1', 'user-1')
    assert [stored.id for stored in runs] == [run.id]
    assert captured['state_guard'] is True
    assert captured['user_id'] == 'user-1'
    assert response['status'] == 'success'
    assert response['model'] == 'model-a'
    assert response['response']['id'] == 'chatcmpl-1'


@pytest.mark.asyncio
async def test_model_call_normalizes_responses_items_before_provider_routing(agent_run_db):
    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    captured = {}

    async def completion_handler(request, form_data, user):
        captured['messages'] = form_data['messages']
        return {'id': 'chatcmpl-structured', 'choices': [{'message': {'content': 'done'}}]}

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    await authority.execute_model_call(
        request,
        ModelCallRequest(
            run_id=run.id,
            participant_id='leader',
            model_call_id='call-structured',
            model='model-a',
            messages=[
                {'role': 'user', 'content': 'Continue.'},
                {
                    'type': 'function_call',
                    'call_id': 'call-1',
                    'name': 'read_file',
                    'arguments': '{"path":"README.md"}',
                },
                {
                    'type': 'function_call',
                    'call_id': 'call-2',
                    'name': 'list_files',
                    'arguments': '{}',
                },
                {
                    'type': 'function_call_output',
                    'call_id': 'call-1',
                    'output': '{"content":"OpenWebUI"}',
                },
                {
                    'type': 'function_call_output',
                    'call_id': 'call-2',
                    'output': '{"files":["README.md"]}',
                },
                {
                    'type': 'message',
                    'id': 'msg-note-1',
                    'status': 'completed',
                    'role': 'assistant',
                    'content': 'I inspected the workspace.',
                    'phase': 'commentary',
                },
            ],
            idempotency_key='model:leader:call-structured:1',
        ),
    )

    assert captured['messages'] == [
        {'role': 'user', 'content': 'Continue.'},
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call-1',
                    'type': 'function',
                    'function': {'name': 'read_file', 'arguments': '{"path":"README.md"}'},
                },
                {
                    'id': 'call-2',
                    'type': 'function',
                    'function': {'name': 'list_files', 'arguments': '{}'},
                },
            ],
        },
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': '{"content":"OpenWebUI"}'},
        {'role': 'tool', 'tool_call_id': 'call-2', 'content': '{"files":["README.md"]}'},
        {
            'role': 'assistant',
            'content': 'I inspected the workspace.',
            'phase': 'commentary',
        },
    ]


@pytest.mark.asyncio
async def test_admin_model_call_uses_product_chat_access_bypass_for_provider_model(
    agent_run_db,
):
    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    captured = {}

    async def completion_handler(request, form_data, user):
        captured['form_data'] = form_data
        captured['user_id'] = user.id
        return {'id': 'chatcmpl-1', 'choices': [{'message': {'content': 'hello'}}]}

    async def access_checker_should_be_bypassed(user, model):
        raise AssertionError('admin provider model access check should be bypassed')

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_admin_user_loader,
        model_access_checker=access_checker_should_be_bypassed,
    )

    response = await authority.execute_model_call(
        request,
        ModelCallRequest(
            run_id=run.id,
            participant_id='leader',
            model_call_id='call-1',
            model='model-a',
            messages=[{'role': 'user', 'content': 'hello'}],
            idempotency_key='model:leader:call-1:1',
        ),
    )

    assert captured['user_id'] == 'user-1'
    assert captured['form_data']['model'] == 'model-a'
    assert response['status'] == 'success'


@pytest.mark.asyncio
async def test_model_call_payload_includes_agent_audit_metadata(agent_run_db):
    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    captured = {}

    async def completion_handler(request, form_data, user):
        captured['metadata'] = form_data['metadata']
        return {'id': 'chatcmpl-1'}

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    await authority.execute_model_call(
        request,
        ModelCallRequest(
            run_id=run.id,
            participant_id='subagent-a',
            model_call_id='call-42',
            model='model-a',
            messages=[{'role': 'user', 'content': 'summarize'}],
            metadata={'purpose': 'summary'},
            idempotency_key='model:subagent-a:call-42:1',
        ),
    )

    assert captured['metadata'] == {
        'purpose': 'summary',
        'agent_run_id': run.id,
        'agent_internal_model_call': True,
        'agent_participant_id': 'subagent-a',
        'agent_model_call_id': 'call-42',
    }


@pytest.mark.asyncio
async def test_model_call_promotes_reasoning_params_to_top_level_form_data(agent_run_db):
    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    captured = {}

    async def completion_handler(request, form_data, user):
        captured['form_data'] = form_data
        return {'id': 'chatcmpl-1'}

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    await authority.execute_model_call(
        request,
        ModelCallRequest(
            run_id=run.id,
            participant_id='leader',
            model_call_id='call-reasoning',
            model='model-a',
            messages=[{'role': 'user', 'content': 'hello'}],
            params={
                'temperature': 0.2,
                'reasoning': {
                    'enabled': True,
                    'effort': 'high',
                    'max_tokens': 8126,
                },
            },
            idempotency_key='model:leader:call-reasoning:1',
        ),
    )

    assert captured['form_data']['params'] == {'temperature': 0.2}
    assert captured['form_data']['reasoning'] == {
        'enabled': True,
        'effort': 'high',
        'max_tokens': 8126,
    }


@pytest.mark.asyncio
async def test_stream_model_call_promotes_reasoning_params_to_top_level_form_data(agent_run_db):
    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    captured = {}

    async def completion_handler(request, form_data, user):
        captured['form_data'] = form_data
        return {'id': 'chatcmpl-1', 'choices': [{'message': {'content': 'hello'}}]}

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    chunks = [
        chunk
        async for chunk in authority.stream_model_call(
            request,
            ModelCallRequest(
                run_id=run.id,
                participant_id='leader',
                model_call_id='call-stream-reasoning',
                model='model-a',
                messages=[{'role': 'user', 'content': 'hello'}],
                stream=True,
                params={
                    'temperature': 0.2,
                    'reasoning': {
                        'enabled': True,
                        'effort': 'xhigh',
                        'max_tokens': 12400,
                    },
                },
                idempotency_key='model:leader:call-stream-reasoning:1',
            ),
        )
    ]

    assert chunks
    assert captured['form_data']['stream'] is True
    assert captured['form_data']['params'] == {'temperature': 0.2}
    assert captured['form_data']['reasoning'] == {
        'enabled': True,
        'effort': 'xhigh',
        'max_tokens': 12400,
    }


@pytest.mark.asyncio
async def test_stream_model_call_preserves_phase_extension_in_raw_sse(agent_run_db):
    from starlette.responses import StreamingResponse

    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)

    async def completion_handler(request, form_data, user):
        async def body():
            yield (
                'data: {"choices":[{"delta":{"content":"Checking.",'
                '"phase":"commentary"}}]}\n\n'
            )
            yield 'data: [DONE]\n\n'

        return StreamingResponse(body(), media_type='text/event-stream')

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    chunks = [
        chunk
        async for chunk in authority.stream_model_call(
            request,
            ModelCallRequest(
                run_id=run.id,
                participant_id='leader',
                model_call_id='call-stream-phase',
                model='model-a',
                messages=[{'role': 'user', 'content': 'hello'}],
                stream=True,
                idempotency_key='model:leader:call-stream-phase:1',
            ),
        )
    ]

    wire_text = b''.join(chunks).decode('utf-8')
    assert '"content":"Checking."' in wire_text
    assert '"phase":"commentary"' in wire_text
    assert 'data: [DONE]' in wire_text


@pytest.mark.asyncio
async def test_stream_model_call_emits_control_heartbeat_while_provider_is_idle(
    agent_run_db,
    monkeypatch,
):
    from starlette.responses import StreamingResponse

    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    release_provider = asyncio.Event()

    async def completion_handler(request, form_data, user):
        async def body():
            await release_provider.wait()
            yield 'data: {"choices":[{"delta":{"content":"ready"}}]}\n\n'
            yield 'data: [DONE]\n\n'

        return StreamingResponse(body(), media_type='text/event-stream')

    monkeypatch.setattr(
        'open_webui.agent.model_authority.AGENT_MODEL_STREAM_HEARTBEAT_SECONDS',
        0.01,
    )
    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )
    stream = authority.stream_model_call(
        request,
        ModelCallRequest(
            run_id=run.id,
            participant_id='leader',
            model_call_id='call-stream-heartbeat',
            model='model-a',
            messages=[{'role': 'user', 'content': 'hello'}],
            stream=True,
            idempotency_key='model:leader:call-stream-heartbeat:1',
        ),
    )

    first = await asyncio.wait_for(anext(stream), timeout=0.2)
    second = await asyncio.wait_for(anext(stream), timeout=0.2)
    assert first == b': openwebui-stream-start\n\n'
    assert second == b': openwebui-keep-alive\n\n'

    release_provider.set()
    remaining = [chunk async for chunk in stream]
    wire_text = b''.join(remaining).decode('utf-8')
    assert '"content":"ready"' in wire_text
    assert 'data: [DONE]' in wire_text


@pytest.mark.asyncio
async def test_stream_model_call_rejects_clean_eof_without_terminal_event(agent_run_db):
    from starlette.responses import StreamingResponse

    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    call = ModelCallRequest(
        run_id=run.id,
        participant_id='leader',
        model_call_id='call-stream-incomplete',
        model='model-a',
        messages=[{'role': 'user', 'content': 'hello'}],
        stream=True,
        idempotency_key='model:leader:call-stream-incomplete:1',
    )

    async def completion_handler(request, form_data, user):
        async def body():
            yield 'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'

        return StreamingResponse(body(), media_type='text/event-stream')

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    chunks = [chunk async for chunk in authority.stream_model_call(request, call)]

    assert b'partial' in b''.join(chunks)
    assert b'stream_end' not in b''.join(chunks)
    operation = await AgentRuns.find_operation_by_idempotency_key(
        run.id,
        operation_type='model.call',
        idempotency_key=call.idempotency_key,
    )
    assert operation is not None
    assert operation.status == 'failed'
    assert operation.error is not None
    assert operation.error['code'] == 'model_stream_incomplete'


@pytest.mark.asyncio
async def test_stream_model_call_commits_success_before_terminal_chunk(agent_run_db):
    from starlette.responses import StreamingResponse

    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    call = ModelCallRequest(
        run_id=run.id,
        participant_id='leader',
        model_call_id='call-stream-terminal-order',
        model='model-a',
        messages=[{'role': 'user', 'content': 'hello'}],
        stream=True,
        idempotency_key='model:leader:call-stream-terminal-order:1',
    )

    async def completion_handler(request, form_data, user):
        async def body():
            yield 'data: {"choices":[{"delta":{"content":"complete"}}]}\n\n'
            yield 'data: [DONE]\n\n'

        return StreamingResponse(body(), media_type='text/event-stream')

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )
    stream = authority.stream_model_call(request, call)

    try:
        assert await anext(stream) == b': openwebui-stream-start\n\n'
        assert b'complete' in await anext(stream)
        terminal = await anext(stream)
        assert terminal == b'data: [DONE]\n\n'

        operation = await AgentRuns.find_operation_by_idempotency_key(
            run.id,
            operation_type='model.call',
            idempotency_key=call.idempotency_key,
        )
        assert operation is not None
        assert operation.status == 'succeeded'
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_stream_model_call_accepts_responses_completed_terminal_event(agent_run_db):
    from starlette.responses import StreamingResponse

    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    call = ModelCallRequest(
        run_id=run.id,
        participant_id='leader',
        model_call_id='call-responses-completed',
        model='model-a',
        messages=[{'role': 'user', 'content': 'hello'}],
        stream=True,
        idempotency_key='model:leader:call-responses-completed:1',
    )

    async def completion_handler(request, form_data, user):
        async def body():
            yield 'data: {"type":"response.output_text.delta","delta":"answer"}\n\n'
            yield 'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'

        return StreamingResponse(body(), media_type='text/event-stream')

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    chunks = [chunk async for chunk in authority.stream_model_call(request, call)]

    wire_text = b''.join(chunks).decode('utf-8')
    assert 'response.output_text.delta' in wire_text
    assert 'response.completed' in wire_text
    operation = await AgentRuns.find_operation_by_idempotency_key(
        run.id,
        operation_type='model.call',
        idempotency_key=call.idempotency_key,
    )
    assert operation is not None
    assert operation.status == 'succeeded'
    assert operation.error is None


@pytest.mark.asyncio
async def test_stream_model_call_marks_responses_incomplete_failed(agent_run_db):
    from starlette.responses import StreamingResponse

    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    call = ModelCallRequest(
        run_id=run.id,
        participant_id='leader',
        model_call_id='call-responses-incomplete',
        model='model-a',
        messages=[{'role': 'user', 'content': 'hello'}],
        stream=True,
        idempotency_key='model:leader:call-responses-incomplete:1',
    )

    async def completion_handler(request, form_data, user):
        async def body():
            yield (
                'data: {"type":"response.incomplete","response":'
                '{"incomplete_details":{"reason":"max_output_tokens"}}}\n\n'
            )
            yield 'data: {"type":"response.completed"}\n\n'

        return StreamingResponse(body(), media_type='text/event-stream')

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    chunks = [chunk async for chunk in authority.stream_model_call(request, call)]

    wire_text = b''.join(chunks).decode('utf-8')
    assert 'response.incomplete' in wire_text
    assert 'response.completed' not in wire_text
    operation = await AgentRuns.find_operation_by_idempotency_key(
        run.id,
        operation_type='model.call',
        idempotency_key=call.idempotency_key,
    )
    assert operation is not None
    assert operation.status == 'failed'
    assert operation.error is not None
    assert operation.error['code'] == 'model_stream_error'
    assert operation.error['message'] == 'max_output_tokens'


@pytest.mark.asyncio
async def test_operation_terminal_state_is_first_writer_wins(agent_run_db):
    run = await _create_running_run()
    claim = await AgentRuns.claim_operation(
        run.id,
        operation_type='model.call',
        idempotency_key='model:leader:call-terminal-state:1',
        request_hash='same-request-hash',
    )

    await AgentRuns.finish_operation_success(claim.operation.id, {'ok': True})
    late_failure = await AgentRuns.finish_operation_error(
        claim.operation.id,
        {'code': 'late_cleanup', 'message': 'must not overwrite success'},
    )

    assert late_failure.status == 'succeeded'
    assert late_failure.response == {'ok': True}
    assert late_failure.error is None


@pytest.mark.asyncio
async def test_operation_terminal_result_refreshes_stale_caller_session(agent_run_db):
    run = await _create_running_run()
    claim = await AgentRuns.claim_operation(
        run.id,
        operation_type='model.call',
        idempotency_key='model:leader:call-terminal-refresh:1',
        request_hash='same-request-hash',
    )

    async with agent_run_db() as stale_session:
        stale_row = await stale_session.get(AgentRunOperation, claim.operation.id)
        assert stale_row is not None
        assert stale_row.status == 'in_progress'

        await AgentRuns.finish_operation_success(claim.operation.id, {'ok': True})
        late_failure = await AgentRuns.finish_operation_error(
            claim.operation.id,
            {'code': 'late_cleanup', 'message': 'must not overwrite success'},
            db=stale_session,
        )

    assert late_failure.status == 'succeeded'
    assert late_failure.response == {'ok': True}
    assert late_failure.error is None


@pytest.mark.asyncio
async def test_model_call_endpoint_rejects_forged_service_callback_without_token(agent_run_db):
    run = await _create_running_run()
    request = _request(enable_agent_mode=True)
    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=_unexpected_completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    with pytest.raises(HTTPException) as exc_info:
        await execute_agent_run_model_call(
            request,
            run.id,
            ModelCallRequest(
                run_id=run.id,
                participant_id='leader',
                model_call_id='call-1',
                model='model-a',
                messages=[{'role': 'user', 'content': 'hello'}],
                metadata={'agent_internal_model_call': True},
                idempotency_key='model:leader:call-1:1',
            ),
            idempotency_key='model:leader:call-1:1',
            authorization=None,
            authority=authority,
        )

    assert exc_info.value.status_code == 401
    assert not getattr(request.state, 'agent_internal_model_call', False)


@pytest.mark.asyncio
async def test_model_call_stream_endpoint_reuses_single_preflight(agent_run_db):
    run = await _create_running_run()
    request = _request(enable_agent_mode=True)
    counts = {'get_run': 0, 'model_access': 0, 'provider': 0}

    class CountingOperationStore:
        async def get_run(self, run_id):
            counts['get_run'] += 1
            return await AgentRuns.get_run(run_id)

        async def claim_operation(self, *args, **kwargs):
            return await AgentRuns.claim_operation(*args, **kwargs)

        async def finish_operation_success(self, *args, **kwargs):
            return await AgentRuns.finish_operation_success(*args, **kwargs)

        async def finish_operation_error(self, *args, **kwargs):
            return await AgentRuns.finish_operation_error(*args, **kwargs)

    async def model_access_checker(user, model):
        counts['model_access'] += 1

    async def completion_handler(request, form_data, user):
        counts['provider'] += 1
        return {'id': 'chatcmpl-stream', 'choices': [{'message': {'content': 'hello'}}]}

    authority = AgentModelAuthority(
        operation_store=CountingOperationStore(),
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=model_access_checker,
    )

    response = await execute_agent_run_model_call(
        request,
        run.id,
        ModelCallRequest(
            run_id=run.id,
            participant_id='leader',
            model_call_id='call-stream',
            model='model-a',
            messages=[{'role': 'user', 'content': 'hello'}],
            stream=True,
            idempotency_key='model:leader:call-stream:1',
        ),
        idempotency_key='model:leader:call-stream:1',
        authorization='Bearer test-service-token',
        authority=authority,
    )
    chunks = [chunk async for chunk in response.body_iterator]

    assert chunks
    assert counts == {'get_run': 1, 'model_access': 1, 'provider': 1}


@pytest.mark.asyncio
async def test_model_call_stream_endpoint_fails_claim_if_response_never_starts(agent_run_db):
    from starlette.requests import ClientDisconnect

    run = await _create_running_run()
    request = _request(enable_agent_mode=True)
    call = ModelCallRequest(
        run_id=run.id,
        participant_id='leader',
        model_call_id='call-stream-not-started',
        model='model-a',
        messages=[{'role': 'user', 'content': 'hello'}],
        stream=True,
        idempotency_key='model:leader:call-stream-not-started:1',
    )
    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=_unexpected_completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    response = await execute_agent_run_model_call(
        request,
        run.id,
        call,
        idempotency_key=call.idempotency_key,
        authorization='Bearer test-service-token',
        authority=authority,
    )

    async def receive():
        return {'type': 'http.disconnect'}

    async def send(message):
        del message
        raise OSError('client disconnected before response start')

    with pytest.raises(ClientDisconnect):
        await response(
            {'type': 'http', 'asgi': {'spec_version': '2.4'}},
            receive,
            send,
        )

    operation = await AgentRuns.find_operation_by_idempotency_key(
        run.id,
        operation_type='model.call',
        idempotency_key=call.idempotency_key,
    )
    assert operation is not None
    assert operation.status == 'failed'
    assert operation.error is not None
    assert operation.error['code'] == 'model_stream_abandoned'


@pytest.mark.asyncio
async def test_model_call_stream_endpoint_cleanup_preserves_success(agent_run_db):
    from starlette.responses import StreamingResponse

    run = await _create_running_run()
    request = _request(enable_agent_mode=True)
    call = ModelCallRequest(
        run_id=run.id,
        participant_id='leader',
        model_call_id='call-stream-cleanup-after-success',
        model='model-a',
        messages=[{'role': 'user', 'content': 'hello'}],
        stream=True,
        idempotency_key='model:leader:call-stream-cleanup-after-success:1',
    )

    async def completion_handler(request, form_data, user):
        async def body():
            yield 'data: {"choices":[{"delta":{"content":"complete"}}]}\n\n'
            yield 'data: [DONE]\n\n'

        return StreamingResponse(body(), media_type='text/event-stream')

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )
    response = await execute_agent_run_model_call(
        request,
        run.id,
        call,
        idempotency_key=call.idempotency_key,
        authorization='Bearer test-service-token',
        authority=authority,
    )
    sent_messages = []

    async def receive():
        return {'type': 'http.disconnect'}

    async def send(message):
        sent_messages.append(message)

    await response(
        {'type': 'http', 'asgi': {'spec_version': '2.4'}},
        receive,
        send,
    )

    assert sent_messages[-1] == {'type': 'http.response.body', 'body': b'', 'more_body': False}
    operation = await AgentRuns.find_operation_by_idempotency_key(
        run.id,
        operation_type='model.call',
        idempotency_key=call.idempotency_key,
    )
    assert operation is not None
    assert operation.status == 'succeeded'
    assert operation.error is None


@pytest.mark.asyncio
async def test_stream_model_call_claims_once_and_never_reposts_provider(agent_run_db):
    run = await _create_running_run()
    provider_calls = 0

    async def completion_handler(request, form_data, user):
        nonlocal provider_calls
        provider_calls += 1
        return {'id': 'chatcmpl-stream', 'choices': [{'message': {'content': 'hello'}}]}

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )
    call = ModelCallRequest(
        run_id=run.id,
        participant_id='leader',
        model_call_id='call-stream-once',
        model='model-a',
        messages=[{'role': 'user', 'content': 'hello'}],
        stream=True,
        idempotency_key='model:leader:call-stream-once:1',
    )

    first = await execute_agent_run_model_call(
        _request(enable_agent_mode=True),
        run.id,
        call,
        idempotency_key=call.idempotency_key,
        authorization='Bearer test-service-token',
        authority=authority,
    )
    duplicate_in_progress = await execute_agent_run_model_call(
        _request(enable_agent_mode=True),
        run.id,
        call,
        idempotency_key=call.idempotency_key,
        authorization='Bearer test-service-token',
        authority=authority,
    )

    assert duplicate_in_progress.status_code == 202
    assert provider_calls == 0

    chunks = [chunk async for chunk in first.body_iterator]
    assert chunks
    assert provider_calls == 1

    with pytest.raises(HTTPException) as exc_info:
        await execute_agent_run_model_call(
            _request(enable_agent_mode=True),
            run.id,
            call,
            idempotency_key=call.idempotency_key,
            authorization='Bearer test-service-token',
            authority=authority,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail['code'] == 'model_stream_not_replayable'
    assert provider_calls == 1


@pytest.mark.asyncio
async def test_concurrent_model_call_claims_resolve_unique_key_race(agent_run_db):
    run = await _create_running_run()
    start = asyncio.Event()

    async def claim():
        await start.wait()
        return await AgentRuns.claim_operation(
            run.id,
            operation_type='model.call',
            idempotency_key='model:leader:call-concurrent:1',
            request_hash='same-request-hash',
        )

    first = asyncio.create_task(claim())
    second = asyncio.create_task(claim())
    start.set()
    claims = await asyncio.gather(first, second)

    assert sorted(claim.created for claim in claims) == [False, True]
    assert claims[0].operation.id == claims[1].operation.id


@pytest.mark.asyncio
async def test_concurrent_model_call_claims_reject_different_request_hash(agent_run_db):
    run = await _create_running_run()
    start = asyncio.Event()

    async def claim(request_hash: str):
        await start.wait()
        return await AgentRuns.claim_operation(
            run.id,
            operation_type='model.call',
            idempotency_key='model:leader:call-concurrent-conflict:1',
            request_hash=request_hash,
        )

    first = asyncio.create_task(claim('request-hash-a'))
    second = asyncio.create_task(claim('request-hash-b'))
    start.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    claims = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [
        result for result in results if isinstance(result, AgentRunOperationConflict)
    ]
    assert len(claims) == 1
    assert claims[0].created is True
    assert len(conflicts) == 1

    operation = await AgentRuns.find_operation_by_idempotency_key(
        run.id,
        operation_type='model.call',
        idempotency_key='model:leader:call-concurrent-conflict:1',
    )
    assert operation is not None
    assert operation.request_hash in {'request-hash-a', 'request-hash-b'}


@pytest.mark.asyncio
async def test_stream_model_call_marks_provider_sse_error_failed(agent_run_db):
    from starlette.responses import StreamingResponse

    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    call = ModelCallRequest(
        run_id=run.id,
        participant_id='leader',
        model_call_id='call-stream-provider-error',
        model='model-a',
        messages=[{'role': 'user', 'content': 'hello'}],
        stream=True,
        idempotency_key='model:leader:call-stream-provider-error:1',
    )

    async def completion_handler(request, form_data, user):
        async def body():
            yield b'data: {"error":{"mess'
            yield b'age":"provider failed"}}\n\n'
            yield b'data: [DONE]\n\n'

        return StreamingResponse(body(), media_type='text/event-stream')

    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    chunks = [chunk async for chunk in authority.stream_model_call(request, call)]

    assert b''.join(chunks).count(b'provider failed') == 1
    assert b'"type":"stream_end"' not in b''.join(chunks)
    operation = await AgentRuns.find_operation_by_idempotency_key(
        run.id,
        operation_type='model.call',
        idempotency_key=call.idempotency_key,
    )
    assert operation is not None
    assert operation.status == 'failed'
    assert operation.error == {
        'code': 'model_stream_error',
        'message': 'provider failed',
    }


@pytest.mark.asyncio
async def test_stream_model_call_marks_operation_failed_when_consumer_closes(agent_run_db):
    run = await _create_running_run()
    request = _trusted_request(enable_agent_mode=True, run_id=run.id)
    call = ModelCallRequest(
        run_id=run.id,
        participant_id='leader',
        model_call_id='call-stream-closed',
        model='model-a',
        messages=[{'role': 'user', 'content': 'hello'}],
        stream=True,
        idempotency_key='model:leader:call-stream-closed:1',
    )
    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=_unexpected_completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )
    prepared = await authority.prepare_stream_model_call(request, call)
    stream = authority.stream_model_call(request, call, prepared=prepared)

    assert await anext(stream) == b': openwebui-stream-start\n\n'
    await stream.aclose()

    operation = await AgentRuns.find_operation_by_idempotency_key(
        run.id,
        operation_type='model.call',
        idempotency_key=call.idempotency_key,
    )
    assert operation is not None
    assert operation.status == 'failed'
    assert operation.error is not None
    assert operation.error['code'] == 'model_stream_closed'


@pytest.mark.asyncio
async def test_model_call_endpoint_rejects_queued_run_with_state_diagnostics(agent_run_db):
    run = await _create_queued_run()
    request = _request(enable_agent_mode=True)
    authority = AgentModelAuthority(
        operation_store=AgentRuns,
        completion_handler=_unexpected_completion_handler,
        user_loader=_user_loader,
        model_access_checker=_allow_model_access,
    )

    with pytest.raises(HTTPException) as exc_info:
        await execute_agent_run_model_call(
            request,
            run.id,
            ModelCallRequest(
                run_id=run.id,
                participant_id='leader',
                model_call_id='call-1',
                model='model-a',
                messages=[{'role': 'user', 'content': 'hello'}],
                stream=True,
                idempotency_key='model:leader:call-1:1',
            ),
            idempotency_key='model:leader:call-1:1',
            authorization='Bearer test-service-token',
            authority=authority,
        )

    detail = exc_info.value.detail
    assert exc_info.value.status_code == 403
    assert detail['code'] == 'model_run_rejected'
    assert detail['message']
    assert detail['current_state'] == 'queued'


async def _create_queued_run():
    return await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='user-msg',
        assistant_message_id='assistant-msg',
        leader_model_id='model-a',
    )


async def _create_running_run():
    run = await AgentRuns.create_run(
        user_id='user-1',
        chat_id='chat-1',
        user_message_id='user-msg',
        assistant_message_id='assistant-msg',
        leader_model_id='model-a',
    )
    return await AgentRuns.transition_state(
        run.id,
        from_states=['queued'],
        to_state='running',
        reason='runtime accepted',
    )


def _request(*, enable_agent_mode: bool):
    config = SimpleNamespace(
        ENABLE_AGENT_MODE=enable_agent_mode,
        AGENT_RUNTIME_SERVICE_TOKEN='test-service-token',
    )
    model = {
        'id': 'model-a',
        'name': 'Model A',
        'owned_by': 'openai',
        'info': {'meta': {}},
    }
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={'model-a': model},
                config=config,
            )
        ),
        state=SimpleNamespace(),
        headers={},
    )


def _trusted_request(*, enable_agent_mode: bool, run_id: str):
    request = _request(enable_agent_mode=enable_agent_mode)
    request.state.agent_internal_model_call = True
    request.state.agent_run_id = run_id
    request.state.agent_service_principal = 'agentscope-runtime'
    return request


async def _user_loader(user_id):
    return SimpleNamespace(id=user_id, role='user')


async def _admin_user_loader(user_id):
    return SimpleNamespace(id=user_id, role='admin')


async def _allow_model_access(user, model):
    return None


async def _unexpected_completion_handler(request, form_data, user):
    raise AssertionError('model provider should not be called')
