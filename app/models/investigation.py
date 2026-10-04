"""Phase 2: investigations, hypotheses, evidence items and the investigation trace.

An investigation reads Phase 1 data and writes ONLY to these tables (plus the incident
status transition DETECTED -> INVESTIGATING). Raw business data is never modified.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models._types import JSONType, enum_column
from app.models.enums import HypothesisStatus, InvestigationStatus, InvestigationSubject


class Investigation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "investigations"
    __table_args__ = (Index("ix_investigations_subject", "subject_type", "status"),)

    subject_type: Mapped[InvestigationSubject] = mapped_column(enum_column(InvestigationSubject), nullable=False)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    risk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_risks.id", ondelete="CASCADE"), index=True)
    status: Mapped[InvestigationStatus] = mapped_column(enum_column(InvestigationStatus), nullable=False, default=InvestigationStatus.CREATED)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[str | None] = mapped_column(Text)
    # Evidence Support Score of the leading hypothesis (0..1). NOT a probability.
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_label: Mapped[str | None] = mapped_column(String(40))
    conclusion: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    impact: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    report: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    graph: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    signals: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    hypotheses: Mapped[list["Hypothesis"]] = relationship(back_populates="investigation", cascade="all, delete-orphan", order_by="Hypothesis.rank")
    evidence_items: Mapped[list["EvidenceItem"]] = relationship(back_populates="investigation", cascade="all, delete-orphan")
    trace_entries: Mapped[list["InvestigationTraceEntry"]] = relationship(back_populates="investigation", cascade="all, delete-orphan", order_by="InvestigationTraceEntry.sequence")

    @property
    def key_signals(self) -> list[dict]:
        """The measurements behind the conclusion, phrased for the business owner (REACT Merchant 'Why?')."""
        return list((self.report or {}).get("key_signals", []))


class Hypothesis(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "hypotheses"
    __table_args__ = (Index("ix_hypotheses_investigation_rank", "investigation_id", "rank"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    hypothesis_type: Mapped[str] = mapped_column(String(60), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_signals: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    supporting_conditions: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    contradicting_conditions: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    initial_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # share of trigger weight, sums to 1 across hypotheses
    evidence_support_score: Mapped[float | None] = mapped_column(Float)  # 0..1 after evidence evaluation
    supporting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contradicting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[HypothesisStatus] = mapped_column(enum_column(HypothesisStatus), nullable=False, default=HypothesisStatus.CANDIDATE)
    score_breakdown: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    investigation: Mapped[Investigation] = relationship(back_populates="hypotheses")

    @property
    def label(self) -> str:
        """Plain-language name of the hypothesis from the registry of its domain pack."""
        from app.investigation.registry import label_for

        return label_for(self.hypothesis_type) or ""
    evidence_items: Mapped[list["EvidenceItem"]] = relationship(back_populates="hypothesis", cascade="all, delete-orphan")


class EvidenceItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "evidence_items"
    __table_args__ = (Index("ix_evidence_items_investigation_hypothesis", "investigation_id", "hypothesis_id"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hypotheses.id", ondelete="CASCADE"))
    signal: Mapped[str] = mapped_column(String(80), nullable=False)  # the signal this evidence was derived from
    source_type: Mapped[str] = mapped_column(String(60), nullable=False)  # sensor_reading, production_metric, ...
    source_id: Mapped[str | None] = mapped_column(String(80))  # primary record id (string form)
    source_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)  # all record ids behind the claim
    timestamp: Mapped[datetime | None] = mapped_column(DateTime)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(30))
    baseline_value: Mapped[float | None] = mapped_column(Float)
    relevance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    supports: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contradicts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_support_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_breakdown: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    investigation: Mapped[Investigation] = relationship(back_populates="evidence_items")
    hypothesis: Mapped[Hypothesis | None] = relationship(back_populates="evidence_items")


class InvestigationTraceEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "investigation_trace_entries"
    __table_args__ = (Index("ix_investigation_trace_seq", "investigation_id", "sequence"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    step: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    duration_ms: Mapped[float | None] = mapped_column(Float)

    investigation: Mapped[Investigation] = relationship(back_populates="trace_entries")
