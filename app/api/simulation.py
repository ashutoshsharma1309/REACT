from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.detection import DetectionEngine
from app.models import BusinessRisk, Incident, Opportunity
from app.opportunities import OpportunityEngine
from app.schemas.insights import DetectionRunResponse, ResetParams, ResetResponse, ScenarioParams, SimulationResponse
from app.simulation.engine import SimulationEngine
from app.simulation.scenarios import SCENARIOS

router = APIRouter(tags=["simulation & detection"])

NEXT_STEP = {
    "bearing_failure": "POST /detection/run to detect the correlated machine degradation incident.",
    "sensor_failure": "POST /detection/run - expect a SENSOR_FAULT incident, not a machine incident.",
    "supplier_delay": "POST /detection/run to surface SUPPLIER_DELAY / INVENTORY_SHORTAGE / DELIVERY_DELAY risks.",
    "demand_spike": "POST /opportunity-detection/run for the demand opportunity, then POST /detection/run for stock-out risk.",
    "demand_drop": "POST /detection/run for REVENUE_DECLINE / MARGIN_DECLINE risks; POST /opportunity-detection/run for excess inventory and spare capacity.",
    "inventory_shortage": "POST /detection/run to surface the INVENTORY_SHORTAGE risk.",
    "production_bottleneck": "POST /detection/run for PRODUCTION_BOTTLENECK / DELIVERY_DELAY / DEMAND_SUPPLY_MISMATCH risks.",
    "power_instability": "POST /detection/run (ABNORMAL_POWER incidents on several machines), then POST /investigations {incident_id, run: true} - expect POWER_INSTABILITY as root cause.",
}


def _run_scenario(db: Session, scenario: str, params: ScenarioParams | None) -> dict:
    run = SimulationEngine(db).run(scenario, params.model_dump(exclude_none=True) if params else {})
    from app.domains import is_merchant

    step = "POST /scan to detect what the event changed (POST /demo/run does both in one call)." if is_merchant(db) else NEXT_STEP[scenario]
    return {"run": run, "summary": run.effects.get("summary", ""), "affected_counts": {k: len(v) for k, v in run.affected_entities.items()}, "next_step": step}


def _make_endpoint(scenario: str):
    def endpoint(params: ScenarioParams | None = Body(default=None), db: Session = Depends(get_db)):
        return _run_scenario(db, scenario, params)

    endpoint.__name__ = f"simulate_{scenario}"
    endpoint.__doc__ = SCENARIOS[scenario].__doc__
    return endpoint


for _scenario in SCENARIOS:
    router.add_api_route(f"/simulation/{_scenario.replace('_', '-')}", _make_endpoint(_scenario), methods=["POST"], response_model=SimulationResponse, summary=f"Simulate {_scenario.replace('_', ' ')}")


@router.post("/simulation/reset", response_model=ResetResponse, summary="Reset the dataset to the deterministic baseline")
def reset(params: ResetParams | None = Body(default=None), db: Session = Depends(get_db)):
    run, summary = SimulationEngine(db).reset(seed=params.seed if params else None, history_days=params.history_days if params else None)
    return {"run": run, "seed": summary.seed, "data_start": summary.data_start, "data_end": summary.data_end, "counts": summary.counts, "duration_seconds": round(summary.duration_seconds, 2)}


@router.post("/detection/run", response_model=DetectionRunResponse, summary="Run operational anomaly + business risk detection")
def run_detection(db: Session = Depends(get_db)):
    run = DetectionEngine(db).run()
    incidents = db.scalars(select(Incident).where(Incident.detection_run_id == run.id).order_by(Incident.severity)).all()
    risks = db.scalars(select(BusinessRisk).where(BusinessRisk.detection_run_id == run.id)).all()
    return {"run": run, "incidents": incidents, "risks": risks}


@router.post("/opportunity-detection/run", response_model=DetectionRunResponse, summary="Run business opportunity detection")
def run_opportunity_detection(db: Session = Depends(get_db)):
    run = OpportunityEngine(db).run()
    opportunities = db.scalars(select(Opportunity).where(Opportunity.detection_run_id == run.id)).all()
    return {"run": run, "opportunities": opportunities}
