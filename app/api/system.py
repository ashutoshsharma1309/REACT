from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.schemas.system import HealthOut, SystemStatusOut
from app.services import system_service
from app.version import APP_VERSION

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut, summary="Liveness: the process answers. Always 200; reports database status without failing (the load balancer uses this).")
def health(db: Session = Depends(get_db)):
    return system_service.health(db, settings.app_name, APP_VERSION)


@router.get("/system/status", response_model=SystemStatusOut)
def system_status(db: Session = Depends(get_db)):
    return system_service.system_status(db)
