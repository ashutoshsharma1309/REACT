"""Merchant detection: 15 deterministic rules over sales, stock, purchasing, pricing and customers.

    risks          STOCKOUT_RISK, DEMAND_DECLINE, SUPPLIER_DELAY, SUPPLIER_RELIABILITY, MARGIN_DECLINE, PRICE_ANOMALY,
                   DISCOUNT_LEAKAGE, EXCESS_INVENTORY, SLOW_MOVING_INVENTORY, REVENUE_DECLINE, REPEAT_CUSTOMER_DECLINE,
                   DEMAND_SUPPLY_MISMATCH
    opportunities  DEMAND_OPPORTUNITY (product demand spike), CATEGORY_OPPORTUNITY, REGIONAL_OPPORTUNITY

Rules return `Finding` objects; the existing detection / opportunity engines persist them (dedupe key, occurrence count,
auto-resolution of risks when a condition clears; opportunities stay open until handled, detection run, simulated timestamps). Every finding carries the rule that
fired, its windows, the measured values and the record ids behind them. Money comes only from the Impact Engine.

Velocity is measured on in-stock days, so a stockout is never mistaken for falling demand.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.detection.base import Finding, ids
from app.domains.merchant import baseline as B
from app.domains.merchant import queries as Q
from app.domains.merchant.language import plural
from app.impact import ImpactEngine
from app.models import Supplier


@dataclass(frozen=True)
class MerchantDetectionConfig:
    stockout_min_velocity: float = 0.4  # units/day; slower sellers are covered by the slow-moving rules
    stockout_min_gap_days: float = 0.5
    stockout_min_impact: float = 1_000.0
    min_in_stock_days_recent: int = 10  # of 14: enough selling days to judge a demand change
    min_in_stock_days_normal: int = 40  # of 56
    spike_min_growth: float = 0.40
    spike_min_units: int = 12
    spike_max_p: float = 0.001  # exact Poisson tail; 300 SKUs x 0.001 keeps chance alarms rare
    decline_max_p: float = 0.0005
    mismatch_max_p: float = 0.01
    opp_max_p: float = 0.0001  # ~50 category x zone tests per scan
    decline_max_growth: float = -0.40
    decline_min_prior_units: int = 25
    category_decline_max_growth: float = -0.25
    reliability_late_of_last: tuple[int, int] = (4, 6)
    overdue_min_days: int = 2
    margin_min_drop_ratio: float = 0.20
    margin_min_drop_inr: float = 15.0
    margin_min_units: int = 15
    margin_min_revenue: float = 10_000.0
    discount_min_rate: float = 0.08
    discount_min_extra: float = 0.06
    excess_min_cover_days: float = 90.0
    excess_min_value: float = 20_000.0
    excess_keep_days: int = 45
    slow_min_age_days: int = 75
    slow_max_units_30d: int = 2
    slow_min_value: float = 8_000.0
    revenue_max_change: float = -0.20
    revenue_max_z: float = -2.5
    repeat_min_rel_drop: float = 0.25
    repeat_min_abs_drop: float = 0.08
    mismatch_min_growth: float = 0.25
    category_min_growth: float = 0.25
    category_min_units: int = 60
    regional_min_growth: float = 0.30
    regional_min_units: int = 50
    supplier_delay_min_impact: float = 1_000.0
    supplier_lookback_days: int = 75  # deliveries older than this do not count towards reliability
    supplier_dependent_min_velocity: float = 0.15  # units/day for a product to count as depending on the supplier
    mismatch_min_units: int = 10  # units sold in 14 days
    category_decline_min_prior_units: int = 60
    price_anomaly_min_target_margin: float = 0.15  # only categories that normally earn at least this
    price_anomaly_max_list_margin: float = 0.05
    discount_min_units: int = 10
    discount_min_list_revenue: float = 10_000.0
    repeat_min_orders: int = 150  # orders in 28 days before the repeat share is judged


def _sev(order: list[tuple[bool, str]], default: str = "LOW") -> str:
    for cond, sev in order:
        if cond:
            return sev
    return default


def _confidence(p_value: float) -> str:
    """Detection signal strength from the exact Poisson tail probability (how unusual the change is)."""
    return "HIGH" if p_value < 1e-6 else "MEDIUM" if p_value < 1e-4 else "LOW"


def _inr(x: float) -> str:
    return f"₹{x:,.0f}"


class MerchantDetector:
    def __init__(self, db: Session, config: MerchantDetectionConfig | None = None, impact: ImpactEngine | None = None) -> None:
        self.db = db
        self.cfg = config or MerchantDetectionConfig()
        self.E = impact or ImpactEngine()
        self.t = Q.today(db)
        t = self.t
        self.w14, self.w56 = Q.window(t, 14), Q.window(t, 56, end_offset=14)
        self.w21, self.wb21 = Q.window(t, 21), Q.window(t, 56, end_offset=21)
        self.w28, self.wp28 = Q.window(t, 28), Q.window(t, 28, end_offset=28)
        self.w30 = Q.window(t, 30)
        self.positions = Q.stock_positions(db)
        inv_ids = [p.inventory.id for p in self.positions]
        self.ins14, self.ins56 = Q.in_stock_days(db, inv_ids, *self.w14), Q.in_stock_days(db, inv_ids, *self.w56)
        self.s14, self.s56 = Q.sales_by_product(db, *self.w14), Q.sales_by_product(db, *self.w56)
        self.s21, self.sb21 = Q.sales_by_product(db, *self.w21), Q.sales_by_product(db, *self.wb21)
        self.s28, self.s30 = Q.sales_by_product(db, *self.w28), Q.sales_by_product(db, *self.w30)
        self._supplier_cache: dict[Any, dict[str, Any]] = {}
        from app.domains.merchant.catalog import get_template
        from app.models import SystemState

        state = db.get(SystemState, 1)
        self.tpl = get_template(((state.profile or {}).get("template")) if state else None)
        self.stockout_products: set = set()

    # ---- shared measures ------------------------------------------------------------------------------------------
    def velocity(self, pos: Q.StockPosition, window: str = "14") -> float:
        if window == "14":
            units = self.s14.get(pos.product.id, Q.SalesStats()).units
            return units / max(7, self.ins14.get(pos.inventory.id, 0)) if units else 0.0
        units = self.s56.get(pos.product.id, Q.SalesStats()).units
        return units / max(28, self.ins56.get(pos.inventory.id, 0)) if units else 0.0

    def line_rate_expectation(self, pos: Q.StockPosition) -> tuple[int, float]:
        """Order lines in the last 14 days and the number expected at the normal in-stock rate.
        Lines (not units) are tested: one small business buying 4 cables is one buying decision."""
        l14 = self.s14.get(pos.product.id, Q.SalesStats()).lines
        l56 = self.s56.get(pos.product.id, Q.SalesStats()).lines
        expected = l56 / max(28, self.ins56.get(pos.inventory.id, 0)) * max(7, self.ins14.get(pos.inventory.id, 0))
        return l14, expected

    def price_margin(self, pid) -> tuple[float, float]:
        st = self.s28.get(pid) or self.s56.get(pid)
        if st and st.units:
            return st.unit_price or 0.0, st.unit_margin or 0.0
        return 0.0, 0.0

    def supplier_info(self, sup: Supplier | None) -> dict[str, Any]:
        if sup is None:
            return {"quoted": 7, "observed": None, "avg_delay": 1.0, "late_delay": 1.0, "late_share": 0.0, "lead_eff": 7.0, "next_order_days": 3, "deliveries": []}
        if sup.id not in self._supplier_cache:
            dels = Q.deliveries(self.db, sup.id, limit=6)
            recent = [d for d in dels[:4]]
            observed = sum(d["lead_days"] for d in recent) / len(recent) if recent else None
            avg_delay = sum(d["delay_days"] for d in recent) / len(recent) if recent else 0.0
            late = [d for d in dels if not d["on_time"]]
            late_delay = sum(d["delay_days"] for d in late) / len(late) if late else 1.0
            late_share = len(late) / len(dels) if dels else 0.0
            spec = next((s for s in self.tpl.suppliers if s.name == sup.name), None)
            weekdays = spec.order_weekdays if spec else (0, 3)
            next_order = min(((wd - self.t.weekday()) % 7 for wd in weekdays))
            quoted = sup.baseline_lead_time_days
            self._supplier_cache[sup.id] = {"quoted": quoted, "observed": observed, "avg_delay": avg_delay, "lead_eff": max(float(quoted), observed or float(quoted)), "next_order_days": next_order, "deliveries": dels, "late_delay": late_delay, "late_share": late_share}
        return self._supplier_cache[sup.id]

    def arrival_days(self, pos: Q.StockPosition) -> tuple[float, str]:
        info = self.supplier_info(pos.supplier)
        expected_delay = info["late_share"] * info["late_delay"]  # what lateness to expect from this supplier
        best = None
        for po in pos.open_pos:
            days_to_expected = (po["expected_date"] - self.t).days
            if days_to_expected >= 0:
                eta = days_to_expected + expected_delay
            else:  # already overdue: assume it is as late as this supplier's late deliveries usually are
                eta = max(1.0, info["late_delay"] + days_to_expected, 0.5 * info["late_delay"])
            if best is None or eta < best[0]:
                best = (eta, f"open purchase order {po['po_number']}, expected {po['expected_date'].isoformat()}" + (" and now overdue" if days_to_expected < 0 else ""))
        if best:
            return best
        return info["next_order_days"] + info["lead_eff"], f"no open purchase order; next order day in {plural(info['next_order_days'], 'day')} plus {info['lead_eff']:.0f} days lead time"

    # ---- risks ------------------------------------------------------------------------------------------------------
    def risks(self) -> list[Finding]:
        out: list[Finding] = []
        out += self._stockout()
        out += self._mismatch()
        out += self._demand_decline()
        out += self._suppliers()
        margin = self._margin_decline()
        out += margin
        flagged = {f.product_id for f in margin}
        out += self._price_anomaly(flagged)
        out += self._discount_leakage(flagged)
        out += self._excess_and_slow()
        out += self._revenue_decline()
        out += self._repeat_decline()
        return out

    def opportunities(self) -> list[Finding]:
        if not self.stockout_products:
            self._stockout()
        return self._demand_spike() + self._category_opportunity() + self._regional_opportunity()

    def _base(self, type_: str, key: str, title: str, desc: str, *, entity_type: str, entity_id=None, entity_name=None, product=None, pos: Q.StockPosition | None = None, severity: str = "MEDIUM", metrics=None, evidence=None, impact=None, region=None, segment=None, signal=None, confidence=None, kind="risk") -> Finding:
        ev = dict(evidence or {})
        if pos is not None:
            ev.setdefault("inventory_id", str(pos.inventory.id))
            ev.setdefault("store_id", str(pos.store.id) if pos.store else None)
            ev.setdefault("store_name", pos.store.name if pos.store else None)
            ev.setdefault("supplier_id", str(pos.supplier.id) if pos.supplier else None)
        if product is not None:
            ev.setdefault("product_id", str(product.id))
            ev.setdefault("category", product.category)
        ev.setdefault("domain", "merchant")
        return Finding(kind=kind, type=type_, severity=severity, title=title, description=desc, dedupe_key=key, entity_type=entity_type, entity_id=entity_id, entity_name=entity_name, product_id=product.id if product is not None else None, region=region, customer_segment=segment, signal=signal, confidence=confidence, affected_metrics=metrics or {}, evidence=ev, impact=impact or {})

    def demand_rate(self, pos: Q.StockPosition) -> tuple[float, str]:
        """Units per day the shelf would sell: the recent in-stock rate, or the normal rate when the item has been
        out of stock for most of the last two weeks (an empty shelf sells nothing, but customers still ask)."""
        if self.ins14.get(pos.inventory.id, 0) >= 7:
            return self.velocity(pos), "last 14 days (in-stock days)"
        return self.velocity(pos, "56"), "normal 8-week rate (mostly out of stock recently)"

    def _stockout(self) -> list[Finding]:
        out = []
        self.stockout_products = set()
        for pos in self.positions:
            v, v_basis = self.demand_rate(pos)
            if v < self.cfg.stockout_min_velocity:
                continue
            cover = pos.on_hand / v
            arrival, basis = self.arrival_days(pos)
            gap = arrival - cover
            if not (gap >= self.cfg.stockout_min_gap_days or cover < 1.0):
                continue
            price, margin = self.price_margin(pos.product.id)
            impact = self.E.stockout_loss(v, max(gap, 0.0), price, margin)
            if impact.value < self.cfg.stockout_min_impact:
                continue  # too small to need the merchant's attention
            info = self.supplier_info(pos.supplier)
            lost = impact.value
            sev = _sev([(lost >= 25000 or (cover <= 1 and lost >= 5000), "CRITICAL"), (lost >= 8000, "HIGH"), (lost >= 3000, "MEDIUM")], "LOW")
            self.stockout_products.add(pos.product.id)
            snaps = Q.stock_series(self.db, pos.inventory.id, *self.w14)
            p = pos.product
            sup_name = pos.supplier.name if pos.supplier else "the supplier"
            out.append(self._base(
                "STOCKOUT_RISK", f"STOCKOUT_RISK:{pos.inventory.id}", (f"{p.name} is out of stock" if pos.on_hand == 0 else f"{p.name} may run out within a day" if cover < 1 else f"{p.name} may run out in {cover:.0f} days"),
                (f"Out of stock and normally selling about {v:.1f} a day. " if pos.on_hand == 0 else f"{plural(pos.on_hand, 'unit')} left and selling about {v:.1f} a day. ") + f"The next stock from {sup_name} is about {plural(round(arrival), 'day')} away ({basis}), so the shelf could be empty for {max(gap, 0):.1f} days.",
                entity_type="product", entity_id=p.id, entity_name=p.name, product=p, pos=pos, severity=sev,
                metrics={"units_on_hand": pos.on_hand, "units_per_day": round(v, 2), "demand_basis": v_basis, "days_of_cover": round(cover, 1), "days_until_next_stock": round(arrival, 1), "days_without_stock": round(max(gap, 0), 1), "quoted_lead_days": info["quoted"], "observed_lead_days": round(info["observed"], 1) if info["observed"] else None, "open_purchase_orders": len(pos.open_pos), "units_on_order": pos.on_order},
                evidence={"window": {"start": self.w14[0].isoformat(), "end": self.w14[1].isoformat()}, "rule": "days of cover (stock / units per in-stock day, last 14 days) below the days until the next credible delivery", "next_stock_basis": basis,
                          "records": {"inventory_ids": [str(pos.inventory.id)], "inventory_snapshot_ids": ids(snaps, 14), "order_item_ids": Q.order_item_ids(self.db, *self.w14, product_id=p.id, limit=40), "purchase_order_ids": [po["purchase_order_id"] for po in pos.open_pos], "supplier_delivery_ids": [d["delivery_id"] for d in info["deliveries"][:4]], "product_ids": [str(p.id)]},
                          "trace": ["order_item", "inventory_snapshot", "inventory", "purchase_order", "supplier_delivery"]},
                impact=impact.as_dict(),
            ))
        return out

    def _mismatch(self) -> list[Finding]:
        out = []
        for pos in self.positions:
            if pos.product.id in self.stockout_products:
                continue
            v14, v56 = self.velocity(pos), self.velocity(pos, "56")
            u14 = self.s14.get(pos.product.id, Q.SalesStats()).units
            if u14 < self.cfg.mismatch_min_units or v56 <= 0 or (v14 - v56) / v56 < self.cfg.mismatch_min_growth:
                continue
            z = Q.z_rates(u14, 14, self.s56.get(pos.product.id, Q.SalesStats()).units, 56)
            l14, expected = self.line_rate_expectation(pos)
            p_value = Q.poisson_upper(l14, expected)
            if p_value > self.cfg.mismatch_max_p:
                continue
            info = self.supplier_info(pos.supplier)
            need_days = info["lead_eff"] + 7
            available = pos.on_hand + pos.on_order
            if available / v14 >= need_days:
                continue
            price, margin = self.price_margin(pos.product.id)
            required = v14 * need_days
            impact = self.E.inventory_risk(required, available, price)
            if impact.value < self.cfg.stockout_min_impact:
                continue
            p = pos.product
            out.append(self._base(
                "DEMAND_SUPPLY_MISMATCH", f"DEMAND_SUPPLY_MISMATCH:{pos.inventory.id}", f"Stock for {p.name} is not keeping up with demand",
                f"Demand is up {(v14 - v56) / v56 * 100:.0f}% ({v56:.1f} to {v14:.1f} units a day), but stock plus incoming orders ({available} units) covers only {available / v14:.1f} days against {need_days:.0f} days needed.",
                entity_type="product", entity_id=p.id, entity_name=p.name, product=p, pos=pos, severity=_sev([(impact.value >= 15000, "HIGH")], "MEDIUM"),
                metrics={"units_per_day_recent": round(v14, 2), "units_per_day_normal": round(v56, 2), "growth_pct": round((v14 - v56) / v56 * 100, 1), "z": round(z, 2), "p_value": p_value, "units_available_incl_on_order": available, "days_needed": round(need_days, 1)},
                evidence={"window": {"recent": [d.isoformat() for d in self.w14], "normal": [d.isoformat() for d in self.w56]}, "rule": "in-stock demand up >= 25% (Poisson p <= 0.01) and stock + open orders below lead time + 7 days of the new demand", "records": {"inventory_ids": [str(pos.inventory.id)], "order_item_ids": Q.order_item_ids(self.db, *self.w14, product_id=p.id, limit=30), "purchase_order_ids": [po["purchase_order_id"] for po in pos.open_pos], "product_ids": [str(p.id)]}, "trace": ["order_item", "inventory", "purchase_order"]},
                impact=impact.as_dict(),
            ))
        return out

    def _demand_decline(self) -> list[Finding]:
        out = []
        seen = set()
        for pos in self.positions:
            p = pos.product
            u56 = self.s56.get(p.id, Q.SalesStats()).units
            if u56 < self.cfg.decline_min_prior_units or p.id in seen:
                continue
            if self.ins14.get(pos.inventory.id, 0) < self.cfg.min_in_stock_days_recent or self.ins56.get(pos.inventory.id, 0) < self.cfg.min_in_stock_days_normal:
                continue  # mostly out of stock: a stockout, not a demand change
            v14, v56 = self.velocity(pos), self.velocity(pos, "56")
            if v56 <= 0 or (v14 - v56) / v56 > self.cfg.decline_max_growth:
                continue
            u14 = self.s14.get(p.id, Q.SalesStats()).units
            z = Q.z_rates(u14, 14, u56, 56)
            l14, expected = self.line_rate_expectation(pos)
            p_value = Q.poisson_lower(l14, expected)
            if p_value > self.cfg.decline_max_p:
                continue
            seen.add(p.id)
            price, _ = self.price_margin(p.id)
            impact = self.E.revenue_impact(v56 * 30 * price, v14 * 30 * price)
            out.append(self._base(
                "DEMAND_DECLINE", f"DEMAND_DECLINE:product:{p.id}", f"Sales of {p.name} have dropped {abs(v14 - v56) / v56 * 100:.0f}%",
                f"Selling {v14:.1f} a day on days it was in stock, against a normal {v56:.1f} over the previous 8 weeks.",
                entity_type="product", entity_id=p.id, entity_name=p.name, product=p, pos=pos, severity=_sev([(impact.value >= 30000, "HIGH")], "MEDIUM"),
                metrics={"units_per_day_recent": round(v14, 2), "units_per_day_normal": round(v56, 2), "change_pct": round((v14 - v56) / v56 * 100, 1), "z": round(z, 2), "p_value": p_value},
                evidence={"window": {"recent": [d.isoformat() for d in self.w14], "normal": [d.isoformat() for d in self.w56]}, "rule": "in-stock demand down >= 40% (Poisson p <= 0.0005, >= 10 of 14 days in stock)", "records": {"order_item_ids": Q.order_item_ids(self.db, *self.w56, product_id=p.id, limit=30), "inventory_ids": [str(pos.inventory.id)], "product_ids": [str(p.id)]}, "trace": ["order_item", "inventory_snapshot"]},
                impact=impact.as_dict(),
            ))
        rc, pc = Q.sales_by_category(self.db, *self.w28), Q.sales_by_category(self.db, *self.wp28)
        for cat_name, prev in pc.items():
            cur = rc.get(cat_name, Q.SalesStats())
            if prev.units < self.cfg.category_decline_min_prior_units:
                continue
            g = (cur.units - prev.units) / prev.units
            z = Q.z_rates(cur.units, 28, prev.units, 28)
            if g > self.cfg.category_decline_max_growth or Q.poisson_lower(cur.lines, prev.lines) > self.cfg.decline_max_p:
                continue
            impact = self.E.revenue_impact(prev.revenue * 30 / 28, cur.revenue * 30 / 28)
            out.append(self._base(
                "DEMAND_DECLINE", f"DEMAND_DECLINE:category:{cat_name}", f"{cat_name} sales are down {abs(g) * 100:.0f}%",
                f"{cur.units} units in the last 28 days against {prev.units} in the 28 days before.", entity_type="category", entity_name=cat_name, severity=_sev([(impact.value >= 50000, "HIGH")], "MEDIUM"),
                metrics={"units_recent": cur.units, "units_previous": prev.units, "change_pct": round(g * 100, 1), "z": round(z, 2)},
                evidence={"category": cat_name, "window": {"recent": [d.isoformat() for d in self.w28], "previous": [d.isoformat() for d in self.wp28]}, "rule": "category units down >= 25% (Poisson p <= 0.0005)", "records": {"order_item_ids": Q.order_item_ids(self.db, *self.wp28, category=cat_name, limit=40)}, "trace": ["order_item", "product"]},
                impact=impact.as_dict(),
            ))
        return out

    def _suppliers(self) -> list[Finding]:
        out = []
        late_need, last_n = self.cfg.reliability_late_of_last
        by_supplier: dict[Any, list[Q.StockPosition]] = {}
        for pos in self.positions:
            if pos.supplier:
                by_supplier.setdefault(pos.supplier.id, []).append(pos)
        for sup in self.db.query(Supplier).order_by(Supplier.name).all():
            info = self.supplier_info(sup)
            recent = [d for d in info["deliveries"][:last_n] if d["delivered_date"] >= self.t - timedelta(days=self.cfg.supplier_lookback_days)]
            late = [d for d in recent if not d["on_time"]]
            overdue = [po for po in Q.open_purchase_orders(self.db, sup.id) if (self.t - po.expected_date).days >= self.cfg.overdue_min_days]
            unreliable = len(recent) >= last_n - 1 and len(late) >= late_need
            if not unreliable and not overdue:
                continue
            # what the delays put at risk: dependent products that would run out before stock can arrive
            items, dependent = [], []
            for pos in by_supplier.get(sup.id, []):
                v = self.velocity(pos)
                if v < self.cfg.supplier_dependent_min_velocity:
                    continue
                arrival, _ = self.arrival_days(pos)
                cover = pos.on_hand / v
                price, margin = self.price_margin(pos.product.id)
                gap = max(0.0, arrival - cover)
                dependent.append({"product_id": str(pos.product.id), "product": pos.product.name, "days_of_cover": round(cover, 1), "units_per_day": round(v, 2)})
                if gap > 0:
                    items.append({"product": pos.product.name, "units_per_day": v, "gap_days": gap, "unit_price": price, "unit_margin": margin})
            impact = self.E.supplier_delay_exposure(items)
            if not unreliable and impact.value < self.cfg.supplier_delay_min_impact and len(overdue) < 2:
                continue  # one slightly late order with nothing at risk is not worth the merchant's attention
            avg_delay = sum(d["delay_days"] for d in late) / len(late) if late else 0.0
            type_ = "SUPPLIER_RELIABILITY" if unreliable else "SUPPLIER_DELAY"
            if unreliable:
                title = f"{sup.name} delivered {len(late)} of the last {len(recent)} orders late"
                desc = f"Late deliveries averaged {avg_delay:.1f} days. " + (f"{plural(len(overdue), 'purchase order')} {'is' if len(overdue) == 1 else 'are'} overdue now. " if overdue else "") + f"{len(dependent)} products you sell depend on this supplier."
            else:
                worst = max((self.t - po.expected_date).days for po in overdue)
                title = f"{plural(len(overdue), 'order')} from {sup.name} {'is' if len(overdue) == 1 else 'are'} overdue"
                desc = f"The oldest is {worst} days past its expected date. {len(dependent)} products you sell depend on this supplier."
            sev = _sev([(impact.value >= 25000 or (unreliable and overdue) or len(late) >= last_n - 1, "HIGH"), (impact.value >= 5000 or unreliable, "MEDIUM")], "LOW")
            out.append(self._base(
                type_, f"{type_}:{sup.id}", title, desc, entity_type="supplier", entity_id=sup.id, entity_name=sup.name, severity=sev,
                metrics={"late_deliveries": len(late), "deliveries_considered": len(recent), "average_delay_days": round(avg_delay, 1), "overdue_purchase_orders": len(overdue), "quoted_lead_days": info["quoted"], "observed_lead_days": round(info["observed"], 1) if info["observed"] else None, "dependent_products": len(dependent), "products_at_risk": len(items)},
                evidence={"supplier_id": str(sup.id), "dependent_products": dependent[:20], "window": {"deliveries_since": (self.t - timedelta(days=self.cfg.supplier_lookback_days)).isoformat()}, "rule": f">= {late_need} late of the last {last_n} deliveries" + (" or purchase orders overdue by >= 2 days" if overdue else ""),
                          "records": {"supplier_delivery_ids": [d["delivery_id"] for d in recent], "purchase_order_ids": [str(po.id) for po in overdue], "product_ids": [d["product_id"] for d in dependent[:30]]}, "trace": ["supplier_delivery", "purchase_order", "product", "inventory"]},
                impact=impact.as_dict(),
            ))
        return out

    def _margin_decline(self) -> list[Finding]:
        out = []
        products = {pos.product.id: pos for pos in self.positions}
        for pid, cur in self.s21.items():
            base = self.sb21.get(pid)
            pos = products.get(pid)
            if pos is None or base is None or cur.units < self.cfg.margin_min_units or base.units < self.cfg.margin_min_units or cur.revenue < self.cfg.margin_min_revenue:
                continue
            um_b, um_r = base.unit_margin or 0.0, cur.unit_margin or 0.0
            drop = um_b - um_r
            if um_b <= 0 or drop < self.cfg.margin_min_drop_inr or drop / um_b < self.cfg.margin_min_drop_ratio:
                continue
            v_r, v_b = cur.units / 21, base.units / 56
            impact = self.E.margin_loss(v_r, um_b, um_r)
            p = pos.product
            sales_change = (v_r - v_b) / v_b * 100 if v_b else 0.0
            out.append(self._base(
                "MARGIN_DECLINE", f"MARGIN_DECLINE:product:{pid}", f"{p.name} earns less on every sale: {_inr(um_b)} down to {_inr(um_r)}",
                f"Margin per unit fell {drop / um_b * 100:.0f}% while sales {'rose' if sales_change >= 0 else 'changed'} {sales_change:+.0f}% ({v_b:.1f} to {v_r:.1f} a day).",
                entity_type="product", entity_id=p.id, entity_name=p.name, product=p, pos=pos, severity=_sev([(impact.value >= 10000, "HIGH"), (impact.value >= 3000, "MEDIUM")], "LOW"),
                metrics={"unit_margin_normal": round(um_b, 2), "unit_margin_recent": round(um_r, 2), "margin_drop_pct": round(drop / um_b * 100, 1), "units_per_day_recent": round(v_r, 2), "units_per_day_normal": round(v_b, 2), "sales_change_pct": round(sales_change, 1), "unit_cost_recent": round(cur.unit_cost or 0, 2), "unit_cost_normal": round(base.unit_cost or 0, 2), "unit_price_recent": round(cur.unit_price or 0, 2), "unit_price_normal": round(base.unit_price or 0, 2), "discount_rate_recent": round(cur.discount_rate or 0, 4), "discount_rate_normal": round(base.discount_rate or 0, 4)},
                evidence={"window": {"recent": [d.isoformat() for d in self.w21], "normal": [d.isoformat() for d in self.wb21]}, "rule": "margin per unit down >= 20% and >= ₹15 (>= 15 units in each window, >= ₹10,000 recent revenue)", "records": {"order_item_ids": Q.order_item_ids(self.db, *self.w21, product_id=pid, limit=40), "product_ids": [str(pid)]}, "trace": ["order_item", "pricing_history", "product"]},
                impact=impact.as_dict(),
            ))
        return out

    def _price_anomaly(self, skip: set) -> list[Finding]:
        out = []
        cats = Q.categories(self.db)
        for pos in self.positions:
            p = pos.product
            if p.id in skip:
                continue
            cur = self.s14.get(p.id)
            price, cost = float(p.selling_price), float(p.unit_cost)
            target = cats.get(p.category).target_margin if cats.get(p.category) else 0.2
            list_margin = (price - cost) / price if price else 0.0
            below_cost = cur is not None and cur.units >= 3 and (cur.unit_price or 0) < (cur.unit_cost or 0)
            mispriced = target >= self.cfg.price_anomaly_min_target_margin and list_margin < self.cfg.price_anomaly_max_list_margin
            if not (below_cost or mispriced):
                continue
            units_day = (cur.units / 14) if cur else 0.0
            impact = self.E.margin_loss(max(units_day, 0.1), price * target, (cur.unit_margin if cur and cur.units else price - cost))
            out.append(self._base(
                "PRICE_ANOMALY", f"PRICE_ANOMALY:{p.id}", f"{p.name} is priced {'below its cost' if below_cost else 'almost at cost'}",
                f"Shelf price {_inr(price)} against a cost of {_inr(cost)} ({list_margin * 100:.0f}% margin; the category normally earns {target * 100:.0f}%).",
                entity_type="product", entity_id=p.id, entity_name=p.name, product=p, pos=pos, severity="HIGH" if below_cost else "MEDIUM",
                metrics={"selling_price": price, "unit_cost": cost, "margin_at_list_price": round(list_margin, 4), "category_target_margin": target, "recent_unit_price": round(cur.unit_price, 2) if cur and cur.units else None},
                evidence={"rule": "sold below cost in the last 14 days, or margin at shelf price < 5% in a category that targets >= 15%", "records": {"product_ids": [str(p.id)], "order_item_ids": Q.order_item_ids(self.db, *self.w14, product_id=p.id, limit=20)}, "trace": ["product", "pricing_history", "order_item"]},
                impact=impact.as_dict(),
            ))
        return out

    def _discount_leakage(self, skip: set) -> list[Finding]:
        out = []
        products = {pos.product.id: pos for pos in self.positions}
        for pid, cur in self.s21.items():
            pos, base = products.get(pid), self.sb21.get(pid)
            if pos is None or pid in skip or cur.units < self.cfg.discount_min_units or cur.list_revenue < self.cfg.discount_min_list_revenue or base is None:
                continue
            r, b = cur.discount_rate or 0.0, base.discount_rate or 0.0
            if r < self.cfg.discount_min_rate or r < max(2 * b, b + self.cfg.discount_min_extra):
                continue
            impact = self.E.discount_impact(cur.list_revenue / 21, b, r)
            p = pos.product
            out.append(self._base(
                "DISCOUNT_LEAKAGE", f"DISCOUNT_LEAKAGE:product:{pid}", f"Discounts on {p.name} jumped to {r * 100:.0f}%",
                f"The normal discount on this product is {b * 100:.1f}%. Over the last 21 days it averaged {r * 100:.1f}% of the shelf price.",
                entity_type="product", entity_id=p.id, entity_name=p.name, product=p, pos=pos, severity=_sev([(impact.value >= 10000, "HIGH")], "MEDIUM"),
                metrics={"discount_rate_recent": round(r, 4), "discount_rate_normal": round(b, 4), "discount_given_recent": round(cur.discount, 2)},
                evidence={"window": {"recent": [d.isoformat() for d in self.w21], "normal": [d.isoformat() for d in self.wb21]}, "rule": "discount rate >= 8% and >= max(2x normal, normal + 6 points)", "records": {"order_item_ids": Q.order_item_ids(self.db, *self.w21, product_id=pid, limit=40), "product_ids": [str(pid)]}, "trace": ["order_item", "discount"]},
                impact=impact.as_dict(),
            ))
        return out

    def _excess_and_slow(self) -> list[Finding]:
        out = []
        for pos in self.positions:
            if pos.on_hand <= 0:
                continue
            p = pos.product
            u28 = self.s28.get(p.id, Q.SalesStats()).units
            v28 = u28 / 28
            unit_cost = float(pos.inventory.unit_value)
            value = pos.on_hand * unit_cost
            cover = pos.on_hand / max(v28, 1 / 60)
            if cover >= self.cfg.excess_min_cover_days and value >= self.cfg.excess_min_value and pos.on_hand >= 10:
                keep = math.ceil(v28 * self.cfg.excess_keep_days)
                excess = pos.on_hand - keep
                age, layers = Q.stock_age(self.db, p.id, pos.store.id if pos.store else None, pos.on_hand, self.t)
                impact = self.E.inventory_capital_locked(excess, unit_cost, float(pos.inventory.holding_cost_per_unit_month))
                out.append(self._base(
                    "EXCESS_INVENTORY", f"EXCESS_INVENTORY:{pos.inventory.id}", f"{_inr(impact.value)} is locked in slow-selling {p.name} stock",
                    f"{pos.on_hand} units on the shelf but only {u28} sold in 28 days: about {cover:.0f} days of stock." + (f" The oldest units arrived {age} days ago." if age else ""),
                    entity_type="product", entity_id=p.id, entity_name=p.name, product=p, pos=pos, severity=_sev([(impact.value >= 50000, "HIGH"), (impact.value >= 20000, "MEDIUM")], "LOW"),
                    metrics={"units_on_hand": pos.on_hand, "units_sold_28d": u28, "days_of_cover": round(cover), "stock_value": round(value, 2), "excess_units": excess, "stock_age_days": age},
                    evidence={"window": {"start": self.w28[0].isoformat(), "end": self.w28[1].isoformat()}, "rule": "days of cover >= 90, stock value >= ₹20,000 and >= 10 units", "receipts": [{"delivery_id": r["delivery_id"], "delivered_date": r["delivered_date"].isoformat(), "units": r["units"], "po_number": r["po_number"], "notes": r["notes"]} for r in layers[:5]],
                              "records": {"inventory_ids": [str(pos.inventory.id)], "supplier_delivery_ids": [r["delivery_id"] for r in layers[:5]], "purchase_order_ids": [r["purchase_order_id"] for r in layers[:5]], "order_item_ids": Q.order_item_ids(self.db, *self.w28, product_id=p.id, limit=20), "product_ids": [str(p.id)]}, "trace": ["inventory", "supplier_delivery", "purchase_order", "order_item"]},
                    impact=impact.as_dict(),
                ))
                continue
            u30 = self.s30.get(p.id, Q.SalesStats()).units
            if u30 > self.cfg.slow_max_units_30d or value < self.cfg.slow_min_value:
                continue
            age, layers = Q.stock_age(self.db, p.id, pos.store.id if pos.store else None, pos.on_hand, self.t)
            if age is None or age < self.cfg.slow_min_age_days:
                continue
            impact = self.E.inventory_capital_locked(pos.on_hand, unit_cost, float(pos.inventory.holding_cost_per_unit_month))
            out.append(self._base(
                "SLOW_MOVING_INVENTORY", f"SLOW_MOVING_INVENTORY:{pos.inventory.id}", f"{p.name} has barely sold in 30 days",
                f"{pos.on_hand} units ({_inr(value)}) have been on the shelf for {age} days; {u30} sold in the last 30 days.", entity_type="product", entity_id=p.id, entity_name=p.name, product=p, pos=pos, severity=_sev([(value >= 25000, "MEDIUM")], "LOW"),
                metrics={"units_on_hand": pos.on_hand, "units_sold_30d": u30, "stock_value": round(value, 2), "stock_age_days": age},
                evidence={"rule": "stock age >= 75 days, <= 2 units sold in 30 days, stock value >= ₹8,000", "records": {"inventory_ids": [str(pos.inventory.id)], "supplier_delivery_ids": [r["delivery_id"] for r in layers[:5]], "product_ids": [str(p.id)]}, "trace": ["inventory", "supplier_delivery", "order_item"]},
                impact=impact.as_dict(),
            ))
        return out

    def _revenue_decline(self) -> list[Finding]:
        wk = B.weekly_revenue(self.db, self.t)
        if wk["change_pct"] is None or wk["change_pct"] / 100 > self.cfg.revenue_max_change or wk["z"] > self.cfg.revenue_max_z:
            return []
        days = B.weekday_sales(self.db, self.t)
        worst = min(days, key=lambda d: d["change_pct"] if d["change_pct"] is not None else 0)
        impact = self.E.revenue_impact(wk["normal_weekly_mean"], wk["last_7_days"])
        return [self._base(
            "REVENUE_DECLINE", "REVENUE_DECLINE:business", f"Sales this week are {abs(wk['change_pct']):.0f}% below normal",
            f"Last 7 days: {_inr(wk['last_7_days'])} against a normal week of {_inr(wk['normal_weekly_mean'])}. {worst['weekday']} was {abs(worst['change_pct'] or 0):.0f}% below its normal range over the previous 8 weeks.",
            entity_type="business", entity_name="Whole business", severity=_sev([(wk["change_pct"] <= -30, "HIGH")], "MEDIUM"),
            metrics={**wk, "worst_weekday": worst["weekday"], "worst_weekday_change_pct": worst["change_pct"]},
            evidence={"weekday_sales": days, "rule": "last 7 days >= 20% below the 8-week weekly mean and z <= -2.5", "records": {"financial_metric_ids": Q.financial_metric_ids(self.db, *Q.window(self.t, 63)), "financial_metric_dates": [d["date"] for d in days], "order_ids": Q.order_ids(self.db, *Q.window(self.t, 7), limit=50)}, "trace": ["order", "financial_metric"]},
            impact=impact.as_dict(),
        )]

    def _repeat_decline(self) -> list[Finding]:
        rp = B.repeat_purchase(self.db, self.t)
        r, b = rp["recent_share"], rp["normal_share"]
        if r is None or b is None or rp["recent_orders"] < self.cfg.repeat_min_orders or b - r < self.cfg.repeat_min_abs_drop or (b - r) / b < self.cfg.repeat_min_rel_drop:
            return []
        recent = Q.sales_total(self.db, *self.w28)
        aov = recent.revenue / recent.orders if recent.orders else 0.0
        lost = (b - r) * rp["recent_orders"] * 30 / 28
        impact = self.E.customer_revenue_exposure(lost, aov)
        return [self._base(
            "REPEAT_CUSTOMER_DECLINE", "REPEAT_CUSTOMER_DECLINE:business", f"Fewer customers are coming back: repeat orders down to {r * 100:.0f}%",
            f"Repeat customers placed {r * 100:.0f}% of orders in the last 28 days against a normal {b * 100:.0f}%.", entity_type="business", entity_name="Whole business", severity="MEDIUM",
            metrics=rp, evidence={"rule": "repeat-order share down >= 25% relative and >= 8 points", "records": {"order_ids": Q.order_ids(self.db, *self.w28, limit=50), "repeat_order_ids": Q.order_ids(self.db, *self.w28, repeat_only=True, limit=50)}, "trace": ["order", "customer"]}, impact=impact.as_dict(),
        )]

    # ---- opportunities ------------------------------------------------------------------------------------------------
    def _demand_spike(self) -> list[Finding]:
        out = []
        for pos in self.positions:
            p = pos.product
            if p.id in self.stockout_products:
                continue
            u14 = self.s14.get(p.id, Q.SalesStats()).units
            if u14 < self.cfg.spike_min_units:
                continue
            v14, v56 = self.velocity(pos), self.velocity(pos, "56")
            if v56 <= 0 or (v14 - v56) / v56 < self.cfg.spike_min_growth:
                continue
            z = Q.z_rates(u14, 14, self.s56.get(p.id, Q.SalesStats()).units, 56)
            l14, expected = self.line_rate_expectation(pos)
            p_value = Q.poisson_upper(l14, expected)
            if p_value > self.cfg.spike_max_p:
                continue
            price, margin = self.price_margin(p.id)
            impact = self.E.demand_opportunity_value(v14 - v56, price, margin)
            out.append(self._base(
                "DEMAND_OPPORTUNITY", f"DEMAND_OPPORTUNITY:product:{p.id}", f"Demand for {p.name} is up {(v14 - v56) / v56 * 100:.0f}%",
                f"Selling {v14:.1f} a day against a normal {v56:.1f} over the previous 8 weeks.", entity_type="product", entity_id=p.id, entity_name=p.name, product=p, pos=pos, kind="opportunity",
                signal="demand_growth", confidence=_confidence(p_value), metrics={"units_per_day_recent": round(v14, 2), "units_per_day_normal": round(v56, 2), "growth_pct": round((v14 - v56) / v56 * 100, 1), "z": round(z, 2), "p_value": p_value, "units_on_hand": pos.on_hand},
                evidence={"window": {"recent": [d.isoformat() for d in self.w14], "normal": [d.isoformat() for d in self.w56]}, "rule": "in-stock demand up >= 40% (>= 12 units, Poisson p <= 0.001)", "records": {"order_item_ids": Q.order_item_ids(self.db, *self.w14, product_id=p.id, limit=40), "product_ids": [str(p.id)]}, "trace": ["order_item", "customer"]},
                impact=impact.as_dict(),
            ))
        return out

    def _category_opportunity(self) -> list[Finding]:
        out = []
        rc, pc = Q.sales_by_category(self.db, *self.w28), Q.sales_by_category(self.db, *self.wp28)
        for cat_name, cur in rc.items():
            prev = pc.get(cat_name)
            if prev is None or prev.units == 0 or cur.units < self.cfg.category_min_units:
                continue
            g = (cur.units - prev.units) / prev.units
            z = Q.z_rates(cur.units, 28, prev.units, 28)
            p_value = Q.poisson_upper(cur.lines, prev.lines)
            if g < self.cfg.category_min_growth or p_value > self.cfg.opp_max_p:
                continue
            impact = self.E.demand_opportunity_value((cur.units - prev.units) / 28, cur.unit_price or 0, cur.unit_margin or 0)
            out.append(self._base(
                "CATEGORY_OPPORTUNITY", f"CATEGORY_OPPORTUNITY:{cat_name}", f"{cat_name} is selling {g * 100:.0f}% faster",
                f"{cur.units} units in the last 28 days against {prev.units} in the 28 days before.", entity_type="category", entity_name=cat_name, kind="opportunity", signal="category_growth", confidence=_confidence(p_value),
                metrics={"units_recent": cur.units, "units_previous": prev.units, "growth_pct": round(g * 100, 1), "z": round(z, 2), "p_value": p_value, "customers_recent": cur.customers, "customers_previous": prev.customers},
                evidence={"category": cat_name, "window": {"recent": [d.isoformat() for d in self.w28], "previous": [d.isoformat() for d in self.wp28]}, "rule": "category units up >= 25% (>= 60 units, Poisson p <= 0.0001 on order lines)", "records": {"order_item_ids": Q.order_item_ids(self.db, *self.w28, category=cat_name, limit=50)}, "trace": ["order_item", "product", "customer"]},
                impact=impact.as_dict(),
            ))
        return out

    def _regional_opportunity(self) -> list[Finding]:
        out = []
        rc, pc = Q.sales_by_category_region(self.db, *self.w28), Q.sales_by_category_region(self.db, *self.wp28)
        for (cat_name, region), cur in rc.items():
            prev = pc.get((cat_name, region))
            if prev is None or prev.units == 0 or cur.units < self.cfg.regional_min_units:
                continue
            g = (cur.units - prev.units) / prev.units
            z = Q.z_rates(cur.units, 28, prev.units, 28)
            p_value = Q.poisson_upper(cur.lines, prev.lines)
            if g < self.cfg.regional_min_growth or p_value > self.cfg.opp_max_p:
                continue
            seg_r, seg_p = Q.sales_by_segment(self.db, *self.w28, category=cat_name, region=region), Q.sales_by_segment(self.db, *self.wp28, category=cat_name, region=region)
            segs = sorted(({"segment": k, "units_recent": v.units, "units_previous": seg_p.get(k, Q.SalesStats()).units} for k, v in seg_r.items()), key=lambda x: -(x["units_recent"] - x["units_previous"]))
            impact = self.E.demand_opportunity_value((cur.units - prev.units) / 28, cur.unit_price or 0, cur.unit_margin or 0)
            out.append(self._base(
                "REGIONAL_OPPORTUNITY", f"REGIONAL_OPPORTUNITY:{cat_name}:{region}", f"{cat_name} is selling {g * 100:.0f}% faster among customers in {region}",
                f"{cur.units} units bought by {region} customers in the last 28 days against {prev.units} in the 28 days before.", entity_type="category", entity_name=cat_name, kind="opportunity", region=region, signal="regional_demand_growth", confidence=_confidence(p_value),
                segment=segs[0]["segment"] if segs else None,
                metrics={"units_recent": cur.units, "units_previous": prev.units, "growth_pct": round(g * 100, 1), "z": round(z, 2), "p_value": p_value, "customers_recent": cur.customers, "customers_previous": prev.customers, "revenue_recent": round(cur.revenue, 2)},
                evidence={"category": cat_name, "region": region, "segments": segs, "window": {"recent": [d.isoformat() for d in self.w28], "previous": [d.isoformat() for d in self.wp28]}, "rule": "category units in one zone up >= 30% (>= 50 units, Poisson p <= 0.0001 on order lines)", "records": {"order_item_ids": Q.order_item_ids(self.db, *self.w28, category=cat_name, region=region, limit=50)}, "trace": ["order_item", "order", "customer"]},
                impact=impact.as_dict(),
            ))
        return out
