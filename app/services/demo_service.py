"""Deterministic demo runner: reset → scenario → business scan. Uses only existing engines.

The reset reuses the stored seed and simulated clock, so every run of a scenario produces the same
business data and the same findings.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.detection import DetectionEngine
from app.models import BusinessRisk, Incident, Opportunity, Product
from app.observability import get_logger, log_event
from app.opportunities import OpportunityEngine
from app.simulation.engine import SimulationEngine
from app.simulation.scenarios import SCENARIOS

log = get_logger("react.demo")

DEMO_SCENARIOS: dict[str, dict[str, str]] = {
    "bearing_failure": {"label": "Machine bearing failure", "story": "A spindle bearing on a CNC machine is wearing out. REACT detects it, finds the cause, prices the impact and recommends maintenance."},
    "demand_spike": {"label": "Demand spike", "story": "Demand for Industrial Valve X jumps in South India. REACT explains why and sizes the opportunity."},
    "supplier_delay": {"label": "Supplier delay", "story": "A sole-source supplier is 10 days late. REACT traces the stock and order exposure."},
    "baseline": {"label": "Normal business", "story": "Reset to the normal baseline and run a business scan."},
}


MERCHANT_DEMO_SCENARIOS: dict[str, dict[str, str]] = {
    "stockout_crisis": {"label": "Stockout crisis", "story": "A best-seller is selling much faster than usual while its supplier has slowed down. REACT spots the empty shelf before the next delivery, prices the lost sales and recommends a reorder."},
    "demand_opportunity": {"label": "Demand opportunity", "story": "One category is selling much faster among customers in the store's own neighbourhood. REACT explains why and sizes the extra revenue."},
    "supplier_delay": {"label": "Supplier delay", "story": "A regular supplier has started delivering late, order after order. REACT traces which products depend on it and what the delays put at risk."},
    "margin_leakage": {"label": "Margin leakage", "story": "A product is selling more than ever, but each sale earns less: the supplier raised its price and the shelf price never changed."},
    "excess_inventory": {"label": "Excess inventory", "story": "A large lot bought months ago is barely selling. REACT shows how much cash is locked in the shelf and what to do about it."},
    "baseline": {"label": "Normal business", "story": "Reset to the merchant's normal trading baseline and run a business scan."},
}


def scenarios_for(db: Session) -> dict[str, dict[str, str]]:
    from app.domains import MERCHANT, current_domain

    return MERCHANT_DEMO_SCENARIOS if current_domain(db) == MERCHANT else DEMO_SCENARIOS


def run_demo(db: Session, scenario: str) -> dict[str, Any]:
    from app.domains import MERCHANT, current_domain

    if current_domain(db) == MERCHANT:
        return _run_merchant_demo(db, scenario)
    if scenario not in DEMO_SCENARIOS:
        raise ValueError(f"unknown demo scenario '{scenario}'")
    engine = SimulationEngine(db)
    engine.reset()
    sim = engine.run(scenario) if scenario in SCENARIOS else None
    det = DetectionEngine(db).run()
    opp = OpportunityEngine(db).run()
    featured = _featured(db, scenario, sim)
    log_event(log, "demo.run", scenario=scenario, incidents=det.incidents_created, risks=det.risks_created, opportunities=opp.opportunities_created, featured=featured)
    return {
        "scenario": scenario, "label": DEMO_SCENARIOS[scenario]["label"], "story": DEMO_SCENARIOS[scenario]["story"],
        "simulation_summary": (sim.effects or {}).get("summary") if sim else "Business data reset to the deterministic baseline.",
        "detected": {"incidents": det.incidents_created, "risks": det.risks_created, "opportunities": opp.opportunities_created},
        "featured": featured,
    }


def _featured(db: Session, scenario: str, sim) -> dict[str, str] | None:
    if scenario == "bearing_failure":
        inc = db.scalar(select(Incident).where(Incident.incident_type == "MACHINE_DEGRADATION"))
        return {"kind": "incident", "id": str(inc.id), "title": inc.title} if inc else None
    if scenario == "supplier_delay":
        r = db.scalar(select(BusinessRisk).where(BusinessRisk.risk_type == "SUPPLIER_DELAY"))
        return {"kind": "risk", "id": str(r.id), "title": r.title} if r else None
    if scenario == "demand_spike" and sim is not None:
        params = sim.parameters or {}
        q = select(Opportunity).where(Opportunity.opportunity_type == "DEMAND_OPPORTUNITY", Opportunity.region == params.get("region"))
        if params.get("product_id"):
            q = q.where(Opportunity.product_id == db.scalar(select(Product.id).where(Product.id == params["product_id"])))
        o = db.scalar(q)
        return {"kind": "opportunity", "id": str(o.id), "title": o.title} if o else None
    return None


def _run_merchant_demo(db: Session, scenario: str) -> dict[str, Any]:
    """Regenerate the business with the scenario's events, then run the real detection engines."""
    from app.domains.merchant.scenarios import build_scenario
    from app.domains.merchant.simulation import regenerate
    from app.models import SystemState

    if scenario not in MERCHANT_DEMO_SCENARIOS:
        raise ValueError(f"unknown demo scenario '{scenario}'")
    run = regenerate(db, scenario=None if scenario == "baseline" else scenario)
    det = DetectionEngine(db).run()
    opp = OpportunityEngine(db).run()
    roles = (db.get(SystemState, 1).profile or {}).get("roles", {})
    spec = build_scenario(scenario, roles) if scenario != "baseline" else None
    featured = _merchant_featured(db, spec, roles) if spec else None
    log_event(log, "demo.run", scenario=scenario, risks=det.risks_created, opportunities=opp.opportunities_created, featured=featured)
    return {
        "scenario": scenario, "label": MERCHANT_DEMO_SCENARIOS[scenario]["label"], "story": MERCHANT_DEMO_SCENARIOS[scenario]["story"],
        "simulation_summary": (run.effects or {}).get("summary"),
        "detected": {"incidents": 0, "risks": det.risks_created, "opportunities": opp.opportunities_created},
        "featured": featured,
    }


def _merchant_featured(db: Session, spec, roles: dict[str, Any]) -> dict[str, str] | None:
    """The finding the scenario was designed to surface (by type and by the product / supplier / zone it targeted)."""
    if spec.finding_kind == "opportunity":
        rows = [o for o in db.scalars(select(Opportunity).where(Opportunity.opportunity_type == spec.finding_type)).all()]
        pick = next((o for o in rows if (o.evidence or {}).get("category") and roles.get("opportunity_zone") and (o.region or "").startswith(roles["opportunity_zone"])), rows[0] if rows else None)
        return {"kind": "opportunity", "id": str(pick.id), "title": pick.title} if pick else None
    rows = [r for r in db.scalars(select(BusinessRisk).where(BusinessRisk.risk_type == spec.finding_type)).all()]
    key = {"STOCKOUT_RISK": roles.get("stockout_sku"), "MARGIN_DECLINE": roles.get("margin_sku"), "EXCESS_INVENTORY": roles.get("excess_sku")}.get(spec.finding_type)
    if key:
        p = db.scalar(select(Product).where(Product.sku == key))
        rows = [r for r in rows if p and r.entity_id == p.id] or rows
    pick = sorted(rows, key=lambda r: -float((r.estimated_impact or {}).get("value") or 0))[0] if rows else None
    return {"kind": "risk", "id": str(pick.id), "title": pick.title} if pick else None
