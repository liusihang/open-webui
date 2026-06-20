import hashlib
import inspect
import json
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from open_webui.agent.approval import (
    AgentApprovalCoordinator,
    ApprovalDecisionConflict,
    ApprovalDecisionRequest,
    ApprovalError,
    ApprovalNotFound,
    ApprovalOperationInProgress,
)
from open_webui.agent.events import (
    AgentEventError,
    AgentEventStore,
    append_agent_event,
    append_agent_event_async,
    append_final_delta,
    append_final_delta_async,
    append_text_delta,
    append_text_delta_async,
)
from open_webui.agent.model_authority import (
    AgentModelAuthority,
    ModelAuthorityError,
    ModelCallRequest,
    ModelGuardRejected,
    ModelNotAllowed,
    ModelOperationInProgress,
)
from open_webui.agent.model_catalog import ModelCatalogError, ModelSelectionNotAllowed
from open_webui.agent.protocol import (
    AgentEventAppend,
    AgentEventType,
    AgentRunEvent,
    AgentStateTransitionAppend,
    FinalDeltaAppend,
    TextDeltaAppend,
)
from open_webui.agent.service.model_call import execute_agent_model_call
from open_webui.agent.service.tool_call import execute_agent_tool_call
from open_webui.agent.subagents import (
    AgentSubagentCoordinator,
    SubagentError,
    SubagentModelSelectionRequest,
    SubagentRegisterRequest,
)
from open_webui.agent.tool_authority import (
    AgentToolAuthority,
    ToolAuthorityError,
    ToolCallRequest,
    ToolOperationInProgress,
    build_tool_access_envelope,
)
from open_webui.models.agent_runs import AgentRunError, AgentRunOperationConflict, AgentRuns
from open_webui.models.chats import Chats
from open_webui.models.users import Users
from open_webui.routers.agent_runs import get_configured_agent_event_store
from open_webui.utils.tools import get_builtin_tools, get_terminal_tools, get_tools

router = APIRouter()


def _require_matching_idempotency_key(
    body_key: str | None,
    header_key: str | None,
) -> str:
    if not body_key and not header_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='idempotency_key_required',
        )
    if body_key and header_key and body_key != header_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='idempotency_key_required',
        )
    return body_key or header_key or ''


class AgentServiceOperationInProgress(Exception):
    pass


def _require_agent_service_credential(request: Request, authorization: str | None) -> None:
    expected_token = getattr(request.app.state.config, 'AGENT_RUNTIME_SERVICE_TOKEN', '') or ''
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Agent service token is not configured',
        )
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='service token required',
        )

    token = authorization.removeprefix('Bearer ').strip()
    if not secrets.compare_digest(token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='invalid service token',
        )


def _trust_internal_model_call(request: Request, run_id: str) -> None:
    request.state.agent_internal_model_call = True
    request.state.agent_run_id = run_id
    request.state.agent_service_principal = 'agentscope-runtime'


def get_agent_model_authority(request: Request) -> AgentModelAuthority:
    authority = getattr(request.app.state, 'AGENT_MODEL_AUTHORITY', None)
    if authority is not None:
        return authority

    return AgentModelAuthority(operation_store=get_agent_operation_store(request))


async def get_agent_tool_authority(request: Request, run_id: str | None = None) -> AgentToolAuthority:
    authority = getattr(request.app.state, 'AGENT_TOOL_AUTHORITY', None)
    if authority is not None:
        return authority

    registry = None
    registries = getattr(request.app.state, 'AGENT_TOOL_REGISTRIES', None)
    if run_id and isinstance(registries, dict):
        registry = registries.get(run_id)
    if registry is None and run_id:
        registry = await _rebuild_agent_tool_registry(request, run_id)
    if registry is None:
        registry = getattr(request.app.state, 'AGENT_TOOL_REGISTRY', None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Agent tool registry is not configured',
        )

    return AgentToolAuthority(
        operation_store=get_agent_operation_store(request),
        registry=registry,
        resource_manager=getattr(request.app.state, 'AGENT_RUN_RESOURCE_MANAGER', None),
        artifact_registrar=getattr(request.app.state, 'AGENT_RUN_ARTIFACT_REGISTRAR', None),
    )


