"""LLM-facing decision schema (no monetary fields) and API schemas for decisions, approvals and actions."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import ORMModel


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Alternative(StrictModel):
    action_type: str
    summary: str
    tradeoff: str


class DecisionRecommendation(StrictModel):
    """Output of the Decision Agent. Contains no money: expected impact is attached by the engine
    from the deterministic impact result. Every id must exist in the supplied context."""

    subject_kind: Literal["incident", "opportunity", "risk"]
    subject_id: str
    decision_readiness: Literal["READY", "NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE"]
    action_type: str | None = Field(description="one of the applicable catalogue action types; null when evidence is insufficient")
    target_type: str | None = None
    target_id: str | None = None
    priority: Literal["P1", "P2", "P3", "P4"] = "P3"
    summary: str = Field(min_length=10)
    problem_statement: str = Field(min_length=10)
    recommendation: str = Field(min_length=10)
    rationale: str = Field(min_length=10, description="must cite evidence ids from the context")
    required_actions: list[str] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    hypothesis_id: str | None = None
    hypothesis_type: str | None = None
    evidence_support_score: float | None = Field(default=None, ge=0, le=1, description="deterministic Evidence Support Score, restated")
    requires_human_approval: bool = True


# ---- API ----------------------------------------------------------------------------------------------
class DecisionCreate(BaseModel):
    agent_run_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    risk_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if sum(1 for x in (self.agent_run_id, self.incident_id, self.opportunity_id, self.risk_id) if x) != 1:
            raise ValueError("exactly one of agent_run_id, incident_id, opportunity_id or risk_id is required")
        return self


class ApprovalRequest(BaseModel):
    reviewed_by: str = Field(min_length=1, max_length=120, description="internal reviewer identity (authentication is a later phase)")
    reason: str | None = None
    execute: bool = Field(default=True, description="execute the simulated action immediately after approval")


class RejectionRequest(BaseModel):
    reviewed_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3)


class ExecuteRequest(BaseModel):
    executed_by: str = Field(default="system", max_length=120)


class ApprovalOut(ORMModel):
    id: uuid.UUID = Field(alias="approval_id")
    decision_id: uuid.UUID
    status: str
    requested_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    reason: str | None
    approval_metadata: dict[str, Any]

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class ActionOut(ORMModel):
    id: uuid.UUID = Field(alias="action_id")
    decision_id: uuid.UUID
    approval_id: uuid.UUID | None
    action_type: str
    target_type: str | None
    target_id: str | None
    target_name: str | None
    status: str
    parameters: dict[str, Any]
    simulated: bool
    result: dict[str, Any]
    executed_by: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    transitions: list[Any]

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class DecisionOut(ORMModel):
    id: uuid.UUID = Field(alias="decision_id")
    agent_run_id: uuid.UUID | None
    investigation_id: uuid.UUID | None
    subject_type: str
    incident_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None
    risk_id: uuid.UUID | None
    status: str
    decision_readiness: str
    action_type: str | None
    target_type: str | None
    target_id: str | None
    target_name: str | None
    priority: str
    summary: str
    recommendation: str
    hypothesis_type: str | None
    hypothesis_label: str | None = None
    evidence_support_score: float | None
    requires_human_approval: bool
    provider: str | None
    llm_used: bool
    fallback_used: bool
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class DecisionDetailOut(DecisionOut):
    problem_statement: str
    rationale: str
    required_actions: list[Any]
    alternatives: list[Any]
    risks: list[Any]
    assumptions: list[Any]
    evidence_ids: list[Any]
    contradicting_evidence_ids: list[Any]
    hypothesis_id: uuid.UUID | None
    expected_impact: dict[str, Any]
    validation: dict[str, Any]
    timeline: list[Any]
    approvals: list[ApprovalOut] = []
    actions: list[ActionOut] = []
    terminology: str = "evidence_support_score is the deterministic Evidence Support Score (not a probability); decision_readiness is a separate rule-based readiness state; expected_impact is copied from the deterministic impact engine."
