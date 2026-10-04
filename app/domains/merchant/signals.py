"""Merchant signal collection: a finding becomes a flat set of named, sourced observations.

Every value comes from a merchant tool call (recorded in the investigation trace) and keeps the ids of the records
behind it (order lines, inventory snapshots, purchase orders, deliveries, price-list entries, promotions), so each
evidence item built on it is traceable: finding -> signal -> evidence -> hypothesis -> conclusion.

Signals are named after what a merchant would ask: is demand up, is stock down, is the supplier late, did the cost
change, are we discounting more, who is buying.
"""
from __future__ import annotations

import statistics
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.domains.merchant import queries as Q
from app.domains.merchant.language import plural
from app.investigation.signals import SignalSet, TraceFn, _call
from app.models import BusinessRisk, Opportunity, Product, Supplier

PRODUCT_RISKS = {"STOCKOUT_RISK", "DEMAND_SUPPLY_MISMATCH", "MARGIN_DECLINE", "PRICE_ANOMALY", "DISCOUNT_LEAKAGE", "EXCESS_INVENTORY", "SLOW_MOVING_INVENTORY", "DEMAND_DECLINE"}
SUPPLIER_RISKS = {"SUPPLIER_DELAY", "SUPPLIER_RELIABILITY"}
BUSINESS_RISKS = {"REVENUE_DECLINE", "REPEAT_CUSTOMER_DECLINE"}


def _ts(d: date | str | None) -> datetime | None:
    if d is None:
        return None
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return datetime.combine(d, time(20, 0))


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return round((new - old) / old * 100, 1)


def collect_merchant_signals(db: Session, subject: BusinessRisk | Opportunity, trace: TraceFn) -> SignalSet:
    ev = subject.evidence or {}
    if isinstance(subject, BusinessRisk):
        kind, stype = "risk", subject.risk_type.value
    else:
        kind, stype = "opportunity", subject.opportunity_type.value
    S = SignalSet(kind, stype, {"subject_id": str(subject.id), "domain": "merchant", "entity_type": getattr(subject, "entity_type", None), "entity_name": getattr(subject, "entity_name", None), "product_id": ev.get("product_id"), "inventory_id": ev.get("inventory_id"), "supplier_id": ev.get("supplier_id"), "category": ev.get("category"), "region": ev.get("region") or getattr(subject, "region", None)})
    S.domain = "merchant"
    t = Q.today(db)
    S.today = t
    product = db.get(Product, uuid.UUID(ev["product_id"])) if ev.get("product_id") else None
    if product is not None:
        _product_signals(db, S, trace, product, ev.get("inventory_id"), t)
    if stype in SUPPLIER_RISKS:
        sup = db.get(Supplier, subject.entity_id) if getattr(subject, "entity_id", None) else None
        if sup is not None:
            _supplier_signals(db, S, trace, sup, ev, t)
    if stype in ("CATEGORY_OPPORTUNITY", "REGIONAL_OPPORTUNITY") or (stype == "DEMAND_DECLINE" and product is None):
        _category_signals(db, S, trace, ev.get("category") or getattr(subject, "entity_name", None), ev.get("region") or getattr(subject, "region", None), t)
    if stype in BUSINESS_RISKS:
        _business_signals(db, S, trace, t)
    return S


