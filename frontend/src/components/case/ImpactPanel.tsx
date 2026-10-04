import type { FindingKind, ImpactAnalysis, ImpactResult } from "../../lib/types";
import { humanize, money, moneyExact, number } from "../../lib/format";

const LABELS: Record<string, string> = {
  lost_sales: "Sales lost while out of stock",
  sales_at_risk: "Demand not covered by stock",
  reorder_cost: "Cost of the suggested reorder",
  delay_exposure: "Sales at risk from late deliveries",
  dependent_revenue: "Sales that depend on this supplier (30 days)",
  margin_loss: "Margin lost over the next 90 days",
  suggested_price: "Price that restores the margin",
  discount_leakage: "Extra discount over the next 90 days",
  capital_locked: "Cash locked in surplus stock",
  revenue_shortfall: "Revenue below normal",
  customer_exposure: "Revenue from fewer repeat orders",
  potential_revenue: "Extra revenue over 30 days",
  stock_needed: "Stock to buy for it (at cost)",
};

function isImpactResult(v: unknown): v is ImpactResult {
  return !!v && typeof v === "object" && "value" in v && "formula" in v && typeof (v as ImpactResult).value === "number";
}

function Calculation({ r }: { r: ImpactResult }) {
  return (
    <details className="tech calc">
      <summary>View calculation</summary>
      <div>
        <p className="mono" style={{ overflowWrap: "anywhere" }}>{r.formula}</p>
        <table>
          <tbody>
            {Object.entries(r.inputs).filter(([, v]) => typeof v !== "object" || v === null).map(([k, v]) => (
              <tr key={k}><td>{humanize(k)}</td><td>{typeof v === "number" ? number(v, 2) : String(v ?? "n/a")}</td></tr>
            ))}
            <tr><td><strong>Result</strong></td><td><strong>{moneyExact(r.value, r.currency)}</strong></td></tr>
          </tbody>
        </table>
        <p className="muted" style={{ marginTop: 6 }}>Calculated by REACT's deterministic impact engine from {r.basis.toLowerCase()} records. An estimate, not a promise.</p>
      </div>
    </details>
  );
}

/** ₹ impact as a ledger page: the lines that make up the total, double-ruled, then context lines that are not added.
 *  The backend is the only calculator; this component only lays out its results. */
export function ImpactPanel({ impact, kind }: { impact: ImpactAnalysis; kind: FindingKind }) {
  const currency = impact.currency ?? "INR";
  if (impact.estimated_total === null || impact.estimated_total === undefined) {
    return <p className="muted">REACT could not estimate a rupee amount for this finding. {impact.note ?? ""}</p>;
  }
  const included = new Set(impact.total_includes ?? []);
  const parts = Object.entries(impact).filter((e): e is [string, ImpactResult] => isImpactResult(e[1]));
  const inTotal = parts.filter(([k]) => included.has(k));
  const context = parts.filter(([k]) => !included.has(k));
  const isOpp = kind === "opportunity";
  const demand = impact.potential_additional_demand;
  return (
    <div>
      <div className="impact-label">{isOpp ? "Potential, estimate" : "Estimated, if nothing is done"}</div>
      <div className="big-number" style={{ color: isOpp ? "var(--gain)" : "var(--critical)" }}>{money(impact.estimated_total, currency)}</div>
      {demand && <p style={{ marginTop: 4 }}>About <strong>{number(demand.units_per_day, 1)} extra units a day</strong> ({number(demand.units_over_horizon)} over {demand.horizon_days} days).</p>}
      <div className="ledger" style={{ marginTop: 12 }}>
        <table className="ledger-rows">
          <thead><tr><th scope="col">How it adds up</th><th scope="col" className="amt">Amount</th></tr></thead>
          <tbody>
            {inTotal.map(([k, r]) => (
              <tr key={k}>
                <td>{LABELS[k] ?? humanize(k)}<div className="muted small">{r.description}</div><Calculation r={r} /></td>
                <td className={`amt ${isOpp ? "cr" : "dr"}`}>{money(r.value, r.currency)}</td>
              </tr>
            ))}
            <tr className="total"><td>Total</td><td className={`amt ${isOpp ? "cr" : "dr"}`}>{money(impact.estimated_total, currency)}</td></tr>
          </tbody>
        </table>
        {context.length > 0 && (
          <table className="ledger-rows" style={{ marginTop: 12 }}>
            <thead><tr><th scope="col">For context (not in the total)</th><th scope="col" className="amt">Amount</th></tr></thead>
            <tbody>
              {context.map(([k, r]) => (
                <tr key={k}><td>{LABELS[k] ?? humanize(k)}<div className="muted small">{r.description}</div><Calculation r={r} /></td><td className="amt">{money(r.value, r.currency)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="ledger-note">{impact.wording}</p>
      </div>
      {impact.operational_constraints && impact.operational_constraints.length > 0 && (
        <div className="stack" style={{ marginTop: 12, gap: 6 }}>
          <div className="section-title" style={{ marginBottom: 0 }}>What could limit this</div>
          <ul className="checklist" style={{ marginTop: 0 }}>
            {impact.operational_constraints.map((c) => (
              <li key={c.type + c.detail} className="check-no"><WarnIcon /><span>{c.detail}</span></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function WarnIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true"><path d="M12 8v5m0 3v.01M4.9 4.9a10 10 0 1 0 14.2 14.2A10 10 0 0 0 4.9 4.9z" /></svg>
  );
}
