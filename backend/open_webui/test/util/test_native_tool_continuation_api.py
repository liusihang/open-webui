import json
import logging
import types

import pytest
from starlette.responses import StreamingResponse

from open_webui.utils import middleware


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

    monkeypatch.setattr(middleware, 'generate_chat_completion', fake_generate_chat_completion)
    monkeypatch.setattr(middleware, 'process_tool_result', fake_process_tool_result)
    monkeypatch.setattr(middleware, 'get_sorted_filter_ids', lambda *args, **kwargs: middleware.asyncio.sleep(0, result=[]))

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
