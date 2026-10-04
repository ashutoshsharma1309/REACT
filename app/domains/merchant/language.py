"""Plain-language labels for merchant signals, used to build the 'Why?' summary of an investigation.

The backend decides which measurements explain a conclusion (the leading hypothesis's satisfied supporting conditions,
strongest first) and how each is phrased, so the UI never re-derives business logic.
"""
from __future__ import annotations

from typing import Any


def plural(n: int | float, one: str, many: str | None = None) -> str:
    """'1 order', '3 orders', '1 order is', etc. without '(s)'."""
    return f"{n:g} {one if n == 1 else (many or one + 's')}"

LABELS: dict[str, str] = {
    "demand_change_pct": "Sales", "units_per_day_recent": "Daily sales", "distinct_customers_change_pct": "Customers buying", "top_customer_share": "Largest single buyer",
    "stock_change_pct": "Stock on hand", "days_of_cover": "Stock cover", "stockout_days_14": "Days out of stock (14)", "open_po_overdue": "Next delivery overdue", "no_open_po": "Nothing on order",
    "overdue_days_max": "Order overdue by", "stock_age_days": "Age of oldest stock", "reorder_cover_ratio": "Reorder level vs need", "purchase_cover_days": "Largest delivery covered",
    "demand_change_since_purchase_pct": "Sales since that delivery", "receipt_vs_normal_order_ratio": "Delivery vs usual order", "supplier_late_share_recent": "Supplier late deliveries (45 days)",
    "supplier_late_deliveries_6": "Late of last 6 deliveries", "supplier_lead_change_days": "Supplier lead time", "late_share_recent": "Late deliveries (45 days)", "late_deliveries_6": "Late of last 6 deliveries",
    "lead_change_days": "Lead time vs quoted", "on_time_change_pts": "On-time rate", "average_delay_when_late": "Average delay when late", "overdue_pos": "Overdue orders", "po_units_change_pct": "Units ordered",
    "products_low_cover": "Products short of stock", "dependent_products": "Products from this supplier", "cost_list_change_pct": "Purchase cost", "unit_cost_change_pct": "Unit cost of sales",
    "unit_price_change_pct": "Average selling price", "unit_margin_change_pct": "Margin per unit", "list_price_change_pct": "Shelf price", "cost_increase_passed_on": "Cost rise passed on",
    "discount_rate_change_pts": "Discount rate", "promotion_active": "Promotion running", "online_share_change_pts": "Online share of sales", "competitor_price_gap_pct": "Online prices vs ours",
    "category_demand_change_pct": "Category sales", "zone_category_growth_pct": "Sales in this zone", "other_zones_growth_pct": "Sales in other zones", "zone_total_growth_pct": "All sales in this zone",
    "distinct_customers_growth_pct": "Customers buying", "new_customer_share_change_pts": "First-time buyers", "segment_share_change_pts": "Leading segment's share", "weekly_revenue_change_pct": "Weekly revenue",
    "repeat_share_change_pts": "Repeat customers", "top_products_short_of_stock": "Best-sellers short of stock", "top_products_declining": "Best-sellers declining", "orders_change_pct": "Orders",
    "average_order_value_change_pct": "Average bill", "stock_value": "Stock value",
}


def _fmt(value: Any, unit: str | None) -> tuple[str, str]:
    """(display text, direction up|down|flat|none)."""
    if isinstance(value, bool):
        return ("Yes" if value else "No"), "none"
    if value is None:
        return "n/a", "none"
    v = float(value)
    direction = "up" if v > 0 else "down" if v < 0 else "flat"
    if unit == "%":
        return f"{'↑' if v > 0 else '↓' if v < 0 else ''} {abs(v):.0f}%".strip(), direction
    if unit == "points":
        return f"{'↑' if v > 0 else '↓' if v < 0 else ''} {abs(v):.1f} pts".strip(), direction
    if unit == "days":
        if v == 999:
            return "no prior sales", "none"
        return f"{v:.1f} days" if v < 100 else f"{v:.0f} days", "none"
    if unit == "share":
        return f"{v * 100:.0f}%", "none"
    if unit == "units/day":
        return f"{v:.1f} a day", "none"
    if unit == "₹":
        return f"₹{v:,.0f}", "none"
    if unit == "x":
        return f"{v:.1f}x", "none"
    if unit == "ratio":
        return f"{v * 100:.0f}% of need", "none"
    if unit == "of 6":
        return f"{v:.0f} of 6", "none"
    return (f"{v:g}" + (f" {unit}" if unit else "")), "none"


def key_signals(drafts: list[Any], limit: int = 5, contributing: list[Any] | None = None) -> list[dict[str, Any]]:
    """The strongest supporting measurements behind the conclusion (leading hypothesis first, then a contributing
    factor), phrased for a merchant. Weak context conditions (weight < 1.5) are left out when stronger ones exist."""
    seen, out = set(), []
    pool = [d for d in drafts if d.supports]
    strong = [d for d in pool if d.weight >= 1.5]
    lead = sorted(strong if len(strong) >= 3 else pool, key=lambda d: (-d.weight, -d.score))
    extra = sorted([d for d in (contributing or []) if d.supports and d.weight >= 1.5], key=lambda d: (-d.weight, -d.score))
    ordered = lead[:3] + extra[:2] + lead[3:] + extra[2:]
    for d in ordered:
        if d.signal in seen:
            continue
        seen.add(d.signal)
        display, direction = _fmt(d.value, d.unit)
        base_display = _fmt(d.baseline, d.unit)[0] if d.baseline not in (None, 0.0) and d.unit not in ("%", "points") else None
        out.append({"signal": d.signal, "label": LABELS.get(d.signal, d.signal.replace("_", " ").capitalize()), "value": d.value, "unit": d.unit, "display": display, "direction": direction, "baseline_display": base_display, "claim": d.claim, "source_type": d.source_type, "record_count": len(d.source_ids), "condition": d.condition_label})
        if len(out) >= limit:
            break
    return out
