"""Impact engine formulas are deterministic, configurable and auditable."""
from __future__ import annotations

from app.impact import ImpactConfig, ImpactEngine


def test_core_formulas():
    e = ImpactEngine()
    assert e.production_loss(100, 25).value == 2500
    assert e.revenue_impact(1000, 800).value == 200
    r = e.inventory_risk(500, 120, unit_value=10)
    assert r.components["shortfall_units"] == 380 and r.value == 3800
    assert e.inventory_risk(100, 150).value == 0
    assert e.downtime_impact(4, 10_000).value == 40_000
    assert e.downtime_impact(2).value == 2 * ImpactConfig().default_hourly_loss


def test_results_are_auditable_and_synthetic():
    d = ImpactEngine().production_loss(10, 5).as_dict()
    assert d["formula"].startswith("production_loss") and d["inputs"] == {"lost_units": 10, "unit_margin": 5}
    assert d["is_estimate"] is True and d["basis"] == "SYNTHETIC" and d["currency"] == "INR"


def test_config_is_respected():
    e = ImpactEngine(ImpactConfig(currency="USD", default_hourly_loss=1.0, max_hourly_loss=5.0, late_delivery_penalty_ratio=0.5))
    assert e.downtime_impact(3).unit == "USD" and e.downtime_impact(3).value == 3
    assert e.downtime_impact(1, 1_000_000).value == 5.0  # capped
    assert e.late_delivery_impact(100).value == 50


def test_composite_formulas():
    e = ImpactEngine()
    deg = e.machine_degradation_impact(lost_units=100, unit_margin=10, extra_defects=10, unit_cost=20, projected_downtime_hours=2, hourly_loss=100)
    assert deg.value == 1000 + 10 * 20 * 1.15 + 200
    assert set(deg.components) == {"production_loss", "defect_cost", "projected_downtime_impact"}
    pricing = e.pricing_opportunity_value(monthly_units=100, current_price=100, unit_cost=70, target_margin=0.4)
    assert pricing.value > 0 and pricing.inputs["target_price"] > 100
    excess = e.excess_inventory_value(100, 10, 1)
    assert excess.value == 100 * 0.6 * 10 + 100
    assert e.opportunity_value(-5, 10).value == 0
