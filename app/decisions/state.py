"""State machines for decisions, approvals and actions. Invalid transitions raise."""
from __future__ import annotations

from app.models.enums import ActionStatus, ApprovalStatus, DecisionStatus

DECISION_TRANSITIONS: dict[DecisionStatus, frozenset[DecisionStatus]] = {
    DecisionStatus.DECISION_READY: frozenset({DecisionStatus.AWAITING_APPROVAL, DecisionStatus.INSUFFICIENT_EVIDENCE, DecisionStatus.FAILED}),
    DecisionStatus.INSUFFICIENT_EVIDENCE: frozenset(),
    DecisionStatus.AWAITING_APPROVAL: frozenset({DecisionStatus.APPROVED, DecisionStatus.REJECTED, DecisionStatus.FAILED}),
    DecisionStatus.APPROVED: frozenset({DecisionStatus.EXECUTING, DecisionStatus.FAILED}),
    DecisionStatus.REJECTED: frozenset(),
    DecisionStatus.EXECUTING: frozenset({DecisionStatus.EXECUTED, DecisionStatus.FAILED}),
    DecisionStatus.EXECUTED: frozenset(),
    DecisionStatus.FAILED: frozenset(),
}

APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset({ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
}

ACTION_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.PROPOSED: frozenset({ActionStatus.APPROVED, ActionStatus.REJECTED}),
    ActionStatus.APPROVED: frozenset({ActionStatus.EXECUTING, ActionStatus.FAILED}),
    ActionStatus.REJECTED: frozenset(),
    ActionStatus.EXECUTING: frozenset({ActionStatus.COMPLETED, ActionStatus.FAILED}),
    ActionStatus.COMPLETED: frozenset(),
    ActionStatus.FAILED: frozenset(),
}


class InvalidTransition(ValueError):
    pass


def check(table: dict, current, new, entity: str) -> None:
    if new not in table[current]:
        raise InvalidTransition(f"{entity} cannot move from {current.value} to {new.value}")
