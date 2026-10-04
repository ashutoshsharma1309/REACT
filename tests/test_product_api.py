"""Phase 5 product read models: overview, findings, activity, scan, demo."""
from __future__ import annotations

import uuid


def test_overview_before_scan_is_honest(client):
    o = client.get("/overview").json()
    assert o["company"]["name"] == "Astra Manufacturing" and o["data_source"].startswith("Synthetic")
    assert o["scanned"] is False and o["business_status"] == "HEALTHY" and o["attention"] == []
    assert o["impact"]["exposure_total"] is None  # no findings -> no number, not zero
    assert o["metrics"]["revenue"] > 0


def test_scan_then_findings(client):
    s = client.post("/scan").json()
    assert s["incidents_created"] == 0 and s["risks_created"] == 0 and s["opportunities_created"] >= 1  # the baseline is quiet
    opps = client.get("/findings", params={"kind": "opportunity"}).json()
    assert opps and all(f["kind"] == "opportunity" and f["stage"] == "DETECTED" and f["confidence"] for f in opps)
    assert client.get("/findings", params={"kind": "nonsense"}).status_code == 422
    o = client.get("/overview").json()
    assert o["scanned"] is True and o["business_status"] == "HEALTHY" and o["counts"]["opportunities"] == len(opps)


def test_bearing_demo_to_executed_action_changes_overview(client):
    r = client.post("/demo/run", json={"scenario": "bearing_failure"}).json()
    assert r["featured"]["kind"] == "incident" and r["detected"]["incidents"] >= 1
    fid = r["featured"]["id"]
    o = client.get("/overview").json()
    assert o["business_status"] == "CRITICAL" and o["counts"]["critical"] == 1
    top = o["attention"][0]
    assert top["id"] == fid and top["severity"] == "CRITICAL" and top["stage"] == "DETECTED" and top["entity"]["type"] == "machine"
    assert top["impact"]["value"] > 0 and top["evidence_count"] > 0 and o["impact"]["exposure_total"] >= top["impact"]["value"]
    d = client.post("/react/run", json={"incident_id": fid}).json()
    f = client.get(f"/findings/incident/{fid}").json()
    assert f["stage"] == "AWAITING_APPROVAL" and f["decision"]["id"] == d["decision_id"] and f["investigation"]["selected_hypothesis"] == "BEARING_DEGRADATION"
    assert client.get("/overview").json()["counts"]["pending_decisions"] == 1
    client.post(f"/decisions/{d['decision_id']}/approve", json={"reviewed_by": "plant.manager"})
    o2 = client.get("/overview").json()
    assert o2["business_status"] != "CRITICAL" and any(x["id"] == fid and x["stage"] == "ACTION_COMPLETED" for x in o2["addressed"])
    titles = [a["title"] for a in client.get("/activity").json()]
    assert "Simulated action completed" in titles and "Approved by plant.manager" in titles and "Investigation completed" in titles


def test_demo_is_deterministic(client):
    a = client.post("/demo/run", json={"scenario": "bearing_failure"}).json()
    va = client.get(f"/findings/incident/{a['featured']['id']}").json()["impact"]["value"]
    b = client.post("/demo/run", json={"scenario": "bearing_failure"}).json()
    vb = client.get(f"/findings/incident/{b['featured']['id']}").json()["impact"]["value"]
    assert a["detected"] == b["detected"] and va == vb


def test_demand_spike_demo_features_valve_x(client):
    r = client.post("/demo/run", json={"scenario": "demand_spike"}).json()
    f = client.get(f"/findings/opportunity/{r['featured']['id']}").json()
    assert f["type"] == "DEMAND_OPPORTUNITY" and f["entity"]["name"] == "Industrial Valve X" and f["location"] == "South India"
    assert client.post("/demo/run", json={"scenario": "volcano"}).status_code == 422
    assert {s["scenario"] for s in client.get("/demo/scenarios").json()} >= {"bearing_failure", "demand_spike"}


def test_finding_errors(client):
    assert client.get(f"/findings/incident/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/findings/volcano/{uuid.uuid4()}").status_code == 404


def test_impact_declares_its_total(client):
    r = client.post("/demo/run", json={"scenario": "bearing_failure"}).json()
    d = client.post("/react/run", json={"incident_id": r["featured"]["id"]}).json()
    imp = d["expected_impact"]
    included = imp["total_includes"]
    assert "estimated_revenue_loss" not in included
    assert abs(sum(imp[k]["value"] for k in included) - imp["estimated_total"]) < 1.0
