"""Schemas for incidents, risks, opportunities and engine runs."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class IncidentOut(ORMModel):
    id: uuid.UUID = Field(alias="incident_id")
    incident_type: str = Field(alias="type")
    source: str
    severity: str
    status: str
    machine_id: uuid.UUID | None
    production_line_id: uuid.UUID | None
    detected_at: datetime
    resolved_at: datetime | None
    title: str
    description: str
    affected_metrics: dict[str, Any]
    estimated_impact: dict[str, Any]
    detection_run_id: uuid.UUID | None
    simulation_run_id: uuid.UUID | None
    occurrence_count: int
    is_synthetic: bool

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class IncidentDetailOut(IncidentOut):
    evidence: dict[str, Any]
    machine_code: str | None = None
    production_line_code: str | None = None


class BusinessRiskOut(ORMModel):
    id: uuid.UUID = Field(alias="risk_id")
    risk_type: str = Field(alias="type")
    source: str
    severity: str
    status: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    entity_name: str | None
    detected_at: datetime
    resolved_at: datetime | None
    title: str
    description: str
    supporting_metrics: dict[str, Any]
    estimated_impact: dict[str, Any]
    evidence: dict[str, Any]
    detection_run_id: uuid.UUID | None
    occurrence_count: int
    is_synthetic: bool

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class OpportunityOut(ORMModel):
    id: uuid.UUID = Field(alias="opportunity_id")
    opportunity_type: str = Field(alias="type")
    source: str
    product_id: uuid.UUID | None
    production_line_id: uuid.UUID | None
    customer_segment: str | None
    region: str | None
    detected_at: datetime
    title: str
    description: str
    signal: str
    supporting_metrics: dict[str, Any]
    estimated_impact: dict[str, Any]
    confidence: str
    status: str
    detection_run_id: uuid.UUID | None
    occurrence_count: int
    is_synthetic: bool

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class OpportunityDetailOut(OpportunityOut):
    evidence: dict[str, Any]
    product_name: str | None = None


class DetectionRunOut(ORMModel):
    id: uuid.UUID
    engine: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    window_end: datetime | None
    incidents_created: int
    incidents_updated: int
    risks_created: int
    risks_updated: int
    opportunities_created: int
    opportunities_updated: int
    summary: dict[str, Any]
    error: str | None


class DetectionRunResponse(BaseModel):
    run: DetectionRunOut
    incidents: list[IncidentOut] = []
    risks: list[BusinessRiskOut] = []
    opportunities: list[OpportunityOut] = []
    notice: str = "Phase 1 deterministic engines. Findings and impact figures are SYNTHETIC estimates derived from generated data."


class SimulationRunOut(ORMModel):
    id: uuid.UUID
    scenario: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    window_start: datetime | None
    window_end: datetime | None
    parameters: dict[str, Any]
    affected_entities: dict[str, Any]
    effects: dict[str, Any]
    error: str | None


class SimulationResponse(BaseModel):
    run: SimulationRunOut
    summary: str
    affected_counts: dict[str, int]
    next_step: str


class ScenarioParams(BaseModel):
    """Optional overrides. Every scenario picks a deterministic default target when omitted."""

    machine_id: uuid.UUID | None = None
    sensor_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    production_line_id: uuid.UUID | None = None
    inventory_id: uuid.UUID | None = None
    component_type: str | None = None
    category: str | None = None
    region: str | None = None
    mode: str | None = Field(default=None, description="sensor_failure: stuck | spike | dropout")
    days: int | None = Field(default=None, ge=1, le=60)
    hours: int | None = Field(default=None, ge=1, le=240)
    intensity: float | None = Field(default=None, ge=0.1, le=3.0)
    multiplier: float | None = Field(default=None, ge=1.0, le=10.0)
    demand_multiplier: float | None = Field(default=None, ge=1.0, le=5.0)
    factor: float | None = Field(default=None, ge=0.01, le=1.0)
    discount: float | None = Field(default=None, ge=0.0, le=0.5)
    delay_days: int | None = Field(default=None, ge=1, le=60)
    level: float | None = Field(default=None, ge=0.0, le=1.0)


class ResetParams(BaseModel):
    seed: int | None = None
    history_days: int | None = Field(default=None, ge=30, le=365)


class ResetResponse(BaseModel):
    run: SimulationRunOut
    seed: int
    data_start: datetime
    data_end: datetime
    counts: dict[str, int]
    duration_seconds: float
