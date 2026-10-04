# Decisions, human approval and simulated actions

A verified investigation becomes **one** evidence-backed recommendation from a fixed catalogue. The merchant approves or rejects it. Only an approved action runs, and it runs as a **simulation**: REACT writes a record of what would be done (`SIM-PO-…`, `SIM-PRICE-…` and so on) and changes no business record. No purchase order, price change, supplier contact, customer message, payment or stock movement leaves REACT.

```
Finding → agent run (detect, investigate, verify, impact) → COMPLETED
  → Decision Agent (DecisionRecommendation, validated by DecisionEngine) + deterministic action parameters
  → Decision DECISION_READY
       ├─ readiness INSUFFICIENT_EVIDENCE → Decision INSUFFICIENT_EVIDENCE (no approval, no action)
       └─ AWAITING_APPROVAL + Approval PENDING + Action PROPOSED
            ├─ POST /decisions/{id}/approve {reviewed_by} → APPROVED → EXECUTING → EXECUTED   (action COMPLETED, simulated)
            └─ POST /decisions/{id}/reject  {reviewed_by, reason} → REJECTED                  (action REJECTED)
  → evidence pack (JSON, SHA-256) written on approve or reject
```

## Responsibilities

| Component | File | Responsibility |
|---|---|---|
| Decision Agent | `app/agents/decision.py` | Reads the finding, agent-run result, evidence, deterministic impact and the applicable catalogue; returns a `DecisionRecommendation` (action, target, rationale citing evidence IDs, alternatives, risks, assumptions). It has no monetary fields |
| Decision engine | `app/decisions/engine.py` | Builds the context (domain-aware), runs the agent with **deterministic validation injected**, merges the merchant action parameters, persists decision, approval and proposed action, and drives approve / reject / execute through the state machines |
| Catalogues | `app/decisions/catalogue.py`, `app/domains/merchant/actions.py` | `catalogue_for(domain)`, `hypothesis_actions_for(domain)`. The merchant catalogue has 14 actions and a default action plus alternatives for each of the 28 merchant hypotheses |
| Parameters | `app/domains/merchant/actions.py` (`targets`, `action_parameters`) | Candidate targets from the finding; deterministic parameters such as reorder quantity, cost, supplier and delivery date, or the suggested price |
| Executor | `app/decisions/actions.py` → `app/domains/merchant/actions.py` (`execute`) | Runs only approved catalogue actions in `EXECUTING` that are linked to an approval; returns the simulated record |
| State machines | `app/decisions/state.py` | Decision, approval and action transition tables; invalid transitions raise `InvalidTransition` (HTTP 409) |

With the default `LLM_PROVIDER=deterministic`, the recommendation is built by the rule-based provider from the same context (the catalogue default for the leading hypothesis). With `bedrock`, the model writes it, and the same validation applies.

## Merchant action catalogue

| Action | UI label | Targets | Simulated record |
|---|---|---|---|
| `REORDER_PRODUCT` | Reorder now | product, inventory | `SIM-PO` purchase order: supplier, quantity, unit cost, estimated cost, expected delivery date |
| `INCREASE_STOCK` | Stock up | product, inventory, category | `SIM-PO` purchase plan with lines per product |
| `REVIEW_SUPPLIER` | Review the supplier | supplier | `SIM-SUPP` review: late of last 6, quoted vs recent lead time, open orders, agenda |
| `REDUCE_PURCHASE` | Pause purchases | inventory, product, category | `SIM-PLAN` purchase-plan change: proposed reorder point, pause days |
| `PROMOTE_PRODUCT` | Run a promotion | product, inventory, category | `SIM-PROMO` plan: discount, duration, offer price, channels |
| `REVIEW_PRICING` | Review the price | product | `SIM-PRICE` review: current price and cost, suggested price, MRP |
| `REVIEW_DISCOUNT` | Review discounts | product, category | Discount review task |
| `REDISTRIBUTE_INVENTORY` | Move or list the stock | inventory, product | `SIM-MOVE` transfer or listing plan |
| `CONTACT_CUSTOMER` | Contact customers | customer segment, region, business | `SIM-CRM` outreach list (no customer contacted) |
| `REVIEW_DEMAND` | Review demand | product, category, business | Demand review task |
| `REVIEW_CATEGORY` | Review the category | category | Category review task |
| `MONITOR_PRODUCT` | Keep watching | product, inventory, supplier | Watchlist entry |
| `INVESTIGATE_MARKET` | Look into the market | category, region, product, business | Market investigation task |
| `PRIORITIZE_PRODUCT` | Give it priority | product, category | Prioritisation task |

Examples of the default action per hypothesis: late deliveries left the shelf short → `REORDER_PRODUCT`; purchase cost increase not passed on → `REVIEW_PRICING`; regional or category demand growth → `INCREASE_STOCK`; demand faded after a large purchase → `PROMOTE_PRODUCT`; supplier reliability deteriorated → `REVIEW_SUPPLIER`. `GET /actions/catalogue` lists the whole catalogue for the active domain.

### Deterministic parameters

Parameters are computed from the records at the business date, never by the LLM. For a reorder:

```
quantity = ceil(units_per_day × (lead_days + cover_days + 3 safety days) − on_hand − on_order)
estimated_cost = quantity × unit_cost                    (Impact Engine: purchase_order_value)
expected_delivery_date = business date + observed lead time
```

