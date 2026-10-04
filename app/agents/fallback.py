"""DeterministicProvider: a rule-based stand-in for an LLM.

It fulfils the same three calls as a real model, but derives its answers from the structured
`context` the agents pass alongside the prompt (the finding, the tool catalogue, the deterministic
hypotheses and evidence). It never invents facts: every id it emits comes from that context.

Uses:
  * default provider when no LLM is configured (runs offline, deterministic, tests);
  * fallback when the configured LLM fails and `agent_fallback_to_deterministic` is enabled
    (the run records `fallback_used=True`, so nobody mistakes it for model reasoning).
"""
from __future__ import annotations

from typing import Any

from app.agents.provider import LLMProvider, LLMResponse, ProviderError, validate_into
from app.agents.schemas import ToolCallRequest

EVIDENCE_CATEGORIES = {
    "incident": ["sensor_readings", "production_metrics", "maintenance_records", "machine_components", "historical_incidents", "supplier_batches"],
    "opportunity": ["orders", "customers", "market_signals", "competitor_signals", "inventory", "production_metrics"],
    "risk": ["supplier_batches", "inventory", "inventory_snapshots", "orders", "production_metrics", "financial_metrics"],
}
OBJECTIVES = {
    "incident": "Determine the most likely root cause of {title} and quantify its operational impact using machine, production, maintenance and historical records.",
    "opportunity": "Explain why {title} is happening, whether it is durable, and what capacity, inventory and supply constraints apply.",
    "risk": "Identify the driver behind {title}, the dependent orders and customers, and the exposure if it is not mitigated.",
}
PRIORITY = {"CRITICAL": "P1", "HIGH": "P2", "MEDIUM": "P3", "LOW": "P4"}


