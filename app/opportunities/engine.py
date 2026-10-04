"""Business opportunity detection (deterministic rules over orders, inventory, capacity and
SYNTHETIC market / competitor signals). All impact figures come from the impact engine and
are clearly labelled as synthetic estimates.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.base import utcnow
from app.detection.base import Finding, LineEconomics, ids, mean, pct_change, production_windows, window_dict
from app.impact import ImpactEngine
from app.models import CompetitorSignal, Customer, DetectionRun, Inventory, InventorySnapshot, MarketSignal, Opportunity, Order, OrderItem, Product, SystemState
from app.models.enums import CompetitorSignalType, Confidence, DetectionSource, OpportunityStatus, OpportunityType, OrderStatus, RunStatus, SignalType
from app.observability import get_logger, log_event
from app.opportunities.config import OpportunityConfig
from app.simulation import catalog as cat

log = get_logger("react.opportunities")
SEGMENT_LABELS = {"ENTERPRISE": "Enterprise", "MID_MARKET": "Mid-market", "SMB": "SMB"}


class OpportunityDetector:
    def __init__(self, db: Session, config: OpportunityConfig | None = None, impact: ImpactEngine | None = None) -> None:
        self.db = db
        self.cfg = config or OpportunityConfig()
        self.impact = impact or ImpactEngine()

    def run(self, now: datetime) -> list[Finding]:
        self.now, self.today = now, now.date()
        self.econ = LineEconomics(self.db)
        n = self.cfg.compare_days
        self.recent_start = self.today - timedelta(days=n)
        self.prev_start = self.today - timedelta(days=2 * n)
        rows = self.db.execute(
            select(Order.id, Order.order_date, Order.region, Order.customer_id, OrderItem.product_id, OrderItem.quantity, OrderItem.line_revenue, OrderItem.unit_cost)
            .join(OrderItem, OrderItem.order_id == Order.id).where(Order.order_date > self.prev_start, Order.order_date <= self.today, Order.status != OrderStatus.CANCELLED)
        ).all()
        self.items = [{"order_id": r[0], "date": r[1], "region": r[2], "customer_id": r[3], "product_id": r[4], "qty": r[5], "rev": float(r[6]), "cost": float(r[8 - 1]) * r[5], "recent": r[1] > self.recent_start} for r in rows]
        self.customers = {c.id: c for c in self.db.scalars(select(Customer)).all()}
        findings: list[Finding] = []
        findings += self._demand_growth()
        findings += self._underserved_regions()
        findings += self._high_margin_growth()
        findings += self._segment_growth()
        findings += self._excess_inventory()
        findings += self._available_capacity()
        findings += self._pricing()
        return findings

    # ---- helpers ------------------------------------------------------------------------------
    def _growth_z(self, growth: float, n_recent: int, n_prev: int) -> float:
        """Deterministic significance of a growth ratio given the order-line counts behind it.
        Standard error of a ratio of two Poisson counts ~ sqrt(1/n1 + 1/n2); a 1.3 factor allows for quantity variance."""
        if n_recent <= 0 or n_prev <= 0:
            return 0.0
        se = 1.3 * math.sqrt(1 / n_recent + 1 / n_prev)
        return growth / se if se else 0.0

    def _agg(self, key_fn) -> dict[Any, dict[str, Any]]:
        agg: dict[Any, dict[str, Any]] = defaultdict(lambda: {"recent_units": 0, "prev_units": 0, "recent_rev": 0.0, "prev_rev": 0.0, "recent_cost": 0.0, "recent_orders": set(), "prev_orders": set(), "recent_lines": 0, "prev_lines": 0})
        for it in self.items:
            k = key_fn(it)
            if k is None:
                continue
            a = agg[k]
            if it["recent"]:
                a["recent_units"] += it["qty"]
                a["recent_rev"] += it["rev"]
                a["recent_cost"] += it["cost"]
                a["recent_orders"].add(it["order_id"])
                a["recent_lines"] += 1
            else:
                a["prev_units"] += it["qty"]
                a["prev_rev"] += it["rev"]
                a["prev_orders"].add(it["order_id"])
                a["prev_lines"] += 1
        return agg

    def _market_signals(self, product_id=None, category=None, region=None, signal_types=None, days=60) -> list[MarketSignal]:
        q = select(MarketSignal).where(MarketSignal.signal_date > self.today - timedelta(days=days))
        if signal_types:
            q = q.where(MarketSignal.signal_type.in_(signal_types))
        conds = []
        if product_id is not None:
            conds.append(MarketSignal.product_id == product_id)
        if category:
            conds.append(MarketSignal.product_category == category)
        if conds:
            from sqlalchemy import or_

            q = q.where(or_(*conds))
        if region:
            q = q.where((MarketSignal.region == region) | (MarketSignal.region.is_(None)))
        return list(self.db.scalars(q.order_by(MarketSignal.signal_date.desc())).all())

    def _signal_summary(self, sigs: list[MarketSignal]) -> list[dict[str, Any]]:
        return [{"id": str(s.id), "date": s.signal_date.isoformat(), "type": s.signal_type.value, "region": s.region, "growth_rate_pct": s.growth_rate_pct, "demand_index": s.demand_index, "source": s.source, "is_synthetic": s.is_synthetic} for s in sigs[:8]]

    def _finding(self, type_, key, title, desc, product=None, region=None, segment=None, signal="", metrics=None, evidence=None, impact=None, confidence="MEDIUM", line_id=None) -> Finding:
        ev = {"synthetic_notice": "All market/competitor signals and impact figures are SYNTHETIC Phase 1 estimates.", **(evidence or {})}
        return Finding(kind="opportunity", type=type_, severity="MEDIUM", title=title, description=desc, dedupe_key=key, product_id=product.id if product else None, production_line_id=line_id or (product.production_line_id if product else None), region=region, customer_segment=segment, signal=signal, confidence=confidence, affected_metrics=metrics or {}, evidence=ev, impact=impact or {})

    # ---- 1. demand growth (product and product x region) --------------------------------------------
    def _demand_growth(self) -> list[Finding]:
        out: list[Finding] = []
        n = self.cfg.compare_days
        for scope, key_fn in (("product", lambda it: it["product_id"]), ("product_region", lambda it: (it["product_id"], it["region"]))):
            for k, a in self._agg(key_fn).items():
                pid, region = (k, None) if scope == "product" else k
                p = self.econ.products.get(pid)
                if not p or a["prev_units"] <= 0 or len(a["recent_orders"]) < self.cfg.demand_min_orders or len(a["prev_orders"]) < self.cfg.demand_min_previous_orders:
                    continue
                growth = pct_change(a["recent_units"], a["prev_units"])
                threshold = self.cfg.demand_growth_pct if scope == "product" else self.cfg.regional_demand_growth_pct
                z = self._growth_z(growth, a["recent_lines"], a["prev_lines"])
                if growth < threshold or z < self.cfg.growth_significance_z or (scope == "product_region" and len(a["recent_orders"]) < self.cfg.regional_min_orders):
                    continue
                sigs = self._market_signals(product_id=p.id, category=p.category, region=region, signal_types=[SignalType.PRODUCT_DEMAND_GROWTH, SignalType.MARKET_DEMAND, SignalType.CATEGORY_GROWTH])
                supportive = [s for s in sigs if s.growth_rate_pct > 0]
                if growth >= self.cfg.demand_growth_high_pct and z >= self.cfg.growth_high_significance_z and len(a["recent_orders"]) >= self.cfg.demand_min_orders_high and supportive:
                    conf = "HIGH"
                elif z >= self.cfg.growth_high_significance_z or supportive:
                    conf = "MEDIUM"
                else:
                    conf = "LOW"
                daily = a["recent_units"] / n
                incremental = daily * growth * self.cfg.horizon_days
                impact = self.impact.opportunity_value(incremental, self.econ.product_margin(p), self.cfg.horizon_days)
                where = f" in {region}" if region else ""
                out.append(self._finding(
                    "DEMAND_OPPORTUNITY", f"DEMAND_OPPORTUNITY:{p.id}:{region or 'ALL'}",
                    f"Demand for {p.name}{where} up {growth * 100:.0f}% ({a['recent_units']} vs {a['prev_units']} units)",
                    f"Orders for {p.name}{where} totalled {a['recent_units']} units across {len(a['recent_orders'])} orders in the last {n} days versus {a['prev_units']} units in the prior {n} days ({growth * 100:+.1f}%). " + (f"{len(supportive)} synthetic market signal(s) corroborate the trend." if supportive else "No corroborating market signal."),
                    product=p, region=region, signal="demand_growth", confidence=conf,
                    metrics={"growth_rate": round(growth * 100, 1), "significance_z": round(z, 2), "recent_units": a["recent_units"], "previous_units": a["prev_units"], "recent_orders": len(a["recent_orders"]), "previous_orders": len(a["prev_orders"]), "recent_revenue": round(a["recent_rev"], 2), "unit_margin": round(self.econ.product_margin(p), 2), "market_signal_count": len(supportive)},
                    evidence={"window": window_dict(self.recent_start, self.today, previous_start=self.prev_start.isoformat()), "market_signals": self._signal_summary(supportive), "records": {"product_id": str(p.id), "order_ids_recent": [str(o) for o in list(a["recent_orders"])[:50]], "order_ids_previous": [str(o) for o in list(a["prev_orders"])[:50]], "market_signal_ids": ids(supportive), "production_line_id": str(p.production_line_id)}, "trace": ["order", "order_item", "product", "market_signal", "region"], "rule": f"unit growth >= {threshold:.0%} over {n}d, significance z >= {self.cfg.growth_significance_z}, >= {self.cfg.demand_min_orders} orders"},
                    impact=impact.as_dict(),
                ))
        return out

    # ---- 2. underserved region --------------------------------------------------------------------
    def _underserved_regions(self) -> list[Finding]:
        out: list[Finding] = []
        by_cat_region = self._agg(lambda it: (self.econ.products[it["product_id"]].category, it["region"]) if it["product_id"] in self.econ.products else None)
        by_cat = self._agg(lambda it: self.econ.products[it["product_id"]].category if it["product_id"] in self.econ.products else None)
        comp = self.db.scalars(select(CompetitorSignal).where(CompetitorSignal.signal_date > self.today - timedelta(days=self.cfg.underserved_signal_days), CompetitorSignal.signal_type == CompetitorSignalType.AVAILABILITY)).all()
        for category, cat_agg in by_cat.items():
            total = cat_agg["recent_units"]
            if total <= 0:
                continue
            for region, rinfo in cat.REGIONS.items():
                a = by_cat_region.get((category, region), {"recent_units": 0, "recent_orders": set(), "recent_rev": 0.0})
                expected = total * rinfo["weight"]
                actual = a["recent_units"]
                comp_signals = [c for c in comp if c.product_category == category and (c.region == region or c.region is None) and (c.availability_score or 1) < 0.9]
                regional_all = sorted([s for s in self._market_signals(region=region, signal_types=[SignalType.REGIONAL_DEMAND], days=self.cfg.underserved_signal_days) if s.region == region], key=lambda s: s.signal_date)
                regional_growth = pct_change(regional_all[-1].demand_index, regional_all[0].demand_index) * 100 if len(regional_all) >= 2 else 0.0
                regional = regional_all if regional_growth >= self.cfg.underserved_regional_growth_pct else []
                if actual >= expected * self.cfg.underserved_share_ratio or not (comp_signals or regional):
                    continue
                gap_units = expected - actual
                prods = [p for p in self.econ.products.values() if p.category == category]
                margin = mean(self.econ.product_margin(p) for p in prods)
                conf = "HIGH" if comp_signals and regional else "MEDIUM" if comp_signals else "LOW"
                out.append(self._finding(
                    "UNDERSERVED_REGION", f"UNDERSERVED_REGION:{category}:{region}",
                    f"{region} is underserved for {category}: {actual} units vs ~{expected:.0f} expected",
                    f"Over the last {self.cfg.compare_days} days {region} accounted for {actual} units of {category} ({len(a['recent_orders'])} orders) against ~{expected:.0f} expected from its {rinfo['weight']:.0%} regional weight. " + (f"{len(comp_signals)} synthetic competitor availability constraint(s) reported in the region. " if comp_signals else "") + (f"{len(regional)} positive regional demand signals." if regional else ""),
                    region=region, signal="regional_gap", confidence=conf, line_id=prods[0].production_line_id if prods else None,
                    metrics={"actual_units": actual, "expected_units": round(expected), "gap_units": round(gap_units), "region_weight": rinfo["weight"], "competitor_availability_signals": len(comp_signals), "regional_demand_signals": len(regional), "regional_index_growth_pct": round(regional_growth, 1)},
                    evidence={"window": window_dict(self.recent_start, self.today), "competitor_signals": [{"id": str(c.id), "competitor": c.competitor_name, "date": c.signal_date.isoformat(), "availability_score": c.availability_score, "description": c.description, "is_synthetic": c.is_synthetic} for c in comp_signals], "market_signals": self._signal_summary(regional), "records": {"product_ids": ids(prods), "order_ids": [str(o) for o in list(a["recent_orders"])[:50]], "competitor_signal_ids": ids(comp_signals), "market_signal_ids": ids(regional)}, "trace": ["order", "order_item", "product", "customer(region)", "competitor_signal", "market_signal"], "rule": f"actual < {self.cfg.underserved_share_ratio} * expected and (competitor availability constraint or regional growth)"},
                    impact=self.impact.opportunity_value(gap_units * self.cfg.horizon_days / self.cfg.compare_days, margin, self.cfg.horizon_days).as_dict(),
                ))
        return out

    # ---- 3. high-margin product growth --------------------------------------------------------------
    def _high_margin_growth(self) -> list[Finding]:
        out: list[Finding] = []
        n = self.cfg.compare_days
        for pid, a in self._agg(lambda it: it["product_id"]).items():
            p = self.econ.products.get(pid)
            if not p or a["prev_units"] <= 0 or a["recent_rev"] <= 0:
                continue
            realised = (a["recent_rev"] - a["recent_cost"]) / a["recent_rev"]
            growth = pct_change(a["recent_units"], a["prev_units"])
            z = self._growth_z(growth, a["recent_lines"], a["prev_lines"])
            if realised < p.target_margin or growth < self.cfg.high_margin_growth_pct or z < self.cfg.growth_significance_z or len(a["recent_orders"]) < self.cfg.demand_min_orders or len(a["prev_orders"]) < self.cfg.demand_min_previous_orders:
                continue
            incremental = a["recent_units"] / n * growth * self.cfg.horizon_days
            out.append(self._finding(
                "HIGH_MARGIN_GROWTH", f"HIGH_MARGIN_GROWTH:{p.id}",
                f"{p.name} is growing {growth * 100:.0f}% at {realised:.0%} margin (target {p.target_margin:.0%})",
                f"{p.name} delivered a realised margin of {realised:.1%} on {a['recent_rev']:,.0f} INR over the last {n} days while unit demand grew {growth * 100:.0f}%; prioritising capacity and sales effort here compounds margin.",
                product=p, signal="high_margin_growth", confidence="HIGH" if growth >= 0.2 else "MEDIUM",
                metrics={"realised_margin": round(realised, 4), "target_margin": p.target_margin, "growth_rate": round(growth * 100, 1), "recent_units": a["recent_units"], "recent_revenue": round(a["recent_rev"], 2)},
                evidence={"window": window_dict(self.recent_start, self.today), "records": {"product_id": str(p.id), "order_ids_recent": [str(o) for o in list(a["recent_orders"])[:50]], "production_line_id": str(p.production_line_id)}, "trace": ["order_item", "order", "product"], "rule": f"realised >= target margin and growth >= {self.cfg.high_margin_growth_pct:.0%}"},
                impact=self.impact.opportunity_value(incremental, self.econ.product_margin(p), self.cfg.horizon_days).as_dict(),
            ))
        return out

    # ---- 4. customer segment / industry growth -------------------------------------------------------
    def _segment_growth(self) -> list[Finding]:
        out: list[Finding] = []
        for scope, key_fn in (("segment", lambda it: self.customers[it["customer_id"]].segment.value if it["customer_id"] in self.customers else None), ("industry", lambda it: self.customers[it["customer_id"]].industry if it["customer_id"] in self.customers else None)):
            for k, a in self._agg(key_fn).items():
                if a["prev_rev"] <= 0:
                    continue
                growth = pct_change(a["recent_rev"], a["prev_rev"])
                z = self._growth_z(growth, a["recent_lines"], a["prev_lines"])
                if growth < self.cfg.segment_growth_pct or z < self.cfg.growth_significance_z or len(a["recent_orders"]) < self.cfg.segment_min_orders or len(a["prev_orders"]) < self.cfg.segment_min_orders // 2:
                    continue
                margin_rate = (a["recent_rev"] - a["recent_cost"]) / a["recent_rev"] if a["recent_rev"] else 0.3
                cust_ids = sorted({str(it["customer_id"]) for it in self.items if it["recent"] and key_fn(it) == k})
                out.append(self._finding(
                    "SEGMENT_GROWTH", f"SEGMENT_GROWTH:{scope}:{k}",
                    f"{SEGMENT_LABELS.get(k, k) if scope == 'segment' else k} {scope} revenue up {growth * 100:.0f}% ({len(a['recent_orders'])} orders)",
                    f"Revenue from the {SEGMENT_LABELS.get(k, k) if scope == 'segment' else k} {scope} rose from {a['prev_rev']:,.0f} INR to {a['recent_rev']:,.0f} INR ({growth * 100:+.0f}%) over the last {self.cfg.compare_days} days across {len(cust_ids)} customers.",
                    segment=k if scope == "segment" else None, signal=f"{scope}_growth", confidence="HIGH" if z >= self.cfg.growth_high_significance_z and len(a["recent_orders"]) >= 10 else "MEDIUM",
                    metrics={"growth_rate": round(growth * 100, 1), "significance_z": round(z, 2), "recent_revenue": round(a["recent_rev"], 2), "previous_revenue": round(a["prev_rev"], 2), "customers": len(cust_ids), "recent_orders": len(a["recent_orders"]), "industry": k if scope == "industry" else None},
                    evidence={"window": window_dict(self.recent_start, self.today), "records": {"customer_ids": cust_ids[:50], "order_ids_recent": [str(o) for o in list(a["recent_orders"])[:50]]}, "trace": ["order", "customer", "order_item", "product"], "rule": f"revenue growth >= {self.cfg.segment_growth_pct:.0%}"},
                    impact=self.impact.opportunity_value((a["recent_rev"] - a["prev_rev"]) * (self.cfg.horizon_days / self.cfg.compare_days) / max(1.0, a["recent_rev"] / max(a["recent_units"], 1)), a["recent_rev"] / max(a["recent_units"], 1) * margin_rate, self.cfg.horizon_days).as_dict(),
                ))
        return out

    # ---- 5. excess inventory --------------------------------------------------------------------------
    def _excess_inventory(self) -> list[Finding]:
        out: list[Finding] = []
        for inv in self.db.scalars(select(Inventory)).all():
            daily = max(inv.avg_daily_consumption, 1e-9)
            cover = inv.quantity_available / daily
            if inv.quantity_available <= inv.max_stock and cover < self.cfg.excess_cover_days:
                continue
            excess = max(inv.quantity_available - inv.max_stock, inv.quantity_available - int(daily * 30))
            if excess <= 0:
                continue
            snaps = self.db.scalars(select(InventorySnapshot).where(InventorySnapshot.inventory_id == inv.id, InventorySnapshot.snapshot_date > self.today - timedelta(days=21)).order_by(InventorySnapshot.snapshot_date)).all()
            p = self.econ.products.get(inv.product_id) if inv.product_id else None
            out.append(self._finding(
                "EXCESS_INVENTORY", f"EXCESS_INVENTORY:{inv.id}",
                f"Excess stock of {inv.item_name}: {inv.quantity_available} units ({cover:.0f} days of cover)",
                f"{inv.item_name} holds {inv.quantity_available} units at {inv.warehouse}, {cover:.0f} days of cover against a maximum of {inv.max_stock}; roughly {excess} units could be redirected to growing regions/customers or promotions.",
                product=p, signal="excess_inventory", confidence="HIGH" if cover >= 60 else "MEDIUM",
                metrics={"quantity_available": inv.quantity_available, "max_stock": inv.max_stock, "days_of_cover": round(cover, 1), "excess_units": excess, "holding_cost_per_unit_month": float(inv.holding_cost_per_unit_month)},
                evidence={"window": window_dict(self.today - timedelta(days=21), self.today), "snapshots_21d": [{"id": str(s.id), "date": s.snapshot_date.isoformat(), "quantity": s.quantity_available} for s in snaps], "records": {"inventory_id": str(inv.id), "product_id": str(inv.product_id) if inv.product_id else None}, "trace": ["inventory", "inventory_snapshot", "product", "order"], "rule": f"quantity > max_stock or cover >= {self.cfg.excess_cover_days} days"},
                impact=self.impact.excess_inventory_value(excess, float(inv.unit_value), float(inv.holding_cost_per_unit_month)).as_dict(),
            ))
        return out

    # ---- 6. available capacity --------------------------------------------------------------------------
    def _available_capacity(self) -> list[Finding]:
        out: list[Finding] = []
        growth_by_product = {}
        for pid, a in self._agg(lambda it: it["product_id"]).items():
            if a["prev_units"] > 0:
                growth_by_product[pid] = pct_change(a["recent_units"], a["prev_units"])
        for line in self.econ.lines.values():
            recent, _ = production_windows(self.db, line.id, self.today, 14, 1)
            if not recent:
                continue
            util = mean(m.utilization for m in recent)
            if util >= self.cfg.capacity_utilization_ceiling:
                continue
            prods = self.econ.by_line.get(line.id, [])
            growing = [p for p in prods if growth_by_product.get(p.id, 0) >= 0.10]
            cat_signals = [s for s in self._market_signals(category=prods[0].category if prods else None, signal_types=[SignalType.MARKET_DEMAND, SignalType.CATEGORY_GROWTH], days=45) if s.growth_rate_pct > 0] if prods else []
            spare_per_day = line.capacity_units_per_day * (self.cfg.capacity_target_utilization - util)
            capture = spare_per_day * self.cfg.horizon_days * self.cfg.capacity_capture_ratio
            conf = "HIGH" if growing and cat_signals else "MEDIUM" if growing or cat_signals else "LOW"
            out.append(self._finding(
                "AVAILABLE_CAPACITY", f"AVAILABLE_CAPACITY:{line.id}",
                f"{line.name} has {spare_per_day:.0f} units/day of spare capacity ({util:.0%} utilised)",
                f"{line.name} averaged {util:.0%} utilisation over the last 14 days versus a {self.cfg.capacity_target_utilization:.0%} target, leaving ~{spare_per_day:.0f} units/day available. " + (f"Demand is growing for {', '.join(p.name for p in growing[:3])}." if growing else "No line product is currently growing >10%."),
                signal="spare_capacity", confidence=conf, line_id=line.id,
                metrics={"utilization_14d": round(util, 3), "target_utilization": self.cfg.capacity_target_utilization, "spare_units_per_day": round(spare_per_day), "growing_products": [p.sku for p in growing], "capture_ratio": self.cfg.capacity_capture_ratio},
                evidence={"window": window_dict(recent[0].metric_date, recent[-1].metric_date), "market_signals": self._signal_summary(cat_signals), "records": {"production_line_id": str(line.id), "production_metric_ids": ids(recent), "product_ids": ids(growing), "market_signal_ids": ids(cat_signals)}, "trace": ["production_metric", "production_line", "product", "order", "market_signal"], "rule": f"utilization < {self.cfg.capacity_utilization_ceiling:.0%}"},
                impact=self.impact.opportunity_value(capture, self.econ.unit_margin(line.id), self.cfg.horizon_days).as_dict(),
            ))
        return out

    # ---- 7. pricing opportunity ----------------------------------------------------------------------------
    def _pricing(self) -> list[Finding]:
        out: list[Finding] = []
        comp = self.db.scalars(select(CompetitorSignal).where(CompetitorSignal.signal_date > self.today - timedelta(days=self.cfg.pricing_signal_days), CompetitorSignal.signal_type == CompetitorSignalType.PRICE_CHANGE)).all()
        by_cat: dict[str, list[CompetitorSignal]] = defaultdict(list)
        for c in comp:
            if c.product_category and (c.price_change_pct or 0) > 0:
                by_cat[c.product_category].append(c)
        agg = self._agg(lambda it: it["product_id"])
        for category, sigs in by_cat.items():
            total_increase = sum(c.price_change_pct or 0 for c in sigs)
            if total_increase < self.cfg.pricing_competitor_min_increase_pct:
                continue
            prods = [p for p in self.econ.products.values() if p.category == category]
            cat_units = sum(agg[p.id]["recent_units"] for p in prods if p.id in agg)
            cat_prev = sum(agg[p.id]["prev_units"] for p in prods if p.id in agg)
            demand_growth = pct_change(cat_units, cat_prev) if cat_prev else 0.0
            if demand_growth < -0.15:
                continue  # do not reprice into a falling market
            cat_rev = sum(agg[p.id]["recent_rev"] for p in prods if p.id in agg)
            cat_cost = sum(agg[p.id]["recent_cost"] for p in prods if p.id in agg)
            realised = (cat_rev - cat_cost) / cat_rev if cat_rev else 0.0
            target = mean(p.target_margin for p in prods) if prods else 0.33
            monthly_units = cat_units * self.cfg.horizon_days / self.cfg.compare_days
            avg_price = cat_rev / cat_units if cat_units else mean(float(p.selling_price) for p in prods)
            avg_cost = cat_cost / cat_units if cat_units else mean(float(p.unit_cost) for p in prods)
            impact = self.impact.pricing_opportunity_value(monthly_units, avg_price, avg_cost, max(target, realised + total_increase / 100 * 0.5))
            conf = "HIGH" if len(sigs) >= 2 and demand_growth >= 0 else "MEDIUM"
            out.append(self._finding(
                "PRICING_OPPORTUNITY", f"PRICING_OPPORTUNITY:{category}",
                f"Pricing headroom in {category}: competitors raised prices {total_increase:.1f}% while Astra realises {realised:.0%} margin",
                f"{len(sigs)} synthetic competitor price increase(s) totalling {total_increase:.1f}% were recorded for {category} in the last {self.cfg.pricing_signal_days} days; Astra's realised margin is {realised:.1%} versus a {target:.0%} target and demand is {demand_growth * 100:+.0f}%. A measured list-price increase is unlikely to lose share.",
                signal="competitor_price_increase", confidence=conf, line_id=prods[0].production_line_id if prods else None,
                metrics={"competitor_price_increase_pct": round(total_increase, 2), "competitor_signals": len(sigs), "realised_margin": round(realised, 4), "target_margin": round(target, 4), "demand_growth_pct": round(demand_growth * 100, 1), "monthly_units": round(monthly_units)},
                evidence={"window": window_dict(self.today - timedelta(days=self.cfg.pricing_signal_days), self.today), "competitor_signals": [{"id": str(c.id), "competitor": c.competitor_name, "date": c.signal_date.isoformat(), "price_change_pct": c.price_change_pct, "region": c.region, "description": c.description, "is_synthetic": c.is_synthetic} for c in sigs], "records": {"competitor_signal_ids": ids(sigs), "product_ids": ids(prods), "order_ids_recent": [str(o) for p in prods if p.id in agg for o in list(agg[p.id]["recent_orders"])[:10]]}, "trace": ["competitor_signal", "product", "order_item", "order"], "rule": f"competitor increases >= {self.cfg.pricing_competitor_min_increase_pct}% and demand not falling"},
                impact=impact.as_dict(),
            ))
        return out


class OpportunityEngine:
    def __init__(self, db: Session, config: OpportunityConfig | None = None, impact: ImpactEngine | None = None) -> None:
        self.db = db
        self.cfg = config or OpportunityConfig()
        self.impact = impact or ImpactEngine()

    def run(self) -> DetectionRun:
        state = self.db.get(SystemState, 1)
        if state is None:
            raise ValueError("Database is not seeded. Call POST /simulation/reset first.")
        now = state.data_end
        run = DetectionRun(engine="opportunity", status=RunStatus.RUNNING, window_end=now)
        self.db.add(run)
        self.db.flush()
        log_event(log, "opportunity_detection.started", run_id=run.id, window_end=now)
        try:
            from app.domains import MERCHANT, current_domain

            if current_domain(self.db) == MERCHANT:
                from app.domains.merchant.detection import MerchantDetector

                findings = MerchantDetector(self.db, impact=self.impact).opportunities()
            else:
                findings = OpportunityDetector(self.db, self.cfg, self.impact).run(now)
            existing = {o.dedupe_key: o for o in self.db.scalars(select(Opportunity).where(Opportunity.status != OpportunityStatus.DISMISSED)).all()}
            created = updated = 0
            for f in findings:
                cur = existing.get(f.dedupe_key)
                if cur:
                    cur.title, cur.description, cur.confidence = f.title, f.description, Confidence(f.confidence or "MEDIUM")
                    cur.supporting_metrics, cur.evidence, cur.estimated_impact = f.affected_metrics, f.evidence, f.impact
                    cur.occurrence_count += 1
                    cur.detection_run_id = run.id
                    updated += 1
                    log_event(log, "opportunity.updated", opportunity_id=cur.id, type=f.type)
                    continue
                opp = Opportunity(
                    opportunity_type=OpportunityType(f.type), source=DetectionSource.OPPORTUNITY_ENGINE, product_id=f.product_id, production_line_id=f.production_line_id, customer_segment=f.customer_segment, region=f.region,
                    detected_at=now, title=f.title, description=f.description, signal=f.signal or "", supporting_metrics=f.affected_metrics, estimated_impact=f.impact, evidence=f.evidence,
                    confidence=Confidence(f.confidence or "MEDIUM"), status=OpportunityStatus.NEW, detection_run_id=run.id, dedupe_key=f.dedupe_key,
                )
                self.db.add(opp)
                self.db.flush()
                created += 1
                log_event(log, "opportunity.created", opportunity_id=opp.id, type=f.type, confidence=f.confidence, product_id=f.product_id, region=f.region, title=f.title, estimated_impact=f.impact.get("value"))
            run.opportunities_created, run.opportunities_updated = created, updated
            run.summary = {"opportunities": {"created": created, "updated": updated, "findings": [{"type": f.type, "confidence": f.confidence, "title": f.title} for f in findings]}}
            run.status = RunStatus.COMPLETED
            run.finished_at = utcnow()
            state.last_detection_id = run.id
            state.last_detection_at = run.finished_at
            self.db.commit()
            log_event(log, "opportunity_detection.completed", run_id=run.id, created=created, updated=updated)
            return run
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            run = self.db.merge(DetectionRun(id=run.id, engine="opportunity", status=RunStatus.FAILED, error=str(exc), finished_at=utcnow()))
            self.db.commit()
            log_event(log, "opportunity_detection.failed", level=40, run_id=run.id, error=str(exc))
            raise
