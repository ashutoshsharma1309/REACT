"""Merchant impact analysis: what a finding is worth in INR, computed only by the Impact Engine.

Each subject type selects the formulas that answer the merchant's question ("what does this cost me?" or "what could
this earn me?"). The numbers are recomputed from current data with the same measures the detection rules use, so the
investigation, the decision and the evidence pack all quote one consistent figure. `total_includes` says which
components make up the headline total; everything else is context (for example a suggested price or the cost of a
reorder), never added to the total.
"""
from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.domains.merchant import queries as Q
from app.domains.merchant.language import plural
from app.domains.merchant.detection import MerchantDetector
from app.impact import ImpactEngine
from app.investigation.signals import SignalSet
from app.models import BusinessRisk, Opportunity, Product, Supplier

WORDING_RISK = "Estimated impact: synthetic, formula-based estimate from the merchant's records; not a guaranteed outcome."
WORDING_OPP = "Potential opportunity: synthetic, formula-based estimate; actual results depend on stock and execution."
REORDER_COVER_DAYS = 14


def _pos(det: MerchantDetector, ev: dict[str, Any]) -> Q.StockPosition | None:
    inv_id = ev.get("inventory_id")
    pid = ev.get("product_id")
    for p in det.positions:
        if inv_id and str(p.inventory.id) == inv_id:
            return p
    for p in det.positions:
        if pid and str(p.product.id) == pid:
            return p
    return None


def reorder_suggestion(det: MerchantDetector, pos: Q.StockPosition, cover_days: int = REORDER_COVER_DAYS) -> dict[str, Any]:
    """Deterministic order quantity: cover the observed lead time + `cover_days` + 3 safety days at today's demand."""
    v, basis = det.demand_rate(pos)
    info = det.supplier_info(pos.supplier)
    v, lead = round(v, 2), round(info["lead_eff"], 1)  # compute from the shown inputs so anyone can reproduce the number
    target = v * (lead + cover_days + 3)
    qty = max(0, math.ceil(round(target - pos.on_hand - pos.on_order, 6)))
    return {"quantity": qty, "units_per_day": v, "demand_basis": basis, "lead_days": lead, "cover_days": cover_days, "on_hand": pos.on_hand, "on_order": pos.on_order, "supplier_id": str(pos.supplier.id) if pos.supplier else None, "supplier": pos.supplier.name if pos.supplier else None, "unit_cost": float(pos.inventory.unit_value), "formula": "quantity = ceil(units_per_day x (lead_days + cover_days + 3 safety days) - on_hand - on_order)"}


