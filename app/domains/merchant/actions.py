"""REACT Merchant actions: the controlled catalogue, deterministic parameters and the simulated executor.

* Catalogue: the only actions a recommendation may contain, with the target types each applies to.
* Parameters: computed deterministically when the decision is created (order quantity, suggested price, promotion
  depth...). Money inside parameters comes from the Impact Engine. The language model never sets them.
* Execution: after a human approves, the executor writes a simulated result record (for example SIM-PO-XXXXXXXX).
  It never changes stock, prices, suppliers or customers, never sends a message and never moves money.
  `business_records_modified` is always False. Dates use the simulated business clock, not the wall clock.
"""
from __future__ import annotations

import math
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.decisions.catalogue import ActionSpec
from app.domains.merchant import queries as Q
from app.impact import ImpactEngine
from app.models import Customer, Decision, Inventory, Order, Product, Store, Supplier

A = ActionSpec
MERCHANT_CATALOGUE: dict[str, ActionSpec] = {s.type: s for s in [
    A("REORDER_PRODUCT", "inventory", "Reorder product", "Create a purchase order to restock a product before it runs out.", ("risk",), ("inventory", "product"), "Simulated purchase order created for the supplier.", ("Cash is tied up in the new stock until it sells.", "The supplier may still deliver late."), ("Demand stays near today's level for the next few weeks.",), params=("quantity", "supplier", "estimated_cost")),
    A("INCREASE_STOCK", "inventory", "Increase stock", "Buy extra stock to serve higher demand.", ("opportunity", "risk"), ("product", "inventory", "category"), "Simulated purchase plan created for the extra demand.", ("Surplus stock if the demand does not last.",), ("The higher demand continues for at least a month.",), params=("quantity", "estimated_cost")),
    A("REVIEW_SUPPLIER", "purchasing", "Review supplier", "Open a supplier review: delivery record, escalation and a second source for key items.", ("risk",), ("supplier",), "Simulated supplier review created with the delivery record attached.", ("Friction with a long-standing supplier.",), ("The lateness is not a one-off.",)),
    A("REDUCE_PURCHASE", "inventory", "Reduce purchases", "Pause or shrink reorders of an item until the surplus sells down.", ("risk", "opportunity"), ("inventory", "product", "category"), "Simulated purchase plan change created (reorders paused).", ("Stock-out if demand recovers faster than expected.",), ("Demand stays at today's level.",)),
    A("PROMOTE_PRODUCT", "sales", "Promote product", "Run a time-limited offer to sell down stock or grow a category.", ("risk", "opportunity"), ("product", "inventory", "category"), "Simulated promotion plan created (no price changed, no message sent).", ("A discount lowers the margin on every unit sold during the offer.",), ("Customers respond to the offer.",), params=("discount_pct", "duration_days")),
    A("REVIEW_PRICING", "pricing", "Review pricing", "Review the shelf price against the current cost and the normal margin.", ("risk",), ("product",), "Simulated pricing review created with a suggested price (prices unchanged).", ("A higher price can reduce sales.",), ("Customers accept a price in line with the market.",), params=("suggested_price",)),
    A("REVIEW_DISCOUNT", "pricing", "Review discounts", "Review promotions and counter discounts on a product or category.", ("risk",), ("product", "category"), "Simulated discount review created.", ("Fewer discounts can slow sales.",)),
    A("REDISTRIBUTE_INVENTORY", "inventory", "Redistribute stock", "Move surplus stock to another store or list it on the online and WhatsApp channels.", ("risk", "opportunity"), ("inventory", "product"), "Simulated transfer / listing plan created.", ("Handling and delivery costs.",)),
    A("CONTACT_CUSTOMER", "customers", "Contact customers", "Prepare an outreach list for a customer group (no message is sent).", ("risk", "opportunity"), ("customer_segment", "region", "business"), "Simulated outreach list created (no customer contacted).", ()),
    A("REVIEW_DEMAND", "planning", "Review demand", "Open a demand review for a product, a category or the whole business before changing stock plans.", ("risk", "opportunity"), ("product", "category", "business"), "Simulated demand review created.", ()),
    A("REVIEW_CATEGORY", "planning", "Review category", "Review range, shelf space and stock targets for a category.", ("risk", "opportunity"), ("category",), "Simulated category review created.", ()),
    A("MONITOR_PRODUCT", "planning", "Monitor", "Put the product or supplier on a watchlist with alert thresholds.", ("risk", "opportunity"), ("product", "inventory", "supplier"), "Simulated watchlist entry created.", ("The situation may worsen while being watched.",)),
    A("INVESTIGATE_MARKET", "growth", "Investigate market", "Look into a category, zone or competitor trend before investing.", ("opportunity", "risk"), ("category", "region", "product", "business"), "Simulated market investigation task created.", ()),
    A("PRIORITIZE_PRODUCT", "growth", "Prioritise product", "Give a product better display, online placement and staff focus.", ("opportunity",), ("product", "category"), "Simulated prioritisation task created.", ("Other products get less attention.",)),
]}

