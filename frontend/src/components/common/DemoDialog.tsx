import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api/endpoints";
import { ApiError } from "../../lib/api/client";
import type { DemoResult } from "../../lib/types";
import { useApi, useRefresh } from "../../lib/useApi";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { ErrorState, LoadingState } from "./States";

/** Loads a deterministic scenario through POST /demo/run (reset → scenario → business scan). */
export function DemoDialog({ onClose }: { onClose: () => void }) {
  const scenarios = useApi(() => api.demoScenarios(), []);
  const [running, setRunning] = useState<string | null>(null);
  const [result, setResult] = useState<DemoResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const { refresh } = useRefresh();
  const navigate = useNavigate();

  const run = async (scenario: string) => {
    setRunning(scenario);
    setError(null);
    // Leave any case page first: the reset replaces every finding, so a mounted case page would
    // otherwise refetch ids that no longer exist.
    navigate("/");
    try {
      const r = await api.runDemo(scenario);
      setResult(r);
      refresh();
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setRunning(null);
    }
  };

  return (
    <Dialog title="Demo scenarios" subtitle="Each scenario rebuilds the simulated business with one event (a supplier slowing down, a cost increase, a demand surge…) and then runs REACT's real detection. Results are the same every time." onClose={onClose}>
      {running ? (
        <LoadingState title="Preparing scenario" detail="Simulating 120 days of sales, stock and deliveries with the event, then scanning for risks and opportunities. This takes a few seconds." />
      ) : result ? (
        <div className="stack">
          <div className="callout">
            <Icon name="check" />
            <div>
              <strong>{result.label} loaded.</strong> {result.simulation_summary}
              <div className="small" style={{ marginTop: 4 }}>
                The scan found {result.detected.risks} risk{result.detected.risks === 1 ? "" : "s"} and {result.detected.opportunities} opportunit{result.detected.opportunities === 1 ? "y" : "ies"}.
              </div>
            </div>
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            {result.featured && (
              <button className="btn btn-primary" onClick={() => { onClose(); navigate(`/cases/${result.featured!.kind}/${result.featured!.id}`); }}>
                Open the main finding <Icon name="arrow" />
              </button>
            )}
            <button className="btn" onClick={onClose}>Back to dashboard</button>
          </div>
        </div>
      ) : (
        <div className="stack">
          {error && <ErrorState error={error} title="Scenario could not be loaded" />}
          {scenarios.loading && <LoadingState title="Loading scenarios" />}
          {scenarios.error && <ErrorState error={scenarios.error} onRetry={scenarios.reload} />}
          {scenarios.data?.map((s) => (
            <button key={s.scenario} className="option" onClick={() => run(s.scenario)} disabled={running !== null}>
              <span style={{ width: 18, marginTop: 2, color: "var(--accent)" }}><Icon name={s.scenario === "baseline" ? "refresh" : "play"} /></span>
              <span>
                <strong>{s.label}</strong>
                <span className="muted small" style={{ display: "block" }}>{s.story}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </Dialog>
  );
}
