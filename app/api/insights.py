from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import Page
from app.schemas.insights import BusinessRiskOut, IncidentDetailOut, IncidentOut, OpportunityDetailOut, OpportunityOut
from app.schemas.system import BusinessMetricsOut
from app.services import insight_service as svc
from app.services import metrics_service

router = APIRouter(tags=["insights"])


@router.get("/incidents", response_model=Page[IncidentOut])
def incidents(status: str | None = None, severity: str | None = None, type: str | None = None, machine_id: uuid.UUID | None = None, production_line_id: uuid.UUID | None = None, active_only: bool = False, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_incidents(db, status, severity, type, machine_id, production_line_id, active_only, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/incidents/{incident_id}", response_model=IncidentDetailOut)
def incident(incident_id: uuid.UUID, db: Session = Depends(get_db)):
    inc = svc.get_incident(db, incident_id)
    if not inc:
        raise HTTPException(404, "incident not found")
    return inc


@router.get("/risks", response_model=Page[BusinessRiskOut])
def risks(status: str | None = None, severity: str | None = None, type: str | None = None, active_only: bool = False, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_risks(db, status, severity, type, active_only, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/opportunities", response_model=Page[OpportunityOut])
def opportunities(status: str | None = None, type: str | None = None, product_id: uuid.UUID | None = None, region: str | None = None, confidence: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_opportunities(db, status, type, product_id, region, confidence, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityDetailOut)
def opportunity(opportunity_id: uuid.UUID, db: Session = Depends(get_db)):
    opp = svc.get_opportunity(db, opportunity_id)
    if not opp:
        raise HTTPException(404, "opportunity not found")
    return opp


@router.get("/metrics/business", response_model=BusinessMetricsOut)
def business_metrics(days: int = Query(14, ge=7, le=120), db: Session = Depends(get_db)):
    return metrics_service.business_metrics(db, days)
