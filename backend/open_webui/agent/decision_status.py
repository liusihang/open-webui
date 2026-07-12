from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DecisionExecutionResponse(BaseModel):
    model_config = ConfigDict(extra='allow')

    status: str
    execution_id: str | None
    execution_status: str


class DecisionExecutionStatus(StrEnum):
    PENDING = 'pending'
    CLAIMED = 'claimed'
    PREPARED = 'prepared'
    COMMITTING = 'committing'
    BACKEND_COMMITTED = 'backend_committed'
    ACTIVATING = 'activating'
    ACTIVATED = 'activated'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    FAILING = 'failing'
    CANCELLED = 'cancelled'


NONTERMINAL_DECISION_EXECUTION_STATUSES = frozenset(
    {
        DecisionExecutionStatus.PENDING.value,
        DecisionExecutionStatus.CLAIMED.value,
        DecisionExecutionStatus.PREPARED.value,
        DecisionExecutionStatus.COMMITTING.value,
        DecisionExecutionStatus.BACKEND_COMMITTED.value,
        DecisionExecutionStatus.ACTIVATING.value,
        DecisionExecutionStatus.ACTIVATED.value,
        DecisionExecutionStatus.FAILING.value,
    }
)

TERMINAL_DECISION_EXECUTION_STATUSES = frozenset(
    {
        DecisionExecutionStatus.SUCCEEDED.value,
        DecisionExecutionStatus.FAILED.value,
        DecisionExecutionStatus.CANCELLED.value,
    }
)


def is_nonterminal_decision_status(value: object) -> bool:
    return value in NONTERMINAL_DECISION_EXECUTION_STATUSES
