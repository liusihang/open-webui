import os
from types import SimpleNamespace

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret')
os.environ.setdefault('ENABLE_DB_MIGRATIONS', 'false')

import pytest
from open_webui.agent.model_catalog import (
    AgentModelCatalog,
    ModelSelectionNotAllowed,
    ModelSelectionRequest,
)


@pytest.mark.asyncio
async def test_catalog_filters_models_through_access_checker():
    catalog = AgentModelCatalog(
        run_store=FakeRunStore(),
        user_loader=_user_loader,
        model_loader=_model_loader(
            [
                _model('allowed-model', 'Allowed Model'),
                _model('private-model', 'Private Model'),
            ]
        ),
        model_access_checker=_reject_model_ids({'private-model'}),
    )

    response = await catalog.select_model(
        _request(),
        ModelSelectionRequest(
            run_id='run-1',
            participant_id='subagent-a',
            selection_id='selection-1',
            source_request={'task': 'summarize a document'},
        ),
    )

    assert [choice['id'] for choice in response['choices']] == ['allowed-model']
    assert response['selected_model_id'] == 'allowed-model'
    assert response['meta']['agent_selection'] == {
        'reason': 'default_permission_valid_model',
        'source_request': {'task': 'summarize a document'},
        'selected_model_id': 'allowed-model',
    }


@pytest.mark.asyncio
async def test_fuzzy_subagent_request_selects_permission_valid_model():
    catalog = AgentModelCatalog(
        run_store=FakeRunStore(),
        user_loader=_user_loader,
        model_loader=_model_loader(
            [
                _model(
                    'general-model',
                    'General Model',
                    agent_selection={'tags': ['general'], 'priority': 10},
                ),
                _model(
                    'python-coder',
                    'Python Coding Model',
                    agent_selection={
                        'tags': ['python', 'coding'],
                        'capabilities': ['code'],
                        'priority': 20,
                    },
                ),
            ]
        ),
        model_access_checker=_allow_model_access,
    )

    response = await catalog.select_model(
        _request(),
        ModelSelectionRequest(
            run_id='run-1',
            participant_id='subagent-coder',
            selection_id='selection-2',
            fuzzy_request='Need a python coding helper',
            source_request={'request': 'Need a python coding helper'},
        ),
    )

    assert [choice['id'] for choice in response['choices']] == [
        'general-model',
        'python-coder',
    ]
    assert response['selected_model_id'] == 'python-coder'
    assert response['meta']['agent_selection'] == {
        'reason': 'fuzzy_match',
        'source_request': {'request': 'Need a python coding helper'},
        'selected_model_id': 'python-coder',
    }


@pytest.mark.asyncio
async def test_explicit_unauthorized_request_is_rejected_with_audit_warning():
    catalog = AgentModelCatalog(
        run_store=FakeRunStore(),
        user_loader=_user_loader,
        model_loader=_model_loader(
            [
                _model('allowed-model', 'Allowed Model'),
                _model('private-model', 'Private Model'),
            ]
        ),
        model_access_checker=_reject_model_ids({'private-model'}),
    )

    with pytest.raises(ModelSelectionNotAllowed) as exc_info:
        await catalog.select_model(
            _request(),
            ModelSelectionRequest(
                run_id='run-1',
                participant_id='subagent-a',
                selection_id='selection-3',
                requested_model_id='private-model',
                source_request={'model': 'private-model'},
            ),
        )

    assert exc_info.value.warnings == [
        {
            'code': 'explicit_model_not_allowed',
            'message': 'Requested model is not available for this run: private-model',
            'requested_model_id': 'private-model',
        }
    ]


@pytest.mark.asyncio
async def test_catalog_selection_does_not_create_nested_run_or_call_provider():
    run_store = FakeRunStore()
    provider_calls = []

    async def model_loader(request, user):
        provider_calls.append('catalog-load-only')
        return [_model('allowed-model', 'Allowed Model')]

    catalog = AgentModelCatalog(
        run_store=run_store,
        user_loader=_user_loader,
        model_loader=model_loader,
        model_access_checker=_allow_model_access,
    )

    response = await catalog.select_model(
        _request(),
        ModelSelectionRequest(
            run_id='run-1',
            participant_id='subagent-a',
            selection_id='selection-4',
        ),
    )

    assert response['selected_model_id'] == 'allowed-model'
    assert provider_calls == ['catalog-load-only']
    assert run_store.created_run_ids == []
    assert 'response' not in response


class FakeRunStore:
    def __init__(self):
        self.created_run_ids = []

    async def get_run(self, run_id):
        return SimpleNamespace(
            id=run_id,
            user_id='user-1',
            state='running',
        )

    async def create_run(self, *args, **kwargs):
        self.created_run_ids.append(kwargs.get('id') or 'created')
        raise AssertionError('model catalog must not create nested agent runs')


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


async def _user_loader(user_id):
    return SimpleNamespace(id=user_id, role='user')


def _model_loader(models):
    async def load_models(request, user):
        return models

    return load_models


async def _allow_model_access(user, model):
    return None


def _reject_model_ids(model_ids):
    async def check_access(user, model):
        if model['id'] in model_ids:
            raise Exception('Model not found')

    return check_access


def _model(model_id, name, *, agent_selection=None):
    return {
        'id': model_id,
        'name': name,
        'object': 'model',
        'owned_by': 'openai',
        'info': {
            'meta': {
                'agent_selection': agent_selection or {},
            }
        },
    }
