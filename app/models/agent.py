"""Phase 3: agent runs, agent trace entries and agent tool calls.

Only structured, auditable information is persisted: actions, tool names and arguments, tool
result metadata, evidence / hypothesis ids, state transitions, concise reasoning summaries and
errors. Hidden chain-of-thought is never stored.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models._types import JSONType, enum_column
from app.models.enums import AgentRunStatus, InvestigationSubject, ToolCallStatus


class AgentRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_subject_status", "subject_type", "status"),)

    subject_type: Mapped[InvestigationSubject] = mapped_column(enum_column(InvestigationSubject), nullable=False)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    risk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_risks.id", ondelete="CASCADE"), index=True)
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("investigations.id", ondelete="SET NULL"), index=True)
    status: Mapped[AgentRunStatus] = mapped_column(enum_column(AgentRunStatus), nullable=False, default=AgentRunStatus.CREATED)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120))
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # False when only the deterministic provider ran
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_calls_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limits: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    detection_analysis: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    investigation_analysis: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    evidence_assessment: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)  # InvestigationResult
    result_status: Mapped[str | None] = mapped_column(String(30))  # CONCLUDED | INSUFFICIENT_EVIDENCE | FAILED
    error: Mapped[str | None] = mapped_column(Text)

    trace_entries: Mapped[list["AgentTraceEntry"]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="AgentTraceEntry.sequence")
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="AgentToolCall.sequence")


class AgentToolCall(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (Index("ix_agent_tool_calls_run_seq", "agent_run_id", "sequence"),)

    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(40), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    purpose: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ToolCallStatus] = mapped_column(enum_column(ToolCallStatus), nullable=False)
    permitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    result_summary: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)  # count, keys, record ids; never the full payload
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    run: Mapped[AgentRun] = relationship(back_populates="tool_calls")


class AgentTraceEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "agent_trace_entries"
    __table_args__ = (Index("ix_agent_trace_run_seq", "agent_run_id", "sequence"),)

    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    agent_name: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)  # state_transition | llm_call | tool_call | validation | retry | fallback | limit | result | error
    state: Mapped[str | None] = mapped_column(String(30))
    tool_name: Mapped[str | None] = mapped_column(String(80))
    tool_arguments: Mapped[dict | None] = mapped_column(JSONType)
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_tool_calls.id", ondelete="SET NULL"))
    evidence_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    hypothesis_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    summary: Mapped[str] = mapped_column(Text, nullable=False)  # concise structured reasoning summary, never chain-of-thought
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text)

    run: Mapped[AgentRun] = relationship(back_populates="trace_entries")
