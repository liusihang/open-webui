import json
import logging
import types
from copy import deepcopy

import pytest
from open_webui.utils import middleware
from starlette.responses import StreamingResponse


class _FakeRequest:
    def __init__(self):
        self.state = types.SimpleNamespace()
        self.cookies = {}
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=types.SimpleNamespace(
                    RAG_TEMPLATE='{{CONTEXT}}\n{{QUERY}}',
                    ENABLE_USER_WEBHOOKS=False,
                    WEBUI_URL='http://test',
                    WEBUI_NAME='Open WebUI',
                )
            )
        )


class _FakeUser:
    id = 'user-1'
    role = 'user'

    def model_dump(self):
        return {'id': self.id, 'role': self.role}


def _ctx():
    async def knowledge_tool(query: str, count: int = 5):
        return f'Grounding evidence for {query} ({count})'

    return {
        'request': _FakeRequest(),
        'form_data': {
            'model': 'gpt-test',
            'stream': False,
            'messages': [{'role': 'user', 'content': 'Use the attached docs.'}],
        },
        'user': _FakeUser(),
        'model': {'info': {'meta': {'capabilities': {'citations': True}}}},
        'metadata': {
            'chat_id': '',
            'message_id': None,
            'session_id': None,
            'params': {'function_calling': 'native'},
            'tools': {
                'query_knowledge_files': {
                    'type': 'builtin',
                    'spec': {
                        'parameters': {
                            'properties': {
                                'query': {'type': 'string'},
                                'count': {'type': 'integer'},
                            }
                        }
                    },
                    'callable': knowledge_tool,
                }
            },
        },
        'tasks': None,
        'events': [],
        'event_emitter': None,
        'event_caller': None,
    }


def _tool_call_response():
    return {
        'id': 'chatcmpl-tool',
        'object': 'chat.completion',
        'model': 'gpt-test',
        'choices': [
            {
                'index': 0,
                'message': {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': [
                        {
                            'id': 'call_knowledge',
                            'type': 'function',
                            'function': {
                                'name': 'query_knowledge_files',
                                'arguments': '{"query":"Transformer layers","count":3}',
                            },
                        }
                    ],
                },
                'finish_reason': 'tool_calls',
            }
        ],
    }


def _responses_guard_base_form_data():
    return {
        'model': 'gpt-test',
        'prompt_cache_key': 'chat-cache-key',
        'temperature': 0.2,
        'text': {'verbosity': 'low'},
        'truncation': 'auto',
        'tools': [{'type': 'function', 'name': 'query_knowledge_files'}],
        'messages': [
            {'role': 'system', 'content': 'stable instructions'},
            {'role': 'user', 'content': 'Use the attached docs.'},
            {
                'role': 'assistant',
                'content': '',
                'tool_calls': [
                    {
                        'id': 'call_knowledge',
                        'type': 'function',
                        'function': {
                            'name': 'query_knowledge_files',
                            'arguments': '{"query":"Transformer layers"}',
                        },
                    }
                ],
            },
        ],
    }


def test_cache_debug_can_be_enabled_from_form_data_or_metadata():
    assert middleware.is_native_tool_cache_debug_enabled({'cache_debug': True}, {}) is True
    assert middleware.is_native_tool_cache_debug_enabled({'extra_body': {'cache_debug': 'true'}}, {}) is True
    assert middleware.is_native_tool_cache_debug_enabled({}, {'cache_debug': 1}) is True
    assert middleware.is_native_tool_cache_debug_enabled({}, {'params': {'cache_debug': '1'}}) is True

    assert middleware.is_native_tool_cache_debug_enabled({}, {}) is False
    assert middleware.is_native_tool_cache_debug_enabled({'cache_debug': False}, {}) is False
    assert middleware.is_native_tool_cache_debug_enabled({'extra_body': {'cache_debug': 'false'}}, {}) is False


