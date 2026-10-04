import { Fragment } from "react";
import { money, number, pct } from "../../lib/format";
import type { Decision } from "../../lib/types";

/** The facts REACT computed for the proposed action (quantity, cost, suggested price...). Deterministic, from
 *  the records at decision time; the language model never sets them. */
export function ActionParameters({ decision: d }: { decision: Decision }) {
  const p = (d.actions[0]?.parameters ?? {}) as Record<string, unknown>;
  const rows: [string, string][] = [];
  const n = (k: string) => (typeof p[k] === "number" ? (p[k] as number) : null);
  if (n("quantity") !== null && (d.action_type === "REORDER_PRODUCT" || d.action_type === "INCREASE_STOCK")) {
    rows.push(["Order", `${number(n("quantity"))} units${p.supplier ? ` from ${p.supplier}` : ""}`]);
    if (n("estimated_cost") !== null) rows.push(["Estimated cost", money(n("estimated_cost"))]);
    if (p.expected_delivery_date) rows.push(["Expected delivery", `${String(p.expected_delivery_date)} (about ${number(n("expected_in_days"))} days)`]);
    const basis = p.basis as Record<string, number | string> | undefined;
    if (basis) rows.push(["How the quantity was set", `${number(Number(basis.units_per_day), 2)} a day × (${number(Number(basis.lead_days), 1)} days lead time + ${basis.cover_days} days cover + 3 days safety) − ${basis.on_hand} on hand − ${basis.on_order} on order`]);
  }
  const lines = p.lines as { product: string; quantity: number; estimated_cost: number; supplier: string }[] | undefined;
  if (lines?.length) lines.forEach((l) => rows.push([l.product, `${number(l.quantity)} units from ${l.supplier}, ${money(l.estimated_cost)}`]));
  if (n("suggested_price") !== null) rows.push(["Suggested shelf price", `${money(n("suggested_price"))} (now ${money(n("current_price"))}, cost ${money(n("current_cost"))}${n("mrp") ? `, MRP ${money(n("mrp"))}` : ""})`]);
  if (n("discount_pct") !== null) rows.push(["Offer", `${pct((n("discount_pct") ?? 0) * 100)} off for ${number(n("duration_days"))} days${n("offer_price") ? `: ${money(n("offer_price"))}` : ""}`]);
  if (n("pause_reorders_days") !== null) rows.push(["Purchase plan", `Pause reorders for about ${number(n("pause_reorders_days"))} days; reorder level ${n("current_reorder_point")} → ${n("proposed_reorder_point")}`]);
  if (n("late_of_last_6") !== null) rows.push(["Delivery record", `${n("late_of_last_6")} of the last 6 deliveries late; recent lead time ${number(n("recent_lead_days"), 1)} days against ${n("quoted_lead_days")} quoted`]);
  if (n("units_to_move") !== null) rows.push(["Move", `${number(n("units_to_move"))} units to ${(p.to as string[] | undefined)?.join(", ")}`]);
  if (n("customers_in_list") !== null) rows.push(["Customers in the list", number(n("customers_in_list"))]);
  if (!rows.length) return null;
  return (
    <div>
      <div className="section-title">What REACT worked out</div>
      <dl className="kv">
        {rows.map(([k, v]) => (<Fragment key={k}><dt>{k}</dt><dd className={k === "How the quantity was set" ? "small" : undefined}>{v}</dd></Fragment>))}
      </dl>
    </div>
  );
}
