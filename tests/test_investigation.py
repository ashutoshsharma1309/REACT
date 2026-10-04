"""Phase 2: investigation engine, hypotheses, evidence, scoring, root cause, impact, report, lifecycle."""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.detection import DetectionEngine
from app.investigation import InvestigationEngine
from app.investigation import tools as T
from app.investigation.engine import InvestigationError
from app.investigation.evidence import build_evidence, support_label
from app.investigation.registry import REGISTRY, Condition, HypothesisSpec, evaluate, generate_hypotheses
from app.investigation.signals import SignalSet
from app.models import BusinessRisk, EvidenceItem, Hypothesis, Incident, Investigation, InvestigationTraceEntry, Opportunity, Order, SensorReading
from app.models.enums import InvestigationStatus
from app.opportunities import OpportunityEngine
from app.simulation.engine import SimulationEngine


# ---- helpers ------------------------------------------------------------------------------------
def incident(db, type_):
    return db.scalars(select(Incident).where(Incident.incident_type == type_)).first()


def risk(db, type_):
    return db.scalars(select(BusinessRisk).where(BusinessRisk.risk_type == type_).order_by(BusinessRisk.severity)).first()


def raw_checksum(db):
    return (db.scalar(select(func.sum(SensorReading.value))), db.scalar(select(func.count()).select_from(Order)), db.scalar(select(func.sum(Order.total_revenue))))


def ranked(inv):
    return sorted(inv.hypotheses, key=lambda h: h.rank)


# ---- 1. bearing failure -------------------------------------------------------------------------
def test_bearing_failure_investigation(db):
    SimulationEngine(db).run("bearing_failure")
    DetectionEngine(db).run()
    inc = incident(db, "MACHINE_DEGRADATION")
    before = raw_checksum(db)
    inv = InvestigationEngine(db).create_and_run(incident_id=inc.id)
    assert inv.status == InvestigationStatus.COMPLETED
    hyps = ranked(inv)
    assert len(hyps) >= 3
    assert hyps[0].hypothesis_type == "BEARING_DEGRADATION"
    assert hyps[0].evidence_support_score >= 0.6
    by_type = {h.hypothesis_type: h for h in hyps}
    assert by_type["SENSOR_FAILURE"].evidence_support_score < hyps[0].evidence_support_score
    assert by_type["POWER_INSTABILITY"].evidence_support_score < hyps[0].evidence_support_score
    assert inv.conclusion["root_cause"] == "BEARING_DEGRADATION" and inv.conclusion["determined"] is True
    assert inv.conclusion["supporting_evidence"] and "probability" not in inv.conclusion["support_label"]
    # raw data untouched, incident moved to INVESTIGATING
    assert raw_checksum(db) == before
    db.refresh(inc)
    assert inc.status.value == "INVESTIGATING"


def test_evidence_is_traceable_to_records(db):
    SimulationEngine(db).run("bearing_failure")
    DetectionEngine(db).run()
    inc = incident(db, "MACHINE_DEGRADATION")
    inv = InvestigationEngine(db).create_and_run(incident_id=inc.id)
    items = db.scalars(select(EvidenceItem).where(EvidenceItem.investigation_id == inv.id)).all()
    assert items
    for e in items:
        assert e.claim and e.source_type and 0 <= e.evidence_support_score <= 1 and 0 <= e.relevance <= 1
        assert set(e.score_breakdown) == {"relevance", "recency", "source_reliability", "correlation", "consistency"}
        assert e.supports != e.contradicts
    vib = next(e for e in items if e.signal == "vibration_change_pct" and e.supports and e.hypothesis.hypothesis_type == "BEARING_DEGRADATION")
    assert vib.source_type == "sensor_reading" and vib.source_ids
    reading = db.get(SensorReading, int(vib.source_ids[0]))
    assert reading is not None and str(reading.sensor.machine_id) == str(inc.machine_id)
    # evidence graph walks subject -> signal -> evidence -> hypothesis -> conclusion
    g = inv.graph
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"incident", "signal", "evidence", "hypothesis", "conclusion", "record"} <= kinds
    assert any(e["type"] == "concludes" for e in g["edges"]) and g["counts"]["records"] > 0


