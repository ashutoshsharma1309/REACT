"""Phase 3: provider abstraction, structured outputs, tool calling, permissions, limits, retries,
failure handling, evidence grounding, trace persistence, lifecycle and the two end-to-end workflows.
No real LLM is required: the deterministic provider and a scripted mock provider are used."""
from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from app.agent_runtime.limits import AgentLimits, LimitExceeded, LimitTracker
from app.agent_runtime.tool_executor import ToolExecutor, summarise_result
from app.agent_runtime.trace import Tracer
from app.agents.fallback import DeterministicProvider
from app.agents.orchestrator import Orchestrator, OrchestratorError
from app.agents.policies import allowed_tools, is_allowed
from app.agents.provider import BedrockProvider, LLMResponse, MockLLMProvider, ProviderTimeout, ProviderUnavailable, StructuredOutputError, build_provider, extract_json, validate_into
from app.agents.schemas import DetectionAnalysis, EvidenceAssessment, InvestigationAnalysis, InvestigationResult, ToolCallRequest
from app.agents.state import IllegalTransition, assert_transition
from app.detection import DetectionEngine
from app.models import AgentRun, AgentToolCall, AgentTraceEntry, BusinessRisk, Incident, Investigation, Opportunity, Order, SensorReading
from app.models.enums import AgentRunStatus
from app.opportunities import OpportunityEngine
from app.simulation.engine import SimulationEngine

DET = DeterministicProvider()


def deterministic_default(context, schema):
    """Mock fallback: produce a valid output the same way the deterministic provider would."""
    if schema is None:  # tool turn
        return DET.generate_with_tools("", [], [{"name": n} for n in allowed_tools("investigation_agent")], context=context)
    return DET.generate_structured("", [], schema, context=context)[0]


def bearing_incident(db) -> Incident:
    SimulationEngine(db).run("bearing_failure")
    DetectionEngine(db).run()
    return db.scalars(select(Incident).where(Incident.incident_type == "MACHINE_DEGRADATION")).first()


def valve_opportunity(db) -> Opportunity:
    SimulationEngine(db).run("demand_spike")
    OpportunityEngine(db).run()
    return next(o for o in db.scalars(select(Opportunity).where(Opportunity.region == "South India")).all() if "Industrial Valve X" in o.title)


def steps(run: AgentRun, event_type: str | None = None):
    return [t for t in run.trace_entries if event_type is None or t.event_type == event_type]


# ---- 1. provider abstraction -------------------------------------------------------------------------
def test_provider_helpers_and_mock_queue():
    assert extract_json('Sure: ```json {"a": 1} ```')["a"] == 1
    assert extract_json('text {"a": {"b": 2}} tail')["a"]["b"] == 2
    with pytest.raises(StructuredOutputError):
        extract_json("no json here")
    with pytest.raises(StructuredOutputError):
        extract_json("{bad json}")

    class S(BaseModel):
        x: int

    assert validate_into(S, {"x": 3}).x == 3
    with pytest.raises(StructuredOutputError):
        validate_into(S, {"x": "not-int"})
    mock = MockLLMProvider([{"x": 1}, ProviderTimeout("slow"), LLMResponse(text='{"x": 5}')])
    obj, resp = mock.generate_structured("s", [{"role": "user", "content": "u"}], S)
    assert obj.x == 1 and resp.provider == "mock"
    with pytest.raises(ProviderTimeout):
        mock.generate_structured("s", [], S)
    assert mock.generate_structured("s", [], S)[0].x == 5
    with pytest.raises(ProviderUnavailable):
        mock.generate_structured("s", [], S)
    assert len(mock.calls) == 4
    with pytest.raises(ProviderUnavailable):
        build_provider("nope")
    assert build_provider("deterministic").is_llm is False and build_provider("mock").name == "mock"