MERCHANT_HYPOTHESIS_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "DEMAND_SURGE": ("REORDER_PRODUCT", ("INCREASE_STOCK", "MONITOR_PRODUCT")),
    "SUPPLIER_DELAY_SHORTAGE": ("REORDER_PRODUCT", ("MONITOR_PRODUCT", "REVIEW_DEMAND")),
    "REORDER_POLICY_GAP": ("REORDER_PRODUCT", ("MONITOR_PRODUCT",)),
    "PROMOTION_DRIVEN_DEMAND": ("REORDER_PRODUCT", ("REVIEW_DISCOUNT",)),
    "BULK_ORDER_DEPLETION": ("REORDER_PRODUCT", ("MONITOR_PRODUCT",)),
    "PURCHASE_COST_INCREASE": ("REVIEW_PRICING", ("MONITOR_PRODUCT", "REVIEW_DISCOUNT")),
    "DISCOUNTING": ("REVIEW_DISCOUNT", ("REVIEW_PRICING",)),
    "SELLING_PRICE_CUT": ("REVIEW_PRICING", ("REVIEW_DEMAND",)),
    "FULFILMENT_COST_INCREASE": ("REVIEW_PRICING", ("REVIEW_DISCOUNT",)),
    "COMPETITOR_PRICE_PRESSURE": ("REVIEW_PRICING", ("INVESTIGATE_MARKET",)),
    "OVER_PURCHASE": ("REDUCE_PURCHASE", ("PROMOTE_PRODUCT", "REDISTRIBUTE_INVENTORY")),
    "DEMAND_FADED": ("PROMOTE_PRODUCT", ("REDUCE_PURCHASE", "REDISTRIBUTE_INVENTORY")),
    "CHRONIC_SLOW_MOVER": ("REDUCE_PURCHASE", ("PROMOTE_PRODUCT",)),
    "SUPPLIER_RELIABILITY_DETERIORATED": ("REVIEW_SUPPLIER", ("MONITOR_PRODUCT",)),
    "ONE_OFF_DELAY": ("MONITOR_PRODUCT", ("REVIEW_SUPPLIER",)),
    "SUPPLIER_CAPACITY_STRAIN": ("REVIEW_SUPPLIER", ("MONITOR_PRODUCT",)),
    "MARKET_DEMAND_DECREASE": ("REDUCE_PURCHASE", ("REVIEW_CATEGORY",)),
    "PRODUCT_LOSING_APPEAL": ("REVIEW_DEMAND", ("PROMOTE_PRODUCT", "REVIEW_PRICING")),
    "PRICE_INCREASE_REDUCED_DEMAND": ("REVIEW_PRICING", ("PROMOTE_PRODUCT",)),
    "STOCKOUTS_LOST_SALES": ("REVIEW_DEMAND", ("INVESTIGATE_MARKET",)),
    "CUSTOMER_RETENTION_DECLINE": ("CONTACT_CUSTOMER", ("INVESTIGATE_MARKET",)),
    "FEWER_SHOPPERS": ("INVESTIGATE_MARKET", ("CONTACT_CUSTOMER",)),
    "REGIONAL_DEMAND_GROWTH": ("INCREASE_STOCK", ("PROMOTE_PRODUCT", "CONTACT_CUSTOMER")),
    "CATEGORY_DEMAND_GROWTH": ("INCREASE_STOCK", ("REVIEW_CATEGORY", "PRIORITIZE_PRODUCT")),
    "SEGMENT_EXPANSION": ("PROMOTE_PRODUCT", ("CONTACT_CUSTOMER",)),
    "PROMOTION_DRIVEN_SPIKE": ("REVIEW_DEMAND", ("MONITOR_PRODUCT",)),
    "BULK_BUYER_SPIKE": ("CONTACT_CUSTOMER", ("REVIEW_DEMAND",)),
    "BROAD_PRODUCT_DEMAND": ("INCREASE_STOCK", ("PRIORITIZE_PRODUCT", "PROMOTE_PRODUCT")),
}

