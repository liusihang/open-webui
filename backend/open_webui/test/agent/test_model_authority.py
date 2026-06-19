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
