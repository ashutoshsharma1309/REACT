import { useState } from "react";
import type { Evidence, Finding, Investigation } from "../../lib/types";
import { causeDescription, causeLabel, evidenceSentence, sourceLabel, supportWords } from "../../lib/language";
import { date } from "../../lib/format";
import { Icon } from "../common/Icon";
import { ImpactPanel } from "./ImpactPanel";
import { WhyDialog } from "./WhyDialog";
import { WhySignals } from "./WhySignals";

export function leadEvidence(investigation: Investigation, evidence: Evidence[]) {
  const leadType = investigation.conclusion.selected_hypothesis;
  const lead = investigation.hypotheses.find((h) => h.type === leadType);
  const mine = lead ? evidence.filter((e) => e.hypothesis_id === lead.hypothesis_id) : [];
  const byScore = (a: Evidence, b: Evidence) => b.evidence_support_score - a.evidence_support_score;
  return { lead, supporting: mine.filter((e) => e.supports).sort(byScore), contradicting: mine.filter((e) => e.contradicts).sort(byScore) };
}

/** The owner's view of an investigation: what happened, why, what the evidence says, what does not fit, ₹ impact. */
export function CaseOverview({ finding: f, investigation: inv, evidence, onViewEvidence }: { finding: Finding; investigation: Investigation; evidence: Evidence[]; onViewEvidence: () => void }) {
  const [whyOpen, setWhyOpen] = useState(false);
  const { lead, supporting, contradicting } = leadEvidence(inv, evidence);
  const c = inv.conclusion;
  const isOpp = f.kind === "opportunity";
  const causeWord = isOpp ? "explanation" : "cause";
  const score = lead?.evidence_support_score ?? c.evidence_support_score;
  const cause = causeLabel(inv, c.selected_hypothesis);
  const signals = inv.key_signals ?? [];
  const records = supporting.reduce((n, e) => n + e.source_ids.length, 0);

  return (
    <div className="card">
      <div className="qa">
        <div className="qa-q">{isOpp ? "What REACT spotted" : "What happened"}</div>
        <div className="qa-a">
          <p style={{ fontSize: 16, fontWeight: 600 }}>{f.title}</p>
          <p style={{ marginTop: 4 }}>{f.description}</p>
          <p className="muted small" style={{ marginTop: 4 }}>Detected on {date(f.detected_at)} from {f.evidence_count} business records.</p>
        </div>
      </div>

      <div className="qa">
        <div className="qa-q">{isOpp ? "Why it is happening" : "Why"}</div>
        <div className="qa-a">
          {c.selected_hypothesis ? (
            <>
              <h3 className="cause">{cause}</h3>
              <p className="muted" style={{ marginTop: 4 }}>{causeDescription(inv, c.selected_hypothesis)}</p>
              <div className="row" style={{ marginTop: 8 }}>
                <span className={`badge ${score !== null && score >= 0.6 ? "badge-good" : "badge-medium"}`}>{supportWords(score)} by the evidence</span>
                {c.contributing_factor && <span className="tag">Also contributing: {causeLabel(inv, c.contributing_factor)}</span>}
              </div>
              <WhySignals signals={signals} />
              <div className="row" style={{ marginTop: 12 }}>
                <button className="btn btn-primary" onClick={() => setWhyOpen(true)}>Why does REACT think this?</button>
                <button className="btn btn-ghost" onClick={onViewEvidence}>View all {evidence.length} evidence items <Icon name="arrow" /></button>
              </div>
            </>
          ) : (
            <p>REACT could not identify a likely {causeWord} from the available records.</p>
          )}
        </div>
      </div>

      <div className="qa">
        <div className="qa-q">What the evidence says</div>
        <div className="qa-a">
          {supporting.length ? (
            <>
              <p className="small muted">{supporting.length} supporting item{supporting.length === 1 ? "" : "s"} from {records} record{records === 1 ? "" : "s"}{contradicting.length ? `, ${contradicting.length} against` : ", none against"}.</p>
              <ul className="checklist">
                {supporting.slice(0, 5).map((e) => (
                  <li key={e.evidence_id} className="check-yes"><Icon name="check" /><span>{evidenceSentence(e)} <span className="muted small">({sourceLabel(e.source_type)})</span></span></li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted">No supporting evidence was found for a single {causeWord}.</p>
          )}
        </div>
      </div>

      <div className="qa">
        <div className="qa-q">What does not fit</div>
        <div className="qa-a">
          {contradicting.length ? (
            <>
              <ul className="checklist" style={{ marginTop: 0 }}>
                {contradicting.slice(0, 3).map((e) => (
                  <li key={e.evidence_id} className="check-no"><Icon name="x" /><span>{evidenceSentence(e)}</span></li>
                ))}
              </ul>
              <p className="muted small" style={{ marginTop: 6 }}>REACT always shows the evidence that argues against its conclusion.</p>
            </>
          ) : (
            <p className="muted">Nothing in the records contradicts this {causeWord}.</p>
          )}
        </div>
      </div>

      <div className="qa">
        <div className="qa-q">{isOpp ? "What it could be worth" : "What it could cost"}</div>
        <div className="qa-a"><ImpactPanel impact={inv.impact} kind={f.kind} /></div>
      </div>

      <div className="qa">
        <div className="qa-q">REACT's conclusion</div>
        <div className="qa-a">
          {c.determined && c.selected_hypothesis ? (
            <p>The most likely {causeWord} is <strong>{cause.toLowerCase()}</strong>, {supportWords(score).toLowerCase()} by {supporting.length} pieces of evidence{contradicting.length ? ` with ${contradicting.length} pointing the other way` : ""}.</p>
          ) : (
            <p>The evidence does not clearly point to one {causeWord} yet. REACT will not present a guess as a conclusion.</p>
          )}
          {c.alternatives && c.alternatives.length > 0 && (
            <p className="muted small" style={{ marginTop: 6 }}>Also considered: {c.alternatives.slice(0, 4).map((a) => `${a.label.toLowerCase()} (${supportWords(a.evidence_support_score).toLowerCase()})`).join(", ")}.</p>
          )}
        </div>
      </div>

      {whyOpen && (
        <WhyDialog cause={cause} description={causeDescription(inv, c.selected_hypothesis)} signals={signals} supporting={supporting} contradicting={contradicting} score={score} onClose={() => setWhyOpen(false)} onViewEvidence={() => { setWhyOpen(false); onViewEvidence(); }} />
      )}
    </div>
  );
}
