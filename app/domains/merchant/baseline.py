"""Merchant baselines: what "normal" looks like for this business, computed from its own records.

Nothing here is hard-coded. Each baseline names its window, so a statement such as "Monday sales are 27% below the
normal Monday range of the previous 8 weeks" can be traced to the exact days that defined the range.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.merchant import queries as Q
from app.models import Supplier

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
BASELINE_WEEKS = 8


def _mean_std(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    return statistics.fmean(xs), (statistics.stdev(xs) if len(xs) > 1 else 0.0)


def weekday_sales(db: Session, t: date, weeks: int = BASELINE_WEEKS) -> list[dict[str, Any]]:
    """For each weekday: last week's revenue against the same weekday over the previous `weeks` weeks."""
    start = t - timedelta(days=7 * (weeks + 1))
    rev = Q.daily_revenue(db, start, t - timedelta(days=1))
    out = []
    for back in range(7, 0, -1):
        d = t - timedelta(days=back)
        hist = [rev.get(d - timedelta(days=7 * k), 0.0) for k in range(1, weeks + 1)]
        mean, std = _mean_std(hist)
        last = rev.get(d, 0.0)
        out.append({
            "weekday": WEEKDAYS[d.weekday()], "date": d.isoformat(), "revenue": round(last, 2), "normal_mean": round(mean, 2), "normal_low": round(max(0.0, mean - std), 2), "normal_high": round(mean + std, 2),
            "change_pct": round((last - mean) / mean * 100, 1) if mean else None, "below_normal_range": last < mean - std, "baseline_dates": [(d - timedelta(days=7 * k)).isoformat() for k in range(1, weeks + 1)],
        })
    return out


def weekly_revenue(db: Session, t: date, weeks: int = BASELINE_WEEKS) -> dict[str, Any]:
    rev = Q.daily_revenue(db, t - timedelta(days=7 * (weeks + 1)), t - timedelta(days=1))
    totals = [sum(rev.get(t - timedelta(days=7 * k + j), 0.0) for j in range(1, 8)) for k in range(0, weeks + 1)]
    last, hist = totals[0], totals[1:]
    mean, std = _mean_std(hist)
    return {"last_7_days": round(last, 2), "normal_weekly_mean": round(mean, 2), "normal_weekly_std": round(std, 2), "change_pct": round((last - mean) / mean * 100, 1) if mean else None, "z": round((last - mean) / std, 2) if std else 0.0, "weeks": weeks}


def category_margins(db: Session, t: date) -> list[dict[str, Any]]:
    rs, re_ = Q.window(t, 21)
    bs, be = Q.window(t, 7 * BASELINE_WEEKS, end_offset=21)
    recent, base = Q.sales_by_category(db, rs, re_), Q.sales_by_category(db, bs, be)
    out = []
    for cat in sorted(set(recent) | set(base)):
        r, b = recent.get(cat, Q.SalesStats()), base.get(cat, Q.SalesStats())
        out.append({"category": cat, "normal_margin_rate": round(b.margin_rate, 4) if b.margin_rate is not None else None, "recent_margin_rate": round(r.margin_rate, 4) if r.margin_rate is not None else None, "points_change": round((r.margin_rate - b.margin_rate) * 100, 2) if r.margin_rate is not None and b.margin_rate is not None else None, "recent_window": [rs.isoformat(), re_.isoformat()], "normal_window": [bs.isoformat(), be.isoformat()]})
    return out


def supplier_delivery(db: Session, t: date) -> list[dict[str, Any]]:
    out = []
    for s in db.scalars(select(Supplier).order_by(Supplier.name)).all():
        rows = Q.deliveries(db, s.id)
        recent = [r for r in rows if r["delivered_date"] >= t - timedelta(days=45)]
        older = [r for r in rows if r["delivered_date"] < t - timedelta(days=45)]

        def rate(rs):
            return round(sum(1 for r in rs if r["on_time"]) / len(rs), 3) if rs else None

        def lead(rs):
            return round(statistics.fmean(r["lead_days"] for r in rs), 1) if rs else None

        out.append({"supplier_id": str(s.id), "supplier": s.name, "quoted_lead_days": s.baseline_lead_time_days, "normal_on_time_rate": rate(older), "recent_on_time_rate": rate(recent), "normal_lead_days": lead(older), "recent_lead_days": lead(recent), "recent_deliveries": len(recent), "normal_deliveries": len(older)})
    return out


def repeat_purchase(db: Session, t: date) -> dict[str, Any]:
    rs, re_ = Q.window(t, 28)
    bs, be = Q.window(t, 56, end_offset=28)
    r_share, r_rep, r_tot = Q.repeat_share(db, rs, re_)
    b_share, b_rep, b_tot = Q.repeat_share(db, bs, be)
    return {"recent_share": round(r_share, 4) if r_share is not None else None, "normal_share": round(b_share, 4) if b_share is not None else None, "recent_orders": r_tot, "normal_orders": b_tot, "recent_window": [rs.isoformat(), re_.isoformat()], "normal_window": [bs.isoformat(), be.isoformat()]}


def product_velocity(db: Session, t: date, top: int = 15) -> list[dict[str, Any]]:
    rs, re_ = Q.window(t, 14)
    bs, be = Q.window(t, 56, end_offset=14)
    positions = Q.stock_positions(db)
    inv_ids = [p.inventory.id for p in positions]
    in_r, in_b = Q.in_stock_days(db, inv_ids, rs, re_), Q.in_stock_days(db, inv_ids, bs, be)
    recent, base = Q.sales_by_product(db, rs, re_), Q.sales_by_product(db, bs, be)
    rows = []
    for p in positions:
        r, b = recent.get(p.product.id, Q.SalesStats()), base.get(p.product.id, Q.SalesStats())
        vr = r.units / max(7, in_r.get(p.inventory.id, 0)) if r.units else 0.0
        vb = b.units / max(28, in_b.get(p.inventory.id, 0)) if b.units else 0.0
        rows.append({"product_id": str(p.product.id), "sku": p.product.sku, "product": p.product.name, "normal_units_per_day": round(vb, 2), "recent_units_per_day": round(vr, 2), "change_pct": round((vr - vb) / vb * 100, 1) if vb else None, "days_of_cover": round(p.on_hand / vr, 1) if vr else None})
    rows.sort(key=lambda x: -x["normal_units_per_day"])
    return rows[:top]


def compute(db: Session) -> dict[str, Any]:
    t = Q.today(db)
    vel = product_velocity(db, t)
    covers = sorted(v["days_of_cover"] for v in vel if v["days_of_cover"] is not None)
    return {
        "as_of": t.isoformat(),
        "weekly_revenue": weekly_revenue(db, t),
        "weekday_sales": weekday_sales(db, t),
        "category_margins": category_margins(db, t),
        "supplier_delivery": supplier_delivery(db, t),
        "repeat_purchase": repeat_purchase(db, t),
        "product_velocity": vel,
        "inventory_coverage": {"median_days_of_cover_top_sellers": covers[len(covers) // 2] if covers else None, "products": len(covers)},
        "basis": "SYNTHETIC",
        "note": "Computed from this merchant's own records in simulated time. 'Normal' means the merchant's history, not an industry benchmark.",
    }
