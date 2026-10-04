"""Read services for agent runs."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.policies import policy_report
from app.agents.provider import build_provider
from app.agents.state import TRANSITIONS
from app.config import settings
from app.models import AgentRun


def list_runs(db: Session, status: str | None = None, subject_type: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(AgentRun).order_by(AgentRun.created_at.desc())
    if status:
        stmt = stmt.where(AgentRun.status == status.upper())
    if subject_type:
        stmt = stmt.where(AgentRun.subject_type == subject_type.upper())
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    return db.scalars(stmt.limit(limit).offset(offset)).all(), total


def get_run(db: Session, run_id: uuid.UUID) -> AgentRun | None:
    return db.scalar(select(AgentRun).options(selectinload(AgentRun.trace_entries), selectinload(AgentRun.tool_calls)).where(AgentRun.id == run_id))


def latest_for_subject(db: Session, incident_id=None, opportunity_id=None, risk_id=None) -> AgentRun | None:
    stmt = select(AgentRun).order_by(AgentRun.created_at.desc())
    if incident_id:
        stmt = stmt.where(AgentRun.incident_id == incident_id)
    elif opportunity_id:
        stmt = stmt.where(AgentRun.opportunity_id == opportunity_id)
    else:
        stmt = stmt.where(AgentRun.risk_id == risk_id)
    run = db.scalar(stmt)
    return get_run(db, run.id) if run else None


def roster(domain: str = "manufacturing") -> dict:
    """Agents, responsibilities, tool policy, state machine and provider configuration (no secrets)."""
    try:
        provider = build_provider().describe()
    except Exception as exc:  # noqa: BLE001
        provider = {"provider": settings.llm_provider, "error": type(exc).__name__}
    return {
        "enabled": settings.agents_enabled,
        "domain": domain,
        "agents": policy_report(domain),
        "state_machine": {k.value: sorted(v.value for v in vs) for k, vs in TRANSITIONS.items()},
        "provider": provider,
        "limits": {"max_iterations": settings.agent_max_iterations, "max_tool_calls": settings.agent_max_tool_calls, "max_llm_calls": settings.agent_max_llm_calls, "max_execution_seconds": settings.agent_max_execution_seconds, "max_retries": settings.llm_max_retries, "fallback_to_deterministic": settings.agent_fallback_to_deterministic},
        "principles": ["LLM outputs are Pydantic-validated and may only reference evidence / hypothesis ids produced by the deterministic engine", "Money is computed only by app/impact/engine.py", "No SQL, code execution, HTTP or filesystem access for agents; only registered tools", "No hidden chain-of-thought is stored"],
    }