async def _rebuild_agent_tool_registry(request: Request, run_id: str) -> dict[str, dict[str, Any]] | None:
    store = get_agent_operation_store(request)
    get_run = getattr(store, 'get_run', None)
    if get_run is None:
        return None

    run = await _maybe_await(get_run(run_id))
    if run is None:
        return None

    snapshot_tools = _snapshot_tools(getattr(run, 'tool_access_snapshot', None))
    if not snapshot_tools:
        return None

    user = await _load_agent_run_user(getattr(run, 'user_id', None))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Agent tool registry cannot be rebuilt because the run user is unavailable',
        )

    rebuilt: dict[str, dict[str, Any]] = {}
    rebuilt.update(await _rebuild_builtin_tools(request, run, user, snapshot_tools))
    rebuilt.update(await _rebuild_terminal_tools(request, run, user, snapshot_tools))
    rebuilt.update(await _rebuild_external_tools(request, run, user, snapshot_tools))

    if not rebuilt:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Agent tool registry could not be rebuilt from persisted snapshot',
        )

    _cache_agent_tool_registry(request, run_id, rebuilt)
    return rebuilt


async def _load_agent_run_user(user_id: str | None):
    if not user_id:
        return None
    return await _maybe_await(Users.get_user_by_id(user_id))


def _snapshot_tools(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    tools = snapshot.get('tools')
    if not isinstance(tools, list):
        return []
    return [tool for tool in tools if isinstance(tool, dict)]


async def _rebuild_builtin_tools(
    request: Request,
    run,
    user,
    snapshot_tools: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    requested = [tool for tool in snapshot_tools if tool.get('type') == 'builtin']
    if not requested:
        return {}

    requested_names = {tool.get('name') for tool in requested if isinstance(tool.get('name'), str)}
    if not requested_names:
        return {}

    user_payload = user.model_dump(mode='json') if hasattr(user, 'model_dump') else dict(getattr(user, '__dict__', {}))
    metadata = _agent_run_metadata(run)
    current_tools = await get_builtin_tools(
        request,
        {
            '__user__': user_payload,
            '__metadata__': metadata,
            '__chat_id__': metadata.get('chat_id'),
            '__message_id__': metadata.get('assistant_message_id'),
        },
        _features_for_builtin_tools(requested_names),
        _model_for_builtin_rebuild(getattr(run, 'leader_model_id', None), requested_names),
    )

    available_names = requested_names & set(current_tools)
    missing = sorted(requested_names - available_names)
    if not available_names:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                'code': 'agent_tool_registry_rebuild_failed',
                'message': 'Agent builtin tools are not available in the current server context',
                'tools': missing,
            },
        )

    _envelope, current_registry = build_tool_access_envelope({name: current_tools[name] for name in available_names})
    return _registry_from_snapshot(requested, current_registry)


async def _rebuild_terminal_tools(
    request: Request,
    run,
    user,
    snapshot_tools: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    requested_by_terminal: dict[str, set[str]] = {}
    for tool in snapshot_tools:
        if tool.get('type') != 'terminal':
            continue
        terminal_id = _terminal_id_from_snapshot_tool(tool)
        name = tool.get('name')
        if terminal_id and isinstance(name, str):
            requested_by_terminal.setdefault(terminal_id, set()).add(name)

    if not requested_by_terminal:
        return {}

    rebuilt: dict[str, dict[str, Any]] = {}
    metadata = _agent_run_metadata(run)
    for terminal_id, names in requested_by_terminal.items():
        terminal_result = await get_terminal_tools(
            request,
            terminal_id,
            user,
            {
                '__metadata__': metadata,
                '__chat_id__': metadata.get('chat_id'),
                '__message_id__': metadata.get('assistant_message_id'),
            },
        )
        terminal_tools = terminal_result[0] if isinstance(terminal_result, tuple) else terminal_result
        terminal_tools = terminal_tools or {}
        missing = sorted(name for name in names if name not in terminal_tools)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    'code': 'agent_tool_registry_rebuild_failed',
                    'message': 'Agent terminal tools are not available in the current server context',
                    'terminal_id': terminal_id,
                    'tools': missing,
                },
            )

        _envelope, current_registry = build_tool_access_envelope({name: terminal_tools[name] for name in names})
        requested = [
            tool
            for tool in snapshot_tools
            if tool.get('type') == 'terminal'
            and tool.get('name') in names
            and _terminal_id_from_snapshot_tool(tool) == terminal_id
        ]
        rebuilt.update(_registry_from_snapshot(requested, current_registry))

    return rebuilt