# ---- 2. sensor failure --------------------------------------------------------------------------
def test_sensor_failure_investigation(db):
    SimulationEngine(db).run("sensor_failure", {"mode": "stuck", "hours": 36})
    DetectionEngine(db).run()
    inc = incident(db, "SENSOR_FAULT")
    inv = InvestigationEngine(db).create_and_run(incident_id=inc.id)
    hyps = ranked(inv)
    assert hyps[0].hypothesis_type == "SENSOR_FAILURE" and hyps[0].evidence_support_score >= 0.6
    assert {h.hypothesis_type: h for h in hyps}["BEARING_DEGRADATION"].evidence_support_score < 0.4
    assert inv.impact["estimated_total"] is not None


# ---- 3. power instability -----------------------------------------------------------------------
def test_power_instability_investigation(db):
    sim = SimulationEngine(db).run("power_instability")
    assert sim.effects["machines_affected"] >= 3
    DetectionEngine(db).run()
    inc = incident(db, "ABNORMAL_POWER")
    assert inc is not None, "power instability should raise ABNORMAL_POWER incidents"
    inv = InvestigationEngine(db).create_and_run(incident_id=inc.id)
    hyps = ranked(inv)
    assert hyps[0].hypothesis_type == "POWER_INSTABILITY"
    by_type = {h.hypothesis_type: h for h in hyps}
    assert by_type["BEARING_DEGRADATION"].evidence_support_score < hyps[0].evidence_support_score
    assert by_type["SENSOR_FAILURE"].evidence_support_score < hyps[0].evidence_support_score
    assert any(e["signal"] == "simultaneous_power_anomalies" for e in inv.conclusion["supporting_evidence"])


# ---- 4. supplier delay --------------------------------------------------------------------------
def test_supplier_delay_investigation(db):
    SimulationEngine(db).run("supplier_delay")
    DetectionEngine(db).run()
    r = risk(db, "SUPPLIER_DELAY")
    inv = InvestigationEngine(db).create_and_run(risk_id=r.id)
    hyps = ranked(inv)
    assert hyps[0].hypothesis_type in ("SUPPLIER_RELIABILITY_ISSUE", "SINGLE_SOURCE_EXPOSURE")
    types = {h.hypothesis_type for h in hyps}
    assert {"SUPPLIER_RELIABILITY_ISSUE", "SINGLE_SOURCE_EXPOSURE", "REORDER_POLICY_GAP"} <= types
    assert inv.conclusion["kind"] == "risk_driver" and inv.impact["estimated_total"] > 0
    assert any(e["source_type"] == "supplier_batch" for e in inv.conclusion["supporting_evidence"])


# ---- 5. demand spike (opportunity) ----------------------------------------------------------------
def test_demand_spike_opportunity_investigation(db):
    SimulationEngine(db).run("demand_spike")
    OpportunityEngine(db).run()
    opp = next(o for o in db.scalars(select(Opportunity).where(Opportunity.opportunity_type == "DEMAND_OPPORTUNITY", Opportunity.region == "South India")).all() if "Industrial Valve X" in o.title)
    assert opp is not None
    inv = InvestigationEngine(db).create_and_run(opportunity_id=opp.id)
    hyps = ranked(inv)
    types = [h.hypothesis_type for h in hyps]
    assert {"SUSTAINED_REGIONAL_DEMAND", "TEMPORARY_CUSTOMER_SPIKE", "COMPETITOR_SUPPLY_SHORTAGE", "SEASONAL_DEMAND"} <= set(types)
    assert hyps[0].hypothesis_type in ("SUSTAINED_REGIONAL_DEMAND", "SEGMENT_EXPANSION", "MARKET_CATEGORY_GROWTH")
    by_type = {h.hypothesis_type: h for h in hyps}
    assert by_type["TEMPORARY_CUSTOMER_SPIKE"].evidence_support_score < hyps[0].evidence_support_score
    assert inv.conclusion["kind"] == "opportunity_explanation"
    imp = inv.impact
    assert imp["potential_additional_demand"]["units_per_day"] > 0
    assert imp["estimated_revenue_opportunity"]["value"] > 0 and imp["estimated_revenue_opportunity"]["basis"] == "SYNTHETIC"
    assert "operational_constraints" in imp and "available_production_capacity" in imp and "inventory_requirements" in imp
    assert "Potential" in imp["wording"]
    assert inv.report["title"] == "REACT OPPORTUNITY INVESTIGATION" and "Potential impact" in inv.report["text"]


