"""Merchant read models for the business UI. They aggregate stored records; they invent nothing.

Money shown here is either a sum of stored Impact Engine results (findings, decisions) or plain arithmetic over the
merchant's own sales records (revenue, gross margin). Every value is labelled synthetic / estimated where it applies.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domains.merchant import queries as Q
from app.domains.merchant.catalog import get_template
from app.models import ActionExecution, BusinessRisk, Company, Customer, Decision, Expense, FinancialMetric, Opportunity, Order, Product, ProductCategory, Store, Supplier, SystemState
from app.models.enums import OpportunityStatus, RiskStatus

STOCK_TYPES = {"STOCKOUT_RISK", "DEMAND_SUPPLY_MISMATCH", "EXCESS_INVENTORY", "SLOW_MOVING_INVENTORY"}
CAPITAL_TYPES = {"EXCESS_INVENTORY", "SLOW_MOVING_INVENTORY"}
MARGIN_TYPES = {"MARGIN_DECLINE", "PRICE_ANOMALY", "DISCOUNT_LEAKAGE"}


def _open_risks(db: Session) -> list[BusinessRisk]:
    return list(db.scalars(select(BusinessRisk).where(BusinessRisk.status != RiskStatus.RESOLVED)).all())


def _flags_by_product(db: Session) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _open_risks(db):
        pid = (r.evidence or {}).get("product_id")
        if pid:
            out[pid].append({"kind": "risk", "id": str(r.id), "type": r.risk_type.value, "severity": r.severity.value, "title": r.title})
    for o in db.scalars(select(Opportunity).where(Opportunity.status != OpportunityStatus.DISMISSED)).all():
        if o.product_id:
            out[str(o.product_id)].append({"kind": "opportunity", "id": str(o.id), "type": o.opportunity_type.value, "severity": None, "title": o.title})
    return out


# ---- merchant profile ------------------------------------------------------------------------------------------
def merchant(db: Session) -> dict[str, Any]:
    state = db.get(SystemState, 1)
    company = db.scalar(select(Company))
    prof = (state.profile or {}) if state else {}
    tpl = get_template(prof.get("template"))
    stores = db.scalars(select(Store).order_by(Store.code)).all()
    cats = db.scalars(select(ProductCategory).order_by(ProductCategory.name)).all()
    return {
        "domain": "merchant",
        "business_name": company.name if company else None, "business_type": company.business_type if company else None, "template": tpl.key, "city": company.city if company else None, "state": company.state if company else None,
        "profile": prof.get("merchant"), "stores": [{"id": str(s.id), "code": s.code, "name": s.name, "locality": s.locality, "region": s.region, "type": s.store_type.value} for s in stores],
        "categories": [{"id": str(c.id), "code": c.code, "name": c.name, "target_margin": c.target_margin} for c in cats],
        "counts": {"products": db.scalar(select(func.count(Product.id))) or 0, "suppliers": db.scalar(select(func.count(Supplier.id))) or 0, "customers": db.scalar(select(func.count(Customer.id))) or 0, "orders": db.scalar(select(func.count(Order.id))) or 0},
        "data_window": {"start": state.data_start, "end": state.data_end, "seed": state.seed, "generator_version": state.generator_version} if state else None,
        "scenario": prof.get("scenario"), "is_synthetic": True, "source": "SYNTHETIC",
    }


def templates() -> list[dict[str, Any]]:
    from app.domains.merchant.catalog import TEMPLATES

    return [{"key": t.key, "label": t.label, "categories": [{"code": c.code, "name": c.name, "description": c.description} for c in t.categories]} for t in TEMPLATES.values()]


# ---- dashboard -------------------------------------------------------------------------------------------------
def _period(db: Session, start: date, end: date) -> dict[str, Any]:
    st = Q.sales_total(db, start, end)
    return {"revenue": round(st.revenue, 2), "gross_margin": round(st.margin, 2), "gross_margin_rate": round(st.margin_rate, 4) if st.margin_rate is not None else None, "orders": st.orders, "units": st.units, "average_order_value": round(st.revenue / st.orders, 2) if st.orders else None, "discount_rate": round(st.discount_rate, 4) if st.discount_rate is not None else None}


def inventory_health(db: Session) -> dict[str, Any]:
    """Share of actively sold products whose stock sits in a healthy band: it lasts until the next delivery and is not
    more than 90 days of sales. Sporadic sellers (under one unit every 20 days, under ₹5,000 of stock) are excluded."""
    t = Q.today(db)
    s28 = Q.sales_by_product(db, *Q.window(t, 28))
    open_types: dict[str, set[str]] = defaultdict(set)
    for r in _open_risks(db):
        inv_id = (r.evidence or {}).get("inventory_id")
        if inv_id:
            open_types[inv_id].add(r.risk_type.value)
    considered = healthy = short = over = 0
    for pos in Q.stock_positions(db):
        v = s28.get(pos.product.id, Q.SalesStats()).units / 28
        if v < 0.05 and pos.value < 5000:
            continue
        considered += 1
        types = open_types.get(str(pos.inventory.id), set())
        lead = pos.supplier.baseline_lead_time_days if pos.supplier else 7
        cover = pos.on_hand / v if v else float("inf")
        if types & {"STOCKOUT_RISK", "DEMAND_SUPPLY_MISMATCH"} or (cover < lead and pos.on_order == 0):
            short += 1
        elif types & {"EXCESS_INVENTORY", "SLOW_MOVING_INVENTORY"} or (cover > 90 and pos.value >= 5000):
            over += 1
        else:
            healthy += 1
    return {"healthy_pct": round(healthy / considered * 100, 1) if considered else None, "products_considered": considered, "healthy": healthy, "short_of_stock": short, "overstocked": over, "rule": "healthy = stock lasts until the next delivery and is not more than 90 days of sales; sporadic sellers excluded"}


def business_health(db: Session) -> dict[str, Any]:
    t = Q.today(db)
    cur, prev = Q.window(t, 7), Q.window(t, 7, end_offset=7)
    c, p = _period(db, *cur), _period(db, *prev)

    def chg(k):
        return round((c[k] - p[k]) / p[k] * 100, 1) if p.get(k) else None

    from app.domains.merchant.baseline import weekly_revenue

    wk = weekly_revenue(db, t)
    return {
        "period": {"label": "Last 7 days", "start": cur[0].isoformat(), "end": cur[1].isoformat()},
        **c, "previous": p,
        "revenue_change_pct": chg("revenue"), "gross_margin_change_pct": chg("gross_margin"), "orders_change_pct": chg("orders"),
        "revenue_vs_normal_week_pct": wk["change_pct"], "normal_weekly_revenue": wk["normal_weekly_mean"],
        "inventory": inventory_health(db), "currency": "INR", "basis": "SYNTHETIC",
    }


def value_metrics(db: Session) -> dict[str, Any]:
    """What REACT has surfaced and what the merchant decided: estimated, potential and simulated, never real savings."""
    risks = list(db.scalars(select(BusinessRisk)).all())
    opps = list(db.scalars(select(Opportunity)).all())
    decisions = list(db.scalars(select(Decision)).all())

    def val(d):
        return float((d or {}).get("value") or 0.0)

    open_r = [r for r in risks if r.status != RiskStatus.RESOLVED]
    by_status = defaultdict(int)
    for d in decisions:
        by_status[d.status.value] += 1
    executed = [d for d in decisions if d.status.value == "EXECUTED"]
    protected = sum(float((d.expected_impact or {}).get("estimated_total") or 0) for d in executed if d.subject_type.value == "RISK")
    margin_protected = sum(float((d.expected_impact or {}).get("estimated_total") or 0) for d in executed if d.subject_type.value == "RISK" and d.risk_id and (db.get(BusinessRisk, d.risk_id).risk_type.value in MARGIN_TYPES))
    opp_pursued = sum(float((d.expected_impact or {}).get("estimated_total") or 0) for d in executed if d.subject_type.value == "OPPORTUNITY")
    return {
        "potential_loss_identified": round(sum(val(r.estimated_impact) for r in open_r), 2),
        "opportunity_identified": round(sum(val(o.estimated_impact) for o in opps if o.status != OpportunityStatus.DISMISSED), 2),
        "inventory_capital_at_risk": round(sum(val(r.estimated_impact) for r in open_r if r.risk_type.value in CAPITAL_TYPES), 2),
        "issues_detected": len(risks), "issues_open": len(open_r), "issues_resolved": len(risks) - len(open_r),
        "stockout_risks_detected": sum(1 for r in risks if r.risk_type.value == "STOCKOUT_RISK"), "supplier_risks_detected": sum(1 for r in risks if r.risk_type.value in ("SUPPLIER_DELAY", "SUPPLIER_RELIABILITY")),
        "opportunities_detected": len(opps),
        "actions_recommended": sum(1 for d in decisions if d.action_type), "actions_approved": by_status["APPROVED"] + by_status["EXECUTING"] + by_status["EXECUTED"], "actions_rejected": by_status["REJECTED"], "simulated_actions_executed": by_status["EXECUTED"],
        "revenue_protected_estimate": round(protected, 2), "margin_protected_estimate": round(margin_protected, 2), "opportunity_pursued_estimate": round(opp_pursued, 2),
        "currency": "INR",
        "labels": {"potential_loss_identified": "Estimated, synthetic", "opportunity_identified": "Potential, synthetic", "revenue_protected_estimate": "Estimated impact of approved simulated actions; not real savings"},
    }


# ---- products / inventory / suppliers ------------------------------------------------------------------------------
def products(db: Session, category: str | None = None) -> list[dict[str, Any]]:
    t = Q.today(db)
    s28 = Q.sales_by_product(db, *Q.window(t, 28))
    w14, w56 = Q.window(t, 14), Q.window(t, 56, end_offset=14)
    s14, s56 = Q.sales_by_product(db, *w14), Q.sales_by_product(db, *w56)
    positions = Q.stock_positions(db)
    by_product: dict[Any, list[Q.StockPosition]] = defaultdict(list)
    for p in positions:
        by_product[p.product.id].append(p)
    inv_ids = [p.inventory.id for p in positions]
    ins14, ins56 = Q.in_stock_days(db, inv_ids, *w14), Q.in_stock_days(db, inv_ids, *w56)
    suppliers = {s.id: s.name for s in db.scalars(select(Supplier)).all()}
    flags = _flags_by_product(db)
    q = select(Product).order_by(Product.category, Product.name)
    if category:
        q = q.where(Product.category == category)
    out = []
    for p in db.scalars(q).all():
        st = s28.get(p.id, Q.SalesStats())
        poss = by_product.get(p.id, [])
        on_hand = sum(x.on_hand for x in poss)
        i14 = sum(ins14.get(x.inventory.id, 0) for x in poss) / max(1, len(poss))
        i56 = sum(ins56.get(x.inventory.id, 0) for x in poss) / max(1, len(poss))
        u14, u56 = s14.get(p.id, Q.SalesStats()).units, s56.get(p.id, Q.SalesStats()).units
        v14 = u14 / max(7, i14) if u14 else 0.0
        v56 = u56 / max(28, i56) if u56 else 0.0
        out.append({
            "id": str(p.id), "company_id": str(p.company_id), "production_line_id": None, "target_margin": p.target_margin, "is_active": p.is_active,
            "sku": p.sku, "name": p.name, "category": p.category, "brand": p.brand, "selling_price": float(p.selling_price), "unit_cost": float(p.unit_cost), "mrp": float(p.mrp) if p.mrp else None,
            "margin_rate_at_price": round((float(p.selling_price) - float(p.unit_cost)) / float(p.selling_price), 4) if float(p.selling_price) else None,
            "units_28d": st.units, "revenue_28d": round(st.revenue, 2), "gross_margin_28d": round(st.margin, 2), "demand_change_pct": round((v14 - v56) / v56 * 100, 1) if v56 and (u14 + u56) >= 8 else None,
            "stock_on_hand": on_hand, "merchant_days_of_cover": round(on_hand / (st.units / 28), 1) if st.units else None, "supplier": suppliers.get(p.supplier_id), "supplier_id": str(p.supplier_id) if p.supplier_id else None,
            "findings": flags.get(str(p.id), []),
        })
    return out


def inventory(db: Session) -> list[dict[str, Any]]:
    t = Q.today(db)
    s28 = Q.sales_by_product(db, *Q.window(t, 28))
    flagged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _open_risks(db):
        inv_id = (r.evidence or {}).get("inventory_id")
        if inv_id:
            flagged[inv_id].append({"kind": "risk", "id": str(r.id), "type": r.risk_type.value, "severity": r.severity.value, "title": r.title})
    out = []
    for pos in Q.stock_positions(db):
        v = s28.get(pos.product.id, Q.SalesStats()).units / 28
        cover = pos.on_hand / v if v else None
        overdue = [po for po in pos.open_pos if po["expected_date"] < t]
        status = "OUT_OF_STOCK" if pos.on_hand == 0 else "LOW" if cover is not None and pos.supplier and cover < pos.supplier.baseline_lead_time_days else "OVERSTOCKED" if (cover is None or cover > 90) and pos.value >= 5000 else "HEALTHY"
        inv = pos.inventory
        out.append({
            "id": str(inv.id), "item_type": "PRODUCT", "component_type": None, "item_name": inv.item_name, "warehouse": inv.warehouse, "quantity_available": pos.on_hand, "quantity_reserved": inv.quantity_reserved, "safety_stock": inv.safety_stock, "max_stock": inv.max_stock,
            "holding_cost_per_unit_month": float(inv.holding_cost_per_unit_month), "unit_value": float(inv.unit_value), "avg_daily_consumption": inv.avg_daily_consumption, "last_updated": inv.last_updated,
            "product_id": str(pos.product.id), "sku": pos.product.sku, "category": pos.product.category, "store": pos.store.name if pos.store else None,
            "on_order": pos.on_order, "reorder_point": inv.reorder_point, "days_of_cover": round(cover, 1) if cover is not None else 9999.0,
            "stock_value": round(pos.value, 2), "open_purchase_orders": len(pos.open_pos), "overdue_purchase_orders": len(overdue),
            "supplier": pos.supplier.name if pos.supplier else None, "status": status, "findings": flagged.get(str(pos.inventory.id), []),
        })
    order = {"OUT_OF_STOCK": 0, "LOW": 1, "OVERSTOCKED": 2, "HEALTHY": 3}
    out.sort(key=lambda x: (order[x["status"]], -(len(x["findings"])), -x["stock_value"]))
    return out


def suppliers(db: Session) -> list[dict[str, Any]]:
    from app.domains.merchant.baseline import supplier_delivery

    t = Q.today(db)
    s28 = Q.sales_by_product(db, *Q.window(t, 28))
    perf = {x["supplier_id"]: x for x in supplier_delivery(db, t)}
    flagged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _open_risks(db):
        if r.entity_type == "supplier" and r.entity_id:
            flagged[str(r.entity_id)].append({"kind": "risk", "id": str(r.id), "type": r.risk_type.value, "severity": r.severity.value, "title": r.title})
    out = []
    for s in db.scalars(select(Supplier).order_by(Supplier.name)).all():
        prods = Q.supplier_products(db, s.id)
        dels = Q.deliveries(db, s.id, limit=6)
        open_pos = Q.open_purchase_orders(db, s.id)
        rev = sum(s28.get(p.id, Q.SalesStats()).revenue for p in prods)
        pf = perf.get(str(s.id), {})
        out.append({
            "id": str(s.id), "name": s.name, "component_type": s.component_type, "location": s.location, "country": s.country, "reliability_score": s.reliability_score, "lead_time_days": s.lead_time_days, "baseline_lead_time_days": s.baseline_lead_time_days,
            "unit_cost": float(s.unit_cost), "status": s.status.value, "is_primary": s.is_primary, "open_batches": [],
            "recent_lead_days": pf.get("recent_lead_days"), "normal_lead_days": pf.get("normal_lead_days"),
            "on_time_rate_recent": pf.get("recent_on_time_rate"), "on_time_rate_normal": pf.get("normal_on_time_rate"),
            "late_of_last_6": sum(1 for d in dels if not d["on_time"]), "deliveries_considered": len(dels), "open_purchase_orders": len(open_pos), "overdue_purchase_orders": sum(1 for po in open_pos if po.expected_date < t),
            "products": len(prods), "revenue_28d": round(rev, 2), "findings": flagged.get(str(s.id), []),
        })
    return out


def sales(db: Session, days: int = 28) -> dict[str, Any]:
    t = Q.today(db)
    start, end = Q.window(t, days)
    fm = db.scalars(select(FinancialMetric).where(FinancialMetric.metric_date >= start, FinancialMetric.metric_date <= end).order_by(FinancialMetric.metric_date)).all()
    by_channel = db.execute(select(Order.channel, func.count(Order.id), func.sum(Order.total_revenue)).where(Order.order_date >= start, Order.order_date <= end).group_by(Order.channel)).all()
    by_pay = db.execute(select(Order.payment_method, func.count(Order.id)).where(Order.order_date >= start, Order.order_date <= end).group_by(Order.payment_method)).all()
    cats = Q.sales_by_category(db, start, end)
    zones = defaultdict(lambda: {"revenue": 0.0, "units": 0})
    for (c, z), st in Q.sales_by_category_region(db, start, end).items():
        zones[z]["revenue"] += st.revenue
        zones[z]["units"] += st.units
    exp = db.execute(select(Expense.category, func.sum(Expense.amount)).where(Expense.expense_date >= start, Expense.expense_date <= end).group_by(Expense.category)).all()
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "daily": [{"date": r.metric_date.isoformat(), "revenue": float(r.revenue), "gross_margin": round(float(r.revenue) - float(r.cost), 2), "orders": r.orders_count, "profit": float(r.profit)} for r in fm],
        "totals": {"revenue": round(sum(float(r.revenue) for r in fm), 2), "gross_margin": round(sum(float(r.revenue) - float(r.cost) for r in fm), 2), "operating_cost": round(sum(float(r.operating_cost) for r in fm), 2), "profit": round(sum(float(r.profit) for r in fm), 2), "discounts": round(sum(float(r.discount_total) for r in fm), 2), "orders": sum(r.orders_count for r in fm)},
        "by_channel": [{"channel": ch, "orders": int(n), "revenue": round(float(v or 0), 2)} for ch, n, v in by_channel],
        "by_payment_method": [{"method": m, "orders": int(n)} for m, n in by_pay],
        "by_category": sorted([{"category": k, "revenue": round(v.revenue, 2), "units": v.units, "gross_margin_rate": round(v.margin_rate, 4) if v.margin_rate is not None else None} for k, v in cats.items()], key=lambda x: -x["revenue"]),
        "by_zone": sorted([{"region": k, "revenue": round(v["revenue"], 2), "units": v["units"]} for k, v in zones.items()], key=lambda x: -x["revenue"]),
        "expenses": {k.value if hasattr(k, "value") else k: round(float(v), 2) for k, v in exp},
        "currency": "INR", "basis": "SYNTHETIC",
    }
