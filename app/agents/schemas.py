"""Structured agent outputs (Pydantic). Every substantive conclusion carries evidence references.

These schemas deliberately contain NO monetary fields: the LLM cannot emit money. Impact figures are
attached by the orchestrator from the deterministic impact engine (`impact_reference`).
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SubjectKind = Literal["incident", "opportunity", "risk"]
Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AffectedEntity(StrictModel):
    entity_type: str = Field(description="machine | production_line | product | supplier | inventory | customer_segment | region | company")
    entity_id: str | None = None
    name: str | None = None


class DetectionAnalysis(StrictModel):
    """Output of the Detection Agent: interprets an existing finding and frames the investigation."""

    subject_kind: SubjectKind
    subject_id: str
    event_type: str
    severity: Severity
    priority: Literal["P1", "P2", "P3", "P4"]
    affected_entities: list[AffectedEntity] = Field(default_factory=list)
    investigation_objective: str = Field(min_length=10)
    evidence_categories: list[str] = Field(default_factory=list, description="record types worth retrieving, e.g. sensor_readings, maintenance_records")
    missing_information: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=10)


class ToolCallRequest(StrictModel):
    call_id: str = Field(default_factory=lambda: f"tc-{uuid.uuid4().hex[:8]}")
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str | None = None


class ToolCallResult(StrictModel):
    call_id: str
    tool_name: str
    status: Literal["OK", "DENIED", "INVALID_ARGUMENTS", "ERROR", "LIMIT_EXCEEDED"]
    duration_ms: float | None = None
    record_count: int | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class InvestigationPlan(StrictModel):
    objective: str
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    done: bool = False
    rationale_summary: str = ""


class HypothesisAssessment(StrictModel):
    hypothesis_id: str
    hypothesis_type: str
    evidence_support_score: float = Field(ge=0, le=1, description="copied from the deterministic engine, never recomputed")
    assessment: Literal["plausible", "uncertain", "unlikely", "insufficient_evidence"]
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    rationale_summary: str = Field(min_length=5)


class InvestigationAnalysis(StrictModel):
    """Output of the Investigation Agent after the deterministic engine has scored the hypotheses."""

    investigation_id: str
    leading_hypothesis_id: str | None
    leading_hypothesis_type: str | None
    hypotheses: list[HypothesisAssessment]
    insufficient_evidence: bool = False
    additional_evidence_needed: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=10)

    @field_validator("hypotheses")
    @classmethod
    def _non_empty(cls, v):
        if not v:
            raise ValueError("at least one hypothesis assessment is required")
        return v


class EvidenceConflict(StrictModel):
    description: str
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceAssessment(StrictModel):
    """Output of the Evidence Agent: verifies grounding using existing evidence ids and scores."""

    investigation_id: str
    hypothesis_id: str | None
    verified: bool
    sufficient: bool
    evidence_support_score: float | None = Field(default=None, ge=0, le=1, description="the deterministic Evidence Support Score, restated")
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=10)


class ImpactReference(StrictModel):
    investigation_id: str
    estimated_total: float | None
    currency: str | None
    basis: str = "SYNTHETIC"
    is_estimate: bool = True
    source: str = "app/impact/engine.py via investigation.impact"
    components: dict[str, Any] = Field(default_factory=dict)


class InvestigationResult(StrictModel):
    """Final structured output of an agent run."""

    agent_run_id: str
    investigation_id: str | None
    subject_kind: SubjectKind
    subject_id: str
    status: Literal["CONCLUDED", "INSUFFICIENT_EVIDENCE", "FAILED"]
    conclusion: str
    hypothesis_id: str | None
    hypothesis_type: str | None
    evidence_support_score: float | None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    impact_reference: ImpactReference | None = None
    recommended_next_step: str | None = None
    provider: str
    llm_used: bool
    fallback_used: bool
    terminology: str = "evidence_support_score is the deterministic Evidence Support Score (strength of support in the records). It is not a probability."


class LLMToolSpec(StrictModel):
    """Tool description handed to the model (derived from the Phase 2 registry)."""

    name: str
    description: str
    parameters: dict[str, str]
