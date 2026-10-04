import type { Overview } from "../../lib/types";
import { money, number, pct } from "../../lib/format";

function Delta({ value, suffix }: { value: number | null | undefined; suffix: string }) {
  if (value === null || value === undefined) return <span>{suffix}</span>;
  return (
    <span>
      <span className={value >= 0 ? "up" : "down"}>{value >= 0 ? "▲" : "▼"} {pct(value, 1)}</span> {suffix}
    </span>
  );
}

/** Business health in five figures, all from the backend (sales records, inventory positions, open findings). */
export function BusinessHealth({ overview: o }: { overview: Overview }) {
  const h = o.business_health;
  if (!h) return null;
  const inv = h.inventory;
  const open = o.counts.critical + o.counts.high + o.counts.other;
  return (
    <section className="kpis" aria-label={`Business health, ${h.period.label.toLowerCase()}`}>
      <div className="kpi">
        <div className="kpi-label">Revenue · {h.period.label.toLowerCase()}</div>
        <div className="kpi-value">{money(h.revenue, h.currency)}</div>
        <div className="kpi-sub"><Delta value={h.revenue_change_pct} suffix="vs previous week" /></div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Gross margin</div>
        <div className="kpi-value">{money(h.gross_margin, h.currency)}</div>
        <div className="kpi-sub">{h.gross_margin_rate !== null ? `${pct(h.gross_margin_rate * 100, 1)} of revenue` : "n/a"}</div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Inventory health</div>
        <div className="kpi-value">{inv.healthy_pct !== null ? pct(inv.healthy_pct, 0) : "n/a"}</div>
        <div className="kpi-sub" title={inv.rule}>{number(inv.short_of_stock)} short · {number(inv.overstocked)} overstocked</div>
      </div>
      <div className="kpi" data-tone={open ? "risk" : undefined}>
        <div className="kpi-label">Open issues</div>
        <div className="kpi-value">{number(open)}</div>
        <div className="kpi-sub">{o.counts.pending_decisions} waiting for your approval</div>
      </div>
      <div className="kpi" data-tone={o.impact.exposure_total ? "risk" : undefined}>
        <div className="kpi-label">At stake (estimate)</div>
        <div className="kpi-value">{o.impact.exposure_total ? money(o.impact.exposure_total, o.impact.currency) : "₹0"}</div>
        <div className="kpi-sub">{o.impact.opportunity_total ? `${money(o.impact.opportunity_total, o.impact.currency)} opportunity` : "No open opportunity"}</div>
      </div>
    </section>
  );
}
