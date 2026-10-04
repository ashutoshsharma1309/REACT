from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.decisions.actions import ActionExecutionError
from app.decisions.catalogue import describe_catalogue
from app.decisions.engine import AIUnavailable, DecisionEngine, DecisionError
from app.decisions.schemas import ActionOut, ApprovalOut, ApprovalRequest, DecisionCreate, DecisionDetailOut, DecisionOut, ExecuteRequest, RejectionRequest
from app.decisions.state import InvalidTransition
from app.models import ActionExecution, Approval
from app.schemas.common import Page
from app.services import decision_service as svc

router = APIRouter(tags=["decisions & approvals (phase 4)"])


def _detail(db: Session, decision_id: uuid.UUID):
    d = svc.get_decision(db, decision_id)
    if d is None:
        raise HTTPException(404, "decision not found")
    return d


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except AIUnavailable as exc:
        # Fail safe: no guessed answer. 503 tells the client the AI step is unavailable, not that the request was wrong.
        raise HTTPException(503, str(exc), headers={"Retry-After": "60"}) from exc
    except DecisionError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 409 if "requires a COMPLETED" in str(exc) or "no pending" in str(exc) or "no approved" in str(exc) or "no proposed" in str(exc) else 400, str(exc)) from exc
    except (InvalidTransition, ActionExecutionError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/decisions", response_model=DecisionDetailOut, status_code=201, summary="Generate an evidence-backed decision from a completed agent run (or run the full workflow for a subject)")
def create_decision(payload: DecisionCreate, db: Session = Depends(get_db)):
    d = _run(DecisionEngine(db).create, agent_run_id=payload.agent_run_id, incident_id=payload.incident_id, opportunity_id=payload.opportunity_id, risk_id=payload.risk_id)
    return _detail(db, d.id)


@router.post("/react/run", response_model=DecisionDetailOut, status_code=201, summary="Full REACT workflow: detect (existing finding) → investigate → verify → impact → decide → awaiting approval")
def react_run(payload: DecisionCreate, db: Session = Depends(get_db)):
    if payload.agent_run_id:
        raise HTTPException(400, "pass incident_id, opportunity_id or risk_id to run the full workflow")
    d = _run(DecisionEngine(db).create, incident_id=payload.incident_id, opportunity_id=payload.opportunity_id, risk_id=payload.risk_id)
    return _detail(db, d.id)


@router.get("/decisions", response_model=Page[DecisionOut])
def list_decisions(status: str | None = None, subject_type: str | None = None, action_type: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_decisions(db, status, subject_type, action_type, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/decisions/{decision_id}", response_model=DecisionDetailOut)
def get_decision(decision_id: uuid.UUID, db: Session = Depends(get_db)):
    return _detail(db, decision_id)


@router.get("/decisions/{decision_id}/evidence", summary="Evidence chain behind the decision (decision → investigation → hypothesis → evidence → records)")
def decision_evidence(decision_id: uuid.UUID, db: Session = Depends(get_db)):
    return svc.decision_evidence(db, _detail(db, decision_id))


@router.get("/decisions/{decision_id}/trace", summary="Decision timeline + approvals + actions + the agent trace")
def decision_trace(decision_id: uuid.UUID, db: Session = Depends(get_db)):
    return svc.decision_trace(db, _detail(db, decision_id))


@router.post("/decisions/{decision_id}/approve", response_model=DecisionDetailOut, summary="Human approval (optionally executes the simulated action)")
def approve(decision_id: uuid.UUID, payload: ApprovalRequest, db: Session = Depends(get_db)):
    _detail(db, decision_id)
    _run(DecisionEngine(db).approve, decision_id, reviewed_by=payload.reviewed_by, reason=payload.reason, execute=payload.execute)
    return _detail(db, decision_id)


@router.post("/decisions/{decision_id}/reject", response_model=DecisionDetailOut)
def reject(decision_id: uuid.UUID, payload: RejectionRequest, db: Session = Depends(get_db)):
    _detail(db, decision_id)
    _run(DecisionEngine(db).reject, decision_id, reviewed_by=payload.reviewed_by, reason=payload.reason)
    return _detail(db, decision_id)


@router.post("/decisions/{decision_id}/execute", response_model=DecisionDetailOut, summary="Execute the approved (simulated) action")
def execute(decision_id: uuid.UUID, payload: ExecuteRequest | None = None, db: Session = Depends(get_db)):
    _detail(db, decision_id)
    _run(DecisionEngine(db).execute, decision_id, executed_by=(payload.executed_by if payload else "system"))
    return _detail(db, decision_id)


@router.get("/approvals", response_model=Page[ApprovalOut])
def approvals(status: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_approvals(db, status, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
def approval(approval_id: uuid.UUID, db: Session = Depends(get_db)):
    a = db.get(Approval, approval_id)
    if a is None:
        raise HTTPException(404, "approval not found")
    return a


@router.get("/actions/catalogue", summary="Controlled action catalogue of the active domain")
def action_catalogue(db: Session = Depends(get_db)):
    from app.domains import current_domain

    cat = describe_catalogue(current_domain(db))
    return {"actions": cat, "count": len(cat), "note": "Every action is simulated: execution creates a task/proposal record and never a real-world side effect."}


@router.get("/actions", response_model=Page[ActionOut])
def actions(status: str | None = None, action_type: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_actions(db, status, action_type, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/actions/{action_id}", response_model=ActionOut)
def action(action_id: uuid.UUID, db: Session = Depends(get_db)):
    a = db.get(ActionExecution, action_id)
    if a is None:
        raise HTTPException(404, "action not found")
    return a


@router.get("/incidents/{incident_id}/decision", response_model=DecisionDetailOut)
def incident_decision(incident_id: uuid.UUID, db: Session = Depends(get_db)):
    d = svc.latest_for_subject(db, incident_id=incident_id)
    if d is None:
        raise HTTPException(404, "no decision for this incident")
    return d


@router.get("/opportunities/{opportunity_id}/decision", response_model=DecisionDetailOut)
def opportunity_decision(opportunity_id: uuid.UUID, db: Session = Depends(get_db)):
    d = svc.latest_for_subject(db, opportunity_id=opportunity_id)
    if d is None:
        raise HTTPException(404, "no decision for this opportunity")
    return d


@router.get("/risks/{risk_id}/decision", response_model=DecisionDetailOut)
def risk_decision(risk_id: uuid.UUID, db: Session = Depends(get_db)):
    d = svc.latest_for_subject(db, risk_id=risk_id)
    if d is None:
        raise HTTPException(404, "no decision for this risk")
    return d
