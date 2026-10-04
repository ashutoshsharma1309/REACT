"""Phase 4 API: decisions, approvals, actions and the one-call REACT workflow."""
from __future__ import annotations

import uuid


def _bearing_incident(client) -> str:
    client.post("/simulation/bearing-failure")
    det = client.post("/detection/run").json()
    return next(i for i in det["incidents"] if i["type"] == "MACHINE_DEGRADATION")["incident_id"]


def test_catalogue_endpoint(client):
    r = client.get("/actions/catalogue").json()
    assert r["count"] >= 10 and "simulated" in r["note"]
    sm = next(a for a in r["actions"] if a["type"] == "SCHEDULE_MAINTENANCE")
    assert "BEARING_DEGRADATION" in sm["default_for_hypotheses"] and sm["requires_human_approval"] is True


def test_react_run_approve_execute_via_api(client):
    inc_id = _bearing_incident(client)
    r = client.post("/react/run", json={"incident_id": inc_id})
    assert r.status_code == 201
    d = r.json()
    assert d["status"] == "AWAITING_APPROVAL" and d["action_type"] == "SCHEDULE_MAINTENANCE" and d["decision_readiness"] == "READY"
    assert d["hypothesis_type"] == "BEARING_DEGRADATION" and d["evidence_ids"] and d["expected_impact"]["estimated_total"] > 0
    assert d["approvals"][0]["status"] == "PENDING" and d["actions"][0]["status"] == "PROPOSED" and "Evidence Support Score" in d["terminology"]
    did = d["decision_id"]
    ev = client.get(f"/decisions/{did}/evidence").json()
    assert ev["supporting"] and all(e["supports"] for e in ev["supporting"]) and ev["chain"][0] == "decision"
    assert client.post(f"/decisions/{did}/execute").status_code == 409  # not approved yet
    assert client.post(f"/decisions/{did}/approve", json={}).status_code == 422  # reviewer required
    ok = client.post(f"/decisions/{did}/approve", json={"reviewed_by": "plant.manager", "reason": "replace in Sunday window"}).json()
    assert ok["status"] == "EXECUTED" and ok["actions"][0]["status"] == "COMPLETED" and ok["actions"][0]["result"]["simulated"] is True
    assert client.post(f"/decisions/{did}/approve", json={"reviewed_by": "again"}).status_code == 409
    trace = client.get(f"/decisions/{did}/trace").json()
    assert trace["timeline"] and trace["approvals"][0]["reviewed_by"] == "plant.manager" and trace["agent_trace"]
    assert any(t["event_type"] == "action_executed" for t in trace["agent_trace"])
    action_id = ok["actions"][0]["action_id"]
    assert client.get(f"/actions/{action_id}").json()["status"] == "COMPLETED"
    assert client.get("/actions", params={"status": "COMPLETED"}).json()["total"] >= 1
    assert client.get("/approvals", params={"status": "APPROVED"}).json()["total"] >= 1
    assert client.get(f"/incidents/{inc_id}/decision").json()["decision_id"] == did
    assert client.get(f"/incidents/{inc_id}").json()["status"] == "EXECUTING"


def test_two_step_decision_from_agent_run_and_reject(client):
    inc_id = _bearing_incident(client)
    run = client.post(f"/incidents/{inc_id}/react").json()
    d = client.post("/decisions", json={"agent_run_id": run["agent_run_id"]}).json()
    assert d["agent_run_id"] == run["agent_run_id"] and d["status"] == "AWAITING_APPROVAL"
    assert client.post(f"/decisions/{d['decision_id']}/reject", json={"reviewed_by": "x"}).status_code == 422  # reason required
    rej = client.post(f"/decisions/{d['decision_id']}/reject", json={"reviewed_by": "plant.manager", "reason": "machine already scheduled for overhaul"}).json()
    assert rej["status"] == "REJECTED" and rej["actions"][0]["status"] == "REJECTED"
    assert client.post(f"/decisions/{d['decision_id']}/execute").status_code == 409
    assert client.get("/decisions", params={"status": "REJECTED"}).json()["total"] >= 1


def test_opportunity_and_risk_decisions_via_api(client):
    client.post("/simulation/demand-spike")
    opps = client.post("/opportunity-detection/run").json()["opportunities"]
    opp = next(o for o in opps if o["type"] == "DEMAND_OPPORTUNITY" and o["region"] == "South India" and "Industrial Valve X" in o["title"])
    d = client.post("/react/run", json={"opportunity_id": opp["opportunity_id"]}).json()
    assert d["subject_type"] == "OPPORTUNITY" and d["target_type"] == "product"
    done = client.post(f"/decisions/{d['decision_id']}/approve", json={"reviewed_by": "sales.director", "execute": False}).json()
    assert done["status"] == "APPROVED"
    done = client.post(f"/decisions/{d['decision_id']}/execute", json={"executed_by": "sales.director"}).json()
    assert done["status"] == "EXECUTED"
    assert client.get(f"/opportunities/{opp['opportunity_id']}/decision").json()["decision_id"] == d["decision_id"]
    client.post("/simulation/supplier-delay")
    risk = next(r for r in client.post("/detection/run").json()["risks"] if r["type"] == "SUPPLIER_DELAY")
    rd = client.post("/react/run", json={"risk_id": risk["risk_id"]}).json()
    assert rd["action_type"] == "REVIEW_SUPPLIER"
    assert client.get(f"/risks/{risk['risk_id']}/decision").json()["decision_id"] == rd["decision_id"]


def test_decision_api_errors(client):
    assert client.post("/decisions", json={}).status_code == 422
    assert client.post("/decisions", json={"agent_run_id": str(uuid.uuid4()), "incident_id": str(uuid.uuid4())}).status_code == 422
    assert client.post("/decisions", json={"agent_run_id": str(uuid.uuid4())}).status_code == 404
    assert client.post("/react/run", json={"agent_run_id": str(uuid.uuid4())}).status_code == 400
    assert client.get(f"/decisions/{uuid.uuid4()}").status_code == 404
    assert client.post(f"/decisions/{uuid.uuid4()}/approve", json={"reviewed_by": "x"}).status_code == 404
    assert client.get(f"/actions/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/approvals/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/incidents/{uuid.uuid4()}/decision").status_code == 404
