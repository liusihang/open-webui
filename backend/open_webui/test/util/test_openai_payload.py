import pytest
from open_webui.utils import payload as payload_utils
from open_webui.utils.openai_payload import (
    dedupe_system_messages,
    responses_continuation_input_items,
    sanitize_openai_payload,
)
from open_webui.utils.payload import (
    apply_model_params_to_body_openai,
    convert_payload_openai_to_ollama,
)


def test_compose_global_system_prompt_places_admin_before_model_and_chat_content():
    composed = payload_utils.compose_global_system_prompt(
        'Administrator policy.',
        'Model policy.\nChat policy.',
    )

    assert composed == (
        '[ADMINISTRATOR INSTRUCTIONS]\nAdministrator policy.\n\n[MODEL INSTRUCTIONS]\nModel policy.\nChat policy.'
    )


def test_compose_global_system_prompt_preserves_legacy_content_when_global_is_empty():
    assert payload_utils.compose_global_system_prompt('', 'Model policy.') == 'Model policy.'


def test_compose_global_system_prompt_emits_only_admin_section_without_downstream_content():
    assert payload_utils.compose_global_system_prompt('Administrator policy.', '') == (
        '[ADMINISTRATOR INSTRUCTIONS]\nAdministrator policy.'
    )


@pytest.mark.asyncio
async def test_apply_model_system_prompt_reads_global_config_and_composes_one_system_message(monkeypatch):
    async def fake_config_get(key, default=None):
        assert key == 'chat.global_system_prompt'
        return 'Administrator policy.'

    monkeypatch.setattr(payload_utils.Config, 'get', fake_config_get)
    form_data = {
        'messages': [
            {'role': 'system', 'content': 'Chat policy.'},
            {'role': 'user', 'content': 'Hello'},
        ]
    }

    result = await payload_utils.apply_model_system_prompt_to_body(
        'Model policy.',
        form_data,
        metadata={},
        user=None,
    )

    assert result['messages'] == [
        {
            'role': 'system',
            'content': (
                '[ADMINISTRATOR INSTRUCTIONS]\n'
                'Administrator policy.\n\n'
                '[MODEL INSTRUCTIONS]\n'
                'Model policy.\nChat policy.'
            ),
        },
        {'role': 'user', 'content': 'Hello'},
    ]


@pytest.mark.asyncio
async def test_apply_model_system_prompt_skips_global_prompt_for_internal_tasks(monkeypatch):
    async def fake_config_get(key, default=None):
        return 'Administrator policy.'

    monkeypatch.setattr(payload_utils.Config, 'get', fake_config_get)
    form_data = {
        'messages': [
            {'role': 'system', 'content': 'Task policy.'},
            {'role': 'user', 'content': 'Generate a title'},
        ]
    }

    result = await payload_utils.apply_model_system_prompt_to_body(
        'Model policy.',
        form_data,
        metadata={'task': 'title_generation'},
        user=None,
    )

    assert result['messages'][0] == {
        'role': 'system',
        'content': 'Model policy.\nTask policy.',
    }


@pytest.mark.asyncio
async def test_apply_model_system_prompt_normalizes_all_system_messages_to_position_zero(monkeypatch):
    async def fake_config_get(key, default=None):
        return 'Administrator policy.'

    monkeypatch.setattr(payload_utils.Config, 'get', fake_config_get)
    form_data = {
        'messages': [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'system', 'content': 'Chat policy A.'},
            {'role': 'system', 'content': 'Chat policy B.'},
        ]
    }

    result = await payload_utils.apply_model_system_prompt_to_body(
        'Model policy.',
        form_data,
        metadata={},
        user=None,
    )

    assert result['messages'] == [
        {
            'role': 'system',
            'content': (
                '[ADMINISTRATOR INSTRUCTIONS]\n'
                'Administrator policy.\n\n'
                '[MODEL INSTRUCTIONS]\n'
                'Model policy.\nChat policy A.\nChat policy B.'
            ),
        },
        {'role': 'user', 'content': 'Hello'},
    ]


