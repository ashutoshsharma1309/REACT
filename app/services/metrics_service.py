"""Business metrics aggregation for GET /metrics/business."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company, FinancialMetric, Order, OrderItem, Product, ProductionLine, ProductionMetric, SystemState
from app.models.enums import OrderStatus


def business_metrics(db: Session, days: int = 14) -> dict:
    state = db.get(SystemState, 1)
    if state is None:
        raise ValueError("Database is not seeded")
    end = state.data_end.date()
    start = end - timedelta(days=days)
    prev_start = start - timedelta(days=days)
    company = db.scalar(select(Company))
    rows = db.scalars(select(FinancialMetric).where(FinancialMetric.metric_date > prev_start, FinancialMetric.metric_date <= end).order_by(FinancialMetric.metric_date)).all()
    recent = [r for r in rows if r.metric_date > start]
    previous = [r for r in rows if r.metric_date <= start]

    def totals(rs):
        rev = sum(float(r.revenue) for r in rs)
        cost = sum(float(r.cost) for r in rs)
        return {
            "revenue": round(rev, 2), "cost": round(cost, 2), "gross_margin": round((rev - cost) / rev, 4) if rev else 0.0,
            "operating_cost": round(sum(float(r.operating_cost) for r in rs), 2), "production_cost": round(sum(float(r.production_cost) for r in rs), 2),
            "profit": round(sum(float(r.profit) for r in rs), 2), "orders": sum(r.orders_count for r in rs), "units_sold": sum(r.units_sold for r in rs),
        }

    cur, prev = totals(recent), totals(previous)
    change = {k: round((cur[k] - prev[k]) / abs(prev[k]) * 100, 1) if prev[k] else 0.0 for k in ("revenue", "cost", "profit", "orders", "units_sold")}
    change["gross_margin_points"] = round((cur["gross_margin"] - prev["gross_margin"]) * 100, 2)
    by_cat = db.execute(select(Product.category, func.sum(OrderItem.line_revenue), func.sum(OrderItem.quantity), func.count(func.distinct(Order.id))).join(OrderItem, OrderItem.product_id == Product.id).join(Order, Order.id == OrderItem.order_id).where(Order.order_date > start, Order.order_date <= end, Order.status != OrderStatus.CANCELLED).group_by(Product.category).order_by(func.sum(OrderItem.line_revenue).desc())).all()
    by_region = db.execute(select(Order.region, func.sum(Order.total_revenue), func.count(Order.id)).where(Order.order_date > start, Order.order_date <= end, Order.status != OrderStatus.CANCELLED).group_by(Order.region).order_by(func.sum(Order.total_revenue).desc())).all()
    lines = []
    for line in db.scalars(select(ProductionLine).order_by(ProductionLine.code)).all():
        pms = db.scalars(select(ProductionMetric).where(ProductionMetric.production_line_id == line.id, ProductionMetric.metric_date > start, ProductionMetric.metric_date <= end, ProductionMetric.operating_hours > 0)).all()
        n = len(pms) or 1
        lines.append({
            "production_line_id": line.id, "code": line.code, "name": line.name, "capacity_units_per_day": line.capacity_units_per_day,
            "avg_utilization": round(sum(m.utilization for m in pms) / n, 3), "avg_output_units": round(sum(m.output_units for m in pms) / n, 1),
            "avg_defect_rate": round(sum(m.defect_rate for m in pms) / n, 4), "total_downtime_hours": round(sum(m.downtime_hours for m in pms), 1), "status": line.status.value,
        })
    return {
        "period_days": days, "window_start": start + timedelta(days=1), "window_end": end, "currency": company.currency if company else "INR", **cur,
        "previous_period": {k: v for k, v in prev.items()}, "change_pct": change,
        "revenue_by_category": [{"category": c, "revenue": float(r or 0), "units": int(u or 0), "orders": int(o or 0)} for c, r, u, o in by_cat],
        "revenue_by_region": [{"region": r, "revenue": float(v or 0), "orders": int(c or 0)} for r, v, c in by_region],
        "line_utilization": lines,
        "daily": [{"date": r.metric_date, "revenue": float(r.revenue), "cost": float(r.cost), "gross_margin": r.gross_margin, "operating_cost": float(r.operating_cost), "production_cost": float(r.production_cost), "profit": float(r.profit), "orders_count": r.orders_count, "units_sold": r.units_sold} for r in recent],
    }
