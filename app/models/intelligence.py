"""Outputs of the deterministic intelligence layer: incidents, risks, opportunities.

Every record carries an `evidence` JSON payload listing the underlying record ids / metrics
that produced it, so later agent phases can reason over traceable structured evidence.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models._types import JSONType, enum_column
from app.models.enums import (
    Confidence,
    DetectionSource,
    IncidentStatus,
    IncidentType,
    OpportunityStatus,
    OpportunityType,
    RiskStatus,
    RiskType,
    Severity,
)


class HistoricalIncident(Base, UUIDPrimaryKeyMixin):
    """Resolved past incidents from the seeded history (reference / evidence for root-cause work)."""

    __tablename__ = "historical_incidents"
    __table_args__ = (Index("ix_historical_incidents_machine_date", "machine_id", "occurred_at"),)

    incident_type: Mapped[IncidentType] = mapped_column(enum_column(IncidentType), nullable=False)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machines.id", ondelete="SET NULL"))
    production_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("production_lines.id", ondelete="SET NULL"))
    component_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machine_components.id", ondelete="SET NULL"))
    severity: Mapped[Severity] = mapped_column(enum_column(Severity), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    downtime_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    estimated_loss: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    leading_indicators: Mapped[dict | None] = mapped_column(JSONType)  # signatures observed before the incident


class Incident(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_status_severity", "status", "severity"),
        Index("ix_incidents_machine_type_status", "machine_id", "incident_type", "status"),
    )

    incident_type: Mapped[IncidentType] = mapped_column(enum_column(IncidentType), nullable=False)
    source: Mapped[DetectionSource] = mapped_column(enum_column(DetectionSource), nullable=False)
    severity: Mapped[Severity] = mapped_column(enum_column(Severity), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(enum_column(IncidentStatus), nullable=False, default=IncidentStatus.DETECTED)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machines.id", ondelete="SET NULL"), index=True)
    production_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("production_lines.id", ondelete="SET NULL"), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    affected_metrics: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    estimated_impact: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)  # traceability payload
    detection_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("detection_runs.id", ondelete="SET NULL"))
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("simulation_runs.id", ondelete="SET NULL"))
    occurrence_count: Mapped[int] = mapped_column(nullable=False, default=1)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    machine: Mapped["Machine | None"] = relationship()  # noqa: F821
    production_line: Mapped["ProductionLine | None"] = relationship()  # noqa: F821


class BusinessRisk(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "business_risks"
    __table_args__ = (Index("ix_business_risks_type_status", "risk_type", "status"),)

    risk_type: Mapped[RiskType] = mapped_column(enum_column(RiskType), nullable=False)
    source: Mapped[DetectionSource] = mapped_column(enum_column(DetectionSource), nullable=False)
    severity: Mapped[Severity] = mapped_column(enum_column(Severity), nullable=False)
    status: Mapped[RiskStatus] = mapped_column(enum_column(RiskStatus), nullable=False, default=RiskStatus.OPEN)
    entity_type: Mapped[str | None] = mapped_column(String(40))  # product | supplier | production_line | company | order
    entity_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    entity_name: Mapped[str | None] = mapped_column(String(200))
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_metrics: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    estimated_impact: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    detection_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("detection_runs.id", ondelete="SET NULL"))
    occurrence_count: Mapped[int] = mapped_column(nullable=False, default=1)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)


class Opportunity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "opportunities"
    __table_args__ = (Index("ix_opportunities_type_status", "opportunity_type", "status"),)

    opportunity_type: Mapped[OpportunityType] = mapped_column(enum_column(OpportunityType), nullable=False)
    source: Mapped[DetectionSource] = mapped_column(enum_column(DetectionSource), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    production_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("production_lines.id", ondelete="SET NULL"))
    customer_segment: Mapped[str | None] = mapped_column(String(40))
    region: Mapped[str | None] = mapped_column(String(100))
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    signal: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. demand_growth
    supporting_metrics: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    estimated_impact: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    confidence: Mapped[Confidence] = mapped_column(enum_column(Confidence), nullable=False)
    status: Mapped[OpportunityStatus] = mapped_column(enum_column(OpportunityStatus), nullable=False, default=OpportunityStatus.NEW)
    detection_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("detection_runs.id", ondelete="SET NULL"))
    occurrence_count: Mapped[int] = mapped_column(nullable=False, default=1)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    product: Mapped["Product | None"] = relationship()  # noqa: F821