_E = ImpactEngine()


# ---- targets ------------------------------------------------------------------------------------------------
def targets(db: Session, f: dict[str, Any]) -> list[dict[str, Any]]:
    """Candidate targets for a merchant finding. Ids come from the stored finding, never from a model."""
    out: list[dict[str, Any]] = []
    if f.get("product_id"):
        out.append({"target_type": "product", "target_id": f["product_id"], "name": f.get("product_name")})
    if f.get("inventory_id"):
        inv = db.get(Inventory, uuid.UUID(f["inventory_id"]))
        if inv:
            out.append({"target_type": "inventory", "target_id": str(inv.id), "name": f"{inv.item_name} at {inv.warehouse}"})
    if f.get("supplier_id"):
        out.append({"target_type": "supplier", "target_id": f["supplier_id"], "name": f.get("supplier_name")})
    if f.get("category"):
        out.append({"target_type": "category", "target_id": f["category"], "name": f["category"]})
    if f.get("region"):
        out.append({"target_type": "region", "target_id": f["region"], "name": f["region"]})
    if f.get("customer_segment"):
        out.append({"target_type": "customer_segment", "target_id": f["customer_segment"], "name": f["customer_segment"].replace("_", " ").title()})
    out.append({"target_type": "business", "target_id": "business", "name": "Whole business"})
    seen, uniq = set(), []
    for t in out:
        k = (t["target_type"], t["target_id"])
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq


# ---- deterministic parameters --------------------------------------------------------------------------------
def _position(db: Session, target_type: str, target_id: str) -> Q.StockPosition | None:
    try:
        tid = uuid.UUID(str(target_id))
    except ValueError:
        return None
    if target_type == "inventory":
        inv = db.get(Inventory, tid)
        return next((p for p in Q.stock_positions(db, product_ids=[inv.product_id]) if p.inventory.id == tid), None) if inv else None
    if target_type == "product":
        positions = Q.stock_positions(db, product_ids=[tid])
        return sorted(positions, key=lambda p: (p.store.code if p.store else ""))[0] if positions else None
    return None