def test_native_tool_continuation_fingerprint_redacts_content_and_reports_shape():
    form_data = {
        'model': 'gpt-test',
        'prompt_cache_key': 'chat-cache-key',
        'previous_response_id': 'resp_123',
        'messages': [
            {'role': 'system', 'content': 'RAW SYSTEM SECRET'},
            {'role': 'user', 'content': 'RAW USER QUESTION'},
            {
                'role': 'tool',
                'tool_call_id': 'call_knowledge',
                'content': 'RAW TOOL RESULT',
            },
        ],
        'tools': [{'type': 'function', 'function': {'name': 'query_knowledge_files'}}],
    }

    fingerprint = middleware.build_native_tool_continuation_request_fingerprint(
        form_data,
        metadata={'params': {'function_calling': 'native'}},
        route_mode='direct_stream',
        response_data={'usage': {'prompt_tokens_details': {'cached_tokens': 77}}},
    )

    serialized = json.dumps(fingerprint, sort_keys=True)
    assert 'RAW SYSTEM SECRET' not in serialized
    assert 'RAW USER QUESTION' not in serialized
    assert 'RAW TOOL RESULT' not in serialized
    assert fingerprint['model'] == 'gpt-test'
    assert fingerprint['route_mode'] == 'direct_stream'
    assert fingerprint['prompt_cache_key_hash']
    assert fingerprint['tools_hash']
    assert fingerprint['instructions_hash']
    assert fingerprint['message_count'] == 3
    assert fingerprint['messages_hash']
    assert fingerprint['previous_response_id_present'] is True
    assert fingerprint['continuation_mode'] == 'stateful_unchecked'
    assert fingerprint['cached_tokens'] == 77


def test_native_tool_continuation_fingerprint_logging_is_debug_gated(caplog):
    form_data = {
        'model': 'gpt-test',
        'messages': [{'role': 'user', 'content': 'RAW USER QUESTION'}],
    }
    metadata = {'params': {'function_calling': 'native'}}

    with caplog.at_level(logging.INFO, logger=middleware.log.name):
        middleware.log_native_tool_continuation_request_fingerprint(
            form_data,
            metadata=metadata,
            route_mode='direct_stream',
        )
    assert 'native_tool_continuation_request_fingerprint' not in caplog.text

    caplog.clear()
    form_data['cache_debug'] = True
    with caplog.at_level(logging.INFO, logger=middleware.log.name):
        middleware.log_native_tool_continuation_request_fingerprint(
            form_data,
            metadata=metadata,
            route_mode='direct_stream',
        )
    assert 'native_tool_continuation_request_fingerprint' in caplog.text
    assert 'RAW USER QUESTION' not in caplog.text


def test_responses_continuation_guard_accepts_exact_append_only_tool_output_delta():
    previous_form_data = _responses_guard_base_form_data()
    current_form_data = deepcopy(previous_form_data)
    current_form_data['messages'].append(
        {
            'role': 'tool',
            'tool_call_id': 'call_knowledge',
            'content': 'Grounding evidence',
        }
    )

    previous_state = middleware.build_responses_continuation_guard_state(
        previous_form_data,
        route_mode='websocket_responses_api',
    )
    result = middleware.evaluate_responses_continuation_delta(
        previous_state,
        current_form_data,
        route_mode='websocket_responses_api',
    )

    assert result['accepted'] is True
    assert result['continuation_mode'] == 'stateful_delta'
    assert result['reason'] == 'accepted'
    assert result['delta_messages'] == [current_form_data['messages'][-1]]


