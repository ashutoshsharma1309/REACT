"""Read services for decisions, approvals and actions."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import ActionExecution, AgentRun, AgentTraceEntry, Approval, Decision, EvidenceItem


def _page(db: Session, stmt, limit: int, offset: int):
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    return db.scalars(stmt.limit(limit).offset(offset)).all(), total


def list_decisions(db: Session, status: str | None = None, subject_type: str | None = None, action_type: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(Decision).order_by(Decision.created_at.desc())
    if status:
        stmt = stmt.where(Decision.status == status.upper())
    if subject_type:
        stmt = stmt.where(Decision.subject_type == subject_type.upper())
    if action_type:
        stmt = stmt.where(Decision.action_type == action_type.upper())
    return _page(db, stmt, limit, offset)


def get_decision(db: Session, decision_id: uuid.UUID) -> Decision | None:
    return db.scalar(select(Decision).options(selectinload(Decision.approvals), selectinload(Decision.actions)).where(Decision.id == decision_id))


def latest_for_subject(db: Session, incident_id=None, opportunity_id=None, risk_id=None) -> Decision | None:
    stmt = select(Decision).order_by(Decision.created_at.desc())
    if incident_id:
        stmt = stmt.where(Decision.incident_id == incident_id)
    elif opportunity_id:
        stmt = stmt.where(Decision.opportunity_id == opportunity_id)
    else:
        stmt = stmt.where(Decision.risk_id == risk_id)
    d = db.scalar(stmt)
    return get_decision(db, d.id) if d else None


def decision_evidence(db: Session, d: Decision) -> dict:
    ids = [uuid.UUID(i) for i in (d.evidence_ids or []) + (d.contradicting_evidence_ids or [])]
    items = db.scalars(select(EvidenceItem).where(EvidenceItem.id.in_(ids))).all() if ids else []
    by_id = {str(e.id): e for e in items}
    def out(e: EvidenceItem) -> dict:
        return {"evidence_id": str(e.id), "hypothesis_id": str(e.hypothesis_id), "signal": e.signal, "claim": e.claim, "value": e.value, "unit": e.unit, "baseline_value": e.baseline_value, "supports": e.supports, "contradicts": e.contradicts, "evidence_support_score": e.evidence_support_score, "source_type": e.source_type, "source_id": e.source_id, "source_ids": e.source_ids[:20], "timestamp": e.timestamp}
    return {
        "decision_id": d.id, "investigation_id": d.investigation_id, "agent_run_id": d.agent_run_id, "hypothesis_id": d.hypothesis_id, "hypothesis_type": d.hypothesis_type, "evidence_support_score": d.evidence_support_score,
        "supporting": [out(by_id[i]) for i in d.evidence_ids if i in by_id], "contradicting": [out(by_id[i]) for i in d.contradicting_evidence_ids if i in by_id],
        "expected_impact": d.expected_impact, "chain": ["decision", "agent_run", "investigation", "hypothesis", "evidence_item", "business records (source_ids)"],
    }


def decision_trace(db: Session, d: Decision) -> dict:
    agent_trace = []
    if d.agent_run_id:
        run = db.scalar(select(AgentRun).options(selectinload(AgentRun.trace_entries)).where(AgentRun.id == d.agent_run_id))
        agent_trace = [{"sequence": t.sequence, "timestamp": t.timestamp, "agent": t.agent_name, "event_type": t.event_type, "state": t.state, "tool_name": t.tool_name, "summary": t.summary, "evidence_ids": t.evidence_ids, "status": t.status} for t in (run.trace_entries if run else [])]
    return {
        "decision_id": d.id, "status": d.status.value, "agent_run_id": d.agent_run_id, "investigation_id": d.investigation_id,
        "timeline": d.timeline, "approvals": [{"approval_id": a.id, "status": a.status.value, "requested_at": a.requested_at, "reviewed_at": a.reviewed_at, "reviewed_by": a.reviewed_by, "reason": a.reason} for a in d.approvals],
        "actions": [{"action_id": a.id, "action_type": a.action_type, "status": a.status.value, "simulated": a.simulated, "transitions": a.transitions, "result": a.result} for a in d.actions],
        "agent_trace": agent_trace,
    }


def list_approvals(db: Session, status: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(Approval).order_by(Approval.requested_at.desc())
    if status:
        stmt = stmt.where(Approval.status == status.upper())
    return _page(db, stmt, limit, offset)


def list_actions(db: Session, status: str | None = None, action_type: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(ActionExecution).order_by(ActionExecution.created_at.desc())
    if status:
        stmt = stmt.where(ActionExecution.status == status.upper())
    if action_type:
        stmt = stmt.where(ActionExecution.action_type == action_type.upper())
    return _page(db, stmt, limit, offset)
