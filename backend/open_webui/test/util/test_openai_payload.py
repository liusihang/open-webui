from open_webui.utils.openai_payload import (
    dedupe_system_messages,
    responses_continuation_input_items,
    sanitize_openai_payload,
)


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