# ---- product -------------------------------------------------------------------------------------------------
def _product_signals(db: Session, S: SignalSet, trace: TraceFn, p: Product, inventory_id: str | None, t: date) -> None:
    recent_ts = _ts(t - timedelta(days=1))
    sales = _call(S, trace, "get_product_sales", db, product_id=p.id, days=14, compare_days=56)
    v_now = 0.0
    if sales:
        r, n = sales["recent"], sales["normal"]
        ids = sales["order_item_ids"]
        v_now, v_norm = r["units_per_in_stock_day"], n["units_per_in_stock_day"]
        S.add("units_per_day_recent", v_now, "order_item", ids, "units/day", v_norm, recent_ts, f"Selling {v_now:.1f} units a day on in-stock days (normal {v_norm:.1f})")
        if sales["demand_change_pct"] is not None:
            S.add("demand_change_pct", sales["demand_change_pct"], "order_item", ids, "%", 0.0, recent_ts, f"Demand changed {sales['demand_change_pct']:+.0f}% versus the previous 8 weeks ({v_norm:.1f} to {v_now:.1f} units a day)", recent_window=r["window"], normal_window=n["window"])
        cust_r, cust_n = r["customers"], n["customers"] * 14 / 56
        if cust_n >= 1:
            S.add("distinct_customers_change_pct", _pct(cust_r, cust_n), "order_item", ids, "%", 0.0, recent_ts, f"{cust_r} different customers bought it in 14 days (normal about {cust_n:.0f})")
        if r["units"]:
            share = round(r["top_customer_units"] / r["units"], 3)
            S.add("top_customer_share", share, "order_item", ids, "share", None, recent_ts, f"The largest single customer bought {share * 100:.0f}% of recent units")
            S.add("online_share_change_pts", round((r["online_units"] / r["units"] - (n["online_units"] / n["units"] if n["units"] else 0)) * 100, 1), "order_item", ids, "points", 0.0, recent_ts, f"Online / WhatsApp share of units: {r['online_units'] / r['units'] * 100:.0f}% (normal {n['online_units'] / n['units'] * 100 if n['units'] else 0:.0f}%)")
        for key, label, unit in (("unit_price", "Average selling price", "₹"), ("unit_cost", "Average unit cost", "₹"), ("unit_margin", "Margin per unit", "₹")):
            if r[key] is not None and n[key]:
                S.add(f"{key}_change_pct", _pct(r[key], n[key]), "order_item", ids, "%", 0.0, recent_ts, f"{label} ₹{r[key]:,.0f} against ₹{n[key]:,.0f} normally", recent=r[key], normal=n[key])
        if r["discount_rate"] is not None and n["discount_rate"] is not None:
            S.add("discount_rate_change_pts", round((r["discount_rate"] - n["discount_rate"]) * 100, 2), "order_item", ids, "points", 0.0, recent_ts, f"Discount averaged {r['discount_rate'] * 100:.1f}% of the shelf price (normal {n['discount_rate'] * 100:.1f}%)")
    sup_info = None
    if p.supplier_id:
        sup_info = _call(S, trace, "get_supplier_history", db, supplier_id=p.supplier_id, days=120)
        if sup_info:
            _supplier_delivery_signals(S, sup_info, t, prefix="supplier_")
    inv = _call(S, trace, "get_inventory_status", db, product_id=p.id)
    if inv and inv["items"]:
        item = next((i for i in inv["items"] if i["inventory_id"] == inventory_id), inv["items"][0])
        snap_ids = [s["id"] for s in item["snapshots_14d"]]
        if item["days_of_cover"] is not None:
            S.add("days_of_cover", item["days_of_cover"], "inventory_snapshot", [item["inventory_id"]] + snap_ids, "days", None, recent_ts, f"{item['on_hand']} units on hand cover about {item['days_of_cover']:.1f} days of sales")
        elif item["on_hand"] == 0:
            S.add("days_of_cover", 0.0, "inventory_snapshot", [item["inventory_id"]] + snap_ids, "days", None, recent_ts, "Out of stock")
        if item["normal_on_hand_avg"]:
            S.add("stock_change_pct", _pct(item["on_hand"], item["normal_on_hand_avg"]), "inventory_snapshot", [item["inventory_id"]] + snap_ids, "%", 0.0, recent_ts, f"Stock on hand {item['on_hand']} units against a normal {item['normal_on_hand_avg']:.0f}")
        S.add("stockout_days_14", len(item["stockout_days_14d"]), "inventory_snapshot", [x["snapshot_id"] for x in item["stockout_days_14d"]] or snap_ids[-3:], "days", 0.0, recent_ts, f"Out of stock on {len(item['stockout_days_14d'])} of the last 14 days")
        pos = item["open_purchase_orders"]
        overdue = [po for po in pos if po["days_overdue"] > 0]
        S.add("open_po_overdue", bool(overdue), "purchase_order", [po["purchase_order_id"] for po in pos], None, None, recent_ts, (f"{plural(len(overdue), 'open purchase order')} {'is' if len(overdue) == 1 else 'are'} overdue (by up to {plural(max(po['days_overdue'] for po in overdue), 'day')})" if overdue else "No open purchase order is overdue"))
        S.add("no_open_po", not pos, "purchase_order", [po["purchase_order_id"] for po in pos] or [item["inventory_id"]], None, None, recent_ts, "No purchase order has been placed for the next delivery" if not pos else f"{plural(len(pos), 'purchase order')} on the way ({item['on_order']} units)")
        if overdue:
            S.add("overdue_days_max", max(po["days_overdue"] for po in overdue), "purchase_order", [po["purchase_order_id"] for po in overdue], "days", 0.0, recent_ts, f"The oldest open order is {max(po['days_overdue'] for po in overdue)} days late")
        if item["stock_age_days"] is not None:
            S.add("stock_age_days", item["stock_age_days"], "supplier_delivery", [r["delivery_id"] for r in item["receipts"]], "days", None, _ts(t - timedelta(days=item["stock_age_days"])), f"The oldest units on the shelf arrived {item['stock_age_days']} days ago")
        S.add("stock_value", item["stock_value"], "inventory", [item["inventory_id"]], "₹", None, recent_ts, f"Stock on hand is worth ₹{item['stock_value']:,.0f} at cost")
        lead = (sup_info or {}).get("recent", {}).get("lead_days") or ((sup_info or {}).get("supplier", {}).get("quoted_lead_days")) or 7
        if v_now > 0:
            need = v_now * (lead + 3.5)
            S.add("reorder_cover_ratio", round(item["reorder_point"] / need, 2) if need else None, "inventory", [item["inventory_id"]], "ratio", 1.0, recent_ts, f"Reorder level {item['reorder_point']} units against about {need:.0f} needed to cover the current lead time ({lead:.0f} days) at today's demand")
        receipts = item["receipts"]
        if receipts:
            _purchase_signals(db, S, trace, p, receipts, t)
    prices = _call(S, trace, "get_pricing_history", db, product_id=p.id)
    if prices:
        recent_changes = [c for c in prices["changes"] if c["reason"] != "INITIAL" and date.fromisoformat(c["date"]) >= t - timedelta(days=45)]
        ids = [c["pricing_history_id"] for c in prices["changes"]]
        before = [c for c in prices["changes"] if date.fromisoformat(c["date"]) < t - timedelta(days=45)]
        base = before[-1] if before else prices["changes"][0]
        cur = prices["current"]
        S.add("cost_list_change_pct", _pct(cur["unit_cost"], base["unit_cost"]), "pricing_history", ids, "%", 0.0, _ts(recent_changes[-1]["date"]) if recent_changes else recent_ts, f"Purchase cost ₹{cur['unit_cost']:,.0f} against ₹{base['unit_cost']:,.0f} six weeks ago" + (f" ({recent_changes[-1]['note']})" if recent_changes and recent_changes[-1].get("note") else ""))
        S.add("list_price_change_pct", _pct(cur["selling_price"], base["selling_price"]), "pricing_history", ids, "%", 0.0, _ts(recent_changes[-1]["date"]) if recent_changes else recent_ts, f"Shelf price ₹{cur['selling_price']:,.0f} against ₹{base['selling_price']:,.0f} six weeks ago")
        if recent_changes:
            passed = cur["selling_price"] != base["selling_price"]
            S.add("cost_increase_passed_on", passed, "pricing_history", ids, None, None, _ts(recent_changes[-1]["date"]), "The cost change was passed on to the shelf price" if passed else "The cost went up but the shelf price stayed the same")
    disc = _call(S, trace, "get_discount_history", db, product_id=p.id, days=21)
    if disc is not None:
        promos = disc["active_promotions"]
        S.add("promotion_active", bool(promos), "discount", [x["discount_id"] for x in promos] or [str(p.id)], None, None, recent_ts, (f"Promotion running: {promos[0]['name']} ({promos[0]['pct'] * 100:.0f}% off)" if promos else "No promotion was running on this product"))
    comp = _call(S, trace, "get_competitor_prices", db, product_id=p.id)
    if comp and comp["recent_price_gap_pct"] is not None:
        S.add("competitor_price_gap_pct", comp["recent_price_gap_pct"], "competitor_signal", [x["competitor_signal_id"] for x in comp["signals"]], "%", 0.0, recent_ts, f"Online listings are {comp['recent_price_gap_pct']:+.1f}% versus our price (synthetic market check)")
    cat = _call(S, trace, "get_category_performance", db, category=p.category)
    if cat and cat["units_change_pct"] is not None:
        S.add("category_demand_change_pct", cat["units_change_pct"], "order_item", cat["product_ids"][:20], "%", 0.0, recent_ts, f"{p.category} as a whole changed {cat['units_change_pct']:+.0f}% over 28 days")


