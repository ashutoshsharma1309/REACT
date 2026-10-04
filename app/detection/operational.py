"""Operational anomaly detection (rule-based, statistical, deterministic).

Detects abnormal temperature / vibration / power / pressure, sensor faults, production
drops, defect spikes, downtime, and correlated multi-sensor degradation. Every finding
carries the record ids that justify it (sensor readings -> sensor -> machine -> line ->
production metrics -> component), so it can be audited and used as agent evidence later.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.detection.base import Finding, LineEconomics, ids, max_severity, mean, pct_change, production_windows, std, window_dict
from app.detection.config import DetectionConfig
from app.impact import ImpactEngine
from app.models import HistoricalIncident, Inventory, Machine, Sensor, SensorReading, SupplierBatch
from app.models.enums import BatchStatus, MachineStatus
from app.simulation import catalog as cat
from app.simulation.clock import is_operating


@dataclass
class SensorStats:
    sensor: Sensor
    n_recent: int = 0
    n_baseline: int = 0
    recent_mean: float = 0.0
    recent_std: float = 0.0
    recent_min: float = 0.0
    recent_max: float = 0.0
    baseline_mean: float = 0.0
    baseline_std: float = 0.0
    z: float = 0.0
    change: float = 0.0
    expected_recent_count: int = 0
    actual_recent_count: int = 0
    daily_means: list[tuple[str, float]] = field(default_factory=list)
    recent_ids: list[int] = field(default_factory=list)
    peak_reading_id: int | None = None
    fault: str | None = None  # stuck | implausible | dropout
    anomaly_severity: str | None = None
    direction: str | None = None  # high | low

    def summary(self) -> dict[str, Any]:
        return {
            "sensor_id": str(self.sensor.id), "sensor_code": self.sensor.code, "sensor_type": self.sensor.sensor_type.value, "unit": self.sensor.unit,
            "recent_mean": round(self.recent_mean, 3), "recent_std": round(self.recent_std, 3), "recent_min": round(self.recent_min, 3), "recent_max": round(self.recent_max, 3),
            "baseline_mean": round(self.baseline_mean, 3), "baseline_std": round(self.baseline_std, 3), "z_score": round(self.z, 2), "pct_change": round(self.change * 100, 1),
            "warning_threshold": self.sensor.warning_threshold, "critical_threshold": self.sensor.critical_threshold, "normal_range": [self.sensor.normal_min, self.sensor.normal_max],
            "n_recent": self.n_recent, "n_baseline": self.n_baseline, "daily_means": self.daily_means, "fault": self.fault, "severity": self.anomaly_severity, "direction": self.direction,
        }


class OperationalAnomalyDetector:
    def __init__(self, db: Session, config: DetectionConfig | None = None, impact: ImpactEngine | None = None) -> None:
        self.db = db
        self.cfg = config or DetectionConfig()
        self.impact = impact or ImpactEngine()

    # ---- entry point --------------------------------------------------------------------
    def run(self, now: datetime) -> list[Finding]:
        econ = LineEconomics(self.db)
        machines = self.db.scalars(select(Machine).options(selectinload(Machine.sensors), selectinload(Machine.components), selectinload(Machine.production_line))).all()
        readings = self._load_readings(now)
        findings: list[Finding] = []
        line_findings: dict[uuid.UUID, list[Finding]] = defaultdict(list)
        # production-level analysis first (used as corroboration for machine findings)
        line_context: dict[uuid.UUID, dict[str, Any]] = {}
        for line in econ.lines.values():
            ctx, lf = self._analyse_line(line, now, econ)
            line_context[line.id] = ctx
            line_findings[line.id].extend(lf)
        machine_keys_by_line: dict[uuid.UUID, list[dict[str, str]]] = defaultdict(list)
        for machine in machines:
            stats = [self._analyse_sensor(s, readings.get(s.id, []), now) for s in machine.sensors]
            mf = self._machine_findings(machine, stats, line_context.get(machine.production_line_id, {}), econ, now)
            findings.extend(mf)
            machine_keys_by_line[machine.production_line_id].extend({"dedupe_key": f.dedupe_key, "type": f.type, "severity": f.severity, "machine_id": str(machine.id)} for f in mf if f.type != "SENSOR_FAULT")
        for line_id, lf in line_findings.items():
            for f in lf:
                f.evidence["correlated_machine_findings"] = machine_keys_by_line.get(line_id, [])
                if machine_keys_by_line.get(line_id):
                    f.description += " Correlated machine-level finding(s): " + ", ".join(k["type"] for k in machine_keys_by_line[line_id]) + "."
            findings.extend(lf)
        return findings

    # ---- data loading -------------------------------------------------------------------
    def _load_readings(self, now: datetime) -> dict[uuid.UUID, list[tuple[int, datetime, float]]]:
        start = now - timedelta(days=self.cfg.baseline_days + self.cfg.baseline_gap_days + 1)
        rows = self.db.execute(select(SensorReading.sensor_id, SensorReading.id, SensorReading.timestamp, SensorReading.value).where(SensorReading.timestamp >= start, SensorReading.timestamp <= now).order_by(SensorReading.timestamp)).all()
        out: dict[uuid.UUID, list[tuple[int, datetime, float]]] = defaultdict(list)
        for sid, rid, ts, v in rows:
            out[sid].append((rid, ts, v))
        return out

    # ---- sensor statistics --------------------------------------------------------------
    def _analyse_sensor(self, sensor: Sensor, rows: list[tuple[int, datetime, float]], now: datetime) -> SensorStats:
        st = SensorStats(sensor=sensor)
        interval = max(settings.sensor_interval_minutes, 1)
        n_recent_target = int(self.cfg.recent_hours * 60 / interval)
        operating = [(rid, ts, v) for rid, ts, v in rows if is_operating(ts)]
        recent = operating[-n_recent_target:] if n_recent_target else []
        gap_start = now - timedelta(days=self.cfg.baseline_gap_days)
        base_start = gap_start - timedelta(days=self.cfg.baseline_days)
        baseline = [v for _, ts, v in operating if base_start <= ts < gap_start]
        st.n_recent, st.n_baseline = len(recent), len(baseline)
        # dropout: count of all readings in the last 24 wall-clock hours vs expected
        last24 = [r for r in rows if r[1] > now - timedelta(hours=24)]
        st.expected_recent_count = int(24 * 60 / interval)
        st.actual_recent_count = len(last24)
        if not recent:
            if st.actual_recent_count < st.expected_recent_count * self.cfg.dropout_ratio:
                st.fault = "dropout"
            return st
        vals = [v for _, _, v in recent]
        st.recent_ids = [rid for rid, _, _ in recent]
        st.recent_mean, st.recent_std, st.recent_min, st.recent_max = mean(vals), std(vals), min(vals), max(vals)
        st.peak_reading_id = max(recent, key=lambda r: r[2])[0]
        if baseline:
            st.baseline_mean, st.baseline_std = mean(baseline), std(baseline)
        floor_std = max(st.baseline_std, abs(st.baseline_mean) * 0.01, 1e-6)
        st.z = (st.recent_mean - st.baseline_mean) / floor_std if baseline else 0.0
        st.change = pct_change(st.recent_mean, st.baseline_mean)
        # daily means of the last 3 operating days (trend evidence)
        by_day: dict[str, list[float]] = defaultdict(list)
        for _, ts, v in operating[-n_recent_target * 3 :]:
            by_day[ts.date().isoformat()].append(v)
        st.daily_means = [(d, round(mean(vs), 3)) for d, vs in sorted(by_day.items())][-3:]
        # ---- faults ----
        if st.actual_recent_count < st.expected_recent_count * self.cfg.dropout_ratio:
            st.fault = "dropout"
            return st
        tail_run = 1
        for i in range(len(operating) - 1, 0, -1):
            if abs(operating[i][2] - operating[i - 1][2]) < 1e-9:
                tail_run += 1
            else:
                break
        if tail_run >= self.cfg.stuck_min_points:
            st.fault = "stuck"
            st.n_recent = tail_run
            return st
        implausible_hi = st.recent_max > sensor.critical_threshold * self.cfg.implausible_multiple_of_critical
        zero_share = sum(1 for v in vals if v <= 0) / len(vals)
        implausible_zero = sensor.sensor_type.value in ("TEMPERATURE", "VIBRATION", "POWER") and zero_share >= 0.2
        if implausible_hi or implausible_zero:
            st.fault = "implausible"
            return st
        # ---- level / statistical anomalies ----
        stype = sensor.sensor_type.value
        if stype in ("TEMPERATURE", "VIBRATION", "POWER", "PRESSURE"):
            if st.recent_mean >= sensor.critical_threshold:
                st.anomaly_severity, st.direction = "CRITICAL", "high"
            elif st.recent_mean >= sensor.warning_threshold:
                st.anomaly_severity, st.direction = "HIGH", "high"
            elif st.n_baseline >= self.cfg.min_baseline_points and st.z >= self.cfg.z_threshold and st.change >= self.cfg.min_pct_change_for_statistical_anomaly:
                st.anomaly_severity, st.direction = "MEDIUM", "high"
        if stype == "PRESSURE" and st.anomaly_severity is None:
            if st.recent_mean <= sensor.normal_min * 0.9:
                st.anomaly_severity, st.direction = "HIGH", "low"
            elif st.n_baseline >= self.cfg.min_baseline_points and st.z <= -self.cfg.z_threshold and st.change <= -0.08:
                st.anomaly_severity, st.direction = "MEDIUM", "low"
        if stype == "RPM" and st.n_baseline >= self.cfg.min_baseline_points and abs(st.z) >= self.cfg.z_threshold and abs(st.change) >= 0.04:
            st.anomaly_severity, st.direction = "LOW", "high" if st.z > 0 else "low"
        return st

    # ---- machine-level reasoning --------------------------------------------------------
    def _machine_findings(self, machine: Machine, stats: list[SensorStats], line_ctx: dict[str, Any], econ: LineEconomics, now: datetime) -> list[Finding]:
        findings: list[Finding] = []
        line = machine.production_line
        faults = [s for s in stats if s.fault]
        anomalies = [s for s in stats if s.anomaly_severity and s.sensor.sensor_type.value != "RPM"]
        corroborating = [s for s in stats if s.anomaly_severity and s.sensor.sensor_type.value == "RPM"]
        normal = [s for s in stats if not s.fault and not s.anomaly_severity]
        window = window_dict(now - timedelta(hours=self.cfg.recent_hours), now, baseline_days=self.cfg.baseline_days, baseline_gap_days=self.cfg.baseline_gap_days)
        production_ok = not (line_ctx.get("output_drop", 0) >= self.cfg.production_drop_pct or line_ctx.get("defect_ratio", 1) >= self.cfg.defect_spike_ratio)

        for s in faults:
            sev = "MEDIUM" if machine.criticality.value in ("HIGH", "CRITICAL") else "LOW"
            desc = {
                "stuck": f"{s.sensor.code} has reported a constant value ({s.recent_mean:.3g} {s.sensor.unit}) for the last {s.n_recent} readings.",
                "implausible": f"{s.sensor.code} reported physically implausible values (max {s.recent_max:.3g} {s.sensor.unit}, min {s.recent_min:.3g}) against a critical threshold of {s.sensor.critical_threshold}.",
                "dropout": f"{s.sensor.code} delivered {s.actual_recent_count} of {s.expected_recent_count} expected readings in the last 24h.",
            }[s.fault]
            corroboration = f" The other {len(normal)} sensor(s) on {machine.name} are within baseline and line production is normal, so this is classified as a sensor fault rather than a machine problem." if normal and production_ok else ""
            findings.append(Finding(
                kind="incident", type="SENSOR_FAULT", severity=sev, dedupe_key=f"SENSOR_FAULT:{s.sensor.id}",
                title=f"Sensor fault ({s.fault}): {s.sensor.code} on {machine.name}", description=desc + corroboration,
                machine_id=machine.id, production_line_id=machine.production_line_id,
                affected_metrics={s.sensor.sensor_type.value.lower(): {"fault": s.fault, "recent_mean": round(s.recent_mean, 3), "recent_std": round(s.recent_std, 4)}},
                evidence={
                    "window": window, "sensor": s.summary(), "other_sensors": [o.summary() for o in stats if o is not s], "production_context": line_ctx.get("summary", {}),
                    "records": {"sensor_id": str(s.sensor.id), "sensor_reading_ids": s.recent_ids[:48], "machine_id": str(machine.id), "production_line_id": str(machine.production_line_id), "production_metric_ids": line_ctx.get("recent_metric_ids", [])},
                    "trace": ["sensor_reading", "sensor", "machine", "production_line", "production_metric"], "classification_rule": f"fault={s.fault}; correlated_anomalies={len(anomalies)}; production_ok={production_ok}",
                },
                impact={**self.impact.downtime_impact(0.0, 0.0).as_dict(), "value": 0.0, "description": "No production impact; monitoring blind spot on a " + machine.criticality.value.lower() + "-criticality machine."},
            ))

        if not anomalies:
            return findings
        types = {s.sensor.sensor_type.value for s in anomalies}
        recent_metrics = line_ctx.get("recent_metric_ids", [])
        hist = self._similar_history(machine, types)
        culprit = min(machine.components, key=lambda c: c.condition_score) if machine.components else None
        if len(types) >= 2 or (len(anomalies) == 1 and anomalies[0].anomaly_severity in ("HIGH", "CRITICAL") and not production_ok):
            sev = max_severity(*[s.anomaly_severity for s in anomalies])
            if {"VIBRATION", "TEMPERATURE"} <= types and sev == "HIGH":
                sev = "CRITICAL"
            parts = [f"{s.sensor.sensor_type.value.lower()} {s.change * 100:+.0f}% (mean {s.recent_mean:.3g} {s.sensor.unit}, z={s.z:.1f})" for s in anomalies + corroborating]
            prod_text = ""
            if line_ctx:
                prod_text = f" On {line.name}: output {-line_ctx.get('output_drop', 0) * 100:+.0f}% and defect rate {line_ctx.get('defect_ratio', 1):.1f}x versus the {self.cfg.production_baseline_days}-day baseline."
            culprit_text = f" Most likely component: {culprit.name} (condition {culprit.condition_score:.2f}, failure probability {culprit.failure_probability:.0%})." if culprit else ""
            lost_units = max(0.0, line_ctx.get("lost_units", 0.0))
            extra_defects = max(0.0, line_ctx.get("extra_defects", 0.0))
            hourly = self.impact.hourly_loss_for_line(line.capacity_units_per_day, econ.unit_margin(line.id)) if line else self.impact.config.default_hourly_loss
            projected = mean([h.downtime_hours for h in hist]) if hist else self.cfg.projected_downtime_hours_if_unaddressed
            impact = self.impact.machine_degradation_impact(lost_units, econ.unit_margin(line.id if line else None), extra_defects, econ.unit_cost(line.id if line else None), projected, hourly)
            findings.append(Finding(
                kind="incident", type="MACHINE_DEGRADATION", severity=sev, dedupe_key=f"MACHINE_DEGRADATION:{machine.id}",
                title=f"Correlated degradation on {machine.name}: {' + '.join(sorted(t.lower() for t in types))} rising",
                description=f"{machine.name} ({machine.code}) shows correlated sensor anomalies: " + "; ".join(parts) + "." + prod_text + culprit_text,
                machine_id=machine.id, production_line_id=machine.production_line_id,
                affected_metrics={**{s.sensor.sensor_type.value.lower(): {"recent_mean": round(s.recent_mean, 3), "baseline_mean": round(s.baseline_mean, 3), "pct_change": round(s.change * 100, 1), "z": round(s.z, 2)} for s in anomalies + corroborating}, **line_ctx.get("summary", {})},
                evidence={
                    "window": window, "sensors": [s.summary() for s in stats], "production_context": line_ctx.get("summary", {}),
                    "likely_component": {"id": str(culprit.id), "name": culprit.name, "type": culprit.component_type, "condition_score": culprit.condition_score, "failure_probability": culprit.failure_probability} if culprit else None,
                    "similar_historical_incidents": [{"id": str(h.id), "type": h.incident_type.value, "occurred_at": h.occurred_at.isoformat(), "root_cause": h.root_cause, "downtime_hours": h.downtime_hours, "estimated_loss": float(h.estimated_loss)} for h in hist],
                    "records": {"machine_id": str(machine.id), "production_line_id": str(machine.production_line_id), "sensor_ids": [str(s.sensor.id) for s in anomalies], "peak_reading_ids": [s.peak_reading_id for s in anomalies if s.peak_reading_id], "sensor_reading_ids": [rid for s in anomalies for rid in s.recent_ids[:24]], "production_metric_ids": recent_metrics, "component_id": str(culprit.id) if culprit else None, "historical_incident_ids": [str(h.id) for h in hist]},
                    "trace": ["sensor_reading", "sensor", "machine", "machine_component", "production_line", "production_metric", "historical_incident"],
                    "classification_rule": f"correlated_sensor_types={sorted(types)}; severity_rule=max(sensor severities), vibration+temperature HIGH -> CRITICAL",
                },
                impact=impact.as_dict(),
            ))
            return findings
        # single-sensor anomaly
        for s in anomalies:
            stype = s.sensor.sensor_type.value
            hourly = self.impact.hourly_loss_for_line(line.capacity_units_per_day, econ.unit_margin(line.id)) if line else self.impact.config.default_hourly_loss
            projected_hours = {"LOW": 2.0, "MEDIUM": 4.0, "HIGH": 8.0, "CRITICAL": 16.0}[s.anomaly_severity]
            impact = self.impact.downtime_impact(projected_hours, hourly)
            impact.description = f"Projected downtime exposure ({projected_hours:.0f}h) if the {stype.lower()} anomaly is not addressed"
            direction = "above" if s.direction == "high" else "below"
            findings.append(Finding(
                kind="incident", type=f"ABNORMAL_{stype}", severity=s.anomaly_severity, dedupe_key=f"ABNORMAL_{stype}:{s.sensor.id}",
                title=f"Abnormal {stype.lower()} on {machine.name} ({s.recent_mean:.3g} {s.sensor.unit})",
                description=f"{s.sensor.code} recent mean {s.recent_mean:.3g} {s.sensor.unit} is {abs(s.change) * 100:.0f}% {direction} its {self.cfg.baseline_days}-day baseline of {s.baseline_mean:.3g} (z={s.z:.1f}; warning {s.sensor.warning_threshold}, critical {s.sensor.critical_threshold}). Other sensors on the machine are within baseline.",
                machine_id=machine.id, production_line_id=machine.production_line_id,
                affected_metrics={stype.lower(): {"recent_mean": round(s.recent_mean, 3), "baseline_mean": round(s.baseline_mean, 3), "pct_change": round(s.change * 100, 1), "z": round(s.z, 2)}},
                evidence={
                    "window": window, "sensor": s.summary(), "other_sensors": [o.summary() for o in stats if o is not s], "production_context": line_ctx.get("summary", {}),
                    "similar_historical_incidents": [{"id": str(h.id), "type": h.incident_type.value, "root_cause": h.root_cause} for h in hist],
                    "records": {"sensor_id": str(s.sensor.id), "sensor_reading_ids": s.recent_ids[:48], "peak_reading_id": s.peak_reading_id, "machine_id": str(machine.id), "production_line_id": str(machine.production_line_id), "production_metric_ids": recent_metrics, "historical_incident_ids": [str(h.id) for h in hist]},
                    "trace": ["sensor_reading", "sensor", "machine", "production_line", "production_metric"],
                },
                impact=impact.as_dict(),
            ))
        return findings

    def _similar_history(self, machine: Machine, types: set[str]) -> list[HistoricalIncident]:
        rows = self.db.scalars(select(HistoricalIncident).order_by(HistoricalIncident.occurred_at.desc())).all()
        out = []
        for h in rows:
            same_machine = h.machine_id == machine.id
            leading = (h.leading_indicators or {}).get("sensor_gain_at_failure", {}) or {}
            overlap = types & set(leading)
            if same_machine or len(overlap) >= 2 or (len(overlap) >= 1 and h.incident_type.value == "MACHINE_DEGRADATION"):
                out.append(h)
        return out[:5]

    # ---- line-level analysis --------------------------------------------------------------
    def _analyse_line(self, line, now: datetime, econ: LineEconomics) -> tuple[dict[str, Any], list[Finding]]:
        recent, baseline = production_windows(self.db, line.id, now.date(), self.cfg.production_recent_days, self.cfg.production_baseline_days)
        if not recent or not baseline:
            return {}, []
        r_out, b_out = mean(m.output_units for m in recent), mean(m.output_units for m in baseline)
        r_plan, b_plan = mean(m.planned_units for m in recent), mean(m.planned_units for m in baseline)
        r_def, b_def = mean(m.defect_rate for m in recent), mean(m.defect_rate for m in baseline)
        r_dt, b_dt = sum(m.downtime_hours for m in recent), mean(m.downtime_hours for m in baseline) * len(recent)
        output_drop = 1 - r_out / b_out if b_out else 0.0
        vs_plan = r_out / r_plan if r_plan else 1.0
        defect_ratio = r_def / b_def if b_def else 1.0
        lost_units = max(0.0, (b_out - r_out) * len(recent))
        extra_defects = max(0.0, sum(m.defect_units for m in recent) - b_def * r_out * len(recent))
        summary = {
            "line_code": line.code, "recent_days": len(recent), "recent_avg_output": round(r_out, 1), "baseline_avg_output": round(b_out, 1), "output_drop_pct": round(output_drop * 100, 1),
            "recent_avg_planned": round(r_plan, 1), "baseline_avg_planned": round(b_plan, 1), "output_vs_plan": round(vs_plan, 3), "recent_avg_utilization": round(mean(m.utilization for m in recent), 3),
            "recent_defect_rate": round(r_def, 4), "baseline_defect_rate": round(b_def, 4), "defect_ratio": round(defect_ratio, 2), "recent_downtime_hours": round(r_dt, 1), "baseline_downtime_hours": round(b_dt, 1),
        }
        ctx = {"summary": summary, "output_drop": output_drop, "defect_ratio": defect_ratio, "lost_units": lost_units, "extra_defects": extra_defects, "recent_metric_ids": ids(recent), "baseline_metric_ids": ids(baseline, cap=40)}
        findings: list[Finding] = []
        window = window_dict(recent[0].metric_date, recent[-1].metric_date, baseline_days=self.cfg.production_baseline_days)
        machine_ids = [str(m.id) for m in line.machines]
        unit_margin = econ.unit_margin(line.id)
        hourly = self.impact.hourly_loss_for_line(line.capacity_units_per_day, unit_margin)
        supply = self._supply_context(line, econ)
        base_records = {"production_line_id": str(line.id), "production_metric_ids": ids(recent), "baseline_metric_ids": ids(baseline, cap=40), "machine_ids": machine_ids, **supply["records"]}

        if output_drop >= self.cfg.production_drop_pct and vs_plan < self.cfg.production_vs_plan_ratio:
            sev = "CRITICAL" if output_drop >= 0.35 else "HIGH" if output_drop >= 0.25 else "MEDIUM"
            findings.append(Finding(
                kind="incident", type="PRODUCTION_DROP", severity=sev, dedupe_key=f"PRODUCTION_DROP:{line.id}", production_line_id=line.id,
                title=f"Production drop on {line.name}: {output_drop * 100:.0f}% below baseline",
                description=f"{line.name} produced {r_out:.0f} units/day over the last {len(recent)} working days versus a baseline of {b_out:.0f} ({output_drop * 100:.0f}% lower) while the plan stayed at {r_plan:.0f}/day (output at {vs_plan:.0%} of plan)." + supply["text"],
                affected_metrics={"output_units": {"recent_avg": round(r_out, 1), "baseline_avg": round(b_out, 1), "pct_change": round(-output_drop * 100, 1)}, "planned_units": {"recent_avg": round(r_plan, 1)}, "utilization": summary["recent_avg_utilization"]},
                evidence={"window": window, "production": summary, "supply_context": supply["detail"], "records": base_records, "trace": ["production_metric", "production_line", "machine", "inventory", "supplier_batch"], "rule": f"output_drop>={self.cfg.production_drop_pct} and output/plan<{self.cfg.production_vs_plan_ratio}"},
                impact=self.impact.production_loss(lost_units, unit_margin).as_dict(),
            ))
        if defect_ratio >= self.cfg.defect_spike_ratio and r_def >= self.cfg.defect_min_rate:
            sev = "HIGH" if defect_ratio >= 3 else "MEDIUM"
            findings.append(Finding(
                kind="incident", type="DEFECT_SPIKE", severity=sev, dedupe_key=f"DEFECT_SPIKE:{line.id}", production_line_id=line.id,
                title=f"Defect spike on {line.name}: {r_def * 100:.1f}% ({defect_ratio:.1f}x baseline)",
                description=f"Defect rate on {line.name} averaged {r_def * 100:.1f}% over the last {len(recent)} working days against a baseline of {b_def * 100:.1f}%, i.e. {defect_ratio:.1f}x." + supply["text"],
                affected_metrics={"defect_rate": {"recent": round(r_def, 4), "baseline": round(b_def, 4), "ratio": round(defect_ratio, 2)}, "defect_units_recent": sum(m.defect_units for m in recent)},
                evidence={"window": window, "production": summary, "supply_context": supply["detail"], "records": base_records, "trace": ["production_metric", "production_line", "machine", "supplier_batch"], "rule": f"defect_ratio>={self.cfg.defect_spike_ratio} and rate>={self.cfg.defect_min_rate}"},
                impact=self.impact.defect_cost(extra_defects, econ.unit_cost(line.id)).as_dict(),
            ))
        down_machines = [m for m in line.machines if m.status == MachineStatus.DOWN]
        max_day = max(recent, key=lambda m: m.downtime_hours)
        if r_dt >= self.cfg.downtime_hours_threshold_recent or max_day.downtime_hours >= self.cfg.downtime_single_day_threshold or down_machines:
            sev = "CRITICAL" if r_dt >= 16 or down_machines else "HIGH" if r_dt >= 12 else "MEDIUM"
            extra = max(0.0, r_dt - b_dt)
            findings.append(Finding(
                kind="incident", type="MACHINE_DOWNTIME", severity=sev, dedupe_key=f"MACHINE_DOWNTIME:{line.id}", production_line_id=line.id, machine_id=down_machines[0].id if down_machines else None,
                title=f"Elevated downtime on {line.name}: {r_dt:.1f}h in {len(recent)} days",
                description=f"{line.name} accumulated {r_dt:.1f}h of downtime over the last {len(recent)} working days (baseline {b_dt:.1f}h; worst day {max_day.metric_date.isoformat()} with {max_day.downtime_hours:.1f}h)." + (f" Machines down: {', '.join(m.code for m in down_machines)}." if down_machines else ""),
                affected_metrics={"downtime_hours": {"recent_total": round(r_dt, 1), "baseline_total": round(b_dt, 1), "worst_day": max_day.metric_date.isoformat(), "worst_day_hours": max_day.downtime_hours}},
                evidence={"window": window, "production": summary, "records": {**base_records, "worst_day_metric_id": str(max_day.id), "down_machine_ids": [str(m.id) for m in down_machines]}, "trace": ["production_metric", "production_line", "machine", "maintenance_record"], "rule": f"downtime>={self.cfg.downtime_hours_threshold_recent}h/{self.cfg.production_recent_days}d or single day>={self.cfg.downtime_single_day_threshold}h"},
                impact=self.impact.downtime_impact(extra, hourly).as_dict(),
            ))
        return ctx, findings

    def _supply_context(self, line, econ: LineEconomics) -> dict[str, Any]:
        """Component shortages / delayed batches feeding this line (root-cause evidence for output drops)."""
        comps: set[str] = set()
        for p in econ.by_line.get(line.id, []):
            comps.update({**cat.BOM_BY_CATEGORY.get(p.category, {}), **cat.BOM_BY_SKU.get(p.sku, {})})
        if not comps:
            return {"text": "", "detail": {}, "records": {}}
        invs = self.db.scalars(select(Inventory).where(Inventory.component_type.in_(sorted(comps)))).all()
        short = [i for i in invs if i.quantity_available < i.safety_stock]
        delayed = self.db.scalars(select(SupplierBatch).where(SupplierBatch.component_type.in_(sorted(comps)), SupplierBatch.status == BatchStatus.DELAYED)).all()
        text = ""
        if short or delayed:
            bits = []
            if short:
                bits.append("component stock below safety level: " + ", ".join(f"{i.item_name} ({i.quantity_available}/{i.safety_stock})" for i in short))
            if delayed:
                bits.append("delayed supplier batches: " + ", ".join(f"{b.batch_number} ({b.component_type}, +{b.delay_days}d)" for b in delayed))
            text = " Supply context: " + "; ".join(bits) + "."
        return {
            "text": text,
            "detail": {"components": sorted(comps), "short_inventory": [{"id": str(i.id), "item": i.item_name, "quantity": i.quantity_available, "safety_stock": i.safety_stock} for i in short], "delayed_batches": [{"id": str(b.id), "batch": b.batch_number, "component": b.component_type, "delay_days": b.delay_days, "supplier_id": str(b.supplier_id)} for b in delayed]},
            "records": {"inventory_ids": [str(i.id) for i in short], "supplier_batch_ids": [str(b.id) for b in delayed]},
        }
