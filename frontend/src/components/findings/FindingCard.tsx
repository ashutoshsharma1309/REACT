import { Link } from "react-router-dom";
import type { Finding } from "../../lib/types";
import { findingHeadline, findingWhere, typeLabel } from "../../lib/language";
import { date, money } from "../../lib/format";
import { ConfidenceBadge, SeverityBadge, StageBadge } from "../common/Badges";
import { Icon } from "../common/Icon";

export function toneOf(f: Finding): string {
  if (f.stage === "ACTION_COMPLETED") return "good";
  if (f.kind === "opportunity") return "info";
  return (f.severity ?? "LOW").toLowerCase();
}

export const caseLink = (f: Pick<Finding, "kind" | "id">) => `/cases/${f.kind}/${f.id}`;

const IMPACT_LABEL = { incident: "Estimated impact", risk: "At risk", opportunity: "Could gain" } as const;

/** One finding as a clickable card: severity, plain headline, where, impact, workflow stage. */
export function FindingCard({ finding: f, compact = false }: { finding: Finding; compact?: boolean }) {
  const where = findingWhere(f).replace(`${typeLabel(f.type)} · `, "").replace(typeLabel(f.type), "");
  return (
    <Link to={caseLink(f)} className="finding" data-tone={toneOf(f)} aria-label={`${findingHeadline(f)}. ${f.severity ?? f.confidence ?? ""}. ${where}`}>
      <span className="finding-rail" aria-hidden="true" />
      <div style={{ minWidth: 0 }}>
        <div className="row">
          {f.severity ? <SeverityBadge severity={f.severity} /> : f.confidence ? <ConfidenceBadge confidence={f.confidence} /> : null}
          <span className="tag">{typeLabel(f.type)}</span>
        </div>
        <div className="finding-title">{findingHeadline(f)}</div>
        <div className="finding-sub">{where || f.title}</div>
        {!compact && <div className="muted small" style={{ marginTop: 6 }}>{f.title}</div>}
      </div>
      <div className="finding-side">
        {f.impact ? (
          <div>
            <div className="impact-value" style={{ color: f.kind === "opportunity" ? "var(--gain)" : "var(--critical)" }}>{money(f.impact.value, f.impact.currency)}</div>
            <div className="impact-label">{IMPACT_LABEL[f.kind]} (estimate)</div>
          </div>
        ) : (
          <div className="impact-label">No impact estimate</div>
        )}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <StageBadge stage={f.stage} />
        </div>
        {!compact && (
          <div className="muted small row" style={{ justifyContent: "flex-end" }}>
            <span style={{ width: 12, display: "inline-flex" }}><Icon name="doc" /></span>
            {f.evidence_count} supporting record{f.evidence_count === 1 ? "" : "s"} · detected {date(f.detected_at)}
          </div>
        )}
      </div>
    </Link>
  );
}
