import asyncio
import json

import httpx
import pytest
import respx
from agentscope_runtime.openwebui_client import OpenWebUIClient
from httpx import Response
from pydantic import ValidationError


def test_parse_openai_chunk_preserves_private_reasoning_delta() -> None:
    from agentscope_runtime.openwebui_client import _parse_openai_chunk

    event = _parse_openai_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "reasoning_content": "先检查工具状态。",
                        "thinking": "ignored because reasoning_content wins",
                        "content": "公开回答",
                        "tool_calls": [],
                    }
                }
            ]
        }
    )

    assert event == {
        "type": "chunk",
        "delta": {
            "content": "公开回答",
            "tool_calls": [],
            "reasoning_content": "先检查工具状态。",
        },
    }


def test_parse_openai_chunk_preserves_valid_assistant_phase() -> None:
    from agentscope_runtime.openwebui_client import _parse_openai_chunk

    event = _parse_openai_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "content": "I will inspect the environment.",
                        "phase": "commentary",
                    }
                }
            ]
        }
    )

    assert event["delta"]["phase"] == "commentary"


def test_parse_openai_chunk_omits_invalid_assistant_phase() -> None:
    from agentscope_runtime.openwebui_client import _parse_openai_chunk

    event = _parse_openai_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "content": "Untyped content.",
                        "phase": "running",
                    }
                }
            ]
        }
    )

    assert "phase" not in event["delta"]


def test_parse_openai_chunk_preserves_provider_auxiliary_content_marker() -> None:
    from agentscope_runtime.openwebui_client import _parse_openai_chunk

    event = _parse_openai_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "content": "Web search results.",
                        "content_kind": "provider_auxiliary",
                        "auxiliary_type": "web_search_result",
                    }
                }
            ]
        }
    )

    assert event["delta"]["content_kind"] == "provider_auxiliary"
    assert event["delta"]["auxiliary_type"] == "web_search_result"


def test_parse_openai_chunk_preserves_structured_stream_error() -> None:
    from agentscope_runtime.openwebui_client import _parse_openai_chunk

    event = _parse_openai_chunk(
        {
            "error": {
                "message": "provider failed",
                "code": "provider_error",
            }
        }
    )

    assert event == {
        "type": "error",
        "error": {
            "message": "provider failed",
            "code": "provider_error",
        },
    }


@pytest.mark.asyncio
async def test_call_model_uses_dedicated_model_call_timeout(monkeypatch) -> None:
    captured_timeouts = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            captured_timeouts.append(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, json=None):
            return Response(
                200,
                json={
                    "status": "success",
                    "model": "model-research",
                    "response": {"content": "model answer"},
                    "metadata": {},
                },
            )

    monkeypatch.setattr("agentscope_runtime.openwebui_client.httpx.AsyncClient", FakeAsyncClient)
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
        timeout=3.0,
        model_call_timeout=45.0,
    )

    await client.append_final_delta(
        run_id="run-1",
        idempotency_key="final:run-1:answer:0",
        final_stream_id="answer",
        delta_index=0,
        delta="final answer",
    )
    await client.call_model(
        run_id="run-1",
        idempotency_key="model:leader:model-call-1:1",
        participant_id="leader",
        model_call_id="model-call-1",
        model="model-research",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert captured_timeouts[0] == 3.0
    assert captured_timeouts[1].connect == 3.0
    assert captured_timeouts[1].read == 45.0


def test_model_call_timeout_layers_are_owned_independently() -> None:
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
        timeout=3.0,
        model_call_connect_timeout=2.0,
        model_call_read_idle_timeout=15.0,
        model_call_total_timeout=120.0,
    )

    assert client._model_call_timeout.connect == 2.0
    assert client._model_call_timeout.read == 15.0
    assert client._model_call_total_timeout == 120.0


@pytest.mark.asyncio
async def test_sse_comments_are_transport_only_and_not_model_chunks() -> None:
    from agentscope_runtime.openwebui_client import _iter_sse_events

    class CommentedResponse:
        async def aiter_lines(self):
            for line in (
                ': openwebui-stream-start',
                '',
                ': bifrost-response-in-progress',
                '',
                'data: {"choices":[{"delta":{"content":"hello"}}]}',
                '',
                'data: [DONE]',
            ):
                yield line

    events = [event async for event in _iter_sse_events(CommentedResponse())]

    assert events == [
        {
            'type': 'chunk',
            'delta': {'content': 'hello', 'tool_calls': None},
        },
        {'type': 'stream_end'},
    ]