def _external_tool_source_id_from_snapshot(tool: dict[str, Any]) -> str | None:
    """Extract the source tool_id from a type=='external' snapshot tool's opaque id.

    The opaque id follows the pattern ``tool:{tool_id}:{name}`` where
    ``tool_id`` is the source identifier (e.g. ``server:openapi:abc``).
    This mirrors the convention in ``_opaque_tool_id``.
    """
    opaque_id = tool.get('id')
    name = tool.get('name')
    if not isinstance(opaque_id, str) or not isinstance(name, str):
        return None
    prefix = 'tool:server:'
    suffix = f':{name}'
    if opaque_id.startswith(prefix) and opaque_id.endswith(suffix):
        source_part = opaque_id[len(prefix) : -len(suffix)]
        if not source_part:
            return None
        # source_part is e.g. "abc" (server:abc) or "openapi:xyz" (server:openapi:xyz)
        return f'server:{source_part}'
    return None


async def _rebuild_external_tools(
    request: Request,
    run,
    user,
    snapshot_tools: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    requested_external: list[dict[str, Any]] = []
    for tool in snapshot_tools:
        if tool.get('type') != 'external':
            continue
        source_id = _external_tool_source_id_from_snapshot(tool)
        if source_id:
            requested_external.append(tool)

    if not requested_external:
        return {}

    # Group tools by source tool_id so we call get_tools only once per tool server
    by_source: dict[str, list[dict[str, Any]]] = {}
    for tool in requested_external:
        source_id = _external_tool_source_id_from_snapshot(tool)
        if source_id:
            by_source.setdefault(source_id, []).append(tool)

    rebuilt: dict[str, dict[str, Any]] = {}
    metadata = _agent_run_metadata(run)
    extra_params = {
        '__user__': _user_payload(user),
        '__metadata__': metadata,
        '__chat_id__': metadata.get('chat_id'),
        '__message_id__': metadata.get('assistant_message_id'),
    }

    for source_id, tools_in_group in by_source.items():
        current_tools = await get_tools(request, [source_id], user, extra_params)
        current_tools = current_tools or {}

        requested_names = {
            t.get('name')
            for t in tools_in_group
            if isinstance(t.get('name'), str)
        }
        available_names = requested_names & set(current_tools)
        missing = sorted(requested_names - available_names)

        if not available_names:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    'code': 'agent_tool_registry_rebuild_failed',
                    'message': 'Agent external tools are not available in the current server context',
                    'tool_source_id': source_id,
                    'tools': missing,
                },
            )

        if missing:
            # Graceful: skip tools that have disappeared but keep those that remain
            tools_in_group = [
                t for t in tools_in_group if t.get('name') in available_names
            ]

        available = {name: current_tools[name] for name in available_names}
        _envelope, current_registry = build_tool_access_envelope(available)
        rebuilt.update(_registry_from_snapshot(tools_in_group, current_registry))

    return rebuilt


def _user_payload(user) -> dict[str, Any]:
    if hasattr(user, 'model_dump'):
        return user.model_dump(mode='json')
    return dict(getattr(user, '__dict__', {}))


