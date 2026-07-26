from __future__ import annotations

from copy import deepcopy

import pytest
from open_webui.agent.conversation_mode_profiles import (
    ALLOWED_MODES,
    PROFILE_SCHEMA_VERSION,
    ConversationModeProfile,
    ModeProfileConflictError,
    ModeProfileValidationError,
    compose_prompt_layers,
    resolve_profile_defaults,
)


def _content(
    *,
    system_prompt: object = 'Administrator prompt',
    defaults: object = None,
) -> dict[str, object]:
    content: dict[str, object] = {
        'schema_version': PROFILE_SCHEMA_VERSION,
        'system_prompt': system_prompt,
        'defaults': {} if defaults is None else defaults,
    }
    return content


def test_allowed_modes_are_exactly_chat_and_agent() -> None:
    assert ALLOWED_MODES == frozenset({'chat', 'agent'})

    for mode in ALLOWED_MODES:
        profile = ConversationModeProfile.from_mapping(mode, _content())
        assert profile.mode.value == mode

    with pytest.raises(ModeProfileValidationError) as exc_info:
        ConversationModeProfile.from_mapping('work', _content())

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == 'unsupported_mode'


@pytest.mark.parametrize('schema_version', [0, 2, '1', True, None])
def test_schema_version_is_validated(schema_version: object) -> None:
    content = _content()
    content['schema_version'] = schema_version

    with pytest.raises(ModeProfileValidationError) as exc_info:
        ConversationModeProfile.from_mapping('chat', content)

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == 'unsupported_schema_version'


def test_canonical_content_hash_is_stable_across_dictionary_order() -> None:
    left = {
        'schema_version': 1,
        'system_prompt': 'Administrator prompt',
        'defaults': {
            'terminal_id': 'terminal-1',
            'tool_ids': ['tool-1', 'tool-2'],
            'skill_ids': ['skill-1'],
            'filter_ids': [],
            'feature_ids': ['web_search'],
        },
    }
    right = {
        'defaults': {
            'feature_ids': ['web_search'],
            'filter_ids': [],
            'skill_ids': ['skill-1'],
            'tool_ids': ['tool-1', 'tool-2'],
            'terminal_id': 'terminal-1',
        },
        'system_prompt': 'Administrator prompt',
        'schema_version': 1,
    }

    left_profile = ConversationModeProfile.from_mapping('agent', left)
    right_profile = ConversationModeProfile.from_mapping('agent', right)

    assert left_profile.content_hash == right_profile.content_hash
    assert len(left_profile.content_hash) == 64


def test_explicitly_empty_system_prompt_is_valid_and_hashable() -> None:
    profile = ConversationModeProfile.from_mapping(
        'chat',
        _content(system_prompt=''),
    )

    assert profile.system_prompt == ''
    assert len(profile.content_hash) == 64


@pytest.mark.parametrize('system_prompt', [None, 7, [], {}])
def test_malformed_system_prompt_is_invalid(system_prompt: object) -> None:
    with pytest.raises(ModeProfileValidationError) as exc_info:
        ConversationModeProfile.from_mapping(
            'chat',
            _content(system_prompt=system_prompt),
        )

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == 'invalid_system_prompt'


def test_missing_system_prompt_is_invalid() -> None:
    content = _content()
    del content['system_prompt']

    with pytest.raises(ModeProfileValidationError) as exc_info:
        ConversationModeProfile.from_mapping('chat', content)

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == 'missing_system_prompt'


def test_omitted_defaults_inherit_model_metadata() -> None:
    profile = ConversationModeProfile.from_mapping('chat', _content(defaults={}))
    model_defaults = {
        'terminal_id': 'model-terminal',
        'tool_ids': ['model-tool'],
        'skill_ids': ['model-skill'],
        'filter_ids': ['model-filter'],
        'feature_ids': ['web_search', 'image_generation'],
    }
    original_model_defaults = deepcopy(model_defaults)

    resolved = resolve_profile_defaults(profile.defaults, model_defaults)

    assert resolved == model_defaults
    assert model_defaults == original_model_defaults