@pytest.mark.asyncio
async def test_sse_clean_eof_without_terminal_event_is_protocol_error() -> None:
    from agentscope_runtime.openwebui_client import _iter_sse_events

    class IncompleteResponse:
        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"partial"}}]}'
            yield ''

    stream = _iter_sse_events(IncompleteResponse())
    assert await anext(stream) == {
        'type': 'chunk',
        'delta': {'content': 'partial', 'tool_calls': None},
    }
    with pytest.raises(RuntimeError, match='model_stream_incomplete'):
        await anext(stream)


@pytest.mark.asyncio
async def test_sse_finish_reason_emits_terminal_event_and_stops() -> None:
    from agentscope_runtime.openwebui_client import _iter_sse_events

    class FinishedResponse:
        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield ''
            yield 'data: {"error":{"message":"must not be consumed"}}'

    events = [event async for event in _iter_sse_events(FinishedResponse())]

    assert events == [
        {
            'type': 'chunk',
            'delta': {'content': None, 'tool_calls': None},
            'finish_reason': 'stop',
        },
        {'type': 'stream_end'},
    ]


@pytest.mark.asyncio
async def test_sse_meta_done_is_terminal_and_ignores_later_lines() -> None:
    from agentscope_runtime.openwebui_client import _iter_sse_events

    class DoneResponse:
        async def aiter_lines(self):
            yield 'data: {"type":"done","payload":{"response":{"ok":true}}}'
            yield ''
            yield 'data: {"error":{"message":"must not be consumed"}}'

    events = [event async for event in _iter_sse_events(DoneResponse())]

    assert events == [
        {'type': 'done', 'payload': {'response': {'ok': True}}},
    ]


@pytest.mark.asyncio
async def test_sse_responses_completed_is_terminal_and_ignores_later_lines() -> None:
    from agentscope_runtime.openwebui_client import _iter_sse_events

    class CompletedResponse:
        async def aiter_lines(self):
            yield 'data: {"type":"response.completed","response":{"status":"completed"}}'
            yield ''
            yield 'data: {"error":{"message":"must not be consumed"}}'

    events = [event async for event in _iter_sse_events(CompletedResponse())]

    assert events == [
        {
            'type': 'stream_end',
            'payload': {
                'type': 'response.completed',
                'response': {'status': 'completed'},
            },
        },
    ]


@pytest.mark.asyncio
async def test_sse_responses_incomplete_is_error_and_ignores_later_lines() -> None:
    from agentscope_runtime.openwebui_client import _iter_sse_events

    class IncompleteResponse:
        async def aiter_lines(self):
            yield (
                'data: {"type":"response.incomplete","response":'
                '{"incomplete_details":{"reason":"max_output_tokens"}}}'
            )
            yield ''
            yield 'data: {"type":"response.completed"}'

    events = [event async for event in _iter_sse_events(IncompleteResponse())]

    assert events == [
        {
            'type': 'error',
            'error': {
                'code': 'response.incomplete',
                'message': 'max_output_tokens',
                'reason': 'max_output_tokens',
            },
        },
    ]


