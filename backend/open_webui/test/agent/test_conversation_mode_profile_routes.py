from __future__ import annotations

import asyncio
import importlib
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')
os.environ.setdefault('DATABASE_ENABLE_SESSION_SHARING', 'true')

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from open_webui.agent import conversation_mode_profile_service as profile_service
from open_webui.agent.conversation_mode_profiles import ConversationModeProfile
from open_webui.internal.db import Base
from open_webui.models import conversation_mode_profiles as profile_store_module
from open_webui.models.access_grants import AccessGrants
from open_webui.models.config import Config
from open_webui.models.conversation_mode_profiles import (
    AGENT_BASELINE_REVISION_ID,
    CHAT_BASELINE_REVISION_ID,
    ConversationModeProfileHead,
    ConversationModeProfileIntegrityError,
    ConversationModeProfileRevision,
    ConversationModeProfileRevisionConflict,
    ConversationModeProfiles,
)
from open_webui.models.functions import Function
from open_webui.models.skills import Skill
from open_webui.models.tools import Tool
from open_webui.routers import configs
from open_webui.utils.auth import get_current_user
from sqlalchemy import delete, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BASE_PATH = '/api/v1/configs/conversation_mode_profiles'
BASELINE_CONTENT = {
    'schema_version': 1,
    'system_prompt': '',
    'defaults': {},
}


class FakeRedis:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.values: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def get(self, key):
        if self.fail:
            raise RuntimeError('redis unavailable')
        return self.values.get(key)

    async def incr(self, key):
        if self.fail:
            raise RuntimeError('redis unavailable')
        value = int(self.values.get(key, '0')) + 1
        self.values[key] = str(value)
        return value

    async def publish(self, channel, payload):
        if self.fail:
            raise RuntimeError('redis unavailable')
        self.published.append((channel, payload))


@pytest_asyncio.fixture
async def profile_db(monkeypatch, tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "profile-routes.sqlite3"}')
    tables = [
        ConversationModeProfileRevision.__table__,
        ConversationModeProfileHead.__table__,
        Tool.__table__,
        Skill.__table__,
        Function.__table__,
        Config.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=tables,
            )
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        for mode, revision_id in (
            ('chat', CHAT_BASELINE_REVISION_ID),
            ('agent', AGENT_BASELINE_REVISION_ID),
        ):
            profile = ConversationModeProfile.from_mapping(mode, BASELINE_CONTENT)
            session.add(
                ConversationModeProfileRevision(
                    id=revision_id,
                    mode=mode,
                    revision_number=1,
                    schema_version=profile.schema_version,
                    system_prompt=profile.system_prompt,
                    defaults=profile.defaults.to_dict(),
                    content_hash=profile.content_hash,
                    created_at=100,
                    created_by=None,
                    restored_from_revision_id=None,
                )
            )
            session.add(
                ConversationModeProfileHead(
                    mode=mode,
                    current_revision_id=revision_id,
                    baseline_revision_id=revision_id,
                    cutover_at=100,
                    updated_at=100,
                    updated_by=None,
                )
            )
        session.add(
            Tool(
                id='tool-1',
                user_id='admin-1',
                name='Tool One',
                content='def tool(): pass',
                specs=[],
                meta={},
                valves={},
                updated_at=100,
                created_at=100,
            )
        )
        session.add(
            Skill(
                id='skill-1',
                user_id='admin-1',
                name='Skill One',
                description='Skill',
                content='Skill content',
                meta={},
                is_active=True,
                updated_at=100,
                created_at=100,
            )
        )
        session.add(
            Function(
                id='filter-1',
                user_id='admin-1',
                name='Filter One',
                type='filter',
                content='def inlet(body): return body',
                meta={},
                valves={},
                is_active=True,
                is_global=False,
                updated_at=100,
                created_at=100,
            )
        )
        session.add(
            Config(
                key='terminal_server.connections',
                value=[
                    {
                        'id': 'terminal-1',
                        'name': 'Terminal One',
                        'url': 'http://terminal.invalid',
                        'enabled': True,
                    }
                ],
                updated_at=100,
            )
        )
        await session.commit()

    @asynccontextmanager
    async def isolated_session(db=None):
        if db is not None:
            yield db
            return
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(profile_store_module, 'get_async_db_context', isolated_session)
    monkeypatch.setattr(profile_service, 'get_async_db_context', isolated_session, raising=False)
    yield session_factory
    await engine.dispose()


@pytest.fixture
def resource_truth(monkeypatch):
    state = {
        'feature_flags': {
            'web.search.enable': True,
            'code_interpreter.enable': True,
            'image_generation.enable': True,
        },
    }

    async def config_get_many(*keys):
        return {key: state['feature_flags'].get(key) for key in keys}

    monkeypatch.setattr(Config, 'get_many', config_get_many)
    return state


