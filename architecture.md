# REACT Merchant: architecture

## 1. Purpose and boundaries

REACT Merchant turns a shop's own structured records (sales, stock, purchase orders, supplier deliveries, prices, discounts and expenses) into evidence-backed, human-approved decisions:

```
DETECT → INVESTIGATE → QUANTIFY (₹) → RECOMMEND → HUMAN APPROVAL → SIMULATED ACTION → AUDIT / EVIDENCE
```

It is a modular monolith: one FastAPI process, one PostgreSQL database and a React single-page app. It has no chatbot, queue, cache, microservices, WebSockets or agent framework. Every action is simulated.

## 2. Component view

```
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│ frontend/ (Vite, React, TypeScript)   pages call /api; no business numbers computed in the UI │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ app/api            thin routers + Pydantic schemas (no business logic)                       │
│ app/services       read models: overview, findings, activity, demo runner, readiness, artifacts│
├──────────────┬──────────────┬──────────────────┬──────────────┬────────────┬───────────────┤
│ domain packs │ detection    │ investigation    │ impact       │ agents     │ decisions     │
│ merchant     │ engines      │ signals, registry│ ImpactEngine │ provider,  │ catalogue,    │
│ (default),   │ (persist,    │ evidence + ESS,  │ (only place  │ tools,     │ validation,   │
│ manufacturing│ dedupe,      │ conclusion,      │ money is     │ policies,  │ approvals,    │
│ (reference)  │ resolve)     │ report, graph    │ computed)    │ limits     │ simulated exec│
├──────────────┴──────────────┴──────────────────┴──────────────┴────────────┴───────────────┤
│ app/models (SQLAlchemy) ─ PostgreSQL ─ Alembic migrations (history preserved, additive only) │
│ app/storage (evidence packs: local folder or S3)   app/observability (JSON logs, EMF metrics) │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

## 3. Domain packs

The generic engines (detection persistence, investigation, evidence scoring, impact, agents, decisions, evidence packs) do not know about retail. Domain specifics live in `app/domains/<pack>/` and are dispatched by the **active domain of the dataset**:

* `system_state.domain` records which pack generated the data, and `current_domain(db)` reads it. `settings.react_domain` (`REACT_DOMAIN`, default `merchant`) only decides what a fresh seed produces.
* Dispatch points: `seed_database`, `SimulationEngine.run/reset`, `DetectionEngine` and `OpportunityEngine`, `collect_signals`, `registry_for`, `analyse_impact`, report building, `TOOLS_BY_DOMAIN` / `resolve_tool`, `POLICIES_BY_DOMAIN`, `catalogue_for` / `hypothesis_actions_for`, the action executor, prompt localisation, the fallback planner, the demo service and the overview.
* **merchant** (`app/domains/merchant/`): catalog, profile, generator, scenarios, simulation, queries, baseline, detection, signals, registry, impact, language, tools, planner, actions and read models.
* **manufacturing** (`app/simulation/`, `app/detection/`, …): the original Astra Manufacturing pack. It is kept as a backend reference with its full test suite. The UI shows merchant only.

| Merchant module | Responsibility |
|---|---|
| `catalog.py`, `profile.py` | Business template (10 categories, 61 product families → 300 SKUs, 8 suppliers, localities), merchant profile validation |
| `generator.py`, `scenarios.py`, `simulation.py` | Deterministic 120-day generator; scenario events; regeneration with a `SimulationRun` |
| `queries.py`, `baseline.py` | Shared aggregations (sales, stock, deliveries, purchase orders); what is normal for this shop |
| `detection.py` | 15 rules → risks and opportunities with evidence record IDs |
| `signals.py`, `registry.py`, `language.py` | Signals for investigation, 28 hypotheses, plain-language key signals |
| `impact.py` | Deterministic merchant impact analysis and reorder suggestion |
| `tools.py`, `planner.py` | 16 read tools and 5 calculation tools; the deterministic tool plan |
| `actions.py` | 14-action catalogue, targets, parameters and the simulated executor |
| `read_models.py` | Products, inventory, suppliers, sales, business health, value metrics for the API |

## 4. Simulated time and data

`system_state.data_end` is the business "today". Detectors compare recent windows ending there with baselines before them. The merchant generator draws every section from its own seeded random stream, so a seed, history length, anchor and profile always give identical rows. Scenarios are **events injected into a regeneration** (demand shift, supplier slowdown, cost change, promotion, bulk purchase) rather than edits to existing rows, so stock, purchase orders, deliveries and accounts stay mutually consistent. See [docs/simulation.md](docs/simulation.md).

## 5. Request flow: one finding, end to end

```
POST /demo/run {scenario}      demo_service → regenerate(...) → DetectionEngine.run() + OpportunityEngine.run()
                               → findings with evidence.records (order_item, inventory_snapshot, purchase_order,
                                 supplier_delivery, pricing_history, discount … IDs) and ImpactEngine estimates
POST /react/run {risk_id}      Orchestrator (agent_runs, trace)
   CLASSIFYING     DetectionAgent        → DetectionAnalysis, validated against the stored finding
   INVESTIGATING   InvestigationAgent    → allow-listed tool calls (ToolExecutor: policy → limits → validation → log)
                                         → InvestigationEngine (authoritative): signals → hypotheses → evidence + ESS
                                           → conclusion (leading + contributing) → analyse_merchant_impact → report
   VERIFYING       EvidenceAgent         → may cite only engine-generated evidence/hypothesis IDs; ESS restated, not recomputed
   COMPLETED       agent run done → DecisionEngine.build_context → DecisionAgent picks one catalogue action;
                   DecisionEngine.validate (catalogue, target, cited IDs, ESS, approval required) + deterministic parameters
                   Decision AWAITING_APPROVAL, Approval PENDING, ActionExecution PROPOSED