@pytest.mark.asyncio
async def test_call_model_stream_does_not_repost_in_progress_operation(
    monkeypatch,
) -> None:
    requests: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, status_code: int, body: bytes, lines=()) -> None:
            self.status_code = status_code
            self._body = body
            self._lines = lines

        @property
        def is_error(self) -> bool:
            return self.status_code >= 400

        async def aread(self) -> bytes:
            return self._body

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class FakeStreamContext:
        def __init__(self, response: FakeResponse) -> None:
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeAsyncClient:
        def __init__(self, *, timeout) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, *, headers, json):
            requests.append({"method": method, "url": url, "headers": headers, "json": json})
            return FakeStreamContext(
                FakeResponse(202, b'{"detail":"operation_in_progress"}')
            )

    monkeypatch.setattr(
        "agentscope_runtime.openwebui_client.httpx.AsyncClient",
        FakeAsyncClient,
    )
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
        model_call_total_timeout=1.0,
    )

    with pytest.raises(RuntimeError, match="operation_in_progress"):
        async for _ in client.call_model_stream(
            run_id="run-1",
            idempotency_key="model:leader:model-call-1:1",
            participant_id="leader",
            model_call_id="model-call-1",
            model="model-a",
            messages=[{"role": "user", "content": "hello"}],
        ):
            pass

    assert len(requests) == 1
    assert {
        request["headers"]["X-Agent-Idempotency-Key"] for request in requests
    } == {"model:leader:model-call-1:1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectTimeout("connect stalled"), "stream connect timeout after 2.0 seconds"),
        (httpx.ReadTimeout("stream idle"), "stream read-idle timeout after 15.0 seconds"),
    ],
)
async def test_call_model_stream_names_transport_timeout_owner(
    monkeypatch,
    failure: httpx.TimeoutException,
    expected: str,
) -> None:
    class FakeStreamContext:
        async def __aenter__(self):
            raise failure

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeAsyncClient:
        def __init__(self, *, timeout) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, *args, **kwargs):
            return FakeStreamContext()

    monkeypatch.setattr(
        "agentscope_runtime.openwebui_client.httpx.AsyncClient",
        FakeAsyncClient,
    )
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
        model_call_connect_timeout=2.0,
        model_call_read_idle_timeout=15.0,
        model_call_total_timeout=120.0,
    )

    with pytest.raises(RuntimeError, match=expected):
        async for _ in client.call_model_stream(
            run_id="run-1",
            idempotency_key="model:leader:model-call-1:1",
            participant_id="leader",
            model_call_id="model-call-1",
            model="model-a",
        ):
            pass


@pytest.mark.asyncio
async def test_call_model_stream_enforces_total_timeout(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        is_error = False

        async def aiter_lines(self):
            await asyncio.sleep(60)
            yield "data: [DONE]"

    class FakeStreamContext:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeAsyncClient:
        def __init__(self, *, timeout) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, *args, **kwargs):
            return FakeStreamContext()

    monkeypatch.setattr(
        "agentscope_runtime.openwebui_client.httpx.AsyncClient",
        FakeAsyncClient,
    )
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
        model_call_total_timeout=0.01,
    )

    with pytest.raises(RuntimeError, match="stream total timeout after 0.1 seconds"):
        async for _ in client.call_model_stream(
            run_id="run-1",
            idempotency_key="model:leader:model-call-1:1",
            participant_id="leader",
            model_call_id="model-call-1",
            model="model-a",
        ):
            pass


