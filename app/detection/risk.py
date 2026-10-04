"""Business risk detection (deterministic rules over commercial + supply data).

Rules: inventory shortage, supplier delay, production bottleneck, revenue decline,
margin decline, delivery delay, demand/supply mismatch. Each finding names the records
(inventory, batches, orders, financial metrics, production metrics) that justify it.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.detection.base import Finding, LineEconomics, ids, mean, pct_change, production_windows, window_dict
from app.detection.config import DetectionConfig
from app.impact import ImpactEngine
from app.models import FinancialMetric, Inventory, InventorySnapshot, Order, OrderItem, Product, ProductionLine, Supplier, SupplierBatch
from app.models.enums import BatchStatus, LineStatus, OrderStatus, SupplierStatus
from app.simulation import catalog as cat

OPEN_STATUSES = [OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.IN_PRODUCTION, OrderStatus.DELAYED]


class BusinessRiskDetector:
    def __init__(self, db: Session, config: DetectionConfig | None = None, impact: ImpactEngine | None = None) -> None:
        self.db = db
        self.cfg = config or DetectionConfig()
        self.impact = impact or ImpactEngine()

    def run(self, now: datetime) -> list[Finding]:
        self.now = now
        self.today = now.date()
        self.econ = LineEconomics(self.db)
        self.products = self.econ.products
        self.bom = {p.id: {**cat.BOM_BY_CATEGORY.get(p.category, {}), **cat.BOM_BY_SKU.get(p.sku, {})} for p in self.products.values()}
        self.open_orders = self.db.scalars(select(Order).where(Order.status.in_(OPEN_STATUSES))).all()
        self.open_items = self.db.execute(select(OrderItem.order_id, OrderItem.product_id, OrderItem.quantity).where(OrderItem.order_id.in_([o.id for o in self.open_orders]))).all() if self.open_orders else []
        self.open_by_product: dict[uuid.UUID, list[tuple[uuid.UUID, int]]] = defaultdict(list)
        for oid, pid, qty in self.open_items:
            self.open_by_product[pid].append((oid, qty))
        self.orders_by_id = {o.id: o for o in self.open_orders}
        findings: list[Finding] = []
        findings += self._inventory_shortage()
        findings += self._supplier_delay()
        findings += self._production_bottleneck()
        findings += self._revenue_decline()
        findings += self._margin_decline()
        findings += self._delivery_delay()
        findings += self._demand_supply_mismatch()
        return findings

    # ---- helpers --------------------------------------------------------------------------
    def _snapshots(self, inv: Inventory, days: int = 14) -> list[dict[str, Any]]:
        rows = self.db.scalars(select(InventorySnapshot).where(InventorySnapshot.inventory_id == inv.id, InventorySnapshot.snapshot_date > self.today - timedelta(days=days)).order_by(InventorySnapshot.snapshot_date)).all()
        return [{"id": str(s.id), "date": s.snapshot_date.isoformat(), "quantity": s.quantity_available, "in": s.inbound_units, "out": s.outbound_units} for s in rows]

    def _dependent_products(self, component_type: str) -> list[Product]:
        return [p for p in self.products.values() if component_type in self.bom[p.id]]

    def _orders_for_products(self, pids: list[uuid.UUID], promised_within_days: int | None = None) -> list[Order]:
        seen: dict[uuid.UUID, Order] = {}
        for pid in pids:
            for oid, _ in self.open_by_product.get(pid, []):
                o = self.orders_by_id[oid]
                if promised_within_days is None or o.promised_delivery_date <= self.today + timedelta(days=promised_within_days):
                    seen[o.id] = o
        return sorted(seen.values(), key=lambda o: o.promised_delivery_date)

    # ---- 1. inventory shortage --------------------------------------------------------------
    def _inventory_shortage(self) -> list[Finding]:
        out: list[Finding] = []
        batches = self.db.scalars(select(SupplierBatch).where(SupplierBatch.received_date.is_(None))).all()
        inbound_by_component: dict[str, list[SupplierBatch]] = defaultdict(list)
        for b in batches:
            inbound_by_component[b.component_type].append(b)
        for inv in self.db.scalars(select(Inventory)).all():
            daily = max(inv.avg_daily_consumption, 1e-9)
            cover_days = inv.quantity_available / daily
            if inv.item_type.value == "PRODUCT":
                product = self.products.get(inv.product_id)
                if not product:
                    continue
                open_orders = self._orders_for_products([product.id], promised_within_days=7)
                open_units = sum(q for oid, q in self.open_by_product.get(product.id, []) if self.orders_by_id[oid] in open_orders)
                required = open_units + inv.avg_daily_consumption * 7
                shortage = inv.quantity_available < inv.safety_stock or inv.quantity_available < required * self.cfg.product_required_ratio
                if not shortage:
                    continue
                sev = "CRITICAL" if inv.quantity_available <= 0 or cover_days < self.cfg.inventory_cover_days_critical else "HIGH" if inv.quantity_available < inv.safety_stock else "MEDIUM"
                impact = self.impact.inventory_risk(required, inv.quantity_available, float(inv.unit_value))
                out.append(Finding(
                    kind="risk", type="INVENTORY_SHORTAGE", severity=sev, dedupe_key=f"INVENTORY_SHORTAGE:{inv.id}", entity_type="inventory", entity_id=inv.id, entity_name=inv.item_name, product_id=product.id, production_line_id=product.production_line_id,
                    title=f"Stock-out risk: {inv.item_name} ({inv.quantity_available} units, {cover_days:.1f} days of cover)",
                    description=f"{inv.item_name} has {inv.quantity_available} units at {inv.warehouse} against a safety stock of {inv.safety_stock} and reorder point of {inv.reorder_point}. {len(open_orders)} open orders ({open_units} units) are due within 7 days and average demand is {inv.avg_daily_consumption:.0f}/day, so about {required:.0f} units are required.",
                    affected_metrics={"quantity_available": inv.quantity_available, "safety_stock": inv.safety_stock, "reorder_point": inv.reorder_point, "days_of_cover": round(cover_days, 1), "required_next_7_days": round(required), "open_orders": len(open_orders)},
                    evidence={"window": window_dict(self.today - timedelta(days=14), self.today), "inventory": {"id": str(inv.id), "warehouse": inv.warehouse, "avg_daily_consumption": inv.avg_daily_consumption}, "snapshots_14d": self._snapshots(inv), "open_orders": [{"id": str(o.id), "number": o.order_number, "promised": o.promised_delivery_date.isoformat(), "status": o.status.value, "revenue": float(o.total_revenue)} for o in open_orders[:25]],
                              "records": {"inventory_id": str(inv.id), "product_id": str(product.id), "production_line_id": str(product.production_line_id), "order_ids": ids(open_orders, 50)}, "trace": ["inventory", "inventory_snapshot", "product", "order", "order_item"], "rule": f"available < safety_stock or available < {self.cfg.product_required_ratio} * (open orders due in 7d + 7d demand)"},
                    impact=impact.as_dict(),
                ))
            else:
                inbound = sorted(inbound_by_component.get(inv.component_type, []), key=lambda b: b.expected_date)
                delayed = [b for b in inbound if b.status == BatchStatus.DELAYED or b.expected_date < self.today]
                on_time = [b for b in inbound if b not in delayed]
                next_ok_arrival_days = min(((b.expected_date - self.today).days for b in on_time), default=None)
                covered_by_inbound = next_ok_arrival_days is not None and next_ok_arrival_days <= cover_days
                shortage = inv.quantity_available < inv.safety_stock or (cover_days < self.cfg.inventory_cover_days_high and not covered_by_inbound)
                if not shortage:
                    continue
                dependent = self._dependent_products(inv.component_type)
                lines = sorted({self.econ.lines[p.production_line_id].code for p in dependent if p.production_line_id in self.econ.lines})
                at_risk_orders = self._orders_for_products([p.id for p in dependent], promised_within_days=int(max(cover_days, 1)) + 7)
                required = inv.avg_daily_consumption * max(self.cfg.inventory_cover_days_high, (delayed[0].delay_days if delayed else 0) + 2)
                sev = "CRITICAL" if inv.quantity_available <= 0 or cover_days < self.cfg.inventory_cover_days_critical else "HIGH" if inv.quantity_available < inv.safety_stock else "MEDIUM"
                out.append(Finding(
                    kind="risk", type="INVENTORY_SHORTAGE", severity=sev, dedupe_key=f"INVENTORY_SHORTAGE:{inv.id}", entity_type="inventory", entity_id=inv.id, entity_name=inv.item_name,
                    title=f"Component shortage: {inv.item_name} ({cover_days:.1f} days of cover)",
                    description=f"{inv.item_name} stock is {inv.quantity_available} units ({cover_days:.1f} days at {inv.avg_daily_consumption:.0f}/day) versus safety stock {inv.safety_stock}. " + (f"{len(delayed)} inbound batch(es) are delayed. " if delayed else "No on-time replenishment arrives before stock runs out. ") + f"Feeds {', '.join(lines) or 'no line'} via {len(dependent)} products.",
                    affected_metrics={"quantity_available": inv.quantity_available, "safety_stock": inv.safety_stock, "days_of_cover": round(cover_days, 1), "delayed_batches": len(delayed), "next_on_time_arrival_days": next_ok_arrival_days, "dependent_products": len(dependent), "at_risk_orders": len(at_risk_orders)},
                    evidence={"window": window_dict(self.today - timedelta(days=14), self.today), "snapshots_14d": self._snapshots(inv), "inbound_batches": [{"id": str(b.id), "batch": b.batch_number, "status": b.status.value, "expected": b.expected_date.isoformat(), "delay_days": b.delay_days, "supplier_id": str(b.supplier_id)} for b in inbound],
                              "dependent_products": [{"id": str(p.id), "sku": p.sku, "name": p.name} for p in dependent], "records": {"inventory_id": str(inv.id), "supplier_batch_ids": ids(inbound), "product_ids": ids(dependent), "order_ids": ids(at_risk_orders, 50), "production_line_codes": lines}, "trace": ["inventory", "inventory_snapshot", "supplier_batch", "supplier", "product", "production_line", "order"], "rule": "available < safety_stock or (days_of_cover < threshold and no on-time inbound batch)"},
                    impact=self.impact.inventory_risk(required, inv.quantity_available, float(inv.unit_value)).as_dict(),
                ))
        return out

    # ---- 2. supplier delay ------------------------------------------------------------------
    def _supplier_delay(self) -> list[Finding]:
        out: list[Finding] = []
        suppliers = self.db.scalars(select(Supplier)).all()
        count_by_component = defaultdict(int)
        for s in suppliers:
            count_by_component[s.component_type] += 1
        for s in suppliers:
            batches = self.db.scalars(select(SupplierBatch).where(SupplierBatch.supplier_id == s.id, SupplierBatch.received_date.is_(None))).all()
            delayed = [b for b in batches if b.status == BatchStatus.DELAYED or b.expected_date < self.today]
            ratio = s.lead_time_days / max(s.baseline_lead_time_days, 1)
            if not delayed and ratio < self.cfg.supplier_lead_time_ratio and s.status != SupplierStatus.AT_RISK:
                continue
            inv = self.db.scalar(select(Inventory).where(Inventory.component_type == s.component_type))
            cover = inv.quantity_available / max(inv.avg_daily_consumption, 1e-9) if inv else 0.0
            sole_source = count_by_component[s.component_type] == 1
            dependent = self._dependent_products(s.component_type)
            delay_days = max([b.delay_days for b in delayed] + [max(0, s.lead_time_days - s.baseline_lead_time_days)])
            at_risk = self._orders_for_products([p.id for p in dependent], promised_within_days=delay_days + 3)
            at_risk_revenue = sum(float(o.total_revenue) for o in at_risk)
            shortfall = max(0.0, (inv.avg_daily_consumption * delay_days - inv.quantity_available)) if inv else 0.0
            sev = "CRITICAL" if sole_source and inv is not None and inv.quantity_available < inv.safety_stock else "HIGH" if delayed and cover < delay_days else "MEDIUM"
            out.append(Finding(
                kind="risk", type="SUPPLIER_DELAY", severity=sev, dedupe_key=f"SUPPLIER_DELAY:{s.id}", entity_type="supplier", entity_id=s.id, entity_name=s.name,
                title=f"Supplier delay: {s.name} ({s.component_type}) +{delay_days} days" + (" - sole source" if sole_source else ""),
                description=f"{s.name} ({s.location}) supplies {s.component_type}" + (" as the only qualified source" if sole_source else "") + f". Lead time is {s.lead_time_days} days versus a baseline of {s.baseline_lead_time_days}; {len(delayed)} open batch(es) are late. Component cover is {cover:.1f} days, and {len(at_risk)} open orders worth {at_risk_revenue:,.0f} INR depend on the {len(dependent)} affected products.",
                affected_metrics={"lead_time_days": s.lead_time_days, "baseline_lead_time_days": s.baseline_lead_time_days, "reliability_score": s.reliability_score, "delayed_batches": len(delayed), "component_days_of_cover": round(cover, 1), "at_risk_orders": len(at_risk), "at_risk_revenue": round(at_risk_revenue, 2)},
                evidence={"window": window_dict(self.today - timedelta(days=14), self.today), "supplier": {"id": str(s.id), "status": s.status.value, "sole_source": sole_source}, "batches": [{"id": str(b.id), "batch": b.batch_number, "status": b.status.value, "ordered": b.ordered_date.isoformat(), "expected": b.expected_date.isoformat(), "delay_days": b.delay_days, "quantity": b.quantity} for b in batches],
                          "component_inventory": {"id": str(inv.id), "quantity": inv.quantity_available, "safety_stock": inv.safety_stock, "snapshots_14d": self._snapshots(inv)} if inv else None, "dependent_products": [{"id": str(p.id), "sku": p.sku, "name": p.name} for p in dependent], "at_risk_orders": [{"id": str(o.id), "number": o.order_number, "status": o.status.value, "promised": o.promised_delivery_date.isoformat(), "revenue": float(o.total_revenue)} for o in at_risk[:25]],
                          "records": {"supplier_id": str(s.id), "supplier_batch_ids": ids(batches), "inventory_id": str(inv.id) if inv else None, "product_ids": ids(dependent), "order_ids": ids(at_risk, 50)}, "trace": ["supplier", "supplier_batch", "inventory", "product", "order"], "rule": f"delayed batch or lead_time/baseline >= {self.cfg.supplier_lead_time_ratio} or status AT_RISK"},
                impact=self.impact.supplier_delay_impact(shortfall, float(s.unit_cost), at_risk_revenue).as_dict(),
            ))
        return out

    # ---- 3. production bottleneck -----------------------------------------------------------
    def _production_bottleneck(self) -> list[Finding]:
        out: list[Finding] = []
        for line in self.econ.lines.values():
            recent, baseline = production_windows(self.db, line.id, self.today, self.cfg.production_recent_days, self.cfg.production_baseline_days)
            if not recent:
                continue
            util = mean(m.utilization for m in recent)
            gap_days = [m for m in recent if m.planned_units > 0 and (m.planned_units - m.output_units) / m.planned_units >= self.cfg.bottleneck_planned_gap]
            planned_gap = mean((m.planned_units - m.output_units) / m.planned_units for m in recent if m.planned_units) if recent else 0.0
            pids = [p.id for p in self.econ.by_line.get(line.id, [])]
            backlog_orders = self._orders_for_products(pids)
            backlog_units = sum(q for pid in pids for _, q in self.open_by_product.get(pid, []))
            backlog_days = backlog_units / max(line.capacity_units_per_day, 1)
            saturated = util >= self.cfg.bottleneck_utilization
            over_plan = len(gap_days) >= self.cfg.bottleneck_min_days and util >= 0.85
            if not (saturated or over_plan or line.status == LineStatus.BOTTLENECKED):
                continue
            sev = "CRITICAL" if util >= 0.98 and planned_gap >= 0.2 else "HIGH" if saturated or planned_gap >= 0.15 else "MEDIUM"
            unfilled = sum(max(0, m.planned_units - m.output_units) for m in recent)
            due_soon = [o for o in backlog_orders if o.promised_delivery_date <= self.today + timedelta(days=7)]
            loss = self.impact.production_loss(unfilled, self.econ.unit_margin(line.id))
            late = self.impact.late_delivery_impact(sum(float(o.total_revenue) for o in due_soon))
            impact = loss.as_dict()
            impact.update({"formula": "bottleneck_impact = production_loss(unfilled planned units) + late_delivery_impact(orders due in 7d)", "value": round(loss.value + late.value, 2), "components": {"unfilled_plan_margin": round(loss.value, 2), "late_delivery_exposure": round(late.value, 2)}, "inputs": {**loss.inputs, **late.inputs}})
            out.append(Finding(
                kind="risk", type="PRODUCTION_BOTTLENECK", severity=sev, dedupe_key=f"PRODUCTION_BOTTLENECK:{line.id}", entity_type="production_line", entity_id=line.id, entity_name=line.name, production_line_id=line.id,
                title=f"Bottleneck on {line.name}: {util:.0%} utilization, plan missed by {planned_gap:.0%}",
                description=f"{line.name} ran at {util:.0%} average utilization over the last {len(recent)} working days (capacity {line.capacity_units_per_day}/day); planned volume exceeded output by {planned_gap:.0%} on {len(gap_days)} day(s), leaving {unfilled} planned units unbuilt. Open backlog is {backlog_units} units (~{backlog_days:.1f} days of capacity) across {len(backlog_orders)} orders, {len(due_soon)} of them due within 7 days.",
                affected_metrics={"utilization_recent": round(util, 3), "utilization_baseline": round(mean(m.utilization for m in baseline), 3) if baseline else None, "planned_gap_pct": round(planned_gap * 100, 1), "unfilled_planned_units": unfilled, "backlog_units": backlog_units, "backlog_days_of_capacity": round(backlog_days, 1), "orders_due_7d": len(due_soon)},
                evidence={"window": window_dict(recent[0].metric_date, recent[-1].metric_date), "daily": [{"id": str(m.id), "date": m.metric_date.isoformat(), "planned": m.planned_units, "output": m.output_units, "utilization": m.utilization, "downtime": m.downtime_hours} for m in recent], "backlog_orders": [{"id": str(o.id), "number": o.order_number, "promised": o.promised_delivery_date.isoformat(), "status": o.status.value} for o in backlog_orders[:25]],
                          "records": {"production_line_id": str(line.id), "production_metric_ids": ids(recent), "order_ids": ids(backlog_orders, 50), "product_ids": [str(p) for p in pids]}, "trace": ["production_metric", "production_line", "product", "order"], "rule": f"utilization >= {self.cfg.bottleneck_utilization} or plan gap >= {self.cfg.bottleneck_planned_gap} on >= {self.cfg.bottleneck_min_days} days"},
                impact=impact,
            ))
        return out

    # ---- 4. revenue decline -------------------------------------------------------------------
    def _periods(self) -> tuple[list[FinancialMetric], list[FinancialMetric]]:
        n = self.cfg.compare_days
        rows = self.db.scalars(select(FinancialMetric).where(FinancialMetric.metric_date > self.today - timedelta(days=3 * n), FinancialMetric.metric_date <= self.today).order_by(FinancialMetric.metric_date)).all()
        split = self.today - timedelta(days=n)
        return [r for r in rows if r.metric_date > split], [r for r in rows if r.metric_date <= split]

    def _category_revenue(self, start, end) -> dict[str, float]:
        rows = self.db.execute(select(Product.category, func.sum(OrderItem.line_revenue)).join(OrderItem, OrderItem.product_id == Product.id).join(Order, Order.id == OrderItem.order_id).where(Order.order_date > start, Order.order_date <= end, Order.status != OrderStatus.CANCELLED).group_by(Product.category)).all()
        return {c: float(v or 0) for c, v in rows}

    def _revenue_decline(self) -> list[Finding]:
        out: list[Finding] = []
        recent, previous = self._periods()
        if not recent or not previous:
            return out
        n = self.cfg.compare_days
        r_rev = sum(float(m.revenue) for m in recent)
        p_rev = sum(float(m.revenue) for m in previous) * (len(recent) / len(previous))  # normalised to the same length
        change = pct_change(r_rev, p_rev)
        nc = self.cfg.category_compare_days
        cur_cat = self._category_revenue(self.today - timedelta(days=nc), self.today)
        prev_cat = self._category_revenue(self.today - timedelta(days=2 * nc), self.today - timedelta(days=nc))
        total_prev = sum(prev_cat.values()) or 1.0
        breakdown = sorted(({"category": c, "recent": round(cur_cat.get(c, 0.0), 2), "previous": round(prev_cat.get(c, 0.0), 2), "change_pct": round(pct_change(cur_cat.get(c, 0.0), prev_cat.get(c, 0.0)) * 100, 1), "share_of_previous": round(prev_cat.get(c, 0.0) / total_prev, 3)} for c in set(cur_cat) | set(prev_cat)), key=lambda b: b["recent"] - b["previous"])
        window = window_dict(self.today - timedelta(days=n), self.today, previous_period_days=2 * n)
        fin_ids = {"financial_metric_ids_recent": ids(recent), "financial_metric_ids_previous": ids(previous, 40)}
        if change <= -self.cfg.revenue_decline_pct:
            sev = "CRITICAL" if change <= -0.3 else "HIGH" if change <= -0.2 else "MEDIUM"
            driver = breakdown[0] if breakdown else None
            out.append(Finding(
                kind="risk", type="REVENUE_DECLINE", severity=sev, dedupe_key="REVENUE_DECLINE:company", entity_type="company", entity_name="Astra Manufacturing",
                title=f"Revenue decline: {change * 100:.0f}% over the last {n} days",
                description=f"Booked revenue over the last {n} days was {r_rev:,.0f} INR versus {p_rev:,.0f} INR in the comparable prior period ({change * 100:+.1f}%)." + (f" Largest contributor: {driver['category']} ({driver['change_pct']:+.0f}%)." if driver else ""),
                affected_metrics={"revenue_recent": round(r_rev, 2), "revenue_previous": round(p_rev, 2), "change_pct": round(change * 100, 1)},
                evidence={"window": window, "category_breakdown": breakdown, "records": fin_ids, "trace": ["financial_metric", "order", "order_item", "product"], "rule": f"decline >= {self.cfg.revenue_decline_pct:.0%} vs normalised prior {2 * n} days"},
                impact=self.impact.revenue_impact(p_rev, r_rev).as_dict(),
            ))
        for b in breakdown:
            if b["share_of_previous"] >= self.cfg.category_min_revenue_share and b["change_pct"] <= -self.cfg.category_revenue_decline_pct * 100:
                products = [p for p in self.products.values() if p.category == b["category"]]
                order_ids = self.db.scalars(select(Order.id).join(OrderItem).join(Product).where(Product.category == b["category"], Order.order_date > self.today - timedelta(days=nc)).distinct()).all()
                sev = "HIGH" if b["change_pct"] <= -50 else "MEDIUM"
                out.append(Finding(
                    kind="risk", type="REVENUE_DECLINE", severity=sev, dedupe_key=f"REVENUE_DECLINE:category:{b['category']}", entity_type="category", entity_name=b["category"], production_line_id=products[0].production_line_id if products else None,
                    title=f"Revenue decline in {b['category']}: {b['change_pct']:+.0f}% over {nc} days",
                    description=f"Revenue from {b['category']} fell from {b['previous']:,.0f} INR to {b['recent']:,.0f} INR ({b['change_pct']:+.1f}%) over the last {nc} days; the category previously contributed {b['share_of_previous']:.0%} of revenue. {len(order_ids)} orders in the period.",
                    affected_metrics={"category_revenue_recent": b["recent"], "category_revenue_previous": b["previous"], "change_pct": b["change_pct"], "share_of_previous": b["share_of_previous"], "company_change_pct": round(change * 100, 1)},
                    evidence={"window": window_dict(self.today - timedelta(days=nc), self.today, previous_period_days=nc), "category_breakdown": breakdown, "products": [{"id": str(p.id), "sku": p.sku, "name": p.name} for p in products], "records": {**fin_ids, "product_ids": ids(products), "order_ids": [str(o) for o in order_ids[:50]]}, "trace": ["financial_metric", "order", "order_item", "product", "market_signal"], "rule": f"category decline >= {self.cfg.category_revenue_decline_pct:.0%} with share >= {self.cfg.category_min_revenue_share:.0%}"},
                    impact=self.impact.revenue_impact(b["previous"], b["recent"]).as_dict(),
                ))
        return out

    # ---- 5. margin decline ---------------------------------------------------------------------
    def _margin_decline(self) -> list[Finding]:
        out: list[Finding] = []
        recent, previous = self._periods()
        n = self.cfg.compare_days
        if recent and previous:
            r_m = mean(m.gross_margin for m in recent if float(m.revenue) > 0)
            p_m = mean(m.gross_margin for m in previous if float(m.revenue) > 0)
            drop = p_m - r_m
            if drop >= self.cfg.margin_decline_points:
                r_rev = sum(float(m.revenue) for m in recent)
                sev = "HIGH" if drop >= 0.04 else "MEDIUM"
                out.append(Finding(
                    kind="risk", type="MARGIN_DECLINE", severity=sev, dedupe_key="MARGIN_DECLINE:company", entity_type="company", entity_name="Astra Manufacturing",
                    title=f"Gross margin down {drop * 100:.1f} pts to {r_m:.1%}",
                    description=f"Average daily gross margin over the last {n} days is {r_m:.1%} versus {p_m:.1%} in the prior {2 * n} days ({-drop * 100:.1f} pts).",
                    affected_metrics={"gross_margin_recent": round(r_m, 4), "gross_margin_previous": round(p_m, 4), "change_points": round(-drop * 100, 2)},
                    evidence={"window": window_dict(self.today - timedelta(days=n), self.today), "records": {"financial_metric_ids_recent": ids(recent), "financial_metric_ids_previous": ids(previous, 40)}, "trace": ["financial_metric", "order", "order_item", "product"], "rule": f"margin drop >= {self.cfg.margin_decline_points * 100:.0f} pts"},
                    impact=self.impact.revenue_impact(r_rev * p_m, r_rev * r_m).as_dict() | {"description": "Gross profit lost versus prior margin on the same revenue"},
                ))
        # product-level realised margin vs target
        rows = self.db.execute(select(OrderItem.product_id, func.sum(OrderItem.line_revenue), func.sum(OrderItem.quantity * OrderItem.unit_cost), func.count(OrderItem.id)).join(Order).where(Order.order_date > self.today - timedelta(days=30), Order.status != OrderStatus.CANCELLED).group_by(OrderItem.product_id)).all()
        total_rev = sum(float(r[1] or 0) for r in rows) or 1.0
        for pid, rev, cost, n_items in rows:
            rev, cost = float(rev or 0), float(cost or 0)
            p = self.products.get(pid)
            if not p or rev <= 0 or rev / total_rev < self.cfg.product_min_revenue_share:
                continue
            realised = (rev - cost) / rev
            gap = p.target_margin - realised
            if gap < self.cfg.product_margin_gap_points:
                continue
            sev = "HIGH" if gap >= 0.10 else "MEDIUM"
            order_ids = self.db.scalars(select(OrderItem.order_id).join(Order).where(OrderItem.product_id == pid, Order.order_date > self.today - timedelta(days=30)).distinct()).all()
            out.append(Finding(
                kind="risk", type="MARGIN_DECLINE", severity=sev, dedupe_key=f"MARGIN_DECLINE:product:{pid}", entity_type="product", entity_id=pid, entity_name=p.name, product_id=pid, production_line_id=p.production_line_id,
                title=f"Margin pressure on {p.name}: {realised:.1%} realised vs {p.target_margin:.0%} target",
                description=f"Over the last 30 days {p.name} realised a {realised:.1%} gross margin on {rev:,.0f} INR of revenue ({n_items} order lines) against a target of {p.target_margin:.0%} (gap {gap * 100:.1f} pts).",
                affected_metrics={"realised_margin": round(realised, 4), "target_margin": p.target_margin, "gap_points": round(gap * 100, 2), "revenue_30d": round(rev, 2), "revenue_share": round(rev / total_rev, 3)},
                evidence={"window": window_dict(self.today - timedelta(days=30), self.today), "records": {"product_id": str(pid), "order_ids": [str(o) for o in order_ids[:50]]}, "trace": ["order_item", "order", "product"], "rule": f"target - realised >= {self.cfg.product_margin_gap_points * 100:.0f} pts"},
                impact=self.impact.revenue_impact(rev * p.target_margin, rev - cost).as_dict() | {"description": "Gross profit shortfall versus target margin over 30 days"},
            ))
        return out

    # ---- 6. delivery delay ----------------------------------------------------------------------
    def _delivery_delay(self) -> list[Finding]:
        out: list[Finding] = []
        n = self.cfg.compare_days
        overdue = [o for o in self.open_orders if o.status == OrderStatus.DELAYED or o.promised_delivery_date < self.today]
        delivered = self.db.scalars(select(Order).where(Order.actual_delivery_date > self.today - timedelta(days=n), Order.status == OrderStatus.DELIVERED)).all()
        late_delivered = [o for o in delivered if o.actual_delivery_date and o.actual_delivery_date > o.promised_delivery_date]
        late_ratio = len(late_delivered) / len(delivered) if delivered else 0.0
        # group by production line through order items
        line_of_order: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        all_ids = [o.id for o in overdue + delivered]
        if all_ids:
            for oid, pid in self.db.execute(select(OrderItem.order_id, OrderItem.product_id).where(OrderItem.order_id.in_(all_ids))).all():
                p = self.products.get(pid)
                if p and p.production_line_id:
                    line_of_order[oid].add(p.production_line_id)
        by_line: dict[uuid.UUID, dict[str, list[Order]]] = defaultdict(lambda: {"overdue": [], "late": [], "delivered": []})
        for o in overdue:
            for lid in line_of_order.get(o.id, set()):
                by_line[lid]["overdue"].append(o)
        for o in delivered:
            for lid in line_of_order.get(o.id, set()):
                by_line[lid]["delivered"].append(o)
                if o.actual_delivery_date and o.actual_delivery_date > o.promised_delivery_date:
                    by_line[lid]["late"].append(o)
        for lid, groups in by_line.items():
            line = self.econ.lines.get(lid)
            if not line:
                continue
            n_over, n_late = len(groups["overdue"]), len(groups["late"])
            line_late_ratio = n_late / len(groups["delivered"]) if groups["delivered"] else 0.0
            if n_over < self.cfg.delivery_delay_min_orders and line_late_ratio < self.cfg.late_delivery_ratio:
                continue
            at_risk_rev = sum(float(o.total_revenue) for o in groups["overdue"])
            sev = "CRITICAL" if n_over >= 10 else "HIGH" if n_over >= 6 else "MEDIUM"
            out.append(Finding(
                kind="risk", type="DELIVERY_DELAY", severity=sev, dedupe_key=f"DELIVERY_DELAY:{lid}", entity_type="production_line", entity_id=lid, entity_name=line.name, production_line_id=lid,
                title=f"Delivery delays on {line.name}: {n_over} overdue orders, {n_late} late deliveries in {n} days",
                description=f"{n_over} open orders for {line.name} products are past their promised date (revenue at risk {at_risk_rev:,.0f} INR) and {n_late} of {len(groups['delivered'])} deliveries of its products in the last {n} days were late ({line_late_ratio:.0%}; company-wide {late_ratio:.0%}).",
                affected_metrics={"overdue_orders": n_over, "late_deliveries_recent": n_late, "deliveries_recent": len(groups["delivered"]), "late_ratio_line": round(line_late_ratio, 3), "late_ratio_company": round(late_ratio, 3), "at_risk_revenue": round(at_risk_rev, 2)},
                evidence={"window": window_dict(self.today - timedelta(days=n), self.today), "overdue_orders": [{"id": str(o.id), "number": o.order_number, "status": o.status.value, "promised": o.promised_delivery_date.isoformat(), "days_overdue": (self.today - o.promised_delivery_date).days, "revenue": float(o.total_revenue), "customer_id": str(o.customer_id)} for o in groups["overdue"][:25]],
                          "late_deliveries": [{"id": str(o.id), "number": o.order_number, "promised": o.promised_delivery_date.isoformat(), "delivered": o.actual_delivery_date.isoformat(), "days_late": (o.actual_delivery_date - o.promised_delivery_date).days} for o in groups["late"][:25]],
                          "records": {"production_line_id": str(lid), "order_ids": ids(groups["overdue"] + groups["late"], 60)}, "trace": ["order", "order_item", "product", "production_line", "customer"], "rule": f"overdue >= {self.cfg.delivery_delay_min_orders} or late ratio >= {self.cfg.late_delivery_ratio:.0%}"},
                impact=self.impact.late_delivery_impact(at_risk_rev).as_dict(),
            ))
        return out

    # ---- 7. demand / supply mismatch -------------------------------------------------------------
    def _demand_supply_mismatch(self) -> list[Finding]:
        out: list[Finding] = []
        n = self.cfg.compare_days
        start = self.today - timedelta(days=n)
        demand_rows = self.db.execute(select(Product.production_line_id, func.sum(OrderItem.quantity)).join(OrderItem, OrderItem.product_id == Product.id).join(Order, Order.id == OrderItem.order_id).where(Order.order_date > start, Order.order_date <= self.today, Order.status != OrderStatus.CANCELLED).group_by(Product.production_line_id)).all()
        demand = {lid: int(q or 0) for lid, q in demand_rows}
        for line in self.econ.lines.values():
            recent, _ = production_windows(self.db, line.id, self.today, n, 1)
            supply = sum(m.output_units - m.defect_units for m in recent)
            d = demand.get(line.id, 0)
            if not d or not supply:
                continue
            gap = (d - supply) / supply
            if gap >= self.cfg.demand_supply_gap:
                kind, sev = "undersupply", "HIGH" if gap >= 0.4 else "MEDIUM"
                unmet = d - supply
                impact = self.impact.production_loss(unmet, self.econ.unit_margin(line.id)).as_dict() | {"description": f"Margin on {unmet} units of demand that current output cannot serve"}
                title = f"Demand exceeds output on {line.name} by {gap:.0%} ({d} ordered vs {supply} good units in {n} days)"
            elif gap <= -self.cfg.oversupply_gap:
                kind, sev = "oversupply", "MEDIUM"
                excess = supply - d
                inv_items = [self.db.scalar(select(Inventory).where(Inventory.product_id == p.id)) for p in self.econ.by_line.get(line.id, [])]
                holding = mean(float(i.holding_cost_per_unit_month) for i in inv_items if i) if inv_items else 20.0
                impact = self.impact.excess_inventory_value(excess, self.econ.unit_cost(line.id), holding).as_dict() | {"description": f"Working capital tied up in {excess} units produced beyond demand"}
                title = f"Output exceeds demand on {line.name} by {-gap:.0%} ({supply} good units vs {d} ordered in {n} days)"
            else:
                continue
            order_ids = self.db.scalars(select(Order.id).join(OrderItem).join(Product).where(Product.production_line_id == line.id, Order.order_date > start).distinct()).all()
            out.append(Finding(
                kind="risk", type="DEMAND_SUPPLY_MISMATCH", severity=sev, dedupe_key=f"DEMAND_SUPPLY_MISMATCH:{line.id}", entity_type="production_line", entity_id=line.id, entity_name=line.name, production_line_id=line.id,
                title=title, description=f"Over the last {n} days customers ordered {d} units of {line.name} products while the line produced {supply} good units ({kind}, gap {gap:+.0%}).",
                affected_metrics={"ordered_units": d, "good_output_units": supply, "gap_pct": round(gap * 100, 1), "mismatch": kind},
                evidence={"window": window_dict(start, self.today), "records": {"production_line_id": str(line.id), "production_metric_ids": ids(recent), "order_ids": [str(o) for o in order_ids[:50]]}, "trace": ["order", "order_item", "product", "production_line", "production_metric"], "rule": f"|demand - supply| / supply >= {self.cfg.demand_supply_gap:.0%} (undersupply) or {self.cfg.oversupply_gap:.0%} (oversupply)"},
                impact=impact,
            ))
        return out
