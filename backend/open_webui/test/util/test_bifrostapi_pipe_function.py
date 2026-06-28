from pathlib import Path


LOCAL_FUNCTION_PATH = (
    Path(__file__).resolve().parents[4] / 'tools' / 'openwebui' / 'functions' / 'bifrostapi.py'
)


def _load_pipe_class():
    namespace = {}
    exec(
        compile(LOCAL_FUNCTION_PATH.read_text(), str(LOCAL_FUNCTION_PATH), 'exec'),
        namespace,
    )
    return namespace['Pipe']


def test_repo_managed_bifrostapi_source_exists_and_compiles():
    assert LOCAL_FUNCTION_PATH.exists()
    pipe_cls = _load_pipe_class()
    assert pipe_cls.__name__ == 'Pipe'


def test_resolve_route_mode_auto_prefers_provider_specific_defaults():
    pipe = _load_pipe_class()()

    assert (
        pipe._resolve_route_mode({'model': 'bifrostapi.ZenMuxOAI/google/gemini-2.5-flash'})
        == 'chat'
    )
    assert (
        pipe._resolve_route_mode({'model': 'bifrostapi.ZenMuxOAI/z-ai/glm-4.5'})
        == 'responses'
    )
    assert pipe._resolve_route_mode({'model': 'bifrostapi.openai/gpt-5'}) == 'auto'
    assert pipe._resolve_route_mode({'model': 'bifrostapi.openai/gpt-5', 'route_mode': 'chat'}) == 'chat'


def test_resolve_effective_cache_settings_auto_generates_stable_gpt_prompt_cache_key():
    pipe = _load_pipe_class()()

    attachments_a = [
        {
            'name': 'b.png',
            'kind': 'image',
            'responses_part': {'type': 'input_image', 'image_url': 'data:image/png;base64,BBB'},
        },
        {
            'name': 'a.pdf',
            'kind': 'document',
            'responses_part': {'type': 'input_file', 'filename': 'a.pdf', 'file_data': 'data:application/pdf;base64,AAA'},
        },
    ]
    attachments_b = list(reversed(attachments_a))
    function_specs_a = [
        {
            'description': 'Second tool',
            'name': 'tool_b',
            'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}},
        },
        {
            'description': 'First tool',
            'name': 'tool_a',
            'parameters': {'type': 'object', 'properties': {'count': {'type': 'integer'}}},
        },
    ]
    function_specs_b = list(reversed(function_specs_a))
    system_message = {'role': 'system', 'content': 'system prompt'}
    messages = [{'role': 'user', 'content': 'hello'}]

    settings_a = pipe._resolve_effective_cache_settings(
        body={'model': 'bifrostapi.openai/gpt-5'},
        model='openai/gpt-5',
        route_mode='responses',
        system_message=system_message,
        messages=messages,
        attachments=attachments_a,
        function_specs=function_specs_a,
    )
    settings_b = pipe._resolve_effective_cache_settings(
        body={'model': 'bifrostapi.openai/gpt-5'},
        model='openai/gpt-5',
        route_mode='responses',
        system_message=system_message,
        messages=messages,
        attachments=attachments_b,
        function_specs=function_specs_b,
    )

    assert settings_a['provider'] == 'openai'
    assert settings_a['prompt_cache_key'].startswith('owg:')
    assert len(settings_a['prompt_cache_key']) == 64
    assert settings_a['prompt_cache_key'] == settings_b['prompt_cache_key']


