from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from open_webui.agent.artifacts import collect_terminal_output_paths
from open_webui.models.agent_runs import AgentRunOperationConflict

NORMALIZED_TOOL_STATUSES = {
    'success',
    'error',
    'approval_required',
    'approval_rejected',
    'cancelled',
    'timeout',
}


class ToolAuthorityError(ValueError):
    code = 'tool_authority_error'


class ToolNotAllowed(ToolAuthorityError):
    code = 'tool_not_allowed'


class ToolOperationInProgress(ToolAuthorityError):
    code = 'operation_in_progress'


class ToolCallRequest(BaseModel):
    run_id: str
    user_id: str | None = None
    participant_id: str
    tool_call_id: str
    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class ToolCallResponse(BaseModel):
    status: str
    content: str
    files: list[dict[str, Any]] = Field(default_factory=list)
    embeds: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    process_refs: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    structured_error: dict[str, Any] | None = None
    raw: Any = None


def build_tool_access_envelope(
    tools: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    envelope_tools = []
    registry = {}

    for name, tool in tools.items():
        opaque_id = _opaque_tool_id(tool.get('tool_id') or name, name)
        schema = dict(tool.get('spec') or {})
        tool_type = tool.get('type') or _infer_tool_type(tool.get('tool_id'))

        envelope_tools.append(
            {
                'id': opaque_id,
                'name': name,
                'type': tool_type,
                'schema': schema,
            }
        )

        registry[opaque_id] = {
            **tool,
            'name': name,
            'opaque_id': opaque_id,
            'type': tool_type,
        }

    return {'tools': envelope_tools}, registry


def normalize_tool_result(
    result: Any,
    *,
    tool_name: str | None = None,
    tool_id: str | None = None,
    tool_type: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = _unwrap_tool_result(result)

    if _is_normalized_result(data):
        normalized = _default_tool_result()
        normalized.update({key: data.get(key, normalized[key]) for key in normalized})
        return normalized

    if isinstance(data, dict) and data.get('error'):
        return _error_result(str(data.get('error')), raw=data)

    normalized = _default_tool_result()
    normalized['raw'] = data if isinstance(data, (dict, list)) else None
    normalized['content'] = _agent_readable_content(data)
    normalized['process_refs'] = _extract_process_refs(
        data,
        tool_name=tool_name,
        tool_id=tool_id,
        tool_type=tool_type,
        arguments=arguments or {},
    )
    return normalized


def normalize_tool_exception(exc: Exception) -> dict[str, Any]:
    return _error_result(
        str(exc),
        code=getattr(exc, 'code', 'tool_execution_error'),
        raw={'type': exc.__class__.__name__},
    )


class AgentToolAuthority:
    def __init__(
        self,
        *,
        operation_store,
        registry: dict[str, dict[str, Any]] | None = None,
        resource_manager=None,
        artifact_registrar=None,
    ):
        self.operation_store = operation_store
        self.registry = registry or {}
        self.resource_manager = resource_manager
        self.artifact_registrar = artifact_registrar

    async def execute_tool_call(self, request: ToolCallRequest) -> dict[str, Any]:
        if not request.idempotency_key:
            raise ToolAuthorityError('idempotency_key_required')

        request_hash = _tool_call_request_hash(request)
        try:
            claim = await self.operation_store.claim_operation(
                request.run_id,
                operation_type='tool.call',
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
            )
        except AgentRunOperationConflict:
            raise

        if not claim.created:
            return _cached_operation_response(claim.operation)

        tool = self.registry.get(request.tool_id)
        if tool is None:
            error = {
                'code': ToolNotAllowed.code,
                'message': f'Tool is not available for this run: {request.tool_id}',
            }
            await self.operation_store.finish_operation_error(claim.operation.id, error)
            raise ToolNotAllowed(error['message'])

        try:
            raw_result = await _call_tool(tool['callable'], request.arguments)
            response = normalize_tool_result(
                raw_result,
                tool_name=tool.get('name'),
                tool_id=tool.get('tool_id'),
                tool_type=tool.get('type'),
                arguments=request.arguments,
            )
            await self._register_terminal_side_effects(
                request=request,
                tool=tool,
                raw_result=raw_result,
                response=response,
            )
        except Exception as exc:
            response = normalize_tool_exception(exc)

        await self.operation_store.finish_operation_success(claim.operation.id, response)
        return response

    async def _register_terminal_side_effects(
        self,
        *,
        request: ToolCallRequest,
        tool: dict[str, Any],
        raw_result: Any,
        response: dict[str, Any],
    ) -> None:
        if not _is_terminal_run_command(tool, response):
            return

        if self.resource_manager is not None:
            for process_ref in response['process_refs']:
                self.resource_manager.register_terminal_process(
                    request.run_id,
                    process_ref,
                )

        if self.artifact_registrar is None or request.user_id is None:
            return

        output_paths = collect_terminal_output_paths(
            arguments=request.arguments,
            result=raw_result,
        )
        if not output_paths:
            return

        terminal_server_id = _terminal_server_id(tool.get('tool_id'))
        artifacts = await self.artifact_registrar.register_terminal_output_artifacts(
            run_id=request.run_id,
            user_id=request.user_id,
            participant_id=request.participant_id,
            terminal_server_id=terminal_server_id,
            output_paths=output_paths,
            output_dir=_requested_output_dir(request.arguments),
        )
        response['artifacts'] = _merge_artifacts(response['artifacts'], artifacts)


def _cached_operation_response(operation) -> dict[str, Any]:
    if operation.status == 'succeeded' and operation.response is not None:
        return operation.response
    if operation.status == 'in_progress':
        raise ToolOperationInProgress('operation_in_progress')
    if operation.status == 'failed':
        error = operation.error or {
            'code': 'tool_operation_failed',
            'message': 'Tool operation failed before producing a response.',
        }
        raise ToolAuthorityError(error.get('message', 'tool operation failed'))
    raise ToolAuthorityError(f'Unsupported operation status: {operation.status}')


async def _call_tool(callable_: Callable[..., Any], arguments: dict[str, Any]) -> Any:
    result = callable_(**arguments)
    if inspect.isawaitable(result):
        return await result
    return result


def _tool_call_request_hash(request: ToolCallRequest) -> str:
    payload = {
        'operation_type': 'tool.call',
        'run_id': request.run_id,
        'participant_id': request.participant_id,
        'tool_call_id': request.tool_call_id,
        'tool_id': request.tool_id,
        'arguments': request.arguments,
        'service_principal': 'agentscope-runtime',
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _opaque_tool_id(source_tool_id: str, name: str) -> str:
    return f'tool:{source_tool_id}:{name}'


def _infer_tool_type(tool_id: str | None) -> str:
    if not tool_id:
        return 'openwebui'
    if tool_id.startswith('builtin:'):
        return 'builtin'
    if tool_id.startswith('terminal:'):
        return 'terminal'
    if tool_id.startswith('server:'):
        return 'external'
    return 'openwebui'


def _unwrap_tool_result(result: Any) -> Any:
    if isinstance(result, tuple):
        return result[0] if result else None
    return result


def _is_normalized_result(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and data.get('status') in NORMALIZED_TOOL_STATUSES
        and 'content' in data
    )


def _default_tool_result() -> dict[str, Any]:
    return ToolCallResponse(status='success', content='').model_dump(mode='json')


def _error_result(
    message: str,
    *,
    code: str = 'tool_execution_error',
    raw: Any = None,
) -> dict[str, Any]:
    result = _default_tool_result()
    result.update(
        {
            'status': 'error',
            'content': message,
            'structured_error': {
                'code': code,
                'message': message,
                'retryable': False,
                'details': {},
            },
            'raw': raw,
        }
    )
    return result


def _agent_readable_content(data: Any) -> str:
    if data is None:
        return ''
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _extract_process_refs(
    data: Any,
    *,
    tool_name: str | None,
    tool_id: str | None,
    tool_type: str | None,
    arguments: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    process_id = data.get('process_id')
    if not process_id:
        return []
    if tool_name != 'run_command' and tool_type != 'terminal':
        return []

    return [
        {
            'terminal_server_id': _terminal_server_id(tool_id),
            'process_id': process_id,
            'command': data.get('command') or arguments.get('command'),
            'status': data.get('status'),
            'exit_code': data.get('exit_code'),
            'log_path': data.get('log_path'),
            'next_offset': data.get('next_offset'),
            'metadata': {},
        }
    ]


def _is_terminal_run_command(tool: dict[str, Any], response: dict[str, Any]) -> bool:
    if not response.get('process_refs'):
        return False
    return tool.get('name') == 'run_command' or tool.get('type') == 'terminal'


def _requested_output_dir(arguments: dict[str, Any]) -> str | None:
    output_dir = arguments.get('output_dir') or arguments.get('output_directory')
    return output_dir if isinstance(output_dir, str) and output_dir else None


def _merge_artifacts(
    existing: list[dict[str, Any]],
    registered: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(existing)
    seen_paths = {
        artifact.get('path')
        for artifact in merged
        if isinstance(artifact, dict) and artifact.get('path')
    }
    for artifact in registered:
        path = artifact.get('path')
        if path and path in seen_paths:
            continue
        if path:
            seen_paths.add(path)
        merged.append(artifact)
    return merged


def _terminal_server_id(tool_id: str | None) -> str | None:
    if tool_id and tool_id.startswith('terminal:'):
        return tool_id.split(':', 1)[1]
    return None
