import type { ActionExecution } from "../../lib/types";
import { actionLabel } from "../../lib/language";
import { dateTime, humanize, money, number, pct } from "../../lib/format";
import { SimulatedBadge } from "../common/Badges";
import { Icon } from "../common/Icon";

/** Result of an approved action. Always simulated: nothing changed in the real world. */
export function ActionResult({ action }: { action: ActionExecution }) {
  const r = action.result as Record<string, unknown>;
  const n = (k: string) => (typeof r[k] === "number" ? (r[k] as number) : null);
  const rows: [string, string][] = [];
  if (r.task_id) rows.push(["Reference", String(r.task_id)]);
  if (r.task_type) rows.push(["Type", humanize(String(r.task_type))]);
  if (r.supplier && typeof r.supplier === "string") rows.push(["Supplier", r.supplier]);
  const items = r.items as { product?: string; quantity?: number; supplier?: string; estimated_cost?: number }[] | undefined;
  items?.forEach((i) => rows.push([i.product ?? "Item", `${number(i.quantity ?? 0)} units${i.supplier ? ` from ${i.supplier}` : ""}${i.estimated_cost ? `, ${money(i.estimated_cost)}` : ""}`]));
  if (n("estimated_cost") !== null) rows.push(["Estimated cost", money(n("estimated_cost"))]);
  if (r.expected_delivery_date) rows.push(["Expected delivery", String(r.expected_delivery_date)]);
  if (n("suggested_price") !== null) rows.push(["Suggested price", `${money(n("suggested_price"))} (now ${money(n("current_price"))})`]);
  if (n("discount_pct") !== null) rows.push(["Offer", `${pct((n("discount_pct") ?? 0) * 100)} off for ${number(n("duration_days"))} days from ${String(r.starts ?? "")}`]);
  if (n("pause_reorders_days") !== null) rows.push(["Purchase plan", `Pause reorders for ${number(n("pause_reorders_days"))} days`]);
  if (n("units_to_move") !== null) rows.push(["Move", `${number(n("units_to_move"))} units`]);
  if (Array.isArray(r.agenda)) rows.push(["Agenda", (r.agenda as string[]).join("; ")]);
  if (n("customers_in_list") !== null) rows.push(["Customers in the list", number(n("customers_in_list"))]);
  rows.push(["Done by", `${action.executed_by ?? "system"}, ${dateTime(action.completed_at)}`]);
  return (
    <div className="result" aria-live="polite">
      <div className="spread" style={{ alignItems: "flex-start" }}>
        <div>
          <div className="section-title" style={{ color: "var(--good)", marginBottom: 2 }}>Action completed</div>
          <h3 style={{ fontSize: 17 }}>{actionLabel(action.action_type)}{action.target_name ? `: ${action.target_name}` : ""}</h3>
        </div>
        <div className="row"><span className="badge badge-good"><Icon name="check" />{action.status}</span>{action.simulated && <SimulatedBadge />}</div>
      </div>
      <dl className="kv" style={{ marginTop: 12 }}>
        {rows.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
      {typeof r.notes === "string" && <p className="small" style={{ marginTop: 10 }}>{r.notes}</p>}
      {action.simulated && (
        <div className="sim-note">
          <Icon name="flask" />
          <span>This was a <strong>simulated action</strong>. REACT recorded what would be done; no purchase order, price change, message or payment was sent to any real system, and your stock and price records are unchanged.</span>
        </div>
      )}
    </div>
  );
}
