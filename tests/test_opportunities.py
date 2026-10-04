"""Opportunity detection + opportunity creation."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import MarketSignal, Opportunity, Order, Product
from app.models.enums import Confidence, OpportunityStatus
from app.opportunities import OpportunityEngine
from app.simulation.engine import SimulationEngine


def opps(db, type_=None):
    stmt = select(Opportunity)
    if type_:
        stmt = stmt.where(Opportunity.opportunity_type == type_)
    return db.scalars(stmt).all()


def test_baseline_opportunities_are_structural_not_noise(db):
    run = OpportunityEngine(db).run()
    assert run.status.value == "COMPLETED"
    types = {o.opportunity_type.value for o in opps(db)}
    assert "PRICING_OPPORTUNITY" in types  # driven by scripted competitor price increases
    for o in opps(db):
        assert o.is_synthetic and o.status == OpportunityStatus.NEW and "synthetic" in o.evidence["synthetic_notice"].lower()


def test_demand_spike_creates_demand_opportunity(db):
    sim = SimulationEngine(db).run("demand_spike", {"multiplier": 3.0, "days": 14})
    run = OpportunityEngine(db).run()
    assert run.opportunities_created > 0
    product = db.scalar(select(Product).where(Product.sku == "AV-CX80"))
    found = [o for o in opps(db, "DEMAND_OPPORTUNITY") if o.product_id == product.id]
    assert found, [o.title for o in opps(db)]
    regional = next((o for o in found if o.region == "South India"), None)
    assert regional is not None and regional.confidence in (Confidence.HIGH, Confidence.MEDIUM)
    assert regional.signal == "demand_growth" and regional.supporting_metrics["growth_rate"] > 30
    assert regional.estimated_impact["value"] > 0 and regional.estimated_impact["basis"] == "SYNTHETIC"
    # traceability: orders and market signals referenced by the opportunity exist and match
    rec = regional.evidence["records"]
    assert rec["product_id"] == str(product.id)
    for oid in rec["order_ids_recent"][:10]:
        order = db.get(Order, uuid.UUID(oid))
        assert order is not None and order.region == "South India"
        assert any(i.product_id == product.id for i in order.items)
    assert any(db.get(MarketSignal, uuid.UUID(sid)).simulation_run_id == sim.id for sid in rec["market_signal_ids"])


def test_opportunities_dedupe_on_rerun(db):
    SimulationEngine(db).run("demand_spike")
    OpportunityEngine(db).run()
    n = len(opps(db))
    run = OpportunityEngine(db).run()
    assert len(opps(db)) == n and run.opportunities_updated == n and run.opportunities_created == 0
    assert all(o.occurrence_count == 2 for o in opps(db))


def test_demand_drop_surfaces_capacity_and_excess(db):
    SimulationEngine(db).run("demand_drop", {"factor": 0.2, "days": 40})
    OpportunityEngine(db).run()
    types = {o.opportunity_type.value for o in opps(db)}
    assert "AVAILABLE_CAPACITY" in types or "EXCESS_INVENTORY" in types


def test_underserved_region_from_competitor_signal(db):
    OpportunityEngine(db).run()
    us = opps(db, "UNDERSERVED_REGION")
    for o in us:
        assert o.region and o.evidence["competitor_signals"] or o.evidence["market_signals"]
