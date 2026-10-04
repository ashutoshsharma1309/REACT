"""Tool layer: reusable, deterministic, validated data-access and calculation functions.

These are NOT agents. They are the functions future REACT agents (Phase 3) will call.
Every tool:
  * validates its inputs (ToolError with a stable code on bad input),
  * returns plain, JSON-serialisable structured output,
  * logs a `tool.executed` event with timing and result size,
  * never exposes connection details or raw exceptions.

Usage:
    run_tool("get_machine_readings", db, machine_id=..., hours=24)
    TOOLS["get_production_metrics"](db, production_line_id=..., days=7)
"""
from __future__ import annotations

import functools
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.detection.base import mean, pct_change, production_windows
from app.detection.config import DetectionConfig
from app.detection.operational import OperationalAnomalyDetector
from app.impact import ImpactEngine
from app.models import (
    BusinessRisk,
    CompetitorSignal,
    Customer,
    FinancialMetric,
    HistoricalIncident,
    Incident,
    Inventory,
    InventorySnapshot,
    Machine,
    MaintenanceRecord,
    MarketSignal,
    Order,
    OrderItem,
    Product,
    ProductionLine,
    SensorReading,
    Supplier,
    SupplierBatch,
    SystemState,
)
from app.models.enums import OrderStatus
from app.observability import get_logger, log_event
from app.simulation import catalog as cat

log = get_logger("react.tools")

# Domain-aware registry. TOOLS / TOOL_SPECS keep the manufacturing reference pack (unchanged public names);
# each domain pack registers its own tools, and agents only ever see the tools of the active domain.
TOOLS: dict[str, Callable[..., Any]] = {}
TOOL_SPECS: dict[str, dict[str, Any]] = {}
TOOLS_BY_DOMAIN: dict[str, dict[str, Callable[..., Any]]] = {"manufacturing": TOOLS}
SPECS_BY_DOMAIN: dict[str, dict[str, dict[str, Any]]] = {"manufacturing": TOOL_SPECS}