def _registry_from_snapshot(
    snapshot_tools: list[dict[str, Any]],
    current_registry: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_name = {tool.get('name'): tool for tool in current_registry.values()}
    registry = {}
    for snapshot_tool in snapshot_tools:
        opaque_id = snapshot_tool.get('id')
        name = snapshot_tool.get('name')
        current = by_name.get(name)
        if not isinstance(opaque_id, str) or current is None:
            continue
        registry[opaque_id] = {
            **current,
            'name': name,
            'opaque_id': opaque_id,
            'type': snapshot_tool.get('type') or current.get('type'),
            'spec': dict(snapshot_tool.get('schema') or current.get('spec') or {}),
        }
    return registry


def _agent_run_metadata(run) -> dict[str, Any]:
    return {
        'chat_id': getattr(run, 'chat_id', None),
        'user_message_id': getattr(run, 'user_message_id', None),
        'message_id': getattr(run, 'assistant_message_id', None),
        'assistant_message_id': getattr(run, 'assistant_message_id', None),
        'agent_run_id': getattr(run, 'id', None),
    }


def _features_for_builtin_tools(tool_names: set[str]) -> dict[str, bool]:
    return {
        'web_search': bool(tool_names & {'search_web', 'fetch_url'}),
        'image_generation': bool(tool_names & {'generate_image', 'edit_image'}),
        'code_interpreter': 'execute_code' in tool_names,
        'memory': bool(
            tool_names
            & {
                'search_memories',
                'add_memory',
                'replace_memory_content',
                'delete_memory',
                'list_memories',
            }
        ),
    }


def _model_for_builtin_rebuild(model_id: str | None, tool_names: set[str]) -> dict[str, Any]:
    capabilities = {
        'builtin_tools': True,
        'web_search': bool(tool_names & {'search_web', 'fetch_url'}),
        'image_generation': bool(tool_names & {'generate_image', 'edit_image'}),
        'code_interpreter': 'execute_code' in tool_names,
        'memory': bool(
            tool_names
            & {
                'search_memories',
                'add_memory',
                'replace_memory_content',
                'delete_memory',
                'list_memories',
            }
        ),
    }
    categories = _builtin_categories_for_tools(tool_names)
    return {
        'id': model_id,
        'info': {
            'meta': {
                'capabilities': capabilities,
                'builtinTools': {category: category in categories for category in _BUILTIN_TOOL_CATEGORIES},
            }
        },
    }


_BUILTIN_TOOL_CATEGORIES = {
    'time',
    'knowledge',
    'chats',
    'memory',
    'agent_memory',
    'web_search',
    'image_generation',
    'code_interpreter',
    'notes',
    'channels',
    'skills',
    'tasks',
    'automations',
    'calendar',
}


def _builtin_categories_for_tools(tool_names: set[str]) -> set[str]:
    categories: set[str] = set()
    mapping = {
        'time': {'get_current_timestamp', 'calculate_timestamp'},
        'knowledge': {
            'kb_exec',
            'list_knowledge',
            'search_knowledge_files',
            'grep_knowledge_files',
            'query_knowledge_files',
            'query_knowledge_evidence',
            'view_file',
            'view_knowledge_file',
            'list_knowledge_bases',
            'search_knowledge_bases',
            'query_knowledge_bases',
            'view_note',
        },
        'chats': {'search_chats', 'view_chat'},
        'web_search': {'search_web', 'fetch_url'},
        'notes': {'search_notes', 'view_note', 'write_note', 'replace_note_content'},
        'tasks': {'create_tasks', 'update_task'},
        'code_interpreter': {'execute_code'},
        'image_generation': {'generate_image', 'edit_image'},
        'memory': {
            'search_memories',
            'add_memory',
            'replace_memory_content',
            'delete_memory',
            'list_memories',
        },
        'agent_memory': {'agent_memory_search', 'agent_memory_read', 'agent_memory_list'},
        'channels': {
            'search_channels',
            'search_channel_messages',
            'view_channel_thread',
            'view_channel_message',
        },
        'skills': {'read_skill', 'install_skill', 'update_skill'},
        'automations': {
            'create_automation',
            'update_automation',
            'list_automations',
            'toggle_automation',
            'delete_automation',
        },
        'calendar': {
            'search_calendar_events',
            'create_calendar_event',
            'update_calendar_event',
            'delete_calendar_event',
        },
    }
    for category, names in mapping.items():
        if tool_names & names:
            categories.add(category)
    return categories


def _terminal_id_from_snapshot_tool(tool: dict[str, Any]) -> str | None:
    opaque_id = tool.get('id')
    name = tool.get('name')
    if not isinstance(opaque_id, str) or not isinstance(name, str):
        return None
    prefix = 'tool:terminal:'
    suffix = f':{name}'
    if opaque_id.startswith(prefix) and opaque_id.endswith(suffix):
        terminal_id = opaque_id[len(prefix) : -len(suffix)]
        return terminal_id or None
    return None


def _cache_agent_tool_registry(request: Request, run_id: str, registry: dict[str, dict[str, Any]]) -> None:
    registries = getattr(request.app.state, 'AGENT_TOOL_REGISTRIES', None)
    if not isinstance(registries, dict):
        registries = {}
        request.app.state.AGENT_TOOL_REGISTRIES = registries
    registries[run_id] = registry


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def get_agent_approval_coordinator(request: Request) -> AgentApprovalCoordinator:
    coordinator = getattr(request.app.state, 'AGENT_APPROVAL_COORDINATOR', None)
    if coordinator is not None:
        return coordinator

    coordinator = AgentApprovalCoordinator(get_agent_operation_store(request))
    request.app.state.AGENT_APPROVAL_COORDINATOR = coordinator
    return coordinator


def get_agent_subagent_coordinator(request: Request) -> AgentSubagentCoordinator:
    coordinator = getattr(request.app.state, 'AGENT_SUBAGENT_COORDINATOR', None)
    if coordinator is not None:
        return coordinator

    coordinator = AgentSubagentCoordinator()
    request.app.state.AGENT_SUBAGENT_COORDINATOR = coordinator
    return coordinator


def get_agent_operation_store(request: Request):
    return get_configured_agent_event_store(request) or AgentRuns


def get_optional_agent_event_store(request: Request) -> AgentEventStore | None:
    return get_configured_agent_event_store(request)


async def _load_service_tool_call_user_id(
    authority: AgentToolAuthority,
    run_id: str,
) -> str | None:
    get_run = getattr(getattr(authority, 'operation_store', None), 'get_run', None)
    if get_run is None:
        return None
    run = await _maybe_await(get_run(run_id))
    if run is None:
        return None
    user_id = getattr(run, 'user_id', None)
    if isinstance(user_id, str) and user_id:
        return user_id
    return None


async def _append_tool_artifact_registered_events(
    tool_request: ToolCallRequest,
    response: dict[str, Any],
) -> None:
    artifacts = response.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:
        return

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_ref = artifact.get('artifact_id') or artifact.get('id') or artifact.get('path')
        if not isinstance(artifact_ref, str) or not artifact_ref:
            continue
        await _append_agent_event_with_operation(
            AgentEventAppend(
                run_id=tool_request.run_id,
                event_type=AgentEventType.ARTIFACT_REGISTERED,
                participant_id=tool_request.participant_id,
                phase='running',
                summary=f'Artifact registered: {artifact.get("path") or artifact_ref}',
                payload={
                    'tool_call_id': tool_request.tool_call_id,
                    'tool_id': tool_request.tool_id,
                    'artifacts': [artifact],
                },
                idempotency_key=f'artifact.registered:{tool_request.tool_call_id}:{artifact_ref}',
            )
        )


@router.post('/runs/{run_id}/subagents')
async def execute_agent_run_subagent_registration(
    request: Request,
    run_id: str,
    form_data: SubagentRegisterRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    coordinator: AgentSubagentCoordinator = Depends(get_agent_subagent_coordinator),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await coordinator.register_subagent(
            request,
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key}),
        )
    except SubagentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': getattr(exc, 'code', 'subagent_error'),
                'message': str(exc),
            },
        ) from exc


