"""Signal collection: turns a subject (incident / opportunity / risk) into a flat set of named,
sourced observations by calling the tool layer. Hypotheses are evaluated against these signals.

Every Observation keeps the ids of the records it was derived from, so the evidence built on
it is traceable (Incident -> Signal -> Evidence -> Hypothesis -> Conclusion).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BusinessRisk, Incident, Inventory, Machine, Opportunity, Product, ProductionLine, Supplier, SupplierBatch
from app.investigation import tools as T
from app.simulation import catalog as cat

TraceFn = Callable[..., None]


@dataclass
class Observation:
    signal: str
    value: Any
    source_type: str
    source_ids: list[str] = field(default_factory=list)
    unit: str | None = None
    baseline: float | None = None
    timestamp: datetime | None = None
    description: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"signal": self.signal, "value": self.value, "unit": self.unit, "baseline": self.baseline, "source_type": self.source_type, "source_ids": self.source_ids[:20], "source_count": len(self.source_ids), "timestamp": self.timestamp.isoformat() if self.timestamp else None, "description": self.description, "details": self.details}


class SignalSet:
    def __init__(self, subject_kind: str, subject_type: str, scope: dict[str, Any], domain: str = "manufacturing") -> None:
        self.domain = domain  # domain pack whose hypotheses and impact formulas apply
        self.subject_kind = subject_kind  # incident | opportunity | risk
        self.subject_type = subject_type  # e.g. MACHINE_DEGRADATION, DEMAND_OPPORTUNITY, SUPPLIER_DELAY
        self.scope = scope  # ids / names describing what was investigated
        self.observations: dict[str, Observation] = {}
        self.tool_calls: list[dict[str, Any]] = []

    def add(self, signal: str, value: Any, source_type: str, source_ids: list[str] | None = None, unit: str | None = None, baseline: float | None = None, timestamp: datetime | None = None, description: str = "", **details: Any) -> Observation:
        obs = Observation(signal, value, source_type, [str(s) for s in (source_ids or [])], unit, baseline, timestamp, description, details)
        self.observations[signal] = obs
        return obs

    def get(self, signal: str) -> Observation | None:
        return self.observations.get(signal)

    def value(self, signal: str, default: Any = None) -> Any:
        obs = self.observations.get(signal)
        return default if obs is None or obs.value is None else obs.value

    def has(self, signal: str) -> bool:
        obs = self.observations.get(signal)
        return obs is not None and obs.value is not None

    def as_dict(self) -> dict[str, Any]:
        return {"domain": self.domain, "subject_kind": self.subject_kind, "subject_type": self.subject_type, "scope": self.scope, "observations": {k: v.as_dict() for k, v in self.observations.items()}, "tool_calls": self.tool_calls}


# ------------------------------------------------------------------------------------------------
def collect_signals(db: Session, subject: Incident | Opportunity | BusinessRisk, trace: TraceFn) -> SignalSet:
    from app.domains import MERCHANT, current_domain

    if current_domain(db) == MERCHANT and not isinstance(subject, Incident):
        from app.domains.merchant.signals import collect_merchant_signals

        return collect_merchant_signals(db, subject, trace)
    if isinstance(subject, Incident):
        return _incident_signals(db, subject, trace)
    if isinstance(subject, Opportunity):
        return _opportunity_signals(db, subject, trace)
    return _risk_signals(db, subject, trace)


def _call(signals: SignalSet, trace: TraceFn, name: str, db: Session, **kwargs: Any) -> dict[str, Any] | None:
    """Run a tool, record it in the signal set and the trace; a failing tool is recorded, not fatal."""
    try:
        data = T.run_tool(name, db, **kwargs)
    except T.ToolError as exc:
        signals.tool_calls.append({"tool": name, "ok": False, "error": exc.message})
        trace("tool_failed", f"{name} failed: {exc.message}", tool=name)
        return None
    count = data.get("count") if isinstance(data, dict) else None
    signals.tool_calls.append({"tool": name, "ok": True, "count": count, "params": {k: str(v) for k, v in kwargs.items() if v is not None}})
    trace("tool", f"{name}: retrieved {count if count is not None else 'n/a'} record(s)", tool=name, count=count)
    return data


def _product_scope(db: Session, product: Product | None, line: ProductionLine | None, category: str | None) -> tuple[list[Product], str | None, ProductionLine | None]:
    if product is not None:
        return [product], product.category, product.production_line
    if category:
        prods = list(db.scalars(select(Product).where(Product.category == category)).all())
        return prods, category, prods[0].production_line if prods else line
    if line is not None:
        prods = list(db.scalars(select(Product).where(Product.production_line_id == line.id)).all())
        return prods, prods[0].category if prods else None, line
    return [], None, None


# ---- incidents ----------------------------------------------------------------------------------
def _incident_signals(db: Session, inc: Incident, trace: TraceFn) -> SignalSet:
    machine = db.get(Machine, inc.machine_id) if inc.machine_id else None
    line = db.get(ProductionLine, inc.production_line_id) if inc.production_line_id else (machine.production_line if machine else None)
    S = SignalSet("incident", inc.incident_type.value, {"incident_id": str(inc.id), "machine_id": str(machine.id) if machine else None, "machine_code": machine.code if machine else None, "production_line_id": str(line.id) if line else None, "line_code": line.code if line else None})
    now = inc.detected_at
    if machine is not None:
        _machine_sensor_signals(db, S, trace, machine, now)
        _maintenance_signals(db, S, trace, machine, now)
        _history_signals(db, S, trace, machine, line, now)
    if line is not None:
        _line_production_signals(db, S, trace, line, now)
        _line_context_signals(db, S, trace, line, machine, now)
    return S


def _machine_sensor_signals(db: Session, S: SignalSet, trace: TraceFn, machine: Machine, now: datetime) -> None:
    data = _call(S, trace, "get_machine_readings", db, machine_id=machine.id, hours=24)
    if not data:
        return
    faults, anomalies, normal = [], [], []
    for s in data["sensors"]:
        t = s["sensor_type"].lower()
        ids = s["reading_ids_recent"]
        S.add(f"{t}_change_pct", s["pct_change"], "sensor_reading", ids, "%", s["baseline_mean"], now, f"{s['sensor_code']} recent mean {s['recent_mean']} {s['unit']} vs baseline {s['baseline_mean']} ({s['pct_change']:+.1f}%)", sensor_id=s["sensor_id"], sensor_code=s["sensor_code"], recent_mean=s["recent_mean"], z=s["z_score"], peak_reading_id=s["peak_reading_id"])
        S.add(f"{t}_level", s["recent_mean"], "sensor_reading", ids, s["unit"], s["baseline_mean"], now, f"{s['sensor_code']} recent mean {s['recent_mean']} {s['unit']}", warning=s["warning_threshold"], critical=s["critical_threshold"], sensor_id=s["sensor_id"])
        S.add(f"{t}_z", s["z_score"], "sensor_reading", ids, "z", None, now, f"{s['sensor_code']} z-score {s['z_score']}", sensor_id=s["sensor_id"])
        S.add(f"{t}_above_warning", s["recent_mean"] >= s["warning_threshold"], "sensor_reading", ids, None, s["warning_threshold"], now, f"{s['sensor_code']} {'above' if s['recent_mean'] >= s['warning_threshold'] else 'below'} warning threshold {s['warning_threshold']} {s['unit']}", sensor_id=s["sensor_id"])
        S.add(f"{t}_above_critical", s["recent_mean"] >= s["critical_threshold"], "sensor_reading", ids, None, s["critical_threshold"], now, f"{s['sensor_code']} {'above' if s['recent_mean'] >= s['critical_threshold'] else 'below'} critical threshold {s['critical_threshold']} {s['unit']}", sensor_id=s["sensor_id"])
        if s["std_ratio"] is not None:
            S.add(f"{t}_std_ratio", s["std_ratio"], "sensor_reading", ids, "x", 1.0, now, f"{s['sensor_code']} variability {s['std_ratio']}x baseline", sensor_id=s["sensor_id"])
        if s["fault"]:
            faults.append(s)
        elif s["severity"]:
            anomalies.append(s)
        else:
            normal.append(s)
    physical = [s for s in data["sensors"] if s["sensor_type"] in ("TEMPERATURE", "VIBRATION", "PRESSURE")]
    S.add("sensor_fault_count", len(faults), "sensor_reading", [i for s in faults for i in s["reading_ids_recent"][:12]], None, None, now, f"{len(faults)} sensor(s) with fault signature ({', '.join(s['sensor_code'] + ':' + s['fault'] for s in faults) or 'none'})", faults=[{"sensor_id": s["sensor_id"], "code": s["sensor_code"], "fault": s["fault"]} for s in faults])
    S.add("anomalous_sensor_types", len({s["sensor_type"] for s in anomalies if s["sensor_type"] != "RPM"}), "sensor_reading", [i for s in anomalies for i in s["reading_ids_recent"][:12]], None, None, now, f"{len({s['sensor_type'] for s in anomalies if s['sensor_type'] != 'RPM'})} physically distinct sensor type(s) anomalous ({', '.join(sorted({s['sensor_type'].lower() for s in anomalies})) or 'none'})", anomalies=[{"sensor_id": s["sensor_id"], "type": s["sensor_type"], "severity": s["severity"]} for s in anomalies])
    others_normal = all(not s["severity"] for s in physical if not s["fault"]) and len([s for s in physical if not s["fault"]]) > 0
    S.add("other_physical_signals_normal", others_normal, "sensor_reading", [i for s in normal for i in s["reading_ids_recent"][:8]], None, None, now, "Other physical sensors on the machine are within baseline" if others_normal else "Other physical sensors on the machine are also abnormal", sensors=[s["sensor_code"] for s in normal])
    S.add("readings_discontinuous", any(s["fault"] in ("stuck", "dropout") for s in faults), "sensor_reading", [i for s in faults for i in s["reading_ids_recent"][:12]], None, None, now, "Sensor readings are discontinuous (stuck or missing)" if any(s["fault"] in ("stuck", "dropout") for s in faults) else "Sensor readings are continuous")
    S.add("implausible_readings", any(s["fault"] == "implausible" for s in faults), "sensor_reading", [i for s in faults for i in s["reading_ids_recent"][:12]], None, None, now, "Readings are physically implausible" if any(s["fault"] == "implausible" for s in faults) else "Readings are physically plausible")


def _maintenance_signals(db: Session, S: SignalSet, trace: TraceFn, machine: Machine, now: datetime) -> None:
    data = _call(S, trace, "get_maintenance_history", db, machine_id=machine.id, days=180)
    if not data:
        return
    summ = data["summary"]
    rec_ids = [r["id"] for r in data["records"][:10]]
    last_ts = datetime.fromisoformat(data["records"][0]["date"]) if data["records"] else now
    S.add("maintenance_overdue_days", summ["preventive_overdue_days"] if summ["preventive_overdue_days"] is not None else (summ["days_since_any_maintenance"] or 0), "maintenance_record", [summ["last_preventive_id"]] if summ["last_preventive_id"] else rec_ids, "days", summ["expected_preventive_interval_days"], now, f"Preventive maintenance overdue by {summ['preventive_overdue_days']} days (last preventive {summ['days_since_preventive']} days ago)" if summ["preventive_overdue_days"] is not None else "No preventive maintenance record in window", days_since_preventive=summ["days_since_preventive"])
    S.add("days_since_maintenance", summ["days_since_any_maintenance"] if summ["days_since_any_maintenance"] is not None else 999, "maintenance_record", rec_ids, "days", None, now, f"Last maintenance activity {summ['days_since_any_maintenance']} days ago" if summ["days_since_any_maintenance"] is not None else "No maintenance activity on record")
    recent_event = summ["days_since_any_maintenance"] is not None and summ["days_since_any_maintenance"] <= 7
    S.add("recent_maintenance_event", recent_event, "maintenance_record", rec_ids[:1], None, None, last_ts, f"Maintenance intervention {summ['days_since_any_maintenance']} days ago (possible workmanship / setup effect)" if recent_event else "No maintenance intervention in the last 7 days")
    S.add("corrective_count_90d", summ["corrective_count_90d"], "maintenance_record", [r["id"] for r in data["records"] if r["type"] == "CORRECTIVE"][:10], "records", None, now, f"{summ['corrective_count_90d']} corrective maintenance action(s) in the last 90 days")
    worst = data["worst_component"]
    if worst:
        S.add("worst_component_condition", worst["condition_score"], "machine_component", [worst["id"]], "score", 1.0, now, f"Most worn component: {worst['name']} ({worst['type']}) condition {worst['condition_score']:.2f}, failure probability {worst['failure_probability']:.0%}", component_type=worst["type"], component_name=worst["name"], failure_probability=worst["failure_probability"])
    bearings = [c for c in data["components"] if c["type"] == "BEARING"]
    if bearings:
        b = min(bearings, key=lambda c: c["condition_score"])
        S.add("bearing_condition_score", b["condition_score"], "machine_component", [b["id"]], "score", 1.0, now, f"{b['name']} condition {b['condition_score']:.2f} ({b['condition']}), {b['life_consumed_pct']}% of expected life consumed", component_id=b["id"], component_name=b["name"], failure_probability=b["failure_probability"])
        S.add("bearing_failure_probability", b["failure_probability"], "machine_component", [b["id"]], "probability", 0.02, now, f"{b['name']} failure probability {b['failure_probability']:.0%} (30-day)")
    heaters = [c for c in data["components"] if c["type"] in ("MOTOR", "HEATER", "DRIVE")]
    if heaters:
        h = min(heaters, key=lambda c: c["condition_score"])
        S.add("electrical_component_condition", h["condition_score"], "machine_component", [h["id"]], "score", 1.0, now, f"{h['name']} ({h['type']}) condition {h['condition_score']:.2f}", component_id=h["id"])
    seals = [c for c in data["components"] if c["type"] in ("SEAL", "PUMP")]
    if seals:
        sl = min(seals, key=lambda c: c["condition_score"])
        S.add("hydraulic_component_condition", sl["condition_score"], "machine_component", [sl["id"]], "score", 1.0, now, f"{sl['name']} ({sl['type']}) condition {sl['condition_score']:.2f}", component_id=sl["id"])


def _history_signals(db: Session, S: SignalSet, trace: TraceFn, machine: Machine, line: ProductionLine | None, now: datetime) -> None:
    data = _call(S, trace, "get_previous_incidents", db, machine_id=machine.id, production_line_id=line.id if line else None, limit=10)
    if not data:
        return
    hist = data["historical_incidents"]
    hist_ts = max((datetime.fromisoformat(h["occurred_at"]) for h in hist), default=now)
    same_machine = [h for h in hist if h["machine_id"] == str(machine.id)]
    bearing = [h for h in hist if "bearing" in (h["root_cause"] or "").lower() or h["leading_indicators"].get("sensor_gain_at_failure", {}).get("VIBRATION", 0) > 0.5]
    power = [h for h in hist if "element" in (h["root_cause"] or "").lower() or "power" in (h["root_cause"] or "").lower()]
    sensor = [h for h in hist if h["type"] == "SENSOR_FAULT"]
    S.add("similar_historical_incidents", len(hist), "historical_incident", [h["id"] for h in hist], "records", None, hist_ts, f"{len(hist)} historical incident(s) on this machine/line; {len(same_machine)} on this machine", root_causes=[h["root_cause"] for h in hist[:5]])
    S.add("historical_bearing_failures", len(bearing), "historical_incident", [h["id"] for h in bearing], "records", None, max((datetime.fromisoformat(h["occurred_at"]) for h in bearing), default=now), f"{len(bearing)} historical bearing-related failure(s) with similar leading indicators" if bearing else "No historical bearing failure with similar indicators", examples=[{"id": h["id"], "root_cause": h["root_cause"], "downtime_hours": h["downtime_hours"]} for h in bearing[:3]])
    S.add("historical_power_failures", len(power), "historical_incident", [h["id"] for h in power], "records", None, max((datetime.fromisoformat(h["occurred_at"]) for h in power), default=now), f"{len(power)} historical electrical/power-related failure(s)" if power else "No historical electrical/power failure")
    S.add("historical_sensor_faults", len(sensor), "historical_incident", [h["id"] for h in sensor], "records", None, max((datetime.fromisoformat(h["occurred_at"]) for h in sensor), default=now), f"{len(sensor)} historical sensor fault(s)" if sensor else "No historical sensor fault on record")
    S.add("historical_avg_downtime_hours", round(sum(h["downtime_hours"] for h in hist) / len(hist), 1) if hist else None, "historical_incident", [h["id"] for h in hist], "hours", None, now, "Average downtime of similar historical incidents")


def _line_production_signals(db: Session, S: SignalSet, trace: TraceFn, line: ProductionLine, now: datetime) -> None:
    data = _call(S, trace, "get_production_metrics", db, production_line_id=line.id, days=7, baseline_days=30)
    if not data:
        return
    s = data["summary"]
    ids = [m["id"] for m in data["recent"]]
    S.add("output_change_pct", s["output_change_pct"], "production_metric", ids, "%", s["baseline_avg_output"], now, f"{line.code} output {s['recent_avg_output']:.0f}/day vs baseline {s['baseline_avg_output']:.0f}/day ({s['output_change_pct']:+.1f}%)", recent_avg=s["recent_avg_output"])
    S.add("output_vs_plan", s["output_vs_plan"], "production_metric", ids, "ratio", 1.0, now, f"{line.code} output at {s['output_vs_plan']:.0%} of plan" if s["output_vs_plan"] is not None else "no plan")
    S.add("defect_ratio", s["defect_ratio"], "production_metric", ids, "x", 1.0, now, f"{line.code} defect rate {s['recent_defect_rate']:.2%} vs baseline {s['baseline_defect_rate']:.2%} ({s['defect_ratio']}x)" if s["defect_ratio"] is not None else "no baseline")
    S.add("downtime_recent_hours", s["recent_downtime_hours"], "production_metric", ids, "hours", s["baseline_downtime_hours_per_day"] * max(s["recent_days"], 1), now, f"{line.code} downtime {s['recent_downtime_hours']}h in {s['recent_days']} working days (baseline {s['baseline_downtime_hours_per_day'] * max(s['recent_days'], 1):.1f}h)")
    S.add("line_utilization_recent", s["recent_utilization"], "production_metric", ids, "ratio", s["baseline_utilization"], now, f"{line.code} utilisation {s['recent_utilization']:.0%} (baseline {s['baseline_utilization']:.0%})")
    S.add("line_power_change_pct", s["power_change_pct"], "production_metric", ids, "%", None, now, f"{line.code} energy consumption {s['power_change_pct']:+.1f}% vs baseline")
    S.add("line_power_variability_ratio", s["recent_power_std_ratio"], "production_metric", ids, "x", 1.0, now, f"{line.code} daily energy variability {s['recent_power_std_ratio']}x baseline" if s["recent_power_std_ratio"] is not None else "no baseline")


def _line_context_signals(db: Session, S: SignalSet, trace: TraceFn, line: ProductionLine, machine: Machine | None, now: datetime) -> None:
    data = _call(S, trace, "get_line_context", db, production_line_id=line.id, hours=24)
    if not data:
        return
    others = [m for m in data["machines"] if not machine or m["id"] != str(machine.id)]
    power_others = [m for m in others if "POWER" in m["anomalous_sensor_types"]]
    any_others = [m for m in others if m["anomalous_sensor_types"]]
    S.add("machines_with_power_anomaly", data["machines_with_power_anomaly"], "sensor_reading", [m["id"] for m in data["machines"] if "POWER" in m["anomalous_sensor_types"]], "machines", None, now, f"{data['machines_with_power_anomaly']} of {len(data['machines'])} machines on {line.code} show a power anomaly", machine_codes=[m["code"] for m in data["machines"] if "POWER" in m["anomalous_sensor_types"]])
    S.add("other_machines_with_anomaly", len(any_others), "sensor_reading", [m["id"] for m in any_others], "machines", None, now, f"{len(any_others)} other machine(s) on {line.code} are anomalous at the same time ({', '.join(m['code'] for m in any_others) or 'none'})")
    S.add("simultaneous_power_anomalies", len(power_others) >= 1 and data["machines_with_power_anomaly"] >= 2, "sensor_reading", [m["id"] for m in power_others], None, None, now, "Simultaneous power anomalies across multiple machines" if data["machines_with_power_anomaly"] >= 2 else "No simultaneous power anomaly on other machines")
    S.add("component_stock_below_safety", sum(1 for c in data["component_stock"] if c["below_safety"]), "inventory", [c["id"] for c in data["component_stock"] if c["below_safety"]], "items", None, now, f"{sum(1 for c in data['component_stock'] if c['below_safety'])} component(s) feeding {line.code} below safety stock", items=[c["component_type"] for c in data["component_stock"] if c["below_safety"]])
    S.add("delayed_component_batches", len(data["delayed_batches"]), "supplier_batch", [b["id"] for b in data["delayed_batches"]], "batches", None, now, f"{len(data['delayed_batches'])} delayed supplier batch(es) for {line.code} components")
    pq_ts = max((datetime.fromisoformat(b["received"]) for b in data["poor_quality_batches"] if b["received"]), default=now)
    S.add("poor_quality_batches", len(data["poor_quality_batches"]), "supplier_batch", [b["id"] for b in data["poor_quality_batches"]], "batches", None, pq_ts, f"{len(data['poor_quality_batches'])} recently received batch(es) with poor quality pass rate")


# ---- opportunities --------------------------------------------------------------------------------
def _opportunity_signals(db: Session, opp: Opportunity, trace: TraceFn) -> SignalSet:
    product = db.get(Product, opp.product_id) if opp.product_id else None
    line = db.get(ProductionLine, opp.production_line_id) if opp.production_line_id else None
    category = None
    rec = (opp.evidence or {}).get("records", {})
    if product is None and rec.get("product_ids"):
        p0 = db.get(Product, uuid.UUID(rec["product_ids"][0]))
        category = p0.category if p0 else None
    products, category, line = _product_scope(db, product, line, category)
    S = SignalSet("opportunity", opp.opportunity_type.value, {"opportunity_id": str(opp.id), "product_id": str(product.id) if product else None, "product": product.name if product else None, "category": category, "region": opp.region, "segment": opp.customer_segment, "production_line_id": str(line.id) if line else None})
    now = opp.detected_at
    _demand_signals(db, S, trace, product, category, line, opp.region, now)
    _market_signals(db, S, trace, product, category, opp.region, now)
    _supply_capacity_signals(db, S, trace, products, line, now)
    return S


def _demand_signals(db: Session, S: SignalSet, trace: TraceFn, product: Product | None, category: str | None, line: ProductionLine | None, region: str | None, now: datetime) -> None:
    kwargs: dict[str, Any] = {"days": 28}
    if product is not None:
        kwargs["product_id"] = product.id
    elif category:
        kwargs["category"] = category
    elif line is not None:
        kwargs["production_line_id"] = line.id
    data = _call(S, trace, "get_customer_orders", db, **kwargs)
    if not data:
        return
    g = data["growth"]
    ids = data["order_ids_recent"]
    label = product.name if product else category or (line.code if line else "scope")
    S.add("order_growth_pct", g["units_pct"], "order", ids, "%", data["previous"]["units"], now, f"Orders for {label}: {data['recent']['units']:.0f} units in the last 28 days vs {data['previous']['units']:.0f} ({g['units_pct']:+.1f}%)", recent_units=data["recent"]["units"], recent_orders=data["recent"]["orders"])
    S.add("revenue_growth_pct", g["revenue_pct"], "order", ids, "%", data["previous"]["revenue"], now, f"Revenue for {label} {g['revenue_pct']:+.1f}% (28d vs prior 28d)")
    S.add("customers_growth_pct", g["customers_pct"], "customer", ids, "%", data["previous"]["customers"], now, f"Active customers {data['recent']['customers']} vs {data['previous']['customers']} ({g['customers_pct']:+.1f}%)")
    S.add("sustained_growth_weeks", data["sustained_growth_weeks"], "order", ids, "weeks", None, now, f"Weekly demand has exceeded its trailing 4-week average for {data['sustained_growth_weeks']} consecutive week(s)", weekly=data["weekly_series"][-8:])
    growing_segments = [b for b in data["by_segment"] if b["growth_pct"] is not None and b["growth_pct"] >= 15]
    growing_regions = [b for b in data["by_region"] if b["growth_pct"] is not None and b["growth_pct"] >= 15]
    S.add("segments_growing", len(growing_segments), "order", ids, "segments", None, now, f"{len(growing_segments)} of {len(data['by_segment'])} customer segments growing >=15% ({', '.join(b['key'] for b in growing_segments) or 'none'})", segments=[{"segment": b["key"], "growth_pct": b["growth_pct"]} for b in data["by_segment"]])
    S.add("regions_growing", len(growing_regions), "order", ids, "regions", None, now, f"{len(growing_regions)} region(s) growing >=15% ({', '.join(b['key'] for b in growing_regions) or 'none'})", regions=[{"region": b["key"], "growth_pct": b["growth_pct"]} for b in data["by_region"]])
    if region:
        rb = next((b for b in data["by_region"] if b["key"] == region), None)
        S.add("region_growth_pct", rb["growth_pct"] if rb else None, "order", ids, "%", rb["previous_units"] if rb else None, now, f"{region}: {rb['recent_units']:.0f} vs {rb['previous_units']:.0f} units ({rb['growth_pct']:+.1f}%)" if rb and rb["growth_pct"] is not None else f"No prior demand from {region}")
        S.add("region_share_of_incremental", round(rb["incremental_units"] / g["incremental_units"], 3) if rb and g["incremental_units"] > 0 else None, "order", ids, "share", None, now, f"{region} accounts for {rb['incremental_units'] / g['incremental_units']:.0%} of incremental units" if rb and g["incremental_units"] > 0 else "no incremental units")
    conc = data["concentration"]
    S.add("top_customer_share_of_incremental", conc["top_customer_share_of_incremental"], "customer", [conc["top_customer_id"]] if conc["top_customer_id"] else [], "share", None, now, f"Largest single customer explains {conc['top_customer_share_of_incremental']:.0%} of the incremental demand" if conc["top_customer_share_of_incremental"] is not None else "No incremental demand to attribute")
    S.add("new_customers", conc["new_customers"], "customer", ids, "customers", None, now, f"{conc['new_customers']} customer(s) ordered for the first time in the window; {conc['repeat_customers']} repeat customers", repeat=conc["repeat_customers"])
    S.add("realised_margin", data["recent"]["realised_margin"], "order_item", ids, "ratio", product.target_margin if product else None, now, f"Realised margin {data['recent']['realised_margin']:.1%}" + (f" vs target {product.target_margin:.0%}" if product else "") if data["recent"]["realised_margin"] is not None else "no revenue")
    if product:
        S.add("margin_gap_points", round((product.target_margin - data["recent"]["realised_margin"]) * 100, 2) if data["recent"]["realised_margin"] is not None else None, "order_item", ids, "points", 0.0, now, "Gap between target and realised margin")
    S.add("avg_discount_pct", data["avg_discount_pct"], "order_item", ids, "%", None, now, f"Average discount versus list price {data['avg_discount_pct']:.1f}%")
    S.add("recent_orders", data["recent"]["orders"], "order", ids, "orders", data["previous"]["orders"], now, f"{data['recent']['orders']} orders in the last 28 days")


def _market_signals(db: Session, S: SignalSet, trace: TraceFn, product: Product | None, category: str | None, region: str | None, now: datetime) -> None:
    data = _call(S, trace, "get_market_signals", db, category=category, product_id=product.id if product else None, region=region, days=90)
    if data:
        summ = data["summary"]
        ids = [s["id"] for s in data["signals"]]
        pdg = summ.get("PRODUCT_DEMAND_GROWTH")
        S.add("market_product_growth_pct", pdg["avg_growth_rate_pct"] if pdg else None, "market_signal", [s["id"] for s in data["signals"] if s["type"] == "PRODUCT_DEMAND_GROWTH"], "%", None, now, f"Synthetic product demand-growth signals average {pdg['avg_growth_rate_pct']:+.1f}% ({pdg['count']} signals)" if pdg else "No product demand-growth signal", synthetic=True)
        cg = summ.get("CATEGORY_GROWTH") or summ.get("MARKET_DEMAND")
        S.add("market_category_growth_pct", cg["index_change_pct"] if cg else None, "market_signal", [s["id"] for s in data["signals"] if s["type"] in ("CATEGORY_GROWTH", "MARKET_DEMAND")], "%", None, now, f"Synthetic category demand index changed {cg['index_change_pct']:+.1f}% over 90 days" if cg else "No category market signal", synthetic=True)
        rg = summ.get("REGIONAL_DEMAND")
        S.add("regional_index_growth_pct", rg["index_change_pct"] if rg else None, "market_signal", [s["id"] for s in data["signals"] if s["type"] == "REGIONAL_DEMAND"], "%", None, now, f"Synthetic regional demand index for {region} changed {rg['index_change_pct']:+.1f}% over 90 days" if rg and region else "No regional market signal", synthetic=True)
        sd = summ.get("SEASONAL_DEMAND")
        seasonal = sd["latest_seasonal_index"] if sd else (cg["latest_seasonal_index"] if cg else None)
        S.add("seasonal_uplift_pct", round((seasonal - 1) * 100, 1) if seasonal else None, "market_signal", [s["id"] for s in data["signals"] if s["type"] == "SEASONAL_DEMAND"][-4:], "%", 0.0, now, f"Seasonal index currently {seasonal:.3f} ({(seasonal - 1) * 100:+.1f}%)" if seasonal else "No seasonal signal", synthetic=True)
        S.add("market_signal_count", data["count"], "market_signal", ids[:20], "signals", None, now, f"{data['count']} synthetic market signals in scope", synthetic=True)
    comp = _call(S, trace, "get_competitor_signals", db, category=category, region=region, days=90)
    if comp:
        summ = comp["summary"]
        S.add("competitor_availability_constraints", summ["availability_constraints"], "competitor_signal", [s["id"] for s in comp["signals"] if s["type"] == "AVAILABILITY"], "signals", None, now, f"{summ['availability_constraints']} synthetic competitor availability constraint(s) (min availability {summ['min_availability_score']:.2f})", min_availability=summ["min_availability_score"], synthetic=True)
        S.add("competitor_price_increase_pct", summ["price_change_total_pct"], "competitor_signal", [s["id"] for s in comp["signals"] if s["type"] == "PRICE_CHANGE"], "%", 0.0, now, f"Competitors raised prices by {summ['price_change_total_pct']:+.1f}% in total ({summ['price_change_count']} signals)", synthetic=True)
        S.add("competitor_discounting", summ["discounting_activity"], "competitor_signal", [s["id"] for s in comp["signals"] if s["type"] == "MARKET_ACTIVITY"], "signals", None, now, f"{summ['discounting_activity']} synthetic competitor discounting signal(s)", synthetic=True)
        S.add("competitor_launches", summ["launches"], "competitor_signal", [s["id"] for s in comp["signals"] if s["type"] == "PRODUCT_LAUNCH"], "signals", None, now, f"{summ['launches']} competitor product launch(es) in the category", synthetic=True)


def _supply_capacity_signals(db: Session, S: SignalSet, trace: TraceFn, products: list[Product], line: ProductionLine | None, now: datetime) -> None:
    if products and len(products) == 1:
        inv = _call(S, trace, "get_inventory_status", db, product_id=products[0].id, snapshot_days=28)
        if inv and inv["items"]:
            it = inv["items"][0]
            S.add("product_days_of_cover", it["days_of_cover"], "inventory", [it["id"]] + [s["id"] for s in it["snapshots"][-7:]], "days", None, now, f"{it['item_name']} finished-goods cover {it['days_of_cover']} days ({it['quantity_available']} units; safety stock {it['safety_stock']})", quantity=it["quantity_available"], safety_stock=it["safety_stock"], unit_value=it["unit_value"])
            S.add("product_stock_change_pct", it["stock_change_pct"], "inventory_snapshot", [s["id"] for s in it["snapshots"]][:28], "%", None, now, f"Finished-goods stock changed {it['stock_change_pct']:+.1f}% over 28 days" if it["stock_change_pct"] is not None else "no snapshots")
    if line is not None:
        capd = _call(S, trace, "get_capacity_status", db, production_line_id=line.id)
        if capd:
            S.add("line_utilization_14d", capd["utilization_14d"], "production_metric", capd["production_metric_ids"], "ratio", capd["baseline_utilization"], now, f"{line.code} utilisation {capd['utilization_14d']:.0%} over 14 days; spare {capd['spare_units_per_day']} units/day up to 95%", spare_units_per_day=capd["spare_units_per_day"], capacity=line.capacity_units_per_day)
            S.add("spare_units_per_day", capd["spare_units_per_day"], "production_metric", capd["production_metric_ids"], "units/day", None, now, f"{capd['spare_units_per_day']} units/day of spare capacity on {line.code}")
    comps: set[str] = set()
    for p in products:
        comps.update({**cat.BOM_BY_CATEGORY.get(p.category, {}), **cat.BOM_BY_SKU.get(p.sku, {})})
    if comps:
        sole, max_lead, min_cover, ids = [], 0, None, []
        for ctype in sorted(comps):
            sup = db.scalars(select(Supplier).where(Supplier.component_type == ctype)).all()
            if len(sup) == 1:
                sole.append(ctype)
            max_lead = max(max_lead, max((s.lead_time_days for s in sup), default=0))
            inv = db.scalar(select(Inventory).where(Inventory.component_type == ctype))
            if inv:
                cover = inv.quantity_available / max(inv.avg_daily_consumption, 1e-9)
                ids.append(str(inv.id))
                min_cover = cover if min_cover is None else min(min_cover, cover)
        S.add("component_sole_source", len(sole) > 0, "supplier", [str(s.id) for c in sole for s in db.scalars(select(Supplier).where(Supplier.component_type == c)).all()], None, None, now, f"Sole-sourced components: {', '.join(sole) or 'none'}", components=sole)
        S.add("component_max_lead_time_days", max_lead, "supplier", [], "days", None, now, f"Longest component lead time {max_lead} days")
        S.add("component_min_cover_days", round(min_cover, 1) if min_cover is not None else None, "inventory", ids, "days", None, now, f"Tightest component cover {min_cover:.1f} days" if min_cover is not None else "no component stock data")


# ---- risks ---------------------------------------------------------------------------------------
def _risk_signals(db: Session, risk: BusinessRisk, trace: TraceFn) -> SignalSet:
    ev = risk.evidence or {}
    rec = ev.get("records", {})
    line = db.get(ProductionLine, uuid.UUID(ev["production_line_id"])) if ev.get("production_line_id") else None
    product = db.get(Product, uuid.UUID(ev["product_id"])) if ev.get("product_id") else None
    S = SignalSet("risk", risk.risk_type.value, {"risk_id": str(risk.id), "entity_type": risk.entity_type, "entity_id": str(risk.entity_id) if risk.entity_id else None, "entity_name": risk.entity_name, "production_line_id": str(line.id) if line else None, "product_id": str(product.id) if product else None})
    now = risk.detected_at
    rtype = risk.risk_type.value
    if rtype == "SUPPLIER_DELAY" and risk.entity_id:
        supplier = db.get(Supplier, risk.entity_id)
        if supplier:
            _supplier_signals(db, S, trace, supplier, now)
    elif rtype == "INVENTORY_SHORTAGE" and risk.entity_id:
        inv = db.get(Inventory, risk.entity_id)
        if inv:
            _inventory_signals(db, S, trace, inv, now)
    elif rtype in ("REVENUE_DECLINE", "MARGIN_DECLINE"):
        category = risk.entity_name if risk.entity_type == "category" else (product.category if product else None)
        products, category, line = _product_scope(db, product, line, category)
        _demand_signals(db, S, trace, product, category, line, None, now)
        _market_signals(db, S, trace, product, category, None, now)
        if line is not None:
            _line_production_signals(db, S, trace, line, now)
        fin = _call(S, trace, "get_financial_metrics", db, days=14)
        if fin:
            S.add("company_revenue_change_pct", fin["revenue_change_pct"], "financial_metric", fin["metric_ids_recent"], "%", fin["previous"]["revenue"], now, f"Company revenue {fin['revenue_change_pct']:+.1f}% (14d vs prior 14d)")
            S.add("company_margin_change_points", fin["margin_change_points"], "financial_metric", fin["metric_ids_recent"], "points", None, now, f"Company gross margin {fin['margin_change_points']:+.2f} pts")
    elif rtype in ("PRODUCTION_BOTTLENECK", "DELIVERY_DELAY", "DEMAND_SUPPLY_MISMATCH") and line is not None:
        _line_production_signals(db, S, trace, line, now)
        _line_context_signals(db, S, trace, line, None, now)
        _demand_signals(db, S, trace, None, None, line, None, now)
        capd = _call(S, trace, "get_capacity_status", db, production_line_id=line.id)
        if capd:
            S.add("line_utilization_14d", capd["utilization_14d"], "production_metric", capd["production_metric_ids"], "ratio", capd["baseline_utilization"], now, f"{line.code} utilisation {capd['utilization_14d']:.0%} over 14 days")
    return S


def _supplier_signals(db: Session, S: SignalSet, trace: TraceFn, supplier: Supplier, now: datetime) -> None:
    data = _call(S, trace, "get_supplier_history", db, supplier_id=supplier.id, days=120)
    if not data:
        return
    entry = data["suppliers"][0]
    sup, summ = entry["supplier"], entry["summary"]
    batch_ids = [b["id"] for b in entry["batches"]]
    S.add("supplier_lead_time_ratio", sup["lead_time_ratio"], "supplier", [sup["id"]], "x", 1.0, now, f"{sup['name']} lead time {sup['lead_time_days']} days vs baseline {sup['baseline_lead_time_days']} ({sup['lead_time_ratio']}x)")
    S.add("supplier_reliability", sup["reliability_score"], "supplier", [sup["id"]], "score", None, now, f"{sup['name']} reliability score {sup['reliability_score']:.2f}")
    S.add("supplier_delayed_batches", summ["delayed_batches"], "supplier_batch", summ["delayed_batch_ids"], "batches", None, now, f"{summ['delayed_batches']} open batch(es) delayed (max {summ['max_delay_days']} days)", max_delay_days=summ["max_delay_days"])
    S.add("supplier_max_delay_days", summ["max_delay_days"], "supplier_batch", summ["delayed_batch_ids"], "days", None, now, f"Longest current delay {summ['max_delay_days']} days")
    S.add("supplier_late_received_ratio", summ["late_received_ratio"], "supplier_batch", batch_ids, "ratio", None, now, f"{summ['late_received_ratio']:.0%} of received batches arrived late" if summ["late_received_ratio"] is not None else "No received batches in window")
    S.add("supplier_sole_source", data["sole_source"], "supplier", [sup["id"]], None, None, now, f"{sup['component_type']} has {data['qualified_sources']} qualified source(s)" , qualified_sources=data["qualified_sources"])
    S.add("supplier_min_quality", summ["min_quality_pass_rate"], "supplier_batch", batch_ids, "ratio", None, now, f"Lowest batch quality pass rate {summ['min_quality_pass_rate']:.2f}")
    inv = db.scalar(select(Inventory).where(Inventory.component_type == supplier.component_type))
    if inv:
        _inventory_signals(db, S, trace, inv, now)


def _inventory_signals(db: Session, S: SignalSet, trace: TraceFn, inv: Inventory, now: datetime) -> None:
    data = _call(S, trace, "get_inventory_status", db, inventory_id=inv.id, snapshot_days=28)
    if not data:
        return
    it = data["items"][0]
    snap_ids = [s["id"] for s in it["snapshots"]]
    S.add("item_days_of_cover", it["days_of_cover"], "inventory", [it["id"]], "days", None, now, f"{it['item_name']}: {it['quantity_available']} units = {it['days_of_cover']} days of cover (safety {it['safety_stock']}, reorder point {it['reorder_point']})", quantity=it["quantity_available"], safety_stock=it["safety_stock"], unit_value=it["unit_value"], avg_daily=it["avg_daily_consumption"])
    S.add("item_below_safety", it["below_safety_stock"], "inventory", [it["id"]], None, it["safety_stock"], now, f"Stock {'below' if it['below_safety_stock'] else 'above'} safety stock")
    S.add("item_stock_change_pct", it["stock_change_pct"], "inventory_snapshot", snap_ids, "%", None, now, f"Stock changed {it['stock_change_pct']:+.1f}% over 28 days" if it["stock_change_pct"] is not None else "no snapshots")
    if it["outbound_prior_avg"]:
        change = round((it["outbound_recent_avg"] - it["outbound_prior_avg"]) / it["outbound_prior_avg"] * 100, 1)
        S.add("consumption_change_pct", change, "inventory_snapshot", snap_ids[-7:], "%", it["outbound_prior_avg"], now, f"Daily consumption {it['outbound_recent_avg']:.0f} vs prior {it['outbound_prior_avg']:.0f} ({change:+.1f}%)")
    # reorder policy: does the reorder point cover the replenishment lead time?
    lead = None
    if it["component_type"]:
        sups = db.scalars(select(Supplier).where(Supplier.component_type == it["component_type"])).all()
        lead = max((s.baseline_lead_time_days for s in sups), default=None)
        sole = len(sups) == 1
        S.add("supplier_sole_source", sole, "supplier", [str(s.id) for s in sups], None, None, now, f"{it['component_type']} has {len(sups)} qualified source(s)", qualified_sources=len(sups))
        delayed = db.scalars(select(SupplierBatch).where(SupplierBatch.component_type == it["component_type"], SupplierBatch.received_date.is_(None), SupplierBatch.status == "DELAYED")).all()
        S.add("supplier_delayed_batches", len(delayed), "supplier_batch", [str(b.id) for b in delayed], "batches", None, now, f"{len(delayed)} delayed inbound batch(es) for {it['component_type']}", max_delay_days=max([b.delay_days for b in delayed] + [0]))
        S.add("supplier_max_delay_days", max([b.delay_days for b in delayed] + [0]), "supplier_batch", [str(b.id) for b in delayed], "days", None, now, f"Longest current delay {max([b.delay_days for b in delayed] + [0])} days")
    elif it["product_id"]:
        p = db.get(Product, uuid.UUID(it["product_id"]))
        if p:
            spec = next((s for s in cat.LINES if p.production_line and s.code == p.production_line.code), None)
            lead = spec.production_lead_days if spec else None
            _demand_signals(db, S, trace, p, None, p.production_line, None, now)
            if p.production_line:
                _line_production_signals(db, S, trace, p.production_line, now)
    if lead:
        reorder_cover = it["reorder_point"] / max(it["avg_daily_consumption"], 1e-9)
        S.add("reorder_policy_gap_days", round(lead - reorder_cover, 1), "inventory", [it["id"]], "days", 0.0, now, f"Reorder point covers {reorder_cover:.1f} days against a {lead}-day replenishment lead time (gap {lead - reorder_cover:+.1f} days)", lead_time_days=lead, reorder_cover_days=round(reorder_cover, 1))
