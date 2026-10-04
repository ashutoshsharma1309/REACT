import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api/endpoints";
import type { OutageMode, Readiness, ReadinessCheck } from "../lib/types";
import { date, number } from "../lib/format";
import { useApi } from "../lib/useApi";
import { ActivityFeed } from "../components/activity/ActivityFeed";
import { Icon } from "../components/common/Icon";
import { EmptyState, ErrorState, LoadingState } from "../components/common/States";

export function ActivityPage() {
  const { data, error, loading, reload } = useApi(() => api.activity(100), []);
  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Activity</h1>
          <p>Everything REACT and your team did: scenarios loaded, scans, investigations, approvals and simulated actions.</p>
        </div>
      </div>
      {loading && !data && <LoadingState title="Loading activity" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && <div className="card card-pad"><ActivityFeed items={data} emptyText="No activity yet. Run a business scan or load a demo scenario." /></div>}
    </div>
  );
}

const FUTURE_STEPS = ["Upload", "Extract", "Normalise", "Analyse", "REACT insights"];
const FUTURE_TYPES = ["Billing / POS exports (CSV)", "Excel stock registers", "Purchase invoices (PDF)", "UPI settlement reports"];

/** What data REACT is using, how the system is configured, and what is not available yet. */
export function DataPage() {
  const status = useApi(() => api.systemStatus(), []);
  const merchant = useApi(() => api.merchant(), []);
  const agents = useApi(() => api.agents(), []);
  const s = status.data;
  const m = merchant.data;
  const provider = agents.data?.provider;
  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Data &amp; System</h1>
          <p>Where REACT's numbers come from, and what this build can and cannot do.</p>
        </div>
      </div>

      <section className="card card-pad stack">
        <h2>Current data source</h2>
        {status.loading && <LoadingState title="Loading" />}
        {(status.error || merchant.error) && <ErrorState error={(status.error ?? merchant.error)!} onRetry={() => { status.reload(); merchant.reload(); }} />}
        {s && m && (
          <>
            <p>A <strong>simulated business</strong>: {m.business_name}, {m.business_type?.toLowerCase()} in {m.city}. REACT generated {m.data_window ? "120" : "its"} days of sales, stock, purchase orders, supplier deliveries, prices and expenses from one deterministic simulation, so the same scenario always produces the same numbers. No real shop's data is used.</p>
            <dl className="kv">
              <dt>Data window</dt><dd>{s.data_window ? `${date(s.data_window.data_start)} to ${date(s.data_window.data_end)}` : "Not seeded"}</dd>
              <dt>Stores</dt><dd>{m.stores.map((x) => x.name).join("; ")}</dd>
              <dt>Products</dt><dd className="num">{number(m.counts.products)}</dd>
              <dt>Customers</dt><dd className="num">{number(m.counts.customers)}</dd>
              <dt>Orders</dt><dd className="num">{number(m.counts.orders)}</dd>
              <dt>Suppliers</dt><dd className="num">{number(m.counts.suppliers)}</dd>
              <dt>Database</dt><dd>{s.database_status}</dd>
            </dl>
            <p className="small"><Link to="/setup">Set up a different demo business</Link></p>
          </>
        )}
      </section>

      <section className="card card-pad stack">
        <h2>How REACT reasons</h2>
        {agents.error && <ErrorState error={agents.error} onRetry={agents.reload} />}
        {provider && (
          <>
            <p>
              Reasoning provider: <strong>{provider.provider}</strong>
              {provider.is_llm === false && " (rule-based). No language model is connected in this build; the agents run the same tools and checks with deterministic rules."}
              {provider.is_llm && provider.model && ` using model ${provider.model}.`}
            </p>
            <p className="muted small">Every figure you see is calculated by REACT's deterministic engines. Any language model only selects tools and writes explanations, and its output is checked against the stored evidence before it is shown.</p>
          </>
        )}
      </section>

      <ReadinessSection />

      <section className="card card-pad stack future" aria-labelledby="upload-title">
        <div className="row"><h2 id="upload-title">Upload business data</h2><span className="badge badge-neutral">Coming soon</span></div>
        <p>Not available in this build. Planned: bring your own sales and stock data instead of the simulated business.</p>
        <div className="flow" aria-label="Planned flow">
          {FUTURE_STEPS.map((step, i) => (
            <span key={step} className="row" style={{ gap: 8 }}>
              <span className="tag">{step}</span>{i < FUTURE_STEPS.length - 1 && <span aria-hidden="true">→</span>}
            </span>
          ))}
        </div>
        <p className="muted small">Planned file types: {FUTURE_TYPES.join(", ")}. Uploading, extraction and analysis of your own files are not implemented, so REACT will not show any results for them.</p>
        <div><button className="btn" disabled aria-disabled="true"><Icon name="upload" /> Upload (not available)</button></div>
      </section>
    </div>
  );
}