@pytest.mark.parametrize(
    ('mutate_current', 'reason'),
    [
        (lambda form_data: form_data.update({'model': 'gpt-other'}), 'model_changed'),
        (lambda form_data: form_data.update({'prompt_cache_key': 'other-cache-key'}), 'prompt_cache_key_changed'),
        (
            lambda form_data: form_data.update({'tools': [{'type': 'function', 'name': 'other_tool'}]}),
            'tools_changed',
        ),
        (
            lambda form_data: form_data['messages'][0].update({'content': 'rewritten instructions'}),
            'instructions_changed',
        ),
        (lambda form_data: form_data.update({'temperature': 0.9}), 'generation_controls_changed'),
        (lambda form_data: form_data.update({'text': {'verbosity': 'high'}}), 'generation_controls_changed'),
        (lambda form_data: form_data.update({'truncation': 'disabled'}), 'generation_controls_changed'),
        (
            lambda form_data: form_data['messages'][1].update({'content': 'rewritten user prompt'}),
            'input_not_strict_extension',
        ),
    ],
)
def test_responses_continuation_guard_rejects_shape_changes(mutate_current, reason):
    previous_form_data = _responses_guard_base_form_data()
    current_form_data = deepcopy(previous_form_data)
    current_form_data['messages'].append(
        {
            'role': 'tool',
            'tool_call_id': 'call_knowledge',
            'content': 'Grounding evidence',
        }
    )
    mutate_current(current_form_data)

    previous_state = middleware.build_responses_continuation_guard_state(
        previous_form_data,
        route_mode='websocket_responses_api',
    )
    result = middleware.evaluate_responses_continuation_delta(
        previous_state,
        current_form_data,
        route_mode='websocket_responses_api',
    )

    assert result['accepted'] is False
    assert result['continuation_mode'] == 'stateful_rejected'
    assert result['reason'] == reason
    assert result['delta_messages'] == []


def test_responses_continuation_guard_rejects_without_previous_state():
    current_form_data = _responses_guard_base_form_data()
    current_form_data['messages'].append(
        {
            'role': 'tool',
            'tool_call_id': 'call_knowledge',
            'content': 'Grounding evidence',
        }
    )

    result = middleware.evaluate_responses_continuation_delta(
        None,
        current_form_data,
        route_mode='websocket_responses_api',
    )

    assert result['accepted'] is False
    assert result['reason'] == 'missing_previous_guard_state'


def test_apply_responses_continuation_guard_rejects_previous_response_id_and_preserves_full_replay():
    previous_form_data = _responses_guard_base_form_data()
    current_form_data = deepcopy(previous_form_data)
    current_form_data['messages'][1]['content'] = 'rewritten user prompt'
    current_form_data['messages'].append(
        {
            'role': 'tool',
            'tool_call_id': 'call_knowledge',
            'content': 'Grounding evidence',
        }
    )

    previous_state = middleware.build_responses_continuation_guard_state(
        previous_form_data,
        route_mode='websocket_responses_api',
    )
    guarded_form_data, result = middleware.apply_responses_continuation_guard(
        current_form_data,
        previous_response_id='resp_123',
        previous_state=previous_state,
        route_mode='websocket_responses_api',
    )

    assert result['accepted'] is False
    assert result['reason'] == 'input_not_strict_extension'
    assert 'previous_response_id' not in guarded_form_data
    assert guarded_form_data['continuation_mode'] == 'stateful_rejected'
    assert guarded_form_data['messages'] == current_form_data['messages']


def test_apply_responses_continuation_guard_accepts_previous_response_id_and_sends_delta_messages():
    previous_form_data = _responses_guard_base_form_data()
    current_form_data = deepcopy(previous_form_data)
    current_form_data['messages'].append(
        {
            'role': 'tool',
            'tool_call_id': 'call_knowledge',
            'content': 'Grounding evidence',
        }
    )

    previous_state = middleware.build_responses_continuation_guard_state(
        previous_form_data,
        route_mode='websocket_responses_api',
    )
    guarded_form_data, result = middleware.apply_responses_continuation_guard(
        current_form_data,
        previous_response_id='resp_123',
        previous_state=previous_state,
        route_mode='websocket_responses_api',
    )

    assert result['accepted'] is True
    assert guarded_form_data['previous_response_id'] == 'resp_123'
    assert guarded_form_data['continuation_mode'] == 'stateful_delta'
    assert guarded_form_data['messages'] == [
        current_form_data['messages'][0],
        current_form_data['messages'][-1],
    ]


