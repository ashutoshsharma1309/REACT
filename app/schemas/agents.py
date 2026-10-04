"""Phase 3 API schemas for agent runs."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel


class AgentRunCreate(BaseModel):
    incident_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    risk_id: uuid.UUID | None = None
    run: bool = Field(default=True, description="Execute the workflow immediately (synchronously)")

    @model_validator(mode="after")
    def _exactly_one(self):
        if sum(1 for x in (self.incident_id, self.opportunity_id, self.risk_id) if x) != 1:
            raise ValueError("exactly one of incident_id, opportunity_id or risk_id is required")
        return self


class AgentRunOut(ORMModel):
    id: uuid.UUID = Field(alias="agent_run_id")
    subject_type: str
    incident_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None
    risk_id: uuid.UUID | None
    investigation_id: uuid.UUID | None
    status: str
    result_status: str | None
    provider: str
    model: str | None
    llm_used: bool
    fallback_used: bool
    started_at: datetime | None
    completed_at: datetime | None
    iterations: int
    tool_calls_count: int
    llm_calls_count: int
    input_tokens: int
    output_tokens: int
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class AgentToolCallOut(ORMModel):
    id: uuid.UUID = Field(alias="tool_call_id")
    sequence: int
    agent_name: str
    tool_name: str
    arguments: dict[str, Any]
    purpose: str | None
    status: str
    permitted: bool
    duration_ms: float | None
    result_summary: dict[str, Any]
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class AgentTraceEntryOut(ORMModel):
    id: uuid.UUID = Field(alias="trace_id")
    sequence: int
    timestamp: datetime
    agent_name: str
    event_type: str
    state: str | None
    tool_name: str | None
    tool_arguments: dict[str, Any] | None
    tool_call_id: uuid.UUID | None
    evidence_ids: list[Any]
    hypothesis_ids: list[Any]
    summary: str
    details: dict[str, Any]
    duration_ms: float | None
    status: str
    error: str | None

    model_config = {"from_attributes": True, "populate_by_name": True, "serialize_by_alias": True}


class AgentRunDetailOut(AgentRunOut):
    limits: dict[str, Any]
    detection_analysis: dict[str, Any]
    investigation_analysis: dict[str, Any]
    evidence_assessment: dict[str, Any]
    result: dict[str, Any]
    trace: list[AgentTraceEntryOut] = []
    tool_calls: list[AgentToolCallOut] = []
    terminology: str = "evidence_support_score values are the deterministic Evidence Support Score restated by the agents; they are not probabilities. Impact figures come from the deterministic impact engine."
