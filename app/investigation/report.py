"""Structured investigation report (dict + plain-text rendering)."""
from __future__ import annotations

from typing import Any


def _money(v: Any, currency: str = "INR") -> str:
    try:
        return f"{currency} {float(v):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def build_report(kind: str, subject: dict[str, Any], scored: list[dict[str, Any]], conclusion: dict[str, Any], impact: dict[str, Any], trace: list[dict[str, Any]], signals_summary: dict[str, Any]) -> dict[str, Any]:
    top = scored[0] if scored else None
    supporting = [e["claim"] for e in conclusion.get("supporting_evidence", [])]
    contradicting = [e["claim"] for e in conclusion.get("contradicting_evidence", [])]
    currency = impact.get("currency", "INR")
    if impact.get("domain") == "merchant":
        title = {"opportunity": "REACT OPPORTUNITY INVESTIGATION"}.get(kind, "REACT RISK INVESTIGATION")
        included = list(impact.get("total_includes") or [])
        keys = included + [k for k, v in impact.items() if isinstance(v, dict) and "value" in v and "formula" in v and k not in included]
        impact_lines = [f"{k.replace('_', ' ').capitalize()}: {_money(impact[k]['value'], currency)} - {impact[k].get('description', '')}" + ("" if k in included else " (context, not in total)") for k in keys]
        if impact.get("estimated_total") is not None:
            impact_lines.append(f"Estimated total: {_money(impact['estimated_total'], currency)}")
        constraints = [c["detail"] for c in impact.get("operational_constraints", [])]
    elif kind == "incident":
        title = "REACT INVESTIGATION"
        impact_lines = []
        if impact.get("estimated_total") is not None:
            pd = impact.get("projected_downtime_if_unaddressed", {})
            impact_lines = [
                f"Estimated production loss: {_money(impact['estimated_production_loss']['value'], currency)} ({impact['inputs']['lost_units']} units)",
                f"Estimated revenue loss: {_money(impact['estimated_revenue_loss']['value'], currency)}",
                f"Downtime cost to date: {_money(impact['downtime_cost']['value'], currency)} ({impact['inputs']['extra_downtime_hours']}h)",
                f"Projected downtime if unaddressed: {pd.get('estimated_downtime_minutes', 0)} minutes ({_money(pd.get('value'), currency)})",
                f"Defect cost: {_money(impact['defect_cost']['value'], currency)}",
                f"Inventory impact: {impact['inventory_impact']['description']}",
                f"Delivery impact: {impact['delivery_impact']['description']} ({_money(impact['delivery_impact']['components'].get('at_risk_revenue'), currency)} at risk)",
                f"Customer impact: {impact['customer_impact']['description']}",
                f"Estimated total impact: {_money(impact['estimated_total'], currency)}",
            ]
        constraints: list[str] = []
    elif kind == "opportunity":
        title = "REACT OPPORTUNITY INVESTIGATION"
        pad = impact.get("potential_additional_demand", {})
        cap = impact.get("available_production_capacity", {})
        impact_lines = [
            f"Potential additional demand: {pad.get('units_per_day')} units/day (~{pad.get('units_over_horizon')} units over {pad.get('horizon_days')} days)",
            f"Available production capacity: {cap.get('spare_units_per_day')} units/day ({'sufficient' if cap.get('sufficient') else 'insufficient'})",
            f"Estimated additional revenue: {_money(impact.get('estimated_revenue_opportunity', {}).get('value'), currency)}",
            f"Estimated margin opportunity: {_money(impact.get('estimated_margin_opportunity', {}).get('potential_margin_captured'), currency)}",
            f"Inventory requirement: {impact.get('inventory_requirements', {}).get('description')}",
        ] if impact.get("estimated_total") is not None else []
        constraints = [c["detail"] for c in impact.get("operational_constraints", [])]
    else:
        title = "REACT RISK INVESTIGATION"
        impact_lines = [
            f"Detection estimate: {_money((impact.get('detection_estimate') or {}).get('value'), currency)}",
            f"Delivery impact: {impact.get('delivery_impact', {}).get('description')}",
            f"Customer impact: {impact.get('customer_impact', {}).get('description')}",
            f"Estimated total exposure: {_money(impact.get('estimated_total'), currency)}",
        ]
        constraints = []
    report = {
        "title": title, "kind": kind, "subject": subject, "summary": conclusion.get("statement"),
        "top_hypothesis": {"type": top["type"], "label": top["label"], "description": top["description"]} if top else None,
        "evidence_support_score": conclusion.get("evidence_support_score"), "support_label": conclusion.get("support_label"), "determined": conclusion.get("determined"),
        "hypotheses_ranked": [{"rank": h["rank"], "type": h["type"], "label": h["label"], "evidence_support_score": h["evidence_support_score"], "initial_confidence": h["initial_confidence"], "supporting_count": h["supporting_count"], "contradicting_count": h["contradicting_count"]} for h in scored],
        "supporting_evidence": supporting, "contradicting_evidence": contradicting,
        "impact_lines": impact_lines, "constraints": constraints,
        "recommended_next_investigation": conclusion.get("recommended_next_step"),
        "signals_considered": signals_summary, "trace_summary": {"steps": len(trace), "tools_called": sum(1 for t in trace if t.get("step") == "tool")},
        "terminology": "Evidence Support Score = strength of support in the available records (0..1). Not a probability. Impact figures are estimates on synthetic data.",
    }
    report["text"] = render_text(report)
    return report


def render_text(r: dict[str, Any]) -> str:
    lines = [r["title"], "=" * len(r["title"]), ""]
    subj = r["subject"]
    lines += [f"{r['kind'].capitalize()}: {subj.get('reference')}", f"Title: {subj.get('title')}", "", "Summary:", f"  {r['summary']}", ""]
    if r["top_hypothesis"]:
        lines += [f"Top hypothesis: {r['top_hypothesis']['label']} ({r['top_hypothesis']['type']})", f"Evidence Support Score: {r['evidence_support_score']:.2f} ({r['support_label']})", ""]
    lines += ["Hypotheses considered:"] + [f"  {h['rank']}. {h['label']} - ESS {h['evidence_support_score']:.2f} (initial {h['initial_confidence']:.2f}; {h['supporting_count']} for / {h['contradicting_count']} against)" for h in r["hypotheses_ranked"]] + [""]
    lines += ["Supporting evidence:"] + ([f"  - {c}" for c in r["supporting_evidence"]] or ["  - none"]) + [""]
    lines += ["Contradicting evidence:"] + ([f"  - {c}" for c in r["contradicting_evidence"]] or ["  - none"]) + [""]
    heading = "Operational impact (estimated):" if r["kind"] == "incident" else "Potential impact (estimated):" if r["kind"] == "opportunity" else "Exposure (estimated):"
    lines += [heading] + ([f"  {x}" for x in r["impact_lines"]] or ["  n/a"]) + [""]
    if r["constraints"]:
        lines += ["Constraints:"] + [f"  - {c}" for c in r["constraints"]] + [""]
    lines += [f"Recommended next investigation: {r['recommended_next_investigation']}", "", r["terminology"]]
    return "\n".join(lines)
