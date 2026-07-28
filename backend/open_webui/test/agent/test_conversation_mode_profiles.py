from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from open_webui.agent import conversation_mode_profiles as mode_profiles
from open_webui.agent.conversation_mode_profiles import (
    ALLOWED_MODES,
    INHERIT,
    PROFILE_SCHEMA_VERSION,
    ConversationModeProfile,
    ModeProfileConflictError,
    ModeProfileError,
    ModeProfileValidationError,
    ProfileDefaults,
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


def test_content_hash_changes_when_prompt_or_defaults_change() -> None:
    baseline = ConversationModeProfile.from_mapping('chat', _content())
    changed_prompt = ConversationModeProfile.from_mapping(
        'chat',
        _content(system_prompt='Different administrator prompt'),
    )
    changed_defaults = ConversationModeProfile.from_mapping(
        'chat',
        _content(defaults={'tool_ids': ['tool-1']}),
    )

    assert len({baseline.content_hash, changed_prompt.content_hash, changed_defaults.content_hash}) == 3


def test_canonical_mapping_represents_inheritance_by_omission() -> None:
    profile = ConversationModeProfile.from_mapping('chat', _content(defaults={}))

    assert profile.to_content_dict()['defaults'] == {}


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
    ('profile_defaults', 'model_defaults', 'expected_terminal'),
    [
        (
            {'terminal_id': 'admin-terminal'},
            {'feature_ids': ['code_interpreter', 'web_search']},
            'admin-terminal',
        ),
        (
            {'feature_ids': ['code_interpreter', 'image_generation']},
            {'terminal_id': 'model-terminal'},
            'model-terminal',
        ),
        (
            {},
            {
                'terminal_id': 'model-terminal',
                'feature_ids': ['code_interpreter', 'web_search'],
            },
            'model-terminal',
        ),
    ],
)
def test_resolve_preserves_terminal_and_code_interpreter_until_final_arbitration(
    profile_defaults: dict[str, object],
    model_defaults: dict[str, object],
    expected_terminal: str,
) -> None:
    profile = ConversationModeProfile.from_mapping(
        'agent',
        _content(defaults=profile_defaults),
    )

    resolved = resolve_profile_defaults(profile.defaults, model_defaults)

    assert resolved['terminal_id'] == expected_terminal
    assert 'code_interpreter' in resolved['feature_ids']


def test_final_arbitration_removes_code_interpreter_when_terminal_remains() -> None:
    filtered_defaults = {
        'terminal_id': 'terminal-1',
        'tool_ids': [],
        'skill_ids': [],
        'filter_ids': [],
        'feature_ids': ['code_interpreter', 'web_search'],
    }

    arbitrated = mode_profiles.arbitrate_profile_defaults(filtered_defaults)

    assert arbitrated == {
        **filtered_defaults,
        'feature_ids': ['web_search'],
    }
    assert filtered_defaults['feature_ids'] == ['code_interpreter', 'web_search']


def test_final_arbitration_preserves_code_interpreter_when_terminal_was_filtered() -> None:
    filtered_defaults = {
        'terminal_id': None,
        'tool_ids': [],
        'skill_ids': [],
        'filter_ids': [],
        'feature_ids': ['code_interpreter', 'web_search'],
    }

    arbitrated = mode_profiles.arbitrate_profile_defaults(filtered_defaults)

    assert arbitrated == filtered_defaults
    assert arbitrated is not filtered_defaults


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


def test_profile_defaults_constructor_preserves_literal_inherit_terminal_id() -> None:
    source_tool_ids = ['tool-1']
    defaults = ProfileDefaults(
        terminal_id='inherit',
        tool_ids=source_tool_ids,
        skill_ids=['skill-1'],
        filter_ids=[],
        feature_ids=['web_search'],
    )

    source_tool_ids.append('tool-2')

    assert defaults.terminal_id == 'inherit'
    assert defaults.terminal_id is not INHERIT
    assert defaults.tool_ids == ('tool-1',)
    assert defaults.skill_ids == ('skill-1',)
    assert defaults.filter_ids == ()
    assert defaults.feature_ids == ('web_search',)

    with pytest.raises(FrozenInstanceError):
        defaults.tool_ids = ('replacement',)


