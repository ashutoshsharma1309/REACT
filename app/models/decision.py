"""Phase 4: decisions, approvals and (simulated) action executions.

A Decision is derived from a completed agent run (Phase 3) and its investigation (Phase 2). It is
traceable: decision -> agent run -> investigation -> hypothesis -> evidence items -> business
records. Money on a decision is copied from the deterministic impact engine, never produced by an LLM.
Actions are simulated: they create result records, never real-world side effects.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models._types import JSONType, enum_column
from app.models.enums import ActionStatus, ApprovalStatus, DecisionReadiness, DecisionStatus, InvestigationSubject


class Decision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_subject_status", "subject_type", "status"),)

    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True)
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("investigations.id", ondelete="SET NULL"), index=True)
    subject_type: Mapped[InvestigationSubject] = mapped_column(enum_column(InvestigationSubject), nullable=False)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    risk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_risks.id", ondelete="CASCADE"), index=True)
    status: Mapped[DecisionStatus] = mapped_column(enum_column(DecisionStatus), nullable=False, default=DecisionStatus.DECISION_READY)
    decision_readiness: Mapped[DecisionReadiness] = mapped_column(enum_column(DecisionReadiness), nullable=False)
    action_type: Mapped[str | None] = mapped_column(String(40), index=True)  # validated against the action catalogue
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(80))
    target_name: Mapped[str | None] = mapped_column(String(200))
    priority: Mapped[str] = mapped_column(String(4), nullable=False, default="P3")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    problem_statement: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    required_actions: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    alternatives: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    risks: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    assumptions: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    evidence_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    contradicting_evidence_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hypotheses.id", ondelete="SET NULL"))
    hypothesis_type: Mapped[str | None] = mapped_column(String(60))
    evidence_support_score: Mapped[float | None] = mapped_column(Float)  # deterministic, restated
    expected_impact: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)  # copied from investigation.impact (impact engine)
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validation: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    timeline: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)  # decision / approval / action events
    provider: Mapped[str | None] = mapped_column(String(40))
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text)

    approvals: Mapped[list["Approval"]] = relationship(back_populates="decision", cascade="all, delete-orphan", order_by="Approval.requested_at")
    actions: Mapped[list["ActionExecution"]] = relationship(back_populates="decision", cascade="all, delete-orphan", order_by="ActionExecution.created_at")

    @property
    def hypothesis_label(self) -> str | None:
        from app.investigation.registry import label_for

        return label_for(self.hypothesis_type)


class Approval(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_decision_status", "decision_id", "status"),)

    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(enum_column(ApprovalStatus), nullable=False, default=ApprovalStatus.PENDING)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_by: Mapped[str | None] = mapped_column(String(120))  # internal reviewer identity; authentication is a later phase
    reason: Mapped[str | None] = mapped_column(Text)
    approval_metadata: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    decision: Mapped[Decision] = relationship(back_populates="approvals")


class ActionExecution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "action_executions"
    __table_args__ = (Index("ix_action_executions_decision_status", "decision_id", "status"),)

    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id", ondelete="SET NULL"))
    action_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(80))
    target_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[ActionStatus] = mapped_column(enum_column(ActionStatus), nullable=False, default=ActionStatus.PROPOSED)
    parameters: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    result: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    executed_by: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    transitions: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)

    decision: Mapped[Decision] = relationship(back_populates="actions")
