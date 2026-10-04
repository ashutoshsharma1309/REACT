"""Read services for incidents, risks and opportunities."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import BusinessRisk, Incident, Opportunity

SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _page(db: Session, stmt, limit: int, offset: int):
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    return db.scalars(stmt.limit(limit).offset(offset)).all(), total


def list_incidents(db: Session, status: str | None = None, severity: str | None = None, incident_type: str | None = None, machine_id: uuid.UUID | None = None, production_line_id: uuid.UUID | None = None, active_only: bool = False, limit: int = 100, offset: int = 0):
    stmt = select(Incident).order_by(Incident.detected_at.desc(), Incident.created_at.desc())
    if status:
        stmt = stmt.where(Incident.status == status.upper())
    if active_only:
        stmt = stmt.where(Incident.status != "RESOLVED")
    if severity:
        stmt = stmt.where(Incident.severity == severity.upper())
    if incident_type:
        stmt = stmt.where(Incident.incident_type == incident_type.upper())
    if machine_id:
        stmt = stmt.where(Incident.machine_id == machine_id)
    if production_line_id:
        stmt = stmt.where(Incident.production_line_id == production_line_id)
    items, total = _page(db, stmt, limit, offset)
    items = sorted(items, key=lambda i: (SEVERITY_RANK.get(i.severity.value, 9), -i.detected_at.timestamp()))
    return items, total


def get_incident(db: Session, incident_id: uuid.UUID) -> Incident | None:
    inc = db.scalar(select(Incident).options(selectinload(Incident.machine), selectinload(Incident.production_line)).where(Incident.id == incident_id))
    if inc:
        inc.machine_code = inc.machine.code if inc.machine else None  # type: ignore[attr-defined]
        inc.production_line_code = inc.production_line.code if inc.production_line else None  # type: ignore[attr-defined]
    return inc


def list_risks(db: Session, status: str | None = None, severity: str | None = None, risk_type: str | None = None, active_only: bool = False, limit: int = 100, offset: int = 0):
    stmt = select(BusinessRisk).order_by(BusinessRisk.detected_at.desc())
    if status:
        stmt = stmt.where(BusinessRisk.status == status.upper())
    if active_only:
        stmt = stmt.where(BusinessRisk.status != "RESOLVED")
    if severity:
        stmt = stmt.where(BusinessRisk.severity == severity.upper())
    if risk_type:
        stmt = stmt.where(BusinessRisk.risk_type == risk_type.upper())
    items, total = _page(db, stmt, limit, offset)
    items = sorted(items, key=lambda r: (SEVERITY_RANK.get(r.severity.value, 9), -r.detected_at.timestamp()))
    return items, total


def list_opportunities(db: Session, status: str | None = None, opportunity_type: str | None = None, product_id: uuid.UUID | None = None, region: str | None = None, confidence: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(Opportunity).order_by(Opportunity.detected_at.desc())
    if status:
        stmt = stmt.where(Opportunity.status == status.upper())
    if opportunity_type:
        stmt = stmt.where(Opportunity.opportunity_type == opportunity_type.upper())
    if product_id:
        stmt = stmt.where(Opportunity.product_id == product_id)
    if region:
        stmt = stmt.where(Opportunity.region == region)
    if confidence:
        stmt = stmt.where(Opportunity.confidence == confidence.upper())
    items, total = _page(db, stmt, limit, offset)
    conf_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    items = sorted(items, key=lambda o: (conf_rank.get(o.confidence.value, 9), -float(o.estimated_impact.get("value", 0) or 0)))
    return items, total


def get_opportunity(db: Session, opportunity_id: uuid.UUID) -> Opportunity | None:
    opp = db.scalar(select(Opportunity).options(selectinload(Opportunity.product)).where(Opportunity.id == opportunity_id))
    if opp:
        opp.product_name = opp.product.name if opp.product else None  # type: ignore[attr-defined]
    return opp
