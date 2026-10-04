"""API endpoints (schemas, filters, errors) and the end-to-end integration flow through HTTP."""
from __future__ import annotations

import uuid


def test_health_and_status(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["database"] == "UP"
    s = client.get("/system/status").json()
    assert s["seeded"] is True and s["database_status"] == "UP" and s["llm_agents_enabled"] is False
    assert s["record_counts"]["sensor_readings"] > 0 and s["data_window"]["seed"] == 42


def test_entity_endpoints(client):
    assert len(client.get("/companies").json()) == 1
    assert client.get("/plants").json()[0]["name"] == "Bangalore Manufacturing Plant"
    lines = client.get("/production-lines").json()
    assert len(lines) == 3 and all(l["machine_count"] >= 3 for l in lines)
    machines = client.get("/machines").json()
    assert len(machines) >= 10
    detail = client.get(f"/machines/{machines[0]['id']}").json()
    assert detail["sensors"] and detail["components"] and detail["production_line_name"]
    readings = client.get(f"/machines/{machines[0]['id']}/readings", params={"hours": 48, "sensor_type": "TEMPERATURE"}).json()
    assert len(readings["sensors"]) == 1 and readings["sensors"][0]["count"] > 20
    assert client.get(f"/machines/{uuid.uuid4()}").status_code == 404
    assert len(client.get("/products").json()) >= 20
    assert len(client.get("/products", params={"category": "Industrial Valves"}).json()) == 7
    customers = client.get("/customers", params={"region": "South India", "limit": 5}).json()
    assert customers["total"] > 5 and len(customers["items"]) == 5 and all(c["region"] == "South India" for c in customers["items"])
    orders = client.get("/orders", params={"status": "DELIVERED", "limit": 3}).json()
    assert orders["total"] > 100 and all(o["status"] == "DELIVERED" and o["items"] for o in orders["items"])
    inv = client.get("/inventory", params={"item_type": "COMPONENT"}).json()
    assert len(inv) == 10 and all("days_of_cover" in i for i in inv)
    sup = client.get("/suppliers").json()
    assert len(sup) >= 10 and "open_batches" in sup[0]


def test_metrics_business(client):
    m = client.get("/metrics/business", params={"days": 14}).json()
    assert m["revenue"] > 0 and m["orders"] > 0 and len(m["daily"]) == 14
    assert m["revenue_by_category"] and m["line_utilization"] and "change_pct" in m
    assert client.get("/metrics/business", params={"days": 3}).status_code == 422


def test_validation_errors(client):
    assert client.post("/simulation/demand-spike", json={"multiplier": 50}).status_code == 422
    assert client.post("/simulation/sensor-failure", json={"mode": "melt"}).status_code == 400
    assert client.post("/simulation/bearing-failure", json={"machine_id": str(uuid.uuid4())}).status_code == 400


def test_integration_risk_flow(client):
    """simulation -> database -> detection -> incident -> impact, through the API."""
    sim = client.post("/simulation/bearing-failure", json={"days": 7}).json()
    assert sim["run"]["status"] == "COMPLETED" and sim["affected_counts"]["sensors"] >= 3
    det = client.post("/detection/run").json()
    assert det["run"]["incidents_created"] >= 1
    types = {i["type"] for i in det["incidents"]}
    assert "MACHINE_DEGRADATION" in types
    inc = next(i for i in det["incidents"] if i["type"] == "MACHINE_DEGRADATION")
    assert inc["severity"] in ("HIGH", "CRITICAL") and inc["estimated_impact"]["value"] > 0
    detail = client.get(f"/incidents/{inc['incident_id']}").json()
    assert detail["evidence"]["records"]["sensor_reading_ids"] and detail["machine_code"]
    assert detail["simulation_run_id"] == sim["run"]["id"]
    listed = client.get("/incidents", params={"active_only": True, "severity": inc["severity"]}).json()
    assert any(i["incident_id"] == inc["incident_id"] for i in listed["items"])
    status = client.get("/system/status").json()
    assert status["active_incidents"] >= 1 and status["last_detection_run"]["engine"] == "detection" and status["last_simulation"]["scenario"] == "bearing_failure"


def test_integration_opportunity_flow(client):
    sim = client.post("/simulation/demand-spike").json()
    assert sim["run"]["status"] == "COMPLETED" and sim["affected_counts"]["orders"] > 0
    opp = client.post("/opportunity-detection/run").json()
    assert opp["run"]["opportunities_created"] >= 1
    found = [o for o in opp["opportunities"] if o["type"] == "DEMAND_OPPORTUNITY" and "Industrial Valve X" in o["title"]]
    assert found and found[0]["confidence"] in ("HIGH", "MEDIUM") and found[0]["estimated_impact"]["value"] > 0
    detail = client.get(f"/opportunities/{found[0]['opportunity_id']}").json()
    assert detail["product_name"] == "Industrial Valve X" and detail["evidence"]["records"]["order_ids_recent"]
    assert client.get("/opportunities", params={"type": "DEMAND_OPPORTUNITY"}).json()["total"] >= 1
    det = client.post("/detection/run").json()
    assert any(r["type"] == "INVENTORY_SHORTAGE" for r in det["risks"])
    assert client.get("/risks", params={"active_only": True}).json()["total"] >= 1
    assert client.get(f"/opportunities/{uuid.uuid4()}").status_code == 404


def test_reset_endpoint(client):
    client.post("/simulation/bearing-failure")
    r = client.post("/simulation/reset", json={"history_days": 45}).json()
    assert r["run"]["scenario"] == "reset" and r["counts"]["orders"] > 0 and r["seed"] == 42
    assert client.get("/system/status").json()["active_incidents"] == 0
