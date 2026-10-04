"""Evidence Agent: verifies grounding of the investigation using existing evidence ids and scores."""
from __future__ import annotations

from app.agents.base import BaseAgent
from app.agents.investigation import investigation_context
from app.agents.prompts import EVIDENCE_SYSTEM
from app.agents.schemas import EvidenceAssessment, InvestigationAnalysis
from app.models import Investigation
from app.models.enums import AgentName


class EvidenceAgent(BaseAgent):
    name = AgentName.EVIDENCE.value

    def run(self, inv: Investigation, analysis: InvestigationAnalysis) -> EvidenceAssessment:
        inv_ctx = investigation_context(self.ctx.db, inv)
        valid_e = set(inv_ctx["evidence_ids"])
        valid_h = {h["hypothesis_id"]: h for h in inv_ctx["hypotheses"]}
        leading = valid_h.get(analysis.leading_hypothesis_id or "") if analysis.leading_hypothesis_id else (inv_ctx["hypotheses"][0] if inv_ctx["hypotheses"] else None)
        # deterministic pre-check: a hypothesis called plausible without any supporting evidence id is an unsupported claim
        unsupported = [f"{h.hypothesis_type} assessed '{h.assessment}' without supporting evidence ids" for h in analysis.hypotheses if h.assessment == "plausible" and not h.supporting_evidence_ids]

        def validate(out: EvidenceAssessment) -> None:
            if out.investigation_id != str(inv.id):
                raise ValueError("investigation_id must match")
            if out.hypothesis_id is not None and out.hypothesis_id not in valid_h:
                raise ValueError("hypothesis_id is not one of the investigation hypotheses")
            bad = (set(out.supporting_evidence_ids) | set(out.contradicting_evidence_ids) | {i for c in out.conflicts for i in c.evidence_ids}) - valid_e
            if bad:
                raise ValueError(f"unknown evidence ids: {sorted(bad)[:3]}")
            if out.hypothesis_id and out.evidence_support_score is not None and abs(out.evidence_support_score - valid_h[out.hypothesis_id]["evidence_support_score"]) > 0.011:
                raise ValueError("evidence_support_score must restate the deterministic value, not a new score")
            if out.sufficient and not out.supporting_evidence_ids:
                raise ValueError("evidence cannot be sufficient without supporting evidence ids")

        assessment = self.structured("evidence_assessment", EVIDENCE_SYSTEM, {"investigation": inv_ctx, "leading": leading, "investigation_analysis": analysis.model_dump(), "unsupported_claims": unsupported}, EvidenceAssessment, validate)
        self.ctx.tracer.record(self.name, "result", f"Verification: verified={assessment.verified} sufficient={assessment.sufficient}; {len(assessment.supporting_evidence_ids)} supporting / {len(assessment.contradicting_evidence_ids)} contradicting; {len(assessment.conflicts)} conflict(s); {len(assessment.missing_evidence)} missing", evidence_ids=assessment.supporting_evidence_ids + assessment.contradicting_evidence_ids, hypothesis_ids=[assessment.hypothesis_id] if assessment.hypothesis_id else [], missing=assessment.missing_evidence, conflicts=[c.model_dump() for c in assessment.conflicts], unsupported_claims=assessment.unsupported_claims)
        return assessment
