# REACT Merchant: presentation narrative (10 slides)

For the PS21 FinTech & Smart Commerce pitch. Every claim carries a tag. Say it only at the strength its tag allows.

| Tag | Meaning |
|---|---|
| **IMPLEMENTED** | Code exists in this repository and automated tests pass |
| **DEMO-VERIFIED** | Shown working end to end in the running app, and covered by the Playwright suite |
| **PLANNED** | Designed or evaluated, not built. Say "planned" or "next" |

---

## Slide 1: The problem

**Headline:** Small merchants lose money they never see.

* A shop like Astra Retail (300 products, 8 suppliers) loses sales to empty shelves, margin to supplier price rises nobody passed on, and cash to stock that stopped selling.
* The signals are already in the shop's own records (sales, purchase orders, deliveries, prices), but nobody has time to read them every day.
* Dashboards show numbers. They don't say what matters, why, how much, or what to do.

*Speaker note:* keep it concrete. Mention a speaker running out while the supplier is late; most of the room has lived that.

## Slide 2: What REACT Merchant does

**Headline:** From records to an approved decision, with the evidence attached.

```
DETECT → INVESTIGATE → QUANTIFY (₹) → RECOMMEND → HUMAN APPROVAL → SIMULATED ACTION → AUDIT
```

* **IMPLEMENTED**: the whole loop, as one product. **DEMO-VERIFIED** on five scenarios.
* It is not a chatbot. There is no prompt box: REACT tells the owner what needs attention.

## Slide 3: Live demo, Stockout crisis

**Headline:** "Wireless Speaker X may run out within a day."

* **DEMO-VERIFIED**: the dashboard turns Critical; the finding is priced (about ₹38,800 of lost sales on the 2 Oct 2026 data); REACT investigates five explanations and concludes "late deliveries left the shelf short", strongly supported; it recommends **Reorder now: 94 units, about ₹1.7 lakh, arriving around 11 Oct**, and shows how the quantity was worked out.
* **DEMO-VERIFIED**: the owner approves, and REACT records a simulated purchase order (`SIM-PO-…`). Nothing is sent and stock records are unchanged.

*Speaker note:* follow [hackathon-demo.md](hackathon-demo.md). Read figures from the screen, not from this page.

## Slide 4: Five scenarios, one engine

| Scenario | Finding | Recommendation | Status |
|---|---|---|---|
| Stockout crisis | Best-seller about to run out while the supplier is late | Reorder now | **DEMO-VERIFIED** |
| Demand opportunity | A category selling much faster in the store's own zone | Stock up | **DEMO-VERIFIED** |
| Supplier delay | A supplier late order after order | Review the supplier | **DEMO-VERIFIED** |
| Margin leakage | Cost up 7%, shelf price unchanged | Review the price (with the price that restores margin) | **DEMO-VERIFIED** |
| Excess inventory | A bulk lot that stopped selling | Run a promotion | **DEMO-VERIFIED** |

* **IMPLEMENTED**: 15 detection rules, 28 hypotheses and a 14-action merchant catalogue.

## Slide 5: Why you can trust the answer

**Headline:** Every claim points to a record. Every rupee comes from a formula.

* **IMPLEMENTED**: evidence items carry the IDs of the stored records (orders, purchase orders, deliveries, stock snapshots, price changes). The UI shows the evidence *against* each explanation too.
* **IMPLEMENTED**: the **Evidence Support Score** (relevance, recency, source reliability, correlation, consistency) is shown as Strongly supported, Supported, Weakly supported or Insufficient support. It is never presented as a probability.
* **IMPLEMENTED**: money comes only from the deterministic Impact Engine. *View calculation* shows the formula and inputs.
* **IMPLEMENTED**: agent outputs that cite IDs the engine did not produce are rejected.

## Slide 6: Human in control

**Headline:** REACT recommends. The owner decides. Actions are simulated.

* **DEMO-VERIFIED**: approve, or reject with a required reason ("No action was taken").
* **IMPLEMENTED**: no real purchases, price changes, supplier contact, customer messages or payments. Tests compare prices, stock levels and purchase orders before and after approval.
* **IMPLEMENTED**: every approval or rejection writes an evidence pack with a SHA-256 hash, re-verified on download.

## Slide 7: AI that fails safely

**Headline:** When the AI is down, REACT says so. It never guesses.

* **DEMO-VERIFIED** (AI-failure drill): with no substitute allowed, REACT answers "AI investigation temporarily unavailable" and produces nothing. With rule-based fallback allowed, it gives the same evidence-grounded answer, labelled.
* **IMPLEMENTED**: agents only call allow-listed read and calculation tools. They have no SQL, shell, filesystem or network access, run under hard limits on calls, time and an hourly budget, and store no chain-of-thought.
* **IMPLEMENTED**: the default provider is deterministic, so every demo result is reproducible.
* **Bedrock:** **IMPLEMENTED** as an optional provider, tested with stubs. It has **not been called live**. Do not say "powered by Bedrock".

## Slide 8: Architecture

```
React UI ─► FastAPI ─► domain pack (merchant) ─► detection ─► investigation ─► Impact Engine ─► agents ─► decision + approval ─► simulated action ─► evidence pack
                                       PostgreSQL (Alembic)                    optional: Bedrock │ S3 │ CloudWatch
```

* **IMPLEMENTED**: a modular monolith with the domain-pack design. The manufacturing pack still runs on the same engines, which shows the core is reusable.
* **IMPLEMENTED (not deployed)**: AWS in CDK: CloudFront, S3, ALB, ECS Fargate, RDS PostgreSQL, Secrets Manager, CloudWatch, optional Bedrock. 12 infrastructure tests. Say "defined and tested in code"; **never say "running on AWS"**.

## Slide 9: Proof

| Evidence | Result (2026-10-03) | Tag |
|---|---|---|
| Backend tests (pytest, merchant and reference packs) | all pass; 2 AWS-live tests skipped (opt-in) | **IMPLEMENTED** |
| Frontend component tests (Vitest, real API fixtures) | 42 pass | **IMPLEMENTED** |
| End-to-end flows (Playwright): 5 scenarios, rejection, AI failure, mobile | 8 pass | **DEMO-VERIFIED** |
| Infrastructure tests (CDK) | 12 pass | **IMPLEMENTED** |
| Synthetic data labelled everywhere; no hard-coded business numbers in the UI | checked in tests and review | **IMPLEMENTED** |

## Slide 10: What's next

* **PLANNED**: connect real records (POS, accounting or marketplace exports) in place of the synthetic generator. The data model already matches those exports.
* **PLANNED**: real actions behind the same approval (purchase orders to suppliers, price updates to the POS, customer offers), each with approval-scoped credentials.
* **PLANNED**: sign-in and roles (owner, store manager) in place of a typed approver name.
* **PLANNED**: an AWS deployment, then multi-tenant scans on a schedule (see `docs/aws-architecture.md` §17).
* **Out of scope on purpose:** payments, UPI, GST and lending. REACT decides what to do with the shop's money. It doesn't move it.

**Close:** "REACT Merchant finds the money a shop is losing or leaving on the table, proves it from the shop's own records, prices it, and lets the owner decide. Nothing happens without them."
