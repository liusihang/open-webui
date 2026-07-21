from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
from uuid import uuid4

from open_webui.agent.decision_status import (
    NONTERMINAL_DECISION_EXECUTION_STATUSES,
    TERMINAL_DECISION_EXECUTION_STATUSES,
    DecisionExecutionStatus,
)
from open_webui.agent.runtime_client import (
    AgentRuntimeAuthenticationError,
    AgentRuntimeClient,
    AgentRuntimeRejected,
    AgentRuntimeUnavailable,
)
from open_webui.models.agent_runs import (
    AgentRunDecisionConflict,
    AgentRunDecisionExecutionModel,
    AgentRuns,
)

log = logging.getLogger(__name__)

DEFAULT_DECISION_LEASE_SECONDS = 30.0
DEFAULT_DECISION_HEARTBEAT_SECONDS = 10.0
DEFAULT_USER_INPUT_TIMEOUT_SCAN_SECONDS = 5.0


class AgentDecisionExecutionLeaseLost(AgentRunDecisionConflict):
    code = 'decision_execution_lease_lost'


def _max_retry_after_seconds(
    *errors: AgentRuntimeUnavailable,
) -> float | None:
    values = [
        error.retry_after_seconds
        for error in errors
        if error.retry_after_seconds is not None
    ]
    return max(values) if values else None