def test_bedrock_provider_parsing_and_errors():
    class FakeClient:
        def __init__(self, resp=None, exc=None):
            self.resp, self.exc, self.calls = resp, exc, []

        def converse(self, **kwargs):
            self.calls.append(kwargs)
            if self.exc:
                raise self.exc
            return self.resp

    resp = {"output": {"message": {"role": "assistant", "content": [{"text": "Calling a tool."}, {"toolUse": {"toolUseId": "tu-1", "name": "get_machine_readings", "input": {"machine_id": "abc"}}}]}}, "usage": {"inputTokens": 120, "outputTokens": 40}, "stopReason": "tool_use"}
    p = BedrockProvider(model="m", region="us-east-1", client=FakeClient(resp))
    out = p.generate_with_tools("sys", [{"role": "user", "content": "u"}], [{"name": "get_machine_readings", "description": "d", "input_schema": {"type": "object", "properties": {}}}])
    assert out.tool_calls[0].tool_name == "get_machine_readings" and out.tool_calls[0].arguments == {"machine_id": "abc"} and out.input_tokens == 120
    body = p._client.calls[0]
    assert body["modelId"] == "m" and "toolConfig" in body and body["inferenceConfig"]["temperature"] == 0.0

    class S(BaseModel):
        y: str

    p2 = BedrockProvider(model="m", region="r", client=FakeClient({"output": {"message": {"content": [{"text": 'Here: {"y": "ok"}'}]}}, "usage": {}}))
    obj, r = p2.generate_structured("sys", [{"role": "user", "content": "u"}], S)
    assert obj.y == "ok" and "JSON schema" in p2._client.calls[0]["messages"][-1]["content"][0]["text"]

    class ReadTimeoutError(Exception):
        pass

    with pytest.raises(ProviderTimeout):
        BedrockProvider(model="m", region="r", client=FakeClient(exc=ReadTimeoutError("Read timed out"))).generate("s", [{"role": "user", "content": "u"}])
    with pytest.raises(Exception) as e:
        BedrockProvider(model="m", region="r", client=FakeClient(exc=RuntimeError("AccessDeniedException: secret-key-123"))).generate("s", [{"role": "user", "content": "u"}])
    assert "secret-key-123" not in str(e.value)  # error messages never leak provider details


# ---- 2. structured output validation + retry --------------------------------------------------------------
def test_invalid_structured_output_is_rejected_then_retried(db):
    inc = bearing_incident(db)
    mock = MockLLMProvider([{"subject_kind": "incident", "summary": "too short"}], default=deterministic_default)  # first detection output invalid
    run = Orchestrator(db, provider=mock, limits=AgentLimits(max_retries=1)).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.COMPLETED and run.llm_used is True and run.fallback_used is False
    val = steps(run, "validation")
    assert len(val) == 1 and val[0].agent_name == "detection_agent" and val[0].status == "error"
    assert run.detection_analysis["subject_id"] == str(inc.id)
    assert run.llm_calls_count >= 6  # one extra call for the retry


def test_wrong_subject_in_detection_output_is_rejected(db):
    inc = bearing_incident(db)
    bad = DET.generate_structured("", [], DetectionAnalysis, context={"task": "detection_analysis", "finding": {"subject_kind": "incident", "subject_id": str(uuid.uuid4()), "type": inc.incident_type.value, "severity": "HIGH", "title": inc.title}})[0]
    mock = MockLLMProvider([bad, bad], default=deterministic_default)  # two bad attempts -> fallback
    run = Orchestrator(db, provider=mock, limits=AgentLimits(max_retries=1)).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.COMPLETED and run.fallback_used is True
    assert any(t.event_type == "fallback" and t.agent_name == "detection_agent" for t in run.trace_entries)


