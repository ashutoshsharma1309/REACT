import type { Evidence, KeySignal } from "../../lib/types";
import { evidenceSentence, sourceLabel, supportWords } from "../../lib/language";
import { Dialog } from "../common/Dialog";
import { WhySignals } from "./WhySignals";

/** "Why does REACT think this?": the key measurements, then every supporting record in plain words,
 *  then what argues against it. All text comes from stored evidence. */
export function WhyDialog({ cause, description, signals, supporting, contradicting, score, onClose, onViewEvidence }: {
  cause: string;
  description: string;
  signals: KeySignal[];
  supporting: Evidence[];
  contradicting: Evidence[];
  score: number | null;
  onClose: () => void;
  onViewEvidence: () => void;
}) {
  const records = supporting.reduce((n, e) => n + e.source_ids.length, 0);
  return (
    <Dialog title={`Why: ${cause.charAt(0).toLowerCase()}${cause.slice(1)}?`} subtitle={description} onClose={onClose}>
      <WhySignals signals={signals} />
      <p className="small" style={{ marginTop: 14 }}>
        <strong>{supporting.length} supporting</strong> and <strong>{contradicting.length} contradicting</strong> evidence items, built from {records} business record{records === 1 ? "" : "s"}.
        Evidence Support Score: <strong>{supportWords(score)}</strong>.
      </p>
      <ol className="stack" style={{ paddingLeft: 20, margin: "10px 0 0", gap: 6 }}>
        {supporting.map((e) => <li key={e.evidence_id} className="small">{evidenceSentence(e)} <span className="muted">({sourceLabel(e.source_type)})</span></li>)}
      </ol>
      {contradicting.length > 0 && (
        <div className="callout callout-warn" style={{ marginTop: 14 }}>
          <div>
            <strong>What does not fit:</strong> {contradicting.slice(0, 3).map((e) => evidenceSentence(e)).join("; ")}. REACT shows these so you can judge for yourself.
          </div>
        </div>
      )}
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 16 }}>
        <button className="btn" onClick={onClose}>Close</button>
        <button className="btn btn-primary" onClick={onViewEvidence}>See the source records</button>
      </div>
    </Dialog>
  );
}
