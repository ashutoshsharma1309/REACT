import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../lib/api/client";
import { api } from "../lib/api/endpoints";
import type { Decision, FindingKind } from "../lib/types";
import { findingHeadline, findingWhere, kindLabel } from "../lib/language";
import { useApi, useRefresh } from "../lib/useApi";
import { ConfidenceBadge, SeverityBadge, StageBadge } from "../components/common/Badges";
import { Icon } from "../components/common/Icon";
import { EmptyState, ErrorState, LoadingState } from "../components/common/States";
import { CaseOverview } from "../components/case/CaseOverview";
import { Stepper } from "../components/case/Stepper";
import { TechnicalDetails } from "../components/case/TechnicalDetails";
import { RecommendationPanel } from "../components/decisions/RecommendationPanel";
import { EvidenceChain } from "../components/evidence/EvidenceChain";
import { EvidenceTable } from "../components/evidence/EvidenceTable";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "evidence", label: "Evidence" },
  { id: "technical", label: "Technical details" },
] as const;
type Tab = (typeof TABS)[number]["id"];

const KINDS: FindingKind[] = ["incident", "risk", "opportunity"];

/** One finding end to end: what happened → why → evidence → impact → recommendation → approval → result. */
export function CasePage() {
  const { kind = "", id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "overview") as Tab;
  const setTab = (t: Tab) => setParams(t === "overview" ? {} : { tab: t }, { replace: true });
  const { refresh } = useRefresh();
  const validKind = KINDS.includes(kind as FindingKind);

  const finding = useApi(validKind ? () => api.finding(kind as FindingKind, id) : null, [kind, id]);
  const decisionId = finding.data?.decision?.id ?? null;
  const decision = useApi(decisionId ? () => api.decision(decisionId) : null, [decisionId]);
  const [decisionOverride, setDecisionOverride] = useState<Decision>();
  useEffect(() => setDecisionOverride(undefined), [decisionId, decision.data]);
  const currentDecision = decisionOverride ?? decision.data;

  const invId = decisionId ? currentDecision?.investigation_id ?? null : finding.data?.investigation?.id ?? null;
  const investigation = useApi(invId ? () => api.investigation(invId) : null, [invId]);
  const evidence = useApi(invId ? () => api.evidence(invId) : null, [invId]);
  const graph = useApi(invId && tab === "evidence" ? () => api.graph(invId) : null, [invId, tab === "evidence"]);

  const [investigating, setInvestigating] = useState(false);
  const [investigateError, setInvestigateError] = useState<string | null>(null);
  const [aiUnavailable, setAiUnavailable] = useState<string | null>(null);
  const investigate = async () => {
    setInvestigating(true);
    setInvestigateError(null);
    setAiUnavailable(null);
    try {
      await api.investigate(kind as FindingKind, id);
      refresh();
    } catch (e) {
      // 503 = the AI step is unavailable and no fallback was allowed: REACT produced nothing, so show that plainly.
      if (e instanceof ApiError && e.status === 503) setAiUnavailable(e.detail);
      else setInvestigateError((e as Error).message);
    } finally {
      setInvestigating(false);
    }
  };

  if (!validKind) return <EmptyState title="Unknown finding type" detail={<Link to="/">Back to the dashboard</Link>} />;
  if (finding.loading && !finding.data) return <LoadingState title="Loading" />;
  if (finding.error) return <ErrorState error={finding.error} title={finding.error.status === 404 ? "This finding does not exist" : "Could not load this finding"} onRetry={finding.reload} />;
  const f = finding.data!;
  const stage = currentDecision?.status === "EXECUTED" ? "ACTION_COMPLETED" : currentDecision?.status === "REJECTED" ? "REJECTED" : currentDecision?.status === "APPROVED" ? "APPROVED" : f.stage;
  const hasInvestigation = !!invId;
  const detailsLoading = (hasInvestigation && (investigation.loading || evidence.loading) && !investigation.data) || (decisionId && decision.loading && !decision.data);

  return (
    <div className="stack-lg">
      <div className="row small">
        <Link to={f.kind === "opportunity" ? "/opportunities" : "/findings"}>← {f.kind === "opportunity" ? "Opportunities" : "Findings"}</Link>
      </div>

      <section className="card case-head" aria-labelledby="case-title">
        <div className="row">
          {f.severity && <SeverityBadge severity={f.severity} />}
          {f.confidence && <ConfidenceBadge confidence={f.confidence} />}
          <span className="tag">{kindLabel(f.kind)}</span>
          <StageBadge stage={stage} />
        </div>
        <h1 className="case-title" id="case-title">{findingHeadline(f)}</h1>
        <p className="muted">{findingWhere(f)}</p>
        <div style={{ marginTop: 16 }}><Stepper stage={stage} /></div>
      </section>

      {aiUnavailable && (
        <section className="card card-pad" role="alert" aria-labelledby="ai-unavailable-title">
          <div className="state">
            <span className="state-error" style={{ width: 28 }}><Icon name="warn" /></span>
            <h3 id="ai-unavailable-title">AI investigation temporarily unavailable</h3>
            <p className="small" style={{ maxWidth: 620 }}>REACT could not reach its AI reasoning service, and this environment does not allow a rule-based substitute. REACT did not produce a conclusion or a recommendation, and nothing in your business was changed.</p>
            <p className="muted small mono" style={{ maxWidth: 620, overflowWrap: "anywhere" }}>{aiUnavailable}</p>
            <button className="btn btn-sm" onClick={investigate} disabled={investigating}><Icon name="refresh" /> Try again</button>
          </div>
        </section>
      )}

      {!hasInvestigation && !decisionId && !aiUnavailable && (
        <section className="card">
          {investigating ? (
            <LoadingState title="Investigation in progress" detail="REACT is checking sales, stock, purchase orders, supplier deliveries, prices and customers, testing possible causes against the evidence, pricing the impact in rupees and preparing a recommendation." />
          ) : (
            <div className="state">
              <h3>{f.kind === "opportunity" ? "REACT has spotted this opportunity but not investigated it yet" : "REACT has detected this but not investigated it yet"}</h3>
              <p className="small" style={{ maxWidth: 560 }}>{f.description}</p>
              <p className="small muted">Investigating tests several possible explanations against your own records, prices what is at stake in rupees and prepares a recommendation for your approval. Nothing is changed.</p>
              <button className="btn btn-primary btn-lg" onClick={investigate}>
                <span style={{ width: 16, display: "inline-flex" }}><Icon name="search" /></span> Investigate with REACT
              </button>
              {investigateError && <p role="alert" className="small" style={{ color: "var(--critical)" }}>{investigateError}</p>}
            </div>
          )}
        </section>
      )}

      {detailsLoading && <LoadingState title="Loading investigation" />}
      {(investigation.error || evidence.error || decision.error) && (
        <ErrorState error={(investigation.error ?? evidence.error ?? decision.error)!} title="Could not load the investigation" onRetry={() => { investigation.reload(); evidence.reload(); decision.reload(); }} />
      )}

      {investigation.data && evidence.data && (
        <>
          <div className="tabs" role="tablist" aria-label="Investigation views">
            {TABS.map((t) => (
              <button key={t.id} role="tab" id={`tab-${t.id}`} aria-selected={tab === t.id} aria-controls={`panel-${t.id}`} onClick={() => setTab(t.id)}>{t.label}</button>
            ))}
          </div>

          {tab === "overview" && (
            <div id="panel-overview" role="tabpanel" aria-labelledby="tab-overview" className="stack-lg">
              <CaseOverview finding={f} investigation={investigation.data} evidence={evidence.data} onViewEvidence={() => setTab("evidence")} />
              <div>
                <h2 style={{ marginBottom: 10 }}>What should I do?</h2>
                {currentDecision ? (
                  <RecommendationPanel decision={currentDecision} onChange={setDecisionOverride} />
                ) : (
                  <div className="card card-pad row spread">
                    <p>REACT investigated this but has not prepared a recommendation yet.</p>
                    <button className="btn btn-primary" onClick={investigate} disabled={investigating}>{investigating ? "Preparing…" : "Get a recommendation"}</button>
                  </div>
                )}
              </div>
            </div>
          )}

          {tab === "evidence" && (
            <div id="panel-evidence" role="tabpanel" aria-labelledby="tab-evidence" className="stack-lg">
              <section className="stack">
                <h2>How REACT reached its conclusion</h2>
                <EvidenceChain eventLabel={findingHeadline(f)} graph={graph.data?.graph} evidence={evidence.data} hypotheses={investigation.data.hypotheses} leadType={investigation.data.conclusion.selected_hypothesis} conclusion={investigation.data.conclusion.statement} />
              </section>
              <section className="stack">
                <h2>The evidence</h2>
                <p className="muted small">Every item comes from a stored business record. “Against” items are shown on purpose so you can judge the conclusion yourself.</p>
                <EvidenceTable evidence={evidence.data} hypotheses={investigation.data.hypotheses} initialHypothesis={investigation.data.conclusion.selected_hypothesis} />
              </section>
            </div>
          )}

          {tab === "technical" && (
            <div id="panel-technical" role="tabpanel" aria-labelledby="tab-technical">
              <TechnicalDetails investigation={investigation.data} decision={currentDecision} agentRunId={currentDecision?.agent_run_id ?? f.agent_run?.id ?? null} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