def test_sanitize_openai_payload_removes_reasoning_encrypted_content_keys_and_required_entries():
    payload = {
        'tools': [
            {
                'type': 'function',
                'function': {
                    'name': 'demo',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'safe_key': {'type': 'string'},
                            'REASONING_ENCRYPTED_CONTENT': {'type': 'string'},
                        },
                        'required': ['safe_key', 'REASONING_ENCRYPTED_CONTENT'],
                    },
                },
            }
        ]
    }

    sanitized, removed = sanitize_openai_payload(payload)

    assert removed == 2
    assert payload['tools'][0]['function']['parameters']['required'] == ['safe_key', 'REASONING_ENCRYPTED_CONTENT']
    assert 'REASONING_ENCRYPTED_CONTENT' not in sanitized['tools'][0]['function']['parameters']['properties']
    assert sanitized['tools'][0]['function']['parameters']['required'] == ['safe_key']


def test_dedupe_system_messages_keeps_first_system_message_and_preserves_order():
    messages = [
        {'role': 'system', 'content': 'first'},
        {'role': 'user', 'content': 'hello'},
        {'role': 'system', 'content': 'second'},
        {'role': 'assistant', 'content': 'world'},
    ]

    deduped, removed = dedupe_system_messages(messages)

    assert removed == 1
    assert deduped == [
        {'role': 'system', 'content': 'first'},
        {'role': 'user', 'content': 'hello'},
        {'role': 'assistant', 'content': 'world'},
    ]


def test_dedupe_system_messages_ignores_non_list_inputs():
    payload, removed = dedupe_system_messages({'role': 'system'})
    assert payload == {'role': 'system'}
    assert removed == 0


def test_responses_continuation_input_items_keeps_latest_user_turn():
    input_items = [
        {
            'type': 'message',
            'role': 'user',
            'content': [{'type': 'input_text', 'text': 'old question'}],
        },
        {
            'type': 'message',
            'role': 'assistant',
            'content': [{'type': 'output_text', 'text': 'old answer'}],
        },
        {
            'type': 'message',
            'role': 'user',
            'content': [{'type': 'input_text', 'text': 'new question'}],
        },
    ]

    trimmed = responses_continuation_input_items(input_items)

    assert trimmed == [input_items[-1]]


def test_convert_payload_openai_to_ollama_preserves_tool_input_images():
    payload = {
        'model': 'llama3.1',
        'messages': [
            {
                'role': 'tool',
                'tool_call_id': 'call_evidence',
                'content': [
                    {'type': 'input_text', 'text': 'Evidence summary'},
                    {'type': 'input_image', 'image_url': 'data:image/png;base64,AAAA'},
                ],
            }
        ],
    }

    ollama_payload = convert_payload_openai_to_ollama(payload)

    assert ollama_payload['messages'] == [
        {
            'role': 'tool',
            'tool_call_id': 'call_evidence',
            'content': 'Evidence summary',
            'images': ['AAAA'],
        }
    ]


def test_apply_model_params_preserves_explicit_reasoning_payload():
    form_data = {
        'model': 'bifrostapi.Cliproxy/gpt-5.5',
        'messages': [{'role': 'user', 'content': 'answer with marker'}],
        'reasoning': {
            'enabled': True,
            'effort': 'high',
            'max_tokens': 8126,
        },
    }
    model_params = {
        'temperature': 0.2,
        'reasoning': {
            'effort': None,
            'summary': 'detailed',
        },
    }

    payload = apply_model_params_to_body_openai(model_params, form_data)

    assert payload['temperature'] == 0.2
    assert payload['reasoning'] == {
        'enabled': True,
        'effort': 'high',
        'max_tokens': 8126,
    }


def test_apply_model_params_applies_default_reasoning_when_request_has_none():
    form_data = {
        'model': 'bifrostapi.Cliproxy/gpt-5.5',
        'messages': [{'role': 'user', 'content': 'hello'}],
    }
    model_params = {
        'reasoning': {
            'effort': 'medium',
            'summary': 'auto',
        },
    }

    payload = apply_model_params_to_body_openai(model_params, form_data)

    assert payload['reasoning'] == {
        'effort': 'medium',
        'summary': 'auto',
    }
