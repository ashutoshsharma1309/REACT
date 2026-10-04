"""Health and system status."""
from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.database.base import Base, utcnow
from app.models import BusinessRisk, DetectionRun, Incident, Opportunity, SimulationRun, SystemState

COUNTED_TABLES = ["companies", "plants", "production_lines", "machines", "machine_components", "sensors", "sensor_readings", "production_metrics", "products", "customers", "orders", "order_items", "sales_transactions", "inventory", "suppliers", "supplier_batches", "maintenance_records", "historical_incidents", "incidents", "business_risks", "opportunities", "financial_metrics", "market_signals", "competitor_signals", "simulation_runs", "detection_runs"]


def database_status(db: Session) -> str:
    try:
        db.execute(text("SELECT 1"))
        return "UP"
    except Exception:  # noqa: BLE001
        return "DOWN"


def health(db: Session, app_name: str, version: str) -> dict:
    status = database_status(db)
    return {"status": "ok" if status == "UP" else "degraded", "app": app_name, "version": version, "database": status, "timestamp": utcnow()}


def _run_dict(run) -> dict | None:
    if run is None:
        return None
    d = {"id": str(run.id), "status": run.status.value, "started_at": run.started_at, "finished_at": run.finished_at}
    if isinstance(run, SimulationRun):
        d.update({"scenario": run.scenario.value, "summary": run.effects.get("summary"), "affected": {k: len(v) for k, v in run.affected_entities.items()}})
    else:
        d.update({"engine": run.engine, "incidents_created": run.incidents_created, "incidents_updated": run.incidents_updated, "risks_created": run.risks_created, "risks_updated": run.risks_updated, "opportunities_created": run.opportunities_created, "opportunities_updated": run.opportunities_updated})
    return d


def system_status(db: Session) -> dict:
    db_status = database_status(db)
    state = db.get(SystemState, 1) if db_status == "UP" else None
    counts: dict[str, int] = {}
    if db_status == "UP":
        for name in COUNTED_TABLES:
            if name in Base.metadata.tables:
                counts[name] = db.scalar(select(func.count()).select_from(Base.metadata.tables[name])) or 0
    last_sim = db.get(SimulationRun, state.last_simulation_id) if state and state.last_simulation_id else db.scalar(select(SimulationRun).order_by(SimulationRun.started_at.desc()))
    last_det = db.scalar(select(DetectionRun).where(DetectionRun.engine == "detection").order_by(DetectionRun.started_at.desc()))
    last_opp = db.scalar(select(DetectionRun).where(DetectionRun.engine == "opportunity").order_by(DetectionRun.started_at.desc()))
    return {
        "database_status": db_status,
        "seeded": state is not None,
        "simulator_status": state.simulator_status if state else "NOT_SEEDED",
        "detection_engine_status": state.detection_status if state else "NOT_SEEDED",
        "opportunity_engine_status": ("IDLE" if not last_opp or last_opp.status.value != "RUNNING" else "RUNNING") if state else "NOT_SEEDED",
        "total_incidents": counts.get("incidents", 0),
        "active_incidents": db.scalar(select(func.count()).select_from(Incident).where(Incident.status != "RESOLVED")) or 0,
        "total_risks": counts.get("business_risks", 0),
        "active_risks": db.scalar(select(func.count()).select_from(BusinessRisk).where(BusinessRisk.status != "RESOLVED")) or 0,
        "opportunities": counts.get("opportunities", 0),
        "new_opportunities": db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.status == "NEW")) or 0,
        "last_simulation": _run_dict(last_sim),
        "last_detection_run": _run_dict(last_det),
        "last_opportunity_run": _run_dict(last_opp),
        "data_window": {"seed": state.seed, "data_start": state.data_start, "data_end": state.data_end, "seeded_at": state.seeded_at, "generator_version": state.generator_version} if state else None,
        "record_counts": counts,
    }