# ---- 3/4/5. tool selection, permissions, execution --------------------------------------------------------------
def test_tool_permissions_and_execution(db):
    inc = bearing_incident(db)
    orch = Orchestrator(db, provider=DET)
    run = orch.start(incident_id=inc.id)
    tracker = LimitTracker(AgentLimits(max_tool_calls=3))
    ex = ToolExecutor(db, run, Tracer(db, run), tracker)
    assert is_allowed("investigation_agent", "calculate_production_loss") and not is_allowed("evidence_agent", "calculate_production_loss") and not is_allowed("detection_agent", "get_machine_readings")
    denied, data = ex.execute("evidence_agent", ToolCallRequest(tool_name="calculate_production_loss", arguments={"lost_units": 1, "unit_margin": 1}))
    assert denied.status == "DENIED" and data is None
    unknown, _ = ex.execute("investigation_agent", ToolCallRequest(tool_name="drop_table", arguments={}))
    assert unknown.status == "DENIED"
    bad, _ = ex.execute("investigation_agent", ToolCallRequest(tool_name="get_machine_readings", arguments={"machine_id": "nope"}))
    assert bad.status == "INVALID_ARGUMENTS" and "INVALID_PARAM" in bad.error
    ok, payload = ex.execute("investigation_agent", ToolCallRequest(tool_name="get_machine_readings", arguments={"machine_id": str(inc.machine_id)}))
    assert ok.status == "OK" and ok.record_count > 0 and ok.duration_ms is not None and payload["machine"]["id"] == str(inc.machine_id)
    assert "reading_ids_recent" in ok.result_summary["record_ids"]
    rows = db.scalars(select(AgentToolCall).where(AgentToolCall.agent_run_id == run.id).order_by(AgentToolCall.sequence)).all()
    assert [r.status.value for r in rows] == ["DENIED", "DENIED", "INVALID_ARGUMENTS", "OK"] and rows[0].permitted is False and rows[3].permitted is True
    assert tracker.tool_calls == 2  # denied calls do not consume the budget
    ex.execute("investigation_agent", ToolCallRequest(tool_name="get_maintenance_history", arguments={"machine_id": str(inc.machine_id)}))
    with pytest.raises(LimitExceeded):
        ex.execute("investigation_agent", ToolCallRequest(tool_name="get_production_metrics", arguments={"production_line_id": str(inc.production_line_id)}))
    assert rows[3].result_summary["count"] == ok.record_count
    assert summarise_result({"count": 2, "items": [{"id": "a"}, {"id": "b"}]})["record_ids"]["id"] == ["a", "b"]


def test_agent_tool_selection_uses_registry_and_records_denials(db):
    inc = bearing_incident(db)
    mock = MockLLMProvider(default=deterministic_default)
    mock.queue = [
        lambda ctx: deterministic_default(ctx, None) if ctx.get("task") != "tool_selection" else {"tool_calls": [{"tool_name": "get_machine_readings", "arguments": {"machine_id": str(inc.machine_id), "hours": 24}, "purpose": "sensor stats"}, {"tool_name": "run_sql", "arguments": {"q": "select 1"}}]},
        {"tool_calls": []},
    ]
    # the first queued item is consumed by the detection step; make it valid for that step and script the tool turns after it
    mock.queue = [lambda ctx: deterministic_default(ctx, DetectionAnalysis), {"tool_calls": [{"tool_name": "get_machine_readings", "arguments": {"machine_id": str(inc.machine_id), "hours": 24}, "purpose": "sensor stats"}, {"tool_name": "run_sql", "arguments": {"q": "select 1"}}]}, {"tool_calls": []}]
    run = Orchestrator(db, provider=mock).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.COMPLETED
    calls = {(c.tool_name, c.status.value) for c in run.tool_calls}
    assert ("get_machine_readings", "OK") in calls and ("run_sql", "DENIED") in calls
    assert run.iterations == 2 and run.tool_calls_count == 2