def _purchase_signals(db: Session, S: SignalSet, trace: TraceFn, p: Product, receipts: list[dict[str, Any]], t: date) -> None:
    hist = _call(S, trace, "get_sales_history", db, product_id=p.id, days=120)
    if not hist:
        return
    series = {date.fromisoformat(x["date"]): x["units"] for x in hist["series"]}
    biggest = max(receipts, key=lambda r: r["units"])
    rd = date.fromisoformat(biggest["date"])
    before = [series.get(rd - timedelta(days=i), 0) for i in range(1, 29) if rd - timedelta(days=i) in series]
    after = [u for d, u in series.items() if d >= rd]
    v_before = sum(before) / len(before) if before else None
    v_after = sum(after) / len(after) if after else None
    others = [r["units"] for r in receipts if r is not biggest]
    ref_ids = [biggest["delivery_id"]]
    if v_before:
        S.add("purchase_cover_days", round(biggest["units"] / v_before), "supplier_delivery", ref_ids, "days", 30.0, _ts(rd), f"The largest delivery ({biggest['units']} units on {rd.isoformat()}, {biggest['po_number']}) was about {biggest['units'] / v_before:.0f} days of the demand at the time ({v_before:.2f} a day)")
        if v_after is not None:
            S.add("demand_change_since_purchase_pct", _pct(v_after, v_before), "order_item", ref_ids, "%", 0.0, _ts(t - timedelta(days=1)), f"Sales since that delivery average {v_after:.2f} a day against {v_before:.2f} before it")
    elif biggest["units"] >= 10:
        S.add("purchase_cover_days", 999, "supplier_delivery", ref_ids, "days", 30.0, _ts(rd), f"The largest delivery ({biggest['units']} units) arrived before any recorded sales")
    if others:
        ratio = round(biggest["units"] / max(1, statistics.median(others)), 1)
        S.add("receipt_vs_normal_order_ratio", ratio, "supplier_delivery", ref_ids + [r["delivery_id"] for r in receipts if r is not biggest][:4], "x", 1.0, _ts(rd), f"That delivery was {ratio:.1f}x the usual order size")
    elif biggest["units"] >= 10:
        S.add("receipt_vs_normal_order_ratio", 5.0, "supplier_delivery", ref_ids, "x", 1.0, _ts(rd), "No other delivery of comparable size was ever received")


