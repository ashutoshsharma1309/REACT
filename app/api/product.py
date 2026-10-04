"""Phase 5 product endpoints for the business UI. Thin: logic lives in services."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.detection import DetectionEngine
from app.opportunities import OpportunityEngine
from app.schemas.product import ActivityItem, DemoOut, DemoRequest, FindingOut, OverviewOut, ScanOut
from app.services import demo_service, product_service

router = APIRouter(tags=["product (phase 5)"])


@router.get("/overview", response_model=OverviewOut, summary="Command Center: business status, what needs attention, opportunities, impact, recent activity")
def overview(db: Session = Depends(get_db)):
    return product_service.overview(db)


@router.get("/findings", response_model=list[FindingOut], summary="Unified incidents / risks / opportunities with their REACT workflow stage")
def findings(kind: str | None = Query(None, pattern="^(incident|risk|opportunity|risk_all)$", description="risk_all = incidents + business risks"), include_closed: bool = False, db: Session = Depends(get_db)):
    return product_service.list_findings(db, kind=kind, include_closed=include_closed)


@router.get("/findings/{kind}/{finding_id}", response_model=FindingOut)
def finding(kind: str, finding_id: uuid.UUID, db: Session = Depends(get_db)):
    f = product_service.get_finding(db, kind, finding_id)
    if f is None:
        raise HTTPException(404, f"{kind} not found")
    return f


@router.get("/activity", response_model=list[ActivityItem])
def activity(limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    return product_service.activity(db, limit)


@router.post("/scan", response_model=ScanOut, summary="Run a business scan (risk + opportunity detection) on the current data")
def scan(db: Session = Depends(get_db)):
    det = DetectionEngine(db).run()
    opp = OpportunityEngine(db).run()
    return {"incidents_created": det.incidents_created, "risks_created": det.risks_created, "opportunities_created": opp.opportunities_created}


@router.get("/demo/scenarios")
def demo_scenarios(db: Session = Depends(get_db)):
    return [{"scenario": k, **v} for k, v in demo_service.scenarios_for(db).items()]


@router.post("/demo/run", response_model=DemoOut, summary="Deterministic demo: reset data, apply a scenario, run the business scan")
def demo_run(payload: DemoRequest, db: Session = Depends(get_db)):
    return demo_service.run_demo(db, payload.scenario)