def analyse_merchant_impact(db: Session, subject: BusinessRisk | Opportunity, S: SignalSet, conclusion: dict[str, Any], E: ImpactEngine | None = None) -> dict[str, Any]:
    E = E or ImpactEngine()
    det = MerchantDetector(db, impact=E)
    ev = subject.evidence or {}
    stype = S.subject_type
    out: dict[str, Any] = {"domain": "merchant", "currency": E.config.currency, "scope": {"subject_type": stype, "entity_name": getattr(subject, "entity_name", None), "product_id": ev.get("product_id"), "inventory_id": ev.get("inventory_id"), "supplier_id": ev.get("supplier_id"), "category": ev.get("category"), "region": ev.get("region") or getattr(subject, "region", None)}, "operational_constraints": []}
    pos = _pos(det, ev)
    price, margin = det.price_margin(pos.product.id) if pos else (0.0, 0.0)

    if stype == "STOCKOUT_RISK" and pos:
        v, basis = det.demand_rate(pos)
        cover = pos.on_hand / v if v else 0.0
        arrival, arrival_basis = det.arrival_days(pos)
        gap = max(0.0, arrival - cover)
        lost = E.stockout_loss(v, gap, price, margin)
        sugg = reorder_suggestion(det, pos)
        cost = E.purchase_order_value(sugg["quantity"], sugg["unit_cost"])
        out.update({"lost_sales": lost.as_dict(), "reorder_cost": {**cost.as_dict(), "description": f"Cost of the suggested reorder ({sugg['quantity']} units)"}, "estimated_total": round(lost.value, 2), "total_includes": ["lost_sales"], "reorder_suggestion": sugg,
                    "inputs": {"units_per_day": round(v, 2), "demand_basis": basis, "days_of_cover": round(cover, 1), "days_until_next_stock": round(arrival, 1), "next_stock_basis": arrival_basis, "unit_price": round(price, 2), "unit_margin": round(margin, 2)}})
        info = det.supplier_info(pos.supplier)
        if info["observed"] and info["observed"] > info["quoted"] + 1:
            out["operational_constraints"].append({"type": "supplier_lead_time", "detail": f"{pos.supplier.name} currently takes about {info['observed']:.0f} days to deliver against a quoted {info['quoted']} days", "signal": "supplier_lead_change_days"})
    elif stype == "DEMAND_SUPPLY_MISMATCH" and pos:
        v = det.velocity(pos)
        info = det.supplier_info(pos.supplier)
        need = v * (info["lead_eff"] + 7)
        risk = E.inventory_risk(need, pos.on_hand + pos.on_order, price)
        sugg = reorder_suggestion(det, pos)
        out.update({"sales_at_risk": {**risk.as_dict(), "description": f"About {max(0, need - pos.on_hand - pos.on_order):.0f} units of demand not covered by stock or open orders"}, "reorder_cost": E.purchase_order_value(sugg["quantity"], sugg["unit_cost"]).as_dict(), "estimated_total": round(risk.value, 2), "total_includes": ["sales_at_risk"], "reorder_suggestion": sugg})
    elif stype in ("SUPPLIER_DELAY", "SUPPLIER_RELIABILITY"):
        sup = db.get(Supplier, subject.entity_id) if subject.entity_id else None
        items, rev_day = [], 0.0
        for p in det.positions:
            if not sup or not p.supplier or p.supplier.id != sup.id:
                continue
            st = det.s28.get(p.product.id)
            rev_day += (st.revenue / 28) if st else 0.0
            v = det.velocity(p)
            if v < 0.15:
                continue
            arrival, _ = det.arrival_days(p)
            pr, mg = det.price_margin(p.product.id)
            gap = max(0.0, arrival - p.on_hand / v)
            if gap > 0:
                items.append({"product": p.product.name, "units_per_day": v, "gap_days": gap, "unit_price": pr, "unit_margin": mg})
        exposure = E.supplier_delay_exposure(items)
        dep = E.dependent_revenue(rev_day)
        out.update({"delay_exposure": exposure.as_dict(), "dependent_revenue": dep.as_dict(), "estimated_total": round(exposure.value, 2), "total_includes": ["delay_exposure"], "products_at_risk": [i["product"] for i in items]})
        if not items:
            out["operational_constraints"].append({"type": "stock_buffer", "detail": "Current stock still covers the expected delays; the exposure grows with every further late delivery", "signal": "products_low_cover"})
    elif stype in ("MARGIN_DECLINE", "PRICE_ANOMALY") and pos:
        cur, base = det.s21.get(pos.product.id), det.sb21.get(pos.product.id)
        um_r = (cur.unit_margin if cur and cur.units else float(pos.product.selling_price) - float(pos.product.unit_cost))
        um_b = (base.unit_margin if base and base.units else float(pos.product.selling_price) * 0.2)
        v = (cur.units / 21) if cur else 0.1
        loss = E.margin_loss(v, um_b, um_r)
        sp = E.price_to_restore_margin(float(pos.product.unit_cost), um_b, float(pos.product.selling_price))
        out.update({"margin_loss": loss.as_dict(), "suggested_price": {**sp.as_dict(), "description": f"A shelf price of ₹{sp.value:,.0f} would restore the normal ₹{um_b:,.0f} margin per unit at today's cost"}, "estimated_total": round(loss.value, 2), "total_includes": ["margin_loss"],
                    "inputs": {"normal_unit_margin": round(um_b, 2), "current_unit_margin": round(um_r, 2), "units_per_day": round(v, 2), "current_cost": float(pos.product.unit_cost), "current_price": float(pos.product.selling_price)}})
        if pos.product.mrp and sp.value > float(pos.product.mrp):
            out["operational_constraints"].append({"type": "mrp", "detail": f"The price that restores the margin (₹{sp.value:,.0f}) is above the printed MRP (₹{float(pos.product.mrp):,.0f}); the cost must be renegotiated instead", "signal": "list_price_change_pct"})
    elif stype == "DISCOUNT_LEAKAGE" and pos:
        cur, base = det.s21.get(pos.product.id), det.sb21.get(pos.product.id)
        imp = E.discount_impact((cur.list_revenue / 21) if cur else 0.0, (base.discount_rate or 0.0) if base else 0.0, (cur.discount_rate or 0.0) if cur else 0.0)
        out.update({"discount_leakage": imp.as_dict(), "estimated_total": round(imp.value, 2), "total_includes": ["discount_leakage"]})
    elif stype in ("EXCESS_INVENTORY", "SLOW_MOVING_INVENTORY") and pos:
        u28 = det.s28.get(pos.product.id, Q.SalesStats()).units
        keep = math.ceil(u28 / 28 * det.cfg.excess_keep_days)
        excess = max(0, pos.on_hand - keep) if stype == "EXCESS_INVENTORY" else pos.on_hand
        cap = E.inventory_capital_locked(excess, float(pos.inventory.unit_value), float(pos.inventory.holding_cost_per_unit_month))
        out.update({"capital_locked": cap.as_dict(), "estimated_total": round(cap.value, 2), "total_includes": ["capital_locked"], "inputs": {"units_on_hand": pos.on_hand, "units_sold_28d": u28, "units_to_keep": keep, "excess_units": excess}})
    elif stype == "DEMAND_DECLINE":
        m = getattr(subject, "supporting_metrics", {}) or {}
        if pos:
            v14, v56 = det.velocity(pos), det.velocity(pos, "56")
            imp = E.revenue_impact(v56 * 30 * price, v14 * 30 * price)
        else:
            cat = ev.get("category")
            rc, pc = Q.sales_total(db, *det.w28, category=cat), Q.sales_total(db, *det.wp28, category=cat)
            imp = E.revenue_impact(pc.revenue * 30 / 28, rc.revenue * 30 / 28)
        out.update({"revenue_shortfall": imp.as_dict(), "estimated_total": round(max(0.0, imp.value), 2), "total_includes": ["revenue_shortfall"]})
    elif stype == "REVENUE_DECLINE":
        m = subject.supporting_metrics or {}
        imp = E.revenue_impact(m.get("normal_weekly_mean", 0.0), m.get("last_7_days", 0.0))
        out.update({"revenue_shortfall": imp.as_dict(), "estimated_total": round(max(0.0, imp.value), 2), "total_includes": ["revenue_shortfall"]})
    elif stype == "REPEAT_CUSTOMER_DECLINE":
        m = subject.supporting_metrics or {}
        recent = Q.sales_total(db, *det.w28)
        aov = recent.revenue / recent.orders if recent.orders else 0.0
        lost = max(0.0, (m.get("normal_share") or 0) - (m.get("recent_share") or 0)) * (m.get("recent_orders") or 0) * 30 / 28
        imp = E.customer_revenue_exposure(lost, aov)
        out.update({"customer_exposure": imp.as_dict(), "estimated_total": round(imp.value, 2), "total_includes": ["customer_exposure"]})
    elif isinstance(subject, Opportunity):
        _opportunity(db, det, subject, S, E, out, pos)
    else:
        out.update({"estimated_total": None, "total_includes": [], "note": "No impact formula applies to this finding."})
    out["wording"] = WORDING_OPP if isinstance(subject, Opportunity) else WORDING_RISK
    if conclusion.get("selected_hypothesis") in ("PROMOTION_DRIVEN_SPIKE", "BULK_BUYER_SPIKE", "PROMOTION_DRIVEN_DEMAND"):
        out["operational_constraints"].append({"type": "demand_durability", "detail": f"The leading explanation is {str(conclusion.get('selected_label', '')).lower()}; treat the projection as an upper bound", "signal": "conclusion"})
    return out


