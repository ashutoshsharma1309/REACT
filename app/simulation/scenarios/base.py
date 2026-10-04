"""Scenario base class + shared, reusable effect helpers.

A scenario mutates the *existing* dataset over a time window ending at the simulated
"now" (SystemState.data_end), applying correlated effects across sensors, production,
inventory, orders, suppliers and financials. Every scenario returns the ids it touched.
"""
from __future__ import annotations

import random
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    FinancialMetric,
    Inventory,
    InventorySnapshot,
    Machine,
    Order,
    OrderItem,
    Product,
    ProductionLine,
    ProductionMetric,
    SalesTransaction,
    Sensor,
    SensorReading,
    SystemState,
)
from app.models.enums import OrderStatus, PaymentStatus
from app.simulation import catalog as cat
from app.simulation.clock import OPERATING_HOURS_PER_DAY, is_operating, is_working_day
from app.simulation.generator import DOWNTIME_OVERHEAD_PER_HOUR, POWER_COST_PER_KWH


@dataclass
class ScenarioResult:
    scenario: str
    window_start: datetime
    window_end: datetime
    parameters: dict[str, Any] = field(default_factory=dict)
    affected_entities: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    effects: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def touch(self, kind: str, *ids: Any) -> None:
        bucket = self.affected_entities.setdefault(kind, [])
        for i in ids:
            s = str(i)
            if s not in bucket:
                bucket.append(s)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "parameters": self.parameters,
            "affected_entities": dict(self.affected_entities),
            "effects": self.effects,
            "summary": self.summary,
        }


class ScenarioError(ValueError):
    """Raised for invalid scenario parameters (mapped to HTTP 400)."""


