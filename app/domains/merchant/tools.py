"""Merchant investigation tools (read-only, validated, deterministic).

Registered in the domain-aware tool registry under domain "merchant". Agents can call them only through the
ToolExecutor and only if the agent's allow-list contains them. No tool runs SQL text from a caller, writes data,
reaches the network or touches the filesystem. Calculation tools delegate to the Impact Engine.
"""
from __future__ import annotations

import math
import statistics
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domains.merchant import baseline as B
from app.domains.merchant import queries as Q
from app.impact import ImpactEngine
from app.investigation.tools import ToolError, _float, _int, _require_db, _uuid, tool
from app.models import CompetitorSignal, Customer, Discount, Expense, FinancialMetric, Inventory, Order, OrderItem, PricingHistory, Product, PurchaseOrder, Supplier

D = "merchant"
_E = ImpactEngine()


def _product(db: Session, product_id: Any) -> Product:
    pid = _uuid(product_id, "product_id")
    p = db.get(Product, pid)
    if p is None:
        raise ToolError("NOT_FOUND", "product not found")
    return p


def _supplier(db: Session, supplier_id: Any) -> Supplier:
    sid = _uuid(supplier_id, "supplier_id")
    s = db.get(Supplier, sid)
    if s is None:
        raise ToolError("NOT_FOUND", "supplier not found")
    return s


def _category(db: Session, category: Any) -> str:
    if not category or not isinstance(category, str):
        raise ToolError("MISSING_PARAM", "category is required")
    if category not in Q.categories(db):
        raise ToolError("NOT_FOUND", f"unknown category '{category}'")
    return category


def _stats(st: Q.SalesStats, days: int) -> dict[str, Any]:
    return {"units": st.units, "units_per_day": round(st.units / days, 3), "revenue": round(st.revenue, 2), "list_revenue": round(st.list_revenue, 2), "discount": round(st.discount, 2), "discount_rate": round(st.discount_rate, 4) if st.discount_rate is not None else None, "cogs": round(st.cogs, 2), "margin": round(st.margin, 2), "unit_price": round(st.unit_price, 2) if st.unit_price else None, "unit_cost": round(st.unit_cost, 2) if st.unit_cost else None, "unit_margin": round(st.unit_margin, 2) if st.unit_margin is not None else None, "order_lines": st.lines, "orders": st.orders, "customers": st.customers}


