"""Phase 2 API + the two end-to-end success scenarios through HTTP."""
from __future__ import annotations

import uuid


def test_registry_and_tools_endpoints(client):
    r = client.get("/investigations/registry").json()
    assert r["count"] >= 20 and {"type", "triggers", "supporting", "contradicting"} <= set(r["hypotheses"][0])
    t = client.get("/investigations/tools").json()
    assert t["count"] >= 13 and any(x["name"] == "get_machine_readings" for x in t["tools"])


def test_scenario_a_incident_investigation_via_api(client):
    """POST /simulation/bearing-failure -> /detection/run -> POST /investigations -> /run -> hypotheses, evidence, root cause, impact, report."""
    assert client.post("/simulation/bearing-failure").status_code == 200
    det = client.post("/detection/run").json()
    inc = next(i for i in det["incidents"] if i["type"] == "MACHINE_DEGRADATION")
    created = client.post("/investigations", json={"incident_id": inc["incident_id"]})
    assert created.status_code == 201 and created.json()["status"] == "CREATED"
    iid = created.json()["investigation_id"]
    assert client.get(f"/investigations/{iid}/report").status_code == 409  # not run yet
    run = client.post(f"/investigations/{iid}/run")
    assert run.status_code == 200
    body = run.json()
    assert body["status"] == "COMPLETED" and body["confidence"] is not None and body["confidence_label"]
    assert "Evidence Support Score" in body["terminology"]
    hyps = client.get(f"/investigations/{iid}/hypotheses").json()
    assert len(hyps) >= 3 and hyps[0]["type"] == "BEARING_DEGRADATION" and hyps[0]["rank"] == 1
    assert hyps[0]["evidence_support_score"] > hyps[1]["evidence_support_score"]
    ev = client.get(f"/investigations/{iid}/evidence").json()
    assert ev and {"evidence_id", "source_type", "source_id", "claim", "relevance", "supports", "contradicts", "evidence_support_score"} <= set(ev[0])
    sup = client.get(f"/investigations/{iid}/evidence", params={"hypothesis_id": hyps[0]["hypothesis_id"], "supports": True}).json()
    assert sup and all(e["supports"] for e in sup)
    rep = client.get(f"/investigations/{iid}/report").json()
    assert rep["report"]["title"] == "REACT INVESTIGATION" and "Top hypothesis: Bearing degradation" in rep["text"]
    assert rep["report"]["supporting_evidence"] and rep["report"]["impact_lines"] and rep["report"]["recommended_next_investigation"]
    detail = client.get(f"/investigations/{iid}").json()
    assert detail["conclusion"]["root_cause"] == "BEARING_DEGRADATION" and detail["impact"]["estimated_total"] > 0 and detail["trace"]
    assert client.get(f"/investigations/{iid}/graph").json()["graph"]["counts"]["hypotheses"] >= 3
    trace = client.get(f"/investigations/{iid}/trace").json()["trace"]
    assert trace[0]["step"] == "created" and trace[-1]["step"] == "completed"
    assert client.get(f"/incidents/{inc['incident_id']}/investigation").json()["investigation_id"] == iid
    sub = client.get(f"/incidents/{inc['incident_id']}/evidence").json()
    assert sub["investigation_id"] == iid and sub["evidence"] and sub["detection_evidence"]["records"]
    assert client.get(f"/incidents/{inc['incident_id']}").json()["status"] == "INVESTIGATING"
    listing = client.get("/investigations", params={"subject_type": "INCIDENT", "status": "COMPLETED"}).json()
    assert listing["total"] >= 1


def test_scenario_b_opportunity_investigation_via_api(client):
    """POST /simulation/demand-spike -> /opportunity-detection/run -> investigation -> evidence -> explanation -> potential impact -> report."""
    assert client.post("/simulation/demand-spike").status_code == 200
    opp_run = client.post("/opportunity-detection/run").json()
    opp = next(o for o in opp_run["opportunities"] if o["type"] == "DEMAND_OPPORTUNITY" and o["region"] == "South India" and "Industrial Valve X" in o["title"])
    body = client.post("/investigations", json={"opportunity_id": opp["opportunity_id"], "run": True}).json()
    assert body["status"] == "COMPLETED" and body["subject_type"] == "OPPORTUNITY"
    iid = body["investigation_id"]
    hyps = client.get(f"/investigations/{iid}/hypotheses").json()
    types = {h["type"] for h in hyps}
    assert {"SUSTAINED_REGIONAL_DEMAND", "TEMPORARY_CUSTOMER_SPIKE", "COMPETITOR_SUPPLY_SHORTAGE", "SEASONAL_DEMAND"} <= types
    ev = client.get(f"/investigations/{iid}/evidence").json()
    assert any(e["source_type"] == "order" and e["supports"] for e in ev)
    rep = client.get(f"/investigations/{iid}/report").json()
    assert rep["report"]["title"] == "REACT OPPORTUNITY INVESTIGATION" and "Potential impact" in rep["text"] and "Evidence Support Score" in rep["text"]
    detail = client.get(f"/opportunities/{opp['opportunity_id']}/investigation").json()
    assert detail["conclusion"]["kind"] == "opportunity_explanation" and detail["impact"]["estimated_revenue_opportunity"]["value"] > 0
    assert client.get(f"/opportunities/{opp['opportunity_id']}/evidence").json()["investigation_id"] == iid


def test_risk_investigation_via_api(client):
    client.post("/simulation/supplier-delay")
    det = client.post("/detection/run").json()
    r = next(x for x in det["risks"] if x["type"] == "SUPPLIER_DELAY")
    assert client.get(f"/risks/{r['risk_id']}").json()["type"] == "SUPPLIER_DELAY"
    body = client.post("/investigations", json={"risk_id": r["risk_id"], "run": True}).json()
    assert body["status"] == "COMPLETED"
    assert client.get(f"/risks/{r['risk_id']}/investigation").json()["investigation_id"] == body["investigation_id"]
    assert client.get(f"/risks/{r['risk_id']}/evidence").json()["evidence"]


def test_investigation_api_errors(client):
    assert client.post("/investigations", json={}).status_code == 422
    assert client.post("/investigations", json={"incident_id": str(uuid.uuid4()), "opportunity_id": str(uuid.uuid4())}).status_code == 422
    assert client.post("/investigations", json={"incident_id": str(uuid.uuid4())}).status_code == 404
    assert client.get(f"/investigations/{uuid.uuid4()}").status_code == 404
    assert client.post(f"/investigations/{uuid.uuid4()}/run").status_code == 404
    assert client.get(f"/incidents/{uuid.uuid4()}/investigation").status_code == 404
    assert client.get(f"/opportunities/{uuid.uuid4()}/evidence").status_code == 404
    assert client.get(f"/risks/{uuid.uuid4()}").status_code == 404


def test_power_instability_endpoint(client):
    r = client.post("/simulation/power-instability").json()
    assert r["run"]["scenario"] == "power_instability" and "POWER_INSTABILITY" in r["next_step"]