@pytest.fixture
def route_app(profile_db, resource_truth, monkeypatch):
    app = FastAPI()
    app.state.redis = FakeRedis()
    app.state.CONVERSATION_MODE_PROFILE_HEADS = {}
    app.state.CONVERSATION_MODE_PROFILE_REVISIONS = {}
    app.state.MODELS = {
        'model-1': {
            'id': 'model-1',
            'info': {
                'meta': {
                    'capabilities': {
                        'function_calling': True,
                        'web_search': True,
                        'code_interpreter': True,
                        'image_generation': True,
                    }
                }
            },
        }
    }
    app.include_router(configs.router, prefix='/api/v1/configs')

    current_user = {
        'value': SimpleNamespace(
            id='admin-1',
            role='admin',
            name='Administrator',
            email='admin@example.com',
        )
    }

    def override_current_user():
        return current_user['value']

    app.dependency_overrides[get_current_user] = override_current_user
    events = []

    async def capture_event(request_or_app, event, **kwargs):
        events.append({'event': event, **kwargs})

    monkeypatch.setattr(configs, 'publish_event', capture_event)
    return SimpleNamespace(
        app=app,
        client=TestClient(app),
        current_user=current_user,
        events=events,
        profile_db=profile_db,
        resource_truth=resource_truth,
    )


def _profile_content(
    *,
    prompt: str = 'Administrator policy',
    terminal_id='terminal-1',
    tool_ids=None,
    skill_ids=None,
    filter_ids=None,
    feature_ids=None,
):
    return {
        'schema_version': 1,
        'system_prompt': prompt,
        'defaults': {
            'terminal_id': terminal_id,
            'tool_ids': ['tool-1'] if tool_ids is None else tool_ids,
            'skill_ids': ['skill-1'] if skill_ids is None else skill_ids,
            'filter_ids': ['filter-1'] if filter_ids is None else filter_ids,
            'feature_ids': ['web_search'] if feature_ids is None else feature_ids,
        },
    }


def _save_payload(expected_revision_id: str, **content_overrides):
    return {
        'expected_current_revision_id': expected_revision_id,
        'profile': _profile_content(**content_overrides),
    }


@pytest.mark.parametrize(
    ('method', 'path', 'payload'),
    [
        ('get', BASE_PATH, None),
        ('get', f'{BASE_PATH}/chat', None),
        ('post', f'{BASE_PATH}/chat/revisions', _save_payload(CHAT_BASELINE_REVISION_ID)),
        ('get', f'{BASE_PATH}/chat/revisions', None),
        ('get', f'{BASE_PATH}/chat/revisions/{CHAT_BASELINE_REVISION_ID}', None),
        (
            'post',
            f'{BASE_PATH}/chat/revisions/{CHAT_BASELINE_REVISION_ID}/restore',
            {'expected_current_revision_id': CHAT_BASELINE_REVISION_ID},
        ),
    ],
)
def test_private_profile_operations_reject_non_admins(route_app, method, path, payload):
    route_app.current_user['value'] = SimpleNamespace(id='user-1', role='user')

    kwargs = {'json': payload} if payload is not None else {}
    response = getattr(route_app.client, method)(path, **kwargs)

    assert response.status_code == 401


def test_admin_reads_complete_current_chat_and_agent_profiles(route_app):
    response = route_app.client.get(BASE_PATH)

    assert response.status_code == 200
    body = response.json()
    assert [profile['mode'] for profile in body['profiles']] == ['agent', 'chat']
    assert all(profile['is_current'] is True for profile in body['profiles'])
    assert all('system_prompt' in profile for profile in body['profiles'])
    assert {profile['revision_id'] for profile in body['profiles']} == {
        AGENT_BASELINE_REVISION_ID,
        CHAT_BASELINE_REVISION_ID,
    }


def test_admin_save_creates_revision_and_prompt_free_audit(route_app, monkeypatch):
    access_grant_calls = []

    async def record_access_grant(*args, **kwargs):
        access_grant_calls.append((args, kwargs))

    monkeypatch.setattr(AccessGrants, 'set_access_grants', record_access_grant)
    response = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(CHAT_BASELINE_REVISION_ID),
    )

    assert response.status_code == 200
    body = response.json()
    assert body['mode'] == 'chat'
    assert body['revision_number'] == 2
    assert body['revision_id'] != CHAT_BASELINE_REVISION_ID
    assert body['system_prompt'] == 'Administrator policy'
    assert body['created_by'] == 'admin-1'
    assert access_grant_calls == []

    assert len(route_app.events) == 1
    event = route_app.events[0]
    assert event['actor'].id == 'admin-1'
    assert event['subject_id'] == body['revision_id']
    assert set(event['data']) == {
        'mode',
        'previous_revision_id',
        'revision_id',
        'counts',
        'warning_codes',
    }
    rendered_event = json.dumps(event['data'], sort_keys=True)
    for forbidden in ('Administrator policy', 'system_prompt', 'defaults', 'content_hash'):
        assert forbidden not in rendered_event


@pytest.mark.parametrize(
    ('field', 'value', 'expected_reason'),
    [
        ('tool_ids', ['tool-1', 'tool-1'], 'duplicate_default_identifier'),
        ('skill_ids', [' skill-1'], 'invalid_default_identifier'),
        ('feature_ids', ['unsupported-feature'], 'unsupported_feature'),
    ],
)
def test_admin_save_rejects_malformed_and_duplicate_ids(route_app, field, value, expected_reason):
    payload = _save_payload(CHAT_BASELINE_REVISION_ID)
    payload['profile']['defaults'][field] = value

    response = route_app.client.post(f'{BASE_PATH}/chat/revisions', json=payload)

    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'invalid_mode_profile'
    assert response.json()['detail']['reason'] == expected_reason


