from __future__ import annotations

import datetime as dt
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp


class AgentRuntimeError(RuntimeError):
    code = 'agent_runtime_error'


class AgentRuntimeUnavailable(AgentRuntimeError):
    code = 'agent_runtime_unavailable'

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AgentRuntimeRejected(AgentRuntimeError):
    code = 'agent_runtime_rejected'


class AgentRuntimeAuthenticationError(AgentRuntimeError):
    code = 'agent_runtime_auth_error'


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

    async def prepare_decision_execution(
        self,
        run_id: str,
        execution_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            'PUT',
            f'/v1/openwebui/runs/{run_id}/executions/{execution_id}',
            payload,
        )

    async def activate_decision_execution(
        self,
        run_id: str,
        execution_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            'POST',
            f'/v1/openwebui/runs/{run_id}/executions/{execution_id}/activate',
        )

    async def get_decision_execution(
        self,
        run_id: str,
        execution_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            'GET',
            f'/v1/openwebui/runs/{run_id}/executions/{execution_id}',
        )

    async def _post(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        return await self._request('POST', path, payload)

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise AgentRuntimeUnavailable('agent runtime base URL is not configured')
        headers = {'Content-Type': 'application/json'}
        if self.service_token:
            headers['Authorization'] = f'Bearer {self.service_token}'

        timeout = aiohttp.ClientTimeout(total=self.timeout) if self.timeout else None
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    f'{self.base_url}{path}',
                    json=payload,
                    headers=headers,
                ) as response:
                    body = await _read_json_response(response)
                    message = _runtime_error_message(body, response.status)
                    if response.status in {401, 403}:
                        raise AgentRuntimeAuthenticationError(message)
                    if response.status in {408, 425, 429} or response.status >= 500:
                        raise AgentRuntimeUnavailable(
                            message,
                            retry_after_seconds=_retry_after_seconds(response),
                        )
                    if response.status >= 400:
                        raise AgentRuntimeRejected(message)
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


def _retry_after_seconds(response: aiohttp.ClientResponse) -> float | None:
    value = response.headers.get('Retry-After')
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=dt.UTC)
    return max(
        (retry_at - dt.datetime.now(dt.UTC)).total_seconds(),
        0.0,
    )