POST /decisions/{id}/approve   APPROVED → EXECUTING → merchant executor → SIM-* record (business_records_modified=false)
                               → EXECUTED; evidence pack written (SHA-256); activity updated
POST /decisions/{id}/reject    REJECTED with reason; evidence pack written; nothing executed
```

## 6. Guarantees and where they are enforced

| Guarantee | Enforcement |
|---|---|
| Money never comes from an LLM | `ImpactEngine` is the only producer of ₹ values. Agent schemas have no monetary fields; `expected_impact` is copied from the engine |
| No invented evidence | Agent outputs are validated against the engine's evidence and hypothesis IDs. Unknown IDs are rejected and the call retried, then falls back or fails |
| Evidence Support Score, not probability | Computed in `app/investigation/evidence.py`; labels Strongly supported / Supported / Weakly supported / Insufficient support; agents restate it |
| Human approval before action | Decision and action state machines (`app/decisions/state.py`); the executor accepts only `EXECUTING` actions linked to an approval |
| Actions are simulated | Merchant executor writes only the action's result (`SIM-PO/PRICE/PROMO/PLAN/MOVE/SUPP/CRM-…`), with `simulated=true` and `business_records_modified=false` |
| Agents are sandboxed | Per-agent tool allow-lists per domain, argument validation, hard limits on iterations, tool calls, LLM calls, time and an hourly budget; no SQL, shell, filesystem or network tools |
| No chain-of-thought stored | Trace entries hold actions, tools, arguments, IDs, states, durations and errors only |
| Synthetic data labelled | Generated rows and estimates carry `is_synthetic` / `source="SYNTHETIC"` / `basis="SYNTHETIC"`; the UI shows a "Simulated business" stamp |
| Logic outside routers | Routers call services and engines; read models live in `app/services` and `app/domains/merchant/read_models.py` |

## 7. Detection, investigation and impact

* **Detection** ([docs/detection.md](docs/detection.md)): in-stock-adjusted velocity, exact Poisson tail tests on order lines (robust to lumpy quantities and to multiple testing across 300 SKUs), arrival-time estimates from supplier late-delay statistics, and minimum ₹ impact before a finding reaches the merchant. Findings are deduplicated by key; open findings no longer detected are resolved.
* **Investigation** ([docs/investigation.md](docs/investigation.md)): signals from the merchant tools, 28 registry hypotheses with trigger, supporting and contradicting conditions, evidence items with source record IDs and an Evidence Support Score, leading and contributing hypotheses, and the key signals for the **Why?** panel.
* **Impact**: stockout loss, supplier-delay exposure, margin loss and discount impact (90-day recurring horizon), inventory capital locked, demand opportunity value (30-day horizon), dependent revenue, purchase-order value and the price that restores margin. Every result records its formula, inputs and `basis="SYNTHETIC"`.

## 8. Agents and providers

`LLMProvider` has three implementations: `DeterministicProvider` (default, rule-based, offline), `BedrockProvider` (Converse API via boto3; the model ID comes only from configuration) and `MockLLMProvider` (tests). The deterministic provider runs the same tools and validation as an LLM would. If the AI is unavailable, `AGENT_FALLBACK_TO_DETERMINISTIC` decides between a labelled rule-based result and a 503 "AI investigation temporarily unavailable". The AI-failure drill (`POST /demo/ai-outage`) exercises both paths without calling AWS. See [docs/agents.md](docs/agents.md).

## 9. Frontend

The "Khata ledger" design: paper and ink palette, a teal accent, a red double margin rule on ledger panels, and money set in IBM Plex Mono. Pages: Dashboard, Findings, Opportunities, Products, Inventory, Suppliers, Decisions, Activity, Data & System, and Set up a demo business. The UI renders backend values and labels only. See [docs/frontend.md](docs/frontend.md).

## 10. Persistence and migrations

Alembic owns the schema. The merchant migration `c7d3e9a1f204` (2026-10-02) is additive: new tables (stores, product categories, purchase orders and items, supplier deliveries, pricing history, discounts, expenses) and nullable columns on existing tables. Nothing is dropped or renamed, and earlier revisions are untouched. The downgrade removes only what it added. See [docs/data-model.md](docs/data-model.md).

## 11. Observability and operations

Structured JSON logs with redaction, CloudWatch EMF metrics when enabled, `GET /health` (liveness) and `GET /ready` (database, migrations, data, storage, AI state; 200 or 503; no secrets). Evidence packs go to `./var/artifacts` locally or S3 on AWS, and downloads re-verify the SHA-256.

## 12. AWS (defined, not deployed)

`infra/` (AWS CDK, TypeScript) defines CloudFront → S3 (UI) and → ALB → ECS Fargate (API), RDS PostgreSQL with Secrets Manager credentials, an S3 evidence-pack bucket, CloudWatch dashboards and alarms, and optional Bedrock permissions for one configured model. The task runs with `REACT_DOMAIN=merchant`. **Nothing has been deployed and Bedrock has never been invoked live.** See [docs/aws-architecture.md](docs/aws-architecture.md).

## 13. Extension points

* Real data ingestion: the merchant tables match what a POS or accounting export provides; uploads are shown as *coming soon* and not implemented.
* Real actions: the executor handlers are the seam for purchase-order, pricing or messaging integrations, each of which would need approval-scoped credentials.
* Authentication: the approver is a typed name until auth exists. Approval `EXPIRED` is defined, but no expiry job runs.
* Another vertical: add a domain pack (catalog, generator, detection, signals, registry, impact, tools, actions) and register it in the dispatch points listed in §3.
