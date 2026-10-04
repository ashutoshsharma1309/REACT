"""Supplier delay: a supplier slips, component stock drains, dependent lines slow, orders slip.

Correlated effects
  supplier lead time ↑, reliability ↓, status AT_RISK                     -> suppliers
  open batches DELAYED and pushed out (or a delayed batch is created)      -> supplier_batches
  component inventory ↓ below safety stock (progressively)                 -> inventory + snapshots
  output ↓ on lines consuming the component                                -> production_metrics
  open orders for dependent products promised soon -> DELAYED              -> orders
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import Order, OrderItem, Product, Supplier, SupplierBatch
from app.models.enums import BatchStatus, OrderStatus, SupplierStatus
from app.simulation import catalog as cat
from app.simulation.scenarios.base import Scenario, ScenarioError, ScenarioResult

DEFAULT_SUPPLIER = "Helios Controls GmbH"  # sole source of ELECTRONIC_ACTUATOR (Industrial Valve X)


class SupplierDelayScenario(Scenario):
    name = "supplier_delay"

    def apply(self) -> ScenarioResult:
        delay_days = self.param_int("delay_days", 10, 2, 45)
        days = self.param_int("days", 7, 2, 30)
        supplier_id = self.param_uuid("supplier_id")
        supplier = self.db.get(Supplier, supplier_id) if supplier_id else self.db.scalar(select(Supplier).where(Supplier.name == DEFAULT_SUPPLIER))
        if not supplier:
            raise ScenarioError("supplier not found")
        start, end = self.window(days)
        result = ScenarioResult(self.name, start, end, {"supplier_id": str(supplier.id), "supplier_name": supplier.name, "component_type": supplier.component_type, "delay_days": delay_days, "days": days})
        # 1. supplier state
        supplier.lead_time_days = supplier.baseline_lead_time_days + delay_days
        supplier.reliability_score = round(max(0.2, supplier.reliability_score - 0.18), 3)
        supplier.status = SupplierStatus.AT_RISK
        result.touch("suppliers", supplier.id)
        # 2. batches: push every open batch out; create one if none is open
        open_batches = list(self.db.scalars(select(SupplierBatch).where(SupplierBatch.supplier_id == supplier.id, SupplierBatch.received_date.is_(None))).all())
        if not open_batches:
            ordered = self.today - timedelta(days=supplier.baseline_lead_time_days - 2)
            b = SupplierBatch(
                supplier=supplier, batch_number=f"PO-SIM-{supplier.component_type[:4]}-{self.today.strftime('%y%m%d')}", component_type=supplier.component_type,
                quantity=cat.SUPPLIERS[[s.name for s in cat.SUPPLIERS].index(supplier.name)].batch_size if supplier.name in [s.name for s in cat.SUPPLIERS] else 5000,
                ordered_date=ordered, expected_date=ordered + timedelta(days=supplier.baseline_lead_time_days), unit_cost=supplier.unit_cost,
            )
            self.db.add(b)
            open_batches.append(b)
        for b in open_batches:
            b.expected_date = min(b.expected_date, self.today - timedelta(days=1))  # it was due already
            b.delay_days = delay_days
            b.status = BatchStatus.DELAYED
            self.db.flush()
            result.touch("supplier_batches", b.id)
        # 3. component inventory drains below safety stock
        inv = self.component_inventory(supplier.component_type)
        if inv:
            floor = int(inv.safety_stock * 0.35)
            removed = self.drain_inventory(inv, max(0, inv.quantity_available - floor), start.date(), end.date(), floor=floor)
            result.touch("inventory", inv.id)
            result.effects["component_units_removed"] = removed
            result.effects["component_quantity_now"] = inv.quantity_available
            result.effects["component_safety_stock"] = inv.safety_stock
        # 4. dependent products / lines
        dependent = [p for p in self.db.scalars(select(Product)).all() if supplier.component_type in self.product_bom(p)]
        lines = {p.production_line for p in dependent if p.production_line is not None}
        weight = {line.id: max((self.product_bom(p).get(supplier.component_type, 0) for p in dependent if p.production_line_id == line.id), default=0) for line in lines}
        lost_total = 0.0
        for line in lines:
            share = min(1.0, weight[line.id])  # lines fully dependent on the component slow more
            totals = self.scale_production(line, (self.now - timedelta(days=min(days, 4))).date(), self.today, output_mult=lambda f, s=share: 1 - 0.22 * s * (0.4 + 0.6 * f), defect_mult=1.0, extra_downtime=lambda f, s=share: 0.8 * s * f)
            lost_total += totals["lost_units"]
            result.touch("production_metrics", *totals["metric_ids"])
            result.touch("production_lines", line.id)
        # 5. orders for dependent products that were promised soon slip
        dep_ids = [p.id for p in dependent]
        at_risk = list(self.db.scalars(
            select(Order).join(OrderItem).where(OrderItem.product_id.in_(dep_ids), Order.status.in_([OrderStatus.IN_PRODUCTION, OrderStatus.CONFIRMED]), Order.promised_delivery_date <= self.today + timedelta(days=delay_days)).distinct()
        ).all())
        for o in at_risk:
            o.status = OrderStatus.DELAYED
            result.touch("orders", o.id)
        for p in dependent:
            result.touch("products", p.id)
        self.recompute_financials(start.date(), end.date())
        result.effects.update({
            "supplier_lead_time_days": supplier.lead_time_days, "supplier_baseline_lead_time_days": supplier.baseline_lead_time_days,
            "delayed_batches": len(open_batches), "dependent_products": [p.sku for p in dependent], "production_units_lost": round(lost_total),
            "orders_delayed": len(at_risk), "at_risk_revenue": round(sum(float(o.total_revenue) for o in at_risk), 2),
        })
        result.summary = f"{supplier.name} ({supplier.component_type}) is {delay_days} days late; component stock is below safety stock, {len(lines)} line(s) slowed and {len(at_risk)} orders slipped."
        return result