def action_parameters(db: Session, action_type: str, target_type: str | None, target_id: str | None, impact: dict[str, Any]) -> dict[str, Any]:
    """Facts the merchant needs to judge the action, computed now from the records (no model involvement)."""
    from app.domains.merchant.detection import MerchantDetector
    from app.domains.merchant.impact import reorder_suggestion

    t = Q.today(db)
    params: dict[str, Any] = {"computed_at_business_date": t.isoformat(), "source": "deterministic action parameters (app/domains/merchant/actions.py)"}
    if not target_type or not target_id:
        return params
    if action_type in ("REORDER_PRODUCT", "INCREASE_STOCK") and target_type in ("product", "inventory"):
        pos = _position(db, target_type, target_id)
        if pos:
            det = MerchantDetector(db)
            det_pos = next((p for p in det.positions if p.inventory.id == pos.inventory.id), pos)
            sugg = reorder_suggestion(det, det_pos, cover_days=14 if action_type == "REORDER_PRODUCT" else 30)
            cost = _E.purchase_order_value(sugg["quantity"], sugg["unit_cost"])
            params.update({"quantity": sugg["quantity"], "supplier": sugg["supplier"], "supplier_id": sugg["supplier_id"], "unit_cost": sugg["unit_cost"], "estimated_cost": round(cost.value, 2), "estimated_cost_formula": cost.formula, "expected_in_days": round(sugg["lead_days"]), "expected_delivery_date": (t + timedelta(days=round(sugg["lead_days"]))).isoformat(), "basis": sugg, "store": det_pos.store.name if det_pos.store else None, "inventory_id": str(det_pos.inventory.id)})
    elif action_type == "INCREASE_STOCK" and target_type == "category":
        det = MerchantDetector(db)
        rows = [p for p in det.positions if p.product.category == target_id]
        rows = sorted(rows, key=lambda p: -det.velocity(p))[:3]
        lines, total = [], 0.0
        for p in rows:
            sugg = reorder_suggestion(det, p, cover_days=30)
            cost = _E.purchase_order_value(sugg["quantity"], sugg["unit_cost"])
            total += cost.value
            lines.append({"product_id": str(p.product.id), "product": p.product.name, "quantity": sugg["quantity"], "supplier": sugg["supplier"], "unit_cost": sugg["unit_cost"], "estimated_cost": round(cost.value, 2)})
        params.update({"lines": lines, "quantity": sum(x["quantity"] for x in lines), "estimated_cost": round(total, 2), "estimated_cost_formula": "sum over lines of quantity * unit_cost"})
    elif action_type == "REVIEW_PRICING":
        sp = impact.get("suggested_price") or {}
        prod = db.get(Product, uuid.UUID(target_id)) if target_type == "product" else None
        if prod:
            params.update({"current_price": float(prod.selling_price), "current_cost": float(prod.unit_cost), "mrp": float(prod.mrp) if prod.mrp else None, "suggested_price": sp.get("value"), "suggested_price_formula": sp.get("formula"), "normal_unit_margin": (sp.get("inputs") or {}).get("normal_unit_margin")})
    elif action_type == "PROMOTE_PRODUCT":
        pos = _position(db, target_type, target_id) if target_type in ("product", "inventory") else None
        excess = (impact.get("inputs") or {}).get("excess_units")
        params.update({"discount_pct": 0.10, "duration_days": 14, "channels": ["STORE", "ONLINE", "WHATSAPP"], "units_to_sell": excess if excess is not None else None, "note": "10% for 14 days is the default plan; the merchant can adjust it."})
        if pos:
            params["current_price"] = float(pos.product.selling_price)
            params["offer_price"] = round(float(pos.product.selling_price) * 0.9)
    elif action_type == "REDUCE_PURCHASE":
        pos = _position(db, target_type, target_id) if target_type in ("product", "inventory") else None
        if pos:
            u28 = Q.sales_total(db, *Q.window(t, 28), product_id=pos.product.id).units
            v = u28 / 28
            cover = pos.on_hand / v if v else None
            params.update({"current_reorder_point": pos.inventory.reorder_point, "proposed_reorder_point": 0 if cover and cover > 60 else max(0, math.ceil(v * 7)), "pause_reorders_days": min(120, round(cover)) if cover else 90, "units_on_hand": pos.on_hand})
    elif action_type == "REDISTRIBUTE_INVENTORY":
        pos = _position(db, target_type, target_id) if target_type in ("product", "inventory") else None
        stores = db.scalars(select(Store).order_by(Store.code)).all()
        if pos:
            others = [s for s in stores if not pos.store or s.id != pos.store.id]
            move = max(0, pos.on_hand - math.ceil(Q.sales_total(db, *Q.window(t, 28), product_id=pos.product.id).units / 28 * 30))
            params.update({"units_to_move": move, "from": pos.store.name if pos.store else None, "to": [s.name for s in others] or ["Online and WhatsApp catalogue"]})
    elif action_type == "REVIEW_SUPPLIER" and target_type == "supplier":
        sup = db.get(Supplier, uuid.UUID(target_id))
        if sup:
            dels = Q.deliveries(db, sup.id, limit=6)
            params.update({"late_of_last_6": sum(1 for d in dels if not d["on_time"]), "quoted_lead_days": sup.baseline_lead_time_days, "recent_lead_days": round(sum(d["lead_days"] for d in dels[:4]) / max(1, len(dels[:4])), 1) if dels else None, "open_orders": len(Q.open_purchase_orders(db, sup.id)), "dependent_products": len(Q.supplier_products(db, sup.id))})
    elif action_type == "CONTACT_CUSTOMER":
        q = select(func.count(Customer.id))
        if target_type == "region":
            q = q.where(Customer.region == target_id)
        elif target_type == "customer_segment":
            q = q.where(Customer.segment == target_id)
        params["customers_in_list"] = int(db.scalar(q) or 0)
    elif action_type == "MONITOR_PRODUCT":
        params.update({"alert_when": "days of cover drops below the supplier lead time, or demand moves more than 30% from normal", "review_in_days": 7})
    return params