# ---- 6/7. limits and retries --------------------------------------------------------------------------------
def test_iteration_and_tool_call_limits_stop_the_loop(db):
    inc = bearing_incident(db)
    greedy = MockLLMProvider(default=lambda ctx, schema: {"tool_calls": [{"tool_name": "get_maintenance_history", "arguments": {"machine_id": str(inc.machine_id)}}]} if ctx.get("task") == "tool_selection" else deterministic_default(ctx, schema))
    run = Orchestrator(db, provider=greedy, limits=AgentLimits(max_iterations=2, max_tool_calls=12)).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.COMPLETED and run.iterations == 2
    assert any(t.event_type == "limit" and "iterations" in t.summary for t in run.trace_entries)
    greedy2 = MockLLMProvider(default=lambda ctx, schema: {"tool_calls": [{"tool_name": "get_maintenance_history", "arguments": {"machine_id": str(inc.machine_id)}}] * 5} if ctx.get("task") == "tool_selection" else deterministic_default(ctx, schema))
    run2 = Orchestrator(db, provider=greedy2, limits=AgentLimits(max_iterations=4, max_tool_calls=3)).start_and_run(incident_id=inc.id)
    assert run2.status == AgentRunStatus.COMPLETED
    assert any(c.status.value == "LIMIT_EXCEEDED" for c in run2.tool_calls) and sum(1 for c in run2.tool_calls if c.status.value == "OK") == 3
    tracker = LimitTracker(AgentLimits(max_execution_seconds=0))
    tracker.started -= 1
    with pytest.raises(LimitExceeded):
        tracker.check_time()


def test_llm_call_limit_yields_insufficient_evidence(db):
    inc = bearing_incident(db)
    run = Orchestrator(db, provider=MockLLMProvider(default=deterministic_default), limits=AgentLimits(max_llm_calls=1), fallback_enabled=False).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.FAILED and run.result_status in ("INSUFFICIENT_EVIDENCE", "FAILED")
    assert run.result["status"] == run.result_status and any(t.event_type == "limit" for t in run.trace_entries)


def test_retry_after_provider_timeout(db):
    inc = bearing_incident(db)
    mock = MockLLMProvider([ProviderTimeout("timed out")], default=deterministic_default)
    run = Orchestrator(db, provider=mock, limits=AgentLimits(max_retries=1)).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.COMPLETED and run.fallback_used is False
    retries = steps(run, "retry")
    assert len(retries) == 1 and "ProviderTimeout" in retries[0].error


# ---- 8. failure handling -----------------------------------------------------------------------------------------
def test_provider_failure_without_fallback_fails_run(db):
    inc = bearing_incident(db)
    dead = MockLLMProvider(default=lambda ctx, schema: ProviderUnavailable("endpoint down"))
    run = Orchestrator(db, provider=dead, limits=AgentLimits(max_retries=1), fallback_enabled=False).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.FAILED and run.result_status == "FAILED" and "AgentFailure" in run.error
    assert run.trace_entries[-1].event_type == "error" and run.trace_entries[-1].status == "error"
    assert run.result["status"] == "FAILED" and run.completed_at is not None
    # the failed run does not block a fresh one
    ok = Orchestrator(db, provider=DET).start_and_run(incident_id=inc.id)
    assert ok.status == AgentRunStatus.COMPLETED


def test_provider_failure_with_fallback_completes_and_is_flagged(db):
    inc = bearing_incident(db)
    dead = MockLLMProvider(default=lambda ctx, schema: ProviderUnavailable("endpoint down"))
    run = Orchestrator(db, provider=dead, limits=AgentLimits(max_retries=0), fallback_enabled=True).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.COMPLETED and run.fallback_used is True and run.llm_used is False
    assert run.result["fallback_used"] is True and len(steps(run, "fallback")) >= 3


