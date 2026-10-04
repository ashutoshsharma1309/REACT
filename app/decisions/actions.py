"""Simulated action executor.

Executes ONLY approved actions whose type is in the catalogue. Every execution produces a structured
result record (a simulated task / proposal) and modifies no business data. There are no emails,
purchases, production changes, supplier changes, customer communications or external calls.
"""
from __future__ import annotations

import math
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.base import utcnow
from app.decisions.catalogue import CATALOGUE, catalogue_for
from app.models import ActionExecution, Decision, Inventory, Machine, Product, ProductionLine, Supplier
from app.models.enums import ActionStatus
from app.observability import get_logger, log_event
from app.simulation import catalog as cat

log = get_logger("react.actions")


class ActionExecutionError(RuntimeError):
    pass


def _task(kind: str, target: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"task_id": f"SIM-{kind[:4].upper()}-{uuid.uuid4().hex[:8]}", "task_type": kind, "status": "OPEN", "target": target, **fields, "simulated": True, "side_effects": "none", "business_records_modified": False}


class ActionExecutor:
    """Deterministic, read-only against business data; writes only the action's own result."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def execute(self, action: ActionExecution, decision: Decision, executed_by: str = "system") -> dict[str, Any]:
        # The engine moves an APPROVED action to EXECUTING (state machine) before calling the executor,
        # so EXECUTING is the only state accepted here; it is unreachable without an approval.
        if action.status != ActionStatus.EXECUTING:
            raise ActionExecutionError(f"action is {action.status.value}; only approved actions in EXECUTING state can be executed")
        if decision.status.value != "EXECUTING":
            raise ActionExecutionError(f"decision is {decision.status.value}; execution requires an approved decision in EXECUTING state")
        from app.domains import current_domain

        domain = current_domain(self.db)
        catalogue = catalogue_for(domain)
        if action.action_type not in catalogue:
            raise ActionExecutionError(f"action type {action.action_type} is not in the catalogue")
        if action.decision_id != decision.id or not action.approval_id:
            raise ActionExecutionError("action is not linked to an approved decision")
        spec = catalogue[action.action_type]
        target = {"type": action.target_type, "id": action.target_id, "name": action.target_name}
        if domain == "merchant":
            from app.domains.merchant.actions import execute as merchant_execute

            result = merchant_execute(self.db, action, decision, target, executed_by)
        else:
            handler = getattr(self, f"_do_{action.action_type.lower()}", self._do_generic)
            result = handler(action, decision, target)
        result.update({"action_type": action.action_type, "catalogue_outcome": spec.simulated_outcome, "decision_id": str(decision.id), "approval_id": str(action.approval_id), "evidence_ids": list(decision.evidence_ids or [])[:20], "hypothesis_type": decision.hypothesis_type, "executed_by": executed_by, "executed_at": utcnow().isoformat()})
        log_event(log, "action.executed", action_id=action.id, decision_id=decision.id, action_type=action.action_type, target=target, simulated=True)
        return result

    # ---- handlers (all simulated) -------------------------------------------------------------------
    def _do_generic(self, action, decision, target) -> dict[str, Any]:
        return _task(action.action_type.lower(), target, notes=CATALOGUE[action.action_type].simulated_outcome)

    def _machine(self, target) -> Machine | None:
        try:
            return self.db.get(Machine, uuid.UUID(str(target["id"]))) if target.get("id") else None
        except ValueError:
            return None

    def _do_inspect_machine(self, action, decision, target) -> dict[str, Any]:
        m = self._machine(target)
        comp = min(m.components, key=lambda c: c.condition_score) if m and m.components else None
        return _task("inspection", target, machine_code=m.code if m else None, focus_component={"id": str(comp.id), "name": comp.name, "type": comp.component_type, "condition_score": comp.condition_score} if comp else None, checklist=["vibration spectrum", "temperature trend", "lubrication", "sensor calibration"], due=(utcnow() + timedelta(days=1)).date().isoformat(), notes="Inspection task created for the maintenance team (simulated).")

    def _do_schedule_maintenance(self, action, decision, target) -> dict[str, Any]:
        m = self._machine(target)
        comp = min(m.components, key=lambda c: c.condition_score) if m and m.components else None
        window = utcnow().date() + timedelta(days=(6 - utcnow().weekday()) % 7 or 7)  # next Sunday (plant closed)
        return _task("maintenance_work_order", target, machine_code=m.code if m else None, component={"id": str(comp.id), "name": comp.name, "type": comp.component_type, "part_number": comp.part_number, "condition_score": comp.condition_score} if comp else None, planned_window=window.isoformat(), estimated_duration_hours=8, notes="Work order created for component replacement in the next planned window (simulated; no maintenance record written).")

    def _do_monitor_machine(self, action, decision, target) -> dict[str, Any]:
        m = self._machine(target)
        return _task("monitoring_profile", target, machine_code=m.code if m else None, sensors=[{"code": s.code, "tightened_warning_threshold": round(s.warning_threshold * 0.9, 2)} for s in (m.sensors if m else [])], sampling="every 15 minutes", notes="Enhanced monitoring profile proposed (simulated; sensor thresholds unchanged).")

    def _do_review_production_plan(self, action, decision, target) -> dict[str, Any]:
        line = self.db.get(ProductionLine, uuid.UUID(str(target["id"]))) if target.get("id") else None
        return _task("planning_review", target, line_code=line.code if line else None, capacity_units_per_day=line.capacity_units_per_day if line else None, scope=["load balancing across lines", "shift plan", "order sequencing"], notes="Production planning review task created (simulated).")

    def _do_review_supplier(self, action, decision, target) -> dict[str, Any]:
        sup = None
        if target.get("type") == "supplier" and target.get("id"):
            sup = self.db.get(Supplier, uuid.UUID(str(target["id"])))
        elif target.get("type") == "inventory" and target.get("id"):
            inv = self.db.get(Inventory, uuid.UUID(str(target["id"])))
            sup = self.db.scalar(select(Supplier).where(Supplier.component_type == inv.component_type, Supplier.is_primary.is_(True))) if inv and inv.component_type else None
        alternates = self.db.scalars(select(Supplier).where(Supplier.component_type == sup.component_type, Supplier.id != sup.id)).all() if sup else []
        return _task("supplier_review", target, supplier={"id": str(sup.id), "name": sup.name, "component_type": sup.component_type, "lead_time_days": sup.lead_time_days, "baseline_lead_time_days": sup.baseline_lead_time_days, "reliability_score": sup.reliability_score} if sup else None, alternate_sources=[{"id": str(a.id), "name": a.name, "lead_time_days": a.lead_time_days} for a in alternates], agenda=["delivery performance", "corrective action plan", "secondary source qualification"], notes="Supplier review task created (simulated; no supplier contacted).")

    def _do_increase_reorder(self, action, decision, target) -> dict[str, Any]:
        inv = self.db.get(Inventory, uuid.UUID(str(target["id"]))) if target.get("type") == "inventory" and target.get("id") else None
        if inv is None:
            return self._do_generic(action, decision, target)
        lead = None
        if inv.component_type:
            sups = self.db.scalars(select(Supplier).where(Supplier.component_type == inv.component_type)).all()
            lead = max((s.lead_time_days for s in sups), default=None)
        elif inv.product and inv.product.production_line:
            spec = next((ls for ls in cat.LINES if ls.code == inv.product.production_line.code), None)
            lead = spec.production_lead_days if spec else None
        lead = lead or 7
        proposed_reorder = math.ceil(inv.avg_daily_consumption * (lead + 5))
        proposed_safety = math.ceil(inv.avg_daily_consumption * 5)
        return _task("reorder_policy_proposal", target, item=inv.item_name, current={"reorder_point": inv.reorder_point, "safety_stock": inv.safety_stock, "quantity_available": inv.quantity_available}, proposed={"reorder_point": proposed_reorder, "safety_stock": proposed_safety}, basis=f"avg_daily_consumption {inv.avg_daily_consumption} x (lead time {lead} d + 5 d)", notes="Reorder-point change proposed for planner confirmation (simulated; inventory policy unchanged).")

    def _do_reduce_purchase(self, action, decision, target) -> dict[str, Any]:
        return _task("purchase_reduction_proposal", target, proposed_reduction_pct=20, notes="Purchase / production volume reduction proposed for planner confirmation (simulated).")

    def _do_review_stock(self, action, decision, target) -> dict[str, Any]:
        return _task("stock_review", target, scope=["cycle count", "allocation to open orders", "expedite options"], notes="Stock review task created (simulated).")

    def _do_redistribute_inventory(self, action, decision, target) -> dict[str, Any]:
        return _task("redistribution_proposal", target, notes="Redistribution proposal created for logistics review (simulated).")

    def _do_contact_customer(self, action, decision, target) -> dict[str, Any]:
        return _task("customer_outreach", target, notes="Outreach task list created for the sales team. No customer has been contacted (simulated).")

    def _do_review_pricing(self, action, decision, target) -> dict[str, Any]:
        prod = self.db.get(Product, uuid.UUID(str(target["id"]))) if target.get("type") == "product" and target.get("id") else None
        return _task("pricing_review", target, product={"sku": prod.sku, "selling_price": float(prod.selling_price), "unit_cost": float(prod.unit_cost), "target_margin": prod.target_margin} if prod else None, notes="Pricing review task created (simulated; prices unchanged).")

    def _do_increase_production(self, action, decision, target) -> dict[str, Any]:
        prod = self.db.get(Product, uuid.UUID(str(target["id"]))) if target.get("type") == "product" and target.get("id") else None
        return _task("production_increase_proposal", target, product=prod.name if prod else None, line=prod.production_line.code if prod and prod.production_line else None, notes="Production increase proposal created for the planning team (simulated; plan unchanged).")

    def _do_review_demand(self, action, decision, target) -> dict[str, Any]:
        return _task("demand_review", target, notes="Demand forecast review task created (simulated).")

    def _do_investigate_market(self, action, decision, target) -> dict[str, Any]:
        return _task("market_investigation", target, notes="Market investigation task created (simulated).")

    def _do_increase_capacity(self, action, decision, target) -> dict[str, Any]:
        return _task("capacity_proposal", target, notes="Capacity increase proposal created (simulated; no shift changes made).")

    def _do_prioritize_product(self, action, decision, target) -> dict[str, Any]:
        return _task("product_prioritisation", target, notes="Product prioritisation task created for planning and sales (simulated).")
