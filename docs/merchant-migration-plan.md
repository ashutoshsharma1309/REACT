# REACT Merchant: migration plan

Audit date: 2026-10-02. Source of truth: the repository as it stood after Phase 6.

## A. Current state summary

| Area | What exists | Domain-specific? |
|---|---|---|
| Architecture | FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL; services and engines behind thin routers; React 19 + Vite UI; AWS CDK stack (not deployed) | Core is generic, data layer is manufacturing |
| Schema | 39 tables, 5 Alembic revisions. Manufacturing-only: plants, production_lines, machines, machine_components, sensors, sensor_readings, production_metrics, maintenance_records, historical_incidents, supplier_batches. Reusable: companies, products, customers, orders, order_items, sales_transactions, inventory, inventory_snapshots, suppliers, financial_metrics, market_signals, competitor_signals, incidents, business_risks, opportunities, investigations, hypotheses, evidence_items, agent_* tables, decisions, approvals, action_executions, artifacts, simulation_runs, detection_runs, system_state | Mixed |
| API | ~95 routes: entities, simulation (8 manufacturing scenarios), detection, investigations, agents, decisions, product (`/overview`, `/findings`, `/activity`, `/scan`, `/demo/*`), operations (`/ready`, artifacts, AI drill) | Product and simulation routes are domain-shaped |
| Frontend | 10 routes, Command Center, risks, opportunities, case page with Why? and evidence, decisions, activity, Data & System; plain-CSS design tokens; 36 Vitest + 5 Playwright tests | Copy and `language.ts` are manufacturing |
| Simulation | Deterministic generator (demand → orders → production → inventory → supplier batches → financials) and 8 mutation scenarios | Manufacturing |
| Detection | Operational anomaly detector (sensors), business risk detector, opportunity detector; generic persistence with dedupe keys and auto-resolution | Rules manufacturing, persistence generic |
| Investigation | Generic engine: signals → hypothesis registry (condition DSL) → evidence → Evidence Support Score → conclusion → impact → graph → report | Engine generic; signals, registry, impact selection manufacturing |
| Agents | Orchestrator, 4 agents, tool executor with allow-lists, limits, trace, fallback, Bedrock/mock/deterministic/unavailable providers | Generic, except prompts, tool plan and allow-list |
| Decisions | Decision Agent, deterministic validation, approval lifecycle, simulated executor, evidence packs | Lifecycle generic; catalogue and handlers manufacturing |
| Tests | 153 backend (+2 AWS live skipped), 36 Vitest, 5 Playwright, 12 CDK | Backend tests exercise the generic core through manufacturing scenarios |
| AWS | CDK stack: CloudFront, S3, ALB, ECS Fargate, RDS, Secrets Manager, CloudWatch, optional Bedrock. Not deployed | Domain-neutral |

## B. Migration plan: domain packs, not a rewrite

The platform core is already domain-agnostic in its mechanics. The manufacturing knowledge sits in a small number of places: generator, scenarios, detection rules, signals, hypothesis registry, impact selection, tools, action catalogue, fallback tool plan, read models and UI copy. The migration adds a **merchant domain pack** next to it and routes those places through a domain hook.

* **The active domain is a property of the dataset.** `system_state.domain` records which pack generated the data, so every engine reads one value from the database. `REACT_DOMAIN` (default `merchant`) only chooses what a fresh seed produces.
* **Merchant is the default and the only domain the UI presents.** Manufacturing stays as a backend reference pack. Its 153 tests keep guarding the shared core: investigation engine, Evidence Support Score, agents, decision lifecycle, artifacts. Nothing that works is deleted.
* **Scenarios regenerate rather than patch.** A merchant scenario is an event injected into the deterministic simulation (demand surge, supplier slowdown, cost increase, bulk over-purchase). The same pipeline then produces sales, stock, purchase orders, deliveries and financials, so every table stays coherent. Same seed and anchor give the same data.
* **No new infrastructure.** No Redis, queues, WebSockets or agent frameworks.

Phases, each followed by tests:

| Phase | Work |
|---|---|
| B | Domain hook, merchant tables and columns, one additive Alembic migration |
| C | Merchant business template, profile, deterministic generator (120 days), merchant baselines |
| D | 15 merchant detection rules feeding the existing risk and opportunity persistence |
| E | Merchant tools, signals and about 28 hypotheses in the existing condition DSL |
| F | Merchant impact formulas in the Impact Engine and merchant impact analysis |
| G | Merchant action catalogue, deterministic action parameters, simulated executor (SIM-PO and others) |
| H | Merchant UI: Dashboard, Findings, Opportunities, Products, Inventory, Suppliers, case page, Why panel, onboarding |
| I | Five demo scenarios plus baseline through the real backend |
| J | Merchant backend tests, rewritten Vitest and Playwright suites |
| K | Documentation rewrite, demo script, presentation |
| L | AWS docs and CDK defaults updated; no deployment claim |
| M | Full regression, demo verification, no-fake-data and terminology sweep |

## C. Files to modify

| File | Change |
|---|---|
| `app/config/settings.py` | `react_domain` |
| `app/models/{organization,commerce,supply,finance,system,enums,__init__}.py` | Additive merchant columns, enum values, `system_state.domain` and `profile` |
| `app/simulation/generator.py`, `engine.py` | `seed_database(domain=…)` dispatch; reset keeps the dataset's domain; merchant scenarios regenerate |
| `app/detection/engine.py`, `app/opportunities/engine.py` | Use merchant detectors when the dataset is merchant |
| `app/investigation/{signals,registry,analysis,evidence,tools}.py` | Domain dispatch; merchant source reliabilities; domain-aware tool registry |
| `app/agents/{base,prompts,fallback,policies,detection,investigation,evidence,decision}.py` | Domain-aware prompts, finding context, tool plan and allow-list |
| `app/agent_runtime/tool_executor.py` | Domain-aware tool resolution |
| `app/decisions/{catalogue,engine,actions}.py` | Domain-aware catalogue, targets, parameters and executor |
| `app/impact/engine.py` | Merchant formulas |
| `app/services/{product,demo,catalog,metrics,artifact}_service.py`, `app/api/*` | Merchant read models and endpoints, additive schema fields |
| `frontend/src/**` | Merchant product experience |
| `tests/conftest.py` | Second session database for merchant data |
| Docs listed in the brief | Rewritten for REACT Merchant |

## D. Files to create

```
app/domains/__init__.py                      domain hook (current_domain, constants)
app/domains/merchant/catalog.py              business template: categories, products, suppliers, zones, segments
app/domains/merchant/profile.py              merchant profile (onboarding input) and validation
app/domains/merchant/generator.py            deterministic merchant simulation
app/domains/merchant/scenarios.py            5 scenarios + baseline as simulation events
app/domains/merchant/queries.py              shared sales / stock / supplier aggregations
app/domains/merchant/baseline.py             merchant "normal" ranges computed from data
app/domains/merchant/detection.py            15 detection rules
app/domains/merchant/tools.py                merchant investigation tools
app/domains/merchant/signals.py              merchant signal collection
app/domains/merchant/registry.py             merchant hypotheses
app/domains/merchant/impact.py               merchant impact analysis
app/domains/merchant/actions.py              action catalogue, parameters, simulated executor
app/domains/merchant/planner.py              deterministic tool plan and objectives
app/domains/merchant/read_models.py          dashboard health, products, inventory, suppliers, sales, value metrics
app/models/merchant.py                       stores, product_categories, purchase_orders, purchase_order_items,
                                             supplier_deliveries, pricing_history, discounts, expenses
app/api/merchant.py                          /merchant, /merchant/setup, /business-baseline, /sales, /metrics/value
alembic/versions/…_merchant_domain.py        additive migration
tests/merchant/…                             merchant test suite
frontend/src/pages/{Products,Inventory,Suppliers,Setup}Page.tsx and merchant components
docs/hackathon-demo.md, docs/hackathon-presentation.md
```

## E. Files to deprecate (kept, not deleted)