# ---- supplier ------------------------------------------------------------------------------------------------
def _supplier_delivery_signals(S: SignalSet, info: dict[str, Any], t: date, prefix: str = "") -> None:
    ids = info["supplier_delivery_ids"]
    rts = _ts(t - timedelta(days=1))
    last6 = info["last_6"]
    rec, nor = info["recent"], info["normal"]
    name = info["supplier"]["name"]
    S.add(f"{prefix}late_deliveries_6", last6["late"], "supplier_delivery", ids[:6], "of 6", None, rts, f"{name}: {last6['late']} of the last {last6['considered']} deliveries arrived late")
    if rec["late_share"] is not None:
        S.add(f"{prefix}late_share_recent", rec["late_share"], "supplier_delivery", ids[:8], "share", nor["late_share"], rts, f"{name}: {rec['late_share'] * 100:.0f}% of deliveries in the last 45 days were late (normal {nor['late_share'] * 100 if nor['late_share'] is not None else 0:.0f}%)")
    if rec["lead_days"] is not None:
        S.add(f"{prefix}lead_change_days", round(rec["lead_days"] - info["supplier"]["quoted_lead_days"], 1), "supplier_delivery", ids[:8], "days", 0.0, rts, f"{name}: orders now take {rec['lead_days']:.1f} days to arrive against a quoted {info['supplier']['quoted_lead_days']} days")
    if not prefix:
        if rec["late_share"] is not None and nor["late_share"] is not None:
            S.add("on_time_change_pts", round((nor["late_share"] - rec["late_share"]) * 100, 1), "supplier_delivery", ids[:12], "points", 0.0, rts, f"On-time rate {100 - rec['late_share'] * 100:.0f}% in the last 45 days against {100 - nor['late_share'] * 100:.0f}% before")
        S.add("average_delay_when_late", last6["average_delay_days_when_late"], "supplier_delivery", ids[:6], "days", 0.0, rts, f"Late deliveries were {last6['average_delay_days_when_late']:.1f} days late on average")
        overdue = [po for po in info["open_purchase_orders"] if po["days_overdue"] > 0]
        S.add("overdue_pos", len(overdue), "purchase_order", [po["purchase_order_id"] for po in info["open_purchase_orders"]], "orders", 0.0, rts, f"{plural(len(overdue), 'open purchase order')} {'is' if len(overdue) == 1 else 'are'} past the expected date")
        o = info["ordered_units_per_day"]
        if o["normal"]:
            S.add("po_units_change_pct", _pct(o["recent"], o["normal"]), "purchase_order", [po["purchase_order_id"] for po in info["open_purchase_orders"]] or ids[:4], "%", 0.0, rts, f"Units ordered from {name}: {o['recent']:.1f} a day recently against {o['normal']:.1f} before")


