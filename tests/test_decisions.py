"""Phase 4: Decision Agent, decision validation, human approval, simulated action execution,
state machines, traceability and the full end-to-end workflow. No real LLM required."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.agent_runtime.limits import AgentLimits
from app.agents.fallback import DeterministicProvider
from app.agents.orchestrator import Orchestrator
from app.agents.provider import MockLLMProvider, ProviderUnavailable
from app.agents.schemas import EvidenceAssessment
from app.decisions.actions import ActionExecutionError, ActionExecutor
from app.decisions.catalogue import CATALOGUE, HYPOTHESIS_ACTIONS
from app.decisions.engine import DecisionEngine, DecisionError
from app.decisions.schemas import DecisionRecommendation
from app.decisions.state import ACTION_TRANSITIONS, DECISION_TRANSITIONS, InvalidTransition, check
from app.detection import DetectionEngine
from app.investigation.registry import REGISTRY
from app.models import ActionExecution, AgentTraceEntry, Approval, BusinessRisk, Decision, EvidenceItem, Incident, Inventory, MaintenanceRecord, Opportunity, Order, SensorReading, Supplier
from app.models.enums import ActionStatus, ApprovalStatus, DecisionReadiness, DecisionStatus, IncidentStatus, OpportunityStatus, RiskStatus
from app.opportunities import OpportunityEngine
from app.simulation.engine import SimulationEngine

DET = DeterministicProvider()


def det_default(context, schema):
    if schema is None:
        from app.agents.policies import allowed_tools

        return DET.generate_with_tools("", [], [{"name": n} for n in allowed_tools("investigation_agent")], context=context)
    return DET.generate_structured("", [], schema, context=context)[0]


def bearing_incident(db) -> Incident:
    SimulationEngine(db).run("bearing_failure")
    DetectionEngine(db).run()
    return db.scalars(select(Incident).where(Incident.incident_type == "MACHINE_DEGRADATION")).first()


def supplier_risk(db) -> BusinessRisk:
    SimulationEngine(db).run("supplier_delay")
    DetectionEngine(db).run()
    return db.scalars(select(BusinessRisk).where(BusinessRisk.risk_type == "SUPPLIER_DELAY")).first()


def valve_opportunity(db) -> Opportunity:
    SimulationEngine(db).run("demand_spike")
    OpportunityEngine(db).run()
    return next(o for o in db.scalars(select(Opportunity).where(Opportunity.region == "South India")).all() if "Industrial Valve X" in o.title)


def business_snapshot(db):
    return (db.scalar(select(func.sum(SensorReading.value))), db.scalar(select(func.count()).select_from(Order)), db.scalar(select(func.count()).select_from(MaintenanceRecord)), db.scalar(select(func.sum(Inventory.reorder_point))), db.scalar(select(func.sum(Inventory.quantity_available))), db.scalar(select(func.sum(Supplier.lead_time_days))))


# ---- catalogue ------------------------------------------------------------------------------------------
def test_catalogue_covers_every_hypothesis_and_is_consistent():
    assert 10 <= len(CATALOGUE) <= 20
    for spec in REGISTRY:
        primary, alts = HYPOTHESIS_ACTIONS[spec.type]
        assert primary in CATALOGUE and all(a in CATALOGUE for a in alts) and primary not in alts
    assert all(s.requires_human_approval for s in CATALOGUE.values())


def test_decision_schema_has_no_money_fields():
    props = DecisionRecommendation.model_json_schema()["properties"]
    assert not any(any(w in k for w in ("revenue", "cost", "value", "amount", "loss", "price", "inr")) for k in props)


# ---- decision generation -----------------------------------------------------------------------------------
def test_bearing_failure_decision_is_evidence_backed(db):
    inc = bearing_incident(db)
    d = DecisionEngine(db, provider=DET).create(incident_id=inc.id)
    assert d.status == DecisionStatus.AWAITING_APPROVAL and d.decision_readiness == DecisionReadiness.READY
    assert d.action_type == "SCHEDULE_MAINTENANCE" and d.target_type == "machine" and d.target_id == str(inc.machine_id)
    assert d.hypothesis_type == "BEARING_DEGRADATION" and d.evidence_support_score >= 0.8 and d.priority == "P1"
    # traceability: decision -> agent run -> investigation -> hypothesis -> evidence -> records
    assert d.agent_run_id and d.investigation_id and d.hypothesis_id
    items = db.scalars(select(EvidenceItem).where(EvidenceItem.id.in_([uuid.UUID(i) for i in d.evidence_ids]))).all()
    assert len(items) == len(d.evidence_ids) and all(e.investigation_id == d.investigation_id and e.supports for e in items)
    assert all(e.hypothesis_id == d.hypothesis_id for e in items) and all(e.source_ids for e in items)
    assert any(i in d.rationale for i in d.evidence_ids)
    # impact is the deterministic engine's figure, copied, not recomputed
    from app.models import Investigation

    inv = db.get(Investigation, d.investigation_id)
    assert d.expected_impact["estimated_total"] == inv.impact["estimated_total"] and d.expected_impact["source"].startswith("app/impact/engine.py")
    assert {a["action_type"] for a in d.alternatives} == {"INSPECT_MACHINE", "MONITOR_MACHINE"} and d.risks and d.assumptions
    assert d.validation["passed"] and {c["check"] for c in d.validation["checks"]} >= {"target", "reason", "evidence", "expected_impact", "proposed_action", "risks_assumptions", "human_approval_required"}
    assert d.requires_human_approval is True
    assert len(d.approvals) == 1 and d.approvals[0].status == ApprovalStatus.PENDING
    assert len(d.actions) == 1 and d.actions[0].status == ActionStatus.PROPOSED and d.actions[0].simulated is True
    db.refresh(inc)
    assert inc.status == IncidentStatus.AWAITING_APPROVAL


def test_decision_from_existing_agent_run(db):
    inc = bearing_incident(db)
    run = Orchestrator(db, provider=DET).start_and_run(incident_id=inc.id)
    d = DecisionEngine(db, provider=DET).create(agent_run_id=run.id)
    assert d.agent_run_id == run.id and d.investigation_id == run.investigation_id
    entries = db.scalars(select(AgentTraceEntry).where(AgentTraceEntry.agent_run_id == run.id)).all()
    assert any(e.agent_name == "decision_agent" and e.event_type == "result" for e in entries)
    assert any(e.event_type == "approval_requested" for e in entries)


def test_risk_and_opportunity_decisions(db):
    r = supplier_risk(db)
    d = DecisionEngine(db, provider=DET).create(risk_id=r.id)
    assert d.action_type == "REVIEW_SUPPLIER" and d.target_type == "supplier" and d.status == DecisionStatus.AWAITING_APPROVAL
    opp = valve_opportunity(db)
    d2 = DecisionEngine(db, provider=DET).create(opportunity_id=opp.id)
    assert d2.subject_type.value == "OPPORTUNITY" and d2.action_type in ("PRIORITIZE_PRODUCT", "INCREASE_PRODUCTION", "INVESTIGATE_MARKET")
    assert d2.target_type == "product" and d2.expected_impact["estimated_total"] > 0 and d2.priority in ("P1", "P2")


def test_insufficient_evidence_produces_no_action(db):
    inc = bearing_incident(db)

    def cautious(ctx, schema):
        out = det_default(ctx, schema)
        if schema is EvidenceAssessment:
            out.sufficient = False
            out.missing_evidence = ["No vibration spectrum to confirm the bearing wear pattern."]
        return out

    run = Orchestrator(db, provider=MockLLMProvider(default=cautious)).start_and_run(incident_id=inc.id)
    assert run.result_status == "INSUFFICIENT_EVIDENCE"
    d = DecisionEngine(db, provider=DET).create(agent_run_id=run.id)
    assert d.status == DecisionStatus.INSUFFICIENT_EVIDENCE and d.decision_readiness == DecisionReadiness.INSUFFICIENT_EVIDENCE
    assert d.action_type is None and d.approvals == [] and d.actions == []
    assert "vibration spectrum" in d.rationale
    with pytest.raises(InvalidTransition):
        DecisionEngine(db, provider=DET).approve(d.id, reviewed_by="x")
    db.refresh(inc)
    assert inc.status == IncidentStatus.AWAITING_DECISION


# ---- validation (the LLM cannot bypass it) -----------------------------------------------------------------
def _tamper(mutator):
    def provider_default(ctx, schema):
        out = det_default(ctx, schema)
        if schema is DecisionRecommendation and not ctx.get("previous_error"):
            mutator(out, ctx)
        return out

    return MockLLMProvider(default=provider_default)


@pytest.mark.parametrize("mutator,expected", [
    (lambda o, c: setattr(o, "action_type", "WIRE_MONEY"), "not an applicable catalogue action"),
    (lambda o, c: setattr(o, "action_type", "INCREASE_PRODUCTION"), "not an applicable catalogue action"),  # opportunity-only action on an incident
    (lambda o, c: o.evidence_ids.append(str(uuid.uuid4())), "unknown evidence ids"),
    (lambda o, c: setattr(o, "evidence_support_score", 0.99), "restate the deterministic"),
    (lambda o, c: setattr(o, "target_id", str(uuid.uuid4())), "candidate targets"),
    (lambda o, c: setattr(o, "rationale", "Because the model believes it strongly."), "cite at least one"),
    (lambda o, c: setattr(o, "requires_human_approval", False), "requires_human_approval"),
    (lambda o, c: setattr(o, "hypothesis_type", "SENSOR_FAILURE"), "leading hypothesis"),
])
def test_invalid_recommendations_are_rejected_then_corrected(db, mutator, expected):
    inc = bearing_incident(db)
    run = Orchestrator(db, provider=DET).start_and_run(incident_id=inc.id)
    d = DecisionEngine(db, provider=_tamper(mutator), limits=AgentLimits(max_retries=1)).create(agent_run_id=run.id)
    assert d.status == DecisionStatus.AWAITING_APPROVAL and d.action_type == "SCHEDULE_MAINTENANCE"  # corrected on retry
    val = db.scalars(select(AgentTraceEntry).where(AgentTraceEntry.agent_run_id == run.id, AgentTraceEntry.event_type == "validation")).all()
    assert val and expected in val[-1].error


def test_insufficient_evidence_cannot_be_overridden_by_llm(db):
    inc = bearing_incident(db)

    def cautious(ctx, schema):
        out = det_default(ctx, schema)
        if schema is EvidenceAssessment:
            out.sufficient = False
        return out

    run = Orchestrator(db, provider=MockLLMProvider(default=cautious)).start_and_run(incident_id=inc.id)
    pushy = {"decision_readiness": "READY", "action_type": "SCHEDULE_MAINTENANCE"}

    def always_pushy(ctx, schema):
        out = det_default(ctx, schema)
        if schema is DecisionRecommendation:
            out = out.model_copy(update={**pushy, "target_type": "machine", "target_id": str(inc.machine_id)})
        return out

    with pytest.raises(DecisionError):
        DecisionEngine(db, provider=MockLLMProvider(default=always_pushy), limits=AgentLimits(max_retries=1), fallback_enabled=False).create(agent_run_id=run.id)
    assert db.scalar(select(func.count()).select_from(Decision)) == 0


def test_provider_failure_falls_back_and_is_flagged(db):
    inc = bearing_incident(db)
    run = Orchestrator(db, provider=DET).start_and_run(incident_id=inc.id)
    dead = MockLLMProvider(default=lambda ctx, schema: ProviderUnavailable("down"))
    d = DecisionEngine(db, provider=dead, limits=AgentLimits(max_retries=0), fallback_enabled=True).create(agent_run_id=run.id)
    assert d.action_type == "SCHEDULE_MAINTENANCE"
    assert db.scalars(select(AgentTraceEntry).where(AgentTraceEntry.agent_run_id == run.id, AgentTraceEntry.event_type == "fallback", AgentTraceEntry.agent_name == "decision_agent")).first() is not None
    run2 = Orchestrator(db, provider=DET).start_and_run(incident_id=inc.id)
    with pytest.raises(DecisionError):
        DecisionEngine(db, provider=dead, limits=AgentLimits(max_retries=0), fallback_enabled=False).create(agent_run_id=run2.id)


def test_create_errors(db):
    eng = DecisionEngine(db, provider=DET)
    with pytest.raises(DecisionError):
        eng.create()
    with pytest.raises(DecisionError):
        eng.create(agent_run_id=uuid.uuid4())
    inc = bearing_incident(db)
    run = Orchestrator(db, provider=DET).start(incident_id=inc.id)  # CREATED, never run
    with pytest.raises(DecisionError):
        eng.create(agent_run_id=run.id)


# ---- approval, rejection, execution ---------------------------------------------------------------------------
def test_approval_executes_simulated_action_without_side_effects(db):
    inc = bearing_incident(db)
    eng = DecisionEngine(db, provider=DET)
    d = eng.create(incident_id=inc.id)
    before = business_snapshot(db)
    d = eng.approve(d.id, reviewed_by="plant.manager", reason="fits the Sunday window")
    assert d.status == DecisionStatus.EXECUTED
    approval, action = d.approvals[0], d.actions[0]
    assert approval.status == ApprovalStatus.APPROVED and approval.reviewed_by == "plant.manager" and approval.reviewed_at
    assert action.status == ActionStatus.COMPLETED and action.approval_id == approval.id and action.simulated
    assert [t["to"] for t in action.transitions] == ["PROPOSED", "APPROVED", "EXECUTING", "COMPLETED"]
    r = action.result
    assert r["task_type"] == "maintenance_work_order" and r["simulated"] is True and r["business_records_modified"] is False and r["side_effects"] == "none"
    assert r["component"]["type"] == "BEARING" and r["decision_id"] == str(d.id) and r["approval_id"] == str(approval.id) and r["evidence_ids"]
    assert business_snapshot(db) == before  # nothing in the business data changed
    assert [e.get("to_status") for e in d.timeline if e["event"] == "status_changed"] == ["AWAITING_APPROVAL", "APPROVED", "EXECUTING", "EXECUTED"]
    db.refresh(inc)
    assert inc.status == IncidentStatus.EXECUTING


def test_approve_without_execute_then_execute(db):
    r = supplier_risk(db)
    eng = DecisionEngine(db, provider=DET)
    d = eng.create(risk_id=r.id)
    d = eng.approve(d.id, reviewed_by="procurement", execute=False)
    assert d.status == DecisionStatus.APPROVED and d.actions[0].status == ActionStatus.APPROVED
    d = eng.execute(d.id, executed_by="procurement")
    assert d.status == DecisionStatus.EXECUTED and d.actions[0].result["task_type"] == "supplier_review"
    assert d.actions[0].result["supplier"]["name"] == "Helios Controls GmbH" and "alternate_sources" in d.actions[0].result
    db.refresh(r)
    assert r.status == RiskStatus.MITIGATING


def test_rejection_path(db):
    opp = valve_opportunity(db)
    eng = DecisionEngine(db, provider=DET)
    d = eng.create(opportunity_id=opp.id)
    d = eng.reject(d.id, reviewed_by="sales.director", reason="capacity reserved for another program")
    assert d.status == DecisionStatus.REJECTED and d.approvals[0].status == ApprovalStatus.REJECTED and d.actions[0].status == ActionStatus.REJECTED
    assert d.actions[0].result == {} and d.actions[0].completed_at is None
    with pytest.raises(InvalidTransition):
        eng.execute(d.id)
    with pytest.raises(InvalidTransition):
        eng.approve(d.id, reviewed_by="someone")
    db.refresh(opp)
    assert opp.status == OpportunityStatus.REVIEWED


def test_invalid_transitions_are_blocked(db):
    inc = bearing_incident(db)
    eng = DecisionEngine(db, provider=DET)
    d = eng.create(incident_id=inc.id)
    with pytest.raises(InvalidTransition):
        eng.execute(d.id)  # AWAITING_APPROVAL -> EXECUTING is not allowed
    d = eng.approve(d.id, reviewed_by="a")
    with pytest.raises(InvalidTransition):
        eng.approve(d.id, reviewed_by="b")
    with pytest.raises(InvalidTransition):
        eng.reject(d.id, reviewed_by="b", reason="too late")
    with pytest.raises(InvalidTransition):
        check(ACTION_TRANSITIONS, ActionStatus.PROPOSED, ActionStatus.COMPLETED, "action")
    with pytest.raises(InvalidTransition):
        check(ACTION_TRANSITIONS, ActionStatus.PROPOSED, ActionStatus.EXECUTING, "action")
    with pytest.raises(InvalidTransition):
        check(DECISION_TRANSITIONS, DecisionStatus.DECISION_READY, DecisionStatus.EXECUTED, "decision")


def test_action_executor_guards(db):
    inc = bearing_incident(db)
    eng = DecisionEngine(db, provider=DET)
    d = eng.create(incident_id=inc.id)
    action = d.actions[0]
    ex = ActionExecutor(db)
    with pytest.raises(ActionExecutionError):
        ex.execute(action, d)  # PROPOSED, not approved
    rogue = ActionExecution(decision_id=d.id, action_type="WIRE_MONEY", status=ActionStatus.EXECUTING, approval_id=d.approvals[0].id, transitions=[])
    d.status = DecisionStatus.EXECUTING
    with pytest.raises(ActionExecutionError):
        ex.execute(rogue, d)  # not in the catalogue
    unapproved = ActionExecution(decision_id=d.id, action_type="INSPECT_MACHINE", status=ActionStatus.EXECUTING, approval_id=None, transitions=[])
    with pytest.raises(ActionExecutionError):
        ex.execute(unapproved, d)  # no approval link
    db.rollback()


def test_execution_failure_is_recorded(db, monkeypatch):
    inc = bearing_incident(db)
    eng = DecisionEngine(db, provider=DET)
    d = eng.create(incident_id=inc.id)

    def boom(self, action, decision, target):
        raise RuntimeError("work-order service unavailable")

    monkeypatch.setattr(ActionExecutor, "_do_schedule_maintenance", boom)
    d = eng.approve(d.id, reviewed_by="plant.manager")
    assert d.status == DecisionStatus.FAILED and d.actions[0].status == ActionStatus.FAILED
    assert "work-order service unavailable" in d.error and "work-order service unavailable" in d.actions[0].error
    assert d.approvals[0].status == ApprovalStatus.APPROVED  # the human approval stays on record


# ---- end-to-end ---------------------------------------------------------------------------------------------------
def test_full_workflow_business_data_to_executed_action(db):
    """Business data -> detection -> agents (investigation, evidence) -> impact -> decision -> approval -> simulated action."""
    SimulationEngine(db).run("bearing_failure")
    det = DetectionEngine(db).run()
    assert det.incidents_created >= 1
    inc = db.scalars(select(Incident).where(Incident.incident_type == "MACHINE_DEGRADATION")).first()
    eng = DecisionEngine(db, provider=DET)
    d = eng.create(incident_id=inc.id)
    from app.models import AgentRun, Investigation

    run = db.get(AgentRun, d.agent_run_id)
    inv = db.get(Investigation, d.investigation_id)
    assert run.status.value == "COMPLETED" and run.result_status == "CONCLUDED" and inv.status.value == "COMPLETED"
    assert inv.conclusion["root_cause"] == "BEARING_DEGRADATION" == d.hypothesis_type
    d = eng.approve(d.id, reviewed_by="plant.manager")
    assert d.status == DecisionStatus.EXECUTED and d.actions[0].status == ActionStatus.COMPLETED
    steps = [t.event_type for t in run.trace_entries] + [t.event_type for t in db.scalars(select(AgentTraceEntry).where(AgentTraceEntry.agent_run_id == run.id)).all()]
    for s in ("state_transition", "tool_call", "investigation_engine", "decision", "approval_requested", "approval", "action_executed"):
        assert s in steps, s
    assert db.scalar(select(func.count()).select_from(Approval)) == 1 and db.scalar(select(func.count()).select_from(ActionExecution)) == 1
