"""Evidence retrieval + Evidence Support Scoring.

For each hypothesis, every supporting / contradicting condition is evaluated against the
collected signals. Each observed condition yields one EvidenceDraft that carries the underlying
record ids, the claim, the value and a deterministic **Evidence Support Score** (0..1).

The score is NOT a probability of truth. It expresses how strongly the available records
support (or contradict) the hypothesis, combining:
    relevance   - condition weight (relative to the hypothesis) x how clearly it is met
    recency     - age of the observation
    reliability - source type reliability (synthetic market/competitor signals rank lower)
    correlation - agreement with the other observed conditions of the hypothesis
    consistency - share of the hypothesis' evidence weight that points the same way
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.investigation.registry import ConditionResult, HypothesisCandidate, evaluate
from app.investigation.signals import SignalSet

SOURCE_RELIABILITY: dict[str, float] = {
    "sensor_reading": 0.9, "production_metric": 0.95, "maintenance_record": 0.9, "machine_component": 0.85, "historical_incident": 0.7,
    "order": 0.95, "order_item": 0.95, "customer": 0.9, "inventory": 0.9, "inventory_snapshot": 0.9, "supplier": 0.85, "supplier_batch": 0.9,
    "financial_metric": 0.9, "market_signal": 0.55, "competitor_signal": 0.5,
    # REACT Merchant record types
    "purchase_order": 0.95, "supplier_delivery": 0.95, "pricing_history": 0.95, "discount": 0.9, "expense": 0.9, "product": 0.95,
}
WEIGHTS = {"relevance": 0.35, "recency": 0.15, "reliability": 0.20, "correlation": 0.15, "consistency": 0.15}
UNKNOWN_PENALTY = 0.5  # weight of unobserved supporting conditions counts half against the hypothesis
UNMET_SUPPORT_FACTOR = 0.6  # an observed-but-unmet supporting condition contradicts with this fraction of its weight


@dataclass
class EvidenceDraft:
    hypothesis_type: str
    signal: str
    source_type: str
    source_id: str | None
    source_ids: list[str]
    timestamp: datetime | None
    claim: str
    value: Any
    unit: str | None
    baseline: float | None
    relevance: float
    supports: bool
    contradicts: bool
    weight: float
    condition_label: str
    strength: float
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"hypothesis": self.hypothesis_type, "signal": self.signal, "source_type": self.source_type, "source_id": self.source_id, "source_ids": self.source_ids[:20], "timestamp": self.timestamp.isoformat() if self.timestamp else None, "claim": self.claim, "value": self.value, "unit": self.unit, "baseline": self.baseline, "relevance": round(self.relevance, 3), "supports": self.supports, "contradicts": self.contradicts, "evidence_support_score": round(self.score, 3), "score_breakdown": self.breakdown, "condition": self.condition_label, "details": self.details}


def recency_factor(ts: datetime | None, now: datetime) -> float:
    if ts is None:
        return 0.6
    age = max(0.0, (now - ts).total_seconds() / 86400)
    if age <= 1:
        return 1.0
    if age <= 7:
        return 0.85
    if age <= 30:
        return 0.7
    if age <= 90:
        return 0.5
    return 0.3


def _draft(cand: HypothesisCandidate, r: ConditionResult, supports: bool, weight: float, max_weight: float, claim_prefix: str) -> EvidenceDraft:
    obs = r.observation
    assert obs is not None
    strength = r.strength if r.satisfied else max(0.3, 1.0 - (r.strength or 0))
    relevance = (weight / max_weight) * (strength if r.satisfied else 0.6)
    value = obs.value
    return EvidenceDraft(
        hypothesis_type=cand.spec.type, signal=obs.signal, source_type=obs.source_type, source_id=obs.source_ids[0] if obs.source_ids else None, source_ids=list(obs.source_ids), timestamp=obs.timestamp,
        claim=f"{claim_prefix}{obs.description}", value=(round(value, 4) if isinstance(value, float) else value), unit=obs.unit, baseline=obs.baseline, relevance=round(min(1.0, relevance), 4), supports=supports, contradicts=not supports,
        weight=weight, condition_label=r.condition.label, strength=strength, details={**obs.details, "op": r.condition.op, "threshold": r.condition.threshold, "satisfied": r.satisfied, "source_count": len(obs.source_ids)},
    )


def build_evidence(cand: HypothesisCandidate, signals: SignalSet, now: datetime) -> tuple[list[EvidenceDraft], dict[str, Any]]:
    """Evaluate the hypothesis' conditions; return scored evidence and the hypothesis-level score."""
    spec = cand.spec
    max_w = max([c.weight for c in spec.supporting + spec.contradicting] + [1.0])
    drafts: list[EvidenceDraft] = []
    unknown_weight = 0.0
    seen: set[tuple[str, str]] = set()
    for c in spec.supporting:
        r = evaluate(c, signals)
        if not r.observed:
            unknown_weight += c.weight
            continue
        if r.satisfied:
            drafts.append(_draft(cand, r, True, c.weight, max_w, ""))
        else:
            drafts.append(_draft(cand, r, False, c.weight * UNMET_SUPPORT_FACTOR, max_w, "Condition not met: "))
        seen.add((c.signal, c.op))
    for c in spec.contradicting:
        r = evaluate(c, signals)
        if r.observed and r.satisfied and (c.signal, c.op) not in seen:
            drafts.append(_draft(cand, r, False, c.weight, max_w, ""))
    # ---- scoring ----
    total_support_w = sum(d.weight for d in drafts if d.supports)
    total_contra_w = sum(d.weight for d in drafts if d.contradicts)
    n_support = sum(1 for d in drafts if d.supports)
    n_total = len(drafts) or 1
    consistency_support = total_support_w / (total_support_w + total_contra_w) if (total_support_w + total_contra_w) else 0.0
    for d in drafts:
        same_direction = (n_support - 1) / (n_total - 1) if d.supports and n_total > 1 else ((n_total - n_support - 1) / (n_total - 1) if not d.supports and n_total > 1 else 1.0)
        correlation = 0.5 + 0.5 * max(0.0, same_direction)
        consistency = consistency_support if d.supports else (1.0 - consistency_support)
        rec = recency_factor(d.timestamp, now)
        rel = SOURCE_RELIABILITY.get(d.source_type, 0.7)
        d.breakdown = {"relevance": round(d.relevance, 3), "recency": round(rec, 3), "source_reliability": round(rel, 3), "correlation": round(correlation, 3), "consistency": round(consistency, 3)}
        d.score = round(WEIGHTS["relevance"] * d.relevance + WEIGHTS["recency"] * rec + WEIGHTS["reliability"] * rel + WEIGHTS["correlation"] * correlation + WEIGHTS["consistency"] * consistency, 4)
    S = sum(d.weight * d.score for d in drafts if d.supports)
    Cc = sum(d.weight * d.score for d in drafts if d.contradicts)
    ess = S / (S + Cc + UNKNOWN_PENALTY * unknown_weight) if (S + Cc + unknown_weight) > 0 else 0.0
    # a hypothesis with no satisfied supporting condition cannot score above the "insufficient support" band
    if n_support == 0:
        ess = min(ess, 0.15)
    summary = {
        "evidence_support_score": round(ess, 4), "supporting_weight": round(S, 3), "contradicting_weight": round(Cc, 3), "unknown_weight": round(unknown_weight, 3),
        "supporting_count": n_support, "contradicting_count": len(drafts) - n_support, "conditions_total": len(spec.supporting) + len(spec.contradicting), "unknown_penalty": UNKNOWN_PENALTY,
        "formula": "ESS = Σ(w·score | supports) / (Σ(w·score | supports) + Σ(w·score | contradicts) + 0.5·Σ(w | unobserved supporting))",
    }
    return drafts, summary


def support_label(ess: float | None) -> str:
    if ess is None:
        return "unknown"
    if ess >= 0.8:
        return "strongly supported"
    if ess >= 0.6:
        return "supported"
    if ess >= 0.4:
        return "weakly supported"
    return "insufficient support"
