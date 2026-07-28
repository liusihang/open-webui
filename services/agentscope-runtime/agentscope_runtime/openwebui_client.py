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
        model_call_timeout: float | None = None,
        model_call_connect_timeout: float | None = None,
        model_call_read_idle_timeout: float | None = None,
        model_call_total_timeout: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout
        legacy_timeout = (
            max(float(model_call_timeout), 0.1)
            if model_call_timeout is not None
            else None
        )
        connect_timeout = max(
            float(
                model_call_connect_timeout
                if model_call_connect_timeout is not None
                else timeout
            ),
            0.1,
        )
        read_idle_timeout = max(
            float(
                model_call_read_idle_timeout
                if model_call_read_idle_timeout is not None
                else legacy_timeout if legacy_timeout is not None else 30.0
            ),
            0.1,
        )
        total_timeout = max(
            float(
                model_call_total_timeout
                if model_call_total_timeout is not None
                else legacy_timeout if legacy_timeout is not None else 300.0
            ),
            0.1,
        )
        self._model_call_poll_timeout = total_timeout
        self._model_call_total_timeout = total_timeout
        self._model_call_timeout = httpx.Timeout(
            read_idle_timeout,
            connect=connect_timeout,
            write=max(float(timeout), 0.1),
            pool=max(float(timeout), 0.1),
        )

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
        return await self._post_callback(
            url,
            idempotency_key,
            body.model_dump(mode="json"),
            retry_operation_in_progress=True,
            operation_poll_timeout=self._timeout,
            retry_timeout_attempts=2,
            accept_idempotency_conflict=True,
            error_prefix="OpenWebUI append-event failed",
        )

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
        return await self._post_callback(
            url,
            idempotency_key,
            body.model_dump(mode="json"),
            retry_operation_in_progress=True,
            operation_poll_timeout=self._timeout,
            retry_timeout_attempts=2,
        )

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
        checkpoint_version: int,
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
            checkpoint_version=checkpoint_version,
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
        try:
            async with asyncio.timeout(self._model_call_total_timeout):
                return await self._post_callback(
                    url,
                    idempotency_key,
                    body.model_dump(mode="json", exclude_none=True),
                    timeout=self._model_call_timeout,
                    retry_operation_in_progress=True,
                    operation_poll_timeout=self._model_call_poll_timeout,
                )
        except httpx.ConnectTimeout as exc:
            raise RuntimeError(
                "OpenWebUI model-call connect timeout "
                f"after {self._model_call_timeout.connect} seconds"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise RuntimeError(
                "OpenWebUI model-call read-idle timeout "
                f"after {self._model_call_timeout.read} seconds"
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(
                "OpenWebUI model-call total timeout "
                f"after {self._model_call_total_timeout} seconds"
            ) from exc

    async def call_tool(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        tool_call_id: str,
        tool_id: str,
        arguments: dict[str, Any] | None = None,
        checkpoint_version: int | None = None,
        decision_execution_id: str | None = None,
    ) -> dict[str, Any]:
        body = ToolCallRequest(
            idempotency_key=idempotency_key,
            run_id=run_id,
            participant_id=participant_id,
            tool_call_id=tool_call_id,
            tool_id=tool_id,
            arguments=arguments or {},
            checkpoint_version=checkpoint_version,
        )
        url = f"{self._base_url}/api/agent/service/runs/{run_id}/tool-call"
        return await self._post_callback(
            url,
            idempotency_key,
            body.model_dump(mode="json", exclude_none=True),
            timeout=_user_input_timeout(None, self._timeout),
            extra_headers=(
                {"X-Agent-Decision-Execution-ID": decision_execution_id}
                if decision_execution_id
                else None
            ),
        )

    async def _post_callback(
        self,
        url: str,
        idempotency_key: str,
        body: dict[str, Any],
        *,
        timeout: float | httpx.Timeout | None = None,
        retry_operation_in_progress: bool = False,
        operation_poll_timeout: float | None = None,
        retry_timeout_attempts: int = 1,
        accept_idempotency_conflict: bool = False,
        error_prefix: str = "OpenWebUI callback failed",
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._service_token}",
            "X-Agent-Idempotency-Key": idempotency_key,
        }
        headers.update(extra_headers or {})
        deadline = (
            time.monotonic() + operation_poll_timeout
            if operation_poll_timeout is not None
            else None
        )
        client_timeout = timeout if timeout is not None else self._timeout
        timeout_attempts = 0

        async with httpx.AsyncClient(timeout=client_timeout) as client:
            while True:
                try:
                    response = await client.post(url, headers=headers, json=body)
                except httpx.TimeoutException:
                    timeout_attempts += 1
                    if timeout_attempts >= retry_timeout_attempts:
                        raise
                    continue
                payload = _safe_response_json(response)

                if accept_idempotency_conflict and response.status_code == 409:
                    return payload or {"detail": "idempotency_conflict"}

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
                f"{error_prefix} with status {response.status_code}: {response.text}",
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

        try:
            async with asyncio.timeout(self._model_call_total_timeout):
                async with httpx.AsyncClient(
                    timeout=self._model_call_timeout
                ) as client:
                    request_body = body.model_dump(mode="json", exclude_none=True)
                    async with client.stream(
                        "POST",
                        url,
                        headers=headers,
                        json=request_body,
                    ) as response:
                        if response.status_code == 202:
                            raw_body = await response.aread()
                            raise RuntimeError(
                                "OpenWebUI model-call stream was not started; "
                                f"status 202: {raw_body.decode('utf-8', 'replace')}",
                            )
                        if response.is_error:
                            raw_body = await response.aread()
                            raise RuntimeError(
                                "OpenWebUI model-call stream failed "
                                f"with status {response.status_code}: "
                                f"{raw_body.decode('utf-8', 'replace')}",
                            )

                        async for event in _iter_sse_events(response):
                            yield event
                        return
        except httpx.ConnectTimeout as exc:
            raise RuntimeError(
                "OpenWebUI model-call stream connect timeout "
                f"after {self._model_call_timeout.connect} seconds"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise RuntimeError(
                "OpenWebUI model-call stream read-idle timeout "
                f"after {self._model_call_timeout.read} seconds"
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(
                "OpenWebUI model-call stream total timeout "
                f"after {self._model_call_total_timeout} seconds"
            ) from exc


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
        if isinstance(payload, dict):
            event_type = str(payload.get("type") or "").strip()
            if event_type in {"response.failed", "response.incomplete"}:
                yield {
                    "type": "error",
                    "error": _model_stream_event_error(payload, event_type),
                }
                return
            if event_type == "response.completed":
                yield {"type": "stream_end", "payload": payload}
                return
            if event_type in {"done", "stream_end"}:
                event = {"type": event_type}
                if "payload" in payload:
                    event["payload"] = payload["payload"]
                yield event
                return
        # OpenAI-style chunk
        event = _parse_openai_chunk(payload)
        yield event
        if event["type"] == "error":
            return
        if event.get("finish_reason"):
            yield {"type": "stream_end"}
            return
    raise RuntimeError(
        "model_stream_incomplete: OpenWebUI model-call stream ended "
        "without a terminal event"
    )


def _parse_openai_chunk(payload: Any) -> dict[str, Any]:
    """Extract text delta and tool_call deltas from an OpenAI chat chunk."""
    delta: dict[str, Any] = {"content": None, "tool_calls": None}
    if not isinstance(payload, dict):
        return {"type": "chunk", "delta": delta}
    error = payload.get("error")
    if isinstance(error, dict):
        return {"type": "error", "error": error}
    if isinstance(error, str) and error:
        return {"type": "error", "error": {"message": error}}
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            finish_reason = choice.get("finish_reason")
            choice_delta = choice.get("delta")
            if isinstance(choice_delta, dict):
                content = choice_delta.get("content")
                if isinstance(content, str):
                    delta["content"] = content
                phase = str(choice_delta.get("phase") or "").strip()
                if phase in {"commentary", "final_answer"}:
                    delta["phase"] = phase
                content_kind = str(choice_delta.get("content_kind") or "").strip()
                if content_kind == "provider_auxiliary":
                    delta["content_kind"] = content_kind
                    auxiliary_type = str(
                        choice_delta.get("auxiliary_type") or ""
                    ).strip()
                    if auxiliary_type:
                        delta["auxiliary_type"] = auxiliary_type
                reasoning_content = (
                    choice_delta.get("reasoning_content")
                    or choice_delta.get("reasoning")
                    or choice_delta.get("thinking")
                )
                if isinstance(reasoning_content, str) and reasoning_content:
                    delta["reasoning_content"] = reasoning_content
                tool_calls = choice_delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    delta["tool_calls"] = tool_calls
            if finish_reason:
                return {
                    "type": "chunk",
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
    return {"type": "chunk", "delta": delta}


def _model_stream_event_error(
    payload: dict[str, Any],
    event_type: str,
) -> dict[str, Any]:
    error = payload.get("error")
    response = payload.get("response")
    if error is None and isinstance(response, dict):
        error = response.get("error") or response.get("incomplete_details")
    if isinstance(error, dict):
        result = dict(error)
        result.setdefault("code", event_type)
        message = str(result.get("message") or result.get("reason") or "").strip()
        if not message:
            message = json.dumps(error, ensure_ascii=False, separators=(",", ":"))
        result["message"] = message or event_type
        return result
    if isinstance(error, str) and error:
        return {"code": event_type, "message": error}
    return {"code": event_type, "message": event_type}


def _safe_json_bytes(raw_body: bytes) -> Any | None:
    try:
        return json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


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
