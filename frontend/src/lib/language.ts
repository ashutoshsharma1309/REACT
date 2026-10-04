// Presentation language for the merchant UI. Business wording (finding titles, evidence claims, cause labels,
// the "Why?" signals) comes from the backend; this file only maps codes to short labels and never computes
// a business value.
import type { Evidence, Finding, FindingKind, Investigation } from "./types";
import { humanize } from "./format";

const KIND_LABEL: Record<FindingKind, string> = { incident: "Operations", risk: "Risk", opportunity: "Opportunity" };
export const kindLabel = (k: FindingKind) => KIND_LABEL[k];

const TYPE_LABEL: Record<string, string> = {
  STOCKOUT_RISK: "Stockout risk",
  DEMAND_SUPPLY_MISMATCH: "Stock not keeping up",
  DEMAND_DECLINE: "Falling demand",
  SUPPLIER_DELAY: "Supplier delay",
  SUPPLIER_RELIABILITY: "Unreliable supplier",
  MARGIN_DECLINE: "Margin leakage",
  PRICE_ANOMALY: "Pricing problem",
  DISCOUNT_LEAKAGE: "Discount leakage",
  EXCESS_INVENTORY: "Excess stock",
  SLOW_MOVING_INVENTORY: "Slow-moving stock",
  REVENUE_DECLINE: "Sales below normal",
  REPEAT_CUSTOMER_DECLINE: "Fewer repeat customers",
  DEMAND_OPPORTUNITY: "Demand spike",
  CATEGORY_OPPORTUNITY: "Growing category",
  REGIONAL_OPPORTUNITY: "Local demand",
};
export const typeLabel = (t: string) => TYPE_LABEL[t] ?? humanize(t);

/** The backend writes merchant-ready titles ("Wireless Speaker X may run out in 2.0 days"). */
export const findingHeadline = (f: Finding): string => f.title;

/** Where it is: product / supplier / category and store or zone, for the card subtitle. */
export function findingWhere(f: Finding): string {
  const parts = [typeLabel(f.type), f.entity.name !== f.title ? f.entity.name : null, f.location].filter((x, i, a): x is string => !!x && a.indexOf(x) === i);
  return parts.join(" · ");
}

/** Plain-language cause name, from the hypothesis registry label the backend returns. */
export function causeLabel(inv: Investigation | null | undefined, type: string | null | undefined): string {
  if (!type) return "No clear cause";
  const h = inv?.hypotheses.find((x) => x.type === type);
  if (h?.label) return h.label;
  if (inv?.conclusion.selected_hypothesis === type && inv.conclusion.selected_label) return inv.conclusion.selected_label;
  const alt = inv?.conclusion.alternatives?.find((a) => a.hypothesis === type);
  return alt?.label ?? humanize(type);
}

export function causeDescription(inv: Investigation | null | undefined, type: string | null | undefined): string {
  return inv?.hypotheses.find((x) => x.type === type)?.description ?? "";
}

// ---- actions ------------------------------------------------------------------------------------------
const ACTIONS: Record<string, string> = {
  REORDER_PRODUCT: "Reorder now",
  INCREASE_STOCK: "Stock up",
  REVIEW_SUPPLIER: "Review the supplier",
  REDUCE_PURCHASE: "Pause purchases",
  PROMOTE_PRODUCT: "Run a promotion",
  REVIEW_PRICING: "Review the price",
  REVIEW_DISCOUNT: "Review discounts",
  REDISTRIBUTE_INVENTORY: "Move or list the stock",
  CONTACT_CUSTOMER: "Contact customers",
  REVIEW_DEMAND: "Review demand",
  REVIEW_CATEGORY: "Review the category",
  MONITOR_PRODUCT: "Keep watching",
  INVESTIGATE_MARKET: "Look into the market",
  PRIORITIZE_PRODUCT: "Give it priority",
};
export const actionLabel = (t: string | null | undefined) => (t ? ACTIONS[t] ?? humanize(t) : "No action");

// ---- evidence sources -------------------------------------------------------------------------------
const SOURCES: Record<string, string> = {
  order_item: "Sales records",
  order: "Orders",
  customer: "Customer records",
  inventory: "Stock records",
  inventory_snapshot: "Daily stock history",
  purchase_order: "Purchase orders",
  supplier_delivery: "Supplier deliveries",
  supplier: "Supplier records",
  pricing_history: "Price list",
  discount: "Promotions",
  product: "Product catalogue",
  financial_metric: "Daily accounts",
  expense: "Expenses",
  competitor_signal: "Online price checks (synthetic)",
  market_signal: "Market data (synthetic)",
};
export const sourceLabel = (s: string) => SOURCES[s] ?? humanize(s);

/** One evidence item as a sentence. Unmet conditions are phrased as "not seen". */
export function evidenceSentence(e: Evidence): string {
  const notMet = /^Condition not met:\s*/;
  if (notMet.test(e.claim)) {
    const cond = String(e.details?.condition ?? "").trim();
    const rest = e.claim.replace(notMet, "");
    return cond ? `Not seen: ${cond} (${rest})` : rest;
  }
  return e.claim;
}

/** Evidence Support Score in words; the number lives in Technical details. Never a probability. */
export function supportWords(score: number | null | undefined): string {
  if (score === null || score === undefined) return "Not assessed";
  if (score >= 0.8) return "Strongly supported";
  if (score >= 0.6) return "Supported";
  if (score >= 0.4) return "Weakly supported";
  return "Insufficient support";
}

export const STAGE_LABEL: Record<string, string> = {
  DETECTED: "Needs investigation",
  INVESTIGATED: "Investigated",
  AWAITING_APPROVAL: "Waiting for your approval",
  APPROVED: "Approved",
  ACTION_COMPLETED: "Done (simulated)",
  REJECTED: "You rejected it",
  INSUFFICIENT_EVIDENCE: "Not enough evidence yet",
  FAILED: "Investigation failed",
};

const SEGMENTS: Record<string, string> = { STUDENT: "Students", YOUNG_PROFESSIONAL: "Young professionals", FAMILY: "Families", SMALL_BUSINESS: "Small businesses" };
export const segmentLabel = (s: string | null | undefined) => (s ? SEGMENTS[s] ?? humanize(s) : "");
