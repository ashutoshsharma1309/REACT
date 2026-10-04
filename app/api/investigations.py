from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.investigation import InvestigationEngine
from app.investigation.engine import InvestigationError
from app.investigation.registry import describe_registry
from app.investigation.tools import describe_tools
from app.schemas.common import Page
from app.schemas.insights import BusinessRiskOut
from app.schemas.investigations import EvidenceGraphOut, EvidenceOut, HypothesisOut, InvestigationCreate, InvestigationDetailOut, InvestigationOut, InvestigationReportOut, SubjectEvidenceOut
from app.services import investigation_service as svc

router = APIRouter(tags=["investigations (phase 2)"])


def _get(db: Session, investigation_id: uuid.UUID):
    inv = svc.get_investigation(db, investigation_id)
    if inv is None:
        raise HTTPException(404, "investigation not found")
    return inv


@router.post("/investigations", response_model=InvestigationDetailOut, status_code=201, summary="Create an investigation for an incident, opportunity or risk")
def create_investigation(payload: InvestigationCreate, db: Session = Depends(get_db)):
    engine = InvestigationEngine(db)
    try:
        inv = engine.create(incident_id=payload.incident_id, opportunity_id=payload.opportunity_id, risk_id=payload.risk_id)
        if payload.run:
            inv = engine.run(inv.id)
    except InvestigationError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 400, str(exc)) from exc
    return _get(db, inv.id)


@router.get("/investigations", response_model=Page[InvestigationOut])
def list_investigations(status: str | None = None, subject_type: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_investigations(db, status, subject_type, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/investigations/registry", summary="Hypothesis registry of the active domain (configurable, deterministic)")
def registry(db: Session = Depends(get_db)):
    from app.domains import current_domain

    items = describe_registry(current_domain(db))
    return {"hypotheses": items, "count": len(items)}


@router.get("/investigations/tools", summary="Tool catalogue of the active domain (validated, read-only functions agents may call)")
def tools(db: Session = Depends(get_db)):
    from app.domains import current_domain

    items = describe_tools(current_domain(db))
    return {"tools": items, "count": len(items)}


@router.get("/investigations/{investigation_id}", response_model=InvestigationDetailOut)
def get_investigation(investigation_id: uuid.UUID, db: Session = Depends(get_db)):
    return _get(db, investigation_id)


@router.post("/investigations/{investigation_id}/run", response_model=InvestigationDetailOut, summary="Run (or re-run) the investigation pipeline")
def run_investigation(investigation_id: uuid.UUID, db: Session = Depends(get_db)):
    _get(db, investigation_id)
    try:
        InvestigationEngine(db).run(investigation_id)
    except InvestigationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001  (investigation is marked FAILED; surface a clean error)
        raise HTTPException(500, f"investigation failed: {type(exc).__name__}") from exc
    return _get(db, investigation_id)


@router.get("/investigations/{investigation_id}/hypotheses", response_model=list[HypothesisOut])
def hypotheses(investigation_id: uuid.UUID, db: Session = Depends(get_db)):
    _get(db, investigation_id)
    return svc.list_hypotheses(db, investigation_id)


@router.get("/investigations/{investigation_id}/evidence", response_model=list[EvidenceOut])
def evidence(investigation_id: uuid.UUID, hypothesis_id: uuid.UUID | None = None, supports: bool | None = None, db: Session = Depends(get_db)):
    _get(db, investigation_id)
    return svc.list_evidence(db, investigation_id, hypothesis_id, supports)


@router.get("/investigations/{investigation_id}/report", response_model=InvestigationReportOut)
def report(investigation_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = _get(db, investigation_id)
    if inv.status.value != "COMPLETED":
        raise HTTPException(409, f"investigation is {inv.status.value}; run it first")
    return {"investigation_id": inv.id, "status": inv.status.value, "report": inv.report, "text": inv.report.get("text", "")}


@router.get("/investigations/{investigation_id}/graph", response_model=EvidenceGraphOut, summary="Evidence graph: subject -> signal -> evidence -> hypothesis -> conclusion")
def graph(investigation_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = _get(db, investigation_id)
    return {"investigation_id": inv.id, "graph": inv.graph}


@router.get("/investigations/{investigation_id}/trace")
def trace(investigation_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = _get(db, investigation_id)
    return {"investigation_id": inv.id, "status": inv.status.value, "trace": [{"sequence": t.sequence, "timestamp": t.timestamp, "step": t.step, "message": t.message, "details": t.details, "duration_ms": t.duration_ms} for t in inv.trace_entries]}


# ---- subject-scoped endpoints ---------------------------------------------------------------------
@router.get("/incidents/{incident_id}/investigation", response_model=InvestigationDetailOut)
def incident_investigation(incident_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = svc.latest_for_subject(db, incident_id=incident_id)
    if inv is None:
        raise HTTPException(404, "no investigation for this incident")
    return inv


@router.get("/opportunities/{opportunity_id}/investigation", response_model=InvestigationDetailOut)
def opportunity_investigation(opportunity_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = svc.latest_for_subject(db, opportunity_id=opportunity_id)
    if inv is None:
        raise HTTPException(404, "no investigation for this opportunity")
    return inv


@router.get("/risks/{risk_id}/investigation", response_model=InvestigationDetailOut)
def risk_investigation(risk_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = svc.latest_for_subject(db, risk_id=risk_id)
    if inv is None:
        raise HTTPException(404, "no investigation for this risk")
    return inv


@router.get("/incidents/{incident_id}/evidence", response_model=SubjectEvidenceOut, summary="Detection evidence + latest investigation evidence")
def incident_evidence(incident_id: uuid.UUID, db: Session = Depends(get_db)):
    out = svc.subject_evidence(db, "incident", incident_id)
    if out is None:
        raise HTTPException(404, "incident not found")
    return out


@router.get("/opportunities/{opportunity_id}/evidence", response_model=SubjectEvidenceOut)
def opportunity_evidence(opportunity_id: uuid.UUID, db: Session = Depends(get_db)):
    out = svc.subject_evidence(db, "opportunity", opportunity_id)
    if out is None:
        raise HTTPException(404, "opportunity not found")
    return out


@router.get("/risks/{risk_id}/evidence", response_model=SubjectEvidenceOut)
def risk_evidence(risk_id: uuid.UUID, db: Session = Depends(get_db)):
    out = svc.subject_evidence(db, "risk", risk_id)
    if out is None:
        raise HTTPException(404, "risk not found")
    return out


@router.get("/risks/{risk_id}", response_model=BusinessRiskOut)
def get_risk(risk_id: uuid.UUID, db: Session = Depends(get_db)):
    from app.models import BusinessRisk

    risk = db.get(BusinessRisk, risk_id)
    if risk is None:
        raise HTTPException(404, "risk not found")
    return risk