def _opportunity(db: Session, det: MerchantDetector, opp: Opportunity, S: SignalSet, E: ImpactEngine, out: dict[str, Any], pos: Q.StockPosition | None) -> None:
    ev = opp.evidence or {}
    stype = S.subject_type
    m = opp.supporting_metrics or {}
    H = E.config.default_horizon_days
    if stype == "DEMAND_OPPORTUNITY" and pos:
        v14, v56 = det.velocity(pos), det.velocity(pos, "56")
        price, margin = det.price_margin(pos.product.id)
        inc = max(0.0, v14 - v56)
        stock_units, unit_cost = inc * H, float(pos.inventory.unit_value)
        cover = pos.on_hand / v14 if v14 else None
        if cover is not None and cover < det.supplier_info(pos.supplier)["lead_eff"]:
            out["operational_constraints"].append({"type": "stock", "detail": f"Only {cover:.1f} days of stock at the new demand; restock before promoting", "signal": "days_of_cover"})
    else:
        cat, region = ev.get("category") or opp.title, ev.get("region") or opp.region
        rw, pw = det.w28, det.wp28
        cur, prev = Q.sales_total(db, *rw, category=cat, region=region), Q.sales_total(db, *pw, category=cat, region=region)
        inc = max(0.0, (cur.units - prev.units) / 28)
        price = cur.unit_price or 0.0
        margin = cur.unit_margin or 0.0
        unit_cost = cur.unit_cost or 0.0
        stock_units = inc * H
        short = []
        for p in det.positions:
            if p.product.category != cat:
                continue
            v = det.velocity(p)
            if v >= 0.5 and p.on_hand / v < det.supplier_info(p.supplier)["lead_eff"]:
                short.append(p.product.name)
        if short:
            out["operational_constraints"].append({"type": "stock", "detail": f"{plural(len(short), 'best-selling ' + cat.lower() + ' item')} already {'has' if len(short) == 1 else 'have'} less stock than one lead time: {', '.join(short[:3])}", "signal": "days_of_cover"})
    value = E.demand_opportunity_value(inc, price, margin)
    need = E.purchase_order_value(stock_units * E.config.opportunity_capture_ratio, unit_cost)
    out.update({
        "potential_revenue": value.as_dict(), "stock_needed": {**need.as_dict(), "description": f"Stock to buy for the extra demand over {H} days (about {stock_units * E.config.opportunity_capture_ratio:.0f} units at cost)"},
        "potential_additional_demand": {"units_per_day": round(inc, 2), "units_over_horizon": round(inc * H), "horizon_days": H, "basis": "28-day comparison" if stype != "DEMAND_OPPORTUNITY" else "14 days vs previous 8 weeks (in-stock days)"},
        "estimated_total": round(value.value, 2), "total_includes": ["potential_revenue"],
        "inputs": {"incremental_units_per_day": round(inc, 2), "unit_price": round(price, 2), "unit_margin": round(margin, 2), "growth_pct": m.get("growth_pct")},
    })
