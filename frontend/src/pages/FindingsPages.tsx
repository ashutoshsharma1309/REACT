import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api/endpoints";
import type { Finding } from "../lib/types";
import { findingHeadline, supportWords } from "../lib/language";
import { money } from "../lib/format";
import { useApi } from "../lib/useApi";
import { ConfidenceBadge, StageBadge } from "../components/common/Badges";
import { EmptyState, ErrorState, LoadingState } from "../components/common/States";
import { FindingCard, caseLink } from "../components/findings/FindingCard";

type RiskFilter = "all" | "critical" | "stock" | "supply" | "money" | "approval" | "handled";
const STOCK = new Set(["STOCKOUT_RISK", "DEMAND_SUPPLY_MISMATCH", "EXCESS_INVENTORY", "SLOW_MOVING_INVENTORY"]);
const SUPPLY = new Set(["SUPPLIER_DELAY", "SUPPLIER_RELIABILITY"]);
const MONEY = new Set(["MARGIN_DECLINE", "PRICE_ANOMALY", "DISCOUNT_LEAKAGE", "REVENUE_DECLINE", "DEMAND_DECLINE", "REPEAT_CUSTOMER_DECLINE"]);

export function RisksPage() {
  const { data, error, loading, reload } = useApi(() => api.findings("risk_all"), []);
  const [filter, setFilter] = useState<RiskFilter>("all");
  const rows = useMemo(() => {
    const all = data ?? [];
    if (filter === "critical") return all.filter((f) => f.severity === "CRITICAL" || f.severity === "HIGH");
    if (filter === "stock") return all.filter((f) => STOCK.has(f.type));
    if (filter === "supply") return all.filter((f) => SUPPLY.has(f.type));
    if (filter === "money") return all.filter((f) => MONEY.has(f.type));
    if (filter === "approval") return all.filter((f) => f.stage === "AWAITING_APPROVAL");
    if (filter === "handled") return all.filter((f) => f.stage === "ACTION_COMPLETED" || f.stage === "APPROVED");
    return all;
  }, [data, filter]);

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Findings</h1>
          <p>What needs attention in your stock, suppliers, pricing and sales, most serious first. Open one to see why it is happening, what it could cost and what REACT recommends.</p>
        </div>
      </div>
      <div className="row" role="group" aria-label="Filter findings">
        {([["all", "All"], ["critical", "Critical and high"], ["stock", "Stock"], ["supply", "Suppliers"], ["money", "Margin and sales"], ["approval", "Waiting for approval"], ["handled", "Handled"]] as [RiskFilter, string][]).map(([k, label]) => (
          <button key={k} className={`btn btn-sm ${filter === k ? "btn-primary" : ""}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>{label}</button>
        ))}
      </div>
      {loading && !data && <LoadingState title="Loading findings" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && !rows.length && (
        <div className="card"><EmptyState title={filter === "all" ? "Nothing needs attention" : "Nothing matches this filter"} detail={filter === "all" ? "Run a business scan or load a demo scenario to check for risks." : undefined} /></div>
      )}
      <div className="stack">{rows.map((f) => <FindingCard key={f.id} finding={f} />)}</div>
    </div>
  );
}

/** Opportunity card answering: what is it, why did REACT flag it, what could it be worth, what could be done. */
function OpportunityCard({ f }: { f: Finding }) {
  return (
    <article className="card">
      <div className="card-pad stack">
        <div className="row">{f.confidence && <ConfidenceBadge confidence={f.confidence} />}<StageBadge stage={f.stage} /></div>
        <h2 style={{ fontSize: 16 }}><Link to={caseLink(f)}>{findingHeadline(f)}</Link></h2>
        <div className="grid grid-3" style={{ gap: 14 }}>
          <div>
            <div className="section-title">Why REACT flagged it</div>
            <p className="small">{f.description}</p>
          </div>
          <div>
            <div className="section-title">Potential value</div>
            <p className="impact-item-value">{f.impact ? money(f.impact.value, f.impact.currency) : "Not estimated"}</p>
            <p className="muted small">{f.impact?.description ?? ""}</p>
          </div>
          <div>
            <div className="section-title">What could be done</div>
            {f.decision ? (
              <p className="small">{f.decision.summary}</p>
            ) : f.investigation ? (
              <p className="small">Likely cause: {f.investigation.selected_label ?? "not determined"} ({supportWords(f.investigation.evidence_support_score).toLowerCase()}).</p>
            ) : (
              <p className="small muted">Investigate to get an evidence-backed recommendation.</p>
            )}
            <Link to={caseLink(f)} className="btn btn-sm" style={{ marginTop: 8 }}>{f.investigation ? "Open" : "Investigate"} →</Link>
          </div>
        </div>
      </div>
    </article>
  );
}

export function OpportunitiesPage() {
  const { data, error, loading, reload } = useApi(() => api.findings("opportunity"), []);
  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Opportunities</h1>
          <p>Where the business could grow: rising demand, spare capacity, pricing headroom and more. Values are estimates from REACT's impact engine on synthetic data.</p>
        </div>
      </div>
      {loading && !data && <LoadingState title="Loading opportunities" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && !data.length && <div className="card"><EmptyState title="No opportunities found yet" detail="Run a business scan or load the Demand opportunity scenario." /></div>}
      <div className="stack">{data?.map((f) => <OpportunityCard key={f.id} f={f} />)}</div>
    </div>
  );
}

export function InvestigationsPage() {
  const { data, error, loading, reload } = useApi(() => api.findings(undefined, true), []);
  const rows = (data ?? []).filter((f) => f.investigation);
  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Investigations</h1>
          <p>Every finding REACT has investigated, with its conclusion. Open one to see the evidence.</p>
        </div>
      </div>
      {loading && !data && <LoadingState title="Loading investigations" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && !rows.length && <div className="card"><EmptyState title="No investigations yet" detail="Open a risk or opportunity and choose “Investigate with REACT”." /></div>}
      {rows.length > 0 && (
        <div className="card table-wrap">
          <table className="table">
            <thead><tr><th>Finding</th><th>Conclusion</th><th>Evidence</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.id}>
                  <td><Link to={caseLink(f)}>{findingHeadline(f)}</Link><div className="muted small">{f.entity.name ?? f.location}</div></td>
                  <td>{f.investigation!.selected_label ?? "Not determined"}</td>
                  <td>{supportWords(f.investigation!.evidence_support_score)}</td>
                  <td><StageBadge stage={f.stage} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export const FindingsPage = RisksPage;