# ---- 9/10/11. evidence grounding, missing and contradictory evidence ------------------------------------------------
def test_unknown_evidence_ids_and_rescored_ess_are_rejected(db):
    inc = bearing_incident(db)

    def tampered(ctx, schema):
        out = deterministic_default(ctx, schema)
        if schema is InvestigationAnalysis and not ctx.get("previous_error"):
            out.hypotheses[0].supporting_evidence_ids.append(str(uuid.uuid4()))  # fabricated evidence id
        if schema is EvidenceAssessment and not ctx.get("previous_error"):
            out.evidence_support_score = 0.99  # attempt to rescore
        return out

    run = Orchestrator(db, provider=MockLLMProvider(default=tampered), limits=AgentLimits(max_retries=1)).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.COMPLETED and run.fallback_used is False
    val = steps(run, "validation")
    assert len(val) == 2 and any("unknown evidence ids" in v.error for v in val) and any("deterministic value" in v.error for v in val)
    inv = db.get(Investigation, run.investigation_id)
    valid = {str(e.id) for e in inv.evidence_items}
    assert set(run.result["supporting_evidence_ids"]) <= valid and run.result["supporting_evidence_ids"]


def test_missing_evidence_yields_insufficient_evidence(db):
    inc = bearing_incident(db)

    def cautious(ctx, schema):
        out = deterministic_default(ctx, schema)
        if schema is EvidenceAssessment:
            out.sufficient = False
            out.missing_evidence = ["No vibration spectrum available to confirm bearing wear pattern."]
        return out

    run = Orchestrator(db, provider=MockLLMProvider(default=cautious)).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.COMPLETED and run.result_status == "INSUFFICIENT_EVIDENCE"
    assert "Insufficient evidence" in run.result["conclusion"] and run.result["missing_evidence"]
    assert run.result["hypothesis_type"] == "BEARING_DEGRADATION"  # the leading hypothesis is still reported, not asserted


def test_contradictory_evidence_is_surfaced(db):
    inc = bearing_incident(db)
    run = Orchestrator(db, provider=DET).start_and_run(incident_id=inc.id)
    assert run.result["contradicting_evidence_ids"] and run.result["conflicts"]
    assert set(run.result["conflicts"][0]["evidence_ids"]) == set(run.result["contradicting_evidence_ids"])


# ---- 12/13. trace persistence and lifecycle ----------------------------------------------------------------------
def test_trace_persistence_and_no_chain_of_thought(db):
    inc = bearing_incident(db)
    run = Orchestrator(db, provider=DET).start_and_run(incident_id=inc.id)
    entries = db.scalars(select(AgentTraceEntry).where(AgentTraceEntry.agent_run_id == run.id).order_by(AgentTraceEntry.sequence)).all()
    assert [e.sequence for e in entries] == list(range(1, len(entries) + 1))
    states = [e.summary for e in entries if e.event_type == "state_transition"]
    assert states[1:] == ["CREATED -> CLASSIFYING", "CLASSIFYING -> INVESTIGATING", "INVESTIGATING -> VERIFYING", "VERIFYING -> COMPLETED"]
    agents = {e.agent_name for e in entries}
    assert {"orchestrator", "detection_agent", "investigation_agent", "evidence_agent"} <= agents
    tool_entries = [e for e in entries if e.event_type == "tool_call"]
    assert tool_entries and all(e.tool_name and e.tool_call_id and e.tool_arguments is not None for e in tool_entries)
    assert any(e.evidence_ids for e in entries) and any(e.hypothesis_ids for e in entries)
    for e in entries:
        assert len(e.summary) <= 2000 and "chain_of_thought" not in e.details and "reasoning" not in e.details
    # tool payloads are summarised, never stored in full
    for c in run.tool_calls:
        assert "sensors" not in c.result_summary and set(c.result_summary) <= {"count", "keys", "record_ids", "summary"}


def test_run_lifecycle_and_state_machine(db):
    inc = bearing_incident(db)
    orch = Orchestrator(db, provider=DET)
    run = orch.start(incident_id=inc.id)
    assert run.status == AgentRunStatus.CREATED and run.started_at is None and len(run.trace_entries) == 1
    run = orch.run(run.id)
    assert run.status == AgentRunStatus.COMPLETED and run.started_at and run.completed_at and run.investigation_id
    with pytest.raises(OrchestratorError):
        orch.run(run.id)  # only CREATED runs can be executed
    with pytest.raises(OrchestratorError):
        orch.start()
    with pytest.raises(OrchestratorError):
        orch.start(incident_id=uuid.uuid4())
    with pytest.raises(IllegalTransition):
        assert_transition(AgentRunStatus.CREATED, AgentRunStatus.COMPLETED)
    assert_transition(AgentRunStatus.VERIFYING, AgentRunStatus.FAILED)
    assert run.limits["max_tool_calls"] == AgentLimits.from_settings().max_tool_calls


