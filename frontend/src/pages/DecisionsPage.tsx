import { Link } from "react-router-dom";
import { api } from "../lib/api/endpoints";
import type { DecisionSummary } from "../lib/types";
import { actionLabel, supportWords } from "../lib/language";
import { dateTime } from "../lib/format";
import { useApi } from "../lib/useApi";
import { SimulatedBadge } from "../components/common/Badges";
import { EmptyState, ErrorState, LoadingState } from "../components/common/States";

function caseOf(d: DecisionSummary): string {
  if (d.incident_id) return `/cases/incident/${d.incident_id}`;
  if (d.opportunity_id) return `/cases/opportunity/${d.opportunity_id}`;
  return `/cases/risk/${d.risk_id}`;
}

const GROUPS: { title: string; statuses: string[]; empty: string }[] = [
  { title: "Waiting for your approval", statuses: ["AWAITING_APPROVAL"], empty: "No decisions are waiting for you." },
  { title: "Approved", statuses: ["APPROVED", "EXECUTING", "EXECUTED"], empty: "Nothing approved yet." },
  { title: "Rejected", statuses: ["REJECTED"], empty: "" },
  { title: "Not enough evidence to act", statuses: ["INSUFFICIENT_EVIDENCE"], empty: "" },
  { title: "Failed", statuses: ["FAILED"], empty: "" },
];

export function DecisionsPage() {
  const { data, error, loading, reload } = useApi(() => api.decisions(), []);
  const items = data?.items ?? [];
  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Decisions</h1>
          <p>REACT recommends; you decide. Approved actions are simulated in this build, so nothing is sent to real systems.</p>
        </div>
      </div>
      {loading && !data && <LoadingState title="Loading decisions" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && !items.length && <div className="card"><EmptyState title="No decisions yet" detail="Investigate a risk or opportunity to get a recommendation." /></div>}
      {items.length > 0 && GROUPS.map((g) => {
        const rows = items.filter((d) => g.statuses.includes(d.status));
        if (!rows.length && !g.empty) return null;
        return (
          <section key={g.title} className="stack">
            <h2>{g.title} <span className="muted">({rows.length})</span></h2>
            {!rows.length && <p className="muted small">{g.empty}</p>}
            {rows.map((d) => (
              <Link key={d.decision_id} to={caseOf(d)} className="card card-pad" style={{ color: "inherit", display: "block" }}>
                <div className="spread">
                  <div style={{ minWidth: 0 }}>
                    <div className="row">
                      <span className="badge badge-info">Priority {d.priority}</span>
                      {d.status === "EXECUTED" && <SimulatedBadge />}
                    </div>
                    <h3 style={{ fontSize: 15, marginTop: 6 }}>{actionLabel(d.action_type)}{d.target_name ? `: ${d.target_name}` : ""}</h3>
                    <p className="muted small">Because: {(d.hypothesis_label ?? "the evidence").toLowerCase()} ({supportWords(d.evidence_support_score).toLowerCase()} by the evidence)</p>
                  </div>
                  <span className="muted small" style={{ whiteSpace: "nowrap" }}>{dateTime(d.updated_at)}</span>
                </div>
              </Link>
            ))}
          </section>
        );
      })}
    </div>
  );
}
