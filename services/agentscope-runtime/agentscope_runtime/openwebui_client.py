from __future__ import annotations

from typing import Any

import httpx

from agentscope_runtime.schemas import (
    AppendEventRequest,
    ModelSelectionRequest,
    SubagentRegisterRequest,
)


class OpenWebUIClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout

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

    async def _post_callback(
        self,
        url: str,
        idempotency_key: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._service_token}",
            "X-Agent-Idempotency-Key": idempotency_key,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.is_error:
            raise RuntimeError(
                "OpenWebUI callback failed "
                f"with status {response.status_code}: {response.text}",
            )
        return response.json()