def test_admin_save_rejects_phase_a_known_conflict(route_app):
    response = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(
            CHAT_BASELINE_REVISION_ID,
            terminal_id='terminal-1',
            feature_ids=['code_interpreter'],
        ),
    )

    assert response.status_code == 400
    assert response.json()['detail'] == {
        'code': 'invalid_mode_profile',
        'reason': 'terminal_code_interpreter_conflict',
        'field': 'defaults',
    }


@pytest.mark.parametrize('schema_version', [True, '1', 1.0])
def test_admin_save_does_not_coerce_schema_version(route_app, schema_version):
    payload = _save_payload(CHAT_BASELINE_REVISION_ID)
    payload['profile']['schema_version'] = schema_version

    response = route_app.client.post(f'{BASE_PATH}/chat/revisions', json=payload)

    assert response.status_code == 422
    assert response.json()['detail'][0]['type'] == 'int_type'


@pytest.mark.parametrize(
    ('resource_type', 'payload_overrides', 'expected_issue'),
    [
        (
            'tool',
            {'tool_ids': ['tool-1'], 'skill_ids': [], 'filter_ids': [], 'terminal_id': None},
            'missing',
        ),
        (
            'skill',
            {'tool_ids': [], 'skill_ids': ['skill-1'], 'filter_ids': [], 'terminal_id': None},
            'inactive',
        ),
        (
            'filter',
            {'tool_ids': [], 'skill_ids': [], 'filter_ids': ['filter-1'], 'terminal_id': None},
            'inactive',
        ),
        (
            'terminal',
            {'tool_ids': [], 'skill_ids': [], 'filter_ids': [], 'terminal_id': 'terminal-1'},
            'inactive',
        ),
    ],
)
def test_admin_save_rejects_missing_or_inactive_global_resources(
    route_app,
    resource_type,
    payload_overrides,
    expected_issue,
):
    async def mutate_database_truth():
        async with route_app.profile_db() as session:
            if resource_type == 'tool':
                await session.execute(delete(Tool).where(Tool.id == 'tool-1'))
            elif resource_type == 'skill':
                await session.execute(update(Skill).where(Skill.id == 'skill-1').values(is_active=False))
            elif resource_type == 'filter':
                await session.execute(update(Function).where(Function.id == 'filter-1').values(is_active=False))
            else:
                row = await session.get(Config, 'terminal_server.connections')
                row.value = [{**row.value[0], 'enabled': False}]
            await session.commit()

    asyncio.run(mutate_database_truth())

    response = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(CHAT_BASELINE_REVISION_ID, **payload_overrides),
    )

    assert response.status_code == 400
    detail = response.json()['detail']
    assert detail['code'] == 'invalid_mode_profile_resource'
    assert detail['issues'] == [
        {
            'resource_type': resource_type,
            'resource_id': f'{resource_type}-1',
            'reason': expected_issue,
        }
    ]


@pytest.mark.parametrize(
    ('resource_type', 'expected_reason'),
    [
        ('tool', 'missing'),
        ('skill', 'inactive'),
        ('filter', 'inactive'),
        ('terminal', 'inactive'),
    ],
)
def test_precommit_validation_rejects_resource_drift_after_initial_validation(
    route_app,
    monkeypatch,
    resource_type,
    expected_reason,
):
    original_lock_head = ConversationModeProfiles._lock_head

    async def lock_head_then_drift_resource(session, mode, dialect_name):
        head = await original_lock_head(session, mode, dialect_name)
        if resource_type == 'tool':
            await session.execute(delete(Tool).where(Tool.id == 'tool-1'))
        elif resource_type == 'skill':
            await session.execute(update(Skill).where(Skill.id == 'skill-1').values(is_active=False))
        elif resource_type == 'filter':
            await session.execute(update(Function).where(Function.id == 'filter-1').values(is_active=False))
        else:
            row = await session.get(Config, 'terminal_server.connections')
            row.value = [{**row.value[0], 'enabled': False}]
            await session.flush()
        return head

    monkeypatch.setattr(ConversationModeProfiles, '_lock_head', lock_head_then_drift_resource)

    response = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(CHAT_BASELINE_REVISION_ID),
    )

    assert response.status_code == 400
    assert response.json()['detail'] == {
        'code': 'invalid_mode_profile_resource',
        'issues': [
            {
                'resource_type': resource_type,
                'resource_id': f'{resource_type}-1',
                'reason': expected_reason,
            }
        ],
    }
    current = route_app.client.get(f'{BASE_PATH}/chat')
    assert current.status_code == 200
    assert current.json()['revision_id'] == CHAT_BASELINE_REVISION_ID


def test_precommit_database_failure_is_service_unavailable(route_app, monkeypatch):
    original_lock_head = ConversationModeProfiles._lock_head

    async def lock_head_then_break_tool_query(session, mode, dialect_name):
        head = await original_lock_head(session, mode, dialect_name)
        original_execute = session.execute

        async def fail_tool_query(statement, *args, **kwargs):
            if 'FROM tool' in str(statement):
                raise OperationalError(str(statement), {}, RuntimeError('database unavailable'))
            return await original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, 'execute', fail_tool_query)
        return head

    monkeypatch.setattr(ConversationModeProfiles, '_lock_head', lock_head_then_break_tool_query)

    response = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(
            CHAT_BASELINE_REVISION_ID,
            terminal_id=None,
            skill_ids=[],
            filter_ids=[],
            feature_ids=[],
        ),
    )

    assert response.status_code == 503
    assert response.json()['detail'] == {
        'code': 'mode_profile_service_unavailable',
        'operation': 'precommit_resource_validation',
        'mode': 'chat',
    }


