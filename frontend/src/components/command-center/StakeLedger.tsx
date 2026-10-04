import { Link } from "react-router-dom";
import type { Finding, Overview } from "../../lib/types";
import { typeLabel } from "../../lib/language";
import { money } from "../../lib/format";
import { caseLink } from "../findings/FindingCard";

/** "What is at stake" kept like a shop's khata: risks are debits, opportunities credits, totals double-ruled.
 *  Every amount is the Impact Engine estimate stored on the finding. */
export function StakeLedger({ overview: o }: { overview: Overview }) {
  const risks = [...o.attention, ...o.addressed].filter((f, i, a) => f.kind !== "opportunity" && f.impact && a.findIndex((x) => x.id === f.id) === i);
  const opps = o.opportunities.filter((f) => f.impact);
  const currency = o.impact.currency;
  const row = (f: Finding, side: "dr" | "cr") => (
    <tr key={f.id}>
      <td>
        <Link to={caseLink(f)}>{f.title}</Link>
        <div className="muted small">{typeLabel(f.type)}{f.stage === "ACTION_COMPLETED" ? " · action taken (simulated)" : ""}</div>
      </td>
      <td className={`amt ${side === "dr" ? "dr" : ""}`}>{side === "dr" ? money(f.impact!.value, currency) : ""}</td>
      <td className={`amt ${side === "cr" ? "cr" : ""}`}>{side === "cr" ? money(f.impact!.value, currency) : ""}</td>
    </tr>
  );
  return (
    <section className="ledger" aria-labelledby="stake-title">
      <div className="ledger-head">
        <h2 className="ledger-title" id="stake-title">What is at stake</h2>
        <span className="muted small">Estimates, synthetic data</span>
      </div>
      {risks.length + opps.length === 0 ? (
        <p className="muted small" style={{ padding: "8px 0" }}>Nothing priced yet. Run a scan or load a demo scenario.</p>
      ) : (
        <table className="ledger-rows">
          <thead>
            <tr><th scope="col">Finding</th><th scope="col" className="amt">At risk</th><th scope="col" className="amt">Could gain</th></tr>
          </thead>
          <tbody>
            {risks.map((f) => row(f, "dr"))}
            {opps.map((f) => row(f, "cr"))}
            <tr className="total">
              <td>Total of open items</td>
              <td className="amt dr">{o.impact.exposure_total ? money(o.impact.exposure_total, currency) : "₹0"}</td>
              <td className="amt cr">{o.impact.opportunity_total ? money(o.impact.opportunity_total, currency) : "₹0"}</td>
            </tr>
          </tbody>
        </table>
      )}
      <p className="ledger-note">Each amount comes from REACT's deterministic impact engine; open any line to see the formula. Estimates can overlap, so totals show scale, not an exact sum.</p>
    </section>
  );
}
