import asyncio
import json
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, web
from open_webui.utils.responses_websocket import ResponsesWebSocketError, send_responses_websocket


class TestResponsesWebSocket(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = {}
        self.events = []
        self.status = 101
        self.close_early = False
        self.disconnected = asyncio.Event()

        app = web.Application()
        app.router.add_get('/v1/responses', self.provider)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        self.url = f'http://127.0.0.1:{runner.addresses[0][1]}/v1/responses'
        self.session = ClientSession()
        self.addAsyncCleanup(self.session.close)
        get_session = patch('open_webui.utils.responses_websocket.get_session', return_value=self.session)
        get_session.start()
        self.addCleanup(get_session.stop)

    async def provider(self, request):
        if self.status != 101:
            return web.Response(status=self.status)
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        try:
            self.received['authorization'] = request.headers.get('Authorization')
            self.received['cookie'] = request.cookies.get('provider_session')
            self.received['message'] = await socket.receive_json()
            for event in self.events:
                await socket.send_json(event)
            if self.close_early:
                await socket.close()
            async for _ in socket:
                pass
        finally:
            self.disconnected.set()
        return socket

    async def send(self, *, stream=True, include_event_names=False):
        return await send_responses_websocket(
            self.url,
            {
                'model': 'gpt-test',
                'input': [{'role': 'user', 'content': 'hi'}],
                'stream': stream,
                'reasoning_effort': 'low',
                'reasoning': {'summary': 'auto'},
                'tool_choice': 'none',
                'store': False,
            },
            {'Authorization': 'Bearer test'},
            {'provider_session': 'abc'},
            include_event_names=include_event_names,
        )

    async def test_streamed_response_and_terminal_usage(self):
        self.events = [
            {'type': 'response.output_text.delta', 'delta': 'hello'},
            {'type': 'response.output_text.delta', 'delta': ' world'},
            {'type': 'response.completed', 'response': {'id': 'resp_1', 'usage': {'output_tokens': 2}}},
        ]
        response = await self.send(include_event_names=True)
        self.assertEqual(response.headers['content-type'], 'text/event-stream; charset=utf-8')
        frames = [frame async for frame in response.body_iterator]
        self.assertEqual(frames[0].splitlines()[0], 'event: response.output_text.delta')
        self.assertEqual([json.loads(frame.splitlines()[1][6:]) for frame in frames], self.events)
        self.assertEqual(self.received['authorization'], 'Bearer test')
        self.assertEqual(self.received['cookie'], 'abc')
        self.assertEqual(
            self.received['message'],
            {
                'type': 'response.create',
                'model': 'gpt-test',
                'input': [{'role': 'user', 'content': 'hi'}],
                'reasoning': {'effort': 'low', 'summary': 'auto'},
                'tool_choice': 'none',
                'store': False,
            },
        )
        await asyncio.wait_for(self.disconnected.wait(), timeout=2)

    async def test_nonstreaming_returns_tool_items_and_usage(self):
        tool_call = {'type': 'function_call', 'call_id': 'call_1', 'name': 'lookup', 'arguments': '{"value":7}'}
        self.events = [
            {'type': 'response.output_item.done', 'output_index': 0, 'item': tool_call},
            {
                'type': 'response.done',
                'response': {'id': 'resp_1', 'status': 'completed', 'output': [], 'usage': {'output_tokens': 5}},
            },
        ]
        result = await self.send(stream=False)
        self.assertEqual(result['output'], [tool_call])
        self.assertEqual(result['usage'], {'output_tokens': 5})
        await asyncio.wait_for(self.disconnected.wait(), timeout=2)

    async def test_upstream_error_retains_status_and_code(self):
        self.events = [{'type': 'error', 'status': 429, 'error': {'code': 'rate_limit', 'message': 'Slow down'}}]
        with self.assertRaises(ResponsesWebSocketError) as error:
            await self.send(stream=False)
        self.assertEqual(error.exception.status, 429)
        self.assertEqual(error.exception.error, {'code': 'rate_limit', 'message': 'Slow down'})

    async def test_rejected_upgrade_returns_handshake_status(self):
        self.status = 401
        with self.assertRaises(ResponsesWebSocketError) as error:
            await self.send()
        self.assertEqual(error.exception.status, 401)
        self.assertFalse(self.received)

    async def test_close_early_keeps_text_and_reports_error(self):
        self.close_early = True
        self.events = [{'type': 'response.output_text.delta', 'delta': 'partial'}]
        response = await self.send()
        events = [json.loads(frame[6:]) async for frame in response.body_iterator]
        self.assertEqual(events[0], self.events[0])
        self.assertEqual(events[-1]['type'], 'error')

    async def test_closing_response_body_disconnects_provider(self):
        self.events = [{'type': 'response.output_text.delta', 'delta': 'partial'}]
        response = await self.send()
        await anext(response.body_iterator)
        await response.body_iterator.aclose()
        await asyncio.wait_for(self.disconnected.wait(), timeout=2)
