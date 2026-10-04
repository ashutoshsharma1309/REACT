"""Controlled action catalogue.

Only these action types can be recommended or executed. Each entry says which subject kinds and
target types it applies to and which investigation hypotheses it is the default response for.
Every action is simulated: execution produces a structured result record, never a real-world side effect.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActionSpec:
    type: str
    category: str  # operational | inventory | sales | opportunity
    label: str
    description: str
    subject_kinds: tuple[str, ...]
    target_types: tuple[str, ...]
    simulated_outcome: str  # what the simulated execution produces
    default_risks: tuple[str, ...] = ()
    default_assumptions: tuple[str, ...] = ()
    reversible: bool = True
    requires_human_approval: bool = True
    params: tuple[str, ...] = field(default_factory=tuple)


CATALOGUE: dict[str, ActionSpec] = {s.type: s for s in [
    # ---- operational ----
    ActionSpec("INSPECT_MACHINE", "operational", "Inspect machine", "Create an inspection task for a machine or one of its components / sensors.", ("incident",), ("machine",), "Inspection task created for the maintenance team.", ("Inspection takes the machine offline briefly.",), ("Maintenance staff are available within the next shift.",)),
    ActionSpec("SCHEDULE_MAINTENANCE", "operational", "Schedule maintenance", "Create a maintenance work order (e.g. component replacement) in a planned window.", ("incident",), ("machine",), "Maintenance work order created in a planned window.", ("Planned downtime during the maintenance window.", "Replacement part must be in stock."), ("The identified component is the failing one.",), False),
    ActionSpec("MONITOR_MACHINE", "operational", "Monitor machine", "Increase monitoring frequency and tighten alert thresholds for a machine.", ("incident",), ("machine",), "Enhanced monitoring profile activated.", ("Degradation may continue while monitoring.",)),
    ActionSpec("REVIEW_PRODUCTION_PLAN", "operational", "Review production plan", "Trigger a production planning review for a line (load balancing, shifts, sequencing).", ("incident", "risk", "opportunity"), ("production_line",), "Production planning review task created.", ("Plan changes may delay some orders.",)),
    ActionSpec("REVIEW_SUPPLIER", "operational", "Review supplier", "Open a supplier performance review (escalation, corrective action, secondary sourcing).", ("incident", "risk"), ("supplier", "inventory"), "Supplier review task created with the delayed batches attached.", ("Supplier relationship friction.",), ("Supplier delay is not a one-off event.",)),
    # ---- inventory ----
    ActionSpec("INCREASE_REORDER", "inventory", "Increase reorder point", "Propose a higher reorder point / safety stock for an item (proposal only).", ("risk", "incident"), ("inventory",), "Reorder-point change proposed for planner confirmation.", ("Higher working capital tied in stock.",), ("Consumption stays at the observed run-rate.",)),
    ActionSpec("REDUCE_PURCHASE", "inventory", "Reduce purchase volume", "Propose lower purchase / production volume for an item with falling demand.", ("risk", "opportunity"), ("inventory", "product"), "Purchase reduction proposed for planner confirmation.", ("Stock-out if demand recovers faster than expected.",)),
    ActionSpec("REVIEW_STOCK", "inventory", "Review stock position", "Open a stock review for an item (counts, allocation, expedite options).", ("risk", "incident"), ("inventory", "product"), "Stock review task created.", ()),
    ActionSpec("REDISTRIBUTE_INVENTORY", "inventory", "Redistribute inventory", "Propose moving excess stock to regions / customers with demand.", ("opportunity",), ("inventory", "product"), "Redistribution proposal created.", ("Logistics cost of moving stock.",)),
    # ---- sales ----
    ActionSpec("CONTACT_CUSTOMER", "sales", "Contact customers", "Create outreach tasks for affected or growing customers (no messages are sent).", ("risk", "opportunity", "incident"), ("customer_segment", "product", "production_line"), "Customer outreach task list created (no communication sent).", ()),
    ActionSpec("REVIEW_PRICING", "sales", "Review pricing", "Open a pricing review for a product or category.", ("risk", "opportunity"), ("product", "category"), "Pricing review task created with margin and competitor context.", ("Price changes can affect win rates.",)),
    ActionSpec("INCREASE_PRODUCTION", "sales", "Increase production", "Propose a production increase for a product on its line (proposal only).", ("opportunity",), ("product",), "Production increase proposal created for the planning team.", ("Component supply must keep pace.", "Capacity may be insufficient."), ("Demand growth is durable.",)),
    ActionSpec("REVIEW_DEMAND", "sales", "Review demand forecast", "Open a demand review for a product / category.", ("risk", "opportunity"), ("product", "category", "production_line"), "Demand review task created.", ()),
    # ---- opportunity ----
    ActionSpec("INVESTIGATE_MARKET", "opportunity", "Investigate market", "Create a market investigation task (category trend, regions, competitors).", ("opportunity",), ("product", "category", "region", "production_line"), "Market investigation task created.", ()),
    ActionSpec("INCREASE_CAPACITY", "opportunity", "Increase capacity", "Propose additional shifts or capacity allocation on a line (proposal only).", ("opportunity", "risk"), ("production_line",), "Capacity increase proposal created.", ("Overtime cost.", "Demand may not persist."), ("Spare capacity can be staffed.",), False),
    ActionSpec("PRIORITIZE_PRODUCT", "opportunity", "Prioritize product", "Prioritise a product in planning and sales focus.", ("opportunity",), ("product",), "Product prioritisation task created for planning and sales.", ("Other products may be de-prioritised.",)),
]}

# Default response per investigation hypothesis: (primary action, alternatives)
HYPOTHESIS_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "BEARING_DEGRADATION": ("SCHEDULE_MAINTENANCE", ("INSPECT_MACHINE", "MONITOR_MACHINE")),
    "SENSOR_FAILURE": ("INSPECT_MACHINE", ("MONITOR_MACHINE",)),
    "POWER_INSTABILITY": ("INSPECT_MACHINE", ("REVIEW_PRODUCTION_PLAN", "MONITOR_MACHINE")),
    "MAINTENANCE_ISSUE": ("INSPECT_MACHINE", ("SCHEDULE_MAINTENANCE",)),
    "HYDRAULIC_SEAL_DEGRADATION": ("SCHEDULE_MAINTENANCE", ("INSPECT_MACHINE",)),
    "COMPONENT_SUPPLY_SHORTAGE": ("INCREASE_REORDER", ("REVIEW_SUPPLIER", "REVIEW_STOCK")),
    "MATERIAL_QUALITY_ISSUE": ("REVIEW_SUPPLIER", ("REVIEW_STOCK",)),
    "DEMAND_OVERLOAD": ("REVIEW_PRODUCTION_PLAN", ("INCREASE_CAPACITY",)),
    "CAPACITY_LOSS": ("REVIEW_PRODUCTION_PLAN", ("INSPECT_MACHINE",)),
    "SUPPLIER_RELIABILITY_ISSUE": ("REVIEW_SUPPLIER", ("INCREASE_REORDER",)),
    "SINGLE_SOURCE_EXPOSURE": ("REVIEW_SUPPLIER", ("INCREASE_REORDER",)),
    "DEMAND_SURGE_CONSUMPTION": ("INCREASE_REORDER", ("REVIEW_STOCK",)),
    "REORDER_POLICY_GAP": ("INCREASE_REORDER", ("REVIEW_STOCK",)),
    "PRODUCTION_SHORTFALL": ("REVIEW_PRODUCTION_PLAN", ("REVIEW_STOCK",)),
    "DEMAND_CONTRACTION": ("REVIEW_DEMAND", ("REDUCE_PURCHASE",)),
    "PRICING_PRESSURE": ("REVIEW_PRICING", ("REVIEW_DEMAND",)),
    "CUSTOMER_CHURN": ("CONTACT_CUSTOMER", ("REVIEW_DEMAND",)),
    "SUPPLY_CONSTRAINED_SALES": ("REVIEW_PRODUCTION_PLAN", ("REVIEW_STOCK",)),
    "COMPETITOR_PRESSURE": ("REVIEW_PRICING", ("INVESTIGATE_MARKET",)),
    "SUSTAINED_REGIONAL_DEMAND": ("INCREASE_PRODUCTION", ("PRIORITIZE_PRODUCT", "INCREASE_CAPACITY")),
    "SEGMENT_EXPANSION": ("PRIORITIZE_PRODUCT", ("CONTACT_CUSTOMER", "INCREASE_PRODUCTION")),
    "COMPETITOR_SUPPLY_SHORTAGE": ("INCREASE_PRODUCTION", ("CONTACT_CUSTOMER",)),
    "SEASONAL_DEMAND": ("REVIEW_DEMAND", ("REVIEW_STOCK",)),
    "MARKET_CATEGORY_GROWTH": ("INVESTIGATE_MARKET", ("PRIORITIZE_PRODUCT",)),
    "PRICING_HEADROOM": ("REVIEW_PRICING", ("INVESTIGATE_MARKET",)),
    "CAPACITY_HEADROOM": ("PRIORITIZE_PRODUCT", ("INVESTIGATE_MARKET",)),
    "INVENTORY_REBALANCING": ("REDISTRIBUTE_INVENTORY", ("REDUCE_PURCHASE",)),
    "TEMPORARY_CUSTOMER_SPIKE": ("REVIEW_DEMAND", ("CONTACT_CUSTOMER",)),
}


def catalogue_for(domain: str = "manufacturing") -> dict[str, ActionSpec]:
    if domain == "merchant":
        from app.domains.merchant.actions import MERCHANT_CATALOGUE

        return MERCHANT_CATALOGUE
    return CATALOGUE


def hypothesis_actions_for(domain: str = "manufacturing") -> dict[str, tuple[str, tuple[str, ...]]]:
    if domain == "merchant":
        from app.domains.merchant.actions import MERCHANT_HYPOTHESIS_ACTIONS

        return MERCHANT_HYPOTHESIS_ACTIONS
    return HYPOTHESIS_ACTIONS


def applicable_actions(subject_kind: str, domain: str = "manufacturing") -> list[ActionSpec]:
    return [s for s in catalogue_for(domain).values() if subject_kind in s.subject_kinds]


def describe_catalogue(domain: str = "manufacturing") -> list[dict]:
    mapping = hypothesis_actions_for(domain)
    return [{"type": s.type, "category": s.category, "label": s.label, "description": s.description, "subject_kinds": list(s.subject_kinds), "target_types": list(s.target_types), "simulated_outcome": s.simulated_outcome, "reversible": s.reversible, "requires_human_approval": s.requires_human_approval, "default_for_hypotheses": sorted(h for h, (p, _) in mapping.items() if p == s.type)} for s in catalogue_for(domain).values()]
