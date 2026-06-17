from __future__ import annotations

from typing import Any

import httpx

from agentscope_runtime.schemas import AppendEventRequest


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
