"""Detection engine: runs the operational + risk detectors and persists incidents / risks.

Persistence rules
  * A finding with a dedupe_key that already has an OPEN incident/risk updates that record
    (severity, description, evidence, occurrence_count) instead of creating a duplicate.
  * Open incidents/risks produced by these engines that are no longer detected are RESOLVED.
  * Every record stores the detection_run_id and, when available, the simulation_run_id that
    touched its records, so insights are traceable end to end.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.base import utcnow
from app.detection.base import Finding, max_severity
from app.detection.config import DetectionConfig
from app.detection.operational import OperationalAnomalyDetector
from app.detection.risk import BusinessRiskDetector
from app.impact import ImpactEngine
from app.models import BusinessRisk, DetectionRun, Incident, SimulationRun, SystemState
from app.models.enums import DetectionSource, IncidentStatus, IncidentType, RiskStatus, RiskType, RunStatus, Severity
from app.observability import get_logger, log_event

log = get_logger("react.detection")


class DetectionEngine:
    def __init__(self, db: Session, config: DetectionConfig | None = None, impact: ImpactEngine | None = None) -> None:
        self.db = db
        self.cfg = config or DetectionConfig()
        self.impact = impact or ImpactEngine()

    def run(self) -> DetectionRun:
        state = self.db.get(SystemState, 1)
        if state is None:
            raise ValueError("Database is not seeded. Call POST /simulation/reset first.")
        now: datetime = state.data_end
        run = DetectionRun(engine="detection", status=RunStatus.RUNNING, window_end=now)
        self.db.add(run)
        self.db.flush()
        state.detection_status = "RUNNING"
        log_event(log, "detection.started", run_id=run.id, window_end=now)
        try:
            from app.domains import MERCHANT, current_domain

            if current_domain(self.db) == MERCHANT:  # REACT Merchant: risk rules only (no machine incidents)
                from app.domains.merchant.detection import MerchantDetector

                operational, risks = [], MerchantDetector(self.db, impact=self.impact).risks()
            else:
                operational = OperationalAnomalyDetector(self.db, self.cfg, self.impact).run(now)
                risks = BusinessRiskDetector(self.db, self.cfg, self.impact).run(now)
            last_sim = self._last_simulation_id(state)
            inc_created, inc_updated, inc_resolved = self._persist_incidents(operational, run, last_sim)
            risk_created, risk_updated, risk_resolved = self._persist_risks(risks, run, last_sim)
            run.incidents_created, run.incidents_updated = inc_created, inc_updated
            run.risks_created, run.risks_updated = risk_created, risk_updated
            run.summary = {
                "incidents": {"created": inc_created, "updated": inc_updated, "resolved": inc_resolved, "findings": [{"type": f.type, "severity": f.severity, "title": f.title} for f in operational]},
                "risks": {"created": risk_created, "updated": risk_updated, "resolved": risk_resolved, "findings": [{"type": f.type, "severity": f.severity, "title": f.title} for f in risks]},
            }
            run.status = RunStatus.COMPLETED
            run.finished_at = utcnow()
            state.detection_status = "IDLE"
            state.last_detection_id = run.id
            state.last_detection_at = run.finished_at
            self.db.commit()
            log_event(log, "detection.completed", run_id=run.id, incidents_created=inc_created, incidents_updated=inc_updated, incidents_resolved=inc_resolved, risks_created=risk_created, risks_updated=risk_updated, risks_resolved=risk_resolved)
            return run
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            run = self.db.merge(DetectionRun(id=run.id, engine="detection", status=RunStatus.FAILED, error=str(exc), finished_at=utcnow()))
            st = self.db.get(SystemState, 1)
            if st:
                st.detection_status = "ERROR"
            self.db.commit()
            log_event(log, "detection.failed", level=40, run_id=run.id, error=str(exc))
            raise

    def _last_simulation_id(self, state: SystemState):
        if state.last_simulation_id:
            sim = self.db.get(SimulationRun, state.last_simulation_id)
            if sim and sim.scenario.value != "reset":
                return sim.id
        return None

    # ---- persistence ----------------------------------------------------------------------
    def _persist_incidents(self, findings: list[Finding], run: DetectionRun, sim_id) -> tuple[int, int, int]:
        open_incidents = {i.dedupe_key: i for i in self.db.scalars(select(Incident).where(Incident.status != IncidentStatus.RESOLVED, Incident.source == DetectionSource.OPERATIONAL_ANOMALY_ENGINE)).all()}
        created = updated = 0
        seen: set[str] = set()
        for f in findings:
            seen.add(f.dedupe_key)
            existing = open_incidents.get(f.dedupe_key)
            if existing:
                existing.severity = Severity(max_severity(existing.severity.value, f.severity))
                existing.title, existing.description = f.title, f.description
                existing.affected_metrics, existing.evidence, existing.estimated_impact = f.affected_metrics, f.evidence, f.impact
                existing.occurrence_count += 1
                existing.detection_run_id = run.id
                existing.simulation_run_id = existing.simulation_run_id or sim_id
                updated += 1
                log_event(log, "incident.updated", incident_id=existing.id, type=f.type, severity=f.severity, occurrence=existing.occurrence_count)
                continue
            inc = Incident(
                incident_type=IncidentType(f.type), source=DetectionSource.OPERATIONAL_ANOMALY_ENGINE, severity=Severity(f.severity), status=IncidentStatus.DETECTED,
                machine_id=f.machine_id, production_line_id=f.production_line_id, detected_at=run.window_end or utcnow(), title=f.title, description=f.description,
                affected_metrics=f.affected_metrics, estimated_impact=f.impact, evidence=f.evidence, detection_run_id=run.id, simulation_run_id=sim_id, dedupe_key=f.dedupe_key,
            )
            self.db.add(inc)
            self.db.flush()
            created += 1
            log_event(log, "incident.created", incident_id=inc.id, type=f.type, severity=f.severity, machine_id=f.machine_id, production_line_id=f.production_line_id, title=f.title, estimated_impact=f.impact.get("value"))
        resolved = 0
        for key, inc in open_incidents.items():
            if key not in seen:
                inc.status = IncidentStatus.RESOLVED
                inc.resolved_at = utcnow()
                resolved += 1
                log_event(log, "incident.resolved", incident_id=inc.id, type=inc.incident_type.value, reason="condition no longer detected")
        return created, updated, resolved

    def _persist_risks(self, findings: list[Finding], run: DetectionRun, sim_id) -> tuple[int, int, int]:
        open_risks = {r.dedupe_key: r for r in self.db.scalars(select(BusinessRisk).where(BusinessRisk.status != RiskStatus.RESOLVED)).all()}
        created = updated = 0
        seen: set[str] = set()
        for f in findings:
            seen.add(f.dedupe_key)
            existing = open_risks.get(f.dedupe_key)
            if existing:
                existing.severity = Severity(max_severity(existing.severity.value, f.severity))
                existing.title, existing.description = f.title, f.description
                existing.supporting_metrics, existing.evidence, existing.estimated_impact = f.affected_metrics, f.evidence, f.impact
                existing.occurrence_count += 1
                existing.detection_run_id = run.id
                updated += 1
                log_event(log, "risk.updated", risk_id=existing.id, type=f.type, severity=f.severity)
                continue
            risk = BusinessRisk(
                risk_type=RiskType(f.type), source=DetectionSource.BUSINESS_RISK_ENGINE, severity=Severity(f.severity), status=RiskStatus.OPEN,
                entity_type=f.entity_type, entity_id=f.entity_id, entity_name=f.entity_name, detected_at=run.window_end or utcnow(), title=f.title, description=f.description,
                supporting_metrics=f.affected_metrics, estimated_impact=f.impact, evidence={**f.evidence, "simulation_run_id": str(sim_id) if sim_id else None, "production_line_id": str(f.production_line_id) if f.production_line_id else None, "product_id": str(f.product_id) if f.product_id else None},
                detection_run_id=run.id, dedupe_key=f.dedupe_key,
            )
            self.db.add(risk)
            self.db.flush()
            created += 1
            log_event(log, "risk.created", risk_id=risk.id, type=f.type, severity=f.severity, entity=f.entity_name, title=f.title, estimated_impact=f.impact.get("value"))
        resolved = 0
        for key, r in open_risks.items():
            if key not in seen:
                r.status = RiskStatus.RESOLVED
                r.resolved_at = utcnow()
                resolved += 1
                log_event(log, "risk.resolved", risk_id=r.id, type=r.risk_type.value)
        return created, updated, resolved


def summarize_findings(findings: list[Finding]) -> list[dict[str, Any]]:
    return [{"type": f.type, "severity": f.severity, "title": f.title, "dedupe_key": f.dedupe_key} for f in findings]