@tool("get_product_sales", "Sales of one product: recent window vs the previous normal window (units, price, cost, margin, discount, customers), in-stock adjusted demand and the order lines behind it.", {"product_id": "UUID", "days": "recent window 7-60 (default 14)", "compare_days": "normal window 14-112 (default 56)"}, domain=D)
def get_product_sales(db: Session, product_id: Any, days: int | None = None, compare_days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    p = _product(db, product_id)
    days, compare = _int(days, "days", 14, 7, 60), _int(compare_days, "compare_days", 56, 14, 112)
    t = Q.today(db)
    rw, nw = Q.window(t, days), Q.window(t, compare, end_offset=days)
    rec, nor = Q.sales_total(db, *rw, product_id=p.id), Q.sales_total(db, *nw, product_id=p.id)
    invs = list(db.scalars(select(Inventory).where(Inventory.product_id == p.id)).all())
    ins_r = sum(Q.in_stock_days(db, [i.id for i in invs], *rw).values()) / max(1, len(invs)) if invs else days
    ins_n = sum(Q.in_stock_days(db, [i.id for i in invs], *nw).values()) / max(1, len(invs)) if invs else compare
    v_r = rec.units / max(min(7, days), ins_r) if rec.units else 0.0
    v_n = nor.units / max(min(28, compare), ins_n) if nor.units else 0.0
    top = db.execute(select(Order.customer_id, func.sum(OrderItem.quantity)).join(Order, Order.id == OrderItem.order_id).where(OrderItem.product_id == p.id, Order.order_date >= rw[0], Order.order_date <= rw[1]).group_by(Order.customer_id).order_by(func.sum(OrderItem.quantity).desc()).limit(1)).first()
    online = db.execute(select(func.sum(OrderItem.quantity)).join(Order, Order.id == OrderItem.order_id).where(OrderItem.product_id == p.id, Order.order_date >= rw[0], Order.order_date <= rw[1], Order.channel != "STORE")).scalar() or 0
    online_n = db.execute(select(func.sum(OrderItem.quantity)).join(Order, Order.id == OrderItem.order_id).where(OrderItem.product_id == p.id, Order.order_date >= nw[0], Order.order_date <= nw[1], Order.channel != "STORE")).scalar() or 0
    return {
        "product": {"id": str(p.id), "sku": p.sku, "name": p.name, "category": p.category, "selling_price": float(p.selling_price), "unit_cost": float(p.unit_cost), "mrp": float(p.mrp) if p.mrp else None},
        "recent": {**_stats(rec, days), "window": [rw[0].isoformat(), rw[1].isoformat()], "in_stock_days": round(ins_r, 1), "units_per_in_stock_day": round(v_r, 3), "online_units": int(online), "top_customer_units": int(top[1]) if top else 0},
        "normal": {**_stats(nor, compare), "window": [nw[0].isoformat(), nw[1].isoformat()], "in_stock_days": round(ins_n, 1), "units_per_in_stock_day": round(v_n, 3), "online_units": int(online_n)},
        "demand_change_pct": round((v_r - v_n) / v_n * 100, 1) if v_n else None,
        "order_item_ids": Q.order_item_ids(db, *rw, product_id=p.id, limit=60), "order_item_ids_normal": Q.order_item_ids(db, *nw, product_id=p.id, limit=30),
        "count": rec.lines,
    }


@tool("get_inventory_status", "Stock position of a product (per store): on hand, on order, open purchase orders, reorder point, days of cover, the last 14 daily snapshots, stockout days and the age of the oldest unit.", {"product_id": "UUID (or inventory_id)", "inventory_id": "UUID (optional)"}, domain=D)
def get_inventory_status(db: Session, product_id: Any = None, inventory_id: Any = None) -> dict[str, Any]:
    db = _require_db(db)
    t = Q.today(db)
    if inventory_id:
        inv = db.get(Inventory, _uuid(inventory_id, "inventory_id"))
        if inv is None:
            raise ToolError("NOT_FOUND", "inventory record not found")
        pids = [inv.product_id]
    else:
        pids = [_product(db, product_id).id]
    w14 = Q.window(t, 14)
    items = []
    for pos in Q.stock_positions(db, product_ids=pids):
        if inventory_id and pos.inventory.id != inv.id:
            continue
        series = Q.stock_series(db, pos.inventory.id, t - timedelta(days=70), t)
        normal = [s.quantity_available for s in series if s.snapshot_date < w14[0]]
        sold14 = sum(s.outbound_units for s in series if s.snapshot_date >= w14[0])
        ins14 = sum(1 for s in series if s.snapshot_date >= w14[0] and (s.quantity_available > 0 or s.outbound_units > 0))
        v = sold14 / max(7, ins14) if sold14 else 0.0
        age, layers = Q.stock_age(db, pos.product.id, pos.store.id if pos.store else None, pos.on_hand, t)
        items.append({
            "inventory_id": str(pos.inventory.id), "store": pos.store.name if pos.store else None, "store_id": str(pos.store.id) if pos.store else None, "on_hand": pos.on_hand, "on_order": pos.on_order, "reorder_point": pos.inventory.reorder_point, "max_stock": pos.inventory.max_stock,
            "unit_cost": float(pos.inventory.unit_value), "stock_value": round(pos.value, 2), "holding_cost_per_unit_month": float(pos.inventory.holding_cost_per_unit_month),
            "units_per_in_stock_day_14d": round(v, 3), "days_of_cover": round(pos.on_hand / v, 1) if v else None, "normal_on_hand_avg": round(statistics.fmean(normal), 1) if normal else None,
            "stockout_days_14d": [{"snapshot_id": str(s.id), "date": s.snapshot_date.isoformat()} for s in series if s.snapshot_date >= w14[0] and s.quantity_available == 0],
            "snapshots_14d": [{"id": str(s.id), "date": s.snapshot_date.isoformat(), "on_hand": s.quantity_available, "received": s.inbound_units, "sold": s.outbound_units} for s in series if s.snapshot_date >= w14[0]],
            "open_purchase_orders": [{**po, "order_date": po["order_date"].isoformat(), "expected_date": po["expected_date"].isoformat(), "days_overdue": max(0, (t - po["expected_date"]).days)} for po in pos.open_pos],
            "stock_age_days": age, "receipts": [{"delivery_id": r["delivery_id"], "date": r["delivered_date"].isoformat(), "units": r["units"], "po_number": r["po_number"], "notes": r["notes"]} for r in layers[:6]],
            "supplier_id": str(pos.supplier.id) if pos.supplier else None, "supplier": pos.supplier.name if pos.supplier else None,
        })
    return {"items": items, "count": len(items)}


@tool("get_sales_history", "Daily units and revenue for a product, a category or a city zone over 7-120 days.", {"product_id": "UUID (optional)", "category": "category name (optional)", "region": "zone, e.g. 'South Bengaluru' (optional)", "days": "7-120 (default 28)"}, domain=D)
def get_sales_history(db: Session, product_id: Any = None, category: str | None = None, region: str | None = None, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    days = _int(days, "days", 28, 7, 120)
    t = Q.today(db)
    start, end = Q.window(t, days)
    q = select(Order.order_date, func.sum(OrderItem.quantity), func.sum(OrderItem.line_revenue)).join(Order, Order.id == OrderItem.order_id).where(Order.order_date >= start, Order.order_date <= end)
    if product_id:
        q = q.where(OrderItem.product_id == _product(db, product_id).id)
    if category:
        q = q.join(Product, Product.id == OrderItem.product_id).where(Product.category == _category(db, category))
    if region:
        q = q.where(Order.region == str(region))
    rows = {d: (int(u), float(r)) for d, u, r in db.execute(q.group_by(Order.order_date)).all()}
    series = [{"date": (start + timedelta(days=i)).isoformat(), "units": rows.get(start + timedelta(days=i), (0, 0.0))[0], "revenue": round(rows.get(start + timedelta(days=i), (0, 0.0))[1], 2)} for i in range(days)]
    return {"series": series, "units": sum(x["units"] for x in series), "revenue": round(sum(x["revenue"] for x in series), 2), "count": len(series)}


@tool("get_supplier_history", "Delivery record of a supplier: expected vs actual dates, lateness, observed lead time (recent vs normal), open and overdue purchase orders, dependent products.", {"supplier_id": "UUID", "days": "30-180 (default 120)"}, domain=D)
def get_supplier_history(db: Session, supplier_id: Any, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    s = _supplier(db, supplier_id)
    days = _int(days, "days", 120, 30, 180)
    t = Q.today(db)
    rows = Q.deliveries(db, s.id, since=t - timedelta(days=days))
    recent = [r for r in rows if r["delivered_date"] >= t - timedelta(days=45)]
    older = [r for r in rows if r["delivered_date"] < t - timedelta(days=45)]
    last6 = rows[:6]
    open_pos = Q.open_purchase_orders(db, s.id)
    products = Q.supplier_products(db, s.id)

    def late_share(rs):
        return round(sum(1 for r in rs if not r["on_time"]) / len(rs), 3) if rs else None

    def lead(rs):
        return round(statistics.fmean(r["lead_days"] for r in rs), 2) if rs else None

    late = [r for r in last6 if not r["on_time"]]
    po_units = db.execute(select(PurchaseOrder.order_date, PurchaseOrder.total_units).where(PurchaseOrder.supplier_id == s.id, PurchaseOrder.order_date >= t - timedelta(days=days), PurchaseOrder.notes.is_distinct_from("Opening stock"))).all()
    po_recent = sum(u for d, u in po_units if d >= t - timedelta(days=45)) / 45
    po_normal = sum(u for d, u in po_units if d < t - timedelta(days=45)) / max(1, days - 45)
    return {
        "supplier": {"id": str(s.id), "name": s.name, "supplies": s.component_type, "city": s.location, "quoted_lead_days": s.baseline_lead_time_days},
        "deliveries": [{**r, "order_date": r["order_date"].isoformat(), "expected_date": r["expected_date"].isoformat(), "delivered_date": r["delivered_date"].isoformat()} for r in rows[:20]],
        "last_6": {"late": len(late), "considered": len(last6), "average_delay_days_when_late": round(statistics.fmean(r["delay_days"] for r in late), 2) if late else 0.0},
        "recent": {"window_days": 45, "deliveries": len(recent), "late_share": late_share(recent), "lead_days": lead(recent)},
        "normal": {"deliveries": len(older), "late_share": late_share(older), "lead_days": lead(older)},
        "open_purchase_orders": [{"purchase_order_id": str(po.id), "po_number": po.po_number, "order_date": po.order_date.isoformat(), "expected_date": po.expected_date.isoformat(), "days_overdue": max(0, (t - po.expected_date).days), "units": po.total_units} for po in open_pos],
        "ordered_units_per_day": {"recent": round(po_recent, 2), "normal": round(po_normal, 2)},
        "dependent_product_ids": [str(p.id) for p in products], "dependent_products": len(products),
        "supplier_delivery_ids": [r["delivery_id"] for r in rows[:12]], "count": len(rows),
    }


@tool("get_purchase_orders", "Purchase orders for a product or a supplier (status, dates, units, cost).", {"product_id": "UUID (optional)", "supplier_id": "UUID (optional)", "status": "OPEN | RECEIVED (optional)", "days": "7-180 (default 90)"}, domain=D)
def get_purchase_orders(db: Session, product_id: Any = None, supplier_id: Any = None, status: str | None = None, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    if not product_id and not supplier_id:
        raise ToolError("MISSING_PARAM", "product_id or supplier_id is required")
    if status and status not in ("OPEN", "RECEIVED", "PARTIALLY_RECEIVED", "CANCELLED"):
        raise ToolError("INVALID_PARAM", "status must be OPEN, RECEIVED, PARTIALLY_RECEIVED or CANCELLED")
    days = _int(days, "days", 90, 7, 180)
    t = Q.today(db)
    q = select(PurchaseOrder).where(PurchaseOrder.order_date >= t - timedelta(days=days))
    if supplier_id:
        q = q.where(PurchaseOrder.supplier_id == _supplier(db, supplier_id).id)
    if status:
        q = q.where(PurchaseOrder.status == status)
    pos = db.scalars(q.order_by(PurchaseOrder.order_date.desc())).all()
    pid = _product(db, product_id).id if product_id else None
    out = []
    for po in pos:
        items = [i for i in po.items if pid is None or i.product_id == pid]
        if not items:
            continue
        out.append({"purchase_order_id": str(po.id), "po_number": po.po_number, "status": po.status.value, "order_date": po.order_date.isoformat(), "expected_date": po.expected_date.isoformat(), "units": sum(i.quantity for i in items), "cost": round(sum(float(i.line_cost) for i in items), 2), "notes": po.notes, "delivered": [d.delivered_date.isoformat() for d in po.deliveries]})
    return {"purchase_orders": out[:50], "count": len(out)}


@tool("get_customer_orders", "Who is buying: orders, distinct customers, new vs returning customers, segment mix and the share bought by the single largest customer.", {"product_id": "UUID (optional)", "category": "category name (optional)", "region": "zone (optional)", "days": "7-90 (default 28)"}, domain=D)
def get_customer_orders(db: Session, product_id: Any = None, category: str | None = None, region: str | None = None, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    days = _int(days, "days", 28, 7, 90)
    t = Q.today(db)

    def window_stats(start: date, end: date) -> dict[str, Any]:
        q = select(Order.id, Order.customer_id, Customer.segment, Customer.customer_since, Order.order_date, func.sum(OrderItem.quantity)).join(OrderItem, OrderItem.order_id == Order.id).join(Customer, Customer.id == Order.customer_id).where(Order.order_date >= start, Order.order_date <= end)
        if product_id:
            q = q.where(OrderItem.product_id == _product(db, product_id).id)
        if category:
            q = q.join(Product, Product.id == OrderItem.product_id).where(Product.category == _category(db, category))
        if region:
            q = q.where(Order.region == str(region))
        rows = db.execute(q.group_by(Order.id, Order.customer_id, Customer.segment, Customer.customer_since, Order.order_date)).all()
        units = sum(int(r[5]) for r in rows)
        by_c: dict[Any, int] = {}
        seg: dict[str, int] = {}
        new = 0
        for r in rows:
            by_c[r[1]] = by_c.get(r[1], 0) + int(r[5])
            k = r[2].value if hasattr(r[2], "value") else r[2]
            seg[k] = seg.get(k, 0) + int(r[5])
            new += 1 if r[3] >= r[4] else 0
        top = max(by_c.values()) if by_c else 0
        return {"orders": len(rows), "units": units, "customers": len(by_c), "new_customer_orders": new, "new_customer_share": round(new / len(rows), 3) if rows else None, "top_customer_share": round(top / units, 3) if units else None, "segment_units": seg, "order_ids": [str(r[0]) for r in rows[:40]]}

    rw, pw = Q.window(t, days), Q.window(t, days, end_offset=days)
    rec, prev = window_stats(*rw), window_stats(*pw)
    return {"recent": {**rec, "window": [rw[0].isoformat(), rw[1].isoformat()]}, "previous": {**prev, "window": [pw[0].isoformat(), pw[1].isoformat()]}, "customers_change_pct": round((rec["customers"] - prev["customers"]) / prev["customers"] * 100, 1) if prev["customers"] else None, "count": rec["orders"]}


@tool("get_customer_segments", "Units bought by each customer segment for a category (optionally one zone), recent 28 days vs the 28 days before.", {"category": "category name", "region": "zone (optional)"}, domain=D)
def get_customer_segments(db: Session, category: str, region: str | None = None) -> dict[str, Any]:
    db = _require_db(db)
    cat = _category(db, category)
    t = Q.today(db)
    r, p = Q.sales_by_segment(db, *Q.window(t, 28), category=cat, region=region), Q.sales_by_segment(db, *Q.window(t, 28, end_offset=28), category=cat, region=region)
    tr, tp = sum(v.units for v in r.values()), sum(v.units for v in p.values())
    rows = [{"segment": k, "units_recent": r.get(k, Q.SalesStats()).units, "units_previous": p.get(k, Q.SalesStats()).units, "share_recent": round(r.get(k, Q.SalesStats()).units / tr, 3) if tr else None, "share_previous": round(p.get(k, Q.SalesStats()).units / tp, 3) if tp else None} for k in sorted(set(r) | set(p))]
    return {"segments": rows, "count": len(rows)}


@tool("get_pricing_history", "Price list changes of a product: selling price, unit cost and MRP over time, with the reason recorded.", {"product_id": "UUID"}, domain=D)
def get_pricing_history(db: Session, product_id: Any) -> dict[str, Any]:
    db = _require_db(db)
    p = _product(db, product_id)
    rows = db.scalars(select(PricingHistory).where(PricingHistory.product_id == p.id).order_by(PricingHistory.effective_date)).all()
    return {"product_id": str(p.id), "current": {"selling_price": float(p.selling_price), "unit_cost": float(p.unit_cost), "mrp": float(p.mrp) if p.mrp else None}, "changes": [{"pricing_history_id": str(r.id), "date": r.effective_date.isoformat(), "selling_price": float(r.selling_price), "unit_cost": float(r.unit_cost), "reason": r.reason.value, "note": r.note} for r in rows], "count": len(rows)}


@tool("get_discount_history", "Discounts given on a product or category: discount rate recent vs normal, share of counter (ad hoc) discounts and promotions that were active.", {"product_id": "UUID (optional)", "category": "category name (optional)", "days": "7-60 (default 21)"}, domain=D)
def get_discount_history(db: Session, product_id: Any = None, category: str | None = None, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    if not product_id and not category:
        raise ToolError("MISSING_PARAM", "product_id or category is required")
    days = _int(days, "days", 21, 7, 60)
    t = Q.today(db)
    rw, nw = Q.window(t, days), Q.window(t, 56, end_offset=days)
    pid = _product(db, product_id).id if product_id else None
    cat = _category(db, category) if category else None
    rec, nor = Q.sales_total(db, *rw, product_id=pid, category=cat), Q.sales_total(db, *nw, product_id=pid, category=cat)
    q = select(Discount).where(Discount.start_date <= rw[1], Discount.end_date >= rw[0])
    promos = [d for d in db.scalars(q).all() if d.scope.value != "AD_HOC" and (d.product_id == pid if pid else True)]
    adhoc = db.execute(select(func.count(OrderItem.id)).join(Order, Order.id == OrderItem.order_id).join(Discount, Discount.id == OrderItem.discount_id).where(Order.order_date >= rw[0], Order.order_date <= rw[1], Discount.scope == "AD_HOC", *( [OrderItem.product_id == pid] if pid else []))).scalar() or 0
    return {"recent": {**_stats(rec, days), "window": [rw[0].isoformat(), rw[1].isoformat()], "counter_discount_lines": int(adhoc)}, "normal": {**_stats(nor, 56), "window": [nw[0].isoformat(), nw[1].isoformat()]}, "active_promotions": [{"discount_id": str(d.id), "name": d.name, "pct": d.discount_pct, "scope": d.scope.value, "start": d.start_date.isoformat(), "end": d.end_date.isoformat()} for d in promos], "count": rec.lines}


@tool("get_financial_metrics", "Daily revenue, cost of goods, gross margin, discounts, delivery costs and profit for the business.", {"days": "7-120 (default 28)"}, domain=D)
def get_financial_metrics(db: Session, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    days = _int(days, "days", 28, 7, 120)
    t = Q.today(db)
    start, end = Q.window(t, days)
    rows = db.scalars(select(FinancialMetric).where(FinancialMetric.metric_date >= start, FinancialMetric.metric_date <= end).order_by(FinancialMetric.metric_date)).all()
    rev, cost = sum(float(r.revenue) for r in rows), sum(float(r.cost) for r in rows)
    return {"revenue": round(rev, 2), "cogs": round(cost, 2), "gross_margin_rate": round((rev - cost) / rev, 4) if rev else None, "discounts": round(sum(float(r.discount_total) for r in rows), 2), "fulfilment": round(sum(float(r.fulfilment_cost) for r in rows), 2), "operating_cost": round(sum(float(r.operating_cost) for r in rows), 2), "profit": round(sum(float(r.profit) for r in rows), 2), "daily": [{"date": r.metric_date.isoformat(), "revenue": float(r.revenue), "profit": float(r.profit), "orders": r.orders_count} for r in rows], "financial_metric_ids": [str(r.id) for r in rows], "count": len(rows)}


@tool("get_expense_history", "Business expenses by category (rent, salaries, utilities, marketing, delivery) over 7-120 days.", {"days": "7-120 (default 60)"}, domain=D)
def get_expense_history(db: Session, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    days = _int(days, "days", 60, 7, 120)
    t = Q.today(db)
    start, end = Q.window(t, days)
    rows = db.scalars(select(Expense).where(Expense.expense_date >= start, Expense.expense_date <= end)).all()
    by: dict[str, float] = {}
    for r in rows:
        by[r.category.value] = by.get(r.category.value, 0.0) + float(r.amount)
    return {"by_category": {k: round(v, 2) for k, v in sorted(by.items())}, "total": round(sum(by.values()), 2), "expense_ids": [str(r.id) for r in rows[:50]], "count": len(rows)}


@tool("get_category_performance", "A category's units, revenue and margin, recent 28 days vs the 28 days before, with its top products.", {"category": "category name"}, domain=D)
def get_category_performance(db: Session, category: str) -> dict[str, Any]:
    db = _require_db(db)
    cat = _category(db, category)
    t = Q.today(db)
    rw, pw = Q.window(t, 28), Q.window(t, 28, end_offset=28)
    rec, prev = Q.sales_total(db, *rw, category=cat), Q.sales_total(db, *pw, category=cat)
    prods = list(db.scalars(select(Product).where(Product.category == cat)).all())
    by = Q.sales_by_product(db, *rw, product_ids=[p.id for p in prods])
    top = sorted(prods, key=lambda p: -by.get(p.id, Q.SalesStats()).revenue)[:8]
    return {"category": cat, "recent": _stats(rec, 28), "previous": _stats(prev, 28), "units_change_pct": round((rec.units - prev.units) / prev.units * 100, 1) if prev.units else None, "top_products": [{"product_id": str(p.id), "name": p.name, "units": by.get(p.id, Q.SalesStats()).units, "revenue": round(by.get(p.id, Q.SalesStats()).revenue, 2)} for p in top], "product_ids": [str(p.id) for p in prods], "count": len(prods)}


@tool("get_regional_performance", "Units of a category bought by customers in each city zone, recent 28 days vs the 28 days before.", {"category": "category name (optional; all categories when omitted)"}, domain=D)
def get_regional_performance(db: Session, category: str | None = None) -> dict[str, Any]:
    db = _require_db(db)
    cat = _category(db, category) if category else None
    t = Q.today(db)
    rw, pw = Q.window(t, 28), Q.window(t, 28, end_offset=28)
    r, p = Q.sales_by_category_region(db, *rw), Q.sales_by_category_region(db, *pw)
    zones: dict[str, dict[str, int]] = {}
    for (c, z), st in r.items():
        if cat is None or c == cat:
            zones.setdefault(z, {"units_recent": 0, "units_previous": 0, "lines_recent": 0, "lines_previous": 0})
            zones[z]["units_recent"] += st.units
            zones[z]["lines_recent"] += st.lines
    for (c, z), st in p.items():
        if cat is None or c == cat:
            zones.setdefault(z, {"units_recent": 0, "units_previous": 0, "lines_recent": 0, "lines_previous": 0})
            zones[z]["units_previous"] += st.units
            zones[z]["lines_previous"] += st.lines
    rows = [{"region": z, **v, "change_pct": round((v["units_recent"] - v["units_previous"]) / v["units_previous"] * 100, 1) if v["units_previous"] else None} for z, v in sorted(zones.items())]
    return {"category": cat, "zones": rows, "count": len(rows)}


@tool("get_business_baseline", "What is normal for this merchant: weekday sales ranges, weekly revenue, category margins, supplier delivery performance, repeat-purchase rate and product velocity, each with its window.", {}, domain=D)
def get_business_baseline(db: Session) -> dict[str, Any]:
    db = _require_db(db)
    data = B.compute(db)
    return {**data, "count": len(data["weekday_sales"])}


@tool("get_competitor_prices", "Weekly online price checks for a product or category (SYNTHETIC market signals; lower reliability than the merchant's own records).", {"product_id": "UUID (optional)", "category": "category name (optional)", "days": "14-120 (default 56)"}, domain=D)
def get_competitor_prices(db: Session, product_id: Any = None, category: str | None = None, days: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    days = _int(days, "days", 56, 14, 120)
    t = Q.today(db)
    q = select(CompetitorSignal).where(CompetitorSignal.signal_date >= t - timedelta(days=days)).order_by(CompetitorSignal.signal_date)
    rows = db.scalars(q).all()
    if product_id:
        pid = str(_product(db, product_id).id)
        rows = [r for r in rows if (r.details or {}).get("product_id") == pid]
    elif category:
        rows = [r for r in rows if r.product_category == _category(db, category)]
    gaps = [((r.details or {}).get("observed_price", 0) - (r.details or {}).get("our_price", 0)) / (r.details or {}).get("our_price", 1) * 100 for r in rows if (r.details or {}).get("our_price")]
    return {"signals": [{"competitor_signal_id": str(r.id), "date": r.signal_date.isoformat(), "description": r.description, "price_change_pct": r.price_change_pct, "is_synthetic": r.is_synthetic} for r in rows[-12:]], "recent_price_gap_pct": round(statistics.fmean(gaps[-4:]), 2) if gaps else None, "count": len(rows)}


@tool("search_business_records", "Search products, suppliers and customers by name, SKU or category.", {"query": "text (min 2 chars)", "entity_types": "optional list of product|supplier|customer", "limit": "1-50 (default 20)"}, domain=D)
def search_business_records(db: Session, query: str, entity_types: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
    db = _require_db(db)
    if not query or len(str(query).strip()) < 2:
        raise ToolError("INVALID_PARAM", "query must be at least 2 characters")
    limit = _int(limit, "limit", 20, 1, 50)
    types = [t.lower() for t in (entity_types or ["product", "supplier", "customer"])]
    if set(types) - {"product", "supplier", "customer"}:
        raise ToolError("INVALID_PARAM", "entity_types must be product, supplier or customer")
    pattern = f"%{str(query).strip()}%"
    out: list[dict[str, Any]] = []
    if "product" in types:
        out += [{"entity_type": "product", "id": str(p.id), "label": p.name, "code": p.sku, "extra": {"category": p.category}} for p in db.scalars(select(Product).where(or_(Product.name.ilike(pattern), Product.sku.ilike(pattern), Product.category.ilike(pattern))).limit(limit)).all()]
    if "supplier" in types:
        out += [{"entity_type": "supplier", "id": str(s.id), "label": s.name, "code": None, "extra": {"supplies": s.component_type}} for s in db.scalars(select(Supplier).where(or_(Supplier.name.ilike(pattern), Supplier.component_type.ilike(pattern))).limit(limit)).all()]
    if "customer" in types:
        out += [{"entity_type": "customer", "id": str(c.id), "label": c.name, "code": None, "extra": {"region": c.region}} for c in db.scalars(select(Customer).where(Customer.name.ilike(pattern)).limit(limit)).all()]
    return {"query": query, "results": out[:limit], "count": min(len(out), limit)}


# ---- calculations (Impact Engine only; no database) ----------------------------------------------------------
@tool("calculate_stockout_loss", "Sales lost while out of stock (Impact Engine).", {"units_per_day": "number", "days_without_stock": "number", "unit_price": "number", "unit_margin": "number"}, domain=D)
def calculate_stockout_loss(db: Session | None, units_per_day: Any, days_without_stock: Any, unit_price: Any, unit_margin: Any) -> dict[str, Any]:
    return _E.stockout_loss(_float(units_per_day, "units_per_day"), _float(days_without_stock, "days_without_stock"), _float(unit_price, "unit_price"), _float(unit_margin, "unit_margin")).as_dict()


@tool("calculate_margin_loss", "Margin given up at a lower unit margin over the recurring horizon (Impact Engine).", {"units_per_day": "number", "normal_unit_margin": "number", "current_unit_margin": "number"}, domain=D)
def calculate_margin_loss(db: Session | None, units_per_day: Any, normal_unit_margin: Any, current_unit_margin: Any) -> dict[str, Any]:
    return _E.margin_loss(_float(units_per_day, "units_per_day"), _float(normal_unit_margin, "normal_unit_margin"), _float(current_unit_margin, "current_unit_margin")).as_dict()


@tool("calculate_inventory_capital", "Cash locked in surplus stock and its holding cost (Impact Engine).", {"excess_units": "number", "unit_cost": "number", "holding_cost_per_unit_month": "number"}, domain=D)
def calculate_inventory_capital(db: Session | None, excess_units: Any, unit_cost: Any, holding_cost_per_unit_month: Any) -> dict[str, Any]:
    return _E.inventory_capital_locked(_float(excess_units, "excess_units"), _float(unit_cost, "unit_cost"), _float(holding_cost_per_unit_month, "holding_cost_per_unit_month")).as_dict()


@tool("calculate_demand_opportunity", "Extra revenue from higher demand over 30 days (Impact Engine).", {"incremental_units_per_day": "number", "unit_price": "number", "unit_margin": "number"}, domain=D)
def calculate_demand_opportunity(db: Session | None, incremental_units_per_day: Any, unit_price: Any, unit_margin: Any) -> dict[str, Any]:
    return _E.demand_opportunity_value(_float(incremental_units_per_day, "incremental_units_per_day"), _float(unit_price, "unit_price"), _float(unit_margin, "unit_margin")).as_dict()


@tool("calculate_reorder_quantity", "Units to order so stock covers lead time + review period + safety days at the given demand (not money).", {"units_per_day": "number", "lead_days": "number", "on_hand": "integer", "on_order": "integer", "cover_days": "days of cover after the lead time (default 14)"}, domain=D)
def calculate_reorder_quantity(db: Session | None, units_per_day: Any, lead_days: Any, on_hand: Any, on_order: Any = 0, cover_days: Any = 14) -> dict[str, Any]:
    v, lead = _float(units_per_day, "units_per_day"), _float(lead_days, "lead_days")
    oh, oo, cover = _float(on_hand, "on_hand"), _float(on_order, "on_order"), _float(cover_days, "cover_days")
    if v < 0 or lead < 0 or oh < 0 or oo < 0 or cover < 0:
        raise ToolError("INVALID_PARAM", "inputs must be non-negative")
    target = v * (lead + cover + 3)
    qty = max(0, math.ceil(target - oh - oo))
    return {"quantity": qty, "formula": "quantity = ceil(units_per_day * (lead_days + cover_days + 3 safety days) - on_hand - on_order)", "inputs": {"units_per_day": round(v, 2), "lead_days": round(lead, 1), "cover_days": cover, "on_hand": oh, "on_order": oo}, "target_units": round(target, 1)}