@pytest.mark.asyncio
async def test_direct_non_streaming_native_tool_calls_continue_to_final_answer(monkeypatch):
    captured = {}

    async def fake_generate_chat_completion(request, form_data, user, bypass_system_prompt=False, **kwargs):
        captured['form_data'] = form_data
        return {
            'id': 'chatcmpl-final',
            'object': 'chat.completion',
            'model': 'gpt-test',
            'choices': [
                {
                    'index': 0,
                    'message': {'role': 'assistant', 'content': 'Grounded final answer.'},
                    'finish_reason': 'stop',
                }
            ],
        }

    async def fake_process_tool_result(request, tool_name, tool_result, tool_type, direct_tool, metadata, user):
        return str(tool_result), [], []

    monkeypatch.setattr(middleware, 'generate_chat_completion', fake_generate_chat_completion)
    monkeypatch.setattr(middleware, 'process_tool_result', fake_process_tool_result)

    result = await middleware.non_streaming_chat_response_handler(_tool_call_response(), _ctx())

    assert result['choices'][0]['message']['content'] == 'Grounded final answer.'
    continuation_messages = captured['form_data']['messages']
    assert continuation_messages[-2]['role'] == 'assistant'
    assert continuation_messages[-2]['tool_calls'][0]['function']['name'] == 'query_knowledge_files'
    assert continuation_messages[-1] == {
        'role': 'tool',
        'tool_call_id': 'call_knowledge',
        'content': 'Grounding evidence for Transformer layers (3)',
    }


@pytest.mark.asyncio
async def test_non_streaming_native_continuation_restores_unbound_private_anchor_and_reapplies_rag_once(monkeypatch):
    captured = {}

    async def fake_generate_chat_completion(request, form_data, user, **kwargs):
        captured['form_data'] = form_data
        return {
            'id': 'chatcmpl-final',
            'object': 'chat.completion',
            'model': 'gpt-test',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'final'}, 'finish_reason': 'stop'}],
        }

    async def fake_process_tool_result(request, tool_name, tool_result, tool_type, direct_tool, metadata, user):
        return str(tool_result), [], []

    async def config_get(key, default=None):
        return 'RAG {{CONTEXT}}' if key == 'rag.template' else default

    monkeypatch.setattr(middleware, 'generate_chat_completion', fake_generate_chat_completion)
    monkeypatch.setattr(middleware, 'process_tool_result', fake_process_tool_result)
    monkeypatch.setattr(middleware.Config, 'get', config_get)
    monkeypatch.setattr(middleware, 'RAG_SYSTEM_CONTEXT', True)

    ctx = _ctx()
    ctx['pre_rag_system_anchor'] = 'UNBOUND PIPELINE SYSTEM LAYER'
    ctx['form_data']['messages'] = [
        {'role': 'system', 'content': 'STALE INITIAL RAG'},
        {'role': 'user', 'content': 'Use the attached docs.'},
    ]
    ctx['metadata'].update(
        {
            'user_prompt': 'Use the attached docs.',
            'sources': [
                {
                    'source': {'id': 'file-1', 'name': 'file-1'},
                    'document': ['attached evidence'],
                    'metadata': [{}],
                }
            ],
        }
    )

    await middleware.non_streaming_chat_response_handler(_tool_call_response(), ctx)

    system_message = captured['form_data']['messages'][0]
    assert system_message['content'].startswith('UNBOUND PIPELINE SYSTEM LAYER')
    assert system_message['content'].count('RAG <source') == 1
    assert 'STALE INITIAL RAG' not in system_message['content']


