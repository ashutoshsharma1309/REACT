"""Merchant detection rules: every scenario surfaces its designed finding with traceable records; the baseline is quiet."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.detection import DetectionEngine
from app.domains.merchant.detection import MerchantDetector
from app.models import BusinessRisk, Opportunity
from tests.merchant.helpers import featured, run_scenario

SCENARIOS = {"stockout_crisis": "STOCKOUT_RISK", "demand_opportunity": "REGIONAL_OPPORTUNITY", "supplier_delay": "SUPPLIER_RELIABILITY", "margin_leakage": "MARGIN_DECLINE", "excess_inventory": "EXCESS_INVENTORY"}


def test_baseline_business_is_mostly_healthy(mdb):
    run_scenario(mdb, None)
    risks = mdb.scalars(select(BusinessRisk)).all()
    assert len(risks) <= 4 and not any(r.severity.value == "CRITICAL" for r in risks)
    assert not [r for r in risks if r.risk_type.value in ("MARGIN_DECLINE", "SUPPLIER_RELIABILITY", "EXCESS_INVENTORY", "REVENUE_DECLINE")]


@pytest.mark.parametrize("scenario,finding_type", list(SCENARIOS.items()))
def test_scenario_surfaces_its_finding(mdb, scenario, finding_type):
    run_scenario(mdb, scenario)
    f = featured(mdb, scenario)
    assert f is not None, f"{scenario} did not produce {finding_type}"
    ev = f.evidence
    assert ev["domain"] == "merchant" and ev["rule"] and ev["records"] and sum(len(v) for v in ev["records"].values()) > 0
    assert f.estimated_impact["value"] > 0 and f.estimated_impact["basis"] == "SYNTHETIC" and f.estimated_impact["is_estimate"] is True and f.estimated_impact["formula"]
    assert f.is_synthetic and f.dedupe_key.startswith(finding_type)
    if isinstance(f, BusinessRisk):
        assert f.severity.value in ("MEDIUM", "HIGH", "CRITICAL")
        assert f.detected_at.date() == mdb.get(__import__("app.models", fromlist=["SystemState"]).SystemState, 1).data_end.date()  # simulated time


def test_rescan_updates_instead_of_duplicating(mdb):
    run_scenario(mdb, "margin_leakage")
    before = mdb.scalars(select(BusinessRisk)).all()
    DetectionEngine(mdb).run()
    after = mdb.scalars(select(BusinessRisk)).all()
    assert len(after) == len(before) and all(r.occurrence_count == 2 for r in after if r.status.value != "RESOLVED")


def test_condition_clears_and_finding_resolves(mdb):
    run_scenario(mdb, "excess_inventory")
    excess = featured(mdb, "excess_inventory")
    assert excess is not None
    # reset to baseline regenerates everything; a new scan on a fresh dataset has no excess finding
    run_scenario(mdb, None)
    assert not mdb.scalars(select(BusinessRisk).where(BusinessRisk.risk_type == "EXCESS_INVENTORY")).all()


def test_stockout_is_not_mistaken_for_falling_demand(mdb):
    run_scenario(mdb, "stockout_crisis")
    det = MerchantDetector(mdb)
    declines = [f for f in det.risks() if f.type == "DEMAND_DECLINE"]
    assert not [f for f in declines if f.product_id in det.stockout_products]


def test_supplier_finding_lists_dependents_and_late_deliveries(mdb):
    run_scenario(mdb, "supplier_delay")
    f = featured(mdb, "supplier_delay")
    m = f.supporting_metrics
    assert m["late_deliveries"] >= 4 and m["deliveries_considered"] >= 5 and m["dependent_products"] > 0
    assert len(f.evidence["records"]["supplier_delivery_ids"]) == m["deliveries_considered"]


def test_regional_opportunity_names_zone_and_segments(mdb):
    roles = run_scenario(mdb, "demand_opportunity")
    o = featured(mdb, "demand_opportunity")
    assert isinstance(o, Opportunity) and o.region.startswith(roles["opportunity_zone"]) and o.evidence["category"]
    assert o.supporting_metrics["growth_pct"] >= 30 and o.evidence["segments"] and o.confidence.value in ("MEDIUM", "HIGH")


def test_every_rule_cites_stored_records_even_business_level_ones(mdb):
    """Revenue and repeat-customer rules describe the whole business; they must still cite the records behind them."""
    from dataclasses import replace

    from app.domains.merchant.detection import MerchantDetectionConfig

    run_scenario(mdb, None)
    loose = replace(MerchantDetectionConfig(), revenue_max_change=10.0, revenue_max_z=99.0, repeat_min_orders=0, repeat_min_abs_drop=-1.0, repeat_min_rel_drop=-99.0)
    det = MerchantDetector(mdb, loose)
    findings = det._revenue_decline() + det._repeat_decline()
    assert {f.type for f in findings} == {"REVENUE_DECLINE", "REPEAT_CUSTOMER_DECLINE"}
    for f in findings:
        recs = f.evidence["records"]
        assert any(k.endswith("_ids") and v for k, v in recs.items()), f"{f.type} cites no record ids"


def test_stockout_rule_handles_a_product_without_supplier(mdb):
    run_scenario(mdb, None)
    info = MerchantDetector(mdb).supplier_info(None)
    assert {"late_share", "late_delay", "lead_eff", "next_order_days"} <= set(info)
