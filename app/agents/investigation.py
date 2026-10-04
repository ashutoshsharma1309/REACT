"""Investigation Agent: bounded tool-calling loop, then assessment of the deterministically scored hypotheses.

The Phase 2 InvestigationEngine remains the authority for signals, hypotheses, evidence items,
Evidence Support Scores, conclusion and impact. The agent adds tool selection, a traceable
exploration of the data, and an assessment that may only cite ids the engine produced.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from app.agent_runtime.limits import LimitExceeded
from app.agents.base import AgentFailure, BaseAgent, finding_context, tool_catalogue
from app.agents.policies import allowed_tools
from app.agents.prompts import INVESTIGATION_ANALYSIS_SYSTEM, INVESTIGATION_TOOLS_SYSTEM
from app.agents.schemas import DetectionAnalysis, InvestigationAnalysis, ToolCallResult
from app.investigation.engine import InvestigationEngine
from app.investigation.evidence import support_label
from app.models import BusinessRisk, EvidenceItem, Incident, Investigation, Opportunity
from app.models.enums import AgentName

MAX_TOOL_CALLS_PER_TURN = 5
MAX_EVIDENCE_IN_CONTEXT = 80


def _label(hypothesis_type: str) -> str:
    from app.investigation.registry import label_for

    return label_for(hypothesis_type) or hypothesis_type


def investigation_context(db, inv: Investigation) -> dict[str, Any]:
    """Deterministic results as the agent sees them: ids, scores, claims. No money, no free text beyond claims."""
    items = db.scalars(select(EvidenceItem).where(EvidenceItem.investigation_id == inv.id).order_by(EvidenceItem.evidence_support_score.desc())).all()
    by_h: dict[str, dict[str, list[str]]] = {}
    for e in items:
        bucket = by_h.setdefault(str(e.hypothesis_id), {"supporting": [], "contradicting": []})
        bucket["supporting" if e.supports else "contradicting"].append(str(e.id))
    hyps = []
    for h in sorted(inv.hypotheses, key=lambda h: h.rank):
        ids = by_h.get(str(h.id), {"supporting": [], "contradicting": []})
        hyps.append({"hypothesis_id": str(h.id), "hypothesis_type": h.hypothesis_type, "hypothesis_label": _label(h.hypothesis_type), "rank": h.rank, "description": h.description, "evidence_support_score": round(h.evidence_support_score or 0.0, 4), "label": support_label(h.evidence_support_score), "supporting_count": h.supporting_count, "contradicting_count": h.contradicting_count, "supporting_evidence_ids": ids["supporting"], "contradicting_evidence_ids": ids["contradicting"], "unknown_weight": (h.score_breakdown or {}).get("unknown_weight", 0), "unobserved_conditions": max(0, (h.score_breakdown or {}).get("conditions_total", 0) - h.supporting_count - h.contradicting_count)})
    evidence = [{"evidence_id": str(e.id), "hypothesis_id": str(e.hypothesis_id), "signal": e.signal, "claim": e.claim, "value": e.value, "unit": e.unit, "baseline": e.baseline_value, "supports": e.supports, "source_type": e.source_type, "source_id": e.source_id, "source_count": len(e.source_ids or []), "evidence_support_score": e.evidence_support_score} for e in items[:MAX_EVIDENCE_IN_CONTEXT]]
    c = inv.conclusion or {}
    return {"investigation_id": str(inv.id), "status": inv.status.value, "hypotheses": hyps, "evidence": evidence, "evidence_ids": [str(e.id) for e in items], "conclusion": {"selected_hypothesis": c.get("selected_hypothesis"), "determined": c.get("determined"), "evidence_support_score": c.get("evidence_support_score"), "margin_over_next": c.get("margin_over_next"), "contributing_factor": c.get("contributing_factor"), "statement": c.get("statement"), "recommended_next_step": c.get("recommended_next_step")}}


class InvestigationAgent(BaseAgent):
    name = AgentName.INVESTIGATION.value

    def run(self, subject: Incident | Opportunity | BusinessRisk, detection: DetectionAnalysis) -> tuple[Investigation, InvestigationAnalysis, list[ToolCallResult]]:
        finding = finding_context(self.ctx.db, subject)
        domain = self.ctx.executor.domain
        tools = tool_catalogue(allowed_tools(self.name, domain), domain)
        called: list[str] = []
        results: list[ToolCallResult] = []
        # ---- 1. bounded tool-calling loop -----------------------------------------------------------
        while True:
            try:
                iteration = self.ctx.limits.iteration()
            except LimitExceeded as exc:
                self.ctx.tracer.record(self.name, "limit", f"Tool loop stopped: {exc}", status="warning")
                break
            self.ctx.run.iterations = iteration
            resp = self.tool_turn("tool_selection", INVESTIGATION_TOOLS_SYSTEM, {"finding": finding, "detection_analysis": detection.model_dump(), "tools_called": called, "tool_results": [r.model_dump() for r in results[-6:]], "iteration": iteration}, tools)
            if not resp.tool_calls:
                break
            stop = False
            for call in resp.tool_calls[:MAX_TOOL_CALLS_PER_TURN]:
                try:
                    result, _ = self.ctx.executor.execute(self.name, call)
                except LimitExceeded as exc:
                    self.ctx.tracer.record(self.name, "limit", f"Tool loop stopped: {exc}", status="warning")
                    stop = True
                    break
                results.append(result)
                if result.status == "OK":
                    called.append(call.tool_name)
            if stop:
                break
        # ---- 2. deterministic investigation (authoritative) -------------------------------------------
        started = time.perf_counter()
        kind = finding["subject_kind"]
        inv = InvestigationEngine(self.ctx.db).create_and_run(**{f"{kind}_id": subject.id})
        self.ctx.run.investigation_id = inv.id
        self.ctx.tracer.record(self.name, "investigation_engine", f"Deterministic investigation {inv.id} completed: {len(inv.hypotheses)} hypotheses, conclusion '{(inv.conclusion or {}).get('selected_hypothesis')}' (ESS {(inv.conclusion or {}).get('evidence_support_score')})", duration_ms=round((time.perf_counter() - started) * 1000, 1), hypothesis_ids=[str(h.id) for h in inv.hypotheses], investigation_id=str(inv.id))
        if inv.status.value != "COMPLETED":
            raise AgentFailure(self.name, f"deterministic investigation ended in {inv.status.value}: {inv.error}")
        # ---- 3. grounded assessment -------------------------------------------------------------------
        inv_ctx = investigation_context(self.ctx.db, inv)
        valid_h = {h["hypothesis_id"]: h for h in inv_ctx["hypotheses"]}
        valid_e = set(inv_ctx["evidence_ids"])

        def validate(out: InvestigationAnalysis) -> None:
            if out.investigation_id != str(inv.id):
                raise ValueError("investigation_id must match")
            for h in out.hypotheses:
                ref = valid_h.get(h.hypothesis_id)
                if ref is None or ref["hypothesis_type"] != h.hypothesis_type:
                    raise ValueError(f"unknown hypothesis {h.hypothesis_id}/{h.hypothesis_type}")
                if abs(h.evidence_support_score - ref["evidence_support_score"]) > 0.011:
                    raise ValueError(f"evidence_support_score for {h.hypothesis_type} must be the deterministic value {ref['evidence_support_score']}")
                bad = (set(h.supporting_evidence_ids) | set(h.contradicting_evidence_ids)) - valid_e
                if bad:
                    raise ValueError(f"unknown evidence ids: {sorted(bad)[:3]}")
            if out.leading_hypothesis_id is not None and out.leading_hypothesis_id not in valid_h:
                raise ValueError("leading_hypothesis_id is not one of the investigation hypotheses")

        analysis = self.structured("investigation_analysis", INVESTIGATION_ANALYSIS_SYSTEM, {"finding": finding, "investigation": inv_ctx, "tools_called": called, "tool_results": [r.model_dump() for r in results]}, InvestigationAnalysis, validate)
        lead = next((h for h in analysis.hypotheses if h.hypothesis_id == analysis.leading_hypothesis_id), None)
        self.ctx.tracer.record(self.name, "result", f"Assessment: leading {analysis.leading_hypothesis_type} ({lead.assessment if lead else 'n/a'}); insufficient_evidence={analysis.insufficient_evidence}", hypothesis_ids=[h.hypothesis_id for h in analysis.hypotheses], evidence_ids=(lead.supporting_evidence_ids if lead else []), summary_text=analysis.summary[:300])
        return inv, analysis, results