The inputs are rounded to the values shown in the UI before computing, so the merchant can check the arithmetic on screen. The suggested price for a margin review comes from the Impact Engine's `price_to_restore_margin` (rounded up).

## Validation (deterministic; the LLM cannot bypass it)

A recommendation is accepted only if:

1. `subject_kind` / `subject_id` match the finding, and `hypothesis_id` / `hypothesis_type` are the investigation's leading hypothesis.
2. `evidence_support_score` restates the deterministic Evidence Support Score exactly.
3. Every evidence id exists in the investigation; the rationale cites at least one of them.
4. If the agent-run result is not `CONCLUDED` or the Evidence Support Score is below 0.5, readiness **must** be `INSUFFICIENT_EVIDENCE` and `action_type` must be null.
5. Otherwise the action is an applicable catalogue action for the subject kind, the target is one of the candidate targets derived from the finding, and the action can target that type.
6. Alternatives are distinct catalogue actions; `requires_human_approval` is true.

A rejected output is traced as `validation` and retried with the error fed back; after the retries, the deterministic provider is used (flagged `fallback`) or, with fallback disabled, creation fails with a structured error and no decision is written.

The decision record stores the list of checks that passed. `DecisionRecommendation` contains no monetary field; `expected_impact` is copied from the investigation's impact result, which the deterministic impact engine produced.

## Terminology

* **Evidence Support Score**: the deterministic score from the investigation engine, restated on the decision.
* **decision_readiness** (`READY` | `NEEDS_REVIEW` | `INSUFFICIENT_EVIDENCE`): a separate rule-based readiness state. `READY` requires Evidence Support Score ≥ 0.6 and no missing evidence. It is not a probability.
* **Expected impact**: synthetic, formula-based estimates, never guaranteed outcomes.

## States

| Entity | States |
|---|---|
| Decision | `DECISION_READY → AWAITING_APPROVAL → APPROVED → EXECUTING → EXECUTED`; `AWAITING_APPROVAL → REJECTED`; `DECISION_READY → INSUFFICIENT_EVIDENCE`; `→ FAILED` from any non-terminal state |
| Approval | `PENDING → APPROVED | REJECTED | EXPIRED` (`EXPIRED` is defined but no expiry job runs yet) |
| Action | `PROPOSED → APPROVED → EXECUTING → COMPLETED | FAILED`; `PROPOSED → REJECTED` |

`PROPOSED → COMPLETED`, `PROPOSED → EXECUTING` and execution before approval are rejected.

The finding reflects the lifecycle: incidents move to `AWAITING_APPROVAL`, then `EXECUTING` after the simulated action (or `AWAITING_DECISION` when evidence is insufficient or the decision is rejected); risks move to `MITIGATING` after execution; opportunities move to `REVIEWED` after execution or rejection.

## Simulated execution

The Stockout crisis reorder after approval (from `frontend/src/test/fixtures/decision_executed.json`):

```json
{"task_id": "SIM-PO-3F4A4ED7", "task_type": "purchase_order", "status": "OPEN",
 "supplier": "SoundWave Wholesale", "store": "Astra Retail, Jayanagar",
 "items": [{"product": "Wireless Speaker X", "quantity": 94, "unit_cost": 1799.0}],
 "estimated_cost": 169106.0, "expected_delivery_date": "2026-10-11", "business_date": "2026-10-02",
 "notes": "Simulated purchase order created. Nothing was sent to the supplier and stock levels are unchanged.",
 "simulated": true, "side_effects": "none", "business_records_modified": false,
 "action_type": "REORDER_PRODUCT", "hypothesis_type": "SUPPLIER_DELAY_SHORTAGE", "decision_id": "…", "approval_id": "…", "executed_by": "Store owner"}
```

Tests check that approving changes no prices, stock levels or purchase orders: `tests/merchant/test_merchant_workflow.py`, plus the Playwright margin and excess-inventory flows, which compare `/products` and `/inventory` before and after approval.

## Traceability

`GET /decisions/{id}/evidence` returns the chain decision → agent run → investigation → hypothesis → evidence items (claims, values, source record ids). `GET /decisions/{id}/trace` returns the decision timeline, approvals, actions and the full agent trace, including the Phase 4 events `decision`, `approval_requested`, `approval` / `rejection`, `action_executed` and `error`.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/react/run` `{incident_id | opportunity_id | risk_id}` | full workflow: agents → decision → awaiting approval |
| POST | `/decisions` `{agent_run_id}` (or a subject id) | decision from a completed agent run |
| GET | `/decisions`, `/decisions/{id}`, `/decisions/{id}/evidence`, `/decisions/{id}/trace` | inspect |
| POST | `/decisions/{id}/approve` `{reviewed_by, reason?, execute?: true}` | human approval (optionally executes) |
| POST | `/decisions/{id}/reject` `{reviewed_by, reason}` | human rejection |
| POST | `/decisions/{id}/execute` `{executed_by?}` | execute an approved decision's simulated action |
| GET | `/approvals`, `/approvals/{id}`, `/actions`, `/actions/{id}`, `/actions/catalogue` | approvals, actions, catalogue |
| GET | `/incidents/{id}/decision`, `/opportunities/{id}/decision`, `/risks/{id}/decision` | latest decision for a finding |

Errors: 404 unknown ids, 409 invalid transitions or runs that are not COMPLETED, 422 missing reviewer / reason.

## Not implemented

Authentication and RBAC (the reviewer is a typed name), approval expiry, and real integrations (supplier ordering, POS pricing, messaging, payments).
