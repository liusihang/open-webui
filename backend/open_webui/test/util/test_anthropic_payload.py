from open_webui.utils.anthropic import (
    convert_anthropic_to_openai_payload,
    convert_openai_to_anthropic_response,
)


def test_convert_anthropic_to_openai_payload_preserves_tool_result_image_blocks():
    anthropic_payload = {
        'model': 'claude-3.7-sonnet',
        'messages': [
            {
                'role': 'assistant',
                'content': [
                    {
                        'type': 'tool_result',
                        'tool_use_id': 'tool_evidence',
                        'content': [
                            {
                                'type': 'image',
                                'source': {
                                    'type': 'base64',
                                    'media_type': 'image/png',
                                    'data': 'AAAA',
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }

    openai_payload = convert_anthropic_to_openai_payload(anthropic_payload)

    assert openai_payload['messages'] == [
        {
            'role': 'tool',
            'tool_call_id': 'tool_evidence',
            'content': [
                {
                    'type': 'image_url',
                    'image_url': {'url': 'data:image/png;base64,AAAA'},
                }
            ],
        }
    ]


def test_convert_anthropic_output_config_to_reasoning_and_structured_output():
    openai_payload = convert_anthropic_to_openai_payload(
        {
            'model': 'claude-sonnet-4-5',
            'messages': [{'role': 'user', 'content': 'Return JSON'}],
            'output_config': {
                'effort': 'high',
                'format': {
                    'type': 'json_schema',
                    'name': 'answer',
                    'description': 'Structured answer',
                    'strict': True,
                    'schema': {
                        'type': 'object',
                        'properties': {'answer': {'type': 'string'}},
                        'required': ['answer'],
                    },
                },
            },
        }
    )

    assert openai_payload['reasoning_effort'] == 'high'
    assert openai_payload['response_format'] == {
        'type': 'json_schema',
        'json_schema': {
            'name': 'answer',
            'description': 'Structured answer',
            'strict': True,
            'schema': {
                'type': 'object',
                'properties': {'answer': {'type': 'string'}},
                'required': ['answer'],
            },
        },
    }


def test_convert_openai_response_preserves_reasoning_and_detailed_usage():
    anthropic_response = convert_openai_to_anthropic_response(
        {
            'id': 'response-1',
            'model': 'claude-sonnet-4-5',
            'choices': [
                {
                    'finish_reason': 'stop',
                    'message': {
                        'reasoning_content': 'Private reasoning summary',
                        'content': 'Final answer',
                    },
                }
            ],
            'usage': {
                'prompt_tokens': 20,
                'completion_tokens': 7,
                'cache_read_input_tokens': 5,
                'output_tokens_details': {'reasoning_tokens': 3},
            },
        }
    )

    assert anthropic_response['content'] == [
        {'type': 'thinking', 'thinking': 'Private reasoning summary'},
        {'type': 'text', 'text': 'Final answer'},
    ]
    assert anthropic_response['usage'] == {
        'input_tokens': 15,
        'output_tokens': 7,
        'cache_read_input_tokens': 5,
        'output_tokens_details': {'reasoning_tokens': 3},
    }
