"""Merchant API surface, tool allow-lists and Impact Engine formulas."""
from __future__ import annotations

import pytest

from app.agents.policies import allowed_tools, is_allowed
from app.impact import ImpactEngine
from app.investigation.tools import ToolError, run_tool, tools_for
from tests.merchant.helpers import featured, run_scenario


def test_merchant_endpoints(mclient):
    m = mclient.get("/merchant").json()
    assert m["business_name"] == "Astra Retail" and m["is_synthetic"] and m["stores"] and len(m["categories"]) == 10
    assert mclient.get("/merchant/templates").json()[0]["key"] == "electronics"
    b = mclient.get("/business-baseline").json()
    assert b["weekly_revenue"]["normal_weekly_mean"] > 0 and len(b["weekday_sales"]) == 7
    s = mclient.get("/sales?days=28").json()
    assert len(s["daily"]) == 28 and {c["channel"] for c in s["by_channel"]} == {"STORE", "ONLINE", "WHATSAPP"} and s["by_payment_method"]
    p = mclient.get("/products").json()
    assert len(p) >= 300 and {"units_28d", "revenue_28d", "stock_on_hand", "findings", "brand"} <= set(p[0])
    inv = mclient.get("/inventory").json()
    assert inv and {"store", "status", "days_of_cover", "stock_value"} <= set(inv[0]) and inv[0]["status"] in ("OUT_OF_STOCK", "LOW", "OVERSTOCKED", "HEALTHY")
    sup = mclient.get("/suppliers").json()
    assert len(sup) == 8 and all("late_of_last_6" in x for x in sup)
    o = mclient.get("/overview").json()
    assert o["domain"] == "merchant" and o["business_health"]["revenue"] > 0 and 0 <= o["business_health"]["inventory"]["healthy_pct"] <= 100 and "potential_loss_identified" in o["value"]
    scen = [x["scenario"] for x in mclient.get("/demo/scenarios").json()]
    assert scen == ["stockout_crisis", "demand_opportunity", "supplier_delay", "margin_leakage", "excess_inventory", "baseline"]
    assert mclient.get("/agents").json()["domain"] == "merchant"
    assert {a["type"] for a in mclient.get("/actions/catalogue").json()["actions"]} >= {"REORDER_PRODUCT", "REVIEW_PRICING", "PROMOTE_PRODUCT", "REVIEW_SUPPLIER"}


def test_onboarding_regenerates_the_business(mclient):
    bad = mclient.post("/merchant/setup", json={"business_name": "Mehta Mobiles", "categories": ["AUDIO"]})
    assert bad.status_code == 422
    r = mclient.post("/merchant/setup", json={"business_name": "Mehta Mobiles", "business_type": "electronics", "city": "Pune", "state": "Maharashtra", "locality": "Kothrud", "store_count": 2, "categories": ["AUDIO", "CASES", "CHARGE", "POWER", "WEAR"]})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["business_name"] == "Mehta Mobiles" and len(m["stores"]) == 2 and m["city"] == "Pune" and len(m["categories"]) == 5
    assert mclient.get("/activity").json()[0]["kind"] in ("scan", "simulation")


def test_demo_run_reports_the_featured_finding(mclient):
    d = mclient.post("/demo/run", json={"scenario": "stockout_crisis"}).json()
    assert d["featured"]["kind"] == "risk" and "Wireless Speaker X" in d["featured"]["title"]
    f = mclient.get(f"/findings/risk/{d['featured']['id']}").json()
    assert f["stage"] == "DETECTED" and f["supporting_metrics"]["units_per_day"] > 0


def test_agents_only_see_merchant_tools(mdb):
    inv_tools = allowed_tools("investigation_agent", "merchant")
    assert "get_product_sales" in inv_tools and "get_machine_readings" not in inv_tools
    assert is_allowed("investigation_agent", "calculate_stockout_loss", "merchant")
    assert not is_allowed("evidence_agent", "calculate_stockout_loss", "merchant")  # verification never computes money
    assert not is_allowed("investigation_agent", "get_machine_readings", "merchant")
    assert "get_machine_readings" not in tools_for("merchant")
    with pytest.raises(ToolError):
        run_tool("get_machine_readings", mdb, machine_id="x")


def test_tool_validation(mdb):
    with pytest.raises(ToolError) as e:
        run_tool("get_product_sales", mdb, product_id="not-a-uuid")
    assert e.value.code == "INVALID_PARAM"
    with pytest.raises(ToolError):
        run_tool("get_product_sales", mdb, product_id="00000000-0000-0000-0000-000000000000")
    with pytest.raises(ToolError):
        run_tool("get_category_performance", mdb, category="DROP TABLE products")
    with pytest.raises(ToolError):
        run_tool("get_purchase_orders", mdb)
    out = run_tool("calculate_reorder_quantity", None, units_per_day=4, lead_days=9, on_hand=6, on_order=0, cover_days=14)
    assert out["quantity"] == 4 * 26 - 6


def test_supplier_tool_reports_lateness(mdb):
    run_scenario(mdb, "supplier_delay")
    f = featured(mdb, "supplier_delay")
    out = run_tool("get_supplier_history", mdb, supplier_id=f.entity_id)
    assert out["last_6"]["late"] >= 4 and out["recent"]["late_share"] > out["normal"]["late_share"]


def test_merchant_impact_formulas():
    E = ImpactEngine()
    s = E.stockout_loss(3.5, 4, 2400, 680)
    assert s.value == 3.5 * 4 * 2400 and s.components["lost_margin"] == 3.5 * 4 * 680 and s.basis == "SYNTHETIC" and s.is_estimate
    m = E.margin_loss(1.7, 207, 145)
    assert m.value == pytest.approx(62 * 1.7 * 90) and m.inputs["horizon_days"] == 90
    c = E.inventory_capital_locked(50, 1295, 19.4)
    assert c.value == 50 * 1295 and c.components["holding_cost"] == pytest.approx(50 * 19.4 * 3)
    d = E.discount_impact(1000, 0.02, 0.10)
    assert d.value == pytest.approx(0.08 * 1000 * 90)
    o = E.demand_opportunity_value(2, 1500, 400)
    assert o.value == pytest.approx(2 * 30 * 0.7 * 1500)
    assert E.price_to_restore_margin(941, 207.4, 1099).value == 1149
    assert E.supplier_delay_exposure([]).value == 0 and E.margin_loss(1, 100, 150).value == 0
