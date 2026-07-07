from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from agentscope_runtime.schemas import (
    AppendEventRequest,
    FinalDeltaRequest,
    ModelCallRequest,
    ModelSelectionRequest,
    StateTransitionRequest,
    SubagentRegisterRequest,
    TextDeltaRequest,
    ToolCallRequest,
    UserInputRequest,
)


MODEL_CALL_IN_PROGRESS_POLL_SECONDS = 0.25


class OpenWebUIClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout: float = 10.0,
        model_call_timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout
        self._model_call_poll_timeout = model_call_timeout
        self._model_call_timeout = httpx.Timeout(model_call_timeout, connect=timeout)

    async def append_event(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        event_type: str,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        participant_id: str | None = None,
        phase: str | None = None,
    ) -> dict[str, Any]:
        body = AppendEventRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            event_type=event_type,
            summary=summary,
            payload=payload or {},
            participant_id=participant_id,
            phase=phase,
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/events"
        headers = {
            "Authorization": f"Bearer {self._service_token}",
            "X-Agent-Idempotency-Key": idempotency_key,
        }

        payload = body.model_dump(mode="json")
        response = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=payload,
                    )
                break
            except httpx.TimeoutException:
                if attempt == 1:
                    raise

        if response.status_code == 409:
            return _safe_response_json(response) or {
                "detail": "idempotency_conflict",
            }

        if response.is_error:
            raise RuntimeError(
                "OpenWebUI append-event failed "
                f"with status {response.status_code}: {response.text}",
            )
        return response.json()

    async def append_final_delta(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        final_stream_id: str,
        delta_index: int,
        delta: str,
        participant_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = FinalDeltaRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            final_stream_id=final_stream_id,
            delta_index=delta_index,
            delta=delta,
            participant_id=participant_id,
            payload=payload or {},
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/final-delta"
        return await self._post_callback(url, idempotency_key, body.model_dump(mode="json"))

    async def append_text_delta(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        block_id: str,
        block_kind: str,
        delta_index: int,
        delta: str,
        participant_id: str | None = None,
        phase: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = TextDeltaRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            block_id=block_id,
            block_kind=block_kind,
            delta_index=delta_index,
            delta=delta,
            participant_id=participant_id,
            phase=phase,
            payload=payload or {},
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/text-delta"
        return await self._post_callback(url, idempotency_key, body.model_dump(mode="json"))

    async def transition_state(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        from_states: list[str],
        to_state: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = StateTransitionRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            from_states=from_states,
            to_state=to_state,
            reason=reason,
            payload=payload or {},
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/state-transition"
        return await self._post_callback(url, idempotency_key, body.model_dump(mode="json"))

    async def register_subagent(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        parent_participant_id: str,
        participant_id: str,
        name: str,
        description: str,
        task: str,
        budget: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = SubagentRegisterRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            parent_participant_id=parent_participant_id,
            participant_id=participant_id,
            name=name,
            description=description,
            task=task,
            budget=budget or {},
            metadata=metadata or {},
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/subagents"
        return await self._post_callback(url, idempotency_key, body.model_dump(mode="json"))

    async def select_model(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        selection_id: str,
        requested_model_id: str | None = None,
        fuzzy_request: str | None = None,
        source_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = ModelSelectionRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            participant_id=participant_id,
            selection_id=selection_id,
            requested_model_id=requested_model_id,
            fuzzy_request=fuzzy_request,
            source_request=source_request or {},
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/model-selection"
        return await self._post_callback(url, idempotency_key, body.model_dump(mode="json"))

    async def request_user_input(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        user_input_id: str,
        tool_call_id: str,
        message: str,
        requested_schema: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        allow_cancel: bool = True,
    ) -> dict[str, Any]:
        body = UserInputRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            participant_id=participant_id,
            user_input_id=user_input_id,
            tool_call_id=tool_call_id,
            message=message,
            requested_schema=requested_schema or {},
            timeout_seconds=timeout_seconds,
            allow_cancel=allow_cancel,
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/user-input-requests"
        request_timeout = _user_input_timeout(timeout_seconds, self._timeout)
        return await self._post_callback(
            url,
            idempotency_key,
            body.model_dump(mode="json"),
            timeout=request_timeout,
        )

    async def call_model(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        model_call_id: str,
        model: str,
        messages: list[dict[str, Any]] | None = None,
        stream: bool = False,
        params: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = ModelCallRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            participant_id=participant_id,
            model_call_id=model_call_id,
            model=model,
            messages=messages or [],
            stream=stream,
            tools=tools,
            tool_choice=tool_choice,
            params=params or {},
            metadata=metadata or {},
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/model-call"
        return await self._post_callback(
            url,
            idempotency_key,
            body.model_dump(mode="json", exclude_none=True),
            timeout=self._model_call_timeout,
            retry_operation_in_progress=True,
            operation_poll_timeout=self._model_call_poll_timeout,
        )

    async def call_tool(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        tool_call_id: str,
        tool_id: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = ToolCallRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            participant_id=participant_id,
            tool_call_id=tool_call_id,
            tool_id=tool_id,
            arguments=arguments or {},
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/tool-call"
        return await self._post_callback(url, idempotency_key, body.model_dump(mode="json"))

    async def _post_callback(
        self,
        url: str,
        idempotency_key: str,
        body: dict[str, Any],
        *,
        timeout: float | httpx.Timeout | None = None,
        retry_operation_in_progress: bool = False,
        operation_poll_timeout: float | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._service_token}",
            "X-Agent-Idempotency-Key": idempotency_key,
        }
        deadline = (
            time.monotonic() + operation_poll_timeout
            if operation_poll_timeout is not None
            else None
        )
        client_timeout = timeout if timeout is not None else self._timeout

        async with httpx.AsyncClient(timeout=client_timeout) as client:
            while True:
                response = await client.post(url, headers=headers, json=body)
                payload = _safe_response_json(response)

                if (
                    retry_operation_in_progress
                    and response.status_code == 202
                    and isinstance(payload, dict)
                    and payload.get("detail") == "operation_in_progress"
                ):
                    if deadline is not None and time.monotonic() >= deadline:
                        raise RuntimeError(
                            "OpenWebUI callback still in progress "
                            f"after {operation_poll_timeout} seconds",
                        )
                    await asyncio.sleep(_poll_sleep_seconds(deadline))
                    continue

                break

        if response.is_error:
            raise RuntimeError(
                "OpenWebUI callback failed "
                f"with status {response.status_code}: {response.text}",
            )
        return payload if payload is not None else response.json()

    async def call_model_stream(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        model_call_id: str,
        model: str,
        messages: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Stream a model call's response as an async generator of parsed
        SSE events.

        Yields dicts of shape:
            {'type': 'chunk', 'delta': {'content': str | None,
                                        'tool_calls': list | None}}
            {'type': 'done', 'response': <full response dict>}  # non-stream fallback
            {'type': 'stream_end', 'model_call_id': str}         # terminal

        The caller is responsible for accumulating text and tool_calls
        across chunks.
        """
        body = ModelCallRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            participant_id=participant_id,
            model_call_id=model_call_id,
            model=model,
            messages=messages or [],
            stream=True,
            tools=tools,
            tool_choice=tool_choice,
            params=params or {},
            metadata=metadata or {},
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/model-call"
        headers = {
            "Authorization": f"Bearer {self._service_token}",
            "X-Agent-Idempotency-Key": idempotency_key,
            "Accept": "text/event-stream",
        }

        async with httpx.AsyncClient(timeout=self._model_call_timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=body.model_dump(mode="json", exclude_none=True),
            ) as response:
                if response.status_code == 202:
                    payload = _safe_response_json_sync(response)
                    if (
                        isinstance(payload, dict)
                        and payload.get("detail") == "operation_in_progress"
                    ):
                        raise _ModelCallOperationInProgress(payload)
                if response.is_error:
                    text = await response.aread()
                    raise RuntimeError(
                        "OpenWebUI model-call stream failed "
                        f"with status {response.status_code}: "
                        f"{text.decode('utf-8', 'replace')}",
                    )

                async for event in _iter_sse_events(response):
                    yield event


async def _iter_sse_events(response: httpx.Response):
    """Parse an SSE stream into typed event dicts.

    Handles two payload shapes:
    - Provider passthrough: standard OpenAI-style `data: {...}` lines,
      where the JSON is an OpenAI chat completion chunk. These are
      parsed into {'type': 'chunk', 'delta': {'content': ..., 'tool_calls': ...}}.
    - OpenWebUI meta events: `data: {"type": "done|stream_end", "payload": ...}`.
      These are passed through with 'type' and 'payload' keys.
    """
    async for raw_line in response.aiter_lines():
        if not raw_line:
            continue
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data:
            continue
        if data == "[DONE]":
            yield {"type": "stream_end"}
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "type" in payload and "payload" in payload:
            # OpenWebUI meta event (done/stream_end)
            yield {"type": payload["type"], "payload": payload["payload"]}
            continue
        # OpenAI-style chunk
        yield _parse_openai_chunk(payload)


def _parse_openai_chunk(payload: Any) -> dict[str, Any]:
    """Extract text delta and tool_call deltas from an OpenAI chat chunk."""
    delta: dict[str, Any] = {"content": None, "tool_calls": None}
    if not isinstance(payload, dict):
        return {"type": "chunk", "delta": delta}
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            choice_delta = choice.get("delta")
            if isinstance(choice_delta, dict):
                content = choice_delta.get("content")
                if isinstance(content, str):
                    delta["content"] = content
                tool_calls = choice_delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    delta["tool_calls"] = tool_calls
    return {"type": "chunk", "delta": delta}


def _safe_response_json_sync(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


class _ModelCallOperationInProgress(RuntimeError):
    """Internal signal: /model-call returned 202 operation_in_progress."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("operation_in_progress")
        self.payload = payload


def _safe_response_json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except ValueError:
        return None


def _poll_sleep_seconds(deadline: float | None) -> float:
    if deadline is None:
        return MODEL_CALL_IN_PROGRESS_POLL_SECONDS
    remaining = max(deadline - time.monotonic(), 0.0)
    return min(MODEL_CALL_IN_PROGRESS_POLL_SECONDS, remaining)


def _user_input_timeout(timeout_seconds: float | None, connect_timeout: float) -> httpx.Timeout:
    try:
        wait_seconds = float(timeout_seconds) if timeout_seconds is not None else 300.0
    except (TypeError, ValueError):
        wait_seconds = 300.0
    total = max(wait_seconds, 0.0) + max(connect_timeout, 1.0)
    return httpx.Timeout(total, connect=connect_timeout)
