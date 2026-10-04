"""Decision Agent: turns a completed, verified agent run into one evidence-backed recommendation.

The agent selects from the controlled action catalogue and explains; it never computes money and
never executes. Its output is validated by the DecisionEngine (the validator is injected), so an
LLM cannot bypass deterministic validation.
"""
from __future__ import annotations

from typing import Any, Callable

from app.agents.base import BaseAgent
from app.agents.prompts import DECISION_SYSTEM
from app.decisions.schemas import DecisionRecommendation
from app.models.enums import AgentName


class DecisionAgent(BaseAgent):
    name = AgentName.DECISION.value
    system_prompt = DECISION_SYSTEM

    def run(self, context: dict[str, Any], validator: Callable[[DecisionRecommendation], None]) -> DecisionRecommendation:
        rec = self.structured("decision_recommendation", self.system_prompt, context, DecisionRecommendation, validator)
        self.ctx.tracer.record(self.name, "result", f"Recommendation: {rec.action_type or 'no action'} ({rec.decision_readiness}, {rec.priority}) for {rec.target_type or 'n/a'} {rec.target_id or ''}: {rec.summary[:120]}", evidence_ids=rec.evidence_ids, hypothesis_ids=[rec.hypothesis_id] if rec.hypothesis_id else [], readiness=rec.decision_readiness, action_type=rec.action_type, alternatives=[a.action_type for a in rec.alternatives])
        return rec