# ---- 6. demand drop -----------------------------------------------------------------------------
def test_demand_drop_investigation(db):
    SimulationEngine(db).run("demand_drop", {"factor": 0.4, "days": 28, "discount": 0.12})
    DetectionEngine(db).run()
    r = risk(db, "REVENUE_DECLINE")
    assert r is not None
    inv = InvestigationEngine(db).create_and_run(risk_id=r.id)
    hyps = ranked(inv)
    assert hyps[0].hypothesis_type in ("DEMAND_CONTRACTION", "PRICING_PRESSURE")
    assert {"DEMAND_CONTRACTION", "PRICING_PRESSURE", "CUSTOMER_CHURN", "SUPPLY_CONSTRAINED_SALES"} <= {h.hypothesis_type for h in hyps}
    assert {h.hypothesis_type: h for h in hyps}["SUPPLY_CONSTRAINED_SALES"].evidence_support_score < hyps[0].evidence_support_score


# ---- 7. inventory shortage ------------------------------------------------------------------------
def test_inventory_shortage_investigation(db):
    SimulationEngine(db).run("inventory_shortage", {"level": 0.2})
    DetectionEngine(db).run()
    r = next(x for x in db.scalars(select(BusinessRisk).where(BusinessRisk.risk_type == "INVENTORY_SHORTAGE")).all() if "Industrial Valve X" in x.title)
    inv = InvestigationEngine(db).create_and_run(risk_id=r.id)
    assert inv.status == InvestigationStatus.COMPLETED and len(inv.hypotheses) >= 2
    assert {h.hypothesis_type for h in inv.hypotheses} & {"DEMAND_SURGE_CONSUMPTION", "PRODUCTION_SHORTFALL", "REORDER_POLICY_GAP"}
    assert inv.conclusion["selected_hypothesis"] in {h.hypothesis_type for h in inv.hypotheses}
    assert inv.impact["delivery_impact"]["inputs"]["orders_at_risk"] >= 0


# ---- 8/9. hypothesis generation -----------------------------------------------------------------
def _signals(kind, subject_type, **values):
    S = SignalSet(kind, subject_type, {})
    for k, v in values.items():
        S.add(k, v, "sensor_reading", ["1"], None, None, datetime(2026, 9, 25, 7), f"{k}={v}")
    return S


def test_hypothesis_generation_is_deterministic_and_ranked():
    S = _signals("incident", "MACHINE_DEGRADATION", vibration_change_pct=80, temperature_change_pct=20, power_change_pct=12, output_change_pct=-20, sensor_fault_count=0, days_since_maintenance=40, bearing_condition_score=0.2, line_utilization_recent=0.7, poor_quality_batches=0, component_stock_below_safety=0, pressure_change_pct=0)
    a = generate_hypotheses(S)
    b = generate_hypotheses(S)
    assert [c.spec.type for c in a] == [c.spec.type for c in b]
    assert a[0].spec.type == "BEARING_DEGRADATION" and a[0].rank == 1
    assert abs(sum(c.initial_confidence for c in a) - 1.0) < 1e-3  # values are rounded to 4 dp
    assert all(c.initial_confidence > 0 for c in a)  # alternatives are kept with a floor
    assert a[0].triggered_signals and a[0].triggered_signals[0]["signal"] == "vibration_change_pct"


def test_registry_is_configurable():
    custom = [HypothesisSpec("FROST", "Frost", "desc", kinds=("incident",), requires_any=("temperature_change_pct",), triggers=(Condition("temperature_change_pct", "lt", -30, 2, "cold"),), supporting=(Condition("temperature_change_pct", "lt", -30, 2, "cold"),))]
    S = _signals("incident", "ABNORMAL_TEMPERATURE", temperature_change_pct=-50)
    cands = generate_hypotheses(S, custom)
    assert [c.spec.type for c in cands] == ["FROST"] and cands[0].initial_confidence == 1.0
    assert all(s.type for s in REGISTRY) and len({s.type for s in REGISTRY}) == len(REGISTRY)
    with pytest.raises(ValueError):
        Condition("x", "between", 1)


def test_condition_evaluation_strength():
    S = _signals("incident", "X", v=20, flag=True)
    r = evaluate(Condition("v", "gt", 10), S)
    assert r.satisfied and r.strength == 1.0  # 2x the threshold margin
    r2 = evaluate(Condition("v", "gt", 19), S)
    assert r2.satisfied and 0.5 < r2.strength < 0.6
    assert evaluate(Condition("v", "lt", 10), S).satisfied is False
    assert evaluate(Condition("missing", "gt", 1), S).satisfied is None
    assert evaluate(Condition("flag", "true"), S).satisfied and not evaluate(Condition("flag", "false"), S).satisfied


