import type { Overview } from "../../lib/types";
import { Icon } from "../common/Icon";

const LABEL = { CRITICAL: "Critical", NEEDS_ATTENTION: "Needs attention", HEALTHY: "Healthy" } as const;
const ICON = { CRITICAL: "alert", NEEDS_ATTENTION: "warn", HEALTHY: "shield" } as const;

/** "How is my business doing?" answered in one line, with the counts behind it. */
export function StatusBanner({ overview: o }: { overview: Overview }) {
  const c = o.counts;
  return (
    <section className="card status-banner" data-status={o.business_status} aria-labelledby="business-status">
      <div className="status-icon" aria-hidden="true"><Icon name={ICON[o.business_status]} /></div>
      <div>
        <div className="status-label" id="business-status">Business status: {LABEL[o.business_status]}</div>
        <div className="status-headline">{o.headline}</div>
        {!o.scanned && <p className="muted small" style={{ marginTop: 2 }}>REACT has not scanned the business data yet. Run a scan or load a demo scenario.</p>}
      </div>
      <div className="status-counts">
        <div><div className="stat-value" style={{ color: c.critical ? "var(--critical)" : undefined }}>{c.critical}</div><div className="stat-label">Critical</div></div>
        <div><div className="stat-value">{c.high + c.other}</div><div className="stat-label">Other issues</div></div>
        <div><div className="stat-value" style={{ color: c.pending_decisions ? "var(--high)" : undefined }}>{c.pending_decisions}</div><div className="stat-label">Awaiting approval</div></div>
        <div><div className="stat-value" style={{ color: "var(--info)" }}>{c.opportunities}</div><div className="stat-label">Opportunities</div></div>
      </div>
    </section>
  );
}
