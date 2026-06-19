from __future__ import annotations

import asyncio
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
    ToolCallRequest,
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

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                headers=headers,
                json=body.model_dump(mode="json"),
            )

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
