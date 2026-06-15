import json
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