def test_initial_database_failure_is_service_unavailable(route_app, monkeypatch):
    @asynccontextmanager
    async def failing_resource_session(db=None):
        async with route_app.profile_db() as session:
            original_execute = session.execute

            async def fail_tool_query(statement, *args, **kwargs):
                if 'FROM tool' in str(statement):
                    raise OperationalError(str(statement), {}, RuntimeError('database unavailable'))
                return await original_execute(statement, *args, **kwargs)

            monkeypatch.setattr(session, 'execute', fail_tool_query)
            yield session

    monkeypatch.setattr(
        profile_service,
        'get_async_db_context',
        failing_resource_session,
        raising=False,
    )

    response = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(
            CHAT_BASELINE_REVISION_ID,
            terminal_id=None,
            skill_ids=[],
            filter_ids=[],
            feature_ids=[],
        ),
    )

    assert response.status_code == 503
    assert response.json()['detail'] == {
        'code': 'mode_profile_service_unavailable',
        'operation': 'initial_resource_validation',
        'mode': 'chat',
    }


def test_disabled_features_and_model_mismatch_are_structured_warnings(route_app):
    route_app.resource_truth['feature_flags']['web.search.enable'] = False
    route_app.app.state.MODELS['model-1']['info']['meta']['capabilities']['web_search'] = False

    response = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(
            CHAT_BASELINE_REVISION_ID,
            terminal_id=None,
            tool_ids=[],
            skill_ids=[],
            filter_ids=[],
            feature_ids=['web_search'],
        ),
    )

    assert response.status_code == 200
    warnings = response.json()['warnings']
    assert {warning['code'] for warning in warnings} == {
        'feature_globally_disabled',
        'model_compatibility_warning',
    }
    assert response.json()['defaults']['feature_ids'] == ['web_search']


def test_stale_save_returns_stable_conflict_with_refresh_metadata(route_app):
    first = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(CHAT_BASELINE_REVISION_ID, prompt='First'),
    )
    assert first.status_code == 200

    stale = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(CHAT_BASELINE_REVISION_ID, prompt='Stale'),
    )

    assert stale.status_code == 409
    detail = stale.json()['detail']
    assert detail['code'] == 'mode_profile_revision_conflict'
    assert detail['mode'] == 'chat'
    assert detail['expected_current_revision_id'] == CHAT_BASELINE_REVISION_ID
    assert detail['current_revision'] == {
        'revision_id': first.json()['revision_id'],
        'revision_number': 2,
        'schema_version': 1,
        'created_at': first.json()['created_at'],
    }


def test_conflict_refreshes_a_stale_local_head_cache(route_app):
    primed = route_app.client.get(f'{BASE_PATH}/chat')
    assert primed.status_code == 200
    assert primed.json()['revision_id'] == CHAT_BASELINE_REVISION_ID

    external = asyncio.run(
        ConversationModeProfiles.save_revision(
            mode='chat',
            content={
                'schema_version': 1,
                'system_prompt': 'External writer',
                'defaults': {},
            },
            expected_current_revision_id=CHAT_BASELINE_REVISION_ID,
            created_by='admin-2',
        )
    )

    conflict = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(CHAT_BASELINE_REVISION_ID, prompt='Stale writer'),
    )
    assert conflict.status_code == 409
    assert conflict.json()['detail']['current_revision']['revision_id'] == external.id

    refreshed = route_app.client.get(f'{BASE_PATH}/chat')
    assert refreshed.status_code == 200
    assert refreshed.json()['revision_id'] == external.id
    assert refreshed.json()['system_prompt'] == 'External writer'


@pytest.mark.parametrize(
    ('path', 'row_type', 'malformed_values'),
    [
        (f'{BASE_PATH}/chat', 'head', {'updated_at': 'PRIVATE INVALID TIMESTAMP'}),
        (
            f'{BASE_PATH}/chat',
            'revision',
            {'created_by': b'\xffPRIVATE INVALID AUTHOR'},
        ),
        (
            f'{BASE_PATH}/chat/revisions',
            'head',
            {'updated_by': b'\xffPRIVATE INVALID AUTHOR'},
        ),
        (
            f'{BASE_PATH}/chat/revisions',
            'revision',
            {'created_at': 'PRIVATE INVALID TIMESTAMP'},
        ),
        (
            f'{BASE_PATH}/chat/revisions',
            'revision',
            {'defaults': ['PRIVATE INVALID DEFAULTS']},
        ),
    ],
)
def test_malformed_persisted_profile_rows_return_controlled_integrity_error(
    route_app,
    path,
    row_type,
    malformed_values,
):
    async def corrupt_row():
        model = ConversationModeProfileHead if row_type == 'head' else ConversationModeProfileRevision
        predicate = (
            ConversationModeProfileHead.mode == 'chat'
            if row_type == 'head'
            else ConversationModeProfileRevision.id == CHAT_BASELINE_REVISION_ID
        )
        async with route_app.profile_db() as session:
            await session.execute(update(model).where(predicate).values(**malformed_values))
            await session.commit()

    asyncio.run(corrupt_row())
    response = TestClient(route_app.app, raise_server_exceptions=False).get(path)

    assert response.status_code == 500
    assert response.json()['detail'] == {
        'code': 'mode_profile_integrity_error',
        'revision_id': CHAT_BASELINE_REVISION_ID,
    }
    assert 'PRIVATE INVALID' not in response.text
    assert 'system_prompt' not in response.text