class Scenario(ABC):
    name: str = "base"

    def __init__(self, db: Session, params: dict[str, Any] | None = None) -> None:
        self.db = db
        self.params = params or {}
        self.state = db.get(SystemState, 1)
        if self.state is None:
            raise ScenarioError("Database is not seeded. Run the seed first (POST /simulation/reset).")
        self.now: datetime = self.state.data_end
        self.today: date = self.now.date()
        self.rng = random.Random(f"{self.state.seed}:{self.name}:{sorted(self.params.items())}")

    @abstractmethod
    def apply(self) -> ScenarioResult: ...

    # ---- parameter helpers -----------------------------------------------------------------
    def param_int(self, key: str, default: int, lo: int, hi: int) -> int:
        v = self.params.get(key)
        if v is None:
            return default
        v = int(v)
        if not lo <= v <= hi:
            raise ScenarioError(f"{key} must be between {lo} and {hi}")
        return v

    def param_float(self, key: str, default: float, lo: float, hi: float) -> float:
        v = self.params.get(key)
        if v is None:
            return default
        v = float(v)
        if not lo <= v <= hi:
            raise ScenarioError(f"{key} must be between {lo} and {hi}")
        return v

    def param_uuid(self, key: str) -> uuid.UUID | None:
        v = self.params.get(key)
        if v in (None, ""):
            return None
        try:
            return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
        except ValueError as e:
            raise ScenarioError(f"{key} must be a UUID") from e

    def window(self, days: int) -> tuple[datetime, datetime]:
        return self.now - timedelta(days=days), self.now

    # ---- lookups -----------------------------------------------------------------------------
    def get_machine(self, machine_id: uuid.UUID | None, default_code: str | None = None) -> Machine:
        if machine_id:
            m = self.db.get(Machine, machine_id)
            if not m:
                raise ScenarioError(f"machine {machine_id} not found")
            return m
        m = self.db.scalar(select(Machine).where(Machine.code == default_code)) if default_code else None
        if not m:
            raise ScenarioError("no target machine available")
        return m

    def get_product(self, product_id: uuid.UUID | None, default_sku: str) -> Product:
        p = self.db.get(Product, product_id) if product_id else self.db.scalar(select(Product).where(Product.sku == default_sku))
        if not p:
            raise ScenarioError("product not found")
        return p

    def get_line(self, line_id: uuid.UUID | None, default_code: str) -> ProductionLine:
        line = self.db.get(ProductionLine, line_id) if line_id else self.db.scalar(select(ProductionLine).where(ProductionLine.code == default_code))
        if not line:
            raise ScenarioError("production line not found")
        return line

    def line_products(self, line: ProductionLine) -> list[Product]:
        return list(self.db.scalars(select(Product).where(Product.production_line_id == line.id)).all())

    def product_bom(self, product: Product) -> dict[str, float]:
        return {**cat.BOM_BY_CATEGORY.get(product.category, {}), **cat.BOM_BY_SKU.get(product.sku, {})}

    # ---- sensor effects ----------------------------------------------------------------------
    def readings(self, sensor: Sensor, start: datetime, end: datetime) -> list[SensorReading]:
        return list(self.db.scalars(select(SensorReading).where(SensorReading.sensor_id == sensor.id, SensorReading.timestamp >= start, SensorReading.timestamp <= end).order_by(SensorReading.timestamp)).all())

    def ramp_readings(self, sensor: Sensor, start: datetime, end: datetime, gain: float, exponent: float = 1.5, operating_only: bool = True) -> int:
        """Multiply readings by (1 + gain * f(t)) where f ramps 0..1 across the window."""
        rows = self.readings(sensor, start, end)
        span = max((end - start).total_seconds(), 1)
        updates = []
        for r in rows:
            if operating_only and not is_operating(r.timestamp):
                continue
            f = ((r.timestamp - start).total_seconds() / span) ** exponent
            updates.append({"id": r.id, "value": round(max(0.0, r.value * (1 + gain * f)), 3)})
        if updates:
            self.db.execute(update(SensorReading), updates)
        return len(updates)

    # ---- production effects ------------------------------------------------------------------
    def line_metrics(self, line: ProductionLine, start: date, end: date) -> list[ProductionMetric]:
        return list(self.db.scalars(select(ProductionMetric).where(ProductionMetric.production_line_id == line.id, ProductionMetric.metric_date >= start, ProductionMetric.metric_date <= end).order_by(ProductionMetric.metric_date)).all())

    def scale_production(self, line: ProductionLine, start: date, end: date, output_mult, defect_mult, extra_downtime, planned_mult=None) -> dict[str, float]:
        """Apply per-day multipliers (callables of ramp fraction f in 0..1 or constants). Returns totals."""
        metrics = self.line_metrics(line, start, end)
        lost_units = 0.0
        extra_defects = 0.0
        added_downtime = 0.0
        n = max(len(metrics) - 1, 1)
        for i, m in enumerate(metrics):
            if m.operating_hours <= 0:
                continue
            f = i / n
            om = output_mult(f) if callable(output_mult) else output_mult
            dm = defect_mult(f) if callable(defect_mult) else defect_mult
            dt = extra_downtime(f) if callable(extra_downtime) else extra_downtime
            before_output, before_defects = m.output_units, m.defect_units
            m.downtime_hours = round(min(OPERATING_HOURS_PER_DAY, m.downtime_hours + dt), 2)
            m.operating_hours = round(OPERATING_HOURS_PER_DAY - m.downtime_hours, 2)
            m.output_units = int(round(m.output_units * om))
            m.defect_rate = round(min(0.6, m.defect_rate * dm), 4)
            m.defect_units = int(round(m.output_units * m.defect_rate))
            m.utilization = round(m.output_units / line.capacity_units_per_day, 4)
            m.output_rate = round(m.output_units / m.operating_hours, 2) if m.operating_hours else 0.0
            if planned_mult is not None:
                pm = planned_mult(f) if callable(planned_mult) else planned_mult
                m.planned_units = int(round(m.planned_units * pm))
            lost_units += before_output - m.output_units
            extra_defects += m.defect_units - before_defects
            added_downtime += dt
        return {"lost_units": lost_units, "extra_defects": extra_defects, "added_downtime_hours": added_downtime, "metric_ids": [str(m.id) for m in metrics]}

    # ---- inventory effects -------------------------------------------------------------------
    def product_inventory(self, product: Product) -> Inventory | None:
        return self.db.scalar(select(Inventory).where(Inventory.product_id == product.id))

    def component_inventory(self, component_type: str) -> Inventory | None:
        return self.db.scalar(select(Inventory).where(Inventory.component_type == component_type))

    def drain_inventory(self, inv: Inventory, total_units: float, start: date, end: date, floor: int = 0) -> int:
        """Remove units progressively across the window (snapshots + current quantity). Returns units removed."""
        snaps = list(self.db.scalars(select(InventorySnapshot).where(InventorySnapshot.inventory_id == inv.id, InventorySnapshot.snapshot_date >= start, InventorySnapshot.snapshot_date <= end).order_by(InventorySnapshot.snapshot_date)).all())
        if not snaps:
            removed = int(min(total_units, max(0, inv.quantity_available - floor)))
            inv.quantity_available -= removed
            return removed
        per_day = total_units / len(snaps)
        cumulative = 0.0
        for s in snaps:
            cumulative += per_day
            s.outbound_units += int(per_day)
            s.quantity_available = max(floor, int(s.quantity_available - cumulative))
        removed = int(min(total_units, max(0, inv.quantity_available - floor)))
        inv.quantity_available = max(floor, inv.quantity_available - removed)
        return removed

    def add_inventory(self, inv: Inventory, total_units: float, start: date, end: date) -> None:
        snaps = list(self.db.scalars(select(InventorySnapshot).where(InventorySnapshot.inventory_id == inv.id, InventorySnapshot.snapshot_date >= start, InventorySnapshot.snapshot_date <= end).order_by(InventorySnapshot.snapshot_date)).all())
        if snaps:
            per_day = total_units / len(snaps)
            cumulative = 0.0
            for s in snaps:
                cumulative += per_day
                s.outbound_units = max(0, s.outbound_units - int(per_day))
                s.quantity_available = int(s.quantity_available + cumulative)
        inv.quantity_available += int(total_units)

    # ---- orders ------------------------------------------------------------------------------
    def create_orders(self, product: Product, region: str | None, start: date, end: date, units_per_day, run_id: uuid.UUID | None = None) -> list[Order]:
        """Create extra orders for a product (ramping with units_per_day(f)). Deterministic given the scenario RNG."""
        spec = {p.sku: p for p in cat.PRODUCTS}[product.sku]
        q = select(Customer)
        if region:
            q = q.where(Customer.region == region)
        customers = list(self.db.scalars(q).all())
        if not customers:
            raise ScenarioError(f"no customers in region {region}")
        weights = [cat.INDUSTRIES[c.industry][product.category] * cat.SEGMENTS[c.segment.value]["orders_mult"] for c in customers]
        line_spec = {ls.code: ls for ls in cat.LINES}[spec.line]
        existing = self.db.scalar(select(Order.order_number).order_by(Order.order_number.desc()))
        seq = int(existing.split("-")[-1]) if existing else 0
        created: list[Order] = []
        d = start
        n_days = max((end - start).days, 1)
        price = float(product.selling_price)
        cost = float(product.unit_cost)
        while d <= end:
            f = (d - start).days / n_days
            units_today = units_per_day(f) if callable(units_per_day) else units_per_day
            if d.weekday() == 6:
                units_today *= 0.2
            remaining = units_today
            while remaining >= max(10, spec.typical_order_qty * 0.25):
                cust = self.rng.choices(customers, weights)[0]
                seg = cat.SEGMENTS[cust.segment.value]
                qty = int(min(remaining, max(10, spec.typical_order_qty * seg["qty_mult"] * self.rng.uniform(0.6, 1.3))))
                remaining -= qty
                seq += 1
                unit_price = round(price * (1 - seg["discount"]), 2)
                lead = line_spec.production_lead_days
                promised = d + timedelta(days=line_spec.promised_lead_days + max(0, {"South India": 2, "West India": 3, "North India": 4, "East India": 4, "Middle East": 6, "Southeast Asia": 7}[cust.region] - 2))
                age = (self.today - d).days
                if age >= lead + 3:
                    status = OrderStatus.SHIPPED
                elif age >= 2:
                    status = OrderStatus.IN_PRODUCTION
                elif age >= 1:
                    status = OrderStatus.CONFIRMED
                else:
                    status = OrderStatus.PENDING
                order = Order(
                    order_number=f"SO-{d.strftime('%y%m')}-{seq:05d}", customer=cust, order_date=d, region=cust.region, promised_delivery_date=promised, status=status,
                    total_quantity=qty, total_revenue=round(qty * unit_price, 2), total_cost=round(qty * cost, 2), simulation_run_id=run_id,
                )
                order.items.append(OrderItem(product=product, quantity=qty, unit_price=unit_price, unit_cost=cost, line_revenue=round(qty * unit_price, 2)))
                self.db.add(order)
                created.append(order)
                if status == OrderStatus.SHIPPED:
                    self.db.flush()
                    self.db.add(SalesTransaction(order_id=order.id, customer_id=cust.id, invoice_number=f"INV-SIM-{seq:05d}", transaction_date=d + timedelta(days=lead), amount=order.total_revenue, payment_status=PaymentStatus.PENDING, payment_terms_days=30))
            d += timedelta(days=1)
        self.db.flush()
        return created

    # ---- financials --------------------------------------------------------------------------
    def recompute_financials(self, start: date, end: date) -> list[str]:
        """Rebuild daily financial metrics for a date range from orders + production metrics."""
        fms = {fm.metric_date: fm for fm in self.db.scalars(select(FinancialMetric).where(FinancialMetric.metric_date >= start, FinancialMetric.metric_date <= end)).all()}
        orders = self.db.scalars(select(Order).where(Order.order_date >= start, Order.order_date <= end, Order.status != OrderStatus.CANCELLED)).all()
        by_day: dict[date, list[Order]] = defaultdict(list)
        for o in orders:
            by_day[o.order_date].append(o)
        lines = {line.id: line for line in self.db.scalars(select(ProductionLine)).all()}
        products = self.db.scalars(select(Product)).all()
        avg_cost_by_line = defaultdict(list)
        for p in products:
            avg_cost_by_line[p.production_line_id].append(float(p.unit_cost))
        metrics = self.db.scalars(select(ProductionMetric).where(ProductionMetric.metric_date >= start, ProductionMetric.metric_date <= end)).all()
        pm_by_day: dict[date, list[ProductionMetric]] = defaultdict(list)
        for m in metrics:
            pm_by_day[m.metric_date].append(m)
        touched = []
        for d, fm in fms.items():
            todays = by_day.get(d, [])
            revenue = sum(float(o.total_revenue) for o in todays)
            cogs = sum(float(o.total_cost) for o in todays)
            production_cost = 0.0
            for m in pm_by_day.get(d, []):
                costs = avg_cost_by_line.get(m.production_line_id) or [1000.0]
                production_cost += m.output_units * (sum(costs) / len(costs)) * 0.55 + m.power_consumption_kwh * POWER_COST_PER_KWH + m.downtime_hours * DOWNTIME_OVERHEAD_PER_HOUR
            fm.revenue = round(revenue, 2)
            fm.cost = round(cogs, 2)
            fm.gross_margin = round((revenue - cogs) / revenue, 4) if revenue else 0.0
            fm.production_cost = round(production_cost, 2)
            fm.profit = round(revenue - cogs - float(fm.operating_cost), 2)
            fm.orders_count = len(todays)
            fm.units_sold = sum(o.total_quantity for o in todays)
            touched.append(str(fm.id))
        _ = lines, is_working_day
        return touched
