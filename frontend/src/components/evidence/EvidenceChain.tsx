import type { Evidence, EvidenceGraph, Hypothesis } from "../../lib/types";
import { supportWords } from "../../lib/language";
import { humanize } from "../../lib/format";

/** Business event → signals → evidence → explanations → conclusion, built from the backend evidence graph. */
export function EvidenceChain({ eventLabel, graph, evidence, hypotheses, leadType, conclusion }: {
  eventLabel: string;
  graph: EvidenceGraph["graph"] | undefined;
  evidence: Evidence[];
  hypotheses: Hypothesis[];
  leadType: string | null;
  conclusion: string;
}) {
  const lead = hypotheses.find((h) => h.type === leadType);
  const leadEvidence = lead ? evidence.filter((e) => e.hypothesis_id === lead.hypothesis_id) : [];
  const supporting = leadEvidence.filter((e) => e.supports).length;
  const signals = graph?.counts.signals ?? new Set(evidence.map((e) => e.signal)).size;
  const records = graph?.counts.records;
  return (
    <div className="chain" aria-label="Evidence chain">
      <div className="chain-node">
        <div className="chain-kind">1 · Business event</div>
        <div className="chain-value">{eventLabel}</div>
      </div>
      <div className="chain-node">
        <div className="chain-kind">2 · Signals checked</div>
        <div className="chain-value">{signals} signals</div>
        <div className="muted small">{records !== undefined ? `from ${records} business records` : "from business records"}</div>
      </div>
      <div className="chain-node">
        <div className="chain-kind">3 · Evidence</div>
        <div className="chain-value">{evidence.length} items</div>
        <div className="muted small">{supporting} support the conclusion</div>
      </div>
      <div className="chain-node">
        <div className="chain-kind">4 · Explanations tested</div>
        <div className="chain-value">{hypotheses.length} possible causes</div>
        <div className="muted small">{hypotheses.slice(0, 3).map((h) => h.label ?? humanize(h.type)).join(", ")}{hypotheses.length > 3 ? "…" : ""}</div>
      </div>
      <div className="chain-node" style={{ borderColor: "var(--accent)" }}>
        <div className="chain-kind">5 · Conclusion</div>
        <div className="chain-value">{hypotheses.find((h) => h.type === leadType)?.label ?? humanize(leadType)}</div>
        <div className="muted small">{lead ? supportWords(lead.evidence_support_score) : conclusion}</div>
      </div>
    </div>
  );
}