@pytest.mark.parametrize('operation', ['save', 'restore'])
@pytest.mark.parametrize('failure', ['database', 'service', 'integrity'])
def test_conflict_refresh_failure_is_controlled_503(route_app, monkeypatch, operation, failure):
    conflict_revision_id = 'conflict-current-revision'
    raw_sentinel = 'PRIVATE CONFLICT REFRESH DETAIL'
    original_get_cached_revision = configs.get_cached_revision

    async def raise_conflict(**kwargs):
        raise ConversationModeProfileRevisionConflict(
            mode='chat',
            expected_revision_id=CHAT_BASELINE_REVISION_ID,
            actual_revision_id=conflict_revision_id,
        )

    async def fail_only_conflict_refresh(app, revision_id, *, expected_mode=None):
        if revision_id != conflict_revision_id:
            return await original_get_cached_revision(
                app,
                revision_id,
                expected_mode=expected_mode,
            )
        if failure == 'database':
            raise OperationalError('SELECT private conflict state', {}, RuntimeError(raw_sentinel))
        if failure == 'service':
            raise profile_service.ModeProfileServiceUnavailableError(
                'read_revision',
                mode='chat',
            )
        raise ConversationModeProfileIntegrityError(
            conflict_revision_id,
            raw_sentinel,
        )

    monkeypatch.setattr(
        ConversationModeProfiles,
        'save_revision' if operation == 'save' else 'restore_revision',
        raise_conflict,
    )
    monkeypatch.setattr(configs, 'get_cached_revision', fail_only_conflict_refresh)
    client = TestClient(route_app.app, raise_server_exceptions=False)

    if operation == 'save':
        response = client.post(
            f'{BASE_PATH}/chat/revisions',
            json=_save_payload(CHAT_BASELINE_REVISION_ID, prompt='PRIVATE PROMPT SENTINEL'),
        )
    else:
        response = client.post(
            f'{BASE_PATH}/chat/revisions/{CHAT_BASELINE_REVISION_ID}/restore',
            json={'expected_current_revision_id': CHAT_BASELINE_REVISION_ID},
        )

    assert response.status_code == 503
    if failure == 'integrity':
        assert response.json()['detail'] == {
            'code': 'mode_profile_integrity_error',
            'revision_id': conflict_revision_id,
        }
    else:
        detail = response.json()['detail']
        assert detail['code'] == 'mode_profile_service_unavailable'
        assert detail['mode'] == 'chat'
        assert detail['operation'] == ('conflict_refresh' if failure == 'database' else 'read_revision')
    assert raw_sentinel not in response.text
    assert 'PRIVATE PROMPT SENTINEL' not in response.text


@pytest.mark.parametrize('operation', ['save', 'restore'])
@pytest.mark.parametrize('failure_phase', ['head_lock', 'flush', 'commit'])
def test_persistence_sqlalchemy_failure_is_controlled_503(
    route_app,
    monkeypatch,
    operation,
    failure_phase,
):
    raw_sentinel = 'PRIVATE DATABASE FAILURE DETAIL'

    async def fail_head_lock(session, mode, dialect_name):
        raise OperationalError('SELECT private head', {}, RuntimeError(raw_sentinel))

    async def fail_flush(self, objects=None):
        raise OperationalError('INSERT private revision', {}, RuntimeError(raw_sentinel))

    async def fail_commit(self):
        raise OperationalError('COMMIT private revision', {}, RuntimeError(raw_sentinel))

    if failure_phase == 'head_lock':
        monkeypatch.setattr(ConversationModeProfiles, '_lock_head', fail_head_lock)
    elif failure_phase == 'flush':
        monkeypatch.setattr(AsyncSession, 'flush', fail_flush)
    else:
        monkeypatch.setattr(AsyncSession, 'commit', fail_commit)

    client = TestClient(route_app.app, raise_server_exceptions=False)
    if operation == 'save':
        response = client.post(
            f'{BASE_PATH}/chat/revisions',
            json=_save_payload(CHAT_BASELINE_REVISION_ID, prompt='PRIVATE PROMPT SENTINEL'),
        )
    else:
        response = client.post(
            f'{BASE_PATH}/chat/revisions/{CHAT_BASELINE_REVISION_ID}/restore',
            json={'expected_current_revision_id': CHAT_BASELINE_REVISION_ID},
        )

    assert response.status_code == 503
    assert response.json()['detail'] == {
        'code': 'mode_profile_service_unavailable',
        'operation': f'{operation}_revision',
        'mode': 'chat',
    }
    assert raw_sentinel not in response.text
    assert 'PRIVATE PROMPT SENTINEL' not in response.text
    assert asyncio.run(ConversationModeProfiles.get_head('chat')).current_revision_id == CHAT_BASELINE_REVISION_ID
    assert len(asyncio.run(ConversationModeProfiles.list_history('chat'))) == 1


