from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.orchestrator import Orchestrator, OrchestratorError
from app.api.deps import get_db
from app.schemas.agents import AgentRunCreate, AgentRunDetailOut, AgentRunOut, AgentToolCallOut, AgentTraceEntryOut
from app.schemas.common import Page
from app.services import agent_service as svc

router = APIRouter(tags=["agents (phase 3)"])


def _detail(db: Session, run_id: uuid.UUID):
    run = svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(404, "agent run not found")
    run.trace = run.trace_entries  # type: ignore[attr-defined]
    return run


def _start(db: Session, payload: AgentRunCreate):
    orch = Orchestrator(db)
    try:
        run = orch.start(incident_id=payload.incident_id, opportunity_id=payload.opportunity_id, risk_id=payload.risk_id)
    except OrchestratorError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 400, str(exc)) from exc
    if payload.run:
        run = orch.run(run.id)  # failures are captured in the run (status FAILED), never raised to the client
    return _detail(db, run.id)


@router.get("/agents", summary="Agent roster, tool policies, state machine and provider configuration")
def agents(db: Session = Depends(get_db)):
    from app.domains import current_domain

    return svc.roster(current_domain(db))


@router.post("/agent-runs", response_model=AgentRunDetailOut, status_code=201, summary="Create (and by default run) an agent workflow for an incident, opportunity or risk")
def create_run(payload: AgentRunCreate, db: Session = Depends(get_db)):
    return _start(db, payload)


@router.get("/agent-runs", response_model=Page[AgentRunOut])
def list_runs(status: str | None = None, subject_type: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_runs(db, status, subject_type, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/agent-runs/{run_id}", response_model=AgentRunDetailOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    return _detail(db, run_id)


@router.post("/agent-runs/{run_id}/run", response_model=AgentRunDetailOut, summary="Execute a CREATED agent run")
def execute_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    _detail(db, run_id)
    try:
        Orchestrator(db).run(run_id)
    except OrchestratorError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _detail(db, run_id)


@router.get("/agent-runs/{run_id}/trace", response_model=list[AgentTraceEntryOut])
def trace(run_id: uuid.UUID, db: Session = Depends(get_db)):
    return _detail(db, run_id).trace_entries


@router.get("/agent-runs/{run_id}/tool-calls", response_model=list[AgentToolCallOut])
def tool_calls(run_id: uuid.UUID, db: Session = Depends(get_db)):
    return _detail(db, run_id).tool_calls


@router.get("/agent-runs/{run_id}/result", summary="Final structured InvestigationResult")
def result(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = _detail(db, run_id)
    return {"agent_run_id": run.id, "status": run.status.value, "result_status": run.result_status, "result": run.result}


# ---- subject-scoped shortcuts: create + run the full REACT workflow ---------------------------------------
@router.post("/incidents/{incident_id}/react", response_model=AgentRunDetailOut, status_code=201, summary="Run the REACT agent workflow for an incident")
def react_incident(incident_id: uuid.UUID, db: Session = Depends(get_db)):
    return _start(db, AgentRunCreate(incident_id=incident_id, run=True))


@router.post("/opportunities/{opportunity_id}/react", response_model=AgentRunDetailOut, status_code=201)
def react_opportunity(opportunity_id: uuid.UUID, db: Session = Depends(get_db)):
    return _start(db, AgentRunCreate(opportunity_id=opportunity_id, run=True))


@router.post("/risks/{risk_id}/react", response_model=AgentRunDetailOut, status_code=201)
def react_risk(risk_id: uuid.UUID, db: Session = Depends(get_db)):
    return _start(db, AgentRunCreate(risk_id=risk_id, run=True))


@router.get("/incidents/{incident_id}/agent-run", response_model=AgentRunDetailOut)
def incident_agent_run(incident_id: uuid.UUID, db: Session = Depends(get_db)):
    run = svc.latest_for_subject(db, incident_id=incident_id)
    if run is None:
        raise HTTPException(404, "no agent run for this incident")
    run.trace = run.trace_entries  # type: ignore[attr-defined]
    return run


@router.get("/opportunities/{opportunity_id}/agent-run", response_model=AgentRunDetailOut)
def opportunity_agent_run(opportunity_id: uuid.UUID, db: Session = Depends(get_db)):
    run = svc.latest_for_subject(db, opportunity_id=opportunity_id)
    if run is None:
        raise HTTPException(404, "no agent run for this opportunity")
    run.trace = run.trace_entries  # type: ignore[attr-defined]
    return run