@pytest.mark.parametrize(
    ('kwargs', 'reason'),
    [
        ({'tool_ids': ['']}, 'invalid_default_identifier'),
        ({'tool_ids': 'inherit'}, 'invalid_default_value'),
        ({'skill_ids': ['skill-1', 'skill-1']}, 'duplicate_default_identifier'),
        ({'feature_ids': ['unknown_feature']}, 'unsupported_feature'),
    ],
)
def test_profile_defaults_constructor_rejects_invalid_direct_values(
    kwargs: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(ModeProfileValidationError) as exc_info:
        ProfileDefaults(**kwargs)

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == reason


def test_direct_profile_construction_normalizes_mode_and_keeps_hash_immutable() -> None:
    source_tool_ids = ['tool-1']
    profile = ConversationModeProfile(
        mode='chat',
        schema_version=PROFILE_SCHEMA_VERSION,
        system_prompt='Administrator prompt',
        defaults=ProfileDefaults(tool_ids=source_tool_ids),
    )
    original_hash = profile.content_hash

    source_tool_ids.append('tool-2')

    assert profile.mode.value == 'chat'
    assert profile.defaults.tool_ids == ('tool-1',)
    assert profile.content_hash == original_hash


def test_profile_defaults_internal_inherit_sentinel_remains_available() -> None:
    defaults = ProfileDefaults()

    assert defaults.terminal_id is INHERIT
    assert defaults.tool_ids is INHERIT


@pytest.mark.parametrize(
    ('overrides', 'reason'),
    [
        ({'mode': 'work'}, 'unsupported_mode'),
        ({'schema_version': 2}, 'unsupported_schema_version'),
        ({'system_prompt': None}, 'invalid_system_prompt'),
        ({'defaults': {}}, 'invalid_defaults'),
    ],
)
def test_direct_profile_construction_cannot_bypass_validation(
    overrides: dict[str, object],
    reason: str,
) -> None:
    values: dict[str, object] = {
        'mode': 'chat',
        'schema_version': PROFILE_SCHEMA_VERSION,
        'system_prompt': 'Administrator prompt',
        'defaults': ProfileDefaults(),
    }
    values.update(overrides)

    with pytest.raises(ModeProfileValidationError) as exc_info:
        ConversationModeProfile(**values)

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == reason


def test_profile_repr_does_not_expose_administrator_prompt() -> None:
    secret = 'administrator-secret-prompt'
    profile = ConversationModeProfile.from_mapping(
        'chat',
        _content(system_prompt=secret),
    )

    assert secret not in repr(profile)


def test_prompt_composition_order_is_administrator_model_then_user() -> None:
    assert compose_prompt_layers(
        administrator='administrator',
        model='model',
        user='user',
    ) == ('administrator', 'model', 'user')


def test_empty_and_whitespace_prompt_layers_are_omitted() -> None:
    assert compose_prompt_layers(
        administrator='   ',
        model='model prompt',
        user='\n\t',
    ) == ('model prompt',)
    assert compose_prompt_layers(administrator='') == ()


def test_prompt_composition_keeps_administrator_type_validation() -> None:
    with pytest.raises(ModeProfileValidationError) as exc_info:
        compose_prompt_layers(administrator=None)

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == 'invalid_system_prompt'


def test_unknown_profile_field_has_general_code_and_specific_reason() -> None:
    content = _content()
    content['temperature'] = 0.2

    with pytest.raises(ModeProfileError) as exc_info:
        ConversationModeProfile.from_mapping('chat', content)

    assert isinstance(exc_info.value, ModeProfileValidationError)
    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == 'unknown_profile_field'
    assert exc_info.value.field == 'temperature'


def test_unknown_default_field_is_rejected() -> None:
    with pytest.raises(ModeProfileValidationError) as exc_info:
        ConversationModeProfile.from_mapping(
            'chat',
            _content(defaults={'unknown_ids': []}),
        )

    assert exc_info.value.code == 'invalid_mode_profile'
    assert exc_info.value.reason == 'unknown_default_field'
    assert exc_info.value.field == 'unknown_ids'


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
