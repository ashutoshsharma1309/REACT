"""Detection Agent: interprets an existing finding and frames the investigation. Creates nothing."""
from __future__ import annotations

from app.agents.base import BaseAgent, finding_context
from app.agents.prompts import DETECTION_SYSTEM
from app.agents.schemas import DetectionAnalysis
from app.models import BusinessRisk, Incident, Opportunity
from app.models.enums import AgentName


class DetectionAgent(BaseAgent):
    name = AgentName.DETECTION.value
    system_prompt = DETECTION_SYSTEM

    def run(self, subject: Incident | Opportunity | BusinessRisk) -> DetectionAnalysis:
        finding = finding_context(self.ctx.db, subject)

        def validate(out: DetectionAnalysis) -> None:
            if out.subject_kind != finding["subject_kind"] or out.subject_id != finding["subject_id"]:
                raise ValueError("subject_kind / subject_id must match the finding exactly")
            if out.event_type != finding["type"]:
                raise ValueError(f"event_type must be the detected type {finding['type']}")

        analysis = self.structured("detection_analysis", self.system_prompt, {"finding": finding}, DetectionAnalysis, validate)
        self.ctx.tracer.record(self.name, "result", f"Classified {analysis.event_type} ({analysis.severity}, {analysis.priority}); objective: {analysis.investigation_objective[:120]}", affected_entities=[e.model_dump() for e in analysis.affected_entities], evidence_categories=analysis.evidence_categories, missing_information=analysis.missing_information)
        return analysis
