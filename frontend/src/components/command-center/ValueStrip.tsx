import type { ValueMetrics } from "../../lib/types";
import { money, number } from "../../lib/format";

/** What REACT has surfaced and what the merchant decided. Estimated and simulated, never presented as real savings. */
export function ValueStrip({ value: v }: { value: ValueMetrics }) {
  const items: [string, string, string][] = [
    [money(v.potential_loss_identified, v.currency), "Potential loss identified", "estimated"],
    [money(v.opportunity_identified, v.currency), "Opportunity discovered", "potential"],
    [money(v.inventory_capital_at_risk, v.currency), "Cash locked in excess stock", "estimated"],
    [number(v.issues_detected), "Issues detected", `${v.issues_resolved} resolved`],
    [number(v.actions_recommended), "Actions recommended", `${v.actions_rejected} rejected`],
    [number(v.actions_approved), "Actions approved", `${v.simulated_actions_executed} simulated`],
  ];
  return (
    <section className="card" aria-labelledby="value-title">
      <div className="card-head">
        <h2 id="value-title">What REACT has found for you</h2>
        <span className="stamp">Estimated · simulated</span>
      </div>
      <div className="kpis" style={{ border: "none", boxShadow: "none", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", borderRadius: 0 }}>
        {items.map(([val, label, note]) => (
          <div className="kpi" key={label}>
            <div className="kpi-label">{label}</div>
            <div className="kpi-value" style={{ fontSize: 21 }}>{val}</div>
            <div className="kpi-sub">{note}</div>
          </div>
        ))}
      </div>
      {v.revenue_protected_estimate > 0 && (
        <p className="muted small" style={{ padding: "0 18px 14px" }}>
          Approved actions address an estimated {money(v.revenue_protected_estimate, v.currency)} of risk{v.margin_protected_estimate > 0 ? `, including ${money(v.margin_protected_estimate, v.currency)} of margin` : ""}. These are estimates from simulated actions, not realised savings.
        </p>
      )}
    </section>
  );
}
