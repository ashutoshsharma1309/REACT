"""Operational anomaly + business risk detection, incident creation, dedupe and resolution."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.database.base import utcnow
from app.detection import DetectionEngine
from app.models import BusinessRisk, DetectionRun, Incident, Machine, ProductionMetric, SensorReading, SystemState
from app.models.enums import DetectionSource, IncidentStatus, IncidentType, Severity
from app.simulation.engine import SimulationEngine


def incidents(db, type_=None):
    stmt = select(Incident).where(Incident.status != IncidentStatus.RESOLVED)
    if type_:
        stmt = stmt.where(Incident.incident_type == type_)
    return db.scalars(stmt).all()


def risks(db, type_=None):
    stmt = select(BusinessRisk).where(BusinessRisk.status != "RESOLVED")
    if type_:
        stmt = stmt.where(BusinessRisk.risk_type == type_)
    return db.scalars(stmt).all()


def test_baseline_is_quiet(db):
    run = DetectionEngine(db).run()
    assert run.status.value == "COMPLETED"
    assert run.incidents_created == 0, run.summary
    assert run.risks_created == 0, run.summary


def test_bearing_failure_creates_correlated_incident(db):
    sim = SimulationEngine(db).run("bearing_failure")
    run = DetectionEngine(db).run()
    degr = incidents(db, "MACHINE_DEGRADATION")
    assert len(degr) == 1
    inc = degr[0]
    assert inc.severity in (Severity.HIGH, Severity.CRITICAL)
    assert str(inc.machine_id) == sim.parameters["machine_id"]
    assert inc.source == DetectionSource.OPERATIONAL_ANOMALY_ENGINE and inc.status == IncidentStatus.DETECTED
    assert inc.simulation_run_id == sim.id and inc.detection_run_id == run.id
    assert {"vibration", "temperature"} <= set(inc.affected_metrics)
    assert inc.estimated_impact["value"] > 0 and inc.estimated_impact["is_estimate"] and inc.estimated_impact["basis"] == "SYNTHETIC"
    # traceability: evidence points at real records of the right machine / line
    rec = inc.evidence["records"]
    machine = db.get(Machine, inc.machine_id)
    sensor_ids = {str(s.id) for s in machine.sensors}
    assert set(rec["sensor_ids"]) <= sensor_ids
    for rid in rec["peak_reading_ids"]:
        r = db.get(SensorReading, rid)
        assert r is not None and str(r.sensor_id) in sensor_ids
    for mid in rec["production_metric_ids"]:
        m = db.get(ProductionMetric, uuid.UUID(mid))
        assert m is not None and m.production_line_id == machine.production_line_id
    assert inc.evidence["likely_component"]["type"] == "BEARING"
    assert inc.evidence["similar_historical_incidents"]
    # no sensor fault should be raised for a genuine machine problem
    assert not incidents(db, "SENSOR_FAULT")


def test_sensor_failure_is_classified_as_sensor_fault(db):
    sim = SimulationEngine(db).run("sensor_failure", {"mode": "stuck", "hours": 36})
    DetectionEngine(db).run()
    faults = incidents(db, "SENSOR_FAULT")
    assert len(faults) == 1
    assert faults[0].evidence["sensor"]["fault"] == "stuck"
    assert faults[0].evidence["sensor"]["sensor_id"] == sim.parameters["sensor_id"]
    assert faults[0].estimated_impact["value"] == 0
    assert not incidents(db, "MACHINE_DEGRADATION") and not incidents(db, "ABNORMAL_TEMPERATURE")
    SimulationEngine(db).reset()
    SimulationEngine(db).run("sensor_failure", {"mode": "spike", "hours": 24})
    DetectionEngine(db).run()
    assert incidents(db, "SENSOR_FAULT")[0].evidence["sensor"]["fault"] == "implausible"
    assert not incidents(db, "MACHINE_DEGRADATION")


def test_supplier_delay_risks(db):
    sim = SimulationEngine(db).run("supplier_delay", {"delay_days": 10})
    DetectionEngine(db).run()
    sd = risks(db, "SUPPLIER_DELAY")
    assert sd and str(sd[0].entity_id) == sim.parameters["supplier_id"]
    assert sd[0].severity == Severity.CRITICAL  # sole source with stock below safety
    assert sd[0].evidence["records"]["supplier_batch_ids"] and sd[0].estimated_impact["value"] > 0
    shortage = [r for r in risks(db, "INVENTORY_SHORTAGE") if r.entity_name == "Electronic Actuator"]
    assert shortage and shortage[0].evidence["snapshots_14d"]
    assert risks(db, "DELIVERY_DELAY")


def test_demand_spike_and_shortage_risks(db):
    SimulationEngine(db).run("demand_spike")
    DetectionEngine(db).run()
    assert any("Industrial Valve X" in r.title for r in risks(db, "INVENTORY_SHORTAGE"))


def test_inventory_shortage_risk(db):
    SimulationEngine(db).run("inventory_shortage", {"level": 0.2})
    DetectionEngine(db).run()
    r = [x for x in risks(db, "INVENTORY_SHORTAGE") if "Industrial Valve X" in x.title]
    assert r and r[0].severity == Severity.CRITICAL
    assert r[0].evidence["records"]["order_ids"] is not None


def test_bottleneck_risks(db):
    sim = SimulationEngine(db).run("production_bottleneck")
    DetectionEngine(db).run()
    b = risks(db, "PRODUCTION_BOTTLENECK")
    assert b and str(b[0].entity_id) == sim.parameters["production_line_id"]
    assert b[0].supporting_metrics["utilization_recent"] >= 0.9
    assert risks(db, "DELIVERY_DELAY")


def test_demand_drop_risks(db):
    SimulationEngine(db).run("demand_drop", {"factor": 0.4, "days": 28, "discount": 0.12})
    DetectionEngine(db).run()
    rd = risks(db, "REVENUE_DECLINE")
    assert any(r.entity_name == "Pump Components" for r in rd)
    cat_risk = next(r for r in rd if r.entity_name == "Pump Components")
    assert cat_risk.evidence["records"]["product_ids"] and cat_risk.estimated_impact["formula"].startswith("revenue_impact")
    assert any(r.entity_type == "product" for r in risks(db, "MARGIN_DECLINE"))


def test_detection_dedupes_and_resolves(db):
    SimulationEngine(db).run("bearing_failure")
    run1 = DetectionEngine(db).run()
    inc = incidents(db, "MACHINE_DEGRADATION")[0]
    # a stale open incident from this engine that is no longer detected must be resolved
    stale = Incident(incident_type=IncidentType.ABNORMAL_POWER, source=DetectionSource.OPERATIONAL_ANOMALY_ENGINE, severity=Severity.LOW, status=IncidentStatus.DETECTED, title="stale", description="stale", dedupe_key="ABNORMAL_POWER:stale", detected_at=utcnow())
    db.add(stale)
    db.commit()
    run2 = DetectionEngine(db).run()
    db.refresh(inc)
    db.refresh(stale)
    assert run2.incidents_created == 0 and run2.incidents_updated >= 1
    assert inc.occurrence_count == 2 and inc.detection_run_id == run2.id
    assert stale.status == IncidentStatus.RESOLVED and stale.resolved_at is not None
    assert db.scalar(select(func.count()).select_from(Incident).where(Incident.incident_type == "MACHINE_DEGRADATION")) == 1
    assert db.get(SystemState, 1).last_detection_id == run2.id
    assert db.get(DetectionRun, run1.id).status.value == "COMPLETED"


def test_incident_status_enum_reserves_agent_states(db):
    assert {"DETECTED", "INVESTIGATING", "AWAITING_DECISION", "AWAITING_APPROVAL", "EXECUTING", "RESOLVED"} == {s.value for s in IncidentStatus}