@router.post('/runs/{run_id}/events')
async def append_agent_run_event(
    request: Request,
    run_id: str,
    form_data: AgentEventAppend,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    store: AgentEventStore | None = Depends(get_optional_agent_event_store),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        event_payload = form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key})
        if store is not None:
            event = append_agent_event(store, event_payload)
        else:
            event = await _append_agent_event_with_operation(event_payload)
    except AgentEventError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except AgentServiceOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    return event


@router.post('/runs/{run_id}/model-selection')
async def execute_agent_run_model_selection(
    request: Request,
    run_id: str,
    form_data: SubagentModelSelectionRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    coordinator: AgentSubagentCoordinator = Depends(get_agent_subagent_coordinator),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await coordinator.select_subagent_model(
            request,
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key}),
        )
    except ModelSelectionNotAllowed as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': exc.code,
                'message': str(exc),
                'warnings': exc.warnings,
            },
        ) from exc
    except ModelCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': getattr(exc, 'code', 'model_catalog_error'),
                'message': str(exc),
            },
        ) from exc
    except SubagentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': getattr(exc, 'code', 'subagent_error'),
                'message': str(exc),
            },
        ) from exc


@router.post('/runs/{run_id}/final-delta')
async def append_agent_run_final_delta(
    request: Request,
    run_id: str,
    form_data: FinalDeltaAppend,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    store: AgentEventStore | None = Depends(get_optional_agent_event_store),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        delta_payload = form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key})
        if store is not None:
            event = append_final_delta(store, delta_payload)
        else:
            event = await _append_final_delta_with_operation(delta_payload)
    except AgentEventError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except AgentServiceOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    return event


