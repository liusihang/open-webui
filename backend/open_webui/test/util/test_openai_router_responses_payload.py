from open_webui.routers.openai import convert_to_responses_payload


def test_convert_to_responses_payload_trims_replay_for_previous_response_id():
    payload = {
        'model': 'gpt-5.5',
        'previous_response_id': 'resp_existing',
        'messages': [
            {'role': 'system', 'content': 'stable instructions'},
            {'role': 'user', 'content': 'old question'},
            {'role': 'assistant', 'content': 'old answer'},
            {'role': 'user', 'content': 'new question'},
        ],
    }

    responses_payload = convert_to_responses_payload(payload)

    assert responses_payload['previous_response_id'] == 'resp_existing'
    assert responses_payload['instructions'] == 'stable instructions'
    assert responses_payload['input'] == [
        {
            'type': 'message',
            'role': 'user',
            'content': [{'type': 'input_text', 'text': 'new question'}],
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
