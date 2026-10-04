"""Readiness (Phase 6). /health answers "is the process alive?"; /ready answers "can this instance serve REACT?".

Readiness checks the database, the Alembic migration revision, the seeded dataset and the storage configuration.
It reports the AI provider state but does not fail on it, because REACT has a deterministic path.
It makes NO network call to AWS (no Bedrock or S3 request per probe), so frequent probes cost nothing.
It never returns connection strings, credentials, bucket names or secret ARNs.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.version import APP_VERSION

ROOT = Path(__file__).resolve().parents[2]


def _alembic_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001
        return None


def _check_database(db: Session) -> dict[str, Any]:
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True, "status": "UP"}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {"ok": False, "status": "DOWN", "detail": type(exc).__name__}


def _check_migrations(db: Session) -> dict[str, Any]:
    head = _alembic_head()
    current = None
    try:
        # Check the table exists first so a missing table never aborts the session's transaction.
        exists = db.execute(text("SELECT to_regclass('alembic_version')")).scalar() if db.bind.dialect.name == "postgresql" else None
        if exists:
            current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001
        db.rollback()
    if current is None and settings.auto_create_schema:
        return {"ok": True, "status": "NOT_MANAGED", "detail": "schema created by AUTO_CREATE_SCHEMA (local development)", "head": head}
    return {"ok": current is not None and current == head, "status": "CURRENT" if current == head else "BEHIND", "current": current, "head": head}


def _check_data(db: Session) -> dict[str, Any]:
    from app.models import SystemState

    try:
        state = db.get(SystemState, 1)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {"ok": False, "status": "UNKNOWN", "detail": type(exc).__name__}
    return {"ok": state is not None, "status": "SEEDED" if state else "NOT_SEEDED", "source": "synthetic"}


def _check_storage() -> dict[str, Any]:
    backend = settings.storage_backend.lower()
    if backend == "s3":
        ok = bool(settings.artifacts_bucket)
        return {"ok": ok, "backend": "s3", "status": "CONFIGURED" if ok else "MISCONFIGURED", **({} if ok else {"detail": "ARTIFACTS_BUCKET is not set"})}
    if backend == "local":
        path = Path(settings.storage_local_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
            ok = os.access(path, os.W_OK)
        except OSError:
            ok = False
        return {"ok": ok, "backend": "local", "status": "WRITABLE" if ok else "NOT_WRITABLE"}
    return {"ok": False, "backend": backend, "status": "UNKNOWN_BACKEND"}


def _check_ai(db: Session) -> dict[str, Any]:
    from app.agent_runtime.guard import llm_calls_last_hour, outage_mode
    from app.agents.provider import UnavailableProvider, build_provider

    provider = build_provider()
    info: dict[str, Any] = {"ok": True, **provider.describe(), "fallback_to_deterministic": settings.agent_fallback_to_deterministic}
    status = "RULE_BASED" if not provider.is_llm else "CONFIGURED"
    if isinstance(provider, UnavailableProvider):
        status = "UNAVAILABLE"
    try:
        mode = outage_mode(db)
        info["outage_drill"] = mode
        if mode != "off":
            status = "OUTAGE_DRILL"
        if provider.is_llm and not isinstance(provider, UnavailableProvider):
            info["llm_calls_last_hour"] = llm_calls_last_hour(db)
            info["llm_hourly_call_budget"] = settings.llm_hourly_call_budget
            if info["llm_calls_last_hour"] >= settings.llm_hourly_call_budget:
                status = "BUDGET_EXHAUSTED"
    except Exception:  # noqa: BLE001
        db.rollback()
    info["status"] = status
    info["reachability"] = "not probed (readiness never calls the model)"
    return info


def readiness(db: Session) -> tuple[bool, dict[str, Any]]:
    checks = {"database": _check_database(db)}
    if checks["database"]["ok"]:
        checks["migrations"] = _check_migrations(db)
        checks["data"] = _check_data(db)
    checks["storage"] = _check_storage()
    ready = all(c["ok"] for c in checks.values())
    checks["ai"] = _check_ai(db) if checks["database"]["ok"] else {"ok": True, "status": "UNKNOWN"}
    return ready, {
        "status": "ready" if ready else "not_ready",
        "app": settings.app_name,
        "version": APP_VERSION,
        "environment": settings.app_env,
        "checks": checks,
        "demo_controls_enabled": settings.demo_controls_enabled,
    }
