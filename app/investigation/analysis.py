"""Conclusion selection (root cause / opportunity explanation / risk driver) and impact analysis.

Terminology: the Evidence Support Score says how strongly the *available records* support a
hypothesis. It is never presented as a probability, model confidence or certainty.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.detection.base import LineEconomics
from app.impact import ImpactEngine
from app.investigation import tools as T
from app.investigation.evidence import EvidenceDraft, support_label
from app.investigation.signals import SignalSet
from app.models import BusinessRisk, Incident, Inventory, Machine, Opportunity, Order, OrderItem, Product, ProductionLine
from app.models.enums import OrderStatus

DETERMINED_MIN_SCORE = 0.5
DETERMINED_MIN_MARGIN = 0.15
NOTE = "Evidence Support Score expresses how strongly the available records support the hypothesis. It is not a probability, a model confidence or a guarantee."


def conclude(kind: str, scored: list[dict[str, Any]], evidence: dict[str, list[EvidenceDraft]]) -> dict[str, Any]:
    """scored: hypotheses sorted by evidence_support_score desc (ties by type)."""
    label_key = {"incident": "root_cause", "opportunity": "opportunity_explanation", "risk": "risk_driver"}[kind]
    if not scored:
        return {"kind": label_key, "determined": False, "selected_hypothesis": None, "evidence_support_score": None, "support_label": "unknown", "statement": "No applicable hypothesis could be evaluated for this subject.", "supporting_evidence": [], "contradicting_evidence": [], "alternatives": [], "note": NOTE}
    top = scored[0]
    second = scored[1] if len(scored) > 1 else None
    margin = round(top["evidence_support_score"] - (second["evidence_support_score"] if second else 0.0), 4)
    co_factor = second is not None and top["evidence_support_score"] >= 0.8 and second["evidence_support_score"] >= 0.8 and margin < DETERMINED_MIN_MARGIN
    determined = top["evidence_support_score"] >= DETERMINED_MIN_SCORE and (second is None or margin >= DETERMINED_MIN_MARGIN or co_factor)
    drafts = evidence.get(top["type"], [])
    supporting = [d.as_dict() for d in drafts if d.supports]
    contradicting = [d.as_dict() for d in drafts if d.contradicts]
    lbl = support_label(top["evidence_support_score"])
    verb = {"strongly supported": "strongly supports", "supported": "supports", "weakly supported": "weakly supports"}.get(lbl, "does not support")
    if determined and co_factor:
        statement = f"The available evidence strongly supports {top['label'].lower()} as the {label_key.replace('_', ' ')} (Evidence Support Score {top['evidence_support_score']:.2f}), with {second['label'].lower()} as a contributing factor ({second['evidence_support_score']:.2f})."
    elif determined:
        statement = f"The available evidence {verb} {top['label'].lower()} as the {label_key.replace('_', ' ')} (Evidence Support Score {top['evidence_support_score']:.2f})."
    elif second and margin < DETERMINED_MIN_MARGIN and top["evidence_support_score"] >= DETERMINED_MIN_SCORE:
        statement = f"Evidence is split between {top['label'].lower()} ({top['evidence_support_score']:.2f}) and {second['label'].lower()} ({second['evidence_support_score']:.2f}); further inspection is needed before acting."
    else:
        statement = f"No hypothesis is adequately supported by the available records (best: {top['label'].lower()} at {top['evidence_support_score']:.2f})."
    return {
        "kind": label_key, label_key: top["type"], "selected_hypothesis": top["type"], "selected_label": top["label"], "evidence_support_score": top["evidence_support_score"], "support_label": lbl,
        "determined": determined, "margin_over_next": margin, "contributing_factor": second["type"] if co_factor else None, "statement": statement, "supporting_evidence": supporting, "contradicting_evidence": contradicting,
        "alternatives": [{"hypothesis": h["type"], "label": h["label"], "evidence_support_score": h["evidence_support_score"], "support_label": support_label(h["evidence_support_score"])} for h in scored[1:]],
        "recommended_next_step": top["next_step"], "note": NOTE,
    }


# ---- impact --------------------------------------------------------------------------------------
def analyse_impact(db: Session, subject: Incident | Opportunity | BusinessRisk, signals: SignalSet, conclusion: dict[str, Any], engine: ImpactEngine | None = None) -> dict[str, Any]:
    engine = engine or ImpactEngine()
    if getattr(signals, "domain", "manufacturing") == "merchant":
        from app.domains.merchant.impact import analyse_merchant_impact

        return analyse_merchant_impact(db, subject, signals, conclusion, engine)
    if isinstance(subject, Incident):
        return _incident_impact(db, subject, signals, conclusion, engine)
    if isinstance(subject, Opportunity):
        return _opportunity_impact(db, subject, signals, conclusion, engine)
    return _risk_impact(db, subject, signals, conclusion, engine)


def _open_orders_for_products(db: Session, product_ids: list[uuid.UUID], due_within_days: int, today) -> list[Order]:
    if not product_ids:
        return []
    return list(db.scalars(select(Order).join(OrderItem).where(OrderItem.product_id.in_(product_ids), Order.status.in_([OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.IN_PRODUCTION, OrderStatus.DELAYED]), Order.promised_delivery_date <= today + timedelta(days=due_within_days)).distinct()).all())


def _incident_impact(db: Session, inc: Incident, S: SignalSet, conclusion: dict[str, Any], E: ImpactEngine) -> dict[str, Any]:
    line = db.get(ProductionLine, inc.production_line_id) if inc.production_line_id else None
    if line is None and inc.machine_id:
        m = db.get(Machine, inc.machine_id)
        line = m.production_line if m else None
    if line is None:
        return {"scope": "unknown", "note": "No production line associated; impact not estimated.", "estimated_total": None}
    econ = LineEconomics(db)
    products = econ.by_line.get(line.id, [])
    pm = T.get_production_metrics(db, production_line_id=line.id, days=7, baseline_days=30)
    s = pm["summary"]
    recent_days = max(s["recent_days"], 1)
    lost_units = max(0.0, (s["baseline_avg_output"] - s["recent_avg_output"]) * recent_days)
    extra_defects = max(0.0, (s["recent_defect_rate"] - s["baseline_defect_rate"]) * s["recent_avg_output"] * recent_days)
    extra_downtime = max(0.0, s["recent_downtime_hours"] - s["baseline_downtime_hours_per_day"] * recent_days)
    unit_margin, unit_cost = econ.unit_margin(line.id), econ.unit_cost(line.id)
    unit_price = (sum(float(p.selling_price) for p in products) / len(products)) if products else unit_margin + unit_cost
    hourly = E.hourly_loss_for_line(line.capacity_units_per_day, unit_margin)
    mechanical = conclusion.get("selected_hypothesis") in ("BEARING_DEGRADATION", "HYDRAULIC_SEAL_DEGRADATION", "POWER_INSTABILITY", "MAINTENANCE_ISSUE")
    projected_hours = S.value("historical_avg_downtime_hours") or 24.0 if mechanical else 0.0
    production_loss = E.production_loss(lost_units, unit_margin)
    revenue_loss = E.revenue_loss(lost_units, unit_price)
    downtime_cost = E.downtime_impact(extra_downtime, hourly)
    projected_downtime = E.downtime_impact(projected_hours, hourly)
    defect_cost = E.defect_cost(extra_defects, unit_cost)
    # inventory impact on the line's finished goods
    invs = [db.scalar(select(Inventory).where(Inventory.product_id == p.id)) for p in products]
    invs = [i for i in invs if i]
    daily = sum(i.avg_daily_consumption for i in invs) or 1.0
    qty = sum(i.quantity_available for i in invs)
    inventory = E.inventory_impact((qty + lost_units) / daily, qty / daily, daily, unit_cost)
    # delivery + customer impact
    today = inc.detected_at.date()
    at_risk = _open_orders_for_products(db, [p.id for p in products], 7, today)
    at_risk_rev = sum(float(o.total_revenue) for o in at_risk)
    avg_days_late = lost_units / max(line.capacity_units_per_day, 1)
    delivery = E.delivery_impact(len(at_risk), at_risk_rev, avg_days_late)
    customers = {o.customer_id for o in at_risk}
    customer = E.customer_impact(len(customers), at_risk_rev)
    total = production_loss.value + defect_cost.value + downtime_cost.value + projected_downtime.value + delivery.value
    return {
        "scope": {"production_line_id": str(line.id), "line_code": line.code, "machine_id": str(inc.machine_id) if inc.machine_id else None, "products": len(products)},
        "estimated_production_loss": production_loss.as_dict(), "estimated_revenue_loss": revenue_loss.as_dict(), "downtime_cost": downtime_cost.as_dict(),
        "projected_downtime_if_unaddressed": {**projected_downtime.as_dict(), "estimated_downtime_minutes": round(projected_hours * 60), "basis": "SYNTHETIC", "source": "average downtime of similar historical incidents" if S.has("historical_avg_downtime_hours") else "default projection"},
        "defect_cost": defect_cost.as_dict(), "inventory_impact": inventory.as_dict(), "delivery_impact": {**delivery.as_dict(), "order_ids": [str(o.id) for o in at_risk[:50]]}, "customer_impact": {**customer.as_dict(), "customer_ids": [str(c) for c in list(customers)[:50]]},
        "estimated_total": round(total, 2), "total_includes": ["estimated_production_loss", "defect_cost", "downtime_cost", "projected_downtime_if_unaddressed", "delivery_impact"], "currency": E.config.currency, "inputs": {"lost_units": round(lost_units), "extra_defective_units": round(extra_defects), "extra_downtime_hours": round(extra_downtime, 1), "recent_days": recent_days, "unit_margin": round(unit_margin, 2), "unit_price": round(unit_price, 2), "hourly_loss": round(hourly, 2)},
        "wording": "Estimated impact - synthetic, formula-based estimates; not guaranteed financial outcomes.",
    }


def _opportunity_impact(db: Session, opp: Opportunity, S: SignalSet, conclusion: dict[str, Any], E: ImpactEngine) -> dict[str, Any]:
    product = db.get(Product, opp.product_id) if opp.product_id else None
    line = db.get(ProductionLine, opp.production_line_id) if opp.production_line_id else (product.production_line if product else None)
    econ = LineEconomics(db)
    growth_obs = S.get("order_growth_pct")
    recent_units = float((growth_obs.details or {}).get("recent_units", 0)) if growth_obs else 0.0
    prev_units = float(growth_obs.baseline or 0) if growth_obs else 0.0
    daily_recent, daily_prev = recent_units / 28, prev_units / 28
    incremental_daily = max(0.0, daily_recent - daily_prev)
    horizon = 30
    incremental = incremental_daily * horizon
    if product:
        price, margin, cost = float(product.selling_price), econ.product_margin(product), float(product.unit_cost)
    else:
        prods = econ.by_line.get(line.id, []) if line else list(econ.products.values())
        price = sum(float(p.selling_price) for p in prods) / len(prods) if prods else 0.0
        margin, cost = econ.unit_margin(line.id if line else None), econ.unit_cost(line.id if line else None)
    revenue = E.opportunity_revenue(incremental, price, margin, horizon)
    value = E.opportunity_value(incremental, margin, horizon)
    spare = float(S.value("spare_units_per_day") or 0.0)
    on_hand = float((S.get("product_days_of_cover").details or {}).get("quantity", 0)) if S.get("product_days_of_cover") else 0.0
    planned = daily_prev * horizon
    requirement = E.inventory_requirement(daily_recent * horizon, on_hand, planned, cost)
    constraints: list[dict[str, Any]] = []
    if spare < incremental_daily:
        constraints.append({"type": "production_capacity", "detail": f"Spare capacity {spare:.0f} units/day is below the incremental demand of {incremental_daily:.0f} units/day", "signal": "spare_units_per_day"})
    cover = S.value("product_days_of_cover")
    if cover is not None and cover < 7:
        constraints.append({"type": "inventory", "detail": f"Finished-goods cover is only {cover:.1f} days", "signal": "product_days_of_cover"})
    if S.value("component_sole_source"):
        constraints.append({"type": "supplier", "detail": f"Sole-sourced components: {', '.join((S.get('component_sole_source').details or {}).get('components', []))} (lead time up to {S.value('component_max_lead_time_days')} days)", "signal": "component_sole_source"})
    ccover = S.value("component_min_cover_days")
    if ccover is not None and ccover < 7:
        constraints.append({"type": "component_inventory", "detail": f"Tightest component cover is {ccover:.1f} days", "signal": "component_min_cover_days"})
    if conclusion.get("selected_hypothesis") in ("TEMPORARY_CUSTOMER_SPIKE", "SEASONAL_DEMAND"):
        constraints.append({"type": "demand_durability", "detail": f"Leading explanation is {conclusion.get('selected_label', '').lower()}; treat the projection as an upper bound", "signal": "conclusion"})
    return {
        "scope": {"product_id": str(product.id) if product else None, "product": product.name if product else None, "production_line_id": str(line.id) if line else None, "region": opp.region},
        "potential_additional_demand": {"units_per_day": round(incremental_daily, 1), "units_over_horizon": round(incremental), "horizon_days": horizon, "recent_daily_units": round(daily_recent, 1), "previous_daily_units": round(daily_prev, 1), "basis": "28-day order comparison"},
        "available_production_capacity": {"spare_units_per_day": round(spare), "utilization_14d": S.value("line_utilization_14d"), "sufficient": spare >= incremental_daily},
        "estimated_revenue_opportunity": revenue.as_dict(), "estimated_margin_opportunity": {**value.as_dict(), "potential_margin_captured": round(revenue.components.get("potential_margin", 0.0), 2)},
        "inventory_requirements": requirement.as_dict(), "operational_constraints": constraints,
        "estimated_total": round(revenue.value, 2), "total_includes": ["estimated_revenue_opportunity"], "currency": E.config.currency,
        "wording": "Potential opportunity - synthetic, formula-based estimates; not guaranteed financial outcomes.",
    }


def _risk_impact(db: Session, risk: BusinessRisk, S: SignalSet, conclusion: dict[str, Any], E: ImpactEngine) -> dict[str, Any]:
    rec = (risk.evidence or {}).get("records", {})
    order_ids = [uuid.UUID(o) for o in rec.get("order_ids", [])[:200]] if rec.get("order_ids") else []
    orders = list(db.scalars(select(Order).where(Order.id.in_(order_ids))).all()) if order_ids else []
    at_risk_rev = sum(float(o.total_revenue) for o in orders)
    customers = {o.customer_id for o in orders}
    delivery = E.delivery_impact(len(orders), at_risk_rev, float(S.value("supplier_max_delay_days") or 0))
    customer = E.customer_impact(len(customers), at_risk_rev)
    phase1 = risk.estimated_impact or {}
    total = float(phase1.get("value") or 0.0) + delivery.value + customer.value
    return {
        "scope": {"entity_type": risk.entity_type, "entity_id": str(risk.entity_id) if risk.entity_id else None, "entity_name": risk.entity_name},
        "detection_estimate": phase1, "delivery_impact": {**delivery.as_dict(), "order_ids": [str(o.id) for o in orders[:50]]}, "customer_impact": {**customer.as_dict(), "customer_ids": [str(c) for c in list(customers)[:50]]},
        "estimated_total": round(total, 2), "total_includes": ["detection_estimate", "delivery_impact", "customer_impact"], "currency": E.config.currency,
        "wording": "Estimated impact - synthetic, formula-based estimates; not guaranteed financial outcomes.",
    }