def test_resolve_effective_cache_settings_keeps_explicit_prompt_cache_key():
    pipe = _load_pipe_class()()

    settings = pipe._resolve_effective_cache_settings(
        body={
            'model': 'bifrostapi.openai/gpt-5',
            'metadata': {'chat_id': 'chat-123'},
            'prompt_cache_key': 'caller-key',
        },
        model='openai/gpt-5',
        route_mode='responses',
        system_message={'role': 'system', 'content': 'system prompt'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[],
        function_specs=[],
    )

    assert settings['prompt_cache_key'] == 'caller-key'


def test_resolve_effective_cache_settings_uses_chat_id_scoped_gpt_prompt_cache_key():
    pipe = _load_pipe_class()()

    base = pipe._resolve_effective_cache_settings(
        body={
            'model': 'bifrostapi.openai/gpt-5',
            'metadata': {'chat_id': 'chat-123'},
        },
        model='openai/gpt-5',
        route_mode='responses',
        system_message={'role': 'system', 'content': 'system prompt'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[],
        function_specs=[],
    )
    changed_user = pipe._resolve_effective_cache_settings(
        body={
            'model': 'bifrostapi.openai/gpt-5',
            'metadata': {'chat_id': 'chat-123'},
        },
        model='openai/gpt-5',
        route_mode='responses',
        system_message={'role': 'system', 'content': 'system prompt'},
        messages=[{'role': 'user', 'content': 'different user text'}],
        attachments=[],
        function_specs=[],
    )
    fallback = pipe._resolve_effective_cache_settings(
        body={'model': 'bifrostapi.openai/gpt-5'},
        model='openai/gpt-5',
        route_mode='responses',
        system_message={'role': 'system', 'content': 'system prompt'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[],
        function_specs=[],
    )

    assert base['provider'] == 'openai'
    assert len(base['prompt_cache_key']) <= 64
    assert base['prompt_cache_key'] == changed_user['prompt_cache_key']
    assert base['prompt_cache_key'] != fallback['prompt_cache_key']
    assert not base['prompt_cache_key'].startswith('owg:')


def test_resolve_effective_cache_settings_chat_id_key_ignores_prefix_shape_changes():
    pipe = _load_pipe_class()()

    base = pipe._resolve_effective_cache_settings(
        body={
            'model': 'bifrostapi.openai/gpt-5',
            'metadata': {'chat_id': 'chat-123'},
        },
        model='openai/gpt-5',
        route_mode='responses',
        system_message={'role': 'system', 'content': 'system prompt'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[
            {
                'name': 'a.pdf',
                'kind': 'document',
                'responses_part': {'type': 'input_file', 'filename': 'a.pdf'},
            }
        ],
        function_specs=[
            {
                'name': 'tool_a',
                'description': 'First tool',
                'parameters': {'type': 'object', 'properties': {'count': {'type': 'integer'}}},
            }
        ],
    )
    changed_prefix_shape = pipe._resolve_effective_cache_settings(
        body={
            'model': 'bifrostapi.openai/gpt-5',
            'metadata': {'chat_id': 'chat-123'},
        },
        model='openai/gpt-5',
        route_mode='responses',
        system_message={'role': 'system', 'content': 'different system prompt'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[
            {
                'name': 'b.png',
                'kind': 'image',
                'responses_part': {'type': 'input_image', 'image_url': 'data:image/png;base64,BBB'},
            }
        ],
        function_specs=[
            {
                'name': 'tool_b',
                'description': 'Second tool',
                'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}},
            }
        ],
    )

    assert base['prompt_cache_key'] == changed_prefix_shape['prompt_cache_key']


def test_resolve_effective_cache_settings_without_chat_id_keeps_prefix_hash_semantics():
    pipe = _load_pipe_class()()

    base = pipe._resolve_effective_cache_settings(
        body={'model': 'bifrostapi.openai/gpt-5'},
        model='openai/gpt-5',
        route_mode='responses',
        system_message={'role': 'system', 'content': 'system prompt'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[],
        function_specs=[],
    )
    changed_system = pipe._resolve_effective_cache_settings(
        body={'model': 'bifrostapi.openai/gpt-5'},
        model='openai/gpt-5',
        route_mode='responses',
        system_message={'role': 'system', 'content': 'different system prompt'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[],
        function_specs=[],
    )
    changed_tools = pipe._resolve_effective_cache_settings(
        body={'model': 'bifrostapi.openai/gpt-5'},
        model='openai/gpt-5',
        route_mode='responses',
        system_message={'role': 'system', 'content': 'system prompt'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[],
        function_specs=[
            {
                'name': 'tool_a',
                'description': 'First tool',
                'parameters': {'type': 'object', 'properties': {'count': {'type': 'integer'}}},
            }
        ],
    )

    assert base['prompt_cache_key'].startswith('owg:')
    assert len(base['prompt_cache_key']) == 64
    assert base['prompt_cache_key'] != changed_system['prompt_cache_key']
    assert base['prompt_cache_key'] != changed_tools['prompt_cache_key']


def test_resolve_effective_cache_settings_does_not_auto_generate_non_gpt_prompt_cache_key():
    pipe = _load_pipe_class()()

    settings = pipe._resolve_effective_cache_settings(
        body={'model': 'bifrostapi.anthropic/claude-3.7-sonnet'},
        model='anthropic/claude-3.7-sonnet',
        route_mode='chat',
        system_message={'role': 'system', 'content': 'system prompt'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[],
        function_specs=[],
    )

    assert settings['provider'] == 'anthropic'
    assert settings['prompt_cache_key'] == ''


def test_apply_prompt_cache_markers_only_marks_anthropic_payload_when_enabled():
    pipe = _load_pipe_class()()
    long_text = 'x' * 1200
    payload = {
        'messages': [
            {'role': 'system', 'content': long_text},
            {'role': 'user', 'content': long_text},
        ],
        'tools': [{'type': 'function', 'function': {'name': 'demo', 'parameters': {'type': 'object', 'properties': {}}}}],
    }

    pipe._apply_prompt_cache_markers(
        payload=payload,
        route_mode='chat',
        body={'enable_prompt_caching': True},
        model='anthropic/claude-3.7-sonnet',
    )

    assert payload['messages'][0]['content'][0]['cache_control'] == {'type': 'ephemeral'}
    assert payload['messages'][1]['content'][0]['cache_control'] == {'type': 'ephemeral'}
    assert payload['tools'][0]['cache_control'] == {'type': 'ephemeral'}

    gpt_payload = {
        'messages': [{'role': 'user', 'content': long_text}],
        'tools': [{'type': 'function', 'function': {'name': 'demo', 'parameters': {'type': 'object', 'properties': {}}}}],
    }
    pipe._apply_prompt_cache_markers(
        payload=gpt_payload,
        route_mode='chat',
        body={'enable_prompt_caching': True},
        model='openai/gpt-5',
    )
    assert 'cache_control' not in gpt_payload['tools'][0]
    assert isinstance(gpt_payload['messages'][0]['content'], str)


def test_normalize_tool_parameters_cleans_nullable_shapes_for_bifrost():
    pipe = _load_pipe_class()()

    schema = pipe._normalize_tool_parameters(
        {
            'type': 'object',
            'required': ['name', 1, 'maybe'],
            'properties': {
                'name': {'type': 'string', 'default': None},
                'maybe': {
                    'anyOf': [
                        {'type': 'null'},
                        {'type': 'array', 'items': {}},
                    ]
                },
                'implicit_string': {},
            },
        }
    )

    assert schema['type'] == 'object'
    assert sorted(schema['required']) == ['maybe', 'name']
    assert 'default' not in schema['properties']['name']
    assert schema['properties']['maybe']['type'] == 'array'
    assert schema['properties']['maybe']['items']['type'] == 'string'
    assert schema['properties']['implicit_string']['type'] == 'string'


def test_build_chat_payload_attaches_attachments_to_last_user_message_and_normalizes_tools():
    pipe = _load_pipe_class()()

    payload = pipe._build_chat_payload(
        body={
            'model': 'bifrostapi.openai/gpt-5',
            'stream': False,
            'tool_choice': {'type': 'function', 'function': {'name': 'demo'}},
        },
        model='openai/gpt-5',
        system_message={'role': 'system', 'content': 'system'},
        messages=[
            {'role': 'user', 'content': 'hello'},
            {'role': 'assistant', 'content': 'hi'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'look at this'}]},
        ],
        attachments=[
            {
                'kind': 'document',
                'name': 'report.pdf',
                'chat_part': {'type': 'file', 'file': {'filename': 'report.pdf', 'file_data': 'data:application/pdf;base64,AAA'}},
                'responses_part': {'type': 'input_file', 'filename': 'report.pdf', 'file_data': 'data:application/pdf;base64,AAA'},
                'fallback_text': None,
            },
            {
                'kind': 'text',
                'name': 'notes.txt',
                'chat_part': None,
                'responses_part': None,
                'fallback_text': 'normalized fallback',
            },
        ],
        function_specs=[
            {
                'name': 'demo',
                'description': 'Demo tool',
                'parameters': {'type': 'object'},
            }
        ],
    )

    assert payload['messages'][0] == {'role': 'system', 'content': 'system'}
    last_user = payload['messages'][-1]
    assert last_user['role'] == 'user'
    assert last_user['content'][0] == {'type': 'text', 'text': 'look at this'}
    assert any(part.get('type') == 'file' for part in last_user['content'])
    assert any(
        part.get('type') == 'text' and part.get('text', '').startswith('[Attachment: notes.txt]\nnormalized fallback')
        for part in last_user['content']
    )
    assert payload['tools'][0]['type'] == 'function'
    assert payload['tools'][0]['function']['parameters'] == {'type': 'object', 'properties': {}}
    assert payload['tool_choice'] == {'type': 'function', 'function': {'name': 'demo'}}


def test_build_payloads_strip_openwebui_internal_assistant_details_from_history():
    pipe = _load_pipe_class()()
    provider_private_id = 'baej6ulr_ts_' + ('x' * 600)
    assistant_content = (
        f'<details type="tool_calls" done="true" id="{provider_private_id}">\n'
        '<summary>Tool Calls</summary>\n'
        'generate_image({"prompt":"city"})\n'
        '</details>\n'
        '<details type="reasoning" done="true" duration="0">\n'
        '<summary>Thought for 0 seconds</summary>\n'
        '&gt; GPT思考中（上游未返回可见思考内容）\n'
        '</details>\n'
        '图片已经生成，可以继续描述你想调整的地方。'
    )

    chat_payload = pipe._build_chat_payload(
        body={'model': 'bifrostapi.gemini/gemini-3.1-pro-preview', 'stream': True},
        model='gemini/gemini-3.1-pro-preview',
        system_message=None,
        messages=[
            {'role': 'user', 'content': '生成图片'},
            {'role': 'assistant', 'content': assistant_content},
            {'role': 'user', 'content': '改成箱根七曲发卡弯'},
        ],
        attachments=[],
        function_specs=[],
    )

    assistant_message = chat_payload['messages'][1]
    assert assistant_message['role'] == 'assistant'
    assert assistant_message['content'] == '图片已经生成，可以继续描述你想调整的地方。'
    assert provider_private_id not in str(chat_payload)
    assert '<details' not in str(chat_payload)

    responses_payload = pipe._build_responses_payload(
        body={'model': 'bifrostapi.gemini/gemini-3.1-pro-preview', 'stream': True},
        model='gemini/gemini-3.1-pro-preview',
        system_message=None,
        messages=[
            {'role': 'user', 'content': '生成图片'},
            {'role': 'assistant', 'content': assistant_content},
            {'role': 'user', 'content': '改成箱根七曲发卡弯'},
        ],
        attachments=[],
        function_specs=[],
    )

    assert provider_private_id not in str(responses_payload)
    assert '<details' not in str(responses_payload)
    assert any(
        part.get('text') == '图片已经生成，可以继续描述你想调整的地方。'
        for item in responses_payload['input']
        if item.get('role') == 'assistant'
        for part in item.get('content', [])
    )


def test_pipe_forces_non_stream_upstream_for_native_image_tool_planning():
    pipe = _load_pipe_class()()
    pipe.valves.BIFROST_API_KEY = 'test-key'
    pipe.valves.ROUTE_MODE = 'chat'

    captured = {}

    def fake_chat_non_stream(payload, tool_name_prefix=''):
        captured['payload'] = payload
        captured['tool_name_prefix'] = tool_name_prefix
        return {
            'choices': [
                {
                    'finish_reason': 'tool_calls',
                    'message': {
                        'role': 'assistant',
                        'tool_calls': [
                            {
                                'id': 'call_image',
                                'type': 'function',
                                'function': {
                                    'name': 'generate_image',
                                    'arguments': '{"prompt":"red apple"}',
                                },
                            }
                        ],
                    },
                }
            ]
        }

    pipe._chat_non_stream_with_fallback = fake_chat_non_stream

    chunks = list(
        pipe.pipe(
            {
                'model': 'bifrostapi.ZenMuxOAI/openai/gpt-5.4',
                'stream': True,
                'features': {'image_generation': True},
                'messages': [{'role': 'user', 'content': '生成一张红苹果图片'}],
                'tools': [
                    {
                        'type': 'function',
                        'function': {
                            'name': 'generate_image',
                            'description': 'Generate image',
                            'parameters': {
                                'type': 'object',
                                'properties': {'prompt': {'type': 'string'}},
                                'required': ['prompt'],
                            },
                        },
                    }
                ],
            }
        )
    )

    assert captured['payload']['stream'] is False
    assert (
        chunks[0]['choices'][0]['delta']['tool_calls'][0]['function']['name']
        == 'generate_image'
    )
    assert chunks[1]['choices'][0]['finish_reason'] == 'tool_calls'


def test_build_responses_payload_attaches_attachments_and_uses_responses_tool_shape():
    pipe = _load_pipe_class()()

    payload = pipe._build_responses_payload(
        body={
            'model': 'bifrostapi.ZenMuxOAI/z-ai/glm-4.5',
            'stream': True,
            'tool_choice': 'required',
            'parallel_tool_calls': True,
        },
        model='ZenMuxOAI/z-ai/glm-4.5',
        system_message={'role': 'system', 'content': 'system'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[
            {
                'kind': 'document',
                'name': 'report.pdf',
                'chat_part': None,
                'responses_part': {'type': 'input_file', 'filename': 'report.pdf', 'file_data': 'data:application/pdf;base64,AAA'},
                'fallback_text': None,
            },
            {
                'kind': 'text',
                'name': 'notes.txt',
                'chat_part': None,
                'responses_part': None,
                'fallback_text': 'normalized fallback',
            },
        ],
        function_specs=[
            {
                'name': 'demo',
                'description': 'Demo tool',
                'parameters': {'type': 'object', 'properties': {'value': {'anyOf': [{'type': 'null'}, {'type': 'string'}]}}},
            }
        ],
    )

    assert payload['instructions'] == 'system'
    assert payload['parallel_tool_calls'] is True
    assert payload['tool_choice'] == 'required'
    assert payload['tools'][0] == {
        'type': 'function',
        'name': 'demo',
        'description': 'Demo tool',
        'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}},
    }
    user_message = payload['input'][0]
    assert user_message['type'] == 'message'
    assert user_message['role'] == 'user'
    assert user_message['content'][0] == {'type': 'input_text', 'text': 'hello'}
    assert any(part.get('type') == 'input_file' for part in user_message['content'])
    assert any(
        part == {
            'type': 'input_text',
            'text': '[Attachment: notes.txt]\nnormalized fallback',
        }
        for part in user_message['content']
    )


def test_build_responses_payload_preserves_request_reasoning_effort():
    pipe = _load_pipe_class()()

    payload = pipe._build_responses_payload(
        body={
            'model': 'bifrostapi.ZenMuxOAI/openai/gpt-5.4',
            'stream': True,
            'reasoning': {
                'enabled': True,
                'effort': 'xhigh',
                'summary': 'detailed',
                'max_tokens': 12400,
            },
        },
        model='ZenMuxOAI/openai/gpt-5.4',
        system_message={'role': 'system', 'content': 'system'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[],
        function_specs=[],
    )

    assert payload['reasoning'] == {
        'enabled': True,
        'effort': 'xhigh',
        'summary': 'detailed',
        'max_tokens': 12400,
    }


def test_build_responses_payload_omits_default_reasoning_summary_without_effort():
    pipe = _load_pipe_class()()
    pipe.valves.DEFAULT_REASONING_ENABLED = True
    pipe.valves.DEFAULT_REASONING_SUMMARY = 'detailed'
    pipe.valves.DEFAULT_REASONING_EFFORT = ''

    payload = pipe._build_responses_payload(
        body={
            'model': 'bifrostapi.ZenMuxOAI/openai/gpt-5.4',
            'stream': True,
        },
        model='ZenMuxOAI/openai/gpt-5.4',
        system_message={'role': 'system', 'content': 'system'},
        messages=[{'role': 'user', 'content': 'hello'}],
        attachments=[],
        function_specs=[],
    )

    assert 'reasoning' not in payload or 'summary' not in payload['reasoning']


def test_reasoning_param_error_detects_empty_level_rejection():
    pipe = _load_pipe_class()()

    assert pipe._is_reasoning_param_error(
        'level "" not supported, valid levels: low, medium, high, xhigh'
    )


def test_responses_streaming_function_call_arguments_emit_tool_calls_not_content():
    pipe = _load_pipe_class()()
    state = pipe._new_stream_state()
    chunks = []

    for event in [
        {
            'type': 'response.output_item.added',
            'output_index': 0,
            'item': {
                'type': 'function_call',
                'id': 'fc_1',
                'call_id': 'call_1',
                'name': 'generate_image',
                'arguments': '',
                'status': 'in_progress',
            },
        },
        {
            'type': 'response.function_call_arguments.delta',
            'output_index': 0,
            'item_id': 'fc_1',
            'delta': {'value': '{"prompt": "A quiet'},
        },
        {
            'type': 'response.function_call_arguments.delta',
            'output_index': 0,
            'item_id': 'fc_1',
            'delta': {'partial_json': ' mountain lake"}'},
        },
        {
            'type': 'response.function_call_arguments.done',
            'output_index': 0,
            'item_id': 'fc_1',
            'arguments': {'value': '{"prompt": "A quiet mountain lake"}'},
        },
    ]:
        chunk = pipe._parse_responses_event(event, state)
        if isinstance(chunk, list):
            chunks.extend(chunk)
        elif chunk:
            chunks.append(chunk)

    assert chunks
    assert all('content' not in chunk['choices'][0]['delta'] for chunk in chunks)

    tool_call_deltas = [
        chunk['choices'][0]['delta']['tool_calls'][0]
        for chunk in chunks
        if chunk['choices'][0]['delta'].get('tool_calls')
    ]
    assert tool_call_deltas[0]['function']['name'] == 'generate_image'
    assert ''.join(delta['function'].get('arguments', '') for delta in tool_call_deltas) == (
        '{"prompt": "A quiet mountain lake"}'
    )


def test_responses_web_search_lifecycle_emits_status_events_without_results():
    pipe = _load_pipe_class()()
    emitted = []
    state = pipe._new_stream_state(__event_emitter__=emitted.append)
    chunks = []

    for event in [
        {
            'type': 'response.output_item.added',
            'item': {
                'id': 'ws_1',
                'type': 'web_search_call',
                'status': 'in_progress',
            },
        },
        {
            'type': 'response.web_search_call.searching',
            'item_id': 'ws_1',
        },
        {
            'type': 'response.output_item.done',
            'item': {
                'id': 'ws_1',
                'type': 'web_search_call',
                'status': 'completed',
                'action': {
                    'type': 'search',
                    'query': 'weather: Shanghai, China',
                    'queries': ['weather: Shanghai, China'],
                },
            },
        },
    ]:
        chunk = pipe._parse_responses_event(event, state)
        if isinstance(chunk, list):
            chunks.extend(chunk)
        elif chunk:
            chunks.append(chunk)

    status_events = [event for event in emitted if event.get('type') == 'status']
    source_events = [event for event in emitted if event.get('type') == 'source']
    assert [event['data']['status'] for event in status_events] == [
        'in_progress',
        'in_progress',
        'complete',
    ]
    assert all(event['data']['action'] == 'web_search' for event in status_events)
    assert status_events[-1]['data']['query'] == 'weather: Shanghai, China'
    assert source_events == []
    assert all(not chunk['choices'][0]['delta'].get('tool_calls') for chunk in chunks)

    content = ''.join(
        chunk['choices'][0]['delta'].get('content', '')
        for chunk in chunks
        if chunk.get('choices')
    )
    assert 'Web Search' in content
    assert 'weather: Shanghai, China' in content


def test_responses_web_search_with_results_still_emits_sources():
    pipe = _load_pipe_class()()
    emitted = []
    state = pipe._new_stream_state(__event_emitter__=emitted.append)

    chunks = pipe._parse_responses_event(
        {
            'type': 'response.output_item.done',
            'item': {
                'id': 'ws_2',
                'type': 'web_search_call',
                'status': 'completed',
                'query': 'world cup schedule',
                'results': [
                    {
                        'title': 'Schedule',
                        'url': 'https://example.com/schedule',
                        'text': 'Match schedule summary',
                    }
                ],
            },
        },
        state,
    )

    assert isinstance(chunks, list)
    source_events = [event for event in emitted if event.get('type') == 'source']
    assert len(source_events) == 1
    assert source_events[0]['data']['source']['url'] == 'https://example.com/schedule'
    content = ''.join(
        chunk['choices'][0]['delta'].get('content', '')
        for chunk in chunks
        if chunk.get('choices')
    )
    assert 'world cup schedule' in content
    assert 'https://example.com/schedule' in content