class DeterministicProvider(LLMProvider):
    name = "deterministic"
    model = "rule-based"
    is_llm = False

    # -- free text is never needed by the agents; return an explicit marker -----------------------------
    def generate(self, system, messages, *, max_tokens=None, context=None) -> LLMResponse:
        return LLMResponse(text="[deterministic provider: free-text generation not available]", provider=self.name, model=self.model)

    def generate_structured(self, system, messages, schema, *, context=None):
        ctx = context or {}
        task = ctx.get("task")
        builder = {"detection_analysis": self._detection, "investigation_analysis": self._analysis, "evidence_assessment": self._assessment, "decision_recommendation": self._decision}.get(task)
        if builder is None:
            raise ProviderError(f"deterministic provider cannot build task '{task}'")
        payload = builder(ctx)
        return validate_into(schema, payload), LLMResponse(provider=self.name, model=self.model, raw_json=payload)

    def generate_with_tools(self, system, messages, tools, *, context=None) -> LLMResponse:
        ctx = context or {}
        calls = self._plan_tools(ctx, {t["name"] for t in tools})
        return LLMResponse(provider=self.name, model=self.model, tool_calls=calls, stop_reason="tool_use" if calls else "end_turn", text="" if calls else "All relevant tools have been consulted.")

    # -- detection --------------------------------------------------------------------------------------
    def _detection(self, ctx: dict[str, Any]) -> dict[str, Any]:
        f = ctx["finding"]
        kind = f["subject_kind"]
        merchant = f.get("domain") == "merchant"
        entities = []
        for et, key in (("machine", "machine_id"), ("production_line", "production_line_id"), ("product", "product_id"), ("supplier", "supplier_id"), ("inventory", "inventory_id")):
            if f.get(key):
                entities.append({"entity_type": et, "entity_id": str(f[key]), "name": f.get(f"{et}_name") or (f.get("store_name") if et == "inventory" else None)})
        if merchant and f.get("category") and not any(e["entity_type"] == "category" for e in entities):
            entities.append({"entity_type": "category", "entity_id": None, "name": f["category"]})
        if f.get("region"):
            entities.append({"entity_type": "region", "entity_id": None, "name": f["region"]})
        if f.get("entity_type") and f.get("entity_id") and not any(e["entity_id"] == str(f["entity_id"]) for e in entities):
            entities.append({"entity_type": f["entity_type"], "entity_id": str(f["entity_id"]), "name": f.get("entity_name")})
        severity = f.get("severity") or {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(f.get("confidence"), "MEDIUM")
        missing = []
        if kind == "incident" and not f.get("machine_id"):
            missing.append("No machine is attached to this incident; machine-level sensor analysis is not possible.")
        if kind == "opportunity" and not f.get("product_id"):
            missing.append("No single product is attached; demand analysis runs at category / zone scope." if merchant else "No single product is attached; demand analysis runs at category / line scope.")
        if merchant:
            from app.domains.merchant.planner import EVIDENCE_CATEGORIES as M_CATS, OBJECTIVES as M_OBJ

            objective, categories = M_OBJ[kind].format(title=f["title"]), M_CATS[kind]
        else:
            objective, categories = OBJECTIVES[kind].format(title=f["title"]), EVIDENCE_CATEGORIES[kind]
        return {
            "subject_kind": kind, "subject_id": str(f["subject_id"]), "event_type": f["type"], "severity": severity, "priority": PRIORITY.get(severity, "P3"),
            "affected_entities": entities, "investigation_objective": objective, "evidence_categories": categories,
            "missing_information": missing, "summary": f"{f['type']} ({severity}) on {', '.join(e['name'] or e['entity_type'] for e in entities) or 'the business'}: {f['title']}",
        }

    # -- tool planning -----------------------------------------------------------------------------------
    def _plan_tools(self, ctx: dict[str, Any], available: set[str]) -> list[ToolCallRequest]:
        f, called = ctx.get("finding", {}), set(ctx.get("tools_called", []))
        kind = f.get("subject_kind")
        plan: list[tuple[str, dict[str, Any], str]] = []
        if f.get("domain") == "merchant":
            from app.domains.merchant.planner import plan as merchant_plan

            plan = merchant_plan(f)
        elif kind == "incident":
            if f.get("machine_id"):
                plan += [("get_machine_readings", {"machine_id": f["machine_id"], "hours": 24}, "recent vs baseline sensor behaviour"), ("get_maintenance_history", {"machine_id": f["machine_id"]}, "component wear and maintenance schedule"), ("get_previous_incidents", {"machine_id": f["machine_id"], "production_line_id": f.get("production_line_id")}, "similar historical failures")]
            if f.get("production_line_id"):
                plan += [("get_production_metrics", {"production_line_id": f["production_line_id"], "days": 7}, "output, defects and downtime"), ("get_line_context", {"production_line_id": f["production_line_id"]}, "other machines and component supply on the line")]
        elif kind == "opportunity":
            args: dict[str, Any] = {"days": 28}
            if f.get("product_id"):
                args["product_id"] = f["product_id"]
            elif f.get("production_line_id"):
                args["production_line_id"] = f["production_line_id"]
            plan += [("get_customer_orders", args, "demand growth by region, segment and customer"), ("get_market_signals", {"category": f.get("category"), "product_id": f.get("product_id"), "region": f.get("region")}, "synthetic market context"), ("get_competitor_signals", {"category": f.get("category"), "region": f.get("region")}, "competitor pricing and availability")]
            if f.get("product_id"):
                plan.append(("get_inventory_status", {"product_id": f["product_id"]}, "finished-goods cover"))
            if f.get("production_line_id"):
                plan.append(("get_capacity_status", {"production_line_id": f["production_line_id"]}, "spare capacity"))
        else:  # risk
            rtype = f.get("type")
            if rtype == "SUPPLIER_DELAY" and f.get("supplier_id"):
                plan += [("get_supplier_history", {"supplier_id": f["supplier_id"]}, "batch delays and reliability")]
            if f.get("inventory_id"):
                plan += [("get_inventory_status", {"inventory_id": f["inventory_id"]}, "stock position and consumption")]
            if f.get("component_type"):
                plan += [("get_inventory_status", {"component_type": f["component_type"]}, "component stock position")]
            if f.get("production_line_id"):
                plan += [("get_production_metrics", {"production_line_id": f["production_line_id"], "days": 7}, "line output and utilisation")]
            if rtype in ("REVENUE_DECLINE", "MARGIN_DECLINE"):
                plan += [("get_customer_orders", {"category": f.get("category"), "product_id": f.get("product_id"), "days": 28}, "demand and discounting"), ("get_financial_metrics", {"days": 14}, "company revenue and margin"), ("get_competitor_signals", {"category": f.get("category")}, "competitor pressure")]
            if rtype in ("PRODUCTION_BOTTLENECK", "DELIVERY_DELAY", "DEMAND_SUPPLY_MISMATCH") and f.get("production_line_id"):
                plan += [("get_customer_orders", {"production_line_id": f["production_line_id"], "days": 28}, "demand on the line"), ("get_line_context", {"production_line_id": f["production_line_id"]}, "machine and supply state")]
        calls = []
        for name, args, purpose in plan:
            if name in available and name not in called:
                calls.append(ToolCallRequest(tool_name=name, arguments={k: v for k, v in args.items() if v is not None}, purpose=purpose))
        return calls

    # -- investigation analysis ---------------------------------------------------------------------------
    def _analysis(self, ctx: dict[str, Any]) -> dict[str, Any]:
        inv = ctx["investigation"]
        hyps = inv["hypotheses"]
        top = hyps[0] if hyps else None
        out = []
        for h in hyps:
            ess = h["evidence_support_score"]
            band = "plausible" if ess >= 0.6 else "uncertain" if ess >= 0.4 else "unlikely"
            if not h["supporting_evidence_ids"] and not h["contradicting_evidence_ids"]:
                band = "insufficient_evidence"
            out.append({"hypothesis_id": h["hypothesis_id"], "hypothesis_type": h["hypothesis_type"], "evidence_support_score": ess, "assessment": band, "supporting_evidence_ids": h["supporting_evidence_ids"], "contradicting_evidence_ids": h["contradicting_evidence_ids"], "rationale_summary": f"{h['supporting_count']} supporting and {h['contradicting_count']} contradicting evidence items; Evidence Support Score {ess:.2f} ({h['label']})."})
        determined = inv["conclusion"].get("determined", False)
        needed = [] if determined else ["The leading hypothesis is not separated from the alternatives by enough evidence; targeted inspection is required."]
        return {"investigation_id": inv["investigation_id"], "leading_hypothesis_id": top["hypothesis_id"] if top else None, "leading_hypothesis_type": top["hypothesis_type"] if top else None, "hypotheses": out, "insufficient_evidence": not determined, "additional_evidence_needed": needed, "summary": inv["conclusion"].get("statement", "No conclusion.") + (f" Tools consulted: {', '.join(ctx.get('tools_called', [])) or 'none'}.")}

    # -- evidence assessment -------------------------------------------------------------------------------
    def _assessment(self, ctx: dict[str, Any]) -> dict[str, Any]:
        inv, lead = ctx["investigation"], ctx.get("leading") or {}
        supporting, contradicting = lead.get("supporting_evidence_ids", []), lead.get("contradicting_evidence_ids", [])
        ess = lead.get("evidence_support_score")
        determined = inv["conclusion"].get("determined", False)
        missing = []
        if lead.get("unknown_weight", 0) > 0:
            missing.append(f"{lead.get('unobserved_conditions', 0)} supporting condition(s) of {lead.get('hypothesis_type')} had no observation in the available records.")
        if not determined:
            missing.append("Evidence does not separate the leading hypothesis from the runner-up (margin below threshold).")
        conflicts = []
        if contradicting:
            conflicts.append({"description": f"{len(contradicting)} evidence item(s) contradict {lead.get('hypothesis_type')} while {len(supporting)} support it.", "evidence_ids": contradicting})
        unsupported = ctx.get("unsupported_claims", [])
        sufficient = bool(determined and supporting and (ess or 0) >= 0.5)
        return {"investigation_id": inv["investigation_id"], "hypothesis_id": lead.get("hypothesis_id"), "verified": bool(supporting), "sufficient": sufficient, "evidence_support_score": ess, "supporting_evidence_ids": supporting, "contradicting_evidence_ids": contradicting, "missing_evidence": missing, "conflicts": conflicts, "unsupported_claims": unsupported, "summary": (f"{len(supporting)} supporting evidence item(s) verified against stored records for {lead.get('hypothesis_type')}; Evidence Support Score {ess:.2f}. " if ess is not None else "No leading hypothesis. ") + ("Evidence is sufficient for a conclusion." if sufficient else "Evidence is insufficient for a firm conclusion.")}

    # -- decision recommendation ------------------------------------------------------------------------
    def _decision(self, ctx: dict[str, Any]) -> dict[str, Any]:
        f, res = ctx["finding"], ctx["result"]
        lead = ctx.get("leading") or {}
        actions = {a["type"]: a for a in ctx.get("actions", [])}
        default = ctx.get("default_action") or {}
        ess = res.get("evidence_support_score")
        concluded = res.get("status") == "CONCLUDED" and ess is not None and ess >= 0.5
        priority = PRIORITY.get(f.get("severity") or f.get("confidence") or "MEDIUM", "P3")  # opportunities carry confidence, not severity
        base = {"subject_kind": f["subject_kind"], "subject_id": str(f["subject_id"]), "priority": priority, "hypothesis_id": res.get("hypothesis_id"), "hypothesis_type": res.get("hypothesis_type"), "evidence_support_score": ess, "evidence_ids": list(res.get("supporting_evidence_ids", [])), "contradicting_evidence_ids": list(res.get("contradicting_evidence_ids", [])), "requires_human_approval": True}
        if not concluded:
            missing = res.get("missing_evidence") or ["The investigation did not reach a grounded conclusion."]
            return {**base, "decision_readiness": "INSUFFICIENT_EVIDENCE", "action_type": None, "target_type": None, "target_id": None, "summary": f"No action recommended for {f['title']}: evidence is insufficient.", "problem_statement": f"{f['type']}: {f['title']}. The investigation status is {res.get('status')} with Evidence Support Score {ess if ess is not None else 'n/a'}.", "recommendation": "Do not act yet. Collect the missing evidence and re-run the investigation before deciding.", "rationale": "The leading hypothesis is not adequately separated or verified: " + " ".join(missing[:3]) + (f" Evidence considered: {', '.join(base['evidence_ids'][:5])}." if base["evidence_ids"] else ""), "required_actions": ["Collect the missing evidence listed above", "Re-run the investigation"], "alternatives": [], "risks": ["Acting on unverified evidence could target the wrong cause."], "assumptions": []}
        primary = default.get("primary") or next(iter(actions), None)
        spec = actions.get(primary) or {}
        targets = ctx.get("targets", [])
        target = next((t for t in targets if t["target_type"] in spec.get("target_types", [])), targets[0] if targets else None)
        ev = base["evidence_ids"]
        claims = ctx.get("leading_claims", [])[:4]
        rationale = f"{res.get('hypothesis_type')} is {'strongly ' if ess >= 0.8 else ''}supported by the available records (Evidence Support Score {ess:.2f}; evidence {', '.join(ev[:6])}). " + (" ".join(claims) + " " if claims else "") + "The deterministic impact estimate attached to this decision quantifies the exposure; the recommended action is the catalogue default for this conclusion."
        alts = [{"action_type": a, "summary": actions[a]["label"], "tradeoff": actions[a]["description"]} for a in default.get("alternatives", []) if a in actions]
        risks = list(spec.get("default_risks", [])) + ([f"{len(base['contradicting_evidence_ids'])} contradicting evidence item(s) exist ({', '.join(base['contradicting_evidence_ids'][:3])})."] if base["contradicting_evidence_ids"] else [])
        assumptions = list(spec.get("default_assumptions", [])) + ["All figures are synthetic estimates from the deterministic impact engine."]
        readiness = "READY" if ess >= 0.6 and not res.get("missing_evidence") else "NEEDS_REVIEW"
        tname = target["name"] if target else "the affected entity"
        cause = lead.get("hypothesis_label") or res.get("hypothesis_type")
        return {**base, "decision_readiness": readiness, "action_type": primary, "target_type": target["target_type"] if target else None, "target_id": target["target_id"] if target else None,
                "summary": f"{spec.get('label', primary)} for {tname}: {str(cause)[:1].lower() + str(cause)[1:]} (Evidence Support Score {ess:.2f}).",
                "problem_statement": f"{f['type']} ({f.get('severity') or f.get('confidence')}): {f['title']}. Conclusion: {res.get('conclusion')}",
                "recommendation": f"{spec.get('label', primary)}: {spec.get('description', '')} Target: {tname}.",
                "rationale": rationale, "required_actions": [spec.get("simulated_outcome", "Task created")] + [f"Review contradicting evidence {i}" for i in base["contradicting_evidence_ids"][:2]],
                "alternatives": alts, "risks": risks, "assumptions": assumptions}
