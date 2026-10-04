"""Orchestrator: state machine, limits, failure handling, trace persistence, final structured result.

    CREATED -> CLASSIFYING (Detection Agent) -> INVESTIGATING (Investigation Agent + Phase 2 engine)
            -> VERIFYING (Evidence Agent) -> COMPLETED ;  any -> FAILED
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agent_runtime.guard import guard_provider
from app.agent_runtime.limits import AgentLimits, LimitExceeded, LimitTracker
from app.agent_runtime.tool_executor import ToolExecutor
from app.agent_runtime.trace import Tracer
from app.agents.base import AgentContext, AgentFailure
from app.agents.detection import DetectionAgent
from app.agents.evidence import EvidenceAgent
from app.agents.investigation import InvestigationAgent
from app.agents.provider import LLMProvider, UnavailableProvider, build_provider
from app.agents.schemas import ImpactReference, InvestigationResult
from app.agents.state import assert_transition
from app.config import settings
from app.database.base import utcnow
from app.models import AgentRun, BusinessRisk, Incident, Investigation, Opportunity
from app.models.enums import AgentName, AgentRunStatus, InvestigationSubject
from app.observability import get_logger, log_event
from app.observability.metrics import emit

log = get_logger("react.agents.orchestrator")


class OrchestratorError(ValueError):
    pass


class Orchestrator:
    def __init__(self, db: Session, provider: LLMProvider | None = None, limits: AgentLimits | None = None, fallback_enabled: bool | None = None) -> None:
        self.db = db
        self.provider = provider or build_provider()
        self.limits = limits or AgentLimits.from_settings()
        self.fallback_enabled = settings.agent_fallback_to_deterministic if fallback_enabled is None else fallback_enabled

    def _apply_guard(self) -> str | None:
        """Outage drill / hourly LLM budget (Phase 6). Returns the reason when the provider was replaced."""
        g = guard_provider(self.db, self.provider, self.fallback_enabled)
        self.provider, self.fallback_enabled = g.provider, g.fallback_enabled
        return self.provider.reason if isinstance(self.provider, UnavailableProvider) else None

    # ---- lifecycle ----------------------------------------------------------------------------------
    def start(self, incident_id: uuid.UUID | None = None, opportunity_id: uuid.UUID | None = None, risk_id: uuid.UUID | None = None) -> AgentRun:
        if not settings.agents_enabled:
            raise OrchestratorError("agents are disabled (AGENTS_ENABLED=false)")
        given = [x for x in (incident_id, opportunity_id, risk_id) if x]
        if len(given) != 1:
            raise OrchestratorError("exactly one of incident_id, opportunity_id or risk_id is required")
        if incident_id:
            subject, kind = self.db.get(Incident, incident_id), InvestigationSubject.INCIDENT
        elif opportunity_id:
            subject, kind = self.db.get(Opportunity, opportunity_id), InvestigationSubject.OPPORTUNITY
        else:
            subject, kind = self.db.get(BusinessRisk, risk_id), InvestigationSubject.RISK
        if subject is None:
            raise OrchestratorError(f"{kind.value.lower()} {given[0]} not found")
        guard_reason = self._apply_guard()
        run = AgentRun(subject_type=kind, incident_id=incident_id, opportunity_id=opportunity_id, risk_id=risk_id, status=AgentRunStatus.CREATED, provider=self.provider.name, model=self.provider.model, llm_used=False, limits=self.limits.as_dict())
        self.db.add(run)
        self.db.flush()
        tracer = Tracer(self.db, run)
        tracer.record(AgentName.ORCHESTRATOR.value, "state_transition", f"Agent run created for {kind.value.lower()} {given[0]} (provider {self.provider.name})", state="CREATED", subject_id=str(given[0]))
        if guard_reason:
            tracer.record(AgentName.ORCHESTRATOR.value, "provider_unavailable", guard_reason, state="CREATED", status="warning", fallback_enabled=self.fallback_enabled)
        self.db.commit()
        self.db.refresh(run)
        log_event(log, "agent_run.created", agent_run_id=run.id, subject_type=kind.value, subject_id=given[0], provider=self.provider.name)
        return run

    def start_and_run(self, **kwargs: Any) -> AgentRun:
        return self.run(self.start(**kwargs).id)

    def run(self, run_id: uuid.UUID) -> AgentRun:
        run = self.db.scalar(select(AgentRun).options(selectinload(AgentRun.trace_entries), selectinload(AgentRun.tool_calls)).where(AgentRun.id == run_id))
        if run is None:
            raise OrchestratorError(f"agent run {run_id} not found")
        if run.status != AgentRunStatus.CREATED:
            raise OrchestratorError(f"agent run is {run.status.value}; only CREATED runs can be started")
        subject = self._subject(run)
        tracer = Tracer(self.db, run)
        guard_reason = self._apply_guard()
        if guard_reason and run.provider != self.provider.name:
            run.provider, run.model = self.provider.name, self.provider.model
            tracer.record(AgentName.ORCHESTRATOR.value, "provider_unavailable", guard_reason, state=run.status.value, status="warning", fallback_enabled=self.fallback_enabled)
        limits = LimitTracker(self.limits)
        ctx = AgentContext(db=self.db, run=run, tracer=tracer, limits=limits, provider=self.provider, executor=ToolExecutor(self.db, run, tracer, limits), fallback_enabled=self.fallback_enabled, max_retries=self.limits.max_retries)
        run.started_at = utcnow()
        log_event(log, "agent_run.started", agent_run_id=run.id, provider=self.provider.name, model=self.provider.model)
        try:
            self._transition(run, tracer, AgentRunStatus.CLASSIFYING)
            detection = DetectionAgent(ctx).run(subject)
            run.detection_analysis = detection.model_dump()
            self.db.commit()

            self._transition(run, tracer, AgentRunStatus.INVESTIGATING)
            inv, analysis, _ = InvestigationAgent(ctx).run(subject, detection)
            run.investigation_analysis = analysis.model_dump()
            self.db.commit()

            self._transition(run, tracer, AgentRunStatus.VERIFYING)
            assessment = EvidenceAgent(ctx).run(inv, analysis)
            run.evidence_assessment = assessment.model_dump()

            result = self._final_result(run, subject, inv, analysis, assessment)
            run.result = result.model_dump()
            run.result_status = result.status
            self._transition(run, tracer, AgentRunStatus.COMPLETED)
            run.completed_at = utcnow()
            tracer.record(AgentName.ORCHESTRATOR.value, "result", f"{result.status}: {result.conclusion[:200]}", state="COMPLETED", evidence_ids=result.supporting_evidence_ids, hypothesis_ids=[result.hypothesis_id] if result.hypothesis_id else [], **limits.snapshot())
            self.db.commit()
            self.db.refresh(run)
            log_event(log, "agent_run.completed", agent_run_id=run.id, result_status=result.status, hypothesis=result.hypothesis_type, evidence_support_score=result.evidence_support_score, llm_used=run.llm_used, fallback_used=run.fallback_used, tool_calls=run.tool_calls_count, llm_calls=run.llm_calls_count, duration_s=round(limits.elapsed_seconds, 2))
            self._metrics(run, limits, failed=False)
            return run
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            run = self.db.get(AgentRun, run_id)
            tracer = Tracer(self.db, run)
            reason = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, LimitExceeded):
                result_status = "INSUFFICIENT_EVIDENCE"
            else:
                result_status = "FAILED"
            run.status = AgentRunStatus.FAILED
            run.error = reason[:2000]
            run.result_status = result_status
            run.completed_at = utcnow()
            run.result = InvestigationResult(agent_run_id=str(run.id), investigation_id=str(run.investigation_id) if run.investigation_id else None, subject_kind=run.subject_type.value.lower(), subject_id=str(run.incident_id or run.opportunity_id or run.risk_id), status=result_status, conclusion=("Agent run stopped by execution limits before a grounded conclusion could be reached." if result_status == "INSUFFICIENT_EVIDENCE" else f"Agent run failed: {reason}"), hypothesis_id=None, hypothesis_type=None, evidence_support_score=None, provider=self.provider.name, llm_used=run.llm_used, fallback_used=run.fallback_used).model_dump()
            tracer.record(AgentName.ORCHESTRATOR.value, "limit" if isinstance(exc, LimitExceeded) else "error", f"Run failed: {reason[:300]}", state="FAILED", status="error", error=reason, agent=getattr(exc, "agent", None))
            self.db.commit()
            self.db.refresh(run)
            log_event(log, "agent_run.failed", level=40, agent_run_id=run.id, error=reason, exc_type=type(exc).__name__)
            self._metrics(run, limits, failed=True, ai_unavailable=isinstance(exc, AgentFailure))
            return run

    # ---- helpers --------------------------------------------------------------------------------------
    def _metrics(self, run: AgentRun, limits: LimitTracker, failed: bool, ai_unavailable: bool = False) -> None:
        values = {"AgentRunFailed" if failed else "AgentRunCompleted": 1, "AgentRunDurationMs": round(limits.elapsed_seconds * 1000, 1)}
        if run.llm_used:
            values.update({"LLMCalls": run.llm_calls_count, "LLMInputTokens": run.input_tokens, "LLMOutputTokens": run.output_tokens})
        if run.fallback_used:
            values["FallbackUsed"] = 1
        if ai_unavailable:
            values["AIUnavailable"] = 1
        emit(values, agent_run_id=run.id, provider=run.provider)

    def _transition(self, run: AgentRun, tracer: Tracer, new: AgentRunStatus) -> None:
        assert_transition(run.status, new)
        old = run.status
        run.status = new
        tracer.record(AgentName.ORCHESTRATOR.value, "state_transition", f"{old.value} -> {new.value}", state=new.value)
        self.db.commit()

    def _subject(self, run: AgentRun):
        if run.incident_id:
            s = self.db.get(Incident, run.incident_id)
        elif run.opportunity_id:
            s = self.db.get(Opportunity, run.opportunity_id)
        else:
            s = self.db.get(BusinessRisk, run.risk_id)
        if s is None:
            raise OrchestratorError("subject of the agent run no longer exists")
        return s

    def _final_result(self, run: AgentRun, subject, inv: Investigation, analysis, assessment) -> InvestigationResult:
        conclusion = inv.conclusion or {}
        lead = next((h for h in analysis.hypotheses if h.hypothesis_id == analysis.leading_hypothesis_id), None)
        determined = bool(conclusion.get("determined"))
        concluded = determined and assessment.sufficient and assessment.verified and not analysis.insufficient_evidence and lead is not None
        impact = inv.impact or {}
        impact_ref = ImpactReference(investigation_id=str(inv.id), estimated_total=impact.get("estimated_total"), currency=impact.get("currency"), components={k: v.get("value") for k, v in impact.items() if isinstance(v, dict) and "value" in v})
        if concluded:
            text = conclusion.get("statement") or f"The available evidence supports {lead.hypothesis_type}."
        else:
            reasons = list(assessment.missing_evidence) + list(analysis.additional_evidence_needed)
            text = "Insufficient evidence for a grounded conclusion." + (f" Leading hypothesis {lead.hypothesis_type} (Evidence Support Score {lead.evidence_support_score:.2f}) is not adequately separated or verified. " if lead else " ") + (" ".join(reasons[:3]))
        return InvestigationResult(
            agent_run_id=str(run.id), investigation_id=str(inv.id), subject_kind=run.subject_type.value.lower(), subject_id=str(subject.id), status="CONCLUDED" if concluded else "INSUFFICIENT_EVIDENCE",
            conclusion=text, hypothesis_id=lead.hypothesis_id if lead else None, hypothesis_type=lead.hypothesis_type if lead else None, evidence_support_score=lead.evidence_support_score if lead else None,
            supporting_evidence_ids=assessment.supporting_evidence_ids, contradicting_evidence_ids=assessment.contradicting_evidence_ids, conflicts=assessment.conflicts, missing_evidence=assessment.missing_evidence,
            impact_reference=impact_ref, recommended_next_step=conclusion.get("recommended_next_step"), provider=self.provider.name, llm_used=run.llm_used, fallback_used=run.fallback_used,
        )
