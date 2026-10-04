"""Merchant investigation, decision, approval, simulated action and evidence pack: the full REACT loop per scenario."""
from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import func, select

from app.agents.provider import MockLLMProvider
from app.decisions.engine import DecisionEngine
from app.domains.merchant.registry import MERCHANT_HYPOTHESES
from app.investigation.engine import InvestigationEngine
from app.models import ActionExecution, EvidenceItem, Inventory, Product, PurchaseOrder
from tests.merchant.helpers import featured, run_scenario, subject_kw

EXPECTED = {
    "stockout_crisis": ({"DEMAND_SURGE", "SUPPLIER_DELAY_SHORTAGE"}, "REORDER_PRODUCT"),
    "demand_opportunity": ({"REGIONAL_DEMAND_GROWTH"}, "INCREASE_STOCK"),
    "supplier_delay": ({"SUPPLIER_RELIABILITY_DETERIORATED"}, "REVIEW_SUPPLIER"),
    "margin_leakage": ({"PURCHASE_COST_INCREASE"}, "REVIEW_PRICING"),
    "excess_inventory": ({"DEMAND_FADED", "OVER_PURCHASE"}, "PROMOTE_PRODUCT"),
}


def test_registry_has_28_distinct_merchant_hypotheses():
    types = [h.type for h in MERCHANT_HYPOTHESES]
    assert len(types) == 28 and len(set(types)) == 28
    assert all(h.supporting and h.contradicting and h.next_step for h in MERCHANT_HYPOTHESES)


@pytest.mark.parametrize("scenario", list(EXPECTED))
def test_investigation_explains_the_scenario(mdb, scenario):
    run_scenario(mdb, scenario)
    f = featured(mdb, scenario)
    inv = InvestigationEngine(mdb).create_and_run(**subject_kw(f))
    c = inv.conclusion
    leading, _ = EXPECTED[scenario]
    assert inv.status.value == "COMPLETED" and c["determined"] is True and c["selected_hypothesis"] in leading
    assert 0.8 <= c["evidence_support_score"] <= 1.0 and c["support_label"] == "strongly supported"
    assert "probability" not in c["statement"].lower() and "confidence" not in c["statement"].lower()
    # every evidence item points at stored records and carries a deterministic score
    items = mdb.scalars(select(EvidenceItem).where(EvidenceItem.investigation_id == inv.id)).all()
    assert items and all(e.source_type and e.claim and 0 <= e.evidence_support_score <= 1 for e in items)
    supporting = [e for e in items if e.supports]
    assert supporting and all(e.source_ids for e in supporting)
    # impact: total == sum of the components it says it includes; all from the Impact Engine
    imp = inv.impact
    assert imp["domain"] == "merchant" and imp["estimated_total"] > 0
    assert abs(sum(imp[k]["value"] for k in imp["total_includes"]) - imp["estimated_total"]) < 0.02
    assert all(imp[k]["basis"] == "SYNTHETIC" and imp[k]["is_estimate"] for k in imp["total_includes"])
    # the Why? summary comes from the backend
    assert 2 <= len(inv.report["key_signals"]) <= 5 and all(k["display"] and k["claim"] and k["record_count"] >= 0 for k in inv.report["key_signals"])