@router.post('/runs/{run_id}/text-delta')
async def append_agent_run_text_delta(
    request: Request,
    run_id: str,
    form_data: TextDeltaAppend,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    store: AgentEventStore | None = Depends(get_optional_agent_event_store),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        delta_payload = form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key})
        if store is not None:
            event = append_text_delta(store, delta_payload)
        else:
            event = await _append_text_delta_with_operation(delta_payload)
    except AgentEventError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except AgentServiceOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    return event


@router.post('/runs/{run_id}/state-transition')
async def transition_agent_run_state(
    request: Request,
    run_id: str,
    form_data: AgentStateTransitionAppend,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await _transition_state_with_operation(
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key})
        )
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except AgentServiceOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    except AgentRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': getattr(exc, 'code', 'agent_run_error'),
                'message': str(exc),
            },
        ) from exc


@router.post('/runs/{run_id}/model-call')
async def execute_agent_run_model_call(
    request: Request,
    run_id: str,
    form_data: ModelCallRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    authority: AgentModelAuthority = Depends(get_agent_model_authority),
):
    _require_agent_service_credential(request, authorization)
    _trust_internal_model_call(request, run_id)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await execute_agent_model_call(
            authority,
            request,
            form_data.model_copy(update={'run_id': run_id, 'idempotency_key': key}),
        )
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except ModelOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    except (ModelGuardRejected, ModelNotAllowed, ModelAuthorityError) as exc:
        detail = {
            'code': getattr(exc, 'code', 'model_authority_error'),
            'message': str(exc),
        }
        current_state = getattr(exc, 'current_state', None)
        if current_state is not None:
            detail['current_state'] = current_state
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        ) from exc


@router.post('/runs/{run_id}/tool-call')
async def execute_agent_run_tool_call(
    request: Request,
    run_id: str,
    form_data: ToolCallRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    authorization: str | None = Header(default=None, alias='Authorization'),
    authority: AgentToolAuthority = Depends(get_agent_tool_authority),
    approval_coordinator: AgentApprovalCoordinator = Depends(get_agent_approval_coordinator),
):
    _require_agent_service_credential(request, authorization)
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    user_id = form_data.user_id or await _load_service_tool_call_user_id(authority, run_id)
    tool_request = form_data.model_copy(
        update={'run_id': run_id, 'user_id': user_id, 'idempotency_key': key}
    )
    try:
        tool = authority.registry.get(tool_request.tool_id)
        if tool is not None:

            async def resume_tool_call():
                response = await execute_agent_tool_call(authority, tool_request)
                await _append_tool_artifact_registered_events(tool_request, response)
                return response

            approval_result = await approval_coordinator.request_tool_approval(
                tool_request,
                tool,
                resume=resume_tool_call,
            )
            if approval_result is not None:
                return approval_result

        response = await execute_agent_tool_call(
            authority,
            tool_request,
        )
        await _append_tool_artifact_registered_events(tool_request, response)
        return response
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except (ToolOperationInProgress, ApprovalOperationInProgress):
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    except ApprovalDecisionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': exc.code,
                'message': str(exc),
            },
        ) from exc
    except ApprovalNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                'code': exc.code,
                'message': str(exc),
            },
        ) from exc
    except ApprovalError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': getattr(exc, 'code', 'approval_error'),
                'message': str(exc),
            },
        ) from exc
    except ToolAuthorityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': getattr(exc, 'code', 'tool_authority_error'),
                'message': str(exc),
            },
        ) from exc


