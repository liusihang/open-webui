from __future__ import annotations

import inspect
from typing import Any, Literal

from pydantic import BaseModel

from open_webui.agent.canonical import canonical_sha256
from open_webui.agent.destructive import (
    DestructiveAssessment,
    classify_destructive_tool_call,
)
from open_webui.agent.protocol import AgentEventType, AgentRunState
from open_webui.agent.tool_authority import ToolCallRequest, ToolCallResponse


class ApprovalError(ValueError):
    code = 'approval_error'


class ApprovalNotFound(ApprovalError):
    code = 'approval_not_found'


class ApprovalOperationInProgress(ApprovalError):
    code = 'operation_in_progress'


class ApprovalDecisionConflict(ApprovalError):
    code = 'approval_decision_conflict'


class ApprovalDecisionRequest(BaseModel):
    run_id: str
    approval_id: str
    decision: Literal['approved', 'rejected']
    idempotency_key: str | None = None


class AgentApprovalCoordinator:
    def __init__(self, store):
        self.store = store

    async def request_tool_approval(
        self,
        request: ToolCallRequest,
        tool: dict[str, Any],
    ) -> dict[str, Any] | None:
        assessment = classify_destructive_tool_call(
            tool_name=tool.get('name'),
            tool_id=tool.get('tool_id'),
            tool_type=tool.get('type'),
            arguments=request.arguments,
            metadata={
                **(tool.get('metadata') or tool.get('meta') or {}),
                'run_id': request.run_id,
            },
        )
        if not assessment.requires_approval:
            return None
        if request.checkpoint_version is None:
            raise ApprovalError('checkpoint_version must be an integer')

        approval_id = _approval_id(request)
        response = _approval_required_result(
            approval_id=approval_id,
            request=request,
            tool=tool,
            assessment=assessment,
        )
        request_hash = _approval_request_hash(
            request=request,
            approval_id=approval_id,
            assessment=assessment,
        )
        claim = await _maybe_await(
            self.store.claim_operation(
                request.run_id,
                operation_type='approval.request',
                idempotency_key=_approval_request_key(request),
                request_hash=request_hash,
            )
        )
        if not claim.created:
            if claim.operation.status != 'in_progress':
                return _cached_operation_response(claim.operation)
            recorded_request = await self._find_recorded_approval_request(
                run_id=request.run_id,
                approval_id=approval_id,
            )
            if recorded_request is None:
                raise ApprovalOperationInProgress('operation_in_progress')
            await _maybe_await(
                self.store.finish_operation_success(claim.operation.id, response)
            )
            return response

        try:
            await _append_approval_event(
                self.store,
                request=request,
                event_type=AgentEventType.APPROVAL_REQUESTED.value,
                phase=AgentRunState.WAITING_APPROVAL.value,
                summary=f'Approval requested for {tool.get("name") or request.tool_id}.',
                payload=_approval_event_payload(
                    approval_id=approval_id,
                    request=request,
                    tool=tool,
                    assessment=assessment,
                ),
            )
            await _maybe_await(
                self.store.finish_operation_success(claim.operation.id, response)
            )
            return response
        except Exception as exc:
            await _maybe_await(
                self.store.finish_operation_error(
                    claim.operation.id,
                    {
                        'code': getattr(exc, 'code', 'approval_request_failed'),
                        'message': str(exc),
                    },
                )
            )
            raise

    async def decide(self, request: ApprovalDecisionRequest) -> dict[str, Any]:
        if not request.idempotency_key:
            raise ApprovalError('idempotency_key_required')

        requested = await self._find_recorded_approval_request(
            run_id=request.run_id,
            approval_id=request.approval_id,
        )
        if requested is None:
            raise ApprovalNotFound(f'Unknown approval: {request.approval_id}')
        try:
            recorded = await _maybe_await(
                self.store.record_decision_execution(
                    request.run_id,
                    resource_type='approval',
                    resource_id=request.approval_id,
                    decision=request.decision,
                    payload={},
                    operation_type='approval.result',
                    idempotency_key=request.idempotency_key,
                    request_hash=_approval_decision_hash(request),
                )
            )
        except Exception as exc:
            if getattr(exc, 'code', None) == 'decision_conflict':
                raise ApprovalDecisionConflict(str(exc)) from exc
            raise
        if recorded.execution is None:
            historical = _event_dict(recorded.historical_event)
            response = self._response_for_recorded_decision(
                {**historical, 'decision': request.decision}
            )
            response['execution_id'] = None
            response['execution_status'] = 'historical_completed'
            return response
        payload = requested.get('payload') or {}
        return _approval_recorded_result(
            approval_id=request.approval_id,
            decision=request.decision,
            tool_name=payload.get('tool_name') or payload.get('tool_id'),
            execution_id=recorded.execution.id,
            execution_status=recorded.execution.status,
        )

    async def validate_approved_tool_replay(
        self,
        request: ToolCallRequest,
        execution_id: str,
    ) -> None:
        execution = await _maybe_await(
            self.store.validate_approved_tool_replay(
                request.run_id,
                execution_id=execution_id,
                tool_call_id=request.tool_call_id,
                tool_id=request.tool_id,
                arguments=request.arguments,
                idempotency_key=request.idempotency_key or '',
            )
        )
        if execution is None:
            raise ApprovalDecisionConflict(
                'Decision execution is not authorized for this tool call'
            )

    async def _find_recorded_approval_request(
        self,
        *,
        run_id: str,
        approval_id: str,
    ) -> dict[str, Any] | None:
        return await self._find_recorded_approval_event(
            run_id=run_id,
            approval_id=approval_id,
            event_type=AgentEventType.APPROVAL_REQUESTED.value,
            after_seq=0,
        )

    async def _find_recorded_approval_event(
        self,
        *,
        run_id: str,
        approval_id: str,
        event_type: str,
        after_seq: int,
    ) -> dict[str, Any] | None:
        list_events_after = getattr(self.store, 'list_events_after', None)
        if list_events_after is None:
            return None
        events = await _maybe_await(list_events_after(run_id, after_seq=after_seq))
        for event in events:
            normalized = _event_dict(event)
            payload = normalized.get('payload') or {}
            if (
                normalized.get('event_type') == event_type
                and payload.get('approval_id') == approval_id
            ):
                return normalized
        return None

    def _response_for_recorded_decision(self, recorded: dict[str, Any]) -> dict[str, Any]:
        if recorded['decision'] == 'rejected':
            return _approval_rejected_result_from_payload(
                approval_id=recorded['payload']['approval_id'],
                payload=recorded['payload'],
            )
        return _approval_recorded_result(
            approval_id=recorded['payload']['approval_id'],
            decision=recorded['decision'],
            tool_name=recorded['payload'].get('tool_name') or recorded['payload'].get('tool_id'),
        )