@pytest.mark.parametrize("scenario", list(EXPECTED))
def test_decision_approval_simulated_action_and_evidence_pack(mclient, mdb, scenario):
    run_scenario(mdb, scenario)
    f = featured(mdb, scenario)
    kind = "opportunity" if "opportunity_id" in subject_kw(f) else "risk"
    inv_before = {i.id: i.quantity_available for i in mdb.scalars(select(Inventory)).all()}
    prices_before = {p.id: (float(p.selling_price), float(p.unit_cost)) for p in mdb.scalars(select(Product)).all()}
    pos_before = mdb.scalar(select(func.count(PurchaseOrder.id)))
    r = mclient.post("/react/run", json={f"{kind}_id": str(f.id)})
    assert r.status_code == 201, r.text
    d = r.json()
    _, action = EXPECTED[scenario]
    assert d["status"] == "AWAITING_APPROVAL" and d["action_type"] == action and d["requires_human_approval"] is True
    assert d["expected_impact"]["estimated_total"] > 0 and d["expected_impact"]["source"].startswith("app/impact/engine.py")
    params = d["actions"][0]["parameters"]
    assert params["source"].startswith("deterministic action parameters")
    if action == "REORDER_PRODUCT":
        b = params["basis"]
        import math

        assert params["quantity"] == max(0, math.ceil(b["units_per_day"] * (b["lead_days"] + b["cover_days"] + 3) - b["on_hand"] - b["on_order"]))
        assert params["estimated_cost"] == round(params["quantity"] * params["unit_cost"], 2)
    if action == "REVIEW_PRICING":
        assert params["suggested_price"] > params["current_price"] - 1
    # nothing happens before a human approves
    assert mclient.post(f"/decisions/{d['decision_id']}/execute").status_code == 409
    ok = mclient.post(f"/decisions/{d['decision_id']}/approve", json={"reviewed_by": "Store owner", "reason": "looks right"}).json()
    assert ok["status"] == "EXECUTED"
    res = ok["actions"][0]["result"]
    assert res["simulated"] is True and res["business_records_modified"] is False and res["task_id"].startswith("SIM-")
    if action in ("REORDER_PRODUCT", "INCREASE_STOCK"):
        assert res["task_id"].startswith("SIM-PO-")
    # business records untouched by the simulated action
    assert {i.id: i.quantity_available for i in mdb.scalars(select(Inventory)).all()} == inv_before
    assert {p.id: (float(p.selling_price), float(p.unit_cost)) for p in mdb.scalars(select(Product)).all()} == prices_before
    assert mdb.scalar(select(func.count(PurchaseOrder.id))) == pos_before
    # audit: verifiable evidence pack with provenance and the visible trace
    art = mclient.get(f"/decisions/{d['decision_id']}/artifacts").json()[0]
    dl = mclient.get(art["download_path"])
    assert dl.status_code == 200 and hashlib.sha256(dl.content).hexdigest() == art["sha256"]
    pack = json.loads(dl.content)
    assert pack["provenance"]["data"] == "SYNTHETIC" and pack["provenance"]["seed"] == 42 and pack["evidence"] and pack["agent_trace"]
    assert pack["approvals"][0]["reviewed_by"] == "Store owner" and pack["actions"][0]["simulated"] is True


def test_rejection_takes_no_action(mclient, mdb):
    run_scenario(mdb, "margin_leakage")
    f = featured(mdb, "margin_leakage")
    d = mclient.post("/react/run", json={"risk_id": str(f.id)}).json()
    assert mclient.post(f"/decisions/{d['decision_id']}/reject", json={"reviewed_by": "Owner"}).status_code == 422
    rej = mclient.post(f"/decisions/{d['decision_id']}/reject", json={"reviewed_by": "Owner", "reason": "supplier will refund the difference"}).json()
    assert rej["status"] == "REJECTED" and rej["actions"][0]["status"] == "REJECTED" and rej["actions"][0]["result"] == {}
    assert mclient.get(f"/decisions/{d['decision_id']}/artifacts").json()[0]["trigger"] == "decision.rejected"


def test_llm_cannot_pick_an_invalid_action_or_rescore(mdb):
    """A scripted 'LLM' that proposes a manufacturing action and a different score is rejected by deterministic validation;
    the run falls back to the rule-based recommendation and says so."""
    run_scenario(mdb, "stockout_crisis")
    f = featured(mdb, "stockout_crisis")
    from app.agents.fallback import DeterministicProvider

    det = DeterministicProvider()

    def default(ctx, schema):
        obj, _ = det.generate_structured("", [], schema, context=ctx)
        payload = obj.model_dump()
        if ctx.get("task") == "decision_recommendation":
            payload["action_type"] = "SCHEDULE_MAINTENANCE"  # not a merchant action
            payload["evidence_support_score"] = 0.42
        return payload

    mock = MockLLMProvider(default=default)
    mock.generate_with_tools = lambda system, messages, tools, context=None: det.generate_with_tools(system, messages, tools, context=context)
    d = DecisionEngine(mdb, provider=mock).create(risk_id=f.id)
    assert d.action_type == "REORDER_PRODUCT" and d.fallback_used is True
    assert abs(d.evidence_support_score - 0.42) > 0.1


def test_ai_outage_without_fallback_returns_503(mclient, mdb):
    run_scenario(mdb, "stockout_crisis")
    f = featured(mdb, "stockout_crisis")
    mclient.post("/demo/ai-outage", json={"mode": "fail"})
    r = mclient.post("/react/run", json={"risk_id": str(f.id)})
    assert r.status_code == 503 and r.json()["detail"].startswith("AI investigation temporarily unavailable")
    mclient.post("/demo/ai-outage", json={"mode": "fallback"})
    d = mclient.post("/react/run", json={"risk_id": str(f.id)}).json()
    assert d["fallback_used"] is True and d["action_type"] == "REORDER_PRODUCT"
