"""Run bookkeeping + a singleton system state row (observability and traceability)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKeyMixin, utcnow
from app.models._types import JSONType, enum_column
from app.models.enums import RunStatus, ScenarioType


class SimulationRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "simulation_runs"

    scenario: Mapped[ScenarioType] = mapped_column(enum_column(ScenarioType), nullable=False, index=True)
    status: Mapped[RunStatus] = mapped_column(enum_column(RunStatus), nullable=False, default=RunStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    window_start: Mapped[datetime | None] = mapped_column(DateTime)
    window_end: Mapped[datetime | None] = mapped_column(DateTime)
    parameters: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    affected_entities: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)  # ids touched, for traceability
    effects: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)  # summary of correlated effects applied
    error: Mapped[str | None] = mapped_column(Text)


class DetectionRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "detection_runs"

    engine: Mapped[str] = mapped_column(String(40), nullable=False, index=True)  # detection | opportunity
    status: Mapped[RunStatus] = mapped_column(enum_column(RunStatus), nullable=False, default=RunStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    window_start: Mapped[datetime | None] = mapped_column(DateTime)
    window_end: Mapped[datetime | None] = mapped_column(DateTime)
    incidents_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incidents_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risks_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risks_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opportunities_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opportunities_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class SystemState(Base):
    """Single row (id=1). Holds the simulated clock and last-run pointers."""

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    data_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    data_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # simulated "now"
    seeded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    simulator_status: Mapped[str] = mapped_column(String(20), nullable=False, default="IDLE")
    detection_status: Mapped[str] = mapped_column(String(20), nullable=False, default="IDLE")
    last_simulation_id: Mapped[uuid.UUID | None] = mapped_column()
    last_simulation_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_detection_id: Mapped[uuid.UUID | None] = mapped_column()
    last_detection_at: Mapped[datetime | None] = mapped_column(DateTime)
    generator_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    # Phase 6 AI-failure drill: off | fallback (LLM unreachable, deterministic fallback) | fail (no fallback)
    ai_outage_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="off", server_default="off")
    # Domain pack that generated this dataset (see app/domains) and, for merchants, the onboarding profile
    domain: Mapped[str] = mapped_column(String(20), nullable=False, default="manufacturing", server_default="manufacturing")
    profile: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default="{}")
