"""Tool access policies: which registered tools each agent may call.

Agents never get database access; they only get names from this allow-list, and every call goes
through the Phase 2 registry (argument validation, logging) via the ToolExecutor.
"""
from __future__ import annotations

from app.investigation.tools import tools_for
from app.models.enums import AgentName

READ_TOOLS = ("get_machine_readings", "get_production_metrics", "get_maintenance_history", "get_previous_incidents", "get_supplier_history", "get_inventory_status", "get_customer_orders", "get_market_signals", "get_competitor_signals", "get_financial_metrics", "get_line_context", "get_capacity_status", "search_business_records")
CALC_TOOLS = ("calculate_production_loss", "calculate_revenue_impact", "calculate_downtime_impact", "calculate_opportunity_value", "calculate_inventory_risk")

AGENT_TOOL_POLICY: dict[str, frozenset[str]] = {
    AgentName.DETECTION.value: frozenset({"search_business_records"}),
    AgentName.INVESTIGATION.value: frozenset(READ_TOOLS + CALC_TOOLS),
    AgentName.EVIDENCE.value: frozenset(READ_TOOLS),  # read-only: verification never computes money
    AgentName.DECISION.value: frozenset(READ_TOOLS),  # read-only; recommendations never compute money or execute anything
    AgentName.ORCHESTRATOR.value: frozenset(),
}

# REACT Merchant allow-lists (same principles: read tools for every reasoning agent, calculations only for investigation)
MERCHANT_READ_TOOLS = ("get_product_sales", "get_inventory_status", "get_sales_history", "get_supplier_history", "get_purchase_orders", "get_customer_orders", "get_customer_segments", "get_pricing_history", "get_discount_history", "get_financial_metrics", "get_expense_history", "get_category_performance", "get_regional_performance", "get_business_baseline", "get_competitor_prices", "search_business_records")
MERCHANT_CALC_TOOLS = ("calculate_stockout_loss", "calculate_margin_loss", "calculate_inventory_capital", "calculate_demand_opportunity", "calculate_reorder_quantity")
MERCHANT_TOOL_POLICY: dict[str, frozenset[str]] = {
    AgentName.DETECTION.value: frozenset({"search_business_records"}),
    AgentName.INVESTIGATION.value: frozenset(MERCHANT_READ_TOOLS + MERCHANT_CALC_TOOLS),
    AgentName.EVIDENCE.value: frozenset(MERCHANT_READ_TOOLS),
    AgentName.DECISION.value: frozenset(MERCHANT_READ_TOOLS),
    AgentName.ORCHESTRATOR.value: frozenset(),
}
POLICIES_BY_DOMAIN = {"manufacturing": AGENT_TOOL_POLICY, "merchant": MERCHANT_TOOL_POLICY}

AGENT_RESPONSIBILITIES: dict[str, str] = {
    AgentName.DETECTION.value: "Interpret an already-detected finding: event type, affected entities, severity, investigation objective, evidence categories, missing information. Never creates findings.",
    AgentName.INVESTIGATION.value: "Select and call registered tools with structured arguments, then assess the deterministically scored hypotheses using only the evidence ids it was given.",
    AgentName.EVIDENCE.value: "Verify grounding: supporting, contradicting and missing evidence by id, conflicts, unsupported claims, and whether the deterministic Evidence Support Score is sufficient.",
    AgentName.DECISION.value: "Convert a verified investigation into one evidence-backed recommendation from the controlled action catalogue, with rationale, alternatives, risks and assumptions. Recommends only; a human approves and the action executor simulates.",
    AgentName.ORCHESTRATOR.value: "State machine, limits, retries, fallback, trace persistence and the final structured result. Calls the Phase 2 investigation and impact engines; never computes money itself.",
}


def allowed_tools(agent_name: str, domain: str = "manufacturing") -> frozenset[str]:
    return POLICIES_BY_DOMAIN.get(domain, AGENT_TOOL_POLICY).get(agent_name, frozenset())


def is_allowed(agent_name: str, tool_name: str, domain: str = "manufacturing") -> bool:
    return tool_name in allowed_tools(agent_name, domain) and tool_name in tools_for(domain)


def policy_report(domain: str = "manufacturing") -> list[dict]:
    return [{"agent": name, "responsibility": AGENT_RESPONSIBILITIES.get(name, ""), "allowed_tools": sorted(tools)} for name, tools in POLICIES_BY_DOMAIN.get(domain, AGENT_TOOL_POLICY).items()]