class AgentDecisionExecutionDispatcher:
    def __init__(
        self,
        store,
        runtime_client: AgentRuntimeClient,
        *,
        worker_id: str | None = None,
        lease_seconds: float = DEFAULT_DECISION_LEASE_SECONDS,
        heartbeat_seconds: float = DEFAULT_DECISION_HEARTBEAT_SECONDS,
        clock_ns: Callable[[], int] | None = None,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        if heartbeat_seconds <= 0 or lease_seconds <= heartbeat_seconds:
            raise ValueError(
                'decision execution lease must be longer than heartbeat interval'
            )
        self.store = store
        self.runtime_client = runtime_client
        self.worker_id = worker_id or f'agent-decision-{uuid4()}'
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.clock_ns = clock_ns
        self.random_fn = random_fn

    async def dispatch_execution(  # noqa: C901
        self,
        execution_id: str,
    ) -> AgentRunDecisionExecutionModel:
        execution = await self.store.get_decision_execution(execution_id)
        if execution is None:
            raise AgentRunDecisionConflict(f'Unknown decision execution: {execution_id}')
        if execution.status in TERMINAL_DECISION_EXECUTION_STATUSES:
            return execution

        if (
            execution.status in NONTERMINAL_DECISION_EXECUTION_STATUSES
            and execution.status
            not in {
                DecisionExecutionStatus.COMMITTING,
                DecisionExecutionStatus.FAILING,
            }
            and execution.claim_owner != self.worker_id
        ):
            claim = await self.store.claim_decision_execution(
                execution.id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if claim is None:
                current = await self.store.get_decision_execution(execution.id)
                if current is None:
                    raise AgentRunDecisionConflict(
                        f'Unknown decision execution: {execution.id}'
                    )
                return current
            execution = claim.execution

        if execution.status == DecisionExecutionStatus.CLAIMED:
            try:
                prepared = await self._prepare_or_query(execution)
                self._validate_runtime_response(
                    execution,
                    prepared,
                    stage='prepare',
                )
            except AgentDecisionExecutionLeaseLost:
                raise
            except (AgentRuntimeRejected, AgentRunDecisionConflict) as exc:
                return await self.store.fail_decision_execution(
                    execution.id,
                    claim_token=execution.claim_token,
                    error={
                        'code': getattr(exc, 'code', 'decision_execution_protocol_error'),
                        'message': str(exc),
                        'stage': 'prepare',
                    },
                )
            except AgentRuntimeAuthenticationError as exc:
                return await self.store.fail_decision_execution(
                    execution.id,
                    claim_token=execution.claim_token,
                    error={
                        'code': exc.code,
                        'message': str(exc),
                        'stage': 'prepare',
                    },
                )
            execution = await self.store.mark_decision_execution_prepared(
                execution.id,
                prepared,
                claim_token=execution.claim_token,
            )

        if execution.status == DecisionExecutionStatus.PREPARED:
            execution = await self.store.commit_prepared_decision_execution(
                execution.id,
                claim_token=execution.claim_token,
            )

        if execution.status in {
            DecisionExecutionStatus.BACKEND_COMMITTED,
            DecisionExecutionStatus.ACTIVATED,
        }:
            execution = await self.store.begin_decision_activation(
                execution.id,
                claim_token=execution.claim_token,
            )

        if execution.status == DecisionExecutionStatus.ACTIVATING:
            try:
                activated = await self._activate_or_query(execution)
                self._validate_runtime_response(
                    execution,
                    activated,
                    stage='activate',
                )
            except AgentDecisionExecutionLeaseLost:
                raise
            except (AgentRuntimeRejected, AgentRunDecisionConflict) as exc:
                return await self.store.fail_decision_execution(
                    execution.id,
                    claim_token=execution.claim_token,
                    error={
                        'code': getattr(exc, 'code', 'decision_execution_protocol_error'),
                        'message': str(exc),
                        'stage': 'activate',
                    },
                )
            except AgentRuntimeAuthenticationError as exc:
                return await self.store.fail_decision_execution(
                    execution.id,
                    claim_token=execution.claim_token,
                    error={
                        'code': exc.code,
                        'message': str(exc),
                        'stage': 'activate',
                    },
                )
            if activated.get('state') in {
                'failed',
                'indeterminate',
                'unrecoverable',
            }:
                return await self.store.fail_decision_execution(
                    execution.id,
                    claim_token=execution.claim_token,
                    error={
                        'code': str(
                            (activated.get('error') or {}).get('code')
                            or activated.get('state')
                        ),
                        'message': str(
                            (activated.get('error') or {}).get('message')
                            or f'Runtime execution {activated.get("state")}'
                        ),
                        'stage': 'activate',
                    },
                )
            execution = await self.store.record_decision_runtime_state(
                execution.id,
                activated,
                claim_token=execution.claim_token,
            )
        return execution

    async def _prepare_or_query(
        self,
        execution: AgentRunDecisionExecutionModel,
    ) -> dict[str, Any]:
        payload = _prepare_payload(execution)
        try:
            return await self._call_with_claim_heartbeat(
                execution,
                lambda: self.runtime_client.prepare_decision_execution(
                    execution.run_id,
                    execution.id,
                    payload,
                ),
            )
        except AgentRuntimeUnavailable as initial_exc:
            try:
                return await self._call_with_claim_heartbeat(
                    execution,
                    lambda: self.runtime_client.get_decision_execution(
                        execution.run_id,
                        execution.id,
                    ),
                )
            except AgentRuntimeUnavailable as exc:
                await self.store.release_decision_execution(
                    execution.id,
                    {
                        'code': exc.code,
                        'message': str(exc),
                        'stage': 'prepare',
                    },
                    claim_token=execution.claim_token,
                    now_ns=self.clock_ns() if self.clock_ns is not None else None,
                    jitter_fraction=self.random_fn(),
                    retry_after_seconds=_max_retry_after_seconds(
                        initial_exc,
                        exc,
                    ),
                )
                raise
        except AgentRuntimeRejected:
            raise

    async def _activate_or_query(
        self,
        execution: AgentRunDecisionExecutionModel,
    ) -> dict[str, Any]:
        try:
            return await self._call_with_claim_heartbeat(
                execution,
                lambda: self.runtime_client.activate_decision_execution(
                    execution.run_id,
                    execution.id,
                ),
            )
        except AgentRuntimeUnavailable as initial_exc:
            try:
                return await self._call_with_claim_heartbeat(
                    execution,
                    lambda: self.runtime_client.get_decision_execution(
                        execution.run_id,
                        execution.id,
                    ),
                )
            except AgentRuntimeUnavailable as exc:
                await self.store.release_decision_execution(
                    execution.id,
                    {
                        'code': exc.code,
                        'message': str(exc),
                        'stage': 'activate',
                    },
                    claim_token=execution.claim_token,
                    now_ns=self.clock_ns() if self.clock_ns is not None else None,
                    jitter_fraction=self.random_fn(),
                    retry_after_seconds=_max_retry_after_seconds(
                        initial_exc,
                        exc,
                    ),
                )
                raise

    async def _call_with_claim_heartbeat(
        self,
        execution: AgentRunDecisionExecutionModel,
        call: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        operation_task = asyncio.create_task(call())
        heartbeat_task = asyncio.create_task(self._heartbeat_claim(execution))
        try:
            done, _ = await asyncio.wait(
                {operation_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                if heartbeat_error is not None:
                    operation_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await operation_task
                    raise heartbeat_error
            return await operation_task
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            if not operation_task.done():
                operation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await operation_task

    async def _heartbeat_claim(
        self,
        execution: AgentRunDecisionExecutionModel,
    ) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            renewed = await self.store.renew_decision_execution_claim(
                execution.id,
                claim_token=execution.claim_token,
                lease_seconds=self.lease_seconds,
            )
            if not renewed:
                raise AgentDecisionExecutionLeaseLost(
                    f'Decision execution {execution.id} lease was lost'
                )

    @staticmethod
    def _validate_runtime_response(
        execution: AgentRunDecisionExecutionModel,
        response: dict[str, Any],
        *,
        stage: str,
    ) -> None:
        expected = {
            'execution_id': execution.id,
            'run_id': execution.run_id,
            'runtime_session_id': execution.runtime_session_id,
            'subject_id': execution.resource_id,
            'command_type': execution.command_type,
        }
        mismatched = {
            key: {'expected': value, 'actual': response.get(key)}
            for key, value in expected.items()
            if response.get(key) != value
        }
        if mismatched:
            raise AgentRunDecisionConflict(
                f'Runtime decision execution response mismatch: {mismatched}'
            )
        if response.get('fingerprint') != execution.fingerprint:
            raise AgentRunDecisionConflict(
                'Runtime decision execution fingerprint mismatch'
            )
        checkpoint_version = response.get('checkpoint_version')
        if not isinstance(checkpoint_version, int):
            raise AgentRunDecisionConflict(
                'Runtime decision execution checkpoint version is invalid'
            )
        state = response.get('state')
        if stage == 'prepare':
            if state != 'prepared':
                raise AgentRunDecisionConflict(
                    f'Runtime prepare returned invalid state: {state}'
                )
            if checkpoint_version != execution.expected_checkpoint_version:
                raise AgentRunDecisionConflict(
                    'Runtime prepare checkpoint version mismatch'
                )
            return
        if checkpoint_version < execution.expected_checkpoint_version:
            raise AgentRunDecisionConflict(
                'Runtime activate checkpoint version moved backwards'
            )
        if state not in {
            'activated',
            'applying',
            'applied',
            'cancelled',
            'failed',
            'indeterminate',
            'unrecoverable',
        }:
            raise AgentRunDecisionConflict(
                f'Runtime activate returned invalid state: {state}'
            )


def _prepare_payload(execution: AgentRunDecisionExecutionModel) -> dict[str, Any]:
    return {
        'schema_version': 1,
        'runtime_session_id': execution.runtime_session_id,
        'execution_id': execution.id,
        'expected_checkpoint_version': execution.expected_checkpoint_version,
        'subject_id': execution.resource_id,
        'command_type': execution.command_type,
        'payload': execution.command_payload,
        'fingerprint': execution.fingerprint,
    }


async def _scan_user_input_timeouts() -> None:
    try:
        await AgentRuns.reconcile_legacy_user_inputs()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception('Legacy Agent user-input reconciliation failed')
    try:
        await AgentRuns.expire_due_user_inputs()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception('Agent user-input timeout scan failed')


async def agent_decision_dispatcher_loop(
    app,
    *,
    poll_seconds: float = 0.25,
    timeout_scan_seconds: float = DEFAULT_USER_INPUT_TIMEOUT_SCAN_SECONDS,
) -> None:
    if timeout_scan_seconds <= 0:
        raise ValueError('user-input timeout scan interval must be positive')
    client = AgentRuntimeClient(
        getattr(app.state.config, 'AGENT_RUNTIME_BASE_URL', ''),
        service_token=getattr(
            app.state.config,
            'AGENT_RUNTIME_SERVICE_TOKEN',
            '',
        ),
        timeout=getattr(
            app.state.config,
            'AGENT_RUN_DEFAULT_TIMEOUT_SECONDS',
            None,
        ),
    )
    dispatcher = AgentDecisionExecutionDispatcher(AgentRuns, client)
    next_timeout_scan_at = 0.0
    while True:
        loop_now = asyncio.get_running_loop().time()
        if loop_now >= next_timeout_scan_at:
            next_timeout_scan_at = loop_now + timeout_scan_seconds
            await _scan_user_input_timeouts()
        try:
            execution = await AgentRuns.claim_next_decision_execution(
                worker_id=dispatcher.worker_id,
                lease_seconds=dispatcher.lease_seconds,
            )
            if execution is None:
                await asyncio.sleep(poll_seconds)
                continue
            await dispatcher.dispatch_execution(execution.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Agent decision execution dispatch failed')
            await asyncio.sleep(poll_seconds)