def test_history_detail_and_restore_create_immutable_new_revision(route_app):
    saved = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(CHAT_BASELINE_REVISION_ID, prompt='Second revision'),
    )
    assert saved.status_code == 200
    saved_revision_id = saved.json()['revision_id']

    history = route_app.client.get(f'{BASE_PATH}/chat/revisions')
    assert history.status_code == 200
    history_body = history.json()
    assert history_body['current_revision_id'] == saved_revision_id
    assert [revision['revision_number'] for revision in history_body['revisions']] == [2, 1]
    assert 'system_prompt' not in json.dumps(history_body)
    assert 'defaults' not in json.dumps(history_body)

    baseline_detail = route_app.client.get(f'{BASE_PATH}/chat/revisions/{CHAT_BASELINE_REVISION_ID}')
    assert baseline_detail.status_code == 200
    assert baseline_detail.json()['system_prompt'] == ''
    assert baseline_detail.json()['is_current'] is False

    restored = route_app.client.post(
        f'{BASE_PATH}/chat/revisions/{CHAT_BASELINE_REVISION_ID}/restore',
        json={'expected_current_revision_id': saved_revision_id},
    )
    assert restored.status_code == 200
    assert restored.json()['revision_number'] == 3
    assert restored.json()['revision_id'] not in {
        CHAT_BASELINE_REVISION_ID,
        saved_revision_id,
    }
    assert restored.json()['restored_from_revision_id'] == CHAT_BASELINE_REVISION_ID
    assert restored.json()['system_prompt'] == ''

    old_detail = route_app.client.get(f'{BASE_PATH}/chat/revisions/{saved_revision_id}')
    assert old_detail.status_code == 200
    assert old_detail.json()['system_prompt'] == 'Second revision'
    assert old_detail.json()['restored_from_revision_id'] is None

    restore_event = route_app.events[-1]
    assert set(restore_event['data']) == {
        'mode',
        'previous_revision_id',
        'revision_id',
        'restored_from_revision_id',
        'counts',
        'warning_codes',
    }
    rendered_event = json.dumps(restore_event['data'], sort_keys=True)
    for forbidden in ('Second revision', 'system_prompt', 'defaults', 'content_hash'):
        assert forbidden not in rendered_event


def test_history_uses_one_repository_snapshot_for_head_and_revisions(route_app, monkeypatch):
    saved = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(CHAT_BASELINE_REVISION_ID, prompt='Snapshot current'),
    )
    assert saved.status_code == 200
    saved_revision_id = saved.json()['revision_id']
    revisions = asyncio.run(ConversationModeProfiles.list_history('chat'))

    async def get_history_snapshot(mode, db=None):
        return SimpleNamespace(
            head=SimpleNamespace(mode='chat', current_revision_id=saved_revision_id),
            revisions=tuple(revisions),
        )

    async def stale_cached_current(app, mode):
        return next(revision for revision in revisions if revision.id == CHAT_BASELINE_REVISION_ID)

    monkeypatch.setattr(
        ConversationModeProfiles,
        'get_history_snapshot',
        get_history_snapshot,
        raising=False,
    )
    monkeypatch.setattr(configs, 'get_cached_current_revision', stale_cached_current)

    response = route_app.client.get(f'{BASE_PATH}/chat/revisions')

    assert response.status_code == 200
    assert response.json()['current_revision_id'] == saved_revision_id
    assert [item['revision_id'] for item in response.json()['revisions'] if item['is_current']] == [saved_revision_id]


def test_cache_publication_failure_does_not_rollback_committed_revision(route_app):
    route_app.app.state.redis = FakeRedis(fail=True)

    response = route_app.client.post(
        f'{BASE_PATH}/chat/revisions',
        json=_save_payload(CHAT_BASELINE_REVISION_ID, prompt='Committed without Redis'),
    )

    assert response.status_code == 200
    saved_revision_id = response.json()['revision_id']
    current = route_app.client.get(f'{BASE_PATH}/chat')
    assert current.status_code == 200
    assert current.json()['revision_id'] == saved_revision_id
    assert current.json()['system_prompt'] == 'Committed without Redis'


