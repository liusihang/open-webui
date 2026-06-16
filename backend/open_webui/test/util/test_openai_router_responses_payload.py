from open_webui.routers.openai import convert_to_responses_payload


def test_convert_to_responses_payload_preserves_full_replay_for_unguarded_previous_response_id():
    payload = {
        'model': 'gpt-5.5',
        'previous_response_id': 'resp_existing',
        'responses_stateful_replay_required_reason': 'tool_source_context_rewrite',
        'messages': [
            {'role': 'system', 'content': 'stable instructions'},
            {'role': 'user', 'content': 'old question'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'new question'},
        ],
    }

    responses_payload = convert_to_responses_payload(payload)

    assert 'previous_response_id' not in responses_payload
    assert 'responses_stateful_replay_required_reason' not in responses_payload
    assert responses_payload['instructions'] == 'stable instructions'
    assert responses_payload['input'] == [
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


def test_convert_to_responses_payload_forwards_guarded_stateful_delta():
    payload = {
        'model': 'gpt-5.5',
        'previous_response_id': 'resp_existing',
        'continuation_mode': 'stateful_delta',
        'messages': [
            {'role': 'system', 'content': 'stable instructions'},
            {
                'role': 'tool',
                'tool_call_id': 'call_evidence',
                'content': 'Evidence summary',
            },
        ],
    }

    responses_payload = convert_to_responses_payload(payload)

    assert responses_payload['previous_response_id'] == 'resp_existing'
    assert 'continuation_mode' not in responses_payload
    assert responses_payload['instructions'] == 'stable instructions'
    assert responses_payload['input'] == [
        {
            'type': 'function_call_output',
            'call_id': 'call_evidence',
            'output': 'Evidence summary',
        }
    ]


def test_convert_to_responses_payload_preserves_tool_output_images():
    payload = {
        'model': 'gpt-5.5',
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

    responses_payload = convert_to_responses_payload(payload)

    assert responses_payload['input'] == [
        {
            'type': 'function_call_output',
            'call_id': 'call_evidence',
            'output': [
                {'type': 'input_text', 'text': 'Evidence summary'},
                {'type': 'input_image', 'image_url': 'data:image/png;base64,AAAA'},
            ],
        }
    ]
