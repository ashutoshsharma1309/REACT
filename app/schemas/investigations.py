"""Phase 2 schemas: investigations, hypotheses, evidence, trace, reports."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel


class InvestigationCreate(BaseModel):
    incident_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    risk_id: uuid.UUID | None = None
    run: bool = Field(default=False, description="Run the investigation immediately after creating it")

    @model_validator(mode="after")
    def _exactly_one(self):
        if sum(1 for x in (self.incident_id, self.opportunity_id, self.risk_id) if x) != 1:
            raise ValueError("exactly one of incident_id, opportunity_id or risk_id is required")
        return self


class HypothesisOut(ORMModel):
    id: uuid.UUID = Field(alias="hypothesis_id")
    hypothesis_type: str = Field(alias="type")
    label: str | None = None
    rank: int
    description: str
    triggered_signals: list[Any]
    supporting_conditions: list[Any]
    contradicting_conditions: list[Any]
    initial_confidence: float
    evidence_support_score: float | None
    supporting_count: int
    contradicting_count: int
    status: str
    score_breakdown: dict[str, Any]

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class EvidenceOut(ORMModel):
    id: uuid.UUID = Field(alias="evidence_id")
    hypothesis_id: uuid.UUID | None
    signal: str
    source_type: str
    source_id: str | None
    source_ids: list[Any]
    timestamp: datetime | None
    claim: str
    value: float | None
    unit: str | None
    baseline_value: float | None
    relevance: float
    supports: bool
    contradicts: bool
    evidence_support_score: float
    score_breakdown: dict[str, Any]
    details: dict[str, Any]

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class TraceEntryOut(ORMModel):
    sequence: int
    timestamp: datetime
    step: str
    message: str
    details: dict[str, Any]
    duration_ms: float | None


class InvestigationOut(ORMModel):
    id: uuid.UUID = Field(alias="investigation_id")
    subject_type: str
    incident_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None
    risk_id: uuid.UUID | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    summary: str | None
    confidence: float | None = Field(description="Evidence Support Score of the leading hypothesis (0..1). Not a probability.")
    confidence_label: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class InvestigationDetailOut(InvestigationOut):
    conclusion: dict[str, Any]
    impact: dict[str, Any]
    hypotheses: list[HypothesisOut] = []
    evidence_count: int = 0
    trace: list[TraceEntryOut] = []
    key_signals: list[dict[str, Any]] = []
    terminology: str = "confidence = Evidence Support Score (strength of support in the available records). It is not a statistical probability or model confidence."


class InvestigationReportOut(BaseModel):
    investigation_id: uuid.UUID
    status: str
    report: dict[str, Any]
    text: str


class EvidenceGraphOut(BaseModel):
    investigation_id: uuid.UUID
    graph: dict[str, Any]


class SubjectEvidenceOut(BaseModel):
    subject_type: str
    subject_id: uuid.UUID
    detection_evidence: dict[str, Any]
    investigation_id: uuid.UUID | None
    investigation_status: str | None
    evidence: list[EvidenceOut] = []
