import { useState } from "react";
import { api } from "../../lib/api/endpoints";
import type { Decision } from "../../lib/types";
import { actionLabel } from "../../lib/language";
import { dateTime, money } from "../../lib/format";
import { useRefresh } from "../../lib/useApi";
import { Icon } from "../common/Icon";
import { ActionParameters } from "./ActionParameters";
import { ActionResult } from "./ActionResult";
import { EvidencePack } from "./EvidencePack";

/** Shown when the AI step failed and REACT's rule-based analysis produced this result instead (Phase 6). */
export function FallbackNotice() {
  return (
    <div className="callout callout-warn" role="note">
      <Icon name="info" />
      <div>
        <strong>AI reasoning was unavailable for part of this investigation.</strong> REACT used its rule-based analysis instead.
        The evidence, the Evidence Support Scores and every figure come from the same deterministic checks, and nothing was guessed.
      </div>
    </div>
  );
}

const REVIEWER_KEY = "react.reviewer";

function rememberedReviewer(): string {
  try {
    return localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

/** REACT's recommendation and the human approval step. The backend enforces every rule;
 *  this component only sends the request and shows what the server returns. */
export function RecommendationPanel({ decision: d, onChange }: { decision: Decision; onChange: (d: Decision) => void }) {
  const [reviewer, setReviewer] = useState(rememberedReviewer);
  const [reason, setReason] = useState("");
  const [mode, setMode] = useState<"idle" | "rejecting">("idle");
  const [busy, setBusy] = useState<"approve" | "reject" | "execute" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { refresh } = useRefresh();

  const act = async (kind: "approve" | "reject" | "execute") => {
    setError(null);
    if (!reviewer.trim()) {
      setError("Enter your name so the approval is recorded against you.");
      return;
    }
    if (kind === "reject" && reason.trim().length < 3) {
      setError("Give a short reason for rejecting.");
      return;
    }
    setBusy(kind);
    try {
      try { localStorage.setItem(REVIEWER_KEY, reviewer.trim()); } catch { /* storage unavailable */ }
      const next = kind === "approve" ? await api.approve(d.decision_id, reviewer.trim()) : kind === "reject" ? await api.reject(d.decision_id, reviewer.trim(), reason.trim()) : await api.execute(d.decision_id, reviewer.trim());
      onChange(next);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  if (d.status === "INSUFFICIENT_EVIDENCE") {
    return (
      <div className="stack">
      {d.fallback_used && <FallbackNotice />}
      <div className="callout callout-warn">
        <Icon name="warn" />
        <div>
          <strong>REACT does not recommend acting yet.</strong> {d.recommendation}
          <p className="small" style={{ marginTop: 6 }}>{d.rationale}</p>
        </div>
      </div>
      </div>
    );
  }

  const approval = d.approvals[0];
  const action = d.actions[0];
  const impact = d.expected_impact;

  return (
    <section className="recommendation" aria-labelledby="rec-title">
      {d.fallback_used && <div style={{ padding: "12px 16px 0" }}><FallbackNotice /></div>}
      <div className="recommendation-head">
        <div>
          <div className="section-title" style={{ marginBottom: 2 }}>REACT recommends · {d.llm_used ? "AI-assisted" : "rule-based"} recommendation</div>
          <div className="recommendation-title" id="rec-title">{actionLabel(d.action_type)}{d.target_name ? `: ${d.target_name}` : ""}</div>
        </div>
        <div className="row">
          <span className="badge badge-info">Priority {d.priority}</span>
          <StatusBadge status={d.status} />
        </div>
      </div>
      <div className="recommendation-body">
        <div className="stack">
          <div>
            <div className="section-title">What it means</div>
            <p>{d.recommendation}</p>
          </div>
          <ActionParameters decision={d} />
          {d.alternatives.length > 0 && (
            <div>
              <div className="section-title">Other options REACT considered</div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {d.alternatives.map((a) => <li key={a.action_type}><strong>{actionLabel(a.action_type)}</strong>: <span className="muted">{a.tradeoff}</span></li>)}
              </ul>
            </div>
          )}
        </div>
        <div className="stack">
          <div>
            <div className="section-title">{d.subject_type === "OPPORTUNITY" ? "Potential value" : "At stake if nothing is done"}</div>
            <p className="impact-item-value" style={{ color: d.subject_type === "OPPORTUNITY" ? "var(--gain)" : "var(--critical)" }}>{money(impact.estimated_total ?? null, impact.currency)}</p>
            <p className="muted small">From REACT's impact engine (estimate, synthetic data).</p>
          </div>
          {(d.risks.length > 0 || d.assumptions.length > 0) && (
            <div>
              <div className="section-title">Things to keep in mind</div>
              <ul style={{ margin: 0, paddingLeft: 18 }} className="small">
                {d.risks.map((r) => <li key={r}>{withoutIds(r)}</li>)}
                {d.assumptions.map((a) => <li key={a} className="muted">Assumes: {a}</li>)}
              </ul>
            </div>
          )}
        </div>
      </div>

      {d.status === "AWAITING_APPROVAL" && (
        <div className="approve-box stack">
          <div className="section-title" style={{ marginBottom: 0 }}>Your decision</div>
          <div className="row" style={{ alignItems: "flex-end" }}>
            <div className="field" style={{ flex: "1 1 220px" }}>
              <label htmlFor="reviewer">Your name</label>
              <input id="reviewer" className="input" value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="e.g. Store owner" autoComplete="name" />
            </div>
            {mode === "idle" && (
              <>
                <button className="btn btn-approve btn-lg" onClick={() => act("approve")} disabled={busy !== null}>
                  <span style={{ width: 16, display: "inline-flex" }}><Icon name="check" /></span>
                  {busy === "approve" ? "Approving…" : "Approve"}
                </button>
                <button className="btn btn-danger btn-lg" onClick={() => setMode("rejecting")} disabled={busy !== null}>Reject</button>
              </>
            )}
          </div>
          {mode === "rejecting" && (
            <div className="stack">
              <div className="field">
                <label htmlFor="reject-reason">Why are you rejecting this?</label>
                <textarea id="reject-reason" className="textarea" value={reason} onChange={(e) => setReason(e.target.value)} />
              </div>
              <div className="row">
                <button className="btn btn-danger" onClick={() => act("reject")} disabled={busy !== null}>{busy === "reject" ? "Rejecting…" : "Confirm rejection"}</button>
                <button className="btn btn-ghost" onClick={() => setMode("idle")}>Cancel</button>
              </div>
            </div>
          )}
          <p className="muted small">Approving runs a <strong>simulated</strong> action: REACT records what would be done. No order is placed, no price changes, no supplier or customer is contacted and no money moves.</p>
          {error && <p className="small" role="alert" style={{ color: "var(--critical)" }}>{error}</p>}
        </div>
      )}

      {d.status === "APPROVED" && (
        <div className="approve-box stack">
          <p>Approved by <strong>{approval?.reviewed_by}</strong>. The simulated action has not been run yet.</p>
          <div className="row">
            <div className="field" style={{ flex: "1 1 220px" }}>
              <label htmlFor="executor">Your name</label>
              <input id="executor" className="input" value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
            </div>
            <button className="btn btn-primary" onClick={() => act("execute")} disabled={busy !== null}>{busy === "execute" ? "Running…" : "Run simulated action"}</button>
          </div>
          {error && <p className="small" role="alert" style={{ color: "var(--critical)" }}>{error}</p>}
        </div>
      )}

      {d.status === "REJECTED" && approval && (
        <div className="approve-box">
          <p><strong>Rejected</strong> by {approval.reviewed_by} on {dateTime(approval.reviewed_at)}: “{approval.reason}”. No action was taken.</p>
        </div>
      )}

      {d.status === "FAILED" && (
        <div className="approve-box"><p role="alert" style={{ color: "var(--critical)" }}>The simulated action failed: {d.error}</p></div>
      )}

      {d.status === "EXECUTED" && action && (
        <div className="approve-box stack">
          <p className="small">Approved by <strong>{approval?.reviewed_by}</strong> on {dateTime(approval?.reviewed_at)}{approval?.reason ? `: “${approval.reason}”` : ""}.</p>
          <ActionResult action={action} />
        </div>
      )}

      {["APPROVED", "EXECUTED", "REJECTED", "FAILED"].includes(d.status) && (
        <div className="approve-box" style={{ paddingTop: 0 }}><EvidencePack decisionId={d.decision_id} status={d.status} /></div>
      )}
    </section>
  );
}

/** Evidence ids belong in Technical details; the owner view drops the parenthesised id lists. */
const withoutIds = (text: string) => text.replace(/\s*\((?:[0-9a-f]{8}-[0-9a-f-]{27}(?:,\s*)?)+\)/g, "");

const STATUS_WORDS: Record<string, [string, string]> = {
  AWAITING_APPROVAL: ["Waiting for your approval", "badge-high"],
  APPROVED: ["Approved", "badge-info"],
  EXECUTING: ["Running", "badge-info"],
  EXECUTED: ["Done (simulated)", "badge-good"],
  REJECTED: ["Rejected", "badge-neutral"],
  FAILED: ["Failed", "badge-critical"],
  DECISION_READY: ["Ready", "badge-info"],
  INSUFFICIENT_EVIDENCE: ["Not enough evidence", "badge-medium"],
};

function StatusBadge({ status }: { status: string }) {
  const [words, tone] = STATUS_WORDS[status] ?? [status, "badge-neutral"];
  return <span className={`badge ${tone}`}>{words}</span>;
}
