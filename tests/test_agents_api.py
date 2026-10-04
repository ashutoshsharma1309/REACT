"""Phase 3 API."""
from __future__ import annotations

import uuid


def test_agent_roster(client):
    r = client.get("/agents").json()
    assert r["enabled"] is True and {a["agent"] for a in r["agents"]} == {"detection_agent", "investigation_agent", "evidence_agent", "decision_agent", "orchestrator"}  # decision_agent added in Phase 4
    dec = next(a for a in r["agents"] if a["agent"] == "decision_agent")
    assert not any(t.startswith("calculate_") for t in dec["allowed_tools"])  # decisions never compute money
    inv = next(a for a in r["agents"] if a["agent"] == "investigation_agent")
    assert "get_machine_readings" in inv["allowed_tools"] and "calculate_production_loss" in inv["allowed_tools"]
    ev = next(a for a in r["agents"] if a["agent"] == "evidence_agent")
    assert "calculate_production_loss" not in ev["allowed_tools"]
    assert r["state_machine"]["CREATED"] == ["CLASSIFYING", "FAILED"] and r["provider"]["provider"] == "deterministic"
    assert "secret" not in str(r).lower() and "AKIA" not in str(r)


def test_incident_react_workflow_via_api(client):
    client.post("/simulation/bearing-failure")
    det = client.post("/detection/run").json()
    inc = next(i for i in det["incidents"] if i["type"] == "MACHINE_DEGRADATION")
    r = client.post(f"/incidents/{inc['incident_id']}/react")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "COMPLETED" and body["result_status"] == "CONCLUDED" and body["result"]["hypothesis_type"] == "BEARING_DEGRADATION"
    assert body["provider"] == "deterministic" and body["llm_used"] is False and body["trace"] and body["tool_calls"]
    rid = body["agent_run_id"]
    trace = client.get(f"/agent-runs/{rid}/trace").json()
    assert trace[0]["event_type"] == "state_transition" and trace[-1]["event_type"] == "result" and all("trace_id" in t for t in trace)
    calls = client.get(f"/agent-runs/{rid}/tool-calls").json()
    assert calls and all(c["status"] == "OK" and c["permitted"] for c in calls) and "record_ids" in calls[0]["result_summary"]
    res = client.get(f"/agent-runs/{rid}/result").json()
    assert res["result"]["supporting_evidence_ids"] and res["result"]["impact_reference"]["estimated_total"] > 0
    assert client.get(f"/agent-runs/{rid}").json()["investigation_id"] == body["investigation_id"]
    assert client.get(f"/incidents/{inc['incident_id']}/agent-run").json()["agent_run_id"] == rid
    listing = client.get("/agent-runs", params={"status": "COMPLETED", "subject_type": "INCIDENT"}).json()
    assert listing["total"] >= 1
    # the Phase 2 investigation created by the agent is reachable through the Phase 2 API
    assert client.get(f"/investigations/{body['investigation_id']}/report").status_code == 200


def test_opportunity_and_risk_react_via_api(client):
    client.post("/simulation/demand-spike")
    opps = client.post("/opportunity-detection/run").json()["opportunities"]
    opp = next(o for o in opps if o["type"] == "DEMAND_OPPORTUNITY" and o["region"] == "South India" and "Industrial Valve X" in o["title"])
    body = client.post(f"/opportunities/{opp['opportunity_id']}/react").json()
    assert body["status"] == "COMPLETED" and body["result"]["subject_kind"] == "opportunity" and body["result"]["impact_reference"]["estimated_total"] > 0
    assert client.get(f"/opportunities/{opp['opportunity_id']}/agent-run").json()["agent_run_id"] == body["agent_run_id"]
    client.post("/simulation/supplier-delay")
    risks = client.post("/detection/run").json()["risks"]
    risk = next(r for r in risks if r["type"] == "SUPPLIER_DELAY")
    body = client.post(f"/risks/{risk['risk_id']}/react").json()
    assert body["status"] == "COMPLETED" and body["subject_type"] == "RISK"


def test_two_step_create_then_run_and_errors(client):
    client.post("/simulation/bearing-failure")
    det = client.post("/detection/run").json()
    inc = next(i for i in det["incidents"] if i["type"] == "MACHINE_DEGRADATION")
    created = client.post("/agent-runs", json={"incident_id": inc["incident_id"], "run": False}).json()
    assert created["status"] == "CREATED" and created["result"] == {}
    ran = client.post(f"/agent-runs/{created['agent_run_id']}/run").json()
    assert ran["status"] == "COMPLETED"
    assert client.post(f"/agent-runs/{created['agent_run_id']}/run").status_code == 409
    assert client.post("/agent-runs", json={}).status_code == 422
    assert client.post("/agent-runs", json={"incident_id": str(uuid.uuid4()), "risk_id": str(uuid.uuid4())}).status_code == 422
    assert client.post("/agent-runs", json={"incident_id": str(uuid.uuid4())}).status_code == 404
    assert client.get(f"/agent-runs/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/incidents/{uuid.uuid4()}/agent-run").status_code == 404
