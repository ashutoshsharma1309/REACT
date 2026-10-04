"""Phase 6 operations endpoints: readiness, the AI-failure drill and evidence-pack artifacts."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.models import Artifact, Decision, SystemState
from app.observability import get_logger, log_event
from app.services import artifact_service, readiness_service
from app.storage import StorageError

router = APIRouter(tags=["operations (phase 6)"])
log = get_logger("react.api.operations")


@router.get("/ready", summary="Readiness: database, migrations, seeded data, storage config, AI provider state. 200 when ready, 503 otherwise.")
def ready(db: Session = Depends(get_db)):
    ok, body = readiness_service.readiness(db)
    return JSONResponse(body, status_code=200 if ok else 503)


class OutageRequest(BaseModel):
    mode: Literal["off", "fallback", "fail"] = Field(description="off = normal; fallback = model unreachable, deterministic fallback used and labelled; fail = model unreachable and no fallback (investigations return 503)")


@router.get("/demo/ai-outage", summary="Current AI-failure drill mode")
def get_outage(db: Session = Depends(get_db)):
    state = db.get(SystemState, 1)
    return {"mode": state.ai_outage_mode if state else "off", "enabled": settings.demo_controls_enabled}


@router.post("/demo/ai-outage", summary="AI-failure drill: make the language model unreachable for new investigations (no real outage, no AWS call)")
def set_outage(payload: OutageRequest, db: Session = Depends(get_db)):
    if not settings.demo_controls_enabled:
        raise HTTPException(403, "demo controls are disabled in this environment (DEMO_CONTROLS_ENABLED=false)")
    state = db.get(SystemState, 1)
    if state is None:
        raise HTTPException(409, "database is not seeded")
    state.ai_outage_mode = payload.mode
    db.commit()
    log_event(log, "ai.outage_drill.set", mode=payload.mode)
    return {"mode": payload.mode, "enabled": True}


class ArtifactOut(BaseModel):
    id: uuid.UUID
    kind: str
    decision_id: uuid.UUID | None
    trigger: str
    status: str
    storage_backend: str
    content_type: str
    size_bytes: int | None
    sha256: str | None
    error: str | None
    created_at: str
    download_path: str | None


def _out(a: Artifact) -> dict:
    return {"id": a.id, "kind": a.kind, "decision_id": a.decision_id, "trigger": a.trigger, "status": a.status, "storage_backend": a.storage_backend, "content_type": a.content_type, "size_bytes": a.size_bytes, "sha256": a.sha256, "error": a.error, "created_at": a.created_at.isoformat(), "download_path": f"/artifacts/{a.id}/download" if a.status == "STORED" else None}


@router.get("/decisions/{decision_id}/artifacts", response_model=list[ArtifactOut], summary="Evidence packs written for this decision")
def decision_artifacts(decision_id: uuid.UUID, db: Session = Depends(get_db)):
    if db.get(Decision, decision_id) is None:
        raise HTTPException(404, "decision not found")
    return [_out(a) for a in artifact_service.list_for_decision(db, decision_id)]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
def artifact(artifact_id: uuid.UUID, db: Session = Depends(get_db)):
    a = db.get(Artifact, artifact_id)
    if a is None:
        raise HTTPException(404, "artifact not found")
    return _out(a)


@router.get("/artifacts/{artifact_id}/download", summary="Download an evidence pack. The SHA-256 is verified before the bytes are returned.")
def download(artifact_id: uuid.UUID, db: Session = Depends(get_db)):
    a = db.get(Artifact, artifact_id)
    if a is None:
        raise HTTPException(404, "artifact not found")
    try:
        data = artifact_service.read_artifact(a)
    except StorageError as exc:
        status = 404 if "not found" in str(exc) or "was not stored" in str(exc) else 502
        raise HTTPException(status, str(exc)) from exc
    return Response(data, media_type=a.content_type, headers={"Content-Disposition": f'attachment; filename="react-evidence-pack-{a.decision_id}.json"', "X-Content-SHA256": a.sha256 or "", "Cache-Control": "no-store"})