@pytest.mark.asyncio
async def test_call_model_stream_surfaces_structured_queued_rejection() -> None:
    url = "https://openwebui.test/api/agent/service/runs/run-queued/model-call"
    async with respx.mock(assert_all_called=True) as router:
        router.post(url).mock(
            return_value=Response(
                403,
                json={
                    "detail": {
                        "code": "model_run_rejected",
                        "message": (
                            "Agent run run-queued cannot execute model calls while queued"
                        ),
                        "current_state": "queued",
                    }
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        with pytest.raises(
            RuntimeError,
            match="model_run_rejected.*while queued",
        ):
            async for _ in client.call_model_stream(
                run_id="run-queued",
                idempotency_key="model:leader:model-call-1:1",
                participant_id="leader",
                model_call_id="model-call-1",
                model="model-a",
                messages=[{"role": "user", "content": "hello"}],
            ):
                pass


@pytest.mark.asyncio
async def test_append_event_sends_bearer_auth_idempotency_key_and_structured_payload() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post("https://openwebui.test/api/agent/service/runs/run-1/events").mock(
            return_value=Response(200, json={"seq": 1, "event_type": "run.running"})
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_event(
            run_id="run-1",
            idempotency_key="evt:session:event-1",
            event_type="run.running",
            summary="Agent runtime accepted run.",
            payload={"runtime_session_id": "session"},
            participant_id="leader",
            phase="running",
        )

    assert response == {"seq": 1, "event_type": "run.running"}
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert request.calls.last.request.headers["x-agent-idempotency-key"] == "evt:session:event-1"
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "evt:session:event-1",
        "run_id": "run-1",
        "event_type": "run.running",
        "summary": "Agent runtime accepted run.",
        "payload": {"runtime_session_id": "session"},
        "participant_id": "leader",
        "phase": "running",
    }


@pytest.mark.asyncio
async def test_append_event_surfaces_callback_failure() -> None:
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://openwebui.test/api/agent/service/runs/run-1/events").mock(
            return_value=Response(503, json={"detail": "down"})
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        with pytest.raises(RuntimeError, match="OpenWebUI append-event failed"):
            await client.append_event(
                run_id="run-1",
                idempotency_key="evt:session:event-1",
                event_type="run.running",
            )


@pytest.mark.asyncio
async def test_append_event_treats_409_idempotency_conflict_as_success() -> None:
    """When openwebui returns 409 idempotency_conflict for event.append, the
    runtime must treat it as success (event already stored) and not crash the
    agent run. See docs/handoff-agent-runtime-streaming-text-2026-06-20.md.
    """
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://openwebui.test/api/agent/service/runs/run-1/events").mock(
            return_value=Response(
                409,
                json={"detail": "idempotency_conflict", "seq": 7},
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_event(
            run_id="run-1",
            idempotency_key="evt:session:sub-1:completed",
            event_type="subagent.completed",
            summary="Subagent finished.",
            payload={"participant_id": "sub-1", "content": "ok"},
            participant_id="sub-1",
            phase="running",
        )

    assert response["detail"] == "idempotency_conflict"
    assert response["seq"] == 7


@pytest.mark.asyncio
async def test_append_event_treats_409_with_empty_body_as_idempotency_conflict() -> None:
    """A 409 without a JSON body still resolves to a synthetic
    idempotency_conflict payload rather than raising."""
    async with respx.mock(assert_all_called=True) as router:
        router.post("https://openwebui.test/api/agent/service/runs/run-1/events").mock(
            return_value=Response(409, text="")
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_event(
            run_id="run-1",
            idempotency_key="evt:session:run-running",
            event_type="run.running",
        )

    assert response["detail"] == "idempotency_conflict"


@pytest.mark.asyncio
async def test_append_event_retries_timeout_with_same_idempotency_key(monkeypatch) -> None:
    calls = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, json=None):
            calls.append({'url': url, 'headers': headers, 'json': json, 'timeout': self.timeout})
            if len(calls) == 1:
                raise httpx.ReadTimeout('event append still in flight')
            return Response(409, json={'detail': 'idempotency_conflict', 'seq': 12})

    monkeypatch.setattr("agentscope_runtime.openwebui_client.httpx.AsyncClient", FakeAsyncClient)
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
        timeout=3.0,
    )

    response = await client.append_event(
        run_id="run-1",
        idempotency_key="evt:session:run-running",
        event_type="run.running",
        summary="Agent runtime accepted run.",
        payload={"runtime_session_id": "session"},
        participant_id="leader",
        phase="running",
    )

    assert response == {'detail': 'idempotency_conflict', 'seq': 12}
    assert len(calls) == 2
    assert calls[0]['json'] == calls[1]['json']
    assert calls[0]['headers']['X-Agent-Idempotency-Key'] == 'evt:session:run-running'
    assert calls[1]['headers']['X-Agent-Idempotency-Key'] == 'evt:session:run-running'


@pytest.mark.asyncio
async def test_append_event_polls_operation_in_progress_after_timeout_with_same_idempotency_key(
    monkeypatch,
) -> None:
    calls = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, json=None):
            calls.append({'url': url, 'headers': headers, 'json': json, 'timeout': self.timeout})
            if len(calls) == 1:
                raise httpx.ReadTimeout('event append still in flight')
            if len(calls) == 2:
                return Response(202, json={'detail': 'operation_in_progress'})
            return Response(200, json={'seq': 13, 'event_type': 'run.failed'})

    monkeypatch.setattr("agentscope_runtime.openwebui_client.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("agentscope_runtime.openwebui_client.MODEL_CALL_IN_PROGRESS_POLL_SECONDS", 0)
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
        timeout=3.0,
    )

    response = await client.append_event(
        run_id="run-1",
        idempotency_key="evt:session:run-failed",
        event_type="run.failed",
        summary="Agent runtime failed.",
        payload={"runtime_session_id": "session"},
        participant_id="leader",
        phase="failed",
    )

    assert response == {'seq': 13, 'event_type': 'run.failed'}
    assert len(calls) == 3
    assert {call['headers']['X-Agent-Idempotency-Key'] for call in calls} == {
        'evt:session:run-failed'
    }


@pytest.mark.asyncio
async def test_append_final_delta_uses_openwebui_final_delta_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post("https://openwebui.test/api/agent/service/runs/run-1/final-delta").mock(
            return_value=Response(200, json={"seq": 3, "event_type": "final.delta"})
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_final_delta(
            run_id="run-1",
            idempotency_key="final:run-1:answer:0",
            final_stream_id="answer",
            delta_index=0,
            delta="final answer",
            participant_id="leader",
            payload={"runtime_session_id": "session"},
        )

    assert response == {"seq": 3, "event_type": "final.delta"}
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert request.calls.last.request.headers["x-agent-idempotency-key"] == "final:run-1:answer:0"
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "final:run-1:answer:0",
        "run_id": "run-1",
        "final_stream_id": "answer",
        "delta_index": 0,
        "delta": "final answer",
        "participant_id": "leader",
        "payload": {"runtime_session_id": "session"},
    }


@pytest.mark.asyncio
async def test_append_text_delta_includes_public_block_kind() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post("https://openwebui.test/api/agent/service/runs/run-1/text-delta").mock(
            return_value=Response(200, json={"seq": 4, "event_type": "text.delta"})
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.append_text_delta(
            run_id="run-1",
            idempotency_key="text:run-1:leader:block-1:0",
            block_id="block-1",
            block_kind="assistant_note",
            delta_index=0,
            delta="Public progress note.",
            participant_id="leader",
            phase="running",
            payload={"runtime_session_id": "session"},
        )

    assert response == {"seq": 4, "event_type": "text.delta"}
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert request.calls.last.request.headers["x-agent-idempotency-key"] == "text:run-1:leader:block-1:0"
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "text:run-1:leader:block-1:0",
        "run_id": "run-1",
        "block_id": "block-1",
        "block_kind": "assistant_note",
        "delta_index": 0,
        "delta": "Public progress note.",
        "participant_id": "leader",
        "phase": "running",
        "payload": {"runtime_session_id": "session"},
    }


@pytest.mark.asyncio
async def test_append_text_delta_rejects_debug_payload_before_callback() -> None:
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
    )

    with pytest.raises(ValidationError, match="debug"):
        await client.append_text_delta(
            run_id="run-1",
            idempotency_key="text:run-1:leader:block-1:0",
            block_id="block-1",
            block_kind="assistant_note",
            delta_index=0,
            delta="Public progress note.",
            payload={"debug": {"trace": "private"}},
        )


@pytest.mark.asyncio
async def test_transition_state_uses_openwebui_state_transition_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/state-transition"
        ).mock(return_value=Response(200, json={"id": "run-1", "state": "finalizing"}))
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.transition_state(
            run_id="run-1",
            idempotency_key="state:run-1:finalizing",
            from_states=["running"],
            to_state="finalizing",
            reason="runtime closed work",
            payload={"runtime_session_id": "session"},
        )

    assert response == {"id": "run-1", "state": "finalizing"}
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert request.calls.last.request.headers["x-agent-idempotency-key"] == "state:run-1:finalizing"
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "state:run-1:finalizing",
        "run_id": "run-1",
        "from_states": ["running"],
        "to_state": "finalizing",
        "reason": "runtime closed work",
        "payload": {"runtime_session_id": "session"},
    }


