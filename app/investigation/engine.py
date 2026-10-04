"""Investigation engine: lifecycle, orchestration and persistence.

    CREATED -> COLLECTING_EVIDENCE -> ANALYZING -> COMPLETED
                                                \\-> FAILED

An investigation only reads Phase 1 data through the tool layer and writes to the
investigation tables. The single exception is the incident status transition
DETECTED -> INVESTIGATING (a status reserved for this purpose in Phase 1).
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.base import utcnow
from app.impact import ImpactEngine
from app.investigation.analysis import analyse_impact, conclude
from app.investigation.evidence import EvidenceDraft, build_evidence, support_label
from app.investigation.graph import build_graph
from app.investigation.registry import HypothesisSpec, generate_hypotheses
from app.investigation.report import build_report
from app.investigation.signals import SignalSet, collect_signals
from app.models import BusinessRisk, EvidenceItem, Hypothesis, Incident, Investigation, InvestigationTraceEntry, Opportunity
from app.models.enums import HypothesisStatus, IncidentStatus, InvestigationStatus, InvestigationSubject
from app.observability import get_logger, log_event

log = get_logger("react.investigation")


class InvestigationError(ValueError):
    pass


class _Trace:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self._last = time.perf_counter()

    def __call__(self, step: str, message: str, **details: Any) -> None:
        now = time.perf_counter()
        self.entries.append({"sequence": len(self.entries) + 1, "timestamp": utcnow(), "step": step, "message": message, "details": {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in details.items()}, "duration_ms": round((now - self._last) * 1000, 1)})
        self._last = now


class InvestigationEngine:
    def __init__(self, db: Session, registry: list[HypothesisSpec] | None = None, impact: ImpactEngine | None = None) -> None:
        self.db = db
        self.registry = registry
        self.impact = impact or ImpactEngine()

    # ---- lifecycle ---------------------------------------------------------------------------
    def create(self, incident_id: uuid.UUID | None = None, opportunity_id: uuid.UUID | None = None, risk_id: uuid.UUID | None = None) -> Investigation:
        given = [x for x in (incident_id, opportunity_id, risk_id) if x]
        if len(given) != 1:
            raise InvestigationError("exactly one of incident_id, opportunity_id or risk_id is required")
        if incident_id:
            subject, kind = self.db.get(Incident, incident_id), InvestigationSubject.INCIDENT
        elif opportunity_id:
            subject, kind = self.db.get(Opportunity, opportunity_id), InvestigationSubject.OPPORTUNITY
        else:
            subject, kind = self.db.get(BusinessRisk, risk_id), InvestigationSubject.RISK
        if subject is None:
            raise InvestigationError(f"{kind.value.lower()} {given[0]} not found")
        inv = Investigation(subject_type=kind, incident_id=incident_id, opportunity_id=opportunity_id, risk_id=risk_id, status=InvestigationStatus.CREATED)
        self.db.add(inv)
        self.db.flush()
        self._persist_trace(inv, [{"sequence": 1, "timestamp": utcnow(), "step": "created", "message": f"Investigation created for {kind.value.lower()} {given[0]}", "details": {"subject_id": str(given[0])}, "duration_ms": 0.0}])
        self.db.commit()
        self.db.refresh(inv)
        log_event(log, "investigation.created", investigation_id=inv.id, subject_type=kind.value, subject_id=given[0])
        return inv

    def create_and_run(self, **kwargs: Any) -> Investigation:
        inv = self.create(**kwargs)
        return self.run(inv.id)

    def run(self, investigation_id: uuid.UUID) -> Investigation:
        inv = self.db.scalar(select(Investigation).options(selectinload(Investigation.trace_entries)).where(Investigation.id == investigation_id))
        if inv is None:
            raise InvestigationError(f"investigation {investigation_id} not found")
        subject = self._subject(inv)
        trace = _Trace()
        seq_offset = len(inv.trace_entries)
        if inv.status in (InvestigationStatus.COMPLETED, InvestigationStatus.FAILED):
            self._clear_results(inv)
            trace("restarted", f"Investigation re-run (previous status {inv.status.value})")
        started = time.perf_counter()
        try:
            inv.status = InvestigationStatus.COLLECTING_EVIDENCE
            inv.started_at = utcnow()
            inv.error = None
            self.db.commit()
            log_event(log, "investigation.started", investigation_id=inv.id, subject_type=inv.subject_type.value)
            trace("started", "Evidence collection started", status=inv.status.value)

            # 1. signals via the tool layer
            signals = collect_signals(self.db, subject, trace)
            trace("signals", f"Derived {len(signals.observations)} signals from {len(signals.tool_calls)} tool call(s)", signals=len(signals.observations))

            inv.status = InvestigationStatus.ANALYZING
            self.db.commit()
            # 2. hypotheses
            candidates = generate_hypotheses(signals, self.registry)
            trace("hypotheses", f"Generated {len(candidates)} hypotheses", hypotheses=[{"type": c.spec.type, "initial_confidence": c.initial_confidence} for c in candidates])
            hyp_rows: dict[str, Hypothesis] = {}
            for c in candidates:
                row = Hypothesis(investigation_id=inv.id, hypothesis_type=c.spec.type, rank=c.rank, description=c.spec.description, triggered_signals=c.triggered_signals, supporting_conditions=[{"signal": x.signal, "op": x.op, "threshold": x.threshold, "weight": x.weight, "label": x.label} for x in c.spec.supporting], contradicting_conditions=[{"signal": x.signal, "op": x.op, "threshold": x.threshold, "weight": x.weight, "label": x.label} for x in c.spec.contradicting], initial_confidence=c.initial_confidence)
                self.db.add(row)
                hyp_rows[c.spec.type] = row
            self.db.flush()

            # 3. evidence + scoring
            evidence: dict[str, list[EvidenceDraft]] = {}
            summaries: dict[str, dict[str, Any]] = {}
            n_items = 0
            for c in candidates:
                drafts, summary = build_evidence(c, signals, subject.detected_at)
                evidence[c.spec.type] = drafts
                summaries[c.spec.type] = summary
                n_items += len(drafts)
            trace("evidence", f"Collected {n_items} evidence items across {len(candidates)} hypotheses", evidence_items=n_items)
            evidence_ids: dict[int, str] = {}
            for c in candidates:
                row = hyp_rows[c.spec.type]
                summ = summaries[c.spec.type]
                row.evidence_support_score = summ["evidence_support_score"]
                row.supporting_count, row.contradicting_count = summ["supporting_count"], summ["contradicting_count"]
                row.score_breakdown = summ
                lbl = support_label(summ["evidence_support_score"])
                row.status = HypothesisStatus.SUPPORTED if lbl in ("supported", "strongly supported") else HypothesisStatus.WEAKLY_SUPPORTED if lbl == "weakly supported" else HypothesisStatus.NOT_SUPPORTED
                for d in evidence[c.spec.type]:
                    e = EvidenceItem(investigation_id=inv.id, hypothesis_id=row.id, signal=d.signal, source_type=d.source_type, source_id=d.source_id, source_ids=d.source_ids, timestamp=d.timestamp, claim=d.claim, value=d.value if isinstance(d.value, (int, float)) and not isinstance(d.value, bool) else None, unit=d.unit, baseline_value=d.baseline, relevance=d.relevance, supports=d.supports, contradicts=d.contradicts, evidence_support_score=d.score, score_breakdown=d.breakdown, details={**d.details, "condition": d.condition_label, "weight": d.weight, "raw_value": d.value if isinstance(d.value, bool) else None})
                    self.db.add(e)
                    self.db.flush()
                    evidence_ids[id(d)] = str(e.id)
            trace("scoring", "Scored evidence and hypotheses (Evidence Support Score)", scores={t: s["evidence_support_score"] for t, s in summaries.items()})

            # 4. ranking + conclusion
            scored = sorted(
                [{"type": c.spec.type, "label": c.spec.label, "description": c.spec.description, "next_step": c.spec.next_step, "initial_confidence": c.initial_confidence, "evidence_support_score": summaries[c.spec.type]["evidence_support_score"], "supporting_count": summaries[c.spec.type]["supporting_count"], "contradicting_count": summaries[c.spec.type]["contradicting_count"]} for c in candidates],
                key=lambda h: (-h["evidence_support_score"], h["type"]),
            )
            for i, h in enumerate(scored, start=1):
                h["rank"] = i
                hyp_rows[h["type"]].rank = i
            conclusion = conclude(signals.subject_kind, scored, evidence)
            trace("conclusion", f"Generated conclusion: {conclusion['statement']}", selected=conclusion.get("selected_hypothesis"), evidence_support_score=conclusion.get("evidence_support_score"))

            # 5. impact
            impact = analyse_impact(self.db, subject, signals, conclusion, self.impact)
            trace("impact", f"Impact analysis completed (estimated total {impact.get('estimated_total')})", estimated_total=impact.get("estimated_total"))

            # 6. graph + report
            subject_ref = self._subject_ref(inv, subject)
            graph = build_graph(signals.subject_kind, subject_ref["id"], subject_ref["title"], signals, scored, evidence, evidence_ids, conclusion)
            report = build_report(signals.subject_kind, subject_ref, scored, conclusion, impact, trace.entries, {"count": len(signals.observations), "tool_calls": signals.tool_calls})
            if getattr(signals, "domain", "manufacturing") == "merchant" and scored:
                from app.domains.merchant.language import key_signals

                cf = conclusion.get("contributing_factor")
                report["key_signals"] = key_signals(evidence.get(scored[0]["type"], []), contributing=evidence.get(cf, []) if cf else None)
            trace("report", "Structured investigation report generated")

            inv.conclusion, inv.impact, inv.graph, inv.report, inv.signals = conclusion, impact, graph, report, signals.as_dict()
            inv.confidence = conclusion.get("evidence_support_score")
            inv.confidence_label = conclusion.get("support_label")
            inv.summary = conclusion.get("statement")
            inv.status = InvestigationStatus.COMPLETED
            inv.completed_at = utcnow()
            if isinstance(subject, Incident) and subject.status == IncidentStatus.DETECTED:
                subject.status = IncidentStatus.INVESTIGATING
            trace("completed", f"Investigation completed in {round((time.perf_counter() - started) * 1000)} ms", status="COMPLETED")
            self._persist_trace(inv, trace.entries, seq_offset)
            self.db.commit()
            self.db.refresh(inv)
            log_event(log, "investigation.completed", investigation_id=inv.id, subject_type=inv.subject_type.value, selected=conclusion.get("selected_hypothesis"), evidence_support_score=inv.confidence, hypotheses=len(candidates), evidence_items=n_items, duration_ms=round((time.perf_counter() - started) * 1000))
            return inv
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            inv = self.db.get(Investigation, investigation_id)
            inv.status = InvestigationStatus.FAILED
            inv.error = f"{type(exc).__name__}: {exc}"
            inv.completed_at = utcnow()
            trace("failed", f"Investigation failed: {type(exc).__name__}", error=str(exc))
            self._persist_trace(inv, trace.entries, len(inv.trace_entries))
            self.db.commit()
            self.db.refresh(inv)
            log_event(log, "investigation.failed", level=40, investigation_id=inv.id, error=str(exc), exc_type=type(exc).__name__)
            raise

    # ---- helpers ----------------------------------------------------------------------------------
    def _subject(self, inv: Investigation):
        if inv.incident_id:
            s = self.db.get(Incident, inv.incident_id)
        elif inv.opportunity_id:
            s = self.db.get(Opportunity, inv.opportunity_id)
        else:
            s = self.db.get(BusinessRisk, inv.risk_id)
        if s is None:
            raise InvestigationError("investigation subject no longer exists")
        return s

    @staticmethod
    def _subject_ref(inv: Investigation, subject) -> dict[str, Any]:
        if isinstance(subject, Incident):
            return {"kind": "incident", "id": str(subject.id), "reference": f"INC-{str(subject.id)[:8].upper()}", "title": subject.title, "type": subject.incident_type.value, "severity": subject.severity.value, "machine_id": str(subject.machine_id) if subject.machine_id else None, "production_line_id": str(subject.production_line_id) if subject.production_line_id else None}
        if isinstance(subject, Opportunity):
            return {"kind": "opportunity", "id": str(subject.id), "reference": f"OPP-{str(subject.id)[:8].upper()}", "title": subject.title, "type": subject.opportunity_type.value, "confidence": subject.confidence.value, "product_id": str(subject.product_id) if subject.product_id else None, "region": subject.region}
        return {"kind": "risk", "id": str(subject.id), "reference": f"RISK-{str(subject.id)[:8].upper()}", "title": subject.title, "type": subject.risk_type.value, "severity": subject.severity.value, "entity_name": subject.entity_name}

    def _clear_results(self, inv: Investigation) -> None:
        for h in list(inv.hypotheses):
            self.db.delete(h)
        for e in list(inv.evidence_items):
            self.db.delete(e)
        inv.conclusion, inv.impact, inv.graph, inv.report, inv.signals = {}, {}, {}, {}, {}
        inv.confidence = inv.confidence_label = inv.summary = None
        self.db.flush()

    def _persist_trace(self, inv: Investigation, entries: list[dict[str, Any]], seq_offset: int = 0) -> None:
        for e in entries:
            self.db.add(InvestigationTraceEntry(investigation_id=inv.id, sequence=seq_offset + e["sequence"], timestamp=e["timestamp"], step=e["step"], message=e["message"], details=e["details"], duration_ms=e.get("duration_ms")))
        self.db.flush()