def _supplier_signals(db: Session, S: SignalSet, trace: TraceFn, sup: Supplier, ev: dict[str, Any], t: date) -> None:
    info = _call(S, trace, "get_supplier_history", db, supplier_id=sup.id, days=120)
    if not info:
        return
    _supplier_delivery_signals(S, info, t)
    deps = ev.get("dependent_products", [])
    low = [d for d in deps if d.get("days_of_cover") is not None and d["days_of_cover"] < (info["recent"]["lead_days"] or info["supplier"]["quoted_lead_days"])]
    S.add("products_low_cover", len(low), "product", [d["product_id"] for d in low] or [d["product_id"] for d in deps][:5], "products", 0.0, _ts(t - timedelta(days=1)), f"{len(low)} of {len(deps)} dependent products have less stock than one current lead time")
    S.add("dependent_products", info["dependent_products"], "product", info["dependent_product_ids"][:20], "products", None, _ts(t - timedelta(days=1)), f"{info['dependent_products']} products in the range come from {sup.name}")


# ---- category / zone -----------------------------------------------------------------------------------------
def _category_signals(db: Session, S: SignalSet, trace: TraceFn, category: str | None, region: str | None, t: date) -> None:
    if not category:
        return
    rts = _ts(t - timedelta(days=1))
    cust = _call(S, trace, "get_customer_orders", db, category=category, region=region, days=28)
    ids = (cust["recent"]["order_ids"] + cust["previous"]["order_ids"][:10]) if cust else []
    reg = _call(S, trace, "get_regional_performance", db, category=category)
    if reg:
        zones = {z["region"]: z for z in reg["zones"]}
        tot_r = sum(z["units_recent"] for z in zones.values())
        tot_p = sum(z["units_previous"] for z in zones.values())
        S.add("category_demand_change_pct", _pct(tot_r, tot_p), "order", ids, "%", 0.0, rts, f"{category} overall: {tot_r} units in 28 days against {tot_p} before")
        if not region:
            S.add("other_zones_growth_pct", _pct(tot_r, tot_p), "order", ids, "%", 0.0, rts, f"{category} across all zones: {tot_r} units against {tot_p} before")
        if region and region in zones:
            z = zones[region]
            o_r, o_p = tot_r - z["units_recent"], tot_p - z["units_previous"]
            S.add("zone_category_growth_pct", z["change_pct"], "order", ids, "%", 0.0, rts, f"{category} bought by {region} customers: {z['units_recent']} units against {z['units_previous']} before ({z['change_pct']:+.0f}%)")
            S.add("other_zones_growth_pct", _pct(o_r, o_p), "order", ids, "%", 0.0, rts, f"{category} in the other zones together: {o_r} units against {o_p} before")
    if region:
        allc = _call(S, trace, "get_regional_performance", db)
        if allc:
            z = next((x for x in allc["zones"] if x["region"] == region), None)
            if z:
                S.add("zone_total_growth_pct", z["change_pct"], "order", ids, "%", 0.0, rts, f"All categories bought by {region} customers: {z['change_pct']:+.0f}% over 28 days")
    if cust:
        r, p = cust["recent"], cust["previous"]
        S.add("distinct_customers_growth_pct", cust["customers_change_pct"], "order", ids, "%", 0.0, rts, f"{r['customers']} different customers bought in the last 28 days against {p['customers']} before")
        if r["top_customer_share"] is not None:
            S.add("top_customer_share", r["top_customer_share"], "order", r["order_ids"][:10], "share", None, rts, f"The largest single customer bought {r['top_customer_share'] * 100:.0f}% of the units")
        if r["new_customer_share"] is not None and p["new_customer_share"] is not None:
            S.add("new_customer_share_change_pts", round((r["new_customer_share"] - p["new_customer_share"]) * 100, 1), "order", ids, "points", 0.0, rts, f"New customers placed {r['new_customer_share'] * 100:.0f}% of these orders (before {p['new_customer_share'] * 100:.0f}%)")
    seg = _call(S, trace, "get_customer_segments", db, category=category, region=region)
    if seg and seg["segments"]:
        rows = [x for x in seg["segments"] if x["share_recent"] is not None and x["share_previous"] is not None]
        if rows:
            top = max(rows, key=lambda x: x["share_recent"] - x["share_previous"])
            S.add("segment_share_change_pts", round((top["share_recent"] - top["share_previous"]) * 100, 1), "order", ids, "points", 0.0, rts, f"{top['segment'].replace('_', ' ').title()} went from {top['share_previous'] * 100:.0f}% to {top['share_recent'] * 100:.0f}% of units", segment=top["segment"])
    disc = _call(S, trace, "get_discount_history", db, category=category, days=28)
    if disc:
        r, n = disc["recent"], disc["normal"]
        if r["discount_rate"] is not None and n["discount_rate"] is not None:
            S.add("discount_rate_change_pts", round((r["discount_rate"] - n["discount_rate"]) * 100, 2), "order", ids, "points", 0.0, rts, f"Category discount {r['discount_rate'] * 100:.1f}% against {n['discount_rate'] * 100:.1f}% normally")
        S.add("promotion_active", bool(disc["active_promotions"]), "discount", [x["discount_id"] for x in disc["active_promotions"]] or ids[:3], None, None, rts, (f"Promotion running: {disc['active_promotions'][0]['name']}" if disc["active_promotions"] else "No promotion ran on this category"))
        if r["unit_price"] and n["unit_price"]:
            S.add("unit_price_change_pct", _pct(r["unit_price"], n["unit_price"]), "order", ids, "%", 0.0, rts, f"Average selling price ₹{r['unit_price']:,.0f} against ₹{n['unit_price']:,.0f}")
    comp = _call(S, trace, "get_competitor_prices", db, category=category)
    if comp and comp["recent_price_gap_pct"] is not None:
        S.add("competitor_price_gap_pct", comp["recent_price_gap_pct"], "competitor_signal", [x["competitor_signal_id"] for x in comp["signals"]], "%", 0.0, rts, f"Online listings are {comp['recent_price_gap_pct']:+.1f}% versus our prices (synthetic market check)")


