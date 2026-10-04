import { describe, expect, it } from "vitest";
import { money, parseUtc, pct } from "./format";
import { actionLabel, evidenceSentence, findingHeadline, findingWhere, sourceLabel, supportWords, typeLabel } from "./language";
import type { Evidence, Finding } from "./types";

describe("format", () => {
  it("formats rupees in lakh and crore without changing the value's magnitude", () => {
    expect(money(3903957.8)).toBe("₹39.0 lakh");
    expect(money(12742575)).toBe("₹1.27 crore");
    expect(money(93744)).toBe("₹93,744");
    expect(money(null)).toBe("Not estimated");
  });
  it("treats backend timestamps as UTC", () => {
    expect(parseUtc("2026-09-25T07:00:00").toISOString()).toBe("2026-09-25T07:00:00.000Z");
  });
  it("formats signed percentages", () => {
    expect(pct(-22.6, 1, true)).toBe("−22.6%");
  });
});

const baseEvidence: Evidence = {
  evidence_id: "e1", hypothesis_id: "h1", signal: "supplier_late_share_recent", source_type: "supplier_delivery", source_id: "1", source_ids: ["1"],
  timestamp: null, claim: "SoundWave Wholesale: 50% of deliveries in the last 45 days were late (normal 13%)", value: 0.5, unit: "share", baseline_value: 0.13,
  relevance: 1, supports: true, contradicts: false, evidence_support_score: 0.9, score_breakdown: {}, details: {},
};

describe("plain language", () => {
  it("uses the stored evidence claim, which the backend writes from the records", () => {
    expect(evidenceSentence(baseEvidence)).toBe(baseEvidence.claim);
  });
  it("phrases an unmet condition as 'not seen' with the measured value", () => {
    const e = { ...baseEvidence, claim: "Condition not met: Discount rate 4% (normal 4%)", details: { condition: "discounts have grown" } };
    expect(evidenceSentence(e)).toBe("Not seen: discounts have grown (Discount rate 4% (normal 4%))");
  });
  it("falls back to the backend claim when no condition is recorded", () => {
    expect(evidenceSentence({ ...baseEvidence, claim: "Condition not met: raw claim" })).toBe("raw claim");
  });
  it("names findings with the backend title and labels merchant finding types", () => {
    const f = { type: "STOCKOUT_RISK", kind: "risk", entity: { name: "Wireless Speaker X" }, location: "Astra Retail, Jayanagar", title: "Wireless Speaker X may run out within a day" } as unknown as Finding;
    expect(findingHeadline(f)).toBe(f.title);
    expect(findingWhere(f)).toBe("Stockout risk · Wireless Speaker X · Astra Retail, Jayanagar");
    expect(typeLabel("MARGIN_DECLINE")).toBe("Margin leakage");
  });
  it("names actions and evidence sources in shop language", () => {
    expect(actionLabel("REORDER_PRODUCT")).toBe("Reorder now");
    expect(sourceLabel("purchase_order")).toBe("Purchase orders");
  });
  it("never calls the Evidence Support Score a probability", () => {
    expect(supportWords(0.95)).toBe("Strongly supported");
    expect(supportWords(0.65)).toBe("Supported");
    expect(supportWords(0.45)).toBe("Weakly supported");
    expect(supportWords(0.1)).toBe("Insufficient support");
  });
});
