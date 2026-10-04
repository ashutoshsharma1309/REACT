"""Inventory shortage: a finished product (or component) runs out while demand continues.

Correlated effects
  stock ↓ to a fraction of safety stock (progressive)     -> inventory + snapshots
  open orders for the item become at risk / DELAYED       -> orders
  (component) dependent lines lose output                  -> production_metrics
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import Inventory, Order, OrderItem, Product
from app.models.enums import OrderStatus
from app.simulation.scenarios.base import Scenario, ScenarioError, ScenarioResult

DEFAULT_SKU = "AV-CX80"


class InventoryShortageScenario(Scenario):
    name = "inventory_shortage"

    def apply(self) -> ScenarioResult:
        days = self.param_int("days", 5, 1, 30)
        level = self.param_float("level", 0.25, 0.0, 0.9)  # remaining share of safety stock
        inventory_id = self.param_uuid("inventory_id")
        component_type = self.params.get("component_type")
        if inventory_id:
            inv = self.db.get(Inventory, inventory_id)
        elif component_type:
            inv = self.component_inventory(str(component_type))
        else:
            inv = self.product_inventory(self.get_product(self.param_uuid("product_id"), DEFAULT_SKU))
        if not inv:
            raise ScenarioError("inventory item not found")
        start, end = self.window(days)
        result = ScenarioResult(self.name, start, end, {"inventory_id": str(inv.id), "item": inv.item_name, "item_type": inv.item_type.value, "level": level, "days": days})
        floor = int(inv.safety_stock * level)
        removed = self.drain_inventory(inv, max(0, inv.quantity_available - floor), start.date(), end.date(), floor=floor)
        result.touch("inventory", inv.id)
        at_risk: list[Order] = []
        if inv.item_type.value == "PRODUCT":
            at_risk = list(self.db.scalars(select(Order).join(OrderItem).where(OrderItem.product_id == inv.product_id, Order.status.in_([OrderStatus.IN_PRODUCTION, OrderStatus.CONFIRMED, OrderStatus.PENDING])).distinct()).all())
            for o in at_risk:
                if o.promised_delivery_date <= self.today + timedelta(days=3):
                    o.status = OrderStatus.DELAYED
                result.touch("orders", o.id)
            result.touch("products", inv.product_id)
        else:
            dependent = [p for p in self.db.scalars(select(Product)).all() if inv.component_type in self.product_bom(p)]
            for line in {p.production_line for p in dependent if p.production_line is not None}:
                totals = self.scale_production(line, (self.now - timedelta(days=min(days, 3))).date(), self.today, output_mult=lambda f: 1 - 0.25 * (0.5 + 0.5 * f), defect_mult=1.0, extra_downtime=0.0)
                result.touch("production_metrics", *totals["metric_ids"])
                result.touch("production_lines", line.id)
            at_risk = list(self.db.scalars(select(Order).join(OrderItem).where(OrderItem.product_id.in_([p.id for p in dependent]), Order.status.in_([OrderStatus.IN_PRODUCTION, OrderStatus.CONFIRMED])).distinct()).all())
            for o in at_risk:
                result.touch("orders", o.id)
        self.recompute_financials(start.date(), end.date())
        result.effects.update({"units_removed": removed, "quantity_now": inv.quantity_available, "safety_stock": inv.safety_stock, "reorder_point": inv.reorder_point, "open_orders_at_risk": len(at_risk), "at_risk_revenue": round(sum(float(o.total_revenue) for o in at_risk), 2)})
        result.summary = f"{inv.item_name} stock fell to {inv.quantity_available} units ({level:.0%} of safety stock) with {len(at_risk)} open orders depending on it."
        return result
