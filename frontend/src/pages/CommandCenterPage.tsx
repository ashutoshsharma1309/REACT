import { Link } from "react-router-dom";
import { api } from "../lib/api/endpoints";
import { useApi } from "../lib/useApi";
import { ActivityFeed } from "../components/activity/ActivityFeed";
import { BusinessHealth } from "../components/command-center/BusinessHealth";
import { StakeLedger } from "../components/command-center/StakeLedger";
import { StatusBanner } from "../components/command-center/StatusBanner";
import { ValueStrip } from "../components/command-center/ValueStrip";
import { FindingCard } from "../components/findings/FindingCard";
import { EmptyState, ErrorState, Skeleton } from "../components/common/States";

/** The merchant's first screen: how the shop is doing, what needs attention, what it is worth, what was done. */
export function CommandCenterPage() {
  const { data: o, error, loading, reload } = useApi(() => api.overview(), []);

  if (error) return <ErrorState error={error} title="Could not load the dashboard" onRetry={reload} />;
  if (loading && !o) {
    return (
      <div className="stack-lg" role="status" aria-label="Loading">
        <Skeleton height={88} />
        <div className="grid grid-main"><Skeleton height={320} /><Skeleton height={320} /></div>
      </div>
    );
  }
  if (!o) return null;
  const recentDecisions = o.recent_activity.filter((a) => a.kind === "decision" || a.kind === "simulation").slice(0, 6);

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>{o.company.name ?? "Your business"}</h1>
          <p>REACT checks your sales, stock and suppliers, explains every finding with your own records, prices it in rupees and recommends what to do. Nothing happens until you approve.</p>
        </div>
      </div>

      <StatusBanner overview={o} />
      <BusinessHealth overview={o} />

      <div className="grid grid-main">
        <div className="stack-lg">
          <section aria-labelledby="attention-title" className="stack">
            <div className="spread">
              <h2 id="attention-title">Needs your attention</h2>
              <Link to="/findings" className="small">All findings →</Link>
            </div>
            {o.attention.length ? (
              o.attention.map((f) => <FindingCard key={f.id} finding={f} compact />)
            ) : (
              <div className="card">
                <EmptyState
                  title={o.scanned ? "Nothing needs your attention" : "No scan has run yet"}
                  detail={o.scanned ? "REACT found no open issues in your records." : "Use “Scan business” to check your records, or “Demo scenarios” to load an event."}
                />
              </div>
            )}
          </section>

          {o.addressed.length > 0 && (
            <section aria-labelledby="handled-title" className="stack">
              <h2 id="handled-title">Handled</h2>
              {o.addressed.map((f) => <FindingCard key={f.id} finding={f} compact />)}
            </section>
          )}

          <section aria-labelledby="opps-title" className="stack">
            <div className="spread">
              <h2 id="opps-title">Opportunities</h2>
              <Link to="/opportunities" className="small">All opportunities →</Link>
            </div>
            {o.opportunities.length ? (
              <div className="grid grid-2">{o.opportunities.map((f) => <FindingCard key={f.id} finding={f} compact />)}</div>
            ) : (
              <div className="card"><EmptyState title="No opportunities right now" detail={o.scanned ? "The latest scan found no growth opportunity." : "Run a scan to look for opportunities."} /></div>
            )}
          </section>
        </div>

        <div className="stack-lg">
          <StakeLedger overview={o} />
          <section className="card" aria-labelledby="activity-title">
            <div className="card-head"><h2 id="activity-title">Recent decisions and events</h2><Link to="/activity" className="small">All →</Link></div>
            <div className="card-body"><ActivityFeed items={recentDecisions.length ? recentDecisions : o.recent_activity.slice(0, 6)} emptyText="Nothing has happened yet." /></div>
          </section>
        </div>
      </div>

      {o.value && <ValueStrip value={o.value} />}
    </div>
  );
}