async def _append_agent_event_with_operation(event: AgentEventAppend) -> AgentRunEvent:
    key = event.idempotency_key or ''
    try:
        claim = await AgentRuns.claim_operation(
            event.run_id,
            operation_type='event.append',
            idempotency_key=key,
            request_hash=_callback_request_hash('event.append', event),
        )
    except AgentRunOperationConflict:
        existing = await AgentRuns.find_operation_by_idempotency_key(
            event.run_id,
            operation_type='event.append',
            idempotency_key=key,
        )
        if existing is None:
            raise
        return _cached_event_operation_response(existing)

    if not claim.created:
        return _cached_event_operation_response(claim.operation)

    try:
        stored = await append_agent_event_async(AgentRuns, event)
    except Exception as exc:
        await AgentRuns.finish_operation_error(
            claim.operation.id,
            {
                'code': getattr(exc, 'code', 'event_append_failed'),
                'message': str(exc),
            },
        )
        raise

    await AgentRuns.finish_operation_success(
        claim.operation.id,
        stored.model_dump(mode='json'),
    )
    return stored


async def _append_final_delta_with_operation(delta: FinalDeltaAppend) -> AgentRunEvent:
    claim = await AgentRuns.claim_operation(
        delta.run_id,
        operation_type='final.delta',
        idempotency_key=delta.idempotency_key or '',
        request_hash=_callback_request_hash('final.delta', delta),
    )
    if not claim.created:
        return _cached_event_operation_response(claim.operation)

    try:
        stored = await append_final_delta_async(AgentRuns, delta)
    except Exception as exc:
        await AgentRuns.finish_operation_error(
            claim.operation.id,
            {
                'code': getattr(exc, 'code', 'final_delta_failed'),
                'message': str(exc),
            },
        )
        raise

    await AgentRuns.finish_operation_success(
        claim.operation.id,
        stored.model_dump(mode='json'),
    )
    return stored


async def _append_text_delta_with_operation(delta: TextDeltaAppend) -> AgentRunEvent:
    key = delta.idempotency_key or ''
    try:
        claim = await AgentRuns.claim_operation(
            delta.run_id,
            operation_type='text.delta',
            idempotency_key=key,
            request_hash=_callback_request_hash('text.delta', delta),
        )
    except AgentRunOperationConflict:
        # text.delta shares the event.append relaxation: a re-used
        # idempotency_key with a different request_hash is treated as a
        # duplicate and returns the previously stored event.
        existing = await AgentRuns.find_operation_by_idempotency_key(
            delta.run_id,
            operation_type='text.delta',
            idempotency_key=key,
        )
        if existing is None:
            raise
        return _cached_event_operation_response(existing)

    if not claim.created:
        return _cached_event_operation_response(claim.operation)

    try:
        stored = await append_text_delta_async(AgentRuns, delta)
    except Exception as exc:
        await AgentRuns.finish_operation_error(
            claim.operation.id,
            {
                'code': getattr(exc, 'code', 'text_delta_failed'),
                'message': str(exc),
            },
        )
        raise

    await AgentRuns.finish_operation_success(
        claim.operation.id,
        stored.model_dump(mode='json'),
    )
    return stored


