"""Product read models for the business UI (Phase 5).

These endpoints aggregate existing data so the UI can answer "how is my business doing?" and
"where is this finding in the REACT workflow?" in one call each. They compute nothing new about the
business: impact values are the ones stored by the deterministic engines, statuses come from the
Phase 1-4 records.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActionExecution,
    AgentRun,
    BusinessRisk,
    Company,
    Decision,
    DetectionRun,
    Incident,
    Investigation,
    Machine,
    Opportunity,
    Product,
    ProductionLine,
    SimulationRun,
    SystemState,
)
from app.models.enums import DecisionStatus, IncidentStatus, OpportunityStatus, RiskStatus
from app.services import metrics_service
from app.domains.merchant.language import plural

SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
CONFIDENCE_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
KINDS = ("incident", "risk", "opportunity")


# ---- findings ------------------------------------------------------------------------------------------
def _latest_by_subject(db: Session, model, subject_col: str, ids: list[uuid.UUID]) -> dict[uuid.UUID, Any]:
    if not ids:
        return {}
    col = getattr(model, subject_col)
    rows = db.scalars(select(model).where(col.in_(ids)).order_by(model.created_at)).all()
    out: dict[uuid.UUID, Any] = {}
    for r in rows:  # ascending, so the last one wins
        out[getattr(r, subject_col)] = r
    return out


def _evidence_count(evidence: dict | None) -> int:
    records = (evidence or {}).get("records", {}) or {}
    n = 0
    for v in records.values():
        if isinstance(v, list):
            n += len(v)
        elif v:
            n += 1
    return n


def _stage(decision: Decision | None, run: AgentRun | None, inv: Investigation | None) -> str:
    """Where the finding is in DETECT → INVESTIGATE → DECIDE → APPROVE → ACT."""
    if decision is not None:
        return {
            DecisionStatus.AWAITING_APPROVAL: "AWAITING_APPROVAL",
            DecisionStatus.APPROVED: "APPROVED",
            DecisionStatus.EXECUTING: "APPROVED",
            DecisionStatus.EXECUTED: "ACTION_COMPLETED",
            DecisionStatus.REJECTED: "REJECTED",
            DecisionStatus.INSUFFICIENT_EVIDENCE: "INSUFFICIENT_EVIDENCE",
            DecisionStatus.FAILED: "FAILED",
            DecisionStatus.DECISION_READY: "INVESTIGATED",
        }[decision.status]
    if run is not None and run.status.value == "FAILED":
        return "FAILED"
    if run is not None or inv is not None:
        return "INVESTIGATED"
    return "DETECTED"


def _impact(estimated: dict | None) -> dict[str, Any] | None:
    if not estimated or estimated.get("value") is None:
        return None
    return {"value": float(estimated["value"]), "currency": estimated.get("currency", "INR"), "description": estimated.get("description"), "formula": estimated.get("formula"), "basis": estimated.get("basis", "SYNTHETIC")}


def list_findings(db: Session, kind: str | None = None, include_closed: bool = False) -> list[dict[str, Any]]:
    """Unified list of incidents, business risks and opportunities with their REACT workflow state."""
    kinds = [kind] if kind in KINDS else (["incident", "risk"] if kind == "risk_all" else list(KINDS))
    machines = {m.id: m for m in db.scalars(select(Machine)).all()}
    lines = {line.id: line for line in db.scalars(select(ProductionLine)).all()}
    products = {p.id: p for p in db.scalars(select(Product)).all()}
    out: list[dict[str, Any]] = []
    if "incident" in kinds:
        q = select(Incident)
        if not include_closed:
            q = q.where(Incident.status != IncidentStatus.RESOLVED)
        items = db.scalars(q).all()
        ids = [i.id for i in items]
        runs, invs, decs = _latest_by_subject(db, AgentRun, "incident_id", ids), _latest_by_subject(db, Investigation, "incident_id", ids), _latest_by_subject(db, Decision, "incident_id", ids)
        for i in items:
            m, line = machines.get(i.machine_id), lines.get(i.production_line_id)
            out.append(_row("incident", i.id, i.incident_type.value, i.title, i.description, i.status.value, i.detected_at, severity=i.severity.value, entity={"type": "machine" if m else "production_line", "id": str(m.id if m else i.production_line_id) if (m or i.production_line_id) else None, "name": m.name if m else (line.name if line else None), "code": m.code if m else (line.code if line else None)}, location=line.name if line else None, impact=_impact(i.estimated_impact), evidence_count=_evidence_count(i.evidence), run=runs.get(i.id), inv=invs.get(i.id), dec=decs.get(i.id)))
    if "risk" in kinds:
        q = select(BusinessRisk)
        if not include_closed:
            q = q.where(BusinessRisk.status != RiskStatus.RESOLVED)
        items = db.scalars(q).all()
        ids = [r.id for r in items]
        runs, invs, decs = _latest_by_subject(db, AgentRun, "risk_id", ids), _latest_by_subject(db, Investigation, "risk_id", ids), _latest_by_subject(db, Decision, "risk_id", ids)
        for r in items:
            ev = r.evidence or {}
            line = lines.get(uuid.UUID(ev["production_line_id"])) if ev.get("production_line_id") else None
            prod = products.get(uuid.UUID(ev["product_id"])) if ev.get("product_id") else None
            location = line.name if line else (ev.get("store_name") or ev.get("region") or (prod.category if prod else None))
            out.append(_row("risk", r.id, r.risk_type.value, r.title, r.description, r.status.value, r.detected_at, severity=r.severity.value, entity={"type": r.entity_type, "id": str(r.entity_id) if r.entity_id else None, "name": r.entity_name, "code": prod.sku if prod else None}, location=location, impact=_impact(r.estimated_impact), evidence_count=_evidence_count(r.evidence), run=runs.get(r.id), inv=invs.get(r.id), dec=decs.get(r.id), extra={"supporting_metrics": r.supporting_metrics}))
    if "opportunity" in kinds:
        q = select(Opportunity)
        if not include_closed:
            q = q.where(Opportunity.status != OpportunityStatus.DISMISSED)
        items = db.scalars(q).all()
        ids = [o.id for o in items]
        runs, invs, decs = _latest_by_subject(db, AgentRun, "opportunity_id", ids), _latest_by_subject(db, Investigation, "opportunity_id", ids), _latest_by_subject(db, Decision, "opportunity_id", ids)
        for o in items:
            p, line = products.get(o.product_id), lines.get(o.production_line_id)
            cat_name = (o.evidence or {}).get("category")
            entity = {"type": "product", "id": str(p.id), "name": p.name, "code": p.sku} if p else ({"type": "production_line", "id": str(line.id), "name": line.name, "code": line.code} if line else ({"type": "category", "id": None, "name": cat_name, "code": None} if cat_name else {"type": "segment" if o.customer_segment else "region" if o.region else None, "id": None, "name": o.customer_segment or o.region, "code": None}))
            out.append(_row("opportunity", o.id, o.opportunity_type.value, o.title, o.description, o.status.value, o.detected_at, confidence=o.confidence.value, entity=entity, location=o.region, impact=_impact(o.estimated_impact), evidence_count=_evidence_count(o.evidence), run=runs.get(o.id), inv=invs.get(o.id), dec=decs.get(o.id), extra={"signal": o.signal, "supporting_metrics": o.supporting_metrics}))
    out.sort(key=lambda f: (SEVERITY_RANK.get(f["severity"] or "", 9) if f["kind"] != "opportunity" else 10 + CONFIDENCE_RANK.get(f["confidence"] or "", 9), -(f["impact"]["value"] if f["impact"] else 0.0)))
    return out


def _row(kind: str, id_: uuid.UUID, type_: str, title: str, description: str, status: str, detected_at: datetime, *, severity: str | None = None, confidence: str | None = None, entity: dict, location: str | None, impact: dict | None, evidence_count: int, run: AgentRun | None, inv: Investigation | None, dec: Decision | None, extra: dict | None = None) -> dict[str, Any]:
    return {
        "kind": kind, "id": str(id_), "type": type_, "title": title, "description": description, "status": status, "detected_at": detected_at,
        "severity": severity, "confidence": confidence, "entity": entity, "location": location, "impact": impact, "evidence_count": evidence_count,
        "stage": _stage(dec, run, inv),
        "investigation": {"id": str(inv.id), "status": inv.status.value, "selected_hypothesis": (inv.conclusion or {}).get("selected_hypothesis"), "selected_label": (inv.conclusion or {}).get("selected_label"), "evidence_support_score": inv.confidence} if inv else None,
        "agent_run": {"id": str(run.id), "status": run.status.value, "result_status": run.result_status} if run else None,
        "decision": {"id": str(dec.id), "status": dec.status.value, "action_type": dec.action_type, "decision_readiness": dec.decision_readiness.value, "summary": dec.summary} if dec else None,
        **(extra or {}),
    }


def get_finding(db: Session, kind: str, finding_id: uuid.UUID) -> dict[str, Any] | None:
    if kind not in KINDS:
        return None
    for f in list_findings(db, kind=kind, include_closed=True):
        if f["id"] == str(finding_id):
            return f
    return None


# ---- activity ------------------------------------------------------------------------------------------
SCENARIO_LABELS = {"stockout_crisis": "Stockout crisis", "demand_opportunity": "Demand opportunity", "margin_leakage": "Margin leakage", "excess_inventory": "Excess inventory", "merchant_setup": "Demo business set up", "bearing_failure": "Machine bearing failure", "sensor_failure": "Sensor failure", "supplier_delay": "Supplier delay", "demand_spike": "Demand spike", "demand_drop": "Demand drop", "inventory_shortage": "Inventory shortage", "production_bottleneck": "Production bottleneck", "power_instability": "Power instability", "reset": "Business data reset to baseline"}


def activity(db: Session, limit: int = 30) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for s in db.scalars(select(SimulationRun).order_by(SimulationRun.started_at.desc()).limit(limit)).all():
        items.append({"at": s.finished_at or s.started_at, "kind": "simulation", "title": f"Scenario loaded: {SCENARIO_LABELS.get(s.scenario.value, s.scenario.value)}", "detail": (s.effects or {}).get("summary"), "status": s.status.value, "link": None})
    for r in db.scalars(select(DetectionRun).order_by(DetectionRun.started_at.desc()).limit(limit)).all():
        if r.engine == "detection":
            title = "Business scan: risks"
            detail = f"{plural(r.risks_created, 'new risk')} found" if not r.incidents_created else f"{plural(r.incidents_created, 'new operational issue')}, {plural(r.risks_created, 'new business risk')}"
        else:
            title, detail = "Business scan: opportunities", f"{r.opportunities_created} new opportunit{'y' if r.opportunities_created == 1 else 'ies'}"
        items.append({"at": r.finished_at or r.started_at, "kind": "scan", "title": title, "detail": detail, "status": r.status.value, "link": None})
    for run in db.scalars(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)).all():
        kind, sid = _subject_of(run)
        res = run.result or {}
        items.append({"at": run.completed_at or run.created_at, "kind": "investigation", "title": "Investigation completed" if run.status.value == "COMPLETED" else f"Investigation {run.status.value.lower()}", "detail": res.get("conclusion"), "status": run.result_status or run.status.value, "link": {"kind": kind, "id": sid}})
    for d in db.scalars(select(Decision).order_by(Decision.created_at.desc()).limit(limit)).all():
        kind, sid = _subject_of(d)
        for ev in d.timeline or []:
            title = {"decision_created": "Recommendation prepared", "approval_requested": None}.get(ev["event"])
            if ev["event"] == "status_changed":
                title = {"AWAITING_APPROVAL": "Waiting for approval", "APPROVED": f"Approved by {ev.get('actor')}", "REJECTED": f"Rejected by {ev.get('actor')}", "EXECUTED": "Simulated action completed", "FAILED": "Action failed", "INSUFFICIENT_EVIDENCE": "Not enough evidence to recommend an action"}.get(ev.get("to_status"))
            if not title:
                continue
            items.append({"at": datetime.fromisoformat(ev["at"]), "kind": "decision", "title": title, "detail": d.summary, "status": ev.get("to_status") or d.status.value, "link": {"kind": kind, "id": sid}})
    items.sort(key=lambda x: x["at"], reverse=True)
    return items[:limit]


def _subject_of(obj) -> tuple[str, str]:
    if obj.incident_id:
        return "incident", str(obj.incident_id)
    if obj.opportunity_id:
        return "opportunity", str(obj.opportunity_id)
    return "risk", str(obj.risk_id)


# ---- overview ------------------------------------------------------------------------------------------
ADDRESSED_STAGES = {"APPROVED", "ACTION_COMPLETED"}


def overview(db: Session) -> dict[str, Any]:
    state = db.get(SystemState, 1)
    company = db.scalar(select(Company))
    findings = list_findings(db)
    problems = [f for f in findings if f["kind"] in ("incident", "risk")]
    opportunities = [f for f in findings if f["kind"] == "opportunity"]
    open_problems = [f for f in problems if f["stage"] not in ADDRESSED_STAGES]
    critical_open = [f for f in open_problems if f["severity"] == "CRITICAL"]
    high_open = [f for f in open_problems if f["severity"] == "HIGH"]
    other_open = [f for f in open_problems if f["severity"] in ("MEDIUM", "LOW")]
    addressed = [f for f in problems if f["stage"] in ADDRESSED_STAGES]
    pending = [f for f in findings if f["stage"] == "AWAITING_APPROVAL"]
    if critical_open:
        status, headline = "CRITICAL", f"{len(critical_open)} critical issue{'s' if len(critical_open) != 1 else ''} need{'s' if len(critical_open) == 1 else ''} your attention"
    elif high_open or other_open or pending:
        n = len(high_open) + len(other_open)
        parts = []
        if n:
            parts.append(f"{n} open issue{'s' if n != 1 else ''}")
        if pending:
            parts.append(f"{len(pending)} decision{'s' if len(pending) != 1 else ''} waiting for approval")
        status, headline = "NEEDS_ATTENTION", " and ".join(parts).capitalize() if parts else "Some items need attention"
    else:
        status, headline = "HEALTHY", "No open risks detected" if not addressed else "All detected issues have an approved action"
    exposure = [f["impact"]["value"] for f in open_problems if f["impact"]]
    upside = [f["impact"]["value"] for f in opportunities if f["impact"]]
    scanned = db.scalar(select(DetectionRun).order_by(DetectionRun.started_at.desc())) is not None
    from app.domains import MERCHANT, current_domain

    domain = current_domain(db)
    metrics = None
    merchant_extra: dict[str, Any] = {}
    if state is not None and domain == MERCHANT:
        from app.domains.merchant import read_models

        merchant_extra = {"business_health": read_models.business_health(db), "value": read_models.value_metrics(db), "merchant": {k: v for k, v in read_models.merchant(db).items() if k in ("business_name", "business_type", "city", "state", "stores", "scenario", "is_synthetic")}}
    elif state is not None:
        m = metrics_service.business_metrics(db, 14)
        metrics = {"period_days": m["period_days"], "revenue": m["revenue"], "revenue_change_pct": m["change_pct"].get("revenue"), "gross_margin": m["gross_margin"], "orders": m["orders"], "currency": m["currency"], "line_utilization": [{"code": x["code"], "name": x["name"], "avg_utilization": x["avg_utilization"], "status": x["status"]} for x in m["line_utilization"]]}
    return {
        "company": {"name": company.name if company else None, "industry": company.industry if company else None, "currency": company.currency if company else "INR"},
        "as_of": state.data_end if state else None, "data_source": "Synthetic dataset (fictional business)" if domain == MERCHANT else "Synthetic dataset (fictional company)", "scanned": scanned, "domain": domain, **merchant_extra,
        "business_status": status, "headline": headline,
        "counts": {"critical": len(critical_open), "high": len(high_open), "other": len(other_open), "addressed": len(addressed), "opportunities": len(opportunities), "pending_decisions": len(pending)},
        "attention": (critical_open + high_open + [f for f in pending if f not in critical_open + high_open] + other_open)[:6],
        "addressed": addressed[:4],
        "opportunities": sorted(opportunities, key=lambda f: -(f["impact"]["value"] if f["impact"] else 0.0))[:4],
        "impact": {
            "exposure_total": round(sum(exposure), 2) if exposure else None, "exposure_count": len(exposure),
            "opportunity_total": round(sum(upside), 2) if upside else None, "opportunity_count": len(upside),
            "currency": company.currency if company else "INR",
            "note": "Sums of the deterministic impact estimates attached to each open finding. Estimates can overlap, so totals indicate scale rather than an exact figure.",
        },
        "metrics": metrics,
        "recent_activity": activity(db, 8),
    }