@pytest.mark.asyncio
async def test_authenticated_app_config_exposes_only_sanitized_current_profiles(
    profile_db,
    resource_truth,
    monkeypatch,
):
    main = importlib.import_module('open_webui.main')

    user = SimpleNamespace(id='user-1', role='user')

    async def get_user_by_id(user_id):
        return user if user_id == user.id else None

    async def has_users():
        return True

    async def get_num_users():
        return 1

    monkeypatch.setattr(main.Users, 'get_user_by_id', get_user_by_id)
    monkeypatch.setattr(main.Users, 'has_users', has_users)
    monkeypatch.setattr(main.Users, 'get_num_users', get_num_users)
    monkeypatch.setattr(main, 'decode_token', lambda token: {'id': user.id})
    monkeypatch.setattr(
        main,
        'get_http_authorization_cred',
        lambda header: SimpleNamespace(credentials='valid-token'),
    )
    main.app.state.redis = None
    main.app.state.CONVERSATION_MODE_PROFILE_HEADS = {}
    main.app.state.CONVERSATION_MODE_PROFILE_REVISIONS = {}

    authenticated_request = SimpleNamespace(
        headers={'Authorization': 'Bearer valid-token'},
        cookies={},
        app=main.app,
    )
    authenticated = await main.get_app_config(authenticated_request)

    profiles = authenticated['conversation_mode_profiles']
    assert [profile['mode'] for profile in profiles] == ['agent', 'chat']
    assert all(
        set(profile)
        == {
            'mode',
            'current_revision_id',
            'schema_version',
            'defaults',
        }
        for profile in profiles
    )
    rendered = json.dumps(profiles, sort_keys=True)
    for forbidden in (
        'system_prompt',
        'content_hash',
        'created_by',
        'restored_from',
        'history',
        'warning',
    ):
        assert forbidden not in rendered

    anonymous_request = SimpleNamespace(headers={}, cookies={}, app=main.app)
    anonymous = await main.get_app_config(anonymous_request)
    assert 'conversation_mode_profiles' not in anonymous