async def _transition_state_with_operation(
    transition: AgentStateTransitionAppend,
) -> dict[str, Any]:
    claim = await AgentRuns.claim_operation(
        transition.run_id,
        operation_type='state.transition',
        idempotency_key=transition.idempotency_key or '',
        request_hash=_callback_request_hash('state.transition', transition),
    )
    if not claim.created:
        return _cached_state_operation_response(claim.operation)

    try:
        updated = await AgentRuns.transition_state(
            transition.run_id,
            from_states=list(transition.from_states),
            to_state=transition.to_state,
            reason=transition.reason,
            payload=transition.payload,
        )
        if updated.state == 'completed':
            await _write_completed_agent_run_message(updated)
    except Exception as exc:
        await AgentRuns.finish_operation_error(
            claim.operation.id,
            {
                'code': getattr(exc, 'code', 'state_transition_failed'),
                'message': str(exc),
            },
        )
        raise

    response = updated.model_dump(mode='json')
    await AgentRuns.finish_operation_success(claim.operation.id, response)
    return response


def _cached_event_operation_response(operation) -> AgentRunEvent:
    if operation.status == 'succeeded' and operation.response is not None:
        return AgentRunEvent.model_validate(operation.response)
    if operation.status == 'in_progress':
        raise AgentServiceOperationInProgress('operation_in_progress')
    if operation.status == 'failed':
        error = operation.error or {
            'code': 'agent_event_operation_failed',
            'message': 'Agent event operation failed before producing a response.',
        }
        raise AgentEventError(error.get('message', 'agent event operation failed'))
    raise AgentEventError(f'Unsupported operation status: {operation.status}')


def _cached_state_operation_response(operation) -> dict[str, Any]:
    if operation.status == 'succeeded' and operation.response is not None:
        return operation.response
    if operation.status == 'in_progress':
        raise AgentServiceOperationInProgress('operation_in_progress')
    if operation.status == 'failed':
        error = operation.error or {
            'code': 'agent_state_operation_failed',
            'message': 'Agent state operation failed before producing a response.',
        }
        raise AgentRunError(error.get('message', 'agent state operation failed'))
    raise AgentRunError(f'Unsupported operation status: {operation.status}')


async def _write_completed_agent_run_message(run) -> None:
    if not run.chat_id or not run.assistant_message_id:
        return
    if run.chat_id.startswith('local:') or run.chat_id.startswith('channel:'):
        return

    await Chats.upsert_message_to_chat_by_id_and_message_id(
        run.chat_id,
        run.assistant_message_id,
        {
            'agent_run_id': run.id,
            'content': run.final_text or '',
            'done': True,
        },
    )


def _callback_request_hash(operation_type: str, model: Any) -> str:
    body = model.model_dump(mode='json', exclude={'idempotency_key'})
    payload = {
        'operation_type': operation_type,
        'body': body,
        'service_principal': 'agentscope-runtime',
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post('/runs/{run_id}/approvals/{approval_id}/decision')
async def decide_agent_run_approval(
    request: Request,
    run_id: str,
    approval_id: str,
    form_data: ApprovalDecisionRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias='X-Agent-Idempotency-Key',
    ),
    approval_coordinator: AgentApprovalCoordinator = Depends(get_agent_approval_coordinator),
):
    key = _require_matching_idempotency_key(
        form_data.idempotency_key,
        idempotency_key,
    )
    try:
        return await approval_coordinator.decide(
            form_data.model_copy(
                update={
                    'run_id': run_id,
                    'approval_id': approval_id,
                    'idempotency_key': key,
                }
            )
        )
    except AgentRunOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='idempotency_conflict',
        ) from exc
    except ApprovalOperationInProgress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={'detail': 'operation_in_progress'},
            headers={'Retry-After': '1'},
        )
    except ApprovalDecisionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': exc.code,
                'message': str(exc),
            },
        ) from exc
    except ApprovalNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                'code': exc.code,
                'message': str(exc),
            },
        ) from exc
    except ApprovalError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': getattr(exc, 'code', 'approval_error'),
                'message': str(exc),
            },
        ) from exc