class ToolError(ValueError):
    """Raised for invalid tool input or a tool failure. Message is safe to return to callers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def tool(name: str, description: str, params: dict[str, str], domain: str = "manufacturing"):
    """Register a tool for a domain pack. The wrapped function receives (db, **kwargs) and returns a dict."""

    def deco(fn: Callable[..., Any]):
        @functools.wraps(fn)
        def wrapper(db: Session | None = None, **kwargs: Any) -> dict[str, Any]:
            started = time.perf_counter()
            try:
                data = fn(db, **kwargs)
                duration = round((time.perf_counter() - started) * 1000, 1)
                size = data.get("count") if isinstance(data, dict) and "count" in data else (len(data) if hasattr(data, "__len__") else None)
                log_event(log, "tool.executed", tool=name, ok=True, duration_ms=duration, result_size=size, params=_safe_params(kwargs))
                return data
            except ToolError as exc:
                log_event(log, "tool.failed", level=30, tool=name, code=exc.code, error=exc.message, params=_safe_params(kwargs))
                raise
            except Exception as exc:  # noqa: BLE001
                # never leak driver / connection details
                log_event(log, "tool.failed", level=40, tool=name, code="TOOL_ERROR", error=type(exc).__name__, params=_safe_params(kwargs))
                raise ToolError("TOOL_ERROR", f"{name} failed: {type(exc).__name__}") from None

        TOOLS_BY_DOMAIN.setdefault(domain, {})[name] = wrapper
        SPECS_BY_DOMAIN.setdefault(domain, {})[name] = {"name": name, "description": description, "params": params, "domain": domain}
        return wrapper

    return deco


def _ensure_domains_loaded() -> None:
    import app.domains.merchant.tools  # noqa: F401  (registers the merchant tools)


def tools_for(domain: str) -> dict[str, Callable[..., Any]]:
    _ensure_domains_loaded()
    return TOOLS_BY_DOMAIN.get(domain, {})


def specs_for(domain: str) -> dict[str, dict[str, Any]]:
    _ensure_domains_loaded()
    return SPECS_BY_DOMAIN.get(domain, {})


def resolve_tool(name: str, db: Session | None, domain: str | None = None) -> Callable[..., Any]:
    """Find a tool in the active domain (the dataset's domain when a session is given).
    Pure calculation tools called without a session are looked up across domains."""
    from app.domains import current_domain

    _ensure_domains_loaded()
    dom = domain or (current_domain(db) if db is not None else None)
    if dom is not None:
        fn = TOOLS_BY_DOMAIN.get(dom, {}).get(name)
        if fn is not None:
            return fn
        if db is not None:
            raise ToolError("UNKNOWN_TOOL", f"unknown tool '{name}'")
    for registry in TOOLS_BY_DOMAIN.values():
        if name in registry:
            return registry[name]
    raise ToolError("UNKNOWN_TOOL", f"unknown tool '{name}'")


def run_tool(name: str, db: Session | None, **kwargs: Any) -> dict[str, Any]:
    return resolve_tool(name, db)(db, **kwargs)


def describe_tools(domain: str | None = None) -> list[dict[str, Any]]:
    specs = specs_for(domain) if domain else TOOL_SPECS
    return [specs[k] for k in sorted(specs)]


# ---- validation helpers ----------------------------------------------------------------------
def _safe_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, (uuid.UUID, datetime, date)) else v) for k, v in kwargs.items()}


def _uuid(value: Any, name: str, required: bool = True) -> uuid.UUID | None:
    if value in (None, ""):
        if required:
            raise ToolError("MISSING_PARAM", f"{name} is required")
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ToolError("INVALID_PARAM", f"{name} must be a UUID") from None


def _int(value: Any, name: str, default: int, lo: int, hi: int) -> int:
    if value is None:
        return default
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ToolError("INVALID_PARAM", f"{name} must be an integer") from None
    if not lo <= v <= hi:
        raise ToolError("INVALID_PARAM", f"{name} must be between {lo} and {hi}")
    return v


def _float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ToolError("INVALID_PARAM", f"{name} must be a number") from None


def _now(db: Session) -> datetime:
    state = db.get(SystemState, 1)
    if state is None:
        raise ToolError("NOT_SEEDED", "database is not seeded")
    return state.data_end


def _require_db(db: Session | None) -> Session:
    if db is None:
        raise ToolError("MISSING_DB", "a database session is required")
    return db


def _d(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


# ---- data tools --------------------------------------------------------------------------------
@tool("get_machine_readings", "Recent vs baseline sensor statistics for a machine (per sensor), with reading ids.", {"machine_id": "UUID", "hours": "recent operating hours (1-168, default 24)", "sensor_type": "optional TEMPERATURE|VIBRATION|POWER|PRESSURE|RPM", "baseline_days": "baseline window (7-90, default 30)"})
def get_machine_readings(db: Session, machine_id: Any, hours: int | None = None, sensor_type: str | None = None, baseline_days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    mid = _uuid(machine_id, "machine_id")
    hours = _int(hours, "hours", 24, 1, 168)
    baseline_days = _int(baseline_days, "baseline_days", 30, 7, 90)
    machine = db.scalar(select(Machine).options(selectinload(Machine.sensors), selectinload(Machine.production_line)).where(Machine.id == mid))
    if machine is None:
        raise ToolError("NOT_FOUND", f"machine {mid} not found")
    now = _now(db)
    cfg = DetectionConfig(recent_hours=hours, baseline_days=baseline_days)
    detector = OperationalAnomalyDetector(db, cfg)
    start = now - timedelta(days=baseline_days + cfg.baseline_gap_days + 1)
    sensors = [s for s in machine.sensors if not sensor_type or s.sensor_type.value == str(sensor_type).upper()]
    out = []
    total = 0
    for s in sorted(sensors, key=lambda s: s.code):
        rows = db.execute(select(SensorReading.id, SensorReading.timestamp, SensorReading.value).where(SensorReading.sensor_id == s.id, SensorReading.timestamp >= start, SensorReading.timestamp <= now).order_by(SensorReading.timestamp)).all()
        total += len(rows)
        st = detector._analyse_sensor(s, [(r[0], r[1], r[2]) for r in rows], now)
        summary = st.summary()
        summary.update({"reading_ids_recent": st.recent_ids[:48], "peak_reading_id": st.peak_reading_id, "readings_loaded": len(rows), "std_ratio": round(st.recent_std / st.baseline_std, 3) if st.baseline_std else None, "last_timestamp": _d(rows[-1][1]) if rows else None})
        out.append(summary)
    return {"machine": {"id": str(machine.id), "code": machine.code, "name": machine.name, "status": machine.status.value, "criticality": machine.criticality.value, "production_line_id": str(machine.production_line_id), "health_score": machine.health_score}, "window_end": _d(now), "sensors": out, "count": total}


@tool("get_production_metrics", "Recent vs baseline production metrics for a production line.", {"production_line_id": "UUID", "days": "recent working days (1-60, default 7)", "baseline_days": "baseline days (7-120, default 30)"})
def get_production_metrics(db: Session, production_line_id: Any, days: int | None = None, baseline_days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    lid = _uuid(production_line_id, "production_line_id")
    days = _int(days, "days", 7, 1, 60)
    baseline_days = _int(baseline_days, "baseline_days", 30, 7, 120)
    line = db.get(ProductionLine, lid)
    if line is None:
        raise ToolError("NOT_FOUND", f"production line {lid} not found")
    now = _now(db)
    recent, baseline = production_windows(db, lid, now.date(), days, baseline_days)
    r_out, b_out = mean(m.output_units for m in recent), mean(m.output_units for m in baseline)
    r_def, b_def = mean(m.defect_rate for m in recent), mean(m.defect_rate for m in baseline)
    r_plan = mean(m.planned_units for m in recent)
    r_power, b_power = mean(m.power_consumption_kwh for m in recent), mean(m.power_consumption_kwh for m in baseline)
    return {
        "line": {"id": str(line.id), "code": line.code, "name": line.name, "capacity_units_per_day": line.capacity_units_per_day, "status": line.status.value},
        "recent": [{"id": str(m.id), "date": _d(m.metric_date), "planned": m.planned_units, "output": m.output_units, "defect_rate": m.defect_rate, "defect_units": m.defect_units, "downtime_hours": m.downtime_hours, "utilization": m.utilization, "power_kwh": m.power_consumption_kwh} for m in recent],
        "summary": {
            "recent_days": len(recent), "baseline_days": len(baseline), "recent_avg_output": round(r_out, 1), "baseline_avg_output": round(b_out, 1), "output_change_pct": round(pct_change(r_out, b_out) * 100, 1),
            "recent_avg_planned": round(r_plan, 1), "output_vs_plan": round(r_out / r_plan, 3) if r_plan else None, "recent_defect_rate": round(r_def, 4), "baseline_defect_rate": round(b_def, 4), "defect_ratio": round(r_def / b_def, 2) if b_def else None,
            "recent_downtime_hours": round(sum(m.downtime_hours for m in recent), 1), "baseline_downtime_hours_per_day": round(mean(m.downtime_hours for m in baseline), 2), "recent_utilization": round(mean(m.utilization for m in recent), 3), "baseline_utilization": round(mean(m.utilization for m in baseline), 3),
            "power_change_pct": round(pct_change(r_power, b_power) * 100, 1), "recent_power_std_ratio": round((_std(m.power_consumption_kwh for m in recent) / _std(m.power_consumption_kwh for m in baseline)), 2) if _std(m.power_consumption_kwh for m in baseline) else None,
        },
        "baseline_metric_ids": [str(m.id) for m in baseline[:40]],
        "count": len(recent) + len(baseline),
    }


def _std(xs) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


@tool("get_maintenance_history", "Maintenance records and component condition for a machine.", {"machine_id": "UUID", "days": "look-back days (7-365, default 180)", "preventive_interval_days": "expected preventive interval (default 30)"})
def get_maintenance_history(db: Session, machine_id: Any, days: int | None = None, preventive_interval_days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    mid = _uuid(machine_id, "machine_id")
    days = _int(days, "days", 180, 7, 365)
    interval = _int(preventive_interval_days, "preventive_interval_days", 30, 7, 180)
    machine = db.scalar(select(Machine).options(selectinload(Machine.components)).where(Machine.id == mid))
    if machine is None:
        raise ToolError("NOT_FOUND", f"machine {mid} not found")
    now = _now(db)
    records = db.scalars(select(MaintenanceRecord).where(MaintenanceRecord.machine_id == mid, MaintenanceRecord.maintenance_date >= now.date() - timedelta(days=days)).order_by(MaintenanceRecord.maintenance_date.desc())).all()
    last_prev = next((r for r in records if r.maintenance_type.value == "PREVENTIVE"), None)
    last_corr = next((r for r in records if r.maintenance_type.value == "CORRECTIVE"), None)
    last_any = records[0] if records else None
    days_since_prev = (now.date() - last_prev.maintenance_date).days if last_prev else None
    worst = min(machine.components, key=lambda c: c.condition_score) if machine.components else None
    return {
        "machine_id": str(mid),
        "records": [{"id": str(r.id), "date": _d(r.maintenance_date), "type": r.maintenance_type.value, "component_id": str(r.component_id) if r.component_id else None, "description": r.description, "downtime_hours": r.downtime_hours, "technician": r.technician, "cost": float(r.cost), "outcome": r.outcome} for r in records],
        "summary": {
            "days_since_preventive": days_since_prev, "preventive_overdue_days": max(0, days_since_prev - interval) if days_since_prev is not None else None, "expected_preventive_interval_days": interval,
            "days_since_corrective": (now.date() - last_corr.maintenance_date).days if last_corr else None, "days_since_any_maintenance": (now.date() - last_any.maintenance_date).days if last_any else None,
            "corrective_count_90d": sum(1 for r in records if r.maintenance_type.value == "CORRECTIVE" and r.maintenance_date >= now.date() - timedelta(days=90)),
            "last_preventive_id": str(last_prev.id) if last_prev else None, "last_corrective_id": str(last_corr.id) if last_corr else None,
        },
        "components": [{"id": str(c.id), "name": c.name, "type": c.component_type, "condition": c.condition.value, "condition_score": c.condition_score, "failure_probability": c.failure_probability, "operating_hours": c.operating_hours, "expected_life_hours": c.expected_life_hours, "life_consumed_pct": round(100 * c.operating_hours / c.expected_life_hours, 1) if c.expected_life_hours else None, "installed_on": _d(c.installed_on)} for c in machine.components],
        "worst_component": {"id": str(worst.id), "name": worst.name, "type": worst.component_type, "condition_score": worst.condition_score, "failure_probability": worst.failure_probability} if worst else None,
        "count": len(records),
    }


@tool("get_previous_incidents", "Historical (seeded) incidents and previously resolved incidents for a machine / line / type.", {"machine_id": "optional UUID", "production_line_id": "optional UUID", "incident_type": "optional type", "limit": "1-50 (default 10)"})
def get_previous_incidents(db: Session, machine_id: Any = None, production_line_id: Any = None, incident_type: str | None = None, limit: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    mid = _uuid(machine_id, "machine_id", required=False)
    lid = _uuid(production_line_id, "production_line_id", required=False)
    limit = _int(limit, "limit", 10, 1, 50)
    hq = select(HistoricalIncident).order_by(HistoricalIncident.occurred_at.desc())
    iq = select(Incident).where(Incident.status == "RESOLVED").order_by(Incident.detected_at.desc())
    conds_h, conds_i = [], []
    if mid:
        conds_h.append(HistoricalIncident.machine_id == mid)
        conds_i.append(Incident.machine_id == mid)
    if lid:
        conds_h.append(HistoricalIncident.production_line_id == lid)
        conds_i.append(Incident.production_line_id == lid)
    if conds_h:
        hq = hq.where(or_(*conds_h))
        iq = iq.where(or_(*conds_i))
    if incident_type:
        hq = hq.where(HistoricalIncident.incident_type == str(incident_type).upper())
        iq = iq.where(Incident.incident_type == str(incident_type).upper())
    hist = db.scalars(hq.limit(limit)).all()
    prev = db.scalars(iq.limit(limit)).all()
    return {
        "historical_incidents": [{"id": str(h.id), "type": h.incident_type.value, "severity": h.severity.value, "machine_id": str(h.machine_id) if h.machine_id else None, "production_line_id": str(h.production_line_id) if h.production_line_id else None, "component_id": str(h.component_id) if h.component_id else None, "occurred_at": _d(h.occurred_at), "root_cause": h.root_cause, "resolution": h.resolution, "downtime_hours": h.downtime_hours, "estimated_loss": float(h.estimated_loss), "leading_indicators": h.leading_indicators or {}} for h in hist],
        "resolved_incidents": [{"id": str(i.id), "type": i.incident_type.value, "severity": i.severity.value, "detected_at": _d(i.detected_at), "resolved_at": _d(i.resolved_at), "title": i.title} for i in prev],
        "count": len(hist) + len(prev),
    }


@tool("get_supplier_history", "Suppliers and their batches for a supplier or component type.", {"supplier_id": "optional UUID", "component_type": "optional component type", "days": "look-back days (7-365, default 120)"})
def get_supplier_history(db: Session, supplier_id: Any = None, component_type: str | None = None, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    sid = _uuid(supplier_id, "supplier_id", required=False)
    days = _int(days, "days", 120, 7, 365)
    if not sid and not component_type:
        raise ToolError("MISSING_PARAM", "supplier_id or component_type is required")
    now = _now(db)
    q = select(Supplier)
    if sid:
        q = q.where(Supplier.id == sid)
    if component_type:
        q = q.where(Supplier.component_type == str(component_type).upper())
    suppliers = db.scalars(q).all()
    if not suppliers:
        raise ToolError("NOT_FOUND", "no supplier matches the given filters")
    out = []
    total = 0
    for s in suppliers:
        batches = db.scalars(select(SupplierBatch).where(SupplierBatch.supplier_id == s.id, SupplierBatch.ordered_date >= now.date() - timedelta(days=days)).order_by(SupplierBatch.ordered_date.desc())).all()
        total += len(batches)
        open_b = [b for b in batches if b.received_date is None]
        delayed = [b for b in batches if b.status.value == "DELAYED" or (b.received_date is None and b.expected_date < now.date())]
        late_received = [b for b in batches if b.received_date and b.delay_days > 0]
        out.append({
            "supplier": {"id": str(s.id), "name": s.name, "component_type": s.component_type, "location": s.location, "country": s.country, "reliability_score": s.reliability_score, "lead_time_days": s.lead_time_days, "baseline_lead_time_days": s.baseline_lead_time_days, "lead_time_ratio": round(s.lead_time_days / max(s.baseline_lead_time_days, 1), 2), "status": s.status.value, "is_primary": s.is_primary, "unit_cost": float(s.unit_cost)},
            "batches": [{"id": str(b.id), "batch": b.batch_number, "status": b.status.value, "quantity": b.quantity, "ordered": _d(b.ordered_date), "expected": _d(b.expected_date), "received": _d(b.received_date), "delay_days": b.delay_days, "quality_pass_rate": b.quality_pass_rate} for b in batches],
            "summary": {"batches": len(batches), "open_batches": len(open_b), "delayed_batches": len(delayed), "max_delay_days": max([b.delay_days for b in delayed] + [0]), "late_received_ratio": round(len(late_received) / len([b for b in batches if b.received_date]), 3) if any(b.received_date for b in batches) else None, "min_quality_pass_rate": min([b.quality_pass_rate for b in batches if b.quality_pass_rate is not None] + [1.0]), "delayed_batch_ids": [str(b.id) for b in delayed]},
        })
    component = component_type or suppliers[0].component_type
    sources = db.scalar(select(func.count()).select_from(Supplier).where(Supplier.component_type == component)) or 0
    return {"component_type": component, "qualified_sources": sources, "sole_source": sources == 1, "suppliers": out, "count": total}


@tool("get_inventory_status", "Inventory position, cover and recent snapshots for a product, component or inventory item.", {"product_id": "optional UUID", "component_type": "optional component type", "inventory_id": "optional UUID", "snapshot_days": "7-120 (default 28)"})
def get_inventory_status(db: Session, product_id: Any = None, component_type: str | None = None, inventory_id: Any = None, snapshot_days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    pid = _uuid(product_id, "product_id", required=False)
    iid = _uuid(inventory_id, "inventory_id", required=False)
    snapshot_days = _int(snapshot_days, "snapshot_days", 28, 7, 120)
    if not (pid or component_type or iid):
        raise ToolError("MISSING_PARAM", "product_id, component_type or inventory_id is required")
    now = _now(db)
    q = select(Inventory)
    if iid:
        q = q.where(Inventory.id == iid)
    if pid:
        q = q.where(Inventory.product_id == pid)
    if component_type:
        q = q.where(Inventory.component_type == str(component_type).upper())
    items = db.scalars(q).all()
    if not items:
        raise ToolError("NOT_FOUND", "no inventory item matches the given filters")
    out = []
    for inv in items:
        snaps = db.scalars(select(InventorySnapshot).where(InventorySnapshot.inventory_id == inv.id, InventorySnapshot.snapshot_date > now.date() - timedelta(days=snapshot_days)).order_by(InventorySnapshot.snapshot_date)).all()
        daily = max(inv.avg_daily_consumption, 1e-9)
        first_q = snaps[0].quantity_available if snaps else inv.quantity_available
        out.append({
            "id": str(inv.id), "item_type": inv.item_type.value, "item_name": inv.item_name, "product_id": str(inv.product_id) if inv.product_id else None, "component_type": inv.component_type, "warehouse": inv.warehouse,
            "quantity_available": inv.quantity_available, "quantity_reserved": inv.quantity_reserved, "reorder_point": inv.reorder_point, "safety_stock": inv.safety_stock, "max_stock": inv.max_stock, "unit_value": float(inv.unit_value),
            "avg_daily_consumption": inv.avg_daily_consumption, "days_of_cover": round(inv.quantity_available / daily, 1), "below_reorder_point": inv.quantity_available < inv.reorder_point, "below_safety_stock": inv.quantity_available < inv.safety_stock,
            "stock_change_pct": round(pct_change(inv.quantity_available, first_q) * 100, 1) if first_q else None, "outbound_recent_avg": round(mean(s.outbound_units for s in snaps[-7:]), 1) if snaps else None, "outbound_prior_avg": round(mean(s.outbound_units for s in snaps[:-7]), 1) if len(snaps) > 7 else None,
            "snapshots": [{"id": str(s.id), "date": _d(s.snapshot_date), "quantity": s.quantity_available, "in": s.inbound_units, "out": s.outbound_units} for s in snaps],
        })
    return {"items": out, "count": len(out)}


@tool("get_customer_orders", "Order demand aggregated by period, region, segment, industry, customer and week.", {"product_id": "optional UUID", "category": "optional category", "production_line_id": "optional UUID", "region": "optional region", "days": "comparison window length (7-120, default 28); two windows are compared"})
def get_customer_orders(db: Session, product_id: Any = None, category: str | None = None, production_line_id: Any = None, region: str | None = None, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    pid = _uuid(product_id, "product_id", required=False)
    lid = _uuid(production_line_id, "production_line_id", required=False)
    days = _int(days, "days", 28, 7, 120)
    now = _now(db)
    today = now.date()
    recent_start, prev_start = today - timedelta(days=days), today - timedelta(days=2 * days)
    q = (select(Order.id, Order.order_date, Order.region, Order.customer_id, Order.status, OrderItem.product_id, OrderItem.quantity, OrderItem.line_revenue, OrderItem.unit_price, OrderItem.unit_cost, Product.category, Product.selling_price)
         .join(OrderItem, OrderItem.order_id == Order.id).join(Product, Product.id == OrderItem.product_id)
         .where(Order.order_date > today - timedelta(days=max(2 * days, 84)), Order.order_date <= today, Order.status != OrderStatus.CANCELLED))
    if pid:
        q = q.where(Product.id == pid)
    if category:
        q = q.where(Product.category == category)
    if lid:
        q = q.where(Product.production_line_id == lid)
    if region:
        q = q.where(Order.region == region)
    rows = db.execute(q).all()
    customers = {c.id: c for c in db.scalars(select(Customer)).all()}
    agg: dict[str, dict[str, dict[str, float]]] = {"region": defaultdict(lambda: defaultdict(float)), "segment": defaultdict(lambda: defaultdict(float)), "industry": defaultdict(lambda: defaultdict(float)), "customer": defaultdict(lambda: defaultdict(float))}
    weekly: dict[date, float] = defaultdict(float)
    totals = {"recent": defaultdict(float), "previous": defaultdict(float)}
    order_ids = {"recent": set(), "previous": set()}
    customers_seen = {"recent": set(), "previous": set()}
    discount_sum, discount_n = 0.0, 0
    for oid, od, reg, cid, status, prod_id, qty, rev, price, cost, categ, list_price in rows:
        week = od - timedelta(days=od.weekday())
        weekly[week] += qty
        period = "recent" if od > recent_start else "previous" if od > prev_start else None
        if list_price:
            discount_sum += 1 - float(price) / float(list_price)
            discount_n += 1
        if period is None:
            continue
        totals[period]["units"] += qty
        totals[period]["revenue"] += float(rev)
        totals[period]["cost"] += float(cost) * qty
        order_ids[period].add(oid)
        customers_seen[period].add(cid)
        c = customers.get(cid)
        keys = {"region": reg, "segment": c.segment.value if c else "UNKNOWN", "industry": c.industry if c else "UNKNOWN", "customer": str(cid)}
        for dim, key in keys.items():
            agg[dim][key][period + "_units"] += qty
            agg[dim][key][period + "_revenue"] += float(rev)
    weeks = sorted(weekly)
    weekly_series = [{"week": _d(w), "units": weekly[w]} for w in weeks]
    # sustained growth: consecutive most-recent weeks above the mean of the 4 weeks before them
    sustained = 0
    for i in range(len(weeks) - 1, 3, -1):
        prior = mean(weekly[w] for w in weeks[i - 4 : i])
        if prior and weekly[weeks[i]] > prior * 1.1:
            sustained += 1
        else:
            break
    def _breakdown(dim: str) -> list[dict[str, Any]]:
        rows_ = []
        for key, v in agg[dim].items():
            g = pct_change(v["recent_units"], v["previous_units"]) if v["previous_units"] else None
            rows_.append({"key": key, "recent_units": v["recent_units"], "previous_units": v["previous_units"], "recent_revenue": round(v["recent_revenue"], 2), "growth_pct": round(g * 100, 1) if g is not None else None, "incremental_units": v["recent_units"] - v["previous_units"]})
        return sorted(rows_, key=lambda r: -r["recent_units"])
    r, p = totals["recent"], totals["previous"]
    incremental = r["units"] - p["units"]
    cust_rows = _breakdown("customer")
    top_inc = max(cust_rows, key=lambda x: x["incremental_units"], default=None)
    return {
        "window": {"recent_start": _d(recent_start), "previous_start": _d(prev_start), "end": _d(today), "days": days},
        "recent": {"units": r["units"], "revenue": round(r["revenue"], 2), "orders": len(order_ids["recent"]), "customers": len(customers_seen["recent"]), "realised_margin": round((r["revenue"] - r["cost"]) / r["revenue"], 4) if r["revenue"] else None},
        "previous": {"units": p["units"], "revenue": round(p["revenue"], 2), "orders": len(order_ids["previous"]), "customers": len(customers_seen["previous"])},
        "growth": {"units_pct": round(pct_change(r["units"], p["units"]) * 100, 1) if p["units"] else None, "revenue_pct": round(pct_change(r["revenue"], p["revenue"]) * 100, 1) if p["revenue"] else None, "orders_pct": round(pct_change(len(order_ids["recent"]), len(order_ids["previous"])) * 100, 1) if order_ids["previous"] else None, "customers_pct": round(pct_change(len(customers_seen["recent"]), len(customers_seen["previous"])) * 100, 1) if customers_seen["previous"] else None, "incremental_units": incremental},
        "by_region": _breakdown("region"), "by_segment": _breakdown("segment"), "by_industry": _breakdown("industry"),
        "top_customers": cust_rows[:10],
        "concentration": {"top_customer_share_of_incremental": round(top_inc["incremental_units"] / incremental, 3) if top_inc and incremental > 0 else None, "top_customer_id": top_inc["key"] if top_inc else None, "new_customers": len(customers_seen["recent"] - customers_seen["previous"]), "repeat_customers": len(customers_seen["recent"] & customers_seen["previous"])},
        "weekly_series": weekly_series, "sustained_growth_weeks": sustained, "avg_discount_pct": round(discount_sum / discount_n * 100, 2) if discount_n else 0.0,
        "order_ids_recent": [str(o) for o in sorted(order_ids["recent"], key=str)[:60]], "order_ids_previous": [str(o) for o in sorted(order_ids["previous"], key=str)[:60]],
        "count": len(rows),
    }


@tool("get_market_signals", "SYNTHETIC market signals for a category / product / region.", {"category": "optional", "product_id": "optional UUID", "region": "optional", "days": "7-365 (default 90)"})
def get_market_signals(db: Session, category: str | None = None, product_id: Any = None, region: str | None = None, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    pid = _uuid(product_id, "product_id", required=False)
    days = _int(days, "days", 90, 7, 365)
    now = _now(db)
    q = select(MarketSignal).where(MarketSignal.signal_date > now.date() - timedelta(days=days))
    conds = []
    if category:
        conds.append(MarketSignal.product_category == category)
    if pid:
        conds.append(MarketSignal.product_id == pid)
    if conds:
        q = q.where(or_(*conds))
    if region:
        q = q.where(or_(MarketSignal.region == region, MarketSignal.region.is_(None)))
    sigs = db.scalars(q.order_by(MarketSignal.signal_date)).all()
    by_type: dict[str, list[MarketSignal]] = defaultdict(list)
    for s in sigs:
        by_type[s.signal_type.value].append(s)
    summary = {}
    for t, lst in by_type.items():
        summary[t] = {"count": len(lst), "latest_growth_rate_pct": lst[-1].growth_rate_pct, "avg_growth_rate_pct": round(mean(s.growth_rate_pct for s in lst), 2), "index_change_pct": round(pct_change(lst[-1].demand_index, lst[0].demand_index) * 100, 1) if len(lst) > 1 and lst[0].demand_index else 0.0, "latest_seasonal_index": lst[-1].seasonal_index, "positive_share": round(sum(1 for s in lst if s.growth_rate_pct > 0) / len(lst), 2)}
    return {"signals": [{"id": str(s.id), "date": _d(s.signal_date), "type": s.signal_type.value, "category": s.product_category, "product_id": str(s.product_id) if s.product_id else None, "region": s.region, "demand_index": s.demand_index, "growth_rate_pct": s.growth_rate_pct, "seasonal_index": s.seasonal_index, "source": s.source, "is_synthetic": s.is_synthetic, "simulation_run_id": str(s.simulation_run_id) if s.simulation_run_id else None} for s in sigs], "summary": summary, "synthetic": True, "count": len(sigs)}


@tool("get_competitor_signals", "SYNTHETIC competitor signals for a category / region.", {"category": "optional", "region": "optional", "days": "7-365 (default 90)"})
def get_competitor_signals(db: Session, category: str | None = None, region: str | None = None, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    days = _int(days, "days", 90, 7, 365)
    now = _now(db)
    q = select(CompetitorSignal).where(CompetitorSignal.signal_date > now.date() - timedelta(days=days))
    if category:
        q = q.where(CompetitorSignal.product_category == category)
    if region:
        q = q.where(or_(CompetitorSignal.region == region, CompetitorSignal.region.is_(None)))
    sigs = db.scalars(q.order_by(CompetitorSignal.signal_date)).all()
    price = [s for s in sigs if s.signal_type.value == "PRICE_CHANGE" and s.price_change_pct]
    avail = [s for s in sigs if s.signal_type.value == "AVAILABILITY" and s.availability_score is not None]
    return {
        "signals": [{"id": str(s.id), "date": _d(s.signal_date), "competitor": s.competitor_name, "type": s.signal_type.value, "category": s.product_category, "region": s.region, "price_change_pct": s.price_change_pct, "availability_score": s.availability_score, "description": s.description, "is_synthetic": s.is_synthetic} for s in sigs],
        "summary": {"price_change_total_pct": round(sum(s.price_change_pct for s in price), 2), "price_change_count": len(price), "availability_constraints": len([s for s in avail if s.availability_score < 0.9]), "min_availability_score": min([s.availability_score for s in avail] + [1.0]), "launches": len([s for s in sigs if s.signal_type.value == "PRODUCT_LAUNCH"]), "discounting_activity": len([s for s in sigs if s.signal_type.value == "MARKET_ACTIVITY" and "discount" in s.description.lower()])},
        "synthetic": True, "count": len(sigs),
    }


@tool("get_financial_metrics", "Company financial metrics for two consecutive windows.", {"days": "7-90 (default 14)"})
def get_financial_metrics(db: Session, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    days = _int(days, "days", 14, 7, 90)
    now = _now(db)
    rows = db.scalars(select(FinancialMetric).where(FinancialMetric.metric_date > now.date() - timedelta(days=2 * days), FinancialMetric.metric_date <= now.date()).order_by(FinancialMetric.metric_date)).all()
    split = now.date() - timedelta(days=days)
    recent, prev = [r for r in rows if r.metric_date > split], [r for r in rows if r.metric_date <= split]
    def tot(rs):
        rev = sum(float(r.revenue) for r in rs)
        return {"revenue": round(rev, 2), "cost": round(sum(float(r.cost) for r in rs), 2), "profit": round(sum(float(r.profit) for r in rs), 2), "gross_margin": round(mean(r.gross_margin for r in rs if float(r.revenue) > 0), 4) if rs else None, "orders": sum(r.orders_count for r in rs)}
    r, p = tot(recent), tot(prev)
    return {"recent": r, "previous": p, "revenue_change_pct": round(pct_change(r["revenue"], p["revenue"]) * 100, 1) if p["revenue"] else None, "margin_change_points": round((r["gross_margin"] or 0) * 100 - (p["gross_margin"] or 0) * 100, 2), "metric_ids_recent": [str(x.id) for x in recent], "count": len(rows)}


@tool("get_line_context", "Machines on a production line with their anomaly state (simultaneous anomalies), BOM components, stock and supplier status.", {"production_line_id": "UUID", "hours": "recent operating hours for sensor stats (default 24)"})
def get_line_context(db: Session, production_line_id: Any, hours: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    lid = _uuid(production_line_id, "production_line_id")
    line = db.scalar(select(ProductionLine).options(selectinload(ProductionLine.machines)).where(ProductionLine.id == lid))
    if line is None:
        raise ToolError("NOT_FOUND", f"production line {lid} not found")
    machines = []
    for m in sorted(line.machines, key=lambda m: m.code):
        r = get_machine_readings(db, machine_id=m.id, hours=hours)
        anomalous = [s for s in r["sensors"] if s["severity"] or s["fault"]]
        machines.append({"id": str(m.id), "code": m.code, "status": m.status.value, "anomalous_sensor_types": sorted({s["sensor_type"] for s in anomalous}), "power_change_pct": next((s["pct_change"] for s in r["sensors"] if s["sensor_type"] == "POWER"), None), "power_std_ratio": next((s["std_ratio"] for s in r["sensors"] if s["sensor_type"] == "POWER"), None), "faults": [s["fault"] for s in anomalous if s["fault"]]})
    products = db.scalars(select(Product).where(Product.production_line_id == lid)).all()
    comps: set[str] = set()
    for p in products:
        comps.update({**cat.BOM_BY_CATEGORY.get(p.category, {}), **cat.BOM_BY_SKU.get(p.sku, {})})
    inv = db.scalars(select(Inventory).where(Inventory.component_type.in_(sorted(comps)))).all() if comps else []
    now = _now(db)
    delayed = db.scalars(select(SupplierBatch).where(SupplierBatch.component_type.in_(sorted(comps)), SupplierBatch.received_date.is_(None), or_(SupplierBatch.status == "DELAYED", SupplierBatch.expected_date < now.date()))).all() if comps else []
    poor_quality = db.scalars(select(SupplierBatch).where(SupplierBatch.component_type.in_(sorted(comps)), SupplierBatch.received_date >= now.date() - timedelta(days=21), SupplierBatch.quality_pass_rate < 0.9)).all() if comps else []
    return {
        "line": {"id": str(line.id), "code": line.code, "name": line.name, "status": line.status.value, "capacity_units_per_day": line.capacity_units_per_day},
        "machines": machines,
        "machines_with_power_anomaly": sum(1 for m in machines if "POWER" in m["anomalous_sensor_types"]), "machines_with_any_anomaly": sum(1 for m in machines if m["anomalous_sensor_types"]),
        "components": sorted(comps), "product_ids": [str(p.id) for p in products],
        "component_stock": [{"id": str(i.id), "component_type": i.component_type, "quantity": i.quantity_available, "safety_stock": i.safety_stock, "days_of_cover": round(i.quantity_available / max(i.avg_daily_consumption, 1e-9), 1), "below_safety": i.quantity_available < i.safety_stock} for i in inv],
        "delayed_batches": [{"id": str(b.id), "batch": b.batch_number, "component_type": b.component_type, "delay_days": b.delay_days, "supplier_id": str(b.supplier_id)} for b in delayed],
        "poor_quality_batches": [{"id": str(b.id), "batch": b.batch_number, "component_type": b.component_type, "quality_pass_rate": b.quality_pass_rate, "received": _d(b.received_date)} for b in poor_quality],
        "count": len(machines),
    }


@tool("get_capacity_status", "Utilisation and spare capacity for a production line over the last 14 days.", {"production_line_id": "UUID"})
def get_capacity_status(db: Session, production_line_id: Any) -> dict[str, Any]:
    db = _require_db(db)
    pm = get_production_metrics(db, production_line_id=production_line_id, days=14, baseline_days=30)
    util = pm["summary"]["recent_utilization"]
    cap = pm["line"]["capacity_units_per_day"]
    return {"line": pm["line"], "utilization_14d": util, "baseline_utilization": pm["summary"]["baseline_utilization"], "spare_units_per_day": round(cap * max(0.0, 0.95 - util)), "spare_units_per_day_at_90pct": round(cap * max(0.0, 0.90 - util)), "recent_downtime_hours": pm["summary"]["recent_downtime_hours"], "production_metric_ids": [m["id"] for m in pm["recent"]], "count": len(pm["recent"])}


@tool("search_business_records", "Search products, customers, machines, suppliers and lines by name / code.", {"query": "text (min 2 chars)", "entity_types": "optional list of product|customer|machine|supplier|production_line", "limit": "1-100 (default 20)"})
def search_business_records(db: Session, query: str, entity_types: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    if not query or len(str(query).strip()) < 2:
        raise ToolError("INVALID_PARAM", "query must be at least 2 characters")
    limit = _int(limit, "limit", 20, 1, 100)
    pattern = f"%{str(query).strip()}%"
    types = [t.lower() for t in (entity_types or ["product", "customer", "machine", "supplier", "production_line"])]
    unknown = set(types) - {"product", "customer", "machine", "supplier", "production_line"}
    if unknown:
        raise ToolError("INVALID_PARAM", f"unknown entity types: {sorted(unknown)}")
    results: list[dict[str, Any]] = []
    if "product" in types:
        results += [{"entity_type": "product", "id": str(p.id), "label": p.name, "code": p.sku, "extra": {"category": p.category}} for p in db.scalars(select(Product).where(or_(Product.name.ilike(pattern), Product.sku.ilike(pattern), Product.category.ilike(pattern))).limit(limit)).all()]
    if "customer" in types:
        results += [{"entity_type": "customer", "id": str(c.id), "label": c.name, "code": None, "extra": {"region": c.region, "industry": c.industry, "segment": c.segment.value}} for c in db.scalars(select(Customer).where(or_(Customer.name.ilike(pattern), Customer.region.ilike(pattern), Customer.industry.ilike(pattern))).limit(limit)).all()]
    if "machine" in types:
        results += [{"entity_type": "machine", "id": str(m.id), "label": m.name, "code": m.code, "extra": {"type": m.machine_type, "status": m.status.value}} for m in db.scalars(select(Machine).where(or_(Machine.name.ilike(pattern), Machine.code.ilike(pattern), Machine.machine_type.ilike(pattern))).limit(limit)).all()]
    if "supplier" in types:
        results += [{"entity_type": "supplier", "id": str(s.id), "label": s.name, "code": s.component_type, "extra": {"status": s.status.value, "location": s.location}} for s in db.scalars(select(Supplier).where(or_(Supplier.name.ilike(pattern), Supplier.component_type.ilike(pattern))).limit(limit)).all()]
    if "production_line" in types:
        results += [{"entity_type": "production_line", "id": str(line.id), "label": line.name, "code": line.code, "extra": {"status": line.status.value}} for line in db.scalars(select(ProductionLine).where(or_(ProductionLine.name.ilike(pattern), ProductionLine.code.ilike(pattern))).limit(limit)).all()]
    return {"query": query, "results": results[:limit], "count": min(len(results), limit)}


# ---- calculation tools (wrap the impact engine; no DB required) -------------------------------
@tool("calculate_production_loss", "Estimated production loss = lost_units × unit_margin.", {"lost_units": "number", "unit_margin": "number"})
def calculate_production_loss(db: Session | None, lost_units: Any, unit_margin: Any) -> dict[str, Any]:
    return ImpactEngine().production_loss(_float(lost_units, "lost_units"), _float(unit_margin, "unit_margin")).as_dict()


@tool("calculate_revenue_impact", "Revenue impact = expected_revenue − actual_revenue.", {"expected_revenue": "number", "actual_revenue": "number"})
def calculate_revenue_impact(db: Session | None, expected_revenue: Any, actual_revenue: Any) -> dict[str, Any]:
    return ImpactEngine().revenue_impact(_float(expected_revenue, "expected_revenue"), _float(actual_revenue, "actual_revenue")).as_dict()


@tool("calculate_downtime_impact", "Downtime impact = downtime_hours × estimated_hourly_loss.", {"downtime_hours": "number", "estimated_hourly_loss": "optional number"})
def calculate_downtime_impact(db: Session | None, downtime_hours: Any, estimated_hourly_loss: Any = None) -> dict[str, Any]:
    return ImpactEngine().downtime_impact(_float(downtime_hours, "downtime_hours"), _float(estimated_hourly_loss, "estimated_hourly_loss") if estimated_hourly_loss is not None else None).as_dict()


@tool("calculate_opportunity_value", "Opportunity value = incremental_units × unit_margin over a horizon.", {"incremental_units": "number", "unit_margin": "number", "horizon_days": "int (default 30)"})
def calculate_opportunity_value(db: Session | None, incremental_units: Any, unit_margin: Any, horizon_days: int | None = None) -> dict[str, Any]:
    return ImpactEngine().opportunity_value(_float(incremental_units, "incremental_units"), _float(unit_margin, "unit_margin"), _int(horizon_days, "horizon_days", 30, 1, 365)).as_dict()


@tool("calculate_inventory_risk", "Inventory shortfall = required_quantity − available_quantity (× unit_value).", {"required_quantity": "number", "available_quantity": "number", "unit_value": "optional number"})
def calculate_inventory_risk(db: Session | None, required_quantity: Any, available_quantity: Any, unit_value: Any = 0.0) -> dict[str, Any]:
    return ImpactEngine().inventory_risk(_float(required_quantity, "required_quantity"), _float(available_quantity, "available_quantity"), _float(unit_value, "unit_value")).as_dict()


__all__ = ["TOOLS", "ToolError", "describe_tools", "run_tool"] + sorted(TOOLS)
