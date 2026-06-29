from types import SimpleNamespace

import pytest

from open_webui.models.models import ModelMeta, ModelModel, ModelParams
from open_webui.utils import models as model_utils


@pytest.mark.asyncio
async def test_get_all_models_skips_active_custom_model_when_base_model_is_missing(monkeypatch):
    async def fake_get_all_base_models(request, user=None):
        return [
            {
                'id': 'available-base',
                'name': 'Available Base',
                'object': 'model',
                'owned_by': 'openai',
            }
        ]

    async def fake_get_all_custom_models():
        return [
            _custom_model('usable-preset', 'available-base'),
            _custom_model('broken-preset', 'missing-base'),
        ]

    monkeypatch.setattr(model_utils, 'get_all_base_models', fake_get_all_base_models)
    monkeypatch.setattr(model_utils.Models, 'get_all_models', fake_get_all_custom_models)
    monkeypatch.setattr(model_utils.Functions, 'get_global_action_functions', _empty_async)
    monkeypatch.setattr(model_utils.Functions, 'get_functions_by_type', _empty_async)
    monkeypatch.setattr(model_utils.Functions, 'get_global_filter_functions', _empty_async)
    monkeypatch.setattr(model_utils.Functions, 'get_functions_by_ids', _empty_async)
    monkeypatch.setattr(model_utils.Functions, 'get_function_valves_by_ids', _empty_dict_async)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={},
                BASE_MODELS=[],
                FUNCTIONS={},
                config=SimpleNamespace(
                    ENABLE_BASE_MODELS_CACHE=False,
                    ENABLE_EVALUATION_ARENA_MODELS=False,
                    EVALUATION_ARENA_MODELS=[],
                    DEFAULT_MODEL_METADATA={},
                ),
            )
        )
    )

    models = await model_utils.get_all_models(request)

    assert [model['id'] for model in models] == ['available-base', 'usable-preset']
    assert 'broken-preset' not in request.app.state.MODELS


async def _empty_async(*args, **kwargs):
    return []


async def _empty_dict_async(*args, **kwargs):
    return {}


def _custom_model(model_id: str, base_model_id: str) -> ModelModel:
    return ModelModel(
        id=model_id,
        user_id='user-1',
        base_model_id=base_model_id,
        name=model_id,
        params=ModelParams(),
        meta=ModelMeta(),
        is_active=True,
        updated_at=1,
        created_at=1,
    )
