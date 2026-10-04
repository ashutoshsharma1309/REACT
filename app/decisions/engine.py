"""Decision engine: builds the decision context, runs the Decision Agent with deterministic validation,
persists the decision + approval + proposed action, and drives the human-approval / execution lifecycle.

    agent run COMPLETED → DecisionAgent → validate → Decision(DECISION_READY)
        ├─ readiness INSUFFICIENT_EVIDENCE → Decision(INSUFFICIENT_EVIDENCE), no approval, no action
        └─ else → AWAITING_APPROVAL + Approval(PENDING) + ActionExecution(PROPOSED)
               → approve(reviewed_by) → APPROVED → execute() → EXECUTING → EXECUTED (simulated action COMPLETED)
               → reject(reviewed_by, reason) → REJECTED
Money is never produced here: expected_impact is copied from the Phase 2 investigation impact.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agent_runtime.guard import guard_provider
from app.agent_runtime.limits import AgentLimits, LimitTracker
from app.agent_runtime.tool_executor import ToolExecutor
from app.agent_runtime.trace import Tracer
from app.agents.base import AgentContext, AgentFailure, finding_context
from app.agents.decision import DecisionAgent
from app.agents.investigation import investigation_context
from app.agents.orchestrator import Orchestrator
from app.agents.provider import LLMProvider, build_provider
from app.config import settings
from app.database.base import utcnow
from app.decisions.actions import ActionExecutor
from app.decisions.catalogue import applicable_actions, catalogue_for, hypothesis_actions_for
from app.decisions.schemas import DecisionRecommendation
from app.decisions.state import ACTION_TRANSITIONS, APPROVAL_TRANSITIONS, DECISION_TRANSITIONS, check
from app.models import ActionExecution, AgentRun, Approval, BusinessRisk, Decision, EvidenceItem, Incident, Inventory, Investigation, Machine, Opportunity, Product, ProductionLine, Supplier
from app.models.enums import ActionStatus, AgentRunStatus, ApprovalStatus, DecisionReadiness, DecisionStatus, IncidentStatus, OpportunityStatus, RiskStatus
from app.observability import get_logger, log_event
from app.observability.metrics import emit

log = get_logger("react.decisions")


class DecisionError(ValueError):
    pass


class AIUnavailable(DecisionError):
    """The language model could not be used and no fallback was allowed. No conclusion or recommendation exists.

    The API maps this to HTTP 503. It is never converted into a guessed answer.
    """

    def __init__(self, reason: str, agent_run_id: uuid.UUID | None = None) -> None:
        self.agent_run_id = agent_run_id
        ref = f" The failure is recorded on agent run {agent_run_id}." if agent_run_id else ""
        super().__init__(f"AI investigation temporarily unavailable: {reason}. REACT produced no conclusion or recommendation and changed nothing.{ref}")


class DecisionEngine:
    def __init__(self, db: Session, provider: LLMProvider | None = None, limits: AgentLimits | None = None, fallback_enabled: bool | None = None) -> None:
        self.db = db
        self.provider = provider or build_provider()
        self.limits = limits or AgentLimits.from_settings()
        self.fallback_enabled = settings.agent_fallback_to_deterministic if fallback_enabled is None else fallback_enabled
        from app.domains import current_domain

        self.domain = current_domain(db)
        self.catalogue = catalogue_for(self.domain)

    # ---- creation --------------------------------------------------------------------------------------
    def create(self, agent_run_id: uuid.UUID | None = None, incident_id: uuid.UUID | None = None, opportunity_id: uuid.UUID | None = None, risk_id: uuid.UUID | None = None) -> Decision:
        given = [x for x in (agent_run_id, incident_id, opportunity_id, risk_id) if x]
        if len(given) != 1:
            raise DecisionError("exactly one of agent_run_id, incident_id, opportunity_id or risk_id is required")
        g = guard_provider(self.db, self.provider, self.fallback_enabled)  # outage drill / hourly LLM budget (Phase 6)
        self.provider, self.fallback_enabled = g.provider, g.fallback_enabled
        if agent_run_id:
            run = self.db.scalar(select(AgentRun).options(selectinload(AgentRun.trace_entries), selectinload(AgentRun.tool_calls)).where(AgentRun.id == agent_run_id))
            if run is None:
                raise DecisionError(f"agent run {agent_run_id} not found")
        else:
            run = Orchestrator(self.db, provider=self.provider, limits=self.limits, fallback_enabled=self.fallback_enabled).start_and_run(incident_id=incident_id, opportunity_id=opportunity_id, risk_id=risk_id)
            run = self.db.scalar(select(AgentRun).options(selectinload(AgentRun.trace_entries), selectinload(AgentRun.tool_calls)).where(AgentRun.id == run.id))
        if run.status == AgentRunStatus.FAILED and (run.error or "").startswith("AgentFailure"):
            raise AIUnavailable(_ai_reason(run.error), run.id)
        if run.status != AgentRunStatus.COMPLETED:
            raise DecisionError(f"agent run {run.id} is {run.status.value}; a decision requires a COMPLETED run")
        inv = self.db.get(Investigation, run.investigation_id) if run.investigation_id else None
        if inv is None:
            raise DecisionError("agent run has no investigation")
        subject = self._subject(run)
        ctx = self.build_context(run, inv, subject)
        tracer = Tracer(self.db, run)
        limits = LimitTracker(self.limits)
        agent_ctx = AgentContext(db=self.db, run=run, tracer=tracer, limits=limits, provider=self.provider, executor=ToolExecutor(self.db, run, tracer, limits), fallback_enabled=self.fallback_enabled, max_retries=self.limits.max_retries)
        try:
            rec = DecisionAgent(agent_ctx).run(ctx, validator=lambda r: self.validate(r, ctx))
        except AgentFailure as exc:
            self.db.commit()
            emit({"AIUnavailable": 1}, agent_run_id=run.id, stage="decision")
            raise AIUnavailable(_ai_reason(str(exc)), run.id) from exc
        checks = self._checks(rec, ctx)
        target_name = next((t["name"] for t in ctx["targets"] if t["target_id"] == rec.target_id and t["target_type"] == rec.target_type), None)
        decision = Decision(
            agent_run_id=run.id, investigation_id=inv.id, subject_type=run.subject_type, incident_id=run.incident_id, opportunity_id=run.opportunity_id, risk_id=run.risk_id,
            status=DecisionStatus.DECISION_READY, decision_readiness=DecisionReadiness(rec.decision_readiness), action_type=rec.action_type, target_type=rec.target_type, target_id=rec.target_id, target_name=target_name,
            priority=rec.priority, summary=rec.summary, problem_statement=rec.problem_statement, recommendation=rec.recommendation, rationale=rec.rationale, required_actions=rec.required_actions,
            alternatives=[a.model_dump() for a in rec.alternatives], risks=rec.risks, assumptions=rec.assumptions, evidence_ids=rec.evidence_ids, contradicting_evidence_ids=rec.contradicting_evidence_ids,
            hypothesis_id=uuid.UUID(rec.hypothesis_id) if rec.hypothesis_id else None, hypothesis_type=rec.hypothesis_type, evidence_support_score=rec.evidence_support_score,
            expected_impact={**(inv.impact or {}), "source": "app/impact/engine.py via investigation.impact", "impact_reference": (run.result or {}).get("impact_reference")},
            requires_human_approval=True, validation={"passed": True, "checks": checks}, provider=self.provider.name, llm_used=run.llm_used, fallback_used=run.fallback_used,
        )
        self.db.add(decision)
        self.db.flush()
        self._event(decision, "decision_created", "decision_agent", status=decision.status.value, readiness=rec.decision_readiness, action_type=rec.action_type)
        tracer.record("decision_agent", "decision", f"Decision {decision.id} created: {rec.decision_readiness}, {rec.action_type or 'no action'} for {target_name or rec.target_type or 'n/a'}", state=decision.status.value, evidence_ids=rec.evidence_ids, hypothesis_ids=[rec.hypothesis_id] if rec.hypothesis_id else [], decision_id=str(decision.id))
        if decision.decision_readiness == DecisionReadiness.INSUFFICIENT_EVIDENCE:
            self._transition_decision(decision, DecisionStatus.INSUFFICIENT_EVIDENCE, "decision_engine")
            self._update_subject(subject, "insufficient")
        else:
            self._transition_decision(decision, DecisionStatus.AWAITING_APPROVAL, "decision_engine")
            approval = Approval(decision_id=decision.id, status=ApprovalStatus.PENDING, approval_metadata={"required": True, "reason": "REACT never executes without an explicit human approval"})
            params: dict[str, Any] = {"priority": rec.priority, "required_actions": rec.required_actions}
            if self.domain == "merchant":  # deterministic facts for the merchant (quantity, cost, suggested price...)
                from app.domains.merchant.actions import action_parameters

                params.update(action_parameters(self.db, rec.action_type, rec.target_type, rec.target_id, inv.impact or {}))
            action = ActionExecution(decision_id=decision.id, action_type=rec.action_type, target_type=rec.target_type, target_id=rec.target_id, target_name=target_name, status=ActionStatus.PROPOSED, parameters=params, simulated=True, transitions=[{"at": utcnow().isoformat(), "to": "PROPOSED", "by": "decision_engine"}])
            self.db.add_all([approval, action])
            self.db.flush()
            self._event(decision, "approval_requested", "decision_engine", approval_id=str(approval.id), action_id=str(action.id))
            tracer.record("decision_engine", "approval_requested", f"Human approval requested for {rec.action_type} (approval {approval.id}); action {action.id} PROPOSED", state="AWAITING_APPROVAL", decision_id=str(decision.id))
            self._update_subject(subject, "awaiting_approval")
        self.db.commit()
        self.db.refresh(decision)
        emit({"DecisionCreated": 1}, decision_id=decision.id, readiness=rec.decision_readiness)
        log_event(log, "decision.created", decision_id=decision.id, agent_run_id=run.id, status=decision.status.value, action_type=rec.action_type, readiness=rec.decision_readiness, evidence_support_score=rec.evidence_support_score)
        return decision

    # ---- context ----------------------------------------------------------------------------------------
    def build_context(self, run: AgentRun, inv: Investigation, subject) -> dict[str, Any]:
        finding = finding_context(self.db, subject)
        result = dict(run.result or {})
        inv_ctx = investigation_context(self.db, inv)
        leading = next((h for h in inv_ctx["hypotheses"] if h["hypothesis_id"] == result.get("hypothesis_id")), None)
        ev_by_id = {e["evidence_id"]: e for e in inv_ctx["evidence"]}
        claims = [ev_by_id[i]["claim"] for i in result.get("supporting_evidence_ids", []) if i in ev_by_id][:8]
        impact = inv.impact or {}
        kind = finding["subject_kind"]
        default = hypothesis_actions_for(self.domain).get(result.get("hypothesis_type") or "", (None, ()))
        actions = [{"type": s.type, "label": s.label, "description": s.description, "target_types": list(s.target_types), "default_risks": list(s.default_risks), "default_assumptions": list(s.default_assumptions), "simulated_outcome": s.simulated_outcome} for s in applicable_actions(kind, self.domain)]
        return {
            "finding": finding, "result": result, "leading": leading, "leading_claims": claims, "alternatives_considered": [{"hypothesis_type": h["hypothesis_type"], "evidence_support_score": h["evidence_support_score"]} for h in inv_ctx["hypotheses"][1:4]],
            "evidence_ids": inv_ctx["evidence_ids"], "evidence": inv_ctx["evidence"][:40],
            "impact": {"estimated_total": impact.get("estimated_total"), "currency": impact.get("currency"), "wording": impact.get("wording"), "components": {k: v.get("value") for k, v in impact.items() if isinstance(v, dict) and "value" in v}, "constraints": [c.get("type") for c in impact.get("operational_constraints", [])] if isinstance(impact.get("operational_constraints"), list) else []},
            "actions": actions, "default_action": {"primary": default[0], "alternatives": list(default[1])} if default[0] else None, "targets": self._targets(finding, subject),
        }

    def _targets(self, f: dict[str, Any], subject) -> list[dict[str, Any]]:
        if self.domain == "merchant":
            from app.domains.merchant.actions import targets as merchant_targets

            return merchant_targets(self.db, f)
        targets: list[dict[str, Any]] = []
        if f.get("machine_id"):
            targets.append({"target_type": "machine", "target_id": f["machine_id"], "name": f.get("machine_name")})
        if f.get("production_line_id"):
            targets.append({"target_type": "production_line", "target_id": f["production_line_id"], "name": f.get("production_line_name")})
        if f.get("product_id"):
            targets.append({"target_type": "product", "target_id": f["product_id"], "name": f.get("product_name")})
            inv = self.db.scalar(select(Inventory).where(Inventory.product_id == uuid.UUID(f["product_id"])))
            if inv:
                targets.append({"target_type": "inventory", "target_id": str(inv.id), "name": inv.item_name})
        if f.get("supplier_id"):
            targets.append({"target_type": "supplier", "target_id": f["supplier_id"], "name": f.get("supplier_name")})
            if f.get("component_type"):
                inv = self.db.scalar(select(Inventory).where(Inventory.component_type == f["component_type"]))
                if inv:
                    targets.append({"target_type": "inventory", "target_id": str(inv.id), "name": inv.item_name})
        if f.get("inventory_id"):
            inv = self.db.get(Inventory, uuid.UUID(f["inventory_id"]))
            if inv:
                targets.append({"target_type": "inventory", "target_id": str(inv.id), "name": inv.item_name})
        if f.get("category"):
            targets.append({"target_type": "category", "target_id": f["category"], "name": f["category"]})
        if f.get("region"):
            targets.append({"target_type": "region", "target_id": f["region"], "name": f["region"]})
        if f.get("customer_segment"):
            targets.append({"target_type": "customer_segment", "target_id": f["customer_segment"], "name": f["customer_segment"]})
        seen = set()
        out = []
        for t in targets:
            key = (t["target_type"], t["target_id"])
            if key not in seen:
                seen.add(key)
                out.append(t)
        return out

    # ---- validation (deterministic; the LLM cannot bypass it) -------------------------------------------
    def validate(self, rec: DecisionRecommendation, ctx: dict[str, Any]) -> None:
        f, res = ctx["finding"], ctx["result"]
        if rec.subject_kind != f["subject_kind"] or rec.subject_id != f["subject_id"]:
            raise ValueError("subject_kind / subject_id must match the finding")
        ess = res.get("evidence_support_score")
        concluded = res.get("status") == "CONCLUDED" and ess is not None and ess >= 0.5
        if not concluded:
            if rec.decision_readiness != "INSUFFICIENT_EVIDENCE" or rec.action_type is not None:
                raise ValueError("evidence is insufficient (result not CONCLUDED or Evidence Support Score < 0.5): decision_readiness must be INSUFFICIENT_EVIDENCE and action_type null")
        else:
            if rec.decision_readiness == "INSUFFICIENT_EVIDENCE" and rec.action_type is not None:
                raise ValueError("INSUFFICIENT_EVIDENCE decisions cannot carry an action")
            if rec.decision_readiness != "INSUFFICIENT_EVIDENCE":
                if not rec.action_type:
                    raise ValueError("a READY / NEEDS_REVIEW decision must recommend an action_type")
                spec = self.catalogue.get(rec.action_type)
                if spec is None or f["subject_kind"] not in spec.subject_kinds:
                    raise ValueError(f"action_type {rec.action_type} is not an applicable catalogue action for {f['subject_kind']}")
                targets = {(t["target_type"], t["target_id"]) for t in ctx["targets"]}
                if (rec.target_type, rec.target_id) not in targets:
                    raise ValueError("target_type / target_id must be one of the candidate targets")
                if rec.target_type not in spec.target_types:
                    raise ValueError(f"{rec.action_type} cannot target a {rec.target_type}")
                if not rec.evidence_ids:
                    raise ValueError("a recommendation must cite at least one evidence id")
                if not any(i in rec.rationale for i in rec.evidence_ids):
                    raise ValueError("the rationale must cite at least one of the evidence ids")
        valid = set(ctx["evidence_ids"])
        bad = (set(rec.evidence_ids) | set(rec.contradicting_evidence_ids)) - valid
        if bad:
            raise ValueError(f"unknown evidence ids: {sorted(bad)[:3]}")
        if rec.hypothesis_id != res.get("hypothesis_id") or rec.hypothesis_type != res.get("hypothesis_type"):
            raise ValueError("hypothesis_id / hypothesis_type must be the investigation's leading hypothesis")
        if (rec.evidence_support_score is None) != (ess is None) or (ess is not None and abs(rec.evidence_support_score - ess) > 0.011):
            raise ValueError("evidence_support_score must restate the deterministic Evidence Support Score")
        for a in rec.alternatives:
            if a.action_type not in self.catalogue or a.action_type == rec.action_type:
                raise ValueError(f"alternative {a.action_type} is not a distinct catalogue action")
        if not rec.requires_human_approval:
            raise ValueError("requires_human_approval must be true")

    def _checks(self, rec: DecisionRecommendation, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"check": "target", "passed": True, "detail": f"{rec.target_type}:{rec.target_id}" if rec.target_id else "no target (insufficient evidence)"},
            {"check": "reason", "passed": bool(rec.problem_statement and rec.rationale)},
            {"check": "evidence", "passed": True, "detail": f"{len(rec.evidence_ids)} supporting / {len(rec.contradicting_evidence_ids)} contradicting evidence ids, all present in investigation {ctx['result'].get('investigation_id')}"},
            {"check": "expected_impact", "passed": True, "detail": "copied from the deterministic impact engine (investigation.impact)"},
            {"check": "proposed_action", "passed": True, "detail": rec.action_type or "none"},
            {"check": "risks_assumptions", "passed": True, "detail": f"{len(rec.risks)} risks, {len(rec.assumptions)} assumptions"},
            {"check": "human_approval_required", "passed": rec.requires_human_approval},
            {"check": "evidence_support_score_restated", "passed": True, "detail": rec.evidence_support_score},
            {"check": "readiness", "passed": True, "detail": rec.decision_readiness},
        ]

    # ---- lifecycle ---------------------------------------------------------------------------------------
    def _load(self, decision_id: uuid.UUID) -> Decision:
        d = self.db.scalar(select(Decision).options(selectinload(Decision.approvals), selectinload(Decision.actions)).where(Decision.id == decision_id))
        if d is None:
            raise DecisionError(f"decision {decision_id} not found")
        return d

    def approve(self, decision_id: uuid.UUID, reviewed_by: str, reason: str | None = None, execute: bool = True) -> Decision:
        d = self._load(decision_id)
        check(DECISION_TRANSITIONS, d.status, DecisionStatus.APPROVED, "decision")
        approval = next((a for a in d.approvals if a.status == ApprovalStatus.PENDING), None)
        if approval is None:
            raise DecisionError("no pending approval for this decision")
        check(APPROVAL_TRANSITIONS, approval.status, ApprovalStatus.APPROVED, "approval")
        approval.status, approval.reviewed_at, approval.reviewed_by, approval.reason = ApprovalStatus.APPROVED, utcnow(), reviewed_by, reason
        action = next((a for a in d.actions if a.status == ActionStatus.PROPOSED), None)
        if action is None:
            raise DecisionError("no proposed action for this decision")
        self._transition_action(action, ActionStatus.APPROVED, reviewed_by)
        action.approval_id = approval.id
        self._transition_decision(d, DecisionStatus.APPROVED, reviewed_by, reason=reason, approval_id=str(approval.id))
        self._trace(d, "human_reviewer", "approval", f"Decision approved by {reviewed_by}" + (f": {reason}" if reason else ""), state="APPROVED")
        self.db.commit()
        log_event(log, "decision.approved", decision_id=d.id, reviewed_by=reviewed_by)
        if execute:
            return self.execute(d.id, executed_by=reviewed_by)  # execute() writes the evidence pack, including the action result
        self._archive(d.id, "decision.approved")
        self.db.refresh(d)
        return d

    def reject(self, decision_id: uuid.UUID, reviewed_by: str, reason: str) -> Decision:
        d = self._load(decision_id)
        check(DECISION_TRANSITIONS, d.status, DecisionStatus.REJECTED, "decision")
        approval = next((a for a in d.approvals if a.status == ApprovalStatus.PENDING), None)
        if approval is None:
            raise DecisionError("no pending approval for this decision")
        check(APPROVAL_TRANSITIONS, approval.status, ApprovalStatus.REJECTED, "approval")
        approval.status, approval.reviewed_at, approval.reviewed_by, approval.reason = ApprovalStatus.REJECTED, utcnow(), reviewed_by, reason
        for action in d.actions:
            if action.status == ActionStatus.PROPOSED:
                self._transition_action(action, ActionStatus.REJECTED, reviewed_by)
        self._transition_decision(d, DecisionStatus.REJECTED, reviewed_by, reason=reason, approval_id=str(approval.id))
        self._trace(d, "human_reviewer", "rejection", f"Decision rejected by {reviewed_by}: {reason}", state="REJECTED")
        self._update_subject(self._subject_of(d), "rejected")
        self.db.commit()
        self.db.refresh(d)
        log_event(log, "decision.rejected", decision_id=d.id, reviewed_by=reviewed_by)
        self._archive(d.id, "decision.rejected")
        self.db.refresh(d)
        return d

    def execute(self, decision_id: uuid.UUID, executed_by: str = "system") -> Decision:
        d = self._load(decision_id)
        check(DECISION_TRANSITIONS, d.status, DecisionStatus.EXECUTING, "decision")
        action = next((a for a in d.actions if a.status == ActionStatus.APPROVED), None)
        if action is None:
            raise DecisionError("no approved action to execute")
        self._transition_decision(d, DecisionStatus.EXECUTING, executed_by)
        self._transition_action(action, ActionStatus.EXECUTING, executed_by)
        action.started_at = utcnow()
        self.db.commit()
        try:
            result = ActionExecutor(self.db).execute(action, d, executed_by=executed_by)
            action.result = result
            action.executed_by = executed_by
            action.completed_at = utcnow()
            self._transition_action(action, ActionStatus.COMPLETED, executed_by)
            self._transition_decision(d, DecisionStatus.EXECUTED, executed_by, action_id=str(action.id), task_id=result.get("task_id"))
            self._trace(d, "action_executor", "action_executed", f"Simulated action {action.action_type} completed for {action.target_name or action.target_type}: {result.get('task_type')} {result.get('task_id')}", state="EXECUTED", action_id=str(action.id))
            self._update_subject(self._subject_of(d), "executed")
            self.db.commit()
            log_event(log, "decision.executed", decision_id=d.id, action_id=action.id, action_type=action.action_type, simulated=True)
            emit({"ActionExecuted": 1}, action_type=action.action_type, simulated=True)
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            d = self._load(decision_id)
            action = next(a for a in d.actions if a.id == action.id)
            reason = f"{type(exc).__name__}: {exc}"
            action.error = reason
            check(ACTION_TRANSITIONS, action.status, ActionStatus.FAILED, "action")
            self._transition_action(action, ActionStatus.FAILED, executed_by)
            d.error = reason
            self._transition_decision(d, DecisionStatus.FAILED, executed_by, error=reason)
            self._trace(d, "action_executor", "error", f"Action execution failed: {reason[:200]}", state="FAILED", status="error", error=reason)
            self.db.commit()
            log_event(log, "decision.execution_failed", level=40, decision_id=d.id, error=reason)
            emit({"ActionFailed": 1}, decision_id=d.id)
        self._archive(decision_id, f"decision.{self._load(decision_id).status.value.lower()}")
        self.db.refresh(d)
        return d

    # ---- helpers ----------------------------------------------------------------------------------------
    def _archive(self, decision_id: uuid.UUID, trigger: str) -> None:
        """Evidence pack for the audit trail (Phase 6). Never raises and never changes the decision outcome."""
        from app.services.artifact_service import archive_decision

        d = self._load(decision_id)
        artifact = archive_decision(self.db, d, trigger)
        summary = f"Evidence pack stored ({artifact.storage_backend}, sha256 {artifact.sha256[:12]}…)" if artifact.status == "STORED" else f"Evidence pack NOT stored: {artifact.error}"
        self._trace(d, "decision_engine", "artifact", summary, state=d.status.value, status="ok" if artifact.status == "STORED" else "warning", artifact_id=str(artifact.id))
        self.db.commit()

    def _transition_decision(self, d: Decision, new: DecisionStatus, actor: str, **details: Any) -> None:
        check(DECISION_TRANSITIONS, d.status, new, "decision")
        old = d.status
        d.status = new
        self._event(d, "status_changed", actor, from_status=old.value, to_status=new.value, **details)

    def _transition_action(self, a: ActionExecution, new: ActionStatus, actor: str) -> None:
        check(ACTION_TRANSITIONS, a.status, new, "action")
        a.status = new
        a.transitions = list(a.transitions or []) + [{"at": utcnow().isoformat(), "to": new.value, "by": actor}]

    def _event(self, d: Decision, event: str, actor: str, **details: Any) -> None:
        d.timeline = list(d.timeline or []) + [{"at": utcnow().isoformat(), "event": event, "actor": actor, **{k: v for k, v in details.items() if v is not None}}]

    def _trace(self, d: Decision, agent_name: str, event_type: str, summary: str, **kw: Any) -> None:
        if d.agent_run_id:
            run = self.db.scalar(select(AgentRun).options(selectinload(AgentRun.trace_entries)).where(AgentRun.id == d.agent_run_id))
            if run:
                Tracer(self.db, run).record(agent_name, event_type, summary, decision_id=str(d.id), **kw)

    def _subject(self, run: AgentRun):
        if run.incident_id:
            return self.db.get(Incident, run.incident_id)
        if run.opportunity_id:
            return self.db.get(Opportunity, run.opportunity_id)
        return self.db.get(BusinessRisk, run.risk_id)

    def _subject_of(self, d: Decision):
        if d.incident_id:
            return self.db.get(Incident, d.incident_id)
        if d.opportunity_id:
            return self.db.get(Opportunity, d.opportunity_id)
        return self.db.get(BusinessRisk, d.risk_id)

    def _update_subject(self, subject, stage: str) -> None:
        """Reflect the decision lifecycle on the Phase 1 finding (statuses reserved for this purpose)."""
        if subject is None:
            return
        if isinstance(subject, Incident):
            mapping = {"awaiting_approval": IncidentStatus.AWAITING_APPROVAL, "insufficient": IncidentStatus.AWAITING_DECISION, "rejected": IncidentStatus.AWAITING_DECISION, "executed": IncidentStatus.EXECUTING}
            if subject.status != IncidentStatus.RESOLVED and stage in mapping:
                subject.status = mapping[stage]
        elif isinstance(subject, Opportunity):
            if stage in ("executed", "rejected") and subject.status == OpportunityStatus.NEW:
                subject.status = OpportunityStatus.REVIEWED
        elif isinstance(subject, BusinessRisk):
            if stage == "executed" and subject.status == RiskStatus.OPEN:
                subject.status = RiskStatus.MITIGATING


def _ai_reason(error: str | None) -> str:
    """Short, safe reason from an AgentFailure message ("AgentFailure: agent: could not ... (ProviderUnavailable: ...)")."""
    text = (error or "the language model could not be reached").split("AgentFailure: ", 1)[-1]
    return text[:300].rstrip(". ")
