"""Send a Responses request over WS and expose the usual Responses result or SSE."""

import asyncio
import re
from contextlib import aclosing
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from fastapi.responses import StreamingResponse
from multidict import CIMultiDict
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.response import handle_responses_streaming_event
from open_webui.utils.session_pool import get_client_timeout, get_session
from starlette.background import BackgroundTask

_RESULT_EVENTS = {'response.completed', 'response.incomplete'}
_TERMINAL_EVENTS = _RESULT_EVENTS | {'error', 'response.failed'}


class ResponsesWebSocketError(Exception):
    def __init__(self, message: str, status: int = 502, error: dict | None = None):
        super().__init__(message)
        self.status = status
        self.error = {**(error or {}), 'message': message}


def uses_responses_websocket(api_config: dict) -> bool:
    return (
        api_config.get('api_type') == 'responses'
        and api_config.get('responses_transport') == 'websocket'
        and not (api_config.get('azure') or api_config.get('provider') == 'azure')
    )


def _websocket_url(url: str) -> str:
    parsed = urlsplit(url)
    scheme = {'http': 'ws', 'https': 'wss'}.get(parsed.scheme)
    if not scheme or not parsed.hostname:
        raise ResponsesWebSocketError('Responses WebSocket requires an HTTP or HTTPS base URL.', 400)
    return urlunsplit(parsed._replace(scheme=scheme))


def _websocket_headers(headers, cookies):
    headers = CIMultiDict(headers)
    if cookies:
        jar = SimpleCookie(headers.get('Cookie', ''))
        for name, value in cookies.items():
            jar[name] = value
        headers['Cookie'] = jar.output(header='', sep=';').strip()
    return headers


def _event_error(event: dict) -> ResponsesWebSocketError:
    if event['type'] == 'response.failed':
        error = (event.get('response') or {}).get('error')
    else:
        error = event.get('error') or {key: event[key] for key in ('message', 'code', 'param') if key in event}
    error = error if isinstance(error, dict) else {}
    status = event.get('status')
    return ResponsesWebSocketError(
        error.get('message') or 'Responses WebSocket generation failed.',
        status if isinstance(status, int) and 400 <= status <= 599 else 502,
        error,
    )


def _parse_event(message):
    if message.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
        raise ResponsesWebSocketError('Responses WebSocket closed before the response completed.')
    try:
        event = JSONCodec.loads(message.data)
    except ValueError as exc:
        raise ResponsesWebSocketError('Invalid JSON from the Responses WebSocket.') from exc
    event_type = event.get('type') if isinstance(event, dict) else None
    if not isinstance(event_type, str) or not re.fullmatch(r'[\w.-]+', event_type):
        raise ResponsesWebSocketError('Invalid event from the Responses WebSocket.')

    if event_type == 'response.done':
        status = (event.get('response') or {}).get('status')
        event_type = (
            f'response.{status}'
            if status in ('failed', 'incomplete', 'cancelled', 'canceled')
            else 'response.completed'
        )
        event['type'] = event_type
    if event_type in ('response.cancelled', 'response.canceled'):
        raise ResponsesWebSocketError('Responses WebSocket generation was cancelled.')
    if event_type == 'error':
        # Gateways may wrap the error while Open WebUI reads its message at the top level.
        error = _event_error(event)
        event = {**event, **{k: v for k, v in error.error.items() if k != 'type'}}
    return event


async def _response_events(socket, deadline):
    async with socket:
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    message = await socket.receive()
            except TimeoutError as exc:
                raise ResponsesWebSocketError('Responses WebSocket timed out.', 504) from exc
            except aiohttp.ClientError as exc:
                raise ResponsesWebSocketError('Responses WebSocket connection was interrupted.') from exc

            event = _parse_event(message)
            yield event
            if event['type'] in _TERMINAL_EVENTS:
                return


async def _response_sse(events, include_event_names):
    def frame(event):
        name = f'event: {event["type"]}\n' if include_event_names else ''
        return f'{name}data: {JSONCodec.dumps(event)}\n\n'

    async with aclosing(events):
        try:
            async for event in events:
                yield frame(event)
        except ResponsesWebSocketError as exc:
            # The client has already received the stream headers.
            yield frame({'type': 'error', 'message': str(exc), 'error': exc.error})


def _encode_create_event(payload):
    if payload.get('background'):
        raise ResponsesWebSocketError('Background generation requires HTTP Responses transport.', 400)
    message = {key: value for key, value in payload.items() if key not in ('stream', 'stream_options', 'background')}
    effort = message.pop('reasoning_effort', None)
    reasoning = message.get('reasoning')
    if effort is not None and (reasoning is None or isinstance(reasoning, dict)):
        # The chat controls use Chat Completions naming; Responses takes a reasoning object.
        message['reasoning'] = {'effort': effort, **(reasoning or {})}
    message['type'] = 'response.create'
    return JSONCodec.dumps(message)


async def send_responses_websocket(url, payload, headers, cookies, *, include_event_names=False):
    """Run one generation per socket, retaining the existing history and tool replay."""
    stream = bool(payload.get('stream'))
    timeout = get_client_timeout(stream=stream)
    deadline = asyncio.get_running_loop().time() + timeout.total if timeout.total else None
    message = _encode_create_event(payload)
    session = await get_session()
    socket = None
    sent = False
    try:
        async with asyncio.timeout_at(deadline):
            socket = await session.ws_connect(
                _websocket_url(url),
                headers=_websocket_headers(headers, cookies),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
                timeout=aiohttp.ClientWSTimeout(ws_receive=timeout.sock_read, ws_close=5),
            )
            await socket.send_str(message)
            sent = True
    except aiohttp.WSServerHandshakeError as exc:
        status = exc.status if 400 <= exc.status <= 599 else 502
        raise ResponsesWebSocketError(f'Responses WebSocket handshake failed (HTTP {exc.status}).', status) from exc
    except TimeoutError as exc:
        raise ResponsesWebSocketError('Responses WebSocket timed out.', 504) from exc
    except aiohttp.ClientError as exc:
        raise ResponsesWebSocketError('Could not connect to the Responses WebSocket.') from exc
    finally:
        # After a successful send the iterator owns the socket; close it on failed sends or cancellation.
        if socket is not None and not sent:
            await socket.close()

    events = _response_events(socket, deadline)
    if stream:
        return StreamingResponse(
            _response_sse(events, include_event_names),
            media_type='text/event-stream',
            background=BackgroundTask(socket.close),
        )

    output = []
    async with aclosing(events):
        async for event in events:
            if event['type'] in {'error', 'response.failed'}:
                raise _event_error(event)
            output, _ = handle_responses_streaming_event(event, output)
            if event['type'] in _RESULT_EVENTS:
                result = event['response']
                return {**result, 'output': result.get('output') or output}

    raise ResponsesWebSocketError('Responses WebSocket did not return a result.')