@pytest.mark.asyncio
async def test_revoked_jwt_is_rejected_before_public_profile_projection(
    profile_db,
    resource_truth,
    monkeypatch,
):
    from open_webui import env

    main = importlib.import_module('open_webui.main')
    user = SimpleNamespace(id='user-1', role='user')
    revoked_jti = 'revoked-token'
    redis = FakeRedis()
    redis.values[f'{env.REDIS_KEY_PREFIX}:auth:token:{revoked_jti}:revoked'] = '1'
    profile_calls = []

    async def get_user_by_id(user_id):
        return user if user_id == user.id else None

    async def public_profiles(app):
        profile_calls.append(app)
        return []

    monkeypatch.setattr(main.Users, 'get_user_by_id', get_user_by_id)
    monkeypatch.setattr(
        main,
        'decode_token',
        lambda token: {'id': user.id, 'jti': revoked_jti, 'iat': 1},
    )
    monkeypatch.setattr(
        main,
        'get_http_authorization_cred',
        lambda header: SimpleNamespace(credentials='revoked-jwt'),
    )
    monkeypatch.setattr(main, 'get_public_conversation_mode_profiles', public_profiles)
    main.app.state.redis = redis

    request = SimpleNamespace(
        headers={'Authorization': 'Bearer revoked-jwt'},
        cookies={},
        app=main.app,
    )
    with pytest.raises(HTTPException) as exc_info:
        await main.get_app_config(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == 'Invalid token'
    assert profile_calls == []


@pytest.mark.asyncio
async def test_invalid_present_jwt_is_rejected_instead_of_treated_as_anonymous(
    profile_db,
    resource_truth,
    monkeypatch,
):
    main = importlib.import_module('open_webui.main')
    monkeypatch.setattr(main, 'decode_token', lambda token: None)
    monkeypatch.setattr(
        main,
        'get_http_authorization_cred',
        lambda header: SimpleNamespace(credentials='invalid-jwt'),
    )
    request = SimpleNamespace(
        headers={'Authorization': 'Bearer invalid-jwt'},
        cookies={},
        app=main.app,
    )

    with pytest.raises(HTTPException) as exc_info:
        await main.get_app_config(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == 'Invalid token'


@pytest.mark.asyncio
async def test_token_revocation_redis_failure_is_controlled_503(
    profile_db,
    resource_truth,
    monkeypatch,
):
    main = importlib.import_module('open_webui.main')
    user = SimpleNamespace(id='user-1', role='user')
    profile_calls = []

    class FailingRedis:
        async def get(self, key):
            raise RuntimeError('PRIVATE REDIS FAILURE DETAIL')

    async def public_profiles(app):
        profile_calls.append(app)
        return []

    monkeypatch.setattr(main, 'decode_token', lambda token: {'id': user.id, 'jti': 'token-1', 'iat': 1})
    monkeypatch.setattr(
        main,
        'get_http_authorization_cred',
        lambda header: SimpleNamespace(credentials='valid-token'),
    )
    monkeypatch.setattr(main, 'get_public_conversation_mode_profiles', public_profiles)
    main.app.state.redis = FailingRedis()
    request = SimpleNamespace(
        headers={'Authorization': 'Bearer valid-token'},
        cookies={},
        app=main.app,
    )

    with pytest.raises(HTTPException) as exc_info:
        await main.get_app_config(request)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {'code': 'auth_token_validation_unavailable'}
    assert 'PRIVATE REDIS FAILURE DETAIL' not in str(exc_info.value.detail)
    assert profile_calls == []


@pytest.mark.asyncio
async def test_app_config_user_lookup_failure_is_controlled_503(
    profile_db,
    resource_truth,
    monkeypatch,
):
    main = importlib.import_module('open_webui.main')
    profile_calls = []
    raw_sentinel = 'PRIVATE USER LOOKUP DATABASE DETAIL'

    async def valid_token(data, redis):
        return True

    async def fail_user_lookup(user_id):
        raise OperationalError('SELECT private user', {}, RuntimeError(raw_sentinel))

    async def public_profiles(app):
        profile_calls.append(app)
        return []

    monkeypatch.setattr(main, 'decode_token', lambda token: {'id': 'user-1'})
    monkeypatch.setattr(main, 'is_valid_token', valid_token)
    monkeypatch.setattr(main.Users, 'get_user_by_id', fail_user_lookup)
    monkeypatch.setattr(
        main,
        'get_http_authorization_cred',
        lambda header: SimpleNamespace(credentials='valid-token'),
    )
    monkeypatch.setattr(main, 'get_public_conversation_mode_profiles', public_profiles)
    request = SimpleNamespace(
        headers={'Authorization': 'Bearer valid-token'},
        cookies={},
        app=main.app,
    )

    with pytest.raises(HTTPException) as exc_info:
        await main.get_app_config(request)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {'code': 'auth_user_lookup_unavailable'}
    assert raw_sentinel not in str(exc_info.value.detail)
    assert profile_calls == []


@pytest.mark.asyncio
async def test_app_config_allows_healthy_slow_token_revocation_check(
    profile_db,
    resource_truth,
    monkeypatch,
):
    main = importlib.import_module('open_webui.main')
    user = SimpleNamespace(id='user-1', role='user')
    profile_calls = []

    async def healthy_slow_token_check(data, redis):
        await asyncio.sleep(0.2)
        return True

    async def get_user_by_id(user_id):
        return user if user_id == user.id else None

    async def has_users():
        return True

    async def get_num_users():
        return 1

    async def public_profiles(app):
        profile_calls.append(app)
        return []

    monkeypatch.setattr(main, 'decode_token', lambda token: {'id': user.id})
    monkeypatch.setattr(main, 'is_valid_token', healthy_slow_token_check)
    monkeypatch.setattr(main.Users, 'get_user_by_id', get_user_by_id)
    monkeypatch.setattr(main.Users, 'has_users', has_users)
    monkeypatch.setattr(main.Users, 'get_num_users', get_num_users)
    monkeypatch.setattr(
        main,
        'get_http_authorization_cred',
        lambda header: SimpleNamespace(credentials='valid-token'),
    )
    monkeypatch.setattr(main, 'get_public_conversation_mode_profiles', public_profiles)
    main.app.state.redis = None
    request = SimpleNamespace(
        headers={'Authorization': 'Bearer valid-token'},
        cookies={},
        app=main.app,
    )

    assert main.APP_CONFIG_TOKEN_VALIDATION_TIMEOUT_SECONDS == 1.0
    response = await asyncio.wait_for(main.get_app_config(request), timeout=1.5)

    assert response['conversation_mode_profiles'] == []
    assert profile_calls == [main.app]


@pytest.mark.asyncio
async def test_token_revocation_redis_hang_is_bounded_and_controlled_503(
    profile_db,
    resource_truth,
    monkeypatch,
):
    main = importlib.import_module('open_webui.main')
    user = SimpleNamespace(id='user-1', role='user')
    profile_calls = []
    cancelled = asyncio.Event()

    class HangingRedis:
        async def get(self, key):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    async def public_profiles(app):
        profile_calls.append(app)
        return []

    monkeypatch.setattr(main, 'decode_token', lambda token: {'id': user.id, 'jti': 'token-1', 'iat': 1})
    monkeypatch.setattr(main, 'APP_CONFIG_TOKEN_VALIDATION_TIMEOUT_SECONDS', 0.01, raising=False)
    monkeypatch.setattr(
        main,
        'get_http_authorization_cred',
        lambda header: SimpleNamespace(credentials='valid-token'),
    )
    monkeypatch.setattr(main, 'get_public_conversation_mode_profiles', public_profiles)
    main.app.state.redis = HangingRedis()
    request = SimpleNamespace(
        headers={'Authorization': 'Bearer valid-token'},
        cookies={},
        app=main.app,
    )

    with pytest.raises(HTTPException) as exc_info:
        await asyncio.wait_for(main.get_app_config(request), timeout=0.2)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {'code': 'auth_token_validation_unavailable'}
    assert profile_calls == []
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_public_profile_integrity_failure_maps_to_explicit_503(
    profile_db,
    resource_truth,
    monkeypatch,
):
    from open_webui.models.conversation_mode_profiles import ConversationModeProfileIntegrityError

    main = importlib.import_module('open_webui.main')
    user = SimpleNamespace(id='user-1', role='user')

    async def get_user_by_id(user_id):
        return user

    async def unavailable_profiles(app):
        raise ConversationModeProfileIntegrityError('chat-head', 'profile unavailable')

    monkeypatch.setattr(main.Users, 'get_user_by_id', get_user_by_id)
    monkeypatch.setattr(main, 'decode_token', lambda token: {'id': user.id})
    monkeypatch.setattr(
        main,
        'get_http_authorization_cred',
        lambda header: SimpleNamespace(credentials='valid-token'),
    )
    monkeypatch.setattr(main, 'get_public_conversation_mode_profiles', unavailable_profiles)
    main.app.state.redis = None
    request = SimpleNamespace(
        headers={'Authorization': 'Bearer valid-token'},
        cookies={},
        app=main.app,
    )

    with pytest.raises(HTTPException) as exc_info:
        await main.get_app_config(request)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        'code': 'mode_profile_integrity_error',
        'revision_id': 'chat-head',
    }
