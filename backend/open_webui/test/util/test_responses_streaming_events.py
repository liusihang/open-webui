from open_webui.utils.middleware import handle_responses_streaming_event, serialize_output


def test_responses_function_call_arguments_delta_coerces_object_fragments():
    output = []

    output, _ = handle_responses_streaming_event(
        {
            'type': 'response.output_item.added',
            'output_index': 0,
            'item': {
                'type': 'reasoning',
                'id': 'rs_1',
                'status': 'in_progress',
                'summary': [],
            },
        },
        output,
    )
    output, _ = handle_responses_streaming_event(
        {
            'type': 'response.output_item.added',
            'output_index': 1,
            'item': {
                'type': 'function_call',
                'id': 'fc_1',
                'call_id': 'call_1',
                'name': 'generate_image',
                'arguments': '',
                'status': 'in_progress',
            },
        },
        output,
    )

    for delta in [
        {'value': '{"prompt": "A quiet'},
        {'partial_json': ' mountain lake'},
        {'input_json_delta': {'partial_json': ' at sunrise"}'}},
    ]:
        output, _ = handle_responses_streaming_event(
            {
                'type': 'response.function_call_arguments.delta',
                'output_index': 1,
                'item_id': 'fc_1',
                'delta': delta,
            },
            output,
        )

    output, _ = handle_responses_streaming_event(
        {
            'type': 'response.function_call_arguments.done',
            'output_index': 1,
            'item_id': 'fc_1',
            'arguments': {'value': '{"prompt": "A quiet mountain lake at sunrise"}'},
        },
        output,
    )

    assert output[1]['arguments'] == '{"prompt": "A quiet mountain lake at sunrise"}'


def test_responses_reasoning_summary_delta_accepts_cumulative_snapshots():
    output = []

    output, _ = handle_responses_streaming_event(
        {
            'type': 'response.output_item.added',
            'output_index': 0,
            'item': {
                'type': 'reasoning',
                'id': 'rs_1',
                'status': 'in_progress',
                'summary': [],
            },
        },
        output,
    )

    for delta in [
        'Looking up current scores\n\nI need the latest sports scores.',
        'Looking up current scores\n\nI need the latest sports scores. Checking sources.',
    ]:
        output, _ = handle_responses_streaming_event(
            {
                'type': 'response.reasoning_summary_text.delta',
                'output_index': 0,
                'summary_index': 0,
                'delta': delta,
            },
            output,
        )

    assert output[0]['summary'][0]['text'] == (
        'Looking up current scores\n\nI need the latest sports scores. Checking sources.'
    )


def test_serialize_output_preserves_reasoning_line_breaks_without_blockquote_markup():
    rendered = serialize_output(
        [
            {
                'type': 'reasoning',
                'id': 'rs_1',
                'status': 'completed',
                'summary': [
                    {
                        'type': 'summary_text',
                        'text': 'Looking up current scores\n\nI need the latest sports scores.',
                    }
                ],
            }
        ]
    )

    assert '&gt;' not in rendered
    assert 'Looking up current scores\n\nI need the latest sports scores.' in rendered
