from __future__ import annotations

from typing import Any

import aiohttp


class AgentRuntimeError(RuntimeError):
    code = 'agent_runtime_error'


class AgentRuntimeUnavailable(AgentRuntimeError):
    code = 'agent_runtime_unavailable'


class AgentRuntimeRejected(AgentRuntimeError):
    code = 'agent_runtime_rejected'


class AgentRuntimeClient:
    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout: int | float | None = None,
    ) -> None:
        self.base_url = (base_url or '').rstrip('/')
        self.service_token = service_token or None
        self.timeout = timeout

    async def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise AgentRuntimeUnavailable('agent runtime base URL is not configured')

        return await self._post('/v1/openwebui/runs', payload)

    async def cancel_run(self, run_id: str) -> dict[str, Any]:
        if not self.base_url:
            raise AgentRuntimeUnavailable('agent runtime base URL is not configured')

        return await self._post(f'/v1/openwebui/runs/{run_id}/cancel', None)

    async def notify_approval_decision(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise AgentRuntimeUnavailable('agent runtime base URL is not configured')

        return await self._post(f'/v1/openwebui/runs/{run_id}/approval-decision', payload)

    async def _post(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        headers = {'Content-Type': 'application/json'}
        if self.service_token:
            headers['Authorization'] = f'Bearer {self.service_token}'

        timeout = aiohttp.ClientTimeout(total=self.timeout) if self.timeout else None
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f'{self.base_url}{path}',
                    json=payload,
                    headers=headers,
                ) as response:
                    body = await _read_json_response(response)
                    if response.status >= 500:
                        raise AgentRuntimeUnavailable(_runtime_error_message(body, response.status))
                    if response.status >= 400:
                        raise AgentRuntimeRejected(_runtime_error_message(body, response.status))
                    if not isinstance(body, dict):
                        raise AgentRuntimeRejected('agent runtime returned a non-object response')
                    if body.get('accepted') is False:
                        raise AgentRuntimeRejected(_runtime_error_message(body, response.status))
                    return body
        except AgentRuntimeError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AgentRuntimeUnavailable(str(exc) or exc.__class__.__name__) from exc


async def _read_json_response(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json(content_type=None)
    except Exception:
        text = await response.text()
        return {'message': text}


def _runtime_error_message(body: Any, status_code: int) -> str:
    if isinstance(body, dict):
        error = body.get('error')
        if isinstance(error, dict):
            message = error.get('message') or error.get('detail')
            if message:
                return str(message)
        for key in ('message', 'detail'):
            if body.get(key):
                return str(body[key])
    return f'agent runtime returned HTTP {status_code}'
