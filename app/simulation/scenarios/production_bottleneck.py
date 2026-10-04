"""Production bottleneck: demand on a line exceeds what it can produce.

Correlated effects
  extra orders for the line's products (demand > capacity)       -> orders
  utilization pinned at ~capacity, planned > output, downtime ↑   -> production_metrics
  defects ↑ (overrun), power ↑ on the line's machines             -> production_metrics, sensor_readings
  orders promised in the window slip (DELAYED / late deliveries)  -> orders
  finished goods ↓                                                 -> inventory
  line status BOTTLENECKED                                         -> production_lines
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import Order, OrderItem
from app.models.enums import LineStatus, OrderStatus
from app.simulation import catalog as cat
from app.simulation.scenarios.base import Scenario, ScenarioResult

DEFAULT_LINE = "LINE-C"


class ProductionBottleneckScenario(Scenario):
    name = "production_bottleneck"

    def apply(self) -> ScenarioResult:
        days = self.param_int("days", 10, 3, 30)
        demand_mult = self.param_float("demand_multiplier", 1.35, 1.05, 2.5)
        line = self.get_line(self.param_uuid("production_line_id"), DEFAULT_LINE)
        start, end = self.window(days)
        run_id = self.params.get("_run_id")
        result = ScenarioResult(self.name, start, end, {"production_line_id": str(line.id), "line_code": line.code, "demand_multiplier": demand_mult, "days": days})
        products = self.line_products(line)
        specs = {p.sku: p for p in cat.PRODUCTS}
        # 1. extra demand spread over the line's products
        created = []
        for p in products:
            spec = specs[p.sku]
            created += self.create_orders(p, None, start.date(), end.date(), lambda f, b=spec.base_daily_demand: b * (demand_mult - 1) * (0.5 + 0.5 * f), run_id=run_id)
        for o in created:
            result.touch("orders", o.id)
        # 2. line pinned at capacity; planned exceeds output; downtime and defects creep up
        metrics = self.line_metrics(line, start.date(), end.date())
        unfilled = 0
        for i, m in enumerate(metrics):
            if m.operating_hours <= 0:
                continue
            f = i / max(len(metrics) - 1, 1)
            m.downtime_hours = round(min(16.0, m.downtime_hours + 1.2 * f), 2)
            m.operating_hours = round(16.0 - m.downtime_hours, 2)
            m.planned_units = int(line.capacity_units_per_day * (1.0 + (demand_mult - 1) * (0.5 + 0.5 * f)))
            m.output_units = int(line.capacity_units_per_day * (0.99 - 0.03 * f))
            m.defect_rate = round(m.defect_rate * (1 + 0.6 * f), 4)
            m.defect_units = int(round(m.output_units * m.defect_rate))
            m.utilization = round(m.output_units / line.capacity_units_per_day, 4)
            m.output_rate = round(m.output_units / m.operating_hours, 2)
            m.power_consumption_kwh = round(m.power_consumption_kwh * 1.18, 1)
            unfilled += m.planned_units - m.output_units
            result.touch("production_metrics", m.id)
        for machine in line.machines:
            for s in machine.sensors:
                if s.sensor_type.value in ("POWER", "TEMPERATURE"):
                    self.ramp_readings(s, start, end, 0.12 if s.sensor_type.value == "POWER" else 0.06, exponent=1.0)
                    result.touch("sensors", s.id)
        line.status = LineStatus.BOTTLENECKED
        result.touch("production_lines", line.id)
        # 3. orders slip: anything for this line promised in the window and not shipped is late; some shipped ones delivered late
        pids = [p.id for p in products]
        open_orders = list(self.db.scalars(select(Order).join(OrderItem).where(OrderItem.product_id.in_(pids), Order.status.in_([OrderStatus.IN_PRODUCTION, OrderStatus.CONFIRMED]), Order.promised_delivery_date <= self.today + timedelta(days=4)).distinct()).all())
        for o in open_orders:
            o.status = OrderStatus.DELAYED
            result.touch("orders", o.id)
        delivered = list(self.db.scalars(select(Order).join(OrderItem).where(OrderItem.product_id.in_(pids), Order.status == OrderStatus.DELIVERED, Order.actual_delivery_date >= start.date()).distinct()).all())
        late = 0
        for o in delivered:
            if self.rng.random() < 0.5:
                o.actual_delivery_date = o.promised_delivery_date + timedelta(days=self.rng.randint(2, 6))
                late += 1
                result.touch("orders", o.id)
        # 4. finished goods drop as the backlog is served from stock
        for p in products:
            inv = self.product_inventory(p)
            if inv:
                self.drain_inventory(inv, inv.avg_daily_consumption * days * 0.5, start.date(), end.date())
                result.touch("inventory", inv.id)
        result.touch("financial_metrics", *self.recompute_financials(start.date(), end.date()))
        result.effects.update({"orders_created": len(created), "unfilled_planned_units": unfilled, "orders_delayed": len(open_orders), "late_deliveries_added": late, "line_status": line.status.value})
        result.summary = f"{line.name} is saturated: demand x{demand_mult} over {days} days, output pinned at capacity, {len(open_orders)} orders slipped."
        return result
