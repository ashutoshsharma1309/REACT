import type { AgentRun, Decision, Investigation } from "../../lib/types";
import { humanize } from "../../lib/format";
import { dateTime, number } from "../../lib/format";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api/endpoints";
import { ErrorState, LoadingState } from "../common/States";

/** Analyst view: scores, hypotheses, agent trace, tool calls, ids. Loaded only when opened. */
export function TechnicalDetails({ investigation, decision, agentRunId }: { investigation: Investigation; decision: Decision | undefined; agentRunId: string | null }) {
  const run = useApi<AgentRun>(agentRunId ? () => api.agentRun(agentRunId) : null, [agentRunId]);
  return (
    <div className="stack-lg">
      <section className="card card-pad">
        <h2>Evidence Support Score</h2>
        <p className="muted small" style={{ marginTop: 4 }}>
          A deterministic 0–1 score of how strongly the stored records support each explanation, combining relevance, recency, source reliability,
          correlation and consistency. It is not a probability or a model confidence.
        </p>
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table className="table">
            <thead><tr><th>Rank</th><th>Explanation</th><th>Code</th><th>Evidence Support Score</th><th>For / against</th><th>Initial trigger share</th></tr></thead>
            <tbody>
              {investigation.hypotheses.map((h) => (
                <tr key={h.hypothesis_id}>
                  <td>{h.rank}</td>
                  <td>{h.label ?? humanize(h.type)}</td>
                  <td className="mono">{h.type}</td>
                  <td className="num">{h.evidence_support_score !== null ? h.evidence_support_score.toFixed(3) : "n/a"}</td>
                  <td className="num">{h.supporting_count} / {h.contradicting_count}</td>
                  <td className="num">{number(h.initial_confidence, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <dl className="kv" style={{ marginTop: 12 }}>
          <dt>Investigation</dt><dd className="mono">{investigation.investigation_id}</dd>
          <dt>Conclusion</dt><dd>{investigation.conclusion.statement}</dd>
          {decision && (
            <>
              <dt>Decision</dt><dd className="mono">{decision.decision_id}</dd>
              <dt>Decision readiness</dt><dd>{decision.decision_readiness} (rule-based readiness, not a probability)</dd>
              <dt>Cited evidence ids</dt><dd className="mono" style={{ overflowWrap: "anywhere" }}>{decision.evidence_ids.join(", ") || "none"}</dd>
              <dt>Validation</dt><dd>{decision.validation.checks.map((c) => `${c.check} ${c.passed ? "✓" : "✗"}`).join(" · ")}</dd>
            </>
          )}
        </dl>
      </section>

      <section className="card card-pad">
        <h2>How REACT worked this out</h2>
        {!agentRunId && <p className="muted small" style={{ marginTop: 6 }}>This investigation was run directly by the deterministic engine, without an agent run.</p>}
        {run.loading && <LoadingState title="Loading agent trace" />}
        {run.error && <ErrorState error={run.error} onRetry={run.reload} />}
        {run.data && (
          <div className="stack" style={{ marginTop: 10 }}>
            <p className="small muted">
              Reasoning provider: <strong>{run.data.provider}</strong>
              {run.data.llm_used ? ` (model ${run.data.model})` : " (rule-based; no language model was called)"}
              {run.data.fallback_used ? " · a fallback was used" : ""} · {run.data.tool_calls_count} tool calls · {run.data.llm_calls_count} reasoning steps
            </p>
            <ol className="timeline">
              {run.data.trace.map((t) => (
                <li key={t.trace_id} data-status={t.status === "error" ? "error" : "ok"}>
                  <div className="small"><strong>{t.agent_name.replace("_", " ")}</strong> · {t.event_type.replace("_", " ")} <span className="muted">· {dateTime(t.timestamp)}{t.duration_ms !== null ? ` · ${number(t.duration_ms)} ms` : ""}</span></div>
                  <div className="small" style={{ overflowWrap: "anywhere" }}>{t.summary}</div>
                </li>
              ))}
            </ol>
            <details className="tech">
              <summary>Tool calls ({run.data.tool_calls.length})</summary>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>#</th><th>Agent</th><th>Tool</th><th>Purpose</th><th>Status</th><th>Records</th><th>ms</th></tr></thead>
                  <tbody>
                    {run.data.tool_calls.map((c) => (
                      <tr key={c.tool_call_id}><td>{c.sequence}</td><td>{c.agent_name}</td><td className="mono">{c.tool_name}</td><td className="small">{c.purpose}</td><td>{c.status}</td><td className="num">{c.result_summary.count ?? "n/a"}</td><td className="num">{number(c.duration_ms)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </div>
        )}
      </section>
    </div>
  );
}
