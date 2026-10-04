"""Demand spike: a product's demand surges in a region over the window.

Correlated effects
  extra orders ramping up (region-weighted customers)     -> orders, order_items, sales_transactions
  finished goods inventory ↓ (possibly below safety stock)  -> inventory + snapshots
  line utilization ↑ towards capacity                        -> production_metrics
  component consumption ↑                                    -> component inventory
  revenue ↑                                                  -> financial_metrics
  product demand growth signal                               -> market_signals (SYNTHETIC)
"""
from __future__ import annotations

from datetime import timedelta

from app.models import MarketSignal
from app.models.enums import SignalType
from app.simulation import catalog as cat
from app.simulation.scenarios.base import Scenario, ScenarioResult

DEFAULT_SKU = "AV-CX80"  # Industrial Valve X
DEFAULT_REGION = "South India"


class DemandSpikeScenario(Scenario):
    name = "demand_spike"

    def apply(self) -> ScenarioResult:
        days = self.param_int("days", 14, 3, 45)
        multiplier = self.param_float("multiplier", 3.0, 1.2, 6.0)
        region = self.params.get("region") or DEFAULT_REGION
        if region not in cat.REGIONS:
            from app.simulation.scenarios.base import ScenarioError

            raise ScenarioError(f"unknown region {region}")
        product = self.get_product(self.param_uuid("product_id"), DEFAULT_SKU)
        spec = {p.sku: p for p in cat.PRODUCTS}[product.sku]
        start, end = self.window(days)
        run_id = self.params.get("_run_id")
        result = ScenarioResult(self.name, start, end, {"product_id": str(product.id), "product": product.name, "region": region, "multiplier": multiplier, "days": days})
        # 1. extra orders: ramp from +20% to +(multiplier-1) of baseline demand
        base = spec.base_daily_demand
        orders = self.create_orders(product, region, start.date(), end.date(), lambda f: base * (multiplier - 1) * (0.4 + 0.6 * f), run_id=run_id)
        extra_units = sum(o.total_quantity for o in orders)
        for o in orders:
            result.touch("orders", o.id)
        result.touch("products", product.id)
        # 2. line runs harder (bounded by capacity), consuming components
        line = product.production_line
        extra_per_day = extra_units / max(days, 1)
        produced_extra = 0.0
        if line is not None:
            metrics = self.line_metrics(line, start.date(), end.date())
            for i, m in enumerate(metrics):
                if m.operating_hours <= 0:
                    continue
                f = i / max(len(metrics) - 1, 1)
                room = line.capacity_units_per_day - m.output_units
                add = int(min(room, extra_per_day * (0.3 + 0.7 * f) * 0.9))
                m.output_units += add
                m.planned_units = int(max(m.planned_units, m.output_units) + extra_per_day * (0.3 + 0.7 * f) * 0.1)
                m.utilization = round(m.output_units / line.capacity_units_per_day, 4)
                m.output_rate = round(m.output_units / m.operating_hours, 2)
                m.defect_units = int(round(m.output_units * m.defect_rate))
                m.power_consumption_kwh = round(m.power_consumption_kwh * (1 + 0.35 * add / max(line.capacity_units_per_day, 1)), 1)
                produced_extra += add
                result.touch("production_metrics", m.id)
            result.touch("production_lines", line.id)
            for ctype, per_unit in self.product_bom(product).items():
                cinv = self.component_inventory(ctype)
                if cinv:
                    self.drain_inventory(cinv, produced_extra * per_unit, start.date(), end.date())
                    result.touch("inventory", cinv.id)
        # 3. finished goods: shipments exceed the extra production
        inv = self.product_inventory(product)
        shipped_extra = sum(o.total_quantity for o in orders if o.status.value in ("SHIPPED", "DELIVERED", "IN_PRODUCTION"))
        if inv:
            net_drain = max(0.0, shipped_extra - produced_extra * 0.5)
            self.drain_inventory(inv, net_drain, start.date(), end.date())
            # the planner's demand estimate follows the new run-rate (used for days-of-cover and reorder logic)
            inv.avg_daily_consumption = round(inv.avg_daily_consumption + extra_units / max(days, 1) * 0.8, 2)
            inv.reorder_point = int(inv.avg_daily_consumption * 10)
            inv.safety_stock = int(inv.avg_daily_consumption * 5)
            result.touch("inventory", inv.id)
            result.effects["inventory_quantity_now"] = inv.quantity_available
            result.effects["inventory_safety_stock"] = inv.safety_stock
        # 4. market signal (synthetic)
        sig = MarketSignal(
            signal_date=self.today, signal_type=SignalType.PRODUCT_DEMAND_GROWTH, product_category=product.category, product_id=product.id, region=region,
            demand_index=round(100 * multiplier, 2), growth_rate_pct=round((multiplier - 1) * 100, 2),
            description=f"[SYNTHETIC] Sharp demand growth for {product.name} in {region} (simulated spike x{multiplier}).", simulation_run_id=run_id,
        )
        self.db.add(sig)
        self.db.flush()
        result.touch("market_signals", sig.id)
        result.touch("financial_metrics", *self.recompute_financials(start.date(), end.date()))
        result.effects.update({"orders_created": len(orders), "extra_units_ordered": extra_units, "extra_units_produced": round(produced_extra), "extra_revenue": round(sum(float(o.total_revenue) for o in orders), 2)})
        result.summary = f"Demand for {product.name} in {region} rose x{multiplier} over {days} days: {len(orders)} new orders, line utilization up, finished-goods stock down."
        _ = timedelta
        return result
