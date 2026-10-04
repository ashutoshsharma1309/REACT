"""Hard limits for agent execution. No loop in REACT runs without one of these counters."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AgentLimits:
    max_iterations: int = 4
    max_tool_calls: int = 12
    max_llm_calls: int = 10
    max_execution_seconds: float = 120.0
    max_retries: int = 1

    @classmethod
    def from_settings(cls) -> "AgentLimits":
        from app.config import settings

        return cls(max_iterations=settings.agent_max_iterations, max_tool_calls=settings.agent_max_tool_calls, max_llm_calls=settings.agent_max_llm_calls, max_execution_seconds=settings.agent_max_execution_seconds, max_retries=settings.llm_max_retries)

    def as_dict(self) -> dict:
        return asdict(self)


class LimitExceeded(RuntimeError):
    def __init__(self, kind: str, limit: int | float, actual: int | float) -> None:
        super().__init__(f"{kind} limit exceeded ({actual} > {limit})")
        self.kind, self.limit, self.actual = kind, limit, actual


class LimitTracker:
    def __init__(self, limits: AgentLimits) -> None:
        self.limits = limits
        self.iterations = 0
        self.tool_calls = 0
        self.llm_calls = 0
        self.started = time.perf_counter()

    @property
    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self.started

    def check_time(self) -> None:
        if self.elapsed_seconds > self.limits.max_execution_seconds:
            raise LimitExceeded("execution_time", self.limits.max_execution_seconds, round(self.elapsed_seconds, 1))

    def iteration(self) -> int:
        self.check_time()
        self.iterations += 1
        if self.iterations > self.limits.max_iterations:
            raise LimitExceeded("iterations", self.limits.max_iterations, self.iterations)
        return self.iterations

    def tool_call(self) -> int:
        self.check_time()
        self.tool_calls += 1
        if self.tool_calls > self.limits.max_tool_calls:
            raise LimitExceeded("tool_calls", self.limits.max_tool_calls, self.tool_calls)
        return self.tool_calls

    def llm_call(self) -> int:
        self.check_time()
        self.llm_calls += 1
        if self.llm_calls > self.limits.max_llm_calls:
            raise LimitExceeded("llm_calls", self.limits.max_llm_calls, self.llm_calls)
        return self.llm_calls

    def snapshot(self) -> dict:
        return {"iterations": self.iterations, "tool_calls": self.tool_calls, "llm_calls": self.llm_calls, "elapsed_seconds": round(self.elapsed_seconds, 2), "limits": self.limits.as_dict()}