# ---- simulated execution ------------------------------------------------------------------------------------------
def _sim_id(prefix: str) -> str:
    return f"SIM-{prefix}-{uuid.uuid4().hex[:8].upper()}"


def execute(db: Session, action, decision: Decision, target: dict[str, Any], executed_by: str) -> dict[str, Any]:
    """Build the simulated result for an approved merchant action. Reads only; writes nothing to business tables."""
    t = Q.today(db)
    p = dict(action.parameters or {})
    base = {"status": "OPEN", "target": target, "simulated": True, "side_effects": "none", "business_records_modified": False, "business_date": t.isoformat()}
    kind = action.action_type
    if kind == "REORDER_PRODUCT" or (kind == "INCREASE_STOCK" and "lines" not in p):
        return {**base, "task_id": _sim_id("PO"), "task_type": "purchase_order", "supplier": p.get("supplier"), "store": p.get("store"), "items": [{"product": target.get("name"), "quantity": p.get("quantity"), "unit_cost": p.get("unit_cost")}], "estimated_cost": p.get("estimated_cost"), "expected_delivery_date": p.get("expected_delivery_date"),
                "notes": "Simulated purchase order created. Nothing was sent to the supplier and stock levels are unchanged."}
    if kind == "INCREASE_STOCK":
        return {**base, "task_id": _sim_id("PO"), "task_type": "purchase_plan", "items": [{"product": x["product"], "quantity": x["quantity"], "supplier": x["supplier"], "estimated_cost": x["estimated_cost"]} for x in p.get("lines", [])], "estimated_cost": p.get("estimated_cost"), "notes": "Simulated purchase plan created for the extra demand. No order was placed."}
    if kind == "REVIEW_PRICING":
        return {**base, "task_id": _sim_id("PRICE"), "task_type": "pricing_review", "current_price": p.get("current_price"), "current_cost": p.get("current_cost"), "suggested_price": p.get("suggested_price"), "mrp": p.get("mrp"), "notes": "Simulated pricing review created. The shelf price has not been changed."}
    if kind == "PROMOTE_PRODUCT":
        return {**base, "task_id": _sim_id("PROMO"), "task_type": "promotion_plan", "discount_pct": p.get("discount_pct"), "duration_days": p.get("duration_days"), "offer_price": p.get("offer_price"), "channels": p.get("channels"), "starts": (t + timedelta(days=1)).isoformat(), "notes": "Simulated promotion plan created. No price was changed and no customer was messaged."}
    if kind == "REDUCE_PURCHASE":
        return {**base, "task_id": _sim_id("PLAN"), "task_type": "purchase_plan_change", "current_reorder_point": p.get("current_reorder_point"), "proposed_reorder_point": p.get("proposed_reorder_point"), "pause_reorders_days": p.get("pause_reorders_days"), "notes": "Simulated purchase plan change. Reorder settings in the records are unchanged."}
    if kind == "REDISTRIBUTE_INVENTORY":
        return {**base, "task_id": _sim_id("MOVE"), "task_type": "stock_transfer_plan", "units_to_move": p.get("units_to_move"), "from": p.get("from"), "to": p.get("to"), "notes": "Simulated transfer / listing plan. No stock was moved."}
    if kind == "REVIEW_SUPPLIER":
        return {**base, "task_id": _sim_id("SUPP"), "task_type": "supplier_review", "late_of_last_6": p.get("late_of_last_6"), "quoted_lead_days": p.get("quoted_lead_days"), "recent_lead_days": p.get("recent_lead_days"), "open_orders": p.get("open_orders"), "agenda": ["delivery record", "recovery date for overdue orders", "second source for the fastest-selling items"], "notes": "Simulated supplier review created. The supplier has not been contacted."}
    if kind == "CONTACT_CUSTOMER":
        return {**base, "task_id": _sim_id("CRM"), "task_type": "customer_outreach_list", "customers_in_list": p.get("customers_in_list"), "notes": "Simulated outreach list created. No customer has been contacted."}
    spec = MERCHANT_CATALOGUE.get(kind)
    return {**base, "task_id": _sim_id(kind.split("_")[0][:5]), "task_type": kind.lower(), "notes": f"{spec.simulated_outcome if spec else 'Simulated task created.'} Nothing outside REACT was changed."}
