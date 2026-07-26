from types import SimpleNamespace

import pytest
from open_webui.utils import filter as filter_utils


@pytest.fixture
def filter_selection(monkeypatch):
    state = SimpleNamespace(
        global_ids=[],
        active_ids=set(),
        toggle_ids=set(),
    )

    async def get_global_filter_functions():
        return [SimpleNamespace(id=filter_id) for filter_id in state.global_ids]

    async def get_functions_by_type(function_type, active_only=False):
        assert function_type == 'filter'
        assert active_only is True
        return [SimpleNamespace(id=filter_id) for filter_id in state.active_ids]

    async def get_module(request, function_id, load_from_db=True):
        return SimpleNamespace(toggle=function_id in state.toggle_ids)

    monkeypatch.setattr(
        filter_utils.Functions,
        'get_global_filter_functions',
        get_global_filter_functions,
    )
    monkeypatch.setattr(
        filter_utils.Functions,
        'get_functions_by_type',
        get_functions_by_type,
    )
    monkeypatch.setattr(filter_utils, 'get_function_module', get_module)
    return state


@pytest.mark.asyncio
async def test_profile_only_enabled_filter_is_a_sorted_candidate(filter_selection):
    filter_selection.active_ids = {'profile-filter'}
    filter_selection.toggle_ids = {'profile-filter'}

    result = await filter_utils.get_sorted_filter_ids(
        SimpleNamespace(),
        {'id': 'model-a', 'info': {'meta': {'filterIds': []}}},
        ['profile-filter'],
    )

    assert result == ['profile-filter']


@pytest.mark.asyncio
async def test_explicit_empty_profile_filters_disable_model_defaults_but_keep_global_mandatory(
    filter_selection,
):
    filter_selection.global_ids = ['global-filter']
    filter_selection.active_ids = {'global-filter', 'model-filter'}

    result = await filter_utils.get_sorted_filter_ids(
        SimpleNamespace(),
        {'id': 'model-a', 'info': {'meta': {'filterIds': ['model-filter']}}},
        [],
    )

    assert result == ['global-filter']
