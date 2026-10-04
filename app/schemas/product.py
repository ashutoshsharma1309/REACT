"""Phase 5 product read-model schemas (business UI)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class EntityRef(BaseModel):
    type: str | None = None
    id: str | None = None
    name: str | None = None
    code: str | None = None


class ImpactSummary(BaseModel):
    value: float
    currency: str
    description: str | None = None
    formula: str | None = None
    basis: str = "SYNTHETIC"


class WorkflowRef(BaseModel):
    id: str
    status: str
    selected_hypothesis: str | None = None
    selected_label: str | None = None
    evidence_support_score: float | None = None
    result_status: str | None = None
    action_type: str | None = None
    decision_readiness: str | None = None
    summary: str | None = None


Stage = Literal["DETECTED", "INVESTIGATED", "AWAITING_APPROVAL", "APPROVED", "ACTION_COMPLETED", "REJECTED", "INSUFFICIENT_EVIDENCE", "FAILED"]


class FindingOut(BaseModel):
    kind: Literal["incident", "risk", "opportunity"]
    id: str
    type: str
    title: str
    description: str
    status: str
    detected_at: datetime
    severity: str | None = None
    confidence: str | None = None
    entity: EntityRef
    location: str | None = None
    impact: ImpactSummary | None = None
    evidence_count: int
    stage: Stage
    investigation: WorkflowRef | None = None
    agent_run: WorkflowRef | None = None
    decision: WorkflowRef | None = None
    signal: str | None = None
    supporting_metrics: dict[str, Any] | None = None


class ActivityItem(BaseModel):
    at: datetime
    kind: Literal["simulation", "scan", "investigation", "decision"]
    title: str
    detail: str | None = None
    status: str | None = None
    link: dict[str, str] | None = None


class OverviewOut(BaseModel):
    company: dict[str, Any]
    as_of: datetime | None
    data_source: str
    scanned: bool
    business_status: Literal["HEALTHY", "NEEDS_ATTENTION", "CRITICAL"]
    headline: str
    counts: dict[str, int]
    attention: list[FindingOut]
    addressed: list[FindingOut]
    opportunities: list[FindingOut]
    impact: dict[str, Any]
    metrics: dict[str, Any] | None
    recent_activity: list[ActivityItem]
    # REACT Merchant
    domain: str = "manufacturing"
    business_health: dict[str, Any] | None = None
    value: dict[str, Any] | None = None
    merchant: dict[str, Any] | None = None


class DemoRequest(BaseModel):
    scenario: Literal["stockout_crisis", "demand_opportunity", "supplier_delay", "margin_leakage", "excess_inventory", "bearing_failure", "demand_spike", "baseline"]


class DemoOut(BaseModel):
    scenario: str
    label: str
    story: str
    simulation_summary: str | None
    detected: dict[str, int]
    featured: dict[str, str] | None


class ScanOut(BaseModel):
    incidents_created: int
    risks_created: int
    opportunities_created: int
