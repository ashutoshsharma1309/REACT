"""Deterministic investigation plan for merchant findings (used by the rule-based provider and as the LLM's fallback).

The plan names allow-listed merchant tools and arguments taken only from the stored finding.
"""
from __future__ import annotations

from typing import Any

EVIDENCE_CATEGORIES = {
    "risk": ["order_items", "inventory_snapshots", "purchase_orders", "supplier_deliveries", "pricing_history", "discounts"],
    "opportunity": ["order_items", "customers", "inventory", "discounts", "competitor_signals"],
}
OBJECTIVES = {
    "risk": "Find out why '{title}' is happening for this merchant, which records support or contradict each explanation, and what it could cost in INR.",
    "opportunity": "Explain why '{title}' is happening, whether it is broad and durable, what stock it needs and what it could earn in INR.",
}


def plan(f: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    rtype = f.get("type")
    pid, sid, cat, region = f.get("product_id"), f.get("supplier_id"), f.get("category"), f.get("region")
    steps: list[tuple[str, dict[str, Any], str]] = []
    if pid:
        steps += [("get_product_sales", {"product_id": pid, "days": 14}, "recent vs normal sales, price, cost and customers"), ("get_inventory_status", {"product_id": pid, "inventory_id": f.get("inventory_id")}, "stock, open orders, stockouts and stock age")]
        if rtype in ("STOCKOUT_RISK", "DEMAND_SUPPLY_MISMATCH") and sid:
            steps += [("get_supplier_history", {"supplier_id": sid}, "delivery record and lead time"), ("get_purchase_orders", {"product_id": pid, "status": "OPEN"}, "orders on the way")]
        if rtype in ("MARGIN_DECLINE", "PRICE_ANOMALY", "DISCOUNT_LEAKAGE"):
            steps += [("get_pricing_history", {"product_id": pid}, "cost and price changes"), ("get_discount_history", {"product_id": pid}, "discounts and promotions"), ("get_competitor_prices", {"product_id": pid}, "online price checks (synthetic)")]
        if rtype in ("EXCESS_INVENTORY", "SLOW_MOVING_INVENTORY"):
            steps += [("get_sales_history", {"product_id": pid, "days": 120}, "sales before and after the last large delivery"), ("get_purchase_orders", {"product_id": pid, "days": 180}, "purchase history")]
        if rtype in ("DEMAND_DECLINE", "DEMAND_OPPORTUNITY"):
            steps += [("get_customer_orders", {"product_id": pid}, "who is buying"), ("get_category_performance", {"category": cat}, "is the whole category moving")]
    elif sid:
        steps += [("get_supplier_history", {"supplier_id": sid}, "delivery record, lateness and open orders"), ("get_purchase_orders", {"supplier_id": sid, "status": "OPEN"}, "overdue purchase orders")]
    elif cat:
        steps += [("get_regional_performance", {"category": cat}, "demand by city zone"), ("get_customer_orders", {"category": cat, "region": region}, "who is buying"), ("get_customer_segments", {"category": cat, "region": region}, "segment mix"), ("get_discount_history", {"category": cat}, "promotions")]
    else:
        steps += [("get_business_baseline", {}, "what is normal for this merchant"), ("get_financial_metrics", {"days": 28}, "revenue and margin")]
    return steps
