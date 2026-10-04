"""Shared helpers for the REACT Merchant tests."""
from __future__ import annotations

from sqlalchemy import select

from app.detection import DetectionEngine
from app.domains.merchant.scenarios import build_scenario
from app.domains.merchant.simulation import regenerate
from app.models import BusinessRisk, Opportunity, Product, SystemState
from app.opportunities import OpportunityEngine


def run_scenario(db, scenario: str | None):
    """Regenerate the business with a scenario and run both detection engines (inside the test transaction)."""
    regenerate(db, scenario=scenario)
    DetectionEngine(db).run()
    OpportunityEngine(db).run()
    return db.get(SystemState, 1).profile["roles"]


def featured(db, scenario: str):
    """The finding the scenario is designed to surface."""
    roles = db.get(SystemState, 1).profile["roles"]
    spec = build_scenario(scenario, roles)
    if spec.finding_kind == "opportunity":
        rows = db.scalars(select(Opportunity).where(Opportunity.opportunity_type == spec.finding_type)).all()
        rows = [o for o in rows if (o.region or "").startswith(roles["opportunity_zone"])] or rows
        return rows[0] if rows else None
    rows = db.scalars(select(BusinessRisk).where(BusinessRisk.risk_type == spec.finding_type)).all()
    sku = {"STOCKOUT_RISK": roles["stockout_sku"], "MARGIN_DECLINE": roles["margin_sku"], "EXCESS_INVENTORY": roles["excess_sku"]}.get(spec.finding_type)
    if sku:
        pid = db.scalar(select(Product.id).where(Product.sku == sku))
        rows = [r for r in rows if r.entity_id == pid]
    return rows[0] if rows else None


def subject_kw(f):
    return {"opportunity_id": f.id} if isinstance(f, Opportunity) else {"risk_id": f.id}
