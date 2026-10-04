"""Demand drop: demand for a product category collapses over the window.

Correlated effects
  orders in the window removed / trimmed for the category          -> orders, order_items, sales_transactions
  remaining category orders discounted (margin pressure)            -> order_items, orders
  finished goods inventory ↑ (production continued, shipments fell) -> inventory + snapshots
  line utilization ↓ (planned volume cut)                           -> production_metrics
  revenue ↓, margin ↓                                                -> financial_metrics
  negative demand signal                                             -> market_signals (SYNTHETIC)
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select

from app.models import MarketSignal, Order, OrderItem, Product, SalesTransaction
from app.models.enums import SignalType
from app.simulation import catalog as cat
from app.simulation.scenarios.base import Scenario, ScenarioError, ScenarioResult

DEFAULT_CATEGORY = "Pump Components"


class DemandDropScenario(Scenario):
    name = "demand_drop"

    def apply(self) -> ScenarioResult:
        days = self.param_int("days", 21, 5, 60)
        factor = self.param_float("factor", 0.4, 0.05, 0.9)  # remaining share of demand
        discount = self.param_float("discount", 0.08, 0.0, 0.3)
        product_id = self.param_uuid("product_id")
        if product_id:
            products = [self.get_product(product_id, "")]
            label = products[0].name
            category = products[0].category
        else:
            category = self.params.get("category") or DEFAULT_CATEGORY
            if category not in cat.CATEGORIES:
                raise ScenarioError(f"unknown category {category}")
            products = list(self.db.scalars(select(Product).where(Product.category == category)).all())
            label = category
        pids = [p.id for p in products]
        start, end = self.window(days)
        run_id = self.params.get("_run_id")
        result = ScenarioResult(self.name, start, end, {"target": label, "category": category, "factor": factor, "discount": discount, "days": days})
        # 1. remove a deterministic share of category demand in the window
        items = list(self.db.scalars(select(OrderItem).join(Order).where(OrderItem.product_id.in_(pids), Order.order_date >= start.date(), Order.order_date <= end.date())).all())
        removed_units = 0
        removed_revenue = 0.0
        returned_units: dict = {}
        orders_deleted = 0
        for it in items:
            order = it.order
            f = (order.order_date - start.date()).days / max(days, 1)
            drop_prob = (1 - factor) * (0.5 + 0.5 * f)  # decline deepens over the window
            if self.rng.random() < drop_prob:
                removed_units += it.quantity
                removed_revenue += float(it.line_revenue)
                returned_units[it.product_id] = returned_units.get(it.product_id, 0) + (it.quantity if order.status.value in ("SHIPPED", "DELIVERED") else 0)
                order.items.remove(it)
                self.db.delete(it)
                if not order.items:
                    self.db.execute(delete(SalesTransaction).where(SalesTransaction.order_id == order.id))
                    self.db.delete(order)
                    orders_deleted += 1
                else:
                    order.total_quantity = sum(i.quantity for i in order.items)
                    order.total_revenue = round(sum(float(i.line_revenue) for i in order.items), 2)
                    order.total_cost = round(sum(i.quantity * float(i.unit_cost) for i in order.items), 2)
                    result.touch("orders", order.id)
            else:
                # margin pressure: remaining category demand only at a discount
                it.unit_price = round(float(it.unit_price) * (1 - discount), 2)
                it.line_revenue = round(it.quantity * float(it.unit_price), 2)
                order.total_revenue = round(sum(float(i.line_revenue) for i in order.items), 2)
                result.touch("orders", order.id)
        self.db.flush()
        # 2. inventory builds up: units that were produced but not shipped
        for p in products:
            inv = self.product_inventory(p)
            if inv:
                self.add_inventory(inv, returned_units.get(p.id, 0) + removed_units * 0.15 / len(products), start.date(), end.date())
                result.touch("inventory", inv.id)
            result.touch("products", p.id)
        # 3. line volume cut as planners react (second half of the window)
        lines = {p.production_line for p in products if p.production_line is not None}
        for line in lines:
            line_share = sum(1 for p in products if p.production_line_id == line.id) / max(1, len(self.line_products(line)))
            totals = self.scale_production(line, (self.now - timedelta(days=days // 2)).date(), self.today, output_mult=lambda f, s=line_share: 1 - (1 - factor) * s * 0.6 * f, defect_mult=1.0, extra_downtime=0.0, planned_mult=lambda f, s=line_share: 1 - (1 - factor) * s * 0.6 * f)
            result.touch("production_metrics", *totals["metric_ids"])
            result.touch("production_lines", line.id)
        # 4. signal + financials
        sig = MarketSignal(signal_date=self.today, signal_type=SignalType.CATEGORY_GROWTH, product_category=category, demand_index=round(100 * factor, 2), growth_rate_pct=round(-(1 - factor) * 100, 2), description=f"[SYNTHETIC] Demand for {label} contracted sharply (simulated drop to {factor:.0%}).", simulation_run_id=run_id)
        self.db.add(sig)
        self.db.flush()
        result.touch("market_signals", sig.id)
        result.touch("financial_metrics", *self.recompute_financials(start.date(), end.date()))
        result.effects.update({"orders_deleted": orders_deleted, "units_removed": removed_units, "revenue_removed": round(removed_revenue, 2), "discount_applied": discount})
        result.summary = f"Demand for {label} fell to {factor:.0%} over {days} days: {removed_units} units of orders lost, inventory building, utilization and margin down."
        return result