# ---- 10/11. evidence retrieval + scoring -------------------------------------------------------------
def test_evidence_scoring_bounds_and_formula():
    now = datetime(2026, 9, 25, 7)
    strong = _signals("incident", "MACHINE_DEGRADATION", vibration_change_pct=100, vibration_above_warning=True, temperature_change_pct=30, power_change_pct=15, rpm_change_pct=-4, output_change_pct=-25, defect_ratio=2.5, bearing_condition_score=0.15, bearing_failure_probability=0.7, maintenance_overdue_days=10, historical_bearing_failures=1, other_physical_signals_normal=False, readings_discontinuous=False, implausible_readings=False, simultaneous_power_anomalies=False, sensor_fault_count=0)
    cand = next(c for c in generate_hypotheses(strong) if c.spec.type == "BEARING_DEGRADATION")
    drafts, summary = build_evidence(cand, strong, now)
    assert summary["evidence_support_score"] >= 0.9 and summary["contradicting_count"] == 0
    assert all(0 <= d.score <= 1 for d in drafts) and all(d.supports for d in drafts)
    assert summary["formula"].startswith("ESS")
    weak = _signals("incident", "MACHINE_DEGRADATION", vibration_change_pct=1, temperature_change_pct=0, power_change_pct=0, output_change_pct=0, defect_ratio=1.0, bearing_condition_score=0.9, other_physical_signals_normal=True, sensor_fault_count=0)
    cand_w = next(c for c in generate_hypotheses(weak) if c.spec.type == "BEARING_DEGRADATION")
    drafts_w, summary_w = build_evidence(cand_w, weak, now)
    assert summary_w["evidence_support_score"] <= 0.15 and summary_w["supporting_count"] == 0
    assert any(d.contradicts and d.claim.startswith("Condition not met") for d in drafts_w)
    assert support_label(0.95) == "strongly supported" and support_label(0.1) == "insufficient support"


def test_evidence_scoring_prefers_reliable_recent_sources():
    now = datetime(2026, 9, 25, 7)
    S = SignalSet("opportunity", "DEMAND_OPPORTUNITY", {})
    S.add("order_growth_pct", 60, "order", ["o1"], "%", 100, now, "orders +60%")
    S.add("market_product_growth_pct", 30, "market_signal", ["m1"], "%", None, datetime(2026, 7, 1), "market +30%")
    cand = next(c for c in generate_hypotheses(S) if c.spec.type == "SUSTAINED_REGIONAL_DEMAND")
    drafts, _ = build_evidence(cand, S, now)
    by_signal = {d.signal: d for d in drafts if d.supports}
    assert by_signal["order_growth_pct"].breakdown["source_reliability"] > by_signal["market_product_growth_pct"].breakdown["source_reliability"]
    assert by_signal["order_growth_pct"].breakdown["recency"] > by_signal["market_product_growth_pct"].breakdown["recency"]


# ---- 12/13. root cause selection & opportunity conclusion -------------------------------------------
def test_root_cause_selection_requires_margin(db):
    from app.investigation.analysis import conclude

    scored = [{"type": "A", "label": "A", "evidence_support_score": 0.7, "next_step": "x", "rank": 1}, {"type": "B", "label": "B", "evidence_support_score": 0.65, "next_step": "y", "rank": 2}]
    c = conclude("incident", scored, {"A": [], "B": []})
    assert c["determined"] is False and "split" in c["statement"] and c["root_cause"] == "A"
    scored[1]["evidence_support_score"] = 0.2
    c = conclude("incident", scored, {"A": [], "B": []})
    assert c["determined"] is True and "supports" in c["statement"] and "probability" in c["note"]
    assert conclude("opportunity", [], {})["determined"] is False


# ---- 14. impact ----------------------------------------------------------------------------------------
def test_incident_impact_structure(db):
    SimulationEngine(db).run("bearing_failure")
    DetectionEngine(db).run()
    inv = InvestigationEngine(db).create_and_run(incident_id=incident(db, "MACHINE_DEGRADATION").id)
    imp = inv.impact
    for key in ("estimated_production_loss", "estimated_revenue_loss", "downtime_cost", "projected_downtime_if_unaddressed", "inventory_impact", "delivery_impact", "customer_impact"):
        assert key in imp and imp[key]["is_estimate"] and imp[key]["basis"] == "SYNTHETIC"
    assert imp["estimated_production_loss"]["value"] > 0 and imp["estimated_total"] > 0
    assert imp["projected_downtime_if_unaddressed"]["estimated_downtime_minutes"] > 0
    assert "Estimated impact" in imp["wording"]
    assert "Estimated production loss" in inv.report["text"] and "Recommended next investigation" in inv.report["text"]


