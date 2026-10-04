"""REACT - FastAPI application entrypoint."""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError

from app.api import api_router
from app.config import settings
from app.database import SessionLocal, init_schema
from app.database.locks import SEED_LOCK_KEY, advisory_lock
from app.database.session import engine
from app.models import SystemState
from app.observability import configure_logging, get_logger, log_event
from app.observability.metrics import emit
from app.simulation.generator import GeneratorConfig, seed_database
from app.simulation.scenarios.base import ScenarioError
from app.version import APP_VERSION

log = get_logger("react.api")

DESCRIPTION = """
**REACT - Real-time Event Analysis & Crisis Triage.** Phase 1: a realistic synthetic business
environment for *Astra Manufacturing* (fictional) plus a deterministic intelligence layer.

* `POST /simulation/*` alters the dataset with correlated business effects.
* `POST /detection/run` creates **incidents** (operational anomalies) and **risks** (business).
* `POST /opportunity-detection/run` creates **opportunities**.

All data, market signals and impact figures are **SYNTHETIC**. No LLM reasoning is involved in Phase 1.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    if settings.auto_create_schema:
        init_schema()
    if settings.seed_on_startup:
        # Several API containers can boot together on ECS: the advisory lock makes exactly one of them seed.
        with engine.connect() as lock_conn, advisory_lock(lock_conn, SEED_LOCK_KEY):
            with SessionLocal() as db:
                if db.get(SystemState, 1) is None:
                    log_event(log, "startup.seeding", seed=settings.seed, history_days=settings.history_days)
                    seed_database(db, GeneratorConfig(seed=settings.seed, history_days=settings.history_days, sensor_interval_minutes=settings.sensor_interval_minutes))
    log_event(log, "startup.ready", env=settings.app_env, version=APP_VERSION, llm_provider=settings.llm_provider, storage_backend=settings.storage_backend, metrics_enabled=settings.metrics_enabled)
    yield
    log_event(log, "shutdown")


app = FastAPI(title="REACT API", version=APP_VERSION, description=DESCRIPTION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()], allow_methods=["GET", "POST"], allow_headers=["content-type"])
app.include_router(api_router)


class StripPathPrefix:
    """On AWS, CloudFront sends /api/* to the load balancer without rewriting the path. This strips the prefix,
    so /api/overview and /overview reach the same route. Locally the Vite proxy strips it already (a no-op here)."""

    def __init__(self, app, prefix: str) -> None:
        self.app, self.prefix = app, prefix.rstrip("/")

    async def __call__(self, scope, receive, send):
        if self.prefix and scope["type"] in ("http", "websocket"):
            path: str = scope.get("path", "")
            if path == self.prefix or path.startswith(self.prefix + "/"):
                new_path = path[len(self.prefix):] or "/"
                scope = {**scope, "path": new_path, "raw_path": new_path.encode()}
        await self.app(scope, receive, send)


app.add_middleware(StripPathPrefix, prefix=settings.api_path_prefix)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        log_event(log, "error", level=40, request_id=request_id, method=request.method, path=request.url.path, error=str(exc), exc_type=type(exc).__name__)
        emit({"UnhandledError": 1}, path=request.url.path, exc_type=type(exc).__name__)
        # Fail safely: a generic message with a request id. Internals (SQL, stack, hosts) stay in the logs.
        return JSONResponse(status_code=500, content={"detail": f"Internal error. Reference {request_id}."}, headers={"X-Request-ID": request_id})
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    log_event(log, "api.request", request_id=request_id, method=request.method, path=request.url.path, status_code=response.status_code, duration_ms=duration_ms)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(ScenarioError)
async def scenario_error_handler(request: Request, exc: ScenarioError):
    log_event(log, "error", level=30, path=request.url.path, error=str(exc), exc_type="ScenarioError")
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
async def database_unavailable_handler(request: Request, exc: Exception):
    # Database unreachable (RDS failover, network, credentials rotated): say so; never leak the driver message.
    log_event(log, "error", level=40, path=request.url.path, error=str(exc)[:300], exc_type=type(exc).__name__)
    emit({"DatabaseUnavailable": 1}, path=request.url.path)
    return JSONResponse(status_code=503, content={"detail": "The REACT database is temporarily unavailable. No data was changed. Please retry shortly."}, headers={"Retry-After": "30"})


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    log_event(log, "error", level=30, path=request.url.path, error=str(exc), exc_type="ValueError")
    status = 409 if "not seeded" in str(exc).lower() else 400
    return JSONResponse(status_code=status, content={"detail": str(exc)})
