import type { KeySignal } from "../../lib/types";
import { sourceLabel } from "../../lib/language";

/** The measurements behind the conclusion, chosen and phrased by the backend (never computed here). */
export function WhySignals({ signals }: { signals: KeySignal[] }) {
  if (!signals.length) return null;
  return (
    <div className="why-signals" role="list" aria-label="Key measurements behind this conclusion">
      {signals.map((k) => (
        <div className="why-signal" role="listitem" key={k.signal} title={k.claim}>
          <div className="why-label">{k.label}</div>
          <div className="why-value" data-dir={k.direction}>{k.display}</div>
          <div className="why-meta">{k.baseline_display ? `Normal: ${k.baseline_display} · ` : ""}{sourceLabel(k.source_type)}{k.record_count ? `, ${k.record_count} record${k.record_count === 1 ? "" : "s"}` : ""}</div>
        </div>
      ))}
    </div>
  );
}