# ---- 15. lifecycle ----------------------------------------------------------------------------------------
def test_investigation_lifecycle_and_trace(db, monkeypatch):
    SimulationEngine(db).run("bearing_failure")
    DetectionEngine(db).run()
    inc = incident(db, "MACHINE_DEGRADATION")
    eng = InvestigationEngine(db)
    inv = eng.create(incident_id=inc.id)
    assert inv.status == InvestigationStatus.CREATED and inv.started_at is None
    assert [t.step for t in db.scalars(select(InvestigationTraceEntry).where(InvestigationTraceEntry.investigation_id == inv.id)).all()] == ["created"]
    inv = eng.run(inv.id)
    assert inv.status == InvestigationStatus.COMPLETED and inv.started_at and inv.completed_at and inv.summary
    steps = [t.step for t in inv.trace_entries]
    for s in ("created", "started", "tool", "signals", "hypotheses", "evidence", "scoring", "conclusion", "impact", "report", "completed"):
        assert s in steps, s
    assert steps == sorted(steps, key=lambda s: steps.index(s)) and [t.sequence for t in inv.trace_entries] == list(range(1, len(steps) + 1))
    # re-run replaces results rather than duplicating them
    n_h = db.scalar(select(func.count()).select_from(Hypothesis).where(Hypothesis.investigation_id == inv.id))
    inv = eng.run(inv.id)
    assert db.scalar(select(func.count()).select_from(Hypothesis).where(Hypothesis.investigation_id == inv.id)) == n_h
    assert "restarted" in [t.step for t in inv.trace_entries]
    # failure path
    import app.investigation.engine as engine_mod

    def boom(*a, **k):
        raise RuntimeError("tool outage")

    monkeypatch.setattr(engine_mod, "collect_signals", boom)
    inv2 = eng.create(incident_id=inc.id)
    with pytest.raises(RuntimeError):
        eng.run(inv2.id)
    db.refresh(inv2)
    assert inv2.status == InvestigationStatus.FAILED and "tool outage" in inv2.error
    assert inv2.trace_entries[-1].step == "failed"


def test_create_validation(db):
    eng = InvestigationEngine(db)
    with pytest.raises(InvestigationError):
        eng.create()
    with pytest.raises(InvestigationError):
        eng.create(incident_id=uuid.uuid4())
    with pytest.raises(InvestigationError):
        eng.run(uuid.uuid4())


# ---- tool layer ---------------------------------------------------------------------------------------------
def test_tools_validate_and_search(db):
    with pytest.raises(T.ToolError) as e:
        T.run_tool("get_machine_readings", db, machine_id="not-a-uuid")
    assert e.value.code == "INVALID_PARAM"
    with pytest.raises(T.ToolError) as e:
        T.run_tool("get_machine_readings", db, machine_id=uuid.uuid4())
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(T.ToolError) as e:
        T.run_tool("nope", db)
    assert e.value.code == "UNKNOWN_TOOL"
    with pytest.raises(T.ToolError):
        T.run_tool("get_supplier_history", db)
    with pytest.raises(T.ToolError):
        T.run_tool("get_production_metrics", db, production_line_id=uuid.uuid4(), days=999)
    res = T.run_tool("search_business_records", db, query="Valve X")
    assert res["count"] >= 1 and res["results"][0]["entity_type"] == "product"
    assert T.run_tool("calculate_production_loss", None, lost_units=10, unit_margin=5)["value"] == 50
    assert T.run_tool("calculate_opportunity_value", None, incremental_units=100, unit_margin=2)["value"] == 200
    m = T.run_tool("search_business_records", db, query="CNC", entity_types=["machine"])["results"][0]
    r = T.run_tool("get_machine_readings", db, machine_id=m["id"], hours=24)
    assert r["count"] > 0 and r["sensors"] and "reading_ids_recent" in r["sensors"][0]
    hist = T.run_tool("get_maintenance_history", db, machine_id=m["id"])
    assert "days_since_preventive" in hist["summary"] and hist["components"]
    assert T.run_tool("get_customer_orders", db, days=28)["recent"]["orders"] > 0
    assert T.run_tool("get_market_signals", db, category="Industrial Valves")["synthetic"] is True
    assert len(T.describe_tools()) >= 13 and all("params" in t for t in T.describe_tools())