async def _append_approval_event(
    store,
    *,
    request: ToolCallRequest,
    event_type: str,
    phase: str,
    summary: str,
    payload: dict[str, Any],
) -> Any:
    return await _maybe_await(
        store.append_event(
            request.run_id,
            event_type=event_type,
            participant_id=request.participant_id,
            phase=phase,
            summary=summary,
            payload=payload,
        )
    )


def _approval_required_result(
    *,
    approval_id: str,
    request: ToolCallRequest,
    tool: dict[str, Any],
    assessment: DestructiveAssessment,
) -> dict[str, Any]:
    tool_name = tool.get('name') or request.tool_id
    return ToolCallResponse(
        status='approval_required',
        content=f'Approval required for {tool_name}.',
        raw={
            'approval_id': approval_id,
            'tool_call_id': request.tool_call_id,
            'tool_id': request.tool_id,
            'tool_name': tool_name,
            'category': assessment.category,
            'reason': assessment.reason,
            'matched': assessment.matched,
            'action': assessment.action,
        },
    ).model_dump(mode='json')


def _approval_rejected_result_from_payload(
    *,
    approval_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    tool_name = payload.get('tool_name') or payload.get('tool_id') or approval_id
    message = f'User rejected approval for {tool_name}.'
    return ToolCallResponse(
        status='approval_rejected',
        content=message,
        structured_error={
            'code': 'approval_rejected',
            'message': message,
            'retryable': False,
            'details': {
                'approval_id': approval_id,
                'tool_call_id': payload.get('tool_call_id'),
                'tool_id': payload.get('tool_id'),
            },
        },
        raw={
            'approval_id': approval_id,
            'decision': 'rejected',
        },
    ).model_dump(mode='json')


def _approval_recorded_result(
    *,
    approval_id: str,
    decision: str,
    tool_name: str | None,
    execution_id: str | None = None,
    execution_status: str | None = None,
) -> dict[str, Any]:
    response = ToolCallResponse(
        status='approval_recorded',
        content=f'Approval {decision} for {tool_name or approval_id}.',
        raw={
            'approval_id': approval_id,
            'decision': decision,
            'execution_id': execution_id,
            'execution_status': execution_status,
        },
    ).model_dump(mode='json')
    response['execution_id'] = execution_id
    response['execution_status'] = execution_status
    return response


def _approval_event_payload(
    *,
    approval_id: str,
    request: ToolCallRequest,
    tool: dict[str, Any],
    assessment: DestructiveAssessment,
) -> dict[str, Any]:
    return {
        'approval_id': approval_id,
        'tool_call_id': request.tool_call_id,
        'tool_id': request.tool_id,
        'tool_name': tool.get('name') or request.tool_id,
        'category': assessment.category,
        'reason': assessment.reason,
        'matched': assessment.matched,
        'action': assessment.action,
        'arguments_summary': _arguments_summary(request.arguments),
        'tool_arguments_fingerprint': _hash_payload(request.arguments),
        'tool_call_idempotency_key': request.idempotency_key,
        'checkpoint_version': request.checkpoint_version,
    }


def _approval_id(request: ToolCallRequest) -> str:
    return f'approval:{request.run_id}:{request.tool_call_id}'


def _approval_request_key(request: ToolCallRequest) -> str:
    if request.idempotency_key:
        return f'approval-request:{request.idempotency_key}'
    return f'approval-request:{request.run_id}:{request.tool_call_id}'


def _approval_request_hash(
    *,
    request: ToolCallRequest,
    approval_id: str,
    assessment: DestructiveAssessment,
) -> str:
    return _hash_payload(
        {
            'operation_type': 'approval.request',
            'run_id': request.run_id,
            'approval_id': approval_id,
            'participant_id': request.participant_id,
            'tool_call_id': request.tool_call_id,
            'tool_id': request.tool_id,
            'arguments': request.arguments,
            'category': assessment.category,
            'matched': assessment.matched,
            'checkpoint_version': request.checkpoint_version,
        }
    )


def _approval_decision_hash(request: ApprovalDecisionRequest) -> str:
    return _hash_payload(
        {
            'operation_type': 'approval.result',
            'run_id': request.run_id,
            'approval_id': request.approval_id,
            'decision': request.decision,
            'service_principal': 'openwebui',
        }
    )


def _hash_payload(payload: dict[str, Any]) -> str:
    return canonical_sha256(payload)


def _cached_operation_response(operation) -> dict[str, Any]:
    if operation.status == 'succeeded' and operation.response is not None:
        return operation.response
    if operation.status == 'in_progress':
        raise ApprovalOperationInProgress('operation_in_progress')
    if operation.status == 'failed':
        error = operation.error or {
            'code': 'approval_operation_failed',
            'message': 'Approval operation failed before producing a response.',
        }
        raise ApprovalError(error.get('message', 'approval operation failed'))
    raise ApprovalError(f'Unsupported operation status: {operation.status}')


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _event_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    if hasattr(event, 'model_dump'):
        return event.model_dump(mode='json')
    return dict(getattr(event, '__dict__', {}))


def _event_seq(event: Any) -> int:
    value = _event_dict(event).get('seq', 0)
    return value if isinstance(value, int) else 0


def _arguments_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    summary = {}
    for key, value in arguments.items():
        if key in {'content', 'data', 'payload'}:
            summary[key] = f'<{len(str(value))} chars>'
        else:
            summary[key] = value
    return summary