def test_explicit_empty_defaults_clear_model_metadata() -> None:
    profile = ConversationModeProfile.from_mapping(
        'chat',
        _content(
            defaults={
                'terminal_id': None,
                'tool_ids': [],
                'skill_ids': [],
                'filter_ids': [],
                'feature_ids': [],
            }
        ),
    )

    resolved = resolve_profile_defaults(
        profile.defaults,
        {
            'terminal_id': 'model-terminal',
            'tool_ids': ['model-tool'],
            'skill_ids': ['model-skill'],
            'filter_ids': ['model-filter'],
            'feature_ids': ['web_search'],
        },
    )

    assert resolved == {
        'terminal_id': None,
        'tool_ids': [],
        'skill_ids': [],
        'filter_ids': [],
        'feature_ids': [],
    }


def test_explicit_defaults_override_model_metadata() -> None:
    profile = ConversationModeProfile.from_mapping(
        'agent',
        _content(
            defaults={
                'terminal_id': 'admin-terminal',
                'tool_ids': ['admin-tool'],
                'skill_ids': ['admin-skill'],
                'filter_ids': ['admin-filter'],
                'feature_ids': ['image_generation'],
            }
        ),
    )

    resolved = resolve_profile_defaults(
        profile.defaults,
        {
            'terminal_id': 'model-terminal',
            'tool_ids': ['model-tool'],
            'skill_ids': ['model-skill'],
            'filter_ids': ['model-filter'],
            'feature_ids': ['web_search'],
        },
    )

    assert resolved == {
        'terminal_id': 'admin-terminal',
        'tool_ids': ['admin-tool'],
        'skill_ids': ['admin-skill'],
        'filter_ids': ['admin-filter'],
        'feature_ids': ['image_generation'],
    }


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('model_id', 'model-1'),
        ('model_ids', ['model-1']),
        ('reasoning_depth', 'high'),
    ],
)
def test_model_ids_and_reasoning_depth_are_rejected_profile_fields(
    field: str,
    value: object,
) -> None:
    content = _content()
    content[field] = value

    with pytest.raises(ModeProfileValidationError) as exc_info:
        ConversationModeProfile.from_mapping('agent', content)

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == 'forbidden_profile_field'
    assert exc_info.value.field == field


def test_terminal_and_code_interpreter_conflict_is_rejected() -> None:
    with pytest.raises(ModeProfileConflictError) as exc_info:
        ConversationModeProfile.from_mapping(
            'agent',
            _content(
                defaults={
                    'terminal_id': 'terminal-1',
                    'feature_ids': ['code_interpreter'],
                }
            ),
        )

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == 'terminal_code_interpreter_conflict'


def test_prompt_composition_order_is_administrator_model_then_user() -> None:
    assert compose_prompt_layers(
        administrator='administrator',
        model='model',
        user='user',
    ) == ('administrator', 'model', 'user')


def test_public_serialization_omits_private_profile_metadata() -> None:
    profile = ConversationModeProfile.from_mapping(
        'agent',
        _content(
            system_prompt='Never expose this',
            defaults={
                'terminal_id': 'inherit',
                'tool_ids': ['tool-1'],
            },
        ),
    )

    public = profile.to_public_dict(current_revision_id='revision-7')

    assert public == {
        'mode': 'agent',
        'current_revision_id': 'revision-7',
        'schema_version': 1,
        'defaults': {
            'terminal_id': 'inherit',
            'tool_ids': ['tool-1'],
            'skill_ids': 'inherit',
            'filter_ids': 'inherit',
            'feature_ids': 'inherit',
        },
    }
    serialized = repr(public).lower()
    for private_name in (
        'system_prompt',
        'prompt',
        'content_hash',
        'hash',
        'created_by',
        'author',
        'history',
    ):
        assert private_name not in serialized