@pytest.mark.asyncio
async def test_transition_state_retries_timeout_and_polls_operation_in_progress_with_same_key(
    monkeypatch,
) -> None:
    calls = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, json=None):
            calls.append({'url': url, 'headers': headers, 'json': json, 'timeout': self.timeout})
            if len(calls) == 1:
                raise httpx.ReadTimeout('state transition still in flight')
            if len(calls) == 2:
                return Response(202, json={'detail': 'operation_in_progress'})
            return Response(200, json={'id': 'run-1', 'state': 'failed'})

    monkeypatch.setattr("agentscope_runtime.openwebui_client.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("agentscope_runtime.openwebui_client.MODEL_CALL_IN_PROGRESS_POLL_SECONDS", 0)
    client = OpenWebUIClient(
        base_url="https://openwebui.test",
        service_token="owui-token",
        timeout=3.0,
    )

    response = await client.transition_state(
        run_id="run-1",
        idempotency_key="state:run-1:failed",
        from_states=["running"],
        to_state="failed",
        reason="runtime finalization failed",
        payload={"runtime_session_id": "session"},
    )

    assert response == {'id': 'run-1', 'state': 'failed'}
    assert len(calls) == 3
    assert {call['headers']['X-Agent-Idempotency-Key'] for call in calls} == {
        'state:run-1:failed'
    }


@pytest.mark.asyncio
async def test_register_subagent_sends_callback_contract() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post("https://openwebui.test/api/agent/service/runs/run-1/subagents").mock(
            return_value=Response(
                200,
                json={
                    "status": "accepted",
                    "participant_id": "subagent:run-1:1",
                    "team_cap": 5,
                    "remaining_slots": 4,
                    "warnings": [],
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.register_subagent(
            run_id="run-1",
            idempotency_key="subagent:run-1:subagent:run-1:1:create",
            parent_participant_id="leader",
            participant_id="subagent:run-1:1",
            name="researcher",
            description="Researches facts.",
            task="Find facts.",
            budget={"max_model_calls": 2},
            metadata={"team_cap": 5, "single_level": True},
        )

    assert response["status"] == "accepted"
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert (
        request.calls.last.request.headers["x-agent-idempotency-key"]
        == "subagent:run-1:subagent:run-1:1:create"
    )
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "subagent:run-1:subagent:run-1:1:create",
        "run_id": "run-1",
        "parent_participant_id": "leader",
        "participant_id": "subagent:run-1:1",
        "name": "researcher",
        "description": "Researches facts.",
        "task": "Find facts.",
        "budget": {"max_model_calls": 2},
        "metadata": {"team_cap": 5, "single_level": True},
    }


@pytest.mark.asyncio
async def test_select_model_uses_openwebui_model_selection_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/model-selection"
        ).mock(
            return_value=Response(
                200,
                json={
                    "selected_model_id": "model-research",
                    "choices": [{"id": "model-research"}],
                    "meta": {"agent_selection": {"reason": "fuzzy_match"}},
                    "warnings": [],
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.select_model(
            run_id="run-1",
            idempotency_key="modelsel:subagent:run-1:1:selection-1:1",
            participant_id="subagent:run-1:1",
            selection_id="selection-1",
            requested_model_id=None,
            fuzzy_request="research long context",
            source_request={"task": "Find facts."},
        )

    assert response["selected_model_id"] == "model-research"
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert (
        request.calls.last.request.headers["x-agent-idempotency-key"]
        == "modelsel:subagent:run-1:1:selection-1:1"
    )
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "modelsel:subagent:run-1:1:selection-1:1",
        "run_id": "run-1",
        "participant_id": "subagent:run-1:1",
        "selection_id": "selection-1",
        "requested_model_id": None,
        "fuzzy_request": "research long context",
        "source_request": {"task": "Find facts."},
    }


@pytest.mark.asyncio
async def test_call_model_uses_openwebui_model_call_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/model-call"
        ).mock(
            return_value=Response(
                200,
                json={
                    "status": "success",
                    "model": "model-research",
                    "response": {"content": "model answer"},
                    "metadata": {"participant_id": "subagent:run-1:1"},
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.call_model(
            run_id="run-1",
            idempotency_key="model:subagent:run-1:1:model-call-1:1",
            participant_id="subagent:run-1:1",
            model_call_id="model-call-1",
            model="model-research",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            stream=False,
            params={"temperature": 0.2},
            metadata={"runtime_session_id": "rt-run-1"},
        )

    assert response["response"]["content"] == "model answer"
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert (
        request.calls.last.request.headers["x-agent-idempotency-key"]
        == "model:subagent:run-1:1:model-call-1:1"
    )
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "model:subagent:run-1:1:model-call-1:1",
        "run_id": "run-1",
        "participant_id": "subagent:run-1:1",
        "model_call_id": "model-call-1",
        "model": "model-research",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "stream": False,
        "params": {"temperature": 0.2},
        "metadata": {"runtime_session_id": "rt-run-1"},
    }


@pytest.mark.asyncio
async def test_call_model_retries_operation_in_progress_until_cached_success() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/model-call"
        ).mock(
            side_effect=[
                Response(202, json={"detail": "operation_in_progress"}),
                Response(202, json={"detail": "operation_in_progress"}),
                Response(
                    200,
                    json={
                        "status": "success",
                        "model": "model-research",
                        "response": {"content": "cached model answer"},
                        "metadata": {"operation_id": "model-call-1"},
                    },
                ),
            ]
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.call_model(
            run_id="run-1",
            idempotency_key="model:leader:model-call-1:1",
            participant_id="leader",
            model_call_id="model-call-1",
            model="model-research",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert response["response"]["content"] == "cached model answer"
    assert len(request.calls) == 3
    assert {call.request.headers["x-agent-idempotency-key"] for call in request.calls} == {
        "model:leader:model-call-1:1"
    }


@pytest.mark.asyncio
async def test_call_model_sends_tools_and_tool_choice_as_top_level_callback_fields() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/model-call"
        ).mock(
            return_value=Response(
                200,
                json={
                    "status": "success",
                    "model": "model-research",
                    "response": {"content": "model answer"},
                    "metadata": {},
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        await client.call_model(
            run_id="run-1",
            idempotency_key="model:leader:model-call-1:1",
            participant_id="leader",
            model_call_id="model-call-1",
            model="model-research",
            messages=[{"role": "user", "content": "read"}],
            stream=False,
            params={"temperature": 0.2},
            tools=tools,
            tool_choice="auto",
            metadata={"runtime_session_id": "rt-run-1"},
        )

    body = json.loads(request.calls.last.request.content)
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
    assert body["params"] == {"temperature": 0.2}
    assert "tools" not in body["params"]
    assert "tool_choice" not in body["params"]


@pytest.mark.asyncio
async def test_call_tool_uses_openwebui_tool_call_callback() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/tool-call"
        ).mock(
            return_value=Response(
                200,
                json={
                    "status": "success",
                    "content": "tool answer",
                    "files": [],
                    "embeds": [],
                    "sources": [],
                    "artifacts": [],
                    "process_refs": [],
                    "warnings": [],
                    "structured_error": None,
                    "raw": None,
                },
            )
        )
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        response = await client.call_tool(
            run_id="run-1",
            idempotency_key="tool:subagent:run-1:1:tool-call-1:1",
            participant_id="subagent:run-1:1",
            tool_call_id="tool-call-1",
            tool_id="tool-search",
            arguments={"query": "agent mode"},
        )

    assert response["content"] == "tool answer"
    assert request.calls.last.request.headers["authorization"] == "Bearer owui-token"
    assert (
        request.calls.last.request.headers["x-agent-idempotency-key"]
        == "tool:subagent:run-1:1:tool-call-1:1"
    )
    assert "x-agent-decision-execution-id" not in request.calls.last.request.headers
    assert json.loads(request.calls.last.request.content) == {
        "idempotency_key": "tool:subagent:run-1:1:tool-call-1:1",
        "run_id": "run-1",
        "participant_id": "subagent:run-1:1",
        "tool_call_id": "tool-call-1",
        "tool_id": "tool-search",
        "arguments": {"query": "agent mode"},
    }


@pytest.mark.asyncio
async def test_approved_tool_replay_sends_decision_execution_identity_only_in_header() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/tool-call"
        ).mock(return_value=Response(200, json={"status": "success", "content": "done"}))
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        await client.call_tool(
            run_id="run-1",
            idempotency_key="tool:leader:provider-call-1:1",
            participant_id="leader",
            tool_call_id="provider-call-1",
            tool_id="tool-write",
            arguments={"path": "/workspace/report.txt"},
            decision_execution_id="rex-1",
        )

    assert request.calls.last.request.headers["x-agent-decision-execution-id"] == "rex-1"
    assert "execution_id" not in json.loads(request.calls.last.request.content)


@pytest.mark.asyncio
async def test_user_input_request_sends_durable_checkpoint_version() -> None:
    async with respx.mock(assert_all_called=True) as router:
        request = router.post(
            "https://openwebui.test/api/agent/service/runs/run-1/user-input-requests"
        ).mock(return_value=Response(200, json={"status": "requested"}))
        client = OpenWebUIClient(
            base_url="https://openwebui.test",
            service_token="owui-token",
        )

        await client.request_user_input(
            run_id="run-1",
            idempotency_key="user-input:leader:tool-call-1:1",
            participant_id="leader",
            user_input_id="user-input:run-1:tool-call-1",
            tool_call_id="tool-call-1",
            message="Choose one",
            requested_schema={"type": "object"},
            checkpoint_version=7,
        )

    body = json.loads(request.calls.last.request.content)
    assert body["checkpoint_version"] == 7
