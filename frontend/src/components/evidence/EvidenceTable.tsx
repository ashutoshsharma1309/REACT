import { useMemo, useState } from "react";
import type { Evidence, Hypothesis } from "../../lib/types";
import { evidenceSentence, sourceLabel } from "../../lib/language";
import { dateTime, humanize, number } from "../../lib/format";
import { Icon } from "../common/Icon";

type Filter = "all" | "supports" | "against";

/** Observed and baseline cells. The backend stores the baseline of a "_change_pct" signal in the
 *  signal's own unit (e.g. mm/s), not in %, so it is never printed with a % sign. */
function cells(e: Evidence): [string, string] {
  const raw = e.details?.raw_value;
  if (typeof raw === "boolean") {
    return [raw ? "Yes" : "No", e.baseline_value !== null ? `Limit ${number(e.baseline_value, 2)}` : "n/a"];
  }
  const v = e.value;
  if (v === null) return ["n/a", "n/a"];
  switch (e.unit) {
    case "%":
      return [`${v > 0 ? "+" : v < 0 ? "−" : ""}${number(Math.abs(v), 1)}% vs normal`, "No change (0%)"];
    case "x":
      return [`${number(v, 2)}×`, e.baseline_value !== null ? `${number(e.baseline_value, 2)}×` : "n/a"];
    case "probability":
      return [`${number(v * 100, 0)}%`, e.baseline_value !== null ? `${number(e.baseline_value * 100, 0)}%` : "n/a"];
    case "ratio":
    case "score":
      return [number(v, 2), e.baseline_value !== null ? number(e.baseline_value, 2) : "n/a"];
    default: {
      const u = e.unit ? ` ${e.unit}` : "";
      return [`${number(v, Math.abs(v) < 10 ? 2 : 1)}${u}`, e.baseline_value !== null ? `${number(e.baseline_value, Math.abs(e.baseline_value) < 10 ? 2 : 1)}${u}` : "n/a"];
    }
  }
}

/** Every piece of evidence behind one explanation, with its source, value, baseline and direction. */
export function EvidenceTable({ evidence, hypotheses, initialHypothesis, showIds = false }: {
  evidence: Evidence[];
  hypotheses: Hypothesis[];
  initialHypothesis: string | null;
  showIds?: boolean;
}) {
  const [hid, setHid] = useState<string>(hypotheses.find((h) => h.type === initialHypothesis)?.hypothesis_id ?? hypotheses[0]?.hypothesis_id ?? "");
  const [filter, setFilter] = useState<Filter>("all");
  const rows = useMemo(
    () => evidence.filter((e) => e.hypothesis_id === hid).filter((e) => (filter === "all" ? true : filter === "supports" ? e.supports : e.contradicts)).sort((a, b) => Number(b.supports) - Number(a.supports) || b.evidence_support_score - a.evidence_support_score),
    [evidence, hid, filter],
  );
  return (
    <div className="stack">
      <div className="row">
        <label className="small" htmlFor="ev-hyp" style={{ fontWeight: 600 }}>Explanation</label>
        <select id="ev-hyp" className="input" value={hid} onChange={(e) => setHid(e.target.value)} style={{ height: 32 }}>
          {hypotheses.map((h) => <option key={h.hypothesis_id} value={h.hypothesis_id}>{h.label ?? humanize(h.type)}{h.type === initialHypothesis ? " (REACT's conclusion)" : ""}</option>)}
        </select>
        <div className="row" role="group" aria-label="Filter evidence">
          {(["all", "supports", "against"] as Filter[]).map((f) => (
            <button key={f} className={`btn btn-sm ${filter === f ? "btn-primary" : ""}`} aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f === "supports" ? "Supports" : "Against"}
            </button>
          ))}
        </div>
      </div>
      <div className="table-wrap card">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Relationship</th>
              <th scope="col">What the data shows</th>
              <th scope="col">Source</th>
              <th scope="col">Observed</th>
              <th scope="col">Normal / baseline</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.evidence_id}>
                <td>
                  {e.supports ? (
                    <span className="badge badge-good"><Icon name="check" />Supports</span>
                  ) : (
                    <span className="badge badge-high"><Icon name="x" />Against</span>
                  )}
                </td>
                <td>
                  <div>{evidenceSentence(e)}</div>
                  <div className="muted small" style={{ overflowWrap: "anywhere" }}>{e.claim.replace(/^Condition not met:\s*/, "")}</div>
                  {showIds && <div className="mono muted">{e.evidence_id}</div>}
                </td>
                <td>
                  <div>{sourceLabel(e.source_type)}</div>
                  <div className="muted small">{e.source_ids.length} record{e.source_ids.length === 1 ? "" : "s"}</div>
                </td>
                <td className="num">{cells(e)[0]}</td>
                <td className="num">{cells(e)[1]}</td>
                <td className="small">{dateTime(e.timestamp)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={6} className="muted">No evidence matches this filter.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