# ---- 14/15. end-to-end workflows -----------------------------------------------------------------------------------
def test_bearing_failure_agent_workflow_end_to_end(db):
    inc = bearing_incident(db)
    before = (db.scalar(select(func.sum(SensorReading.value))), db.scalar(select(func.count()).select_from(Order)))
    run = Orchestrator(db, provider=DET).start_and_run(incident_id=inc.id)
    assert run.status == AgentRunStatus.COMPLETED and run.result_status == "CONCLUDED"
    res = InvestigationResult.model_validate(run.result)
    assert res.hypothesis_type == "BEARING_DEGRADATION" and res.evidence_support_score >= 0.8
    inv = db.get(Investigation, run.investigation_id)
    lead = next(h for h in inv.hypotheses if h.hypothesis_type == "BEARING_DEGRADATION")
    assert res.hypothesis_id == str(lead.id) and abs(res.evidence_support_score - lead.evidence_support_score) < 1e-6  # deterministic score restated
    assert res.impact_reference.estimated_total == inv.impact["estimated_total"] and res.impact_reference.basis == "SYNTHETIC"
    assert {c.tool_name for c in run.tool_calls} >= {"get_machine_readings", "get_maintenance_history", "get_previous_incidents"}
    assert all(c.status.value == "OK" for c in run.tool_calls)
    assert run.detection_analysis["event_type"] == "MACHINE_DEGRADATION" and run.evidence_assessment["sufficient"] is True
    # no money field exists in any LLM-facing schema
    for schema in (DetectionAnalysis, InvestigationAnalysis, EvidenceAssessment):
        assert not any(k for k in schema.model_json_schema()["properties"] if "revenue" in k or "cost" in k or "value" in k)
    assert (db.scalar(select(func.sum(SensorReading.value))), db.scalar(select(func.count()).select_from(Order))) == before  # raw data untouched
    db.refresh(inc)
    assert inc.status.value == "INVESTIGATING"


def test_demand_spike_agent_workflow_end_to_end(db):
    opp = valve_opportunity(db)
    run = Orchestrator(db, provider=DET).start_and_run(opportunity_id=opp.id)
    assert run.status == AgentRunStatus.COMPLETED and run.result_status == "CONCLUDED"
    res = InvestigationResult.model_validate(run.result)
    assert res.subject_kind == "opportunity" and res.hypothesis_type in ("SEGMENT_EXPANSION", "SUSTAINED_REGIONAL_DEMAND", "MARKET_CATEGORY_GROWTH")
    assert res.impact_reference.estimated_total > 0 and res.supporting_evidence_ids
    assert {c.tool_name for c in run.tool_calls} >= {"get_customer_orders", "get_market_signals", "get_inventory_status"}
    assert run.detection_analysis["subject_kind"] == "opportunity" and "orders" in run.detection_analysis["evidence_categories"]


def test_risk_agent_workflow(db):
    SimulationEngine(db).run("supplier_delay")
    DetectionEngine(db).run()
    risk = db.scalars(select(BusinessRisk).where(BusinessRisk.risk_type == "SUPPLIER_DELAY")).first()
    run = Orchestrator(db, provider=DET).start_and_run(risk_id=risk.id)
    assert run.status == AgentRunStatus.COMPLETED and run.result["hypothesis_type"] in ("SINGLE_SOURCE_EXPOSURE", "SUPPLIER_RELIABILITY_ISSUE")
    assert any(c.tool_name == "get_supplier_history" and c.status.value == "OK" for c in run.tool_calls)
