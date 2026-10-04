"""Provider guard applied before any agent work: the AI-outage drill and the hourly LLM call budget.

Both replace the configured provider with an UnavailableProvider, so the normal, tested failure path runs:
the failure is recorded in the visible trace, then either the deterministic fallback is used (and marked
fallback_used) or, with fallback disabled, the run fails with "AI investigation temporarily unavailable". Nothing is guessed.

The budget is counted in PostgreSQL (sum of LLM calls on agent runs in the last hour), so it holds across every
API container, not per process.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.provider import LLMProvider, UnavailableProvider
from app.database.base import utcnow
from app.observability import get_logger, log_event

log = get_logger("react.agent_runtime.guard")

OUTAGE_MODES = ("off", "fallback", "fail")
NO_BUDGET_PROVIDERS = ("deterministic", "unavailable")


@dataclass
class GuardResult:
    provider: LLMProvider
    fallback_enabled: bool
    reason: str | None = None  # why the provider was replaced (None = unchanged)


def outage_mode(db: Session) -> str:
    from app.models import SystemState

    state = db.get(SystemState, 1)
    mode = (state.ai_outage_mode if state else "off") or "off"
    return mode if mode in OUTAGE_MODES else "off"


def llm_calls_last_hour(db: Session) -> int:
    from app.models import AgentRun

    since = utcnow() - timedelta(hours=1)
    q = select(func.coalesce(func.sum(AgentRun.llm_calls_count), 0)).where(AgentRun.created_at >= since, AgentRun.provider.notin_(NO_BUDGET_PROVIDERS))
    return int(db.scalar(q) or 0)


def guard_provider(db: Session, provider: LLMProvider, fallback_enabled: bool) -> GuardResult:
    if isinstance(provider, UnavailableProvider):
        return GuardResult(provider, fallback_enabled)
    mode = outage_mode(db)
    if mode != "off":
        reason = "Simulated AI outage (demo drill): the language model is treated as unreachable"
        log_event(log, "ai.outage_drill", mode=mode, provider=provider.name)
        return GuardResult(UnavailableProvider(reason, model=provider.model), fallback_enabled=(mode == "fallback"), reason=reason)
    if provider.is_llm and provider.name not in NO_BUDGET_PROVIDERS:
        from app.config import settings
        from app.observability.metrics import emit

        used = llm_calls_last_hour(db)
        if used >= settings.llm_hourly_call_budget:
            reason = f"Hourly LLM call budget reached ({used} of {settings.llm_hourly_call_budget} calls in the last hour)"
            log_event(log, "ai.budget_exhausted", level=30, used=used, budget=settings.llm_hourly_call_budget)
            emit({"LLMBudgetExhausted": 1})
            return GuardResult(UnavailableProvider(reason, model=provider.model), fallback_enabled, reason=reason)
    return GuardResult(provider, fallback_enabled)