const CHECK_LABELS: Record<string, string> = { database: "Database", migrations: "Database schema version", data: "Business data", storage: "Evidence-pack storage", ai: "AI reasoning" };

function checkText(name: string, c: ReadinessCheck, r: Readiness): string {
  if (name === "storage") return c.backend === "s3" ? `Amazon S3 (${c.status.toLowerCase()})` : `Local folder (${c.status.toLowerCase()})`;
  if (name === "ai") {
    const ai = r.checks.ai;
    if (ai.is_llm === false) return ai.status === "RULE_BASED" ? "Rule-based analysis; no language model is connected" : `${humanStatus(ai.status)} (rule-based, no language model)`;
    const who = `${ai.provider}${ai.model ? `, model ${ai.model}` : ""}`;
    const budget = ai.llm_hourly_call_budget !== undefined ? ` · ${ai.llm_calls_last_hour ?? 0} of ${ai.llm_hourly_call_budget} model calls used this hour` : "";
    return `${humanStatus(ai.status)} (${who})${budget}`;
  }
  if (name === "migrations") return c.status === "NOT_MANAGED" ? "Created automatically (local development)" : humanStatus(c.status);
  return humanStatus(c.status);
}
const humanStatus = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^./, (x) => x.toUpperCase());

const DRILL: { mode: OutageMode; label: string; detail: string }[] = [
  { mode: "off", label: "Normal", detail: "AI reasoning behaves as configured." },
  { mode: "fallback", label: "AI down, use rule-based analysis", detail: "New investigations cannot reach the AI service. REACT switches to its deterministic analysis and labels the result." },
  { mode: "fail", label: "AI down, no substitute", detail: "New investigations stop with “AI investigation temporarily unavailable”. REACT produces no conclusion and changes nothing." },
];

/** Phase 6: live readiness from the backend, plus the AI-failure drill for demonstrations. */
function ReadinessSection() {
  const ready = useApi(() => api.ready(), []);
  const [busy, setBusy] = useState(false);
  const [drillError, setDrillError] = useState<string | null>(null);
  const [selected, setSelected] = useState<OutageMode | null>(null); // shown while the request is in flight
  const r = ready.data;
  const current = selected ?? r?.checks.ai.outage_drill ?? "off";
  const setMode = async (mode: OutageMode) => {
    setBusy(true);
    setDrillError(null);
    setSelected(mode);
    try {
      const res = await api.setOutage(mode);
      setSelected(res.mode); // the backend's answer, not the click
      ready.reload();
    } catch (e) {
      setSelected(null); // revert to what the server reported
      setDrillError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card card-pad stack" aria-labelledby="ready-title">
      <div className="row"><h2 id="ready-title">System readiness</h2>{r && <span className={`badge ${r.status === "ready" ? "badge-good" : "badge-critical"}`}>{r.status === "ready" ? "Ready" : "Not ready"}</span>}</div>
      {ready.loading && !r && <LoadingState title="Checking" />}
      {ready.error && <ErrorState error={ready.error} onRetry={ready.reload} />}
      {r && (
        <>
          <dl className="kv">
            <dt>Environment</dt><dd>{r.environment} · version {r.version}</dd>
            {Object.entries(r.checks).map(([name, c]) => (
              <FragmentKV key={name} k={CHECK_LABELS[name] ?? name} v={`${c.ok ? "OK" : "Problem"}: ${checkText(name, c, r)}`} />
            ))}
          </dl>
          <p className="muted small">Readiness never calls the AI model or cloud storage, so checking it costs nothing. It never shows passwords, keys or connection details.</p>
          {r.demo_controls_enabled && (
            <fieldset className="stack" style={{ border: "1px solid var(--border)", borderRadius: "var(--r-sm)", padding: 12 }}>
              <legend className="section-title" style={{ padding: "0 6px" }}>AI failure drill (demonstration)</legend>
              <p className="small">Simulates the AI service being unreachable. It affects new investigations only and makes no call to any cloud service.</p>
              {DRILL.map((d) => (
                <label key={d.mode} className="row small" style={{ alignItems: "flex-start", gap: 8 }}>
                  <input type="radio" name="ai-drill" value={d.mode} checked={current === d.mode} disabled={busy} onChange={() => setMode(d.mode)} />
                  <span><strong>{d.label}.</strong> <span className="muted">{d.detail}</span></span>
                </label>
              ))}
              {drillError && <p role="alert" className="small" style={{ color: "var(--critical)" }}>{drillError}</p>}
            </fieldset>
          )}
        </>
      )}
    </section>
  );
}

function FragmentKV({ k, v }: { k: string; v: string }) {
  return (<><dt>{k}</dt><dd>{v}</dd></>);
}

export function NotFoundPage() {
  return <EmptyState title="Page not found" detail={<Link to="/">Back to the dashboard</Link>} />;
}
