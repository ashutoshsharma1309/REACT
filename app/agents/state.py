"""Agent run state machine: CREATED -> CLASSIFYING -> INVESTIGATING -> VERIFYING -> COMPLETED, any -> FAILED."""
from __future__ import annotations

from app.models.enums import AgentRunStatus

TRANSITIONS: dict[AgentRunStatus, frozenset[AgentRunStatus]] = {
    AgentRunStatus.CREATED: frozenset({AgentRunStatus.CLASSIFYING, AgentRunStatus.FAILED}),
    AgentRunStatus.CLASSIFYING: frozenset({AgentRunStatus.INVESTIGATING, AgentRunStatus.FAILED}),
    AgentRunStatus.INVESTIGATING: frozenset({AgentRunStatus.VERIFYING, AgentRunStatus.FAILED}),
    AgentRunStatus.VERIFYING: frozenset({AgentRunStatus.COMPLETED, AgentRunStatus.FAILED}),
    AgentRunStatus.COMPLETED: frozenset(),
    AgentRunStatus.FAILED: frozenset(),
}


class IllegalTransition(RuntimeError):
    pass


def assert_transition(current: AgentRunStatus, new: AgentRunStatus) -> None:
    if new not in TRANSITIONS[current]:
        raise IllegalTransition(f"cannot move agent run from {current.value} to {new.value}")


def is_terminal(state: AgentRunStatus) -> bool:
    return state in (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED)
