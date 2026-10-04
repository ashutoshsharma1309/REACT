# REACT Merchant: project state handoff (2026-10-03)

## 1. What this is

**REACT Merchant** is decision intelligence for small merchants, built for the PS21 FinTech & Smart Commerce hackathon. It runs one loop on a shop's own structured records:

```
DETECT → INVESTIGATE → QUANTIFY (₹) → RECOMMEND → HUMAN APPROVAL → SIMULATED ACTION → AUDIT / EVIDENCE
```

The demo merchant is **Astra Retail**, a fictional consumer-electronics and mobile-accessories shop in Jayanagar, Bengaluru: 300 SKUs, 10 categories, 8 suppliers, 1–3 stores and 120 days of history. All data is synthetic and labelled synthetic. REACT is not a chatbot.

Until 2026-09-29 the same codebase was **REACT for Astra Manufacturing** (Phases 1–6: synthetic factory, deterministic detection and investigation, agents, decisions, business UI, AWS in CDK). The merchant transformation (plan: `docs/merchant-migration-plan.md`) kept every engine and added a domain-pack layer. The manufacturing pack still exists as a backend reference with its tests; the UI shows only merchant.

## 2. Status

| Area | Status |
|---|---|
| Merchant data model, additive Alembic migration `c7d3e9a1f204` | Done |
| Deterministic merchant generator, onboarding profile, 5 scenarios + baseline | Done |
| 15 detection rules (risks and opportunities), Poisson tests on order lines, in-stock-adjusted velocity | Done |
| Investigation: merchant signals, 28 hypotheses, evidence and Evidence Support Score, key signals | Done |
| Impact: stockout, supplier delay, margin and discount (90 d), inventory capital, opportunity (30 d), reorder and price suggestions | Done |
| Agents: domain-aware tools (16 read + 5 calculation), allow-lists, prompts, fallback planner | Done |
| Decisions: 14-action merchant catalogue, deterministic parameters, simulated executor (`SIM-*`) | Done |
| Frontend: "Khata ledger" redesign; Dashboard, Findings, Opportunities, Products, Inventory, Suppliers, Decisions, Activity, Data & System, Setup | Done |
| Tests: backend, Vitest, Playwright (8 flows), CDK | Done (see §6) |
| Docs: README, architecture, docs/*, hackathon demo and presentation | Done |
| AWS | Defined in CDK with `REACT_DOMAIN=merchant`; **not deployed** |
| Bedrock | Adapter implemented and stub-tested; **never invoked live** |

## 3. How to run

```bash
createdb react
export DATABASE_URL=postgresql+psycopg://localhost:5432/react
.venv/bin/alembic upgrade head && .venv/bin/python -m scripts.seed      # merchant by default; --domain manufacturing for the reference pack
.venv/bin/uvicorn app.main:app --port 8000
cd frontend && npm run dev                                               # http://localhost:5180
```

* Local environment: Homebrew PostgreSQL on :5432 (no password), venv at `.venv` (Python 3.13). **Docker is not installed. The project is not a git repository.**
* Another app runs on port 5173 on this machine; don't kill it.
* Databases: `react` (dev; migrated to head), `react_merchant_dev` (the dev server used during this work), `react_test` and `react_test_merchant` (pytest), `react_e2e` (Playwright; reseeded every run), `react_migr` (migration round-trip checks).

## 4. Where things are

| Path | Contents |
|---|---|
| `app/domains/__init__.py` | `MERCHANT`, `MANUFACTURING`, `current_domain(db)` (reads `system_state.domain`), `is_merchant` |
| `app/domains/merchant/` | catalog, profile, generator, scenarios, simulation, queries, baseline, detection, signals, registry, impact, language, tools, planner, actions, read_models |
| `app/models/merchant.py` | Store, ProductCategory, PurchaseOrder(+Item), SupplierDelivery, PricingHistory, Discount, Expense |
| `app/api/merchant.py` | `/merchant`, `/merchant/templates`, `/merchant/setup`, `/business-baseline`, `/sales`, `/metrics/value` |
| `app/api/entities.py` | `/products`, `/inventory`, `/suppliers` return merchant read models on a merchant dataset |
| `app/services/demo_service.py`, `product_service.py` | Merchant demo scenarios; overview with business health and value metrics |
| `frontend/src/pages/MerchantPages.tsx`, `components/command-center/*`, `components/case/*` | Merchant UI |
| `tests/merchant/` | Merchant data, detection, workflow, API and tool tests |
| `scripts/capture_ui_fixtures.py`, `scripts/e2e_backend.sh`, `scripts/demo.sh` | Fixture capture, isolated E2E backend, shell demo |

Dispatch points that switch on the domain are listed in `architecture.md` §3.

## 5. Non-negotiable rules (from the brief)

1. REACT is not a chatbot.
2. Every insight is traceable to stored records (evidence carries record IDs, and a test checks business-level findings too).
3. LLM output never invents evidence. Agents cite only engine-generated evidence and hypothesis IDs, and LLM-generated IDs are never trusted.
4. **Money is never calculated by the LLM**, only by the deterministic Impact Engine.
5. The term is "Evidence Support Score", never probability, confidence, certainty or likelihood. Labels: Strongly supported ≥ 0.8, Supported ≥ 0.6, Weakly supported ≥ 0.4, otherwise Insufficient support.
6. Synthetic data stays labelled (`is_synthetic=true`, `source="SYNTHETIC"`, `basis="SYNTHETIC"`).
7. Business logic lives in services and engines, never in routers.
8. Human approval is mandatory, and actions are simulated. No real purchases, supplier changes, messages, payments or inventory updates.
9. Never claim AWS deployment or real Bedrock execution.
10. No hard-coded business numbers in the UI; fixtures are for tests only.
11. Agents get no SQL, shell, filesystem or open network access. Tool inputs and agent outputs are validated. No chain-of-thought is stored.
12. Alembic for every schema change. Never delete old migrations.
13. Out of scope: payments, UPI, GST, POS, CRM, auth, WhatsApp automation, microservices, Redis, Kafka, WebSockets, SQS, Step Functions, agent frameworks.
14. Don't reset branches, force push, delete user work, or store or log secrets.

## 6. Tests (last full run 2026-10-03)

| Suite | Command | Result |
|---|---|---|
| Backend | `.venv/bin/pytest` | 195 passed, 2 skipped (AWS live, opt-in) |
| Merchant subset | `.venv/bin/pytest tests/merchant` | 42 passed |
| Vitest | `cd frontend && npm test` | 42 passed |
| Playwright | `cd frontend && npm run e2e` | 8 passed (5 scenarios, rejection, AI failure, mobile) |
| CDK | `cd infra && npm test` | 12 passed |

`npx tsc -b --noEmit` and `npm run build` pass.

## 7. Known gaps and caveats

* **Opportunities are never auto-resolved** (same as the manufacturing engine). Risks are.
* **`PRICE_ANOMALY` impact** is computed slightly differently in detection (14-day volume, category target margin) and in investigation (21-day baseline margin), so the two figures can differ.
* **Multi-store:** `get_product_sales` averages in-stock days across a product's store rows, while detection counts per row. With more than one store the figures can differ slightly.
* **Merchant tables have no per-row `is_synthetic` column.** Synthetic labelling is at dataset level (`system_state`, `GET /merchant`, read models, evidence packs, impact `basis`). Signal and finding tables keep their flags.
* `Supplier.component_type` is reused for the merchant's "supplies" description (80 characters).
* Some enum values are defined but never generated (e.g. `StoreType.ONLINE`, `PurchaseOrderStatus.PARTIALLY_RECEIVED`/`CANCELLED`, `ExpenseCategory.PACKAGING`, `DiscountScope.STORE_WIDE`, `PriceChangeReason.PRICE_CHANGE`). `FamilySpec.lifecycle` and `BusinessTemplate.orders_per_day_hint` are unused.
* `GET /metrics/business` is shared with manufacturing, so on merchant data `production_cost` is 0.
* `/simulation/<name>` routes come from the manufacturing scenario list. On a merchant dataset only `supplier-delay` maps to a merchant scenario; the others return an error. Use `POST /demo/run`.
* There is no authentication (the approver is a typed name), and approval `EXPIRED` has no expiry job.
* Upload of the merchant's own data is shown as *coming soon* and is not implemented.

## 8. Next steps if work continues

1. Real data import (CSV / POS export) into the merchant tables, labelled `source` ≠ `SYNTHETIC`.
2. Deploy to AWS with `scripts/deploy_aws.sh`, then run `tests/aws_live`. Only after that may anyone say "runs on AWS" or "uses Bedrock".
3. Auth and roles; approval expiry.
4. Real action integrations behind the same approval gate.