# ---- whole business --------------------------------------------------------------------------------------------
def _business_signals(db: Session, S: SignalSet, trace: TraceFn, t: date) -> None:
    rts = _ts(t - timedelta(days=1))
    base = _call(S, trace, "get_business_baseline", db)
    if base:
        wk = base["weekly_revenue"]
        S.add("weekly_revenue_change_pct", wk["change_pct"], "financial_metric", [d["date"] for d in base["weekday_sales"]], "%", 0.0, rts, f"Last 7 days ₹{wk['last_7_days']:,.0f} against a normal week of ₹{wk['normal_weekly_mean']:,.0f}")
        rp = base["repeat_purchase"]
        if rp["recent_share"] is not None and rp["normal_share"] is not None:
            S.add("repeat_share_change_pts", round((rp["recent_share"] - rp["normal_share"]) * 100, 1), "order", [], "points", 0.0, rts, f"Repeat customers placed {rp['recent_share'] * 100:.0f}% of orders (normal {rp['normal_share'] * 100:.0f}%)")
        vel = base["product_velocity"]
        short = [v for v in vel if v["days_of_cover"] is not None and v["days_of_cover"] < 3]
        S.add("top_products_short_of_stock", len(short), "inventory", [v["product_id"] for v in short], "products", 0.0, rts, f"{len(short)} of the top {len(vel)} products have under 3 days of stock")
        declines = [v for v in vel if v["change_pct"] is not None and v["change_pct"] < -25]
        S.add("top_products_declining", len(declines), "order_item", [v["product_id"] for v in declines], "products", 0.0, rts, f"{len(declines)} of the top {len(vel)} products are selling at least 25% less")
    fin = _call(S, trace, "get_financial_metrics", db, days=28)
    prev = _call(S, trace, "get_financial_metrics", db, days=56)
    if fin and prev:
        orders_r = sum(d["orders"] for d in fin["daily"])
        orders_p = sum(d["orders"] for d in prev["daily"][: len(prev["daily"]) - len(fin["daily"])])
        rev_p = prev["revenue"] - fin["revenue"]
        S.add("orders_change_pct", _pct(orders_r, orders_p), "financial_metric", fin["financial_metric_ids"], "%", 0.0, rts, f"{orders_r} orders in the last 28 days against {orders_p} in the 28 days before")
        if orders_r and orders_p:
            S.add("average_order_value_change_pct", _pct(fin["revenue"] / orders_r, rev_p / orders_p), "financial_metric", fin["financial_metric_ids"], "%", 0.0, rts, f"Average bill ₹{fin['revenue'] / orders_r:,.0f} against ₹{rev_p / orders_p:,.0f}")
