from open_webui.utils.anthropic import convert_anthropic_to_openai_payload


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
