"""Read services for investigations."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import BusinessRisk, EvidenceItem, Hypothesis, Incident, Investigation, Opportunity


def list_investigations(db: Session, status: str | None = None, subject_type: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(Investigation).order_by(Investigation.created_at.desc())
    if status:
        stmt = stmt.where(Investigation.status == status.upper())
    if subject_type:
        stmt = stmt.where(Investigation.subject_type == subject_type.upper())
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    return db.scalars(stmt.limit(limit).offset(offset)).all(), total


def get_investigation(db: Session, investigation_id: uuid.UUID) -> Investigation | None:
    inv = db.scalar(select(Investigation).options(selectinload(Investigation.hypotheses), selectinload(Investigation.trace_entries)).where(Investigation.id == investigation_id))
    if inv:
        inv.evidence_count = db.scalar(select(func.count()).select_from(EvidenceItem).where(EvidenceItem.investigation_id == inv.id)) or 0  # type: ignore[attr-defined]
        inv.trace = inv.trace_entries  # type: ignore[attr-defined]
    return inv


def latest_for_subject(db: Session, incident_id: uuid.UUID | None = None, opportunity_id: uuid.UUID | None = None, risk_id: uuid.UUID | None = None) -> Investigation | None:
    stmt = select(Investigation).order_by(Investigation.created_at.desc())
    if incident_id:
        stmt = stmt.where(Investigation.incident_id == incident_id)
    elif opportunity_id:
        stmt = stmt.where(Investigation.opportunity_id == opportunity_id)
    else:
        stmt = stmt.where(Investigation.risk_id == risk_id)
    inv = db.scalar(stmt)
    return get_investigation(db, inv.id) if inv else None


def list_hypotheses(db: Session, investigation_id: uuid.UUID):
    return db.scalars(select(Hypothesis).where(Hypothesis.investigation_id == investigation_id).order_by(Hypothesis.rank)).all()


def list_evidence(db: Session, investigation_id: uuid.UUID, hypothesis_id: uuid.UUID | None = None, supports: bool | None = None):
    stmt = select(EvidenceItem).where(EvidenceItem.investigation_id == investigation_id)
    if hypothesis_id:
        stmt = stmt.where(EvidenceItem.hypothesis_id == hypothesis_id)
    if supports is not None:
        stmt = stmt.where(EvidenceItem.supports.is_(supports))
    return db.scalars(stmt.order_by(EvidenceItem.evidence_support_score.desc(), EvidenceItem.claim)).all()


def subject_evidence(db: Session, subject_type: str, subject_id: uuid.UUID) -> dict | None:
    model = {"incident": Incident, "opportunity": Opportunity, "risk": BusinessRisk}[subject_type]
    subject = db.get(model, subject_id)
    if subject is None:
        return None
    inv = latest_for_subject(db, **{f"{subject_type}_id": subject_id})
    items = list_evidence(db, inv.id) if inv else []
    return {"subject_type": subject_type.upper(), "subject_id": subject_id, "detection_evidence": subject.evidence or {}, "investigation_id": inv.id if inv else None, "investigation_status": inv.status.value if inv else None, "evidence": items}
