from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from open_webui.agent.destructive import (
    DestructiveAssessment,
    classify_destructive_tool_call,
)
from open_webui.agent.protocol import AgentEventType, AgentRunState
from open_webui.agent.tool_authority import (
    ToolCallRequest,
    ToolCallResponse,
    normalize_tool_exception,
    normalize_tool_result,
)


APPROVAL_DECISION_POLL_INTERVAL_SECONDS = 0.05
DEFAULT_APPROVAL_DECISION_TIMEOUT_SECONDS = 300.0


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


@dataclass
class _PendingApproval:
    request: ToolCallRequest
    tool: dict[str, Any]
    assessment: DestructiveAssessment
    resume: Callable[[], Any] | None
    wait_for_decision: bool = False


class AgentApprovalCoordinator:
    def __init__(self, store):
        self.store = store
        self._pending: dict[tuple[str, str], _PendingApproval] = {}
        self._resolved: dict[tuple[str, str], dict[str, Any]] = {}

    async def request_tool_approval(
        self,
        request: ToolCallRequest,
        tool: dict[str, Any],
        *,
        resume: Callable[[], Any] | None = None,
        wait_for_decision: bool = False,
        decision_timeout_seconds: float | None = None,
        poll_interval_seconds: float = APPROVAL_DECISION_POLL_INTERVAL_SECONDS,
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
            return _cached_operation_response(claim.operation)

        pending_key = (request.run_id, approval_id)
        self._pending[pending_key] = _PendingApproval(
            request=request,
            tool=tool,
            assessment=assessment,
            resume=resume,
            wait_for_decision=wait_for_decision,
        )

        try:
            await _maybe_await(
                self.store.transition_state(
                    request.run_id,
                    from_states=[AgentRunState.RUNNING.value],
                    to_state=AgentRunState.WAITING_APPROVAL.value,
                    reason='destructive action requires approval',
                    payload={
                        'approval_id': approval_id,
                        'tool_call_id': request.tool_call_id,
                        'category': assessment.category,
                    },
                )
            )
            requested_event = await _append_approval_event(
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
            if wait_for_decision:
                try:
                    return await self._wait_for_recorded_decision_and_resume(
                        pending_key=pending_key,
                        requested_seq=_event_seq(requested_event),
                        approval_required_response=response,
                        timeout_seconds=(
                            DEFAULT_APPROVAL_DECISION_TIMEOUT_SECONDS
                            if decision_timeout_seconds is None
                            else decision_timeout_seconds
                        ),
                        poll_interval_seconds=poll_interval_seconds,
                    )
                except TimeoutError:
                    pending = self._pending.get(pending_key)
                    if pending is not None:
                        pending.wait_for_decision = False
                    return response
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
            self._pending.pop(pending_key, None)
            raise

    async def decide(self, request: ApprovalDecisionRequest) -> dict[str, Any]:
        if not request.idempotency_key:
            raise ApprovalError('idempotency_key_required')

        request_hash = _approval_decision_hash(request)
        claim = await _maybe_await(
            self.store.claim_operation(
                request.run_id,
                operation_type='approval.result',
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
            )
        )
        if not claim.created:
            return _cached_operation_response(claim.operation)

        pending_key = (request.run_id, request.approval_id)
        resolved = self._resolved.get(pending_key)
        if resolved is not None:
            if resolved['decision'] != request.decision:
                await self._finish_decision_error(
                    claim.operation.id,
                    code=ApprovalDecisionConflict.code,
                    message='approval already has a different decision',
                )
                raise ApprovalDecisionConflict('approval already has a different decision')
            await _maybe_await(
                self.store.finish_operation_success(
                    claim.operation.id,
                    resolved['response'],
                )
            )
            return resolved['response']

        recorded = await self._find_recorded_approval_decision(
            run_id=request.run_id,
            approval_id=request.approval_id,
            after_seq=0,
        )
        if recorded is not None:
            if recorded['decision'] != request.decision:
                await self._finish_decision_error(
                    claim.operation.id,
                    code=ApprovalDecisionConflict.code,
                    message='approval already has a different decision',
                )
                raise ApprovalDecisionConflict('approval already has a different decision')
            response = self._response_for_recorded_decision(recorded)
            await _maybe_await(
                self.store.finish_operation_success(claim.operation.id, response)
            )
            return response

        pending = self._pending.get(pending_key)
        if pending is None:
            requested = await self._find_recorded_approval_request(
                run_id=request.run_id,
                approval_id=request.approval_id,
            )
            if requested is None:
                await self._finish_decision_error(
                    claim.operation.id,
                    code=ApprovalNotFound.code,
                    message=f'Unknown approval: {request.approval_id}',
                )
                raise ApprovalNotFound(f'Unknown approval: {request.approval_id}')

            response = await self._record_external_decision(request, requested)
            await _maybe_await(
                self.store.finish_operation_success(claim.operation.id, response)
            )
            return response

        try:
            await self._record_pending_decision(request, pending)
            if request.decision == 'rejected':
                response = _approval_rejected_result(
                    approval_id=request.approval_id,
                    pending=pending,
                )
            elif pending.wait_for_decision:
                response = _approval_recorded_result(
                    approval_id=request.approval_id,
                    decision=request.decision,
                    tool_name=pending.tool.get('name') or pending.request.tool_id,
                )
            else:
                response = await self._resume_approved_tool(pending)

            if not pending.wait_for_decision:
                self._pending.pop(pending_key, None)
            self._resolved[pending_key] = {
                'decision': request.decision,
                'response': response,
            }
            await _maybe_await(
                self.store.finish_operation_success(claim.operation.id, response)
            )
            return response
        except Exception as exc:
            await self._finish_decision_error(
                claim.operation.id,
                code=getattr(exc, 'code', 'approval_result_failed'),
                message=str(exc),
            )
            raise

    async def _wait_for_recorded_decision_and_resume(
        self,
        *,
        pending_key: tuple[str, str],
        requested_seq: int,
        approval_required_response: dict[str, Any],
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> dict[str, Any]:
        run_id, approval_id = pending_key
        deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 0.0)
        while True:
            recorded = await self._find_recorded_approval_decision(
                run_id=run_id,
                approval_id=approval_id,
                after_seq=requested_seq,
            )
            if recorded is not None:
                pending = self._pending.pop(pending_key, None)
                if pending is None:
                    return approval_required_response
                if recorded['decision'] == 'rejected':
                    response = _approval_rejected_result(
                        approval_id=approval_id,
                        pending=pending,
                    )
                else:
                    response = await self._resume_approved_tool(pending)
                self._resolved[pending_key] = {
                    'decision': recorded['decision'],
                    'response': response,
                }
                return response

            now = asyncio.get_running_loop().time()
            if now >= deadline:
                raise TimeoutError('approval decision wait timed out')
            await asyncio.sleep(min(poll_interval_seconds, max(deadline - now, 0.0)))

    async def _record_pending_decision(
        self,
        request: ApprovalDecisionRequest,
        pending: _PendingApproval,
    ) -> None:
        await _maybe_await(
            self.store.transition_state(
                request.run_id,
                from_states=[AgentRunState.WAITING_APPROVAL.value],
                to_state=AgentRunState.RUNNING.value,
                reason=f'approval {request.decision}',
                payload={
                    'approval_id': request.approval_id,
                    'decision': request.decision,
                },
            )
        )
        await _append_approval_event(
            self.store,
            request=pending.request,
            event_type=AgentEventType.APPROVAL_COMPLETED.value,
            phase=AgentRunState.RUNNING.value,
            summary=f'Approval {request.decision} for {pending.tool.get("name") or pending.request.tool_id}.',
            payload={
                **_approval_event_payload(
                    approval_id=request.approval_id,
                    request=pending.request,
                    tool=pending.tool,
                    assessment=pending.assessment,
                ),
                'decision': request.decision,
            },
        )

    async def _record_external_decision(
        self,
        request: ApprovalDecisionRequest,
        requested: dict[str, Any],
    ) -> dict[str, Any]:
        payload = requested['payload']
        await _maybe_await(
            self.store.transition_state(
                request.run_id,
                from_states=[AgentRunState.WAITING_APPROVAL.value],
                to_state=AgentRunState.RUNNING.value,
                reason=f'approval {request.decision}',
                payload={
                    'approval_id': request.approval_id,
                    'decision': request.decision,
                },
            )
        )
        completed_payload = {**payload, 'decision': request.decision}
        await _maybe_await(
            self.store.append_event(
                request.run_id,
                event_type=AgentEventType.APPROVAL_COMPLETED.value,
                participant_id=requested.get('participant_id'),
                phase=AgentRunState.RUNNING.value,
                summary=f'Approval {request.decision} for {payload.get("tool_name") or payload.get("tool_id")}.',
                payload=completed_payload,
            )
        )
        if request.decision == 'rejected':
            return _approval_rejected_result_from_payload(
                approval_id=request.approval_id,
                payload=completed_payload,
            )
        return _approval_recorded_result(
            approval_id=request.approval_id,
            decision=request.decision,
            tool_name=payload.get('tool_name') or payload.get('tool_id') or request.approval_id,
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

    async def _find_recorded_approval_decision(
        self,
        *,
        run_id: str,
        approval_id: str,
        after_seq: int,
    ) -> dict[str, Any] | None:
        event = await self._find_recorded_approval_event(
            run_id=run_id,
            approval_id=approval_id,
            event_type=AgentEventType.APPROVAL_COMPLETED.value,
            after_seq=after_seq,
        )
        if event is None:
            return None
        payload = event['payload']
        decision = payload.get('decision')
        if decision not in {'approved', 'rejected'}:
            return None
        return {**event, 'decision': decision}

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

    async def _resume_approved_tool(self, pending: _PendingApproval) -> dict[str, Any]:
        if pending.resume is None:
            return ToolCallResponse(
                status='success',
                content=f'Approval accepted for {pending.tool.get("name") or pending.request.tool_id}.',
            ).model_dump(mode='json')

        try:
            result = await _maybe_await(pending.resume())
        except Exception as exc:
            return normalize_tool_exception(exc)

        return normalize_tool_result(
            result,
            tool_name=pending.tool.get('name'),
            tool_id=pending.tool.get('tool_id'),
            tool_type=pending.tool.get('type'),
            arguments=pending.request.arguments,
        )

    async def _finish_decision_error(
        self,
        operation_id: str,
        *,
        code: str,
        message: str,
    ) -> None:
        await _maybe_await(
            self.store.finish_operation_error(
                operation_id,
                {
                    'code': code,
                    'message': message,
                },
            )
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


def _approval_rejected_result(
    *,
    approval_id: str,
    pending: _PendingApproval,
) -> dict[str, Any]:
    tool_name = pending.tool.get('name') or pending.request.tool_id
    message = f'User rejected approval for {tool_name}.'
    result = ToolCallResponse(
        status='approval_rejected',
        content=message,
        structured_error={
            'code': 'approval_rejected',
            'message': message,
            'retryable': False,
            'details': {
                'approval_id': approval_id,
                'tool_call_id': pending.request.tool_call_id,
                'tool_id': pending.request.tool_id,
            },
        },
        raw={
            'approval_id': approval_id,
            'decision': 'rejected',
        },
    ).model_dump(mode='json')
    return result


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
) -> dict[str, Any]:
    return ToolCallResponse(
        status='approval_recorded',
        content=f'Approval {decision} for {tool_name or approval_id}.',
        raw={
            'approval_id': approval_id,
            'decision': decision,
        },
    ).model_dump(mode='json')


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
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


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