Manufacturing reference pack, unused by REACT Merchant and absent from the UI: `app/simulation/{catalog,history}.py` and `scenarios/*`, `app/detection/{operational,risk}.py`, manufacturing parts of `app/opportunities/engine.py`, manufacturing hypotheses in `app/investigation/registry.py`, manufacturing tools, `/machines`, `/plants`, `/production-lines`, `/simulation/<manufacturing scenario>` routes. They stay because they still pass their tests and anchor the shared core's regression suite. Old Alembic migrations are never deleted.

## F. Database migration plan

One additive revision after `b6e1f0c2d9a7`. No table is dropped or renamed, and no existing column changes type.

* **New tables:** `stores`, `product_categories`, `purchase_orders`, `purchase_order_items`, `supplier_deliveries`, `pricing_history`, `discounts`, `expenses`.
* **New nullable or defaulted columns:**
  * `companies`: `business_type`, `city`, `state`.
  * `products`: `category_id`, `brand`, `mrp`, `supplier_id`.
  * `customers`: `locality`, `acquisition_channel`.
  * `orders`: `channel`, `store_id`, `discount_total`, `fulfilment_cost`, `payment_method`.
  * `order_items`: `list_price`, `discount_amount`, `discount_id`.
  * `sales_transactions`: `payment_method`.
  * `inventory`: `store_id`.
  * `financial_metrics`: `discount_total`, `fulfilment_cost`.
  * `system_state`: `domain` (server default `manufacturing` for existing rows) and `profile`.
* **Reused as-is:** `companies` is the merchant, `business_risks` and `opportunities` are the findings, and the investigation, agent, decision and artifact tables are unchanged.
* **New enum values (VARCHAR columns, no DDL):** risk types `STOCKOUT_RISK`, `DEMAND_DECLINE`, `SUPPLIER_RELIABILITY`, `PRICE_ANOMALY`, `DISCOUNT_LEAKAGE`, `EXCESS_INVENTORY`, `SLOW_MOVING_INVENTORY`, `REPEAT_CUSTOMER_DECLINE`. Opportunity types `CATEGORY_OPPORTUNITY`, `REGIONAL_OPPORTUNITY`. Merchant customer segments.
* Downgrade drops only what the revision added.

## G. API migration plan

Kept, with merchant read models when the dataset is merchant:

* `/overview` gains business health and business impact.
* `/findings`, `/findings/{kind}/{id}`, `/activity`, `/scan`, `/demo/scenarios`, `/demo/run` keep their paths.
* `/products`, `/inventory`, `/suppliers` and `/customers` gain optional merchant fields: velocity, days of cover, stock age, on-time rate. Manufacturing responses are unchanged.
* `/metrics/business` returns merchant revenue, margin, discounts and expenses.

New endpoints:

* `GET /merchant` and `POST /merchant/setup` (onboarding, "Generate demo business");
* `GET /business-baseline`;
* `GET /sales`;
* `GET /metrics/value` (estimated, potential and simulated value metrics).

All routers stay thin.

## H. Frontend migration plan

* **Navigation:** Dashboard, Findings, Opportunities, Products, Inventory, Suppliers, Decisions, Activity, Data & System. There is also a Setup page for onboarding.
* **Reused and adapted:** layout, states, dialogs, evidence table, impact panel, recommendation panel, evidence pack and stepper.
* **New:** business health tiles, business impact strip, Why panel driven by backend key signals, product, inventory and supplier tables, onboarding form, and demo scenarios panel.
* `language.ts` is rewritten for merchant signals, and Evidence Support Score labels follow the brief.
* Every number still comes from the API.

## I. Test migration plan

| Suite | Plan |
|---|---|
| Manufacturing backend (153) | Unchanged; runs on its own seeded test database with `domain=manufacturing` |
| Merchant backend (new) | Second session database. Covers generator coherence and determinism, baselines, every detection rule, investigation per scenario, ESS bands, impact formulas, actions and parameters, approval, simulated execution, artifacts and API |
| Vitest | Rewritten on fixtures captured from the merchant API |
| Playwright | Stockout, demand opportunity, supplier risk, margin leakage, excess inventory, rejection, AI failure and fallback, mobile |
| CDK | Existing 12 assertions kept |