@pytest.mark.asyncio
async def test_direct_streaming_native_tool_calls_continue_without_final_tool_calls(monkeypatch):
    captured = {}

    async def initial_stream():
        chunks = [
            {
                'choices': [
                    {
                        'delta': {
                            'tool_calls': [
                                {
                                    'index': 0,
                                    'id': 'call_knowledge',
                                    'type': 'function',
                                    'function': {
                                        'name': 'query_knowledge_files',
                                        'arguments': '{"query":"Transformer',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': ' layers","count":3}'}}]}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]},
            '[DONE]',
        ]
        for chunk in chunks:
            if chunk == '[DONE]':
                yield 'data: [DONE]\n\n'
            else:
                yield f'data: {json.dumps(chunk)}\n\n'

    async def final_stream():
        yield 'data: {"choices":[{"delta":{"content":"Grounded final answer."}}]}\n\n'
        yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        yield 'data: [DONE]\n\n'

    async def fake_generate_chat_completion(request, form_data, user, bypass_system_prompt=False, **kwargs):
        captured['form_data'] = form_data
        return StreamingResponse(final_stream(), media_type='text/event-stream')

    async def fake_process_tool_result(request, tool_name, tool_result, tool_type, direct_tool, metadata, user):
        return str(tool_result), [], []

    async def no_filters(*args, **kwargs):
        return []

    monkeypatch.setattr(middleware, 'generate_chat_completion', fake_generate_chat_completion)
    monkeypatch.setattr(middleware, 'process_tool_result', fake_process_tool_result)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', no_filters)

    ctx = _ctx()
    ctx['form_data']['stream'] = True
    response = StreamingResponse(initial_stream(), media_type='text/event-stream')

    result = await middleware.streaming_chat_response_handler(response, ctx)

    body = ''
    async for chunk in result.body_iterator:
        body += chunk.decode() if isinstance(chunk, bytes) else chunk

    assert 'Grounded final answer.' in body
    assert 'finish_reason":"tool_calls' not in body.replace(' ', '')
    continuation_messages = captured['form_data']['messages']
    assert continuation_messages[-2]['role'] == 'assistant'
    assert continuation_messages[-1]['role'] == 'tool'
    assert continuation_messages[-1]['tool_call_id'] == 'call_knowledge'


@pytest.mark.asyncio
async def test_direct_streaming_native_continuation_restores_private_anchor_on_each_iteration(monkeypatch):
    captured = []

    async def tool_stream(call_id):
        yield (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"'
            + call_id
            + '","type":"function","function":{"name":"query_knowledge_files",'
            '"arguments":"{\\"query\\":\\"docs\\"}"}}]}}]}\n\n'
        )
        yield 'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        yield 'data: [DONE]\n\n'

    async def final_stream():
        yield 'data: {"choices":[{"delta":{"content":"final"}}]}\n\n'
        yield 'data: [DONE]\n\n'

    async def fake_generate(request, form_data, user, **kwargs):
        captured.append(form_data)
        stream = tool_stream('call-2') if len(captured) == 1 else final_stream()
        return StreamingResponse(stream, media_type='text/event-stream')

    async def fake_process_tool_result(request, tool_name, tool_result, tool_type, direct_tool, metadata, user):
        return str(tool_result), [], []

    async def no_filters(*args, **kwargs):
        return []

    async def config_get(key, default=None):
        return 'RAG {{CONTEXT}}' if key == 'rag.template' else default

    monkeypatch.setattr(middleware, 'generate_chat_completion', fake_generate)
    monkeypatch.setattr(middleware, 'process_tool_result', fake_process_tool_result)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', no_filters)
    monkeypatch.setattr(middleware.Config, 'get', config_get)
    monkeypatch.setattr(middleware, 'RAG_SYSTEM_CONTEXT', True)

    ctx = _ctx()
    ctx['form_data'].update(
        {
            'stream': True,
            'messages': [
                {'role': 'system', 'content': 'STALE INITIAL RAG'},
                {'role': 'user', 'content': 'Use the attached docs.'},
            ],
        }
    )
    ctx['pre_rag_system_anchor'] = 'UNBOUND PIPELINE SYSTEM LAYER'
    ctx['metadata'].update(
        {
            'user_prompt': 'Use the attached docs.',
            'sources': [
                {
                    'source': {'id': 'file-1', 'name': 'file-1'},
                    'document': ['attached evidence'],
                    'metadata': [{}],
                }
            ],
        }
    )

    response = StreamingResponse(tool_stream('call-1'), media_type='text/event-stream')
    result = await middleware.streaming_chat_response_handler(response, ctx)
    async for _chunk in result.body_iterator:
        pass

    assert len(captured) == 2
    for continuation in captured:
        system_message = continuation['messages'][0]
        assert system_message['content'].startswith('UNBOUND PIPELINE SYSTEM LAYER')
        assert system_message['content'].count('RAG <source') == 1
        assert 'STALE INITIAL RAG' not in system_message['content']


@pytest.mark.asyncio
async def test_streaming_native_tool_loop_restores_private_pre_rag_anchor_and_appends_rag_once(monkeypatch):
    captured = {}

    async def initial_stream():
        yield (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_knowledge",'
            '"type":"function","function":{"name":"query_knowledge_files","arguments":"{}"}}]}}]}\n\n'
        )
        yield 'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        yield 'data: [DONE]\n\n'

    async def second_tool_stream():
        yield (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_knowledge_2",'
            '"type":"function","function":{"name":"query_knowledge_files","arguments":"{}"}}]}}]}\n\n'
        )
        yield 'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        yield 'data: [DONE]\n\n'

    async def final_stream():
        yield 'data: {"choices":[{"delta":{"content":"Grounded final answer."}}]}\n\n'
        yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        yield 'data: [DONE]\n\n'

    async def fake_execute(_request, _form_data, _user, _metadata, response_tool_calls, **kwargs):
        call_id = response_tool_calls[0]['id']
        return (
            [{'tool_call_id': call_id, 'content': f'tool result {call_id}'}],
            [
                {
                    'source': {'id': 'tool-source', 'name': 'tool-source'},
                    'document': ['tool evidence'],
                    'metadata': [{}],
                }
            ],
        )

    async def fake_generate(request, form_data, user, **kwargs):
        captured.setdefault('form_data', []).append(form_data)
        stream = second_tool_stream if len(captured['form_data']) == 1 else final_stream
        return StreamingResponse(stream(), media_type='text/event-stream')

    async def no_filters(*args, **kwargs):
        return []

    async def no_oauth(*args, **kwargs):
        return None

    async def config_get(key, default=None):
        return 'RAG {{CONTEXT}}' if key == 'rag.template' else default

    emitted = []

    async def emit(event):
        emitted.append(event)

    monkeypatch.setattr(middleware, 'execute_native_tool_calls', fake_execute)
    monkeypatch.setattr(middleware, 'generate_chat_completion', fake_generate)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', no_filters)
    monkeypatch.setattr(middleware, 'get_system_oauth_token', no_oauth)
    monkeypatch.setattr(middleware.Config, 'get', config_get)
    monkeypatch.setattr(middleware, 'RAG_SYSTEM_CONTEXT', True)
    monkeypatch.setattr(middleware, 'ENABLE_REALTIME_CHAT_SAVE', False)

    anchor = 'ADMINISTRATOR LAYER\nGLOBAL LAYER\nMODEL LAYER\nREQUEST LAYER\nUSER LAYER'
    ctx = _ctx()
    ctx.update(
        {
            'form_data': {
                'model': 'gpt-test',
                'stream': True,
                'messages': [
                    {'role': 'system', 'content': 'STALE INITIAL RAG'},
                    {'role': 'user', 'content': 'Use the attached docs.'},
                ],
            },
            'metadata': {
                'chat_id': 'channel:test',
                'message_id': 'message-1',
                'session_id': None,
                'params': {'function_calling': 'native'},
                'sources': [],
                'user_prompt': 'Use the attached docs.',
            },
            'event_emitter': emit,
            'pre_rag_system_anchor': anchor,
        }
    )

    response = StreamingResponse(initial_stream(), media_type='text/event-stream')
    result = await middleware.streaming_chat_response_handler(response, ctx)

    assert result is None

    assert len(captured['form_data']) == 2
    system_message = captured['form_data'][-1]['messages'][0]
    assert system_message['role'] == 'system'
    assert system_message['content'].startswith(anchor)
    assert system_message['content'].count('RAG <source') == 1
    assert 'STALE INITIAL RAG' not in system_message['content']
    assert 'system_prompt' not in captured['form_data'][-1]['metadata']
    assert emitted
