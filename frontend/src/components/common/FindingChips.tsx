import { Link } from "react-router-dom";
import type { FindingFlag } from "../../lib/types";
import { typeLabel } from "../../lib/language";

/** Open findings attached to a product, stock item or supplier, each linking to its case. */
export function FindingChips({ findings }: { findings: FindingFlag[] }) {
  if (!findings.length) return <span className="muted small">None</span>;
  return (
    <span className="chips">
      {findings.map((f) => (
        <Link key={f.id} to={`/cases/${f.kind}/${f.id}`} className={`badge ${f.kind === "opportunity" ? "badge-good" : f.severity === "CRITICAL" ? "badge-critical" : f.severity === "HIGH" ? "badge-high" : "badge-medium"}`} title={f.title}>
          {typeLabel(f.type)}
        </Link>
      ))}
    </span>
  );
}
