# REACT — Full Project Context (handoff for an AI assistant)

> **Historical (2026-10-03).** This file describes REACT before the REACT Merchant transformation, when it was a manufacturing demo (Astra Manufacturing). The current product, its rules and its status are in `README.md`, `PROJECT_STATE.md` and `docs/hackathon-presentation.md`. The manufacturing pack described here still exists as a backend reference.

Snapshot date: 2026-10-02. Verified directly against the repository at `/Users/sangeetasinha/Desktop/react`.
Use this as the single source of truth when helping extend the project. If something here conflicts with code you are shown later, trust the code and say so.

---

## 1. What REACT is

**REACT = Real-time Event Analysis & Crisis Triage.** A hackathon project for the **AWS Innovation Challenge 2026**.

It is an evidence-grounded AI business-intelligence and operations platform for manufacturers. It continuously answers four questions from business data:
1. **What is happening?** (operational incidents, business risks, opportunities)
2. **Why is it happening?** (ranked hypotheses scored against stored evidence)
3. **How much does it matter?** (rupee impact from deterministic formulas)
4. **What should we do next?** (one recommended action, human approval, simulated execution)

It is explicitly **not a chatbot**. Every statement must trace back to database records, and money is never computed by an LLM.

Demo company (fictional, all data synthetic): **Astra Manufacturing**, Industrial Components Manufacturing, Bangalore Manufacturing Plant (Peenya), currency INR. 3 production lines, 12 machines, 43 components, 43 sensors, 25 products in 5 categories, 120 B2B customers in 6 regions / 10 industries / 3 segments, 12 suppliers across 10 component types. Default history 120 days (~4,000 orders, ~124k hourly sensor readings).

End-to-end loop that works today:
```
synthetic data → scenario → detection → agent run (classify → investigate → verify) → impact
→ Decision Agent recommendation → human approve/reject → simulated action → evidence pack (audit copy)
```

---

## 2. Phase history and status

| Phase | Scope | Status |
|---|---|---|
| 1 | Synthetic data generator, 8 scenarios, rule-based detection (incidents, risks, opportunities), impact engine, API | Done 2026-09-25 |
| 2 | Deterministic investigation: tools, signals, hypothesis registry, evidence + Evidence Support Score, conclusion, impact, evidence graph, report, trace | Done 2026-09-25 |
| 3 | Agents: Detection / Investigation / Evidence agents + orchestrator, controlled tool calling, limits, retries, fallback, agent trace | Done 2026-09-27/28 |
| 4 | Decision Agent, deterministic decision validation, human approval, simulated action executor (16-action catalogue) | Done 2026-09-28 |
| 5 | Business web UI (React) + product endpoints (`/overview`, `/findings`, `/activity`, `/scan`, `/demo/run`) | Done 2026-09-28 |
| 6 | AWS architecture **in code only** (CDK), evidence packs to local/S3, `/ready`, AI-outage drill, LLM hourly budget, CloudWatch metrics, CI/CD workflows | Done in code 2026-09-29, **NOT deployed** |
| Next (not started) | Live AWS deployment, live Bedrock run, authentication, real data connectors, document upload, multi-tenancy, async investigations, presentation | — |

App version: `0.6.0` (`app/version.py`).

---

## 3. The most important honesty rules (judges will cross-question)

- **No LLM runs by default.** `LLM_PROVIDER=deterministic` is the default. The agents are driven by `DeterministicProvider` (rule-based, `app/agents/fallback.py`). Every run reports `llm_used=false`. The Amazon Bedrock provider (`BedrockProvider`, Converse API) is written and tested only with botocore Stubber / fake clients. **It has never called real Bedrock.**
- **Nothing is deployed to AWS.** The CDK stack synthesises and passes 12 assertion tests. No AWS account, credentials, CLI or Docker were available on the build Mac. Do not say "runs on AWS" until `scripts/deploy_aws.sh` has run and `/api/ready` answers from CloudFront.
- **All actions are simulated.** Approved actions create task/proposal records; no emails, purchases, production or supplier changes. A test proves business data is unchanged.
- **No authentication.** The approver is a free-text `reviewed_by` field.
- **Document upload does not exist.** The UI shows it as "Coming soon" with a disabled control.
- **The repository is not under git** and CI has never run on GitHub.
- Tag every claim in the deck as **implemented / demo-verified / planned**.

---

## 4. Non-negotiable project rules (the owner enforces these)

1. **Build only the named phase.** Do not add features from later phases unless asked.
2. **Money only from `app/impact/engine.py`.** LLM-facing schemas have no money fields; decisions and evidence packs copy engine figures.
3. **Terminology:** say **"Evidence Support Score"** (ESS). Never call it a probability, confidence or certainty. (The DB column `investigations.confidence` stores the ESS for legacy reasons; the API labels it as not a probability.)
4. **Traceability:** every insight, hypothesis, decision and evidence item carries source record ids.
5. **Determinism where possible:** seeded data, no wall-clock dependence (simulated "now" = `system_state.data_end`).
6. **Synthetic data stays labelled synthetic** (`is_synthetic=true`, `source="SYNTHETIC"`, `basis="SYNTHETIC"`).
7. **Thin routers:** business logic lives in engines/services, never in FastAPI route handlers.
8. Agent outputs may only cite evidence/hypothesis ids the deterministic engine produced and must restate the deterministic ESS.

---

## 5. Tech stack

**Backend:** Python 3.12+ (local venv on 3.13), FastAPI, SQLAlchemy 2, Pydantic 2 + pydantic-settings, Alembic, PostgreSQL 16/17, psycopg 3, boto3 (Bedrock + S3), uvicorn. No agent framework (no LangChain/CrewAI): the agent loop is plain Python. Hash-locked deps in `requirements.lock` (runtime source `requirements.runtime.in`).

**Frontend (`frontend/`):** Vite 8, React 19, TypeScript 7, React Router 7, plain CSS with design tokens (`src/styles/app.css`), no UI framework / icon font / web font. Tests: Vitest + Testing Library, Playwright E2E.

**Infra (`infra/`):** AWS CDK v2 (TypeScript, `aws-cdk-lib 2.271.0`), Vitest assertion tests. CDK is the only IaC framework.

**Ops:** two-stage non-root Dockerfile (base pinned by digest, HEALTHCHECK, entrypoint runs migrations under a PostgreSQL advisory lock), `docker-compose.yml` (postgres + backend), `.github/workflows/ci.yml` and manual `deploy.yml` (OIDC), `scripts/deploy_aws.sh`.

---

## 6. Local environment (owner's Mac)

- Homebrew PostgreSQL 17 on `localhost:5432`, **no password**. Databases: `react` (dev, under Alembic), `react_test` (pytest), `react_e2e` (Playwright, isolated).
- `.env`: `DATABASE_URL=postgresql+psycopg://localhost:5432/react`, `TEST_DATABASE_URL=postgresql+psycopg://localhost:5432/react_test`, `SEED_ON_STARTUP=true`, `AUTO_CREATE_SCHEMA=true`.
- No Docker installed. No AWS account/CLI configured.
- Venv at `.venv` (created with uv).
- UI dev port is **5180** (another local app owns 5173). Vite proxies `/api` to `REACT_API_TARGET` (default `http://127.0.0.1:8000`).

Run:
```bash
.venv/bin/uvicorn app.main:app --reload --port 8000     # API, Swagger at /docs
cd frontend && npm install && npm run dev               # UI at http://localhost:5180
```

Tests:
```bash
.venv/bin/pytest                    # backend (unit + integration), needs local PostgreSQL
.venv/bin/pytest -m unit            # no DB
cd frontend && npm test             # Vitest (36 tests)
cd frontend && npm run e2e          # Playwright (5 flows, uses react_e2e DB)
cd infra && npm test                # CDK assertions (12 tests)
REACT_AWS_LIVE=1 LLM_MODEL=<id> ARTIFACTS_BUCKET=<bucket> pytest tests/aws_live -m aws_live   # opt-in, real AWS (never run)
```

---

## 7. Repository layout

```
app/
  main.py               App factory, lifespan, request logging, error handlers (prefix /api stripped)
  version.py            APP_VERSION = "0.6.0"
  api/                  Thin routers: system, entities, insights, simulation, investigations, agents,
                        decisions, product, operations
  models/               SQLAlchemy ORM (39 tables)
  schemas/              Pydantic API schemas
  services/             Read/aggregate services (catalog, insight, metrics, system, investigation, agent,
                        decision, product, demo, artifact, readiness)
  simulation/           catalog.py, generator.py, history.py (6 historical incidents), clock.py, engine.py,
                        scenarios/ (8 scenarios)
  detection/            operational.py (incidents), risk.py (risks), engine.py (persist/dedupe/resolve), config.py
  opportunities/        engine.py + config.py
  impact/engine.py      ImpactEngine — the ONLY place money is computed
  investigation/        tools.py (18 tools), signals.py, registry.py (28 hypotheses), evidence.py (ESS),
                        analysis.py (conclusion + impact), graph.py, report.py, engine.py
  agents/               provider.py (LLMProvider, Bedrock, Mock, Unavailable), fallback.py (DeterministicProvider),
                        schemas.py, prompts.py, policies.py (tool allow-lists), state.py, base.py,
                        detection.py, investigation.py, evidence.py, decision.py, orchestrator.py
  agent_runtime/        limits.py, tool_executor.py, trace.py, guard.py (AI-outage drill + hourly LLM budget)
  decisions/            catalogue.py (16 actions), engine.py (validation + lifecycle), state.py, actions.py
                        (simulated executor), schemas.py
  storage/              base.py, local.py, s3.py (evidence-pack store)
  observability/        logging.py (JSON logs, secret masking), metrics.py (CloudWatch EMF)
  database/             session.py, base.py, locks.py (advisory locks)
  config/settings.py    All env-driven settings
alembic/versions/       5 migrations (see §9)
frontend/               React UI (see §14)
infra/                  CDK app: bin/react.ts, lib/react-stack.ts, test/react-stack.test.ts
scripts/                seed.py, demo.sh (Phase 1 flow only), deploy_aws.sh, entrypoint.sh,
                        e2e_backend.sh, capture_ui_fixtures.py
tests/                  backend tests incl. test_aws_stubbed.py, test_cloud_config.py, aws_live/
docs/                   data-model, simulation, detection, investigation, agents, decisions, frontend,
                        aws-architecture (status table in section 0), aws-presentation
var/artifacts/          local evidence packs (git-ignored)
README.md, architecture.md, PROJECT_STATE.md (stale, Phase 2 era), REACT_HACKATHON_AUDIT.md (Phase 3 era)
```

Size: ~15,900 lines of Python (app + tests + scripts), ~3,500 lines of frontend TS/TSX, CDK stack ~330 lines.

---

## 8. Configuration (key env vars)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` / `TEST_DATABASE_URL` | local postgres | When `DB_HOST` is set, URL is composed from `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SSLMODE` (ECS injects user/password from Secrets Manager) |
| `AUTO_CREATE_SCHEMA` | true (local) | false on AWS; Alembic is the schema authority |
| `SEED_ON_STARTUP`, `SEED`, `HISTORY_DAYS`, `SENSOR_INTERVAL_MINUTES` | true, 42, 120, 60 | |
| `AGENTS_ENABLED` | true | |
| `LLM_PROVIDER` | `deterministic` | also `bedrock`, `mock` (tests), `unavailable` (drill) |
| `LLM_MODEL` | **empty, no default** | Required for Bedrock; missing ⇒ treated as unavailable |
| `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `AWS_REGION` | 2048, 0, 30, 1, us-east-1 | |
| `AGENT_MAX_ITERATIONS / _TOOL_CALLS / _LLM_CALLS / _EXECUTION_SECONDS` | 4 / 12 / 10 / 120 (50 on AWS) | Hard per-run limits |
| `AGENT_FALLBACK_TO_DETERMINISTIC` | true | false ⇒ AI failure returns 503 |
| `LLM_HOURLY_CALL_BUDGET` | 200 | Counted in PostgreSQL across all containers |
| `STORAGE_BACKEND`, `STORAGE_LOCAL_DIR`, `ARTIFACTS_BUCKET`, `ARTIFACTS_PREFIX` | local, var/artifacts, –, evidence-packs/ | |
| `METRICS_ENABLED`, `METRICS_NAMESPACE` | false, REACT | EMF lines on stdout |
| `API_PATH_PREFIX` | /api | CloudFront forwards /api/*; stripped before routing |
| `CORS_ORIGINS` | localhost:5180 | |
| `DEMO_CONTROLS_ENABLED` | true | gates `POST /demo/ai-outage`; false in production |
| `REDIS_URL` | unset | Not used anywhere |

---

## 9. Data model (39 tables, 5 Alembic migrations)

Migrations chain: `387ed9242674` Phase 1 schema → `4fd6fbe3c4b1` Phase 2 investigations → `561ae240f5d0` Phase 3 agent runs → `a8d2ea2a985c` Phase 4 decisions/approvals/actions → `b6e1f0c2d9a7` Phase 6 artifacts + `system_state.ai_outage_mode`.
(A DB created with `AUTO_CREATE_SCHEMA` can be adopted with `alembic stamp a8d2ea2a985c && alembic upgrade head`.)

```
companies ─┬─ plants ─── production_lines ─┬─ machines ─┬─ machine_components
           │                               │            └─ sensors ─── sensor_readings (BIGINT id, hourly)
           │                               ├─ production_metrics (daily)
           │                               └─ products
           ├─ products ─┬─ order_items ─── orders ─── customers
           │            │                    └─ sales_transactions
           │            └─ inventory (PRODUCT) ─── inventory_snapshots
           ├─ suppliers ─── supplier_batches      inventory (COMPONENT) ─── inventory_snapshots
           └─ financial_metrics (daily)
machines ─── maintenance_records ─── machine_components
historical_incidents (6 seeded, with leading indicators)
market_signals, competitor_signals (synthetic)
incidents, business_risks, opportunities           (findings: evidence JSON, estimated_impact JSON, dedupe_key)
simulation_runs, detection_runs, system_state       (provenance; data_end = simulated now; ai_outage_mode)
investigations, hypotheses, evidence_items, investigation_trace_entries      (Phase 2)
agent_runs, agent_tool_calls, agent_trace_entries                            (Phase 3)
decisions, approvals, action_executions                                      (Phase 4)
artifacts                                                                    (Phase 6 evidence packs)
```

Conventions: UUID PKs (readings use BIGINT identity), enums stored as VARCHAR, JSONB columns, naive UTC timestamps.

Key enums:
- `IncidentType` (9): ABNORMAL_TEMPERATURE / VIBRATION / POWER / PRESSURE, PRODUCTION_DROP, DEFECT_SPIKE, MACHINE_DOWNTIME, MACHINE_DEGRADATION, SENSOR_FAULT
- `IncidentStatus`: DETECTED, INVESTIGATING, AWAITING_DECISION, AWAITING_APPROVAL, EXECUTING, RESOLVED
- `RiskType` (7): INVENTORY_SHORTAGE, SUPPLIER_DELAY, PRODUCTION_BOTTLENECK, REVENUE_DECLINE, MARGIN_DECLINE, DELIVERY_DELAY, DEMAND_SUPPLY_MISMATCH; `RiskStatus` OPEN/MITIGATING/RESOLVED
- `OpportunityType` (7): DEMAND_OPPORTUNITY, UNDERSERVED_REGION, HIGH_MARGIN_GROWTH, SEGMENT_GROWTH, EXCESS_INVENTORY, AVAILABLE_CAPACITY, PRICING_OPPORTUNITY; `OpportunityStatus` NEW/REVIEWED/DISMISSED; `Confidence` LOW/MEDIUM/HIGH (opportunity rule strength)
- `InvestigationStatus`: CREATED, COLLECTING_EVIDENCE, ANALYZING, COMPLETED, FAILED
- `AgentRunStatus`: CREATED, CLASSIFYING, INVESTIGATING, VERIFYING, COMPLETED, FAILED
- `DecisionStatus`: DECISION_READY, INSUFFICIENT_EVIDENCE, AWAITING_APPROVAL, APPROVED, REJECTED, EXECUTING, EXECUTED, FAILED
- `ApprovalStatus`: PENDING, APPROVED, REJECTED, EXPIRED (no expiry job)
- `ActionStatus`: PROPOSED, APPROVED, REJECTED, EXECUTING, COMPLETED, FAILED
- `DecisionReadiness`: READY (ESS ≥ 0.6 and no missing evidence), NEEDS_REVIEW, INSUFFICIENT_EVIDENCE
- `ToolCallStatus`: OK, DENIED, INVALID_ARGUMENTS, ERROR, LIMIT_EXCEEDED

---

## 10. Simulation (synthetic data)

- **Generator** (`app/simulation/generator.py`): deterministic per-section RNG `random.Random(f"{seed}:{section}")`; correlated day-by-day pipeline: demand → orders → production (bounded by capacity/downtime) → finished-goods inventory → BOM component consumption → reorder → supplier batches with reliability delays → financials/sales. Sensors follow shift/warm-up/load profiles and degradation. Plant runs 06:30–22:30 IST (01:00–17:00 UTC), Sundays off.
- **6 historical incidents** with leading indicators (bearing failure GRD-B1, hydraulic pressure loss HPR-A3, stuck sensor CNC-C1, furnace downtime FUR-B3, coating defect spike LINE-C, casting supply delay LINE-A).
- **8 scenarios** (`POST /simulation/<name>`, plus `/simulation/reset`): `bearing-failure` (CNC-C1), `sensor-failure` (VMC-A2-TEMP, stuck/spike/dropout), `supplier-delay` (Helios Controls GmbH, sole source of ELECTRONIC_ACTUATOR, +10 days), `demand-spike` (Industrial Valve X, South India, ×3), `demand-drop` (Pump Components), `inventory-shortage` (Industrial Valve X), `production-bottleneck` (LINE-C), `power-instability` (LINE-A). Each records a `simulation_run` with every touched record id.
- **Demo runner** `POST /demo/run` with scenarios: `bearing_failure`, `demand_spike`, `supplier_delay`, `baseline` (reset + scan).

---

## 11. Detection (deterministic)

- **Operational → incidents** (`app/detection/operational.py`): recent = last 24 operating hours vs 30-day baseline ending 3 days back. Sensor faults (dropout <50% readings, stuck ≥8 identical, implausible >2.2× critical or ≥20% zeros). Level anomalies (≥critical CRITICAL, ≥warning HIGH, z≥3 & ≥10% MEDIUM; pressure-low rules; RPM corroboration only). ≥2 anomalous sensor types (or one HIGH+ with line drop/defects) ⇒ one `MACHINE_DEGRADATION` (vibration+temperature HIGH ⇒ CRITICAL). Line rules: PRODUCTION_DROP (≥15% below baseline and <90% of plan), DEFECT_SPIKE (≥2× and ≥2%), MACHINE_DOWNTIME (8 h/7 d, 4 h/day, or machine DOWN).
- **Business risks** (`app/detection/risk.py`): 7 rules (stock vs safety/required, supplier delay incl. sole-source CRITICAL, bottleneck utilisation ≥95%, revenue decline 15%/25% category, margin decline 2 pts / 6 pts product, delivery delay, demand/supply mismatch 20%/35%).
- **Opportunities** (`app/opportunities/engine.py`): 7 rules, 28 d vs prior 28 d, significance z = growth / (1.3·√(1/n₁+1/n₂)) ≥ 2.
- **Persistence:** `dedupe_key` per finding; open matches updated (max severity, `occurrence_count += 1`); no-longer-detected incidents/risks auto-resolved. Each finding stores evidence `{window, rule, records{ids}, trace}` and `estimated_impact` from the engine.
- Thresholds live in `DetectionConfig` / `OpportunityConfig` dataclasses.

---

## 12. Investigation (deterministic, authoritative)

Pipeline (`app/investigation/engine.py`): signals via tool layer → hypothesis generation/ranking (registry triggers) → evidence items from supporting/contradicting conditions → ESS per item and per hypothesis → conclusion → impact → evidence graph → report → trace. Never modifies raw data; only incident status changes.

- **18 tools** (`tools.py`): 13 read (`get_machine_readings`, `get_production_metrics`, `get_maintenance_history`, `get_previous_incidents`, `get_supplier_history`, `get_inventory_status`, `get_customer_orders`, `get_market_signals`, `get_competitor_signals`, `get_financial_metrics`, `get_line_context`, `get_capacity_status`, `search_business_records`) + 5 calculation tools wrapping the impact engine. Validated inputs, `ToolError` codes, logged.
- **28 hypotheses, 264 conditions** (`registry.py`), data not code. Applicable by kind: 8 incident, 9 opportunity, 13 risk (some shared).
- **~74 named signals** (+ per-sensor families) with source ids.
- **Evidence Support Score:** per item = 0.35·relevance + 0.15·recency + 0.20·source reliability + 0.15·correlation + 0.15·consistency. Per hypothesis ESS = S / (S + C + 0.5·U) (supporting, contradicting, unobserved weight); capped at 0.15 with no satisfied support. Labels ≥0.8 strongly supported, ≥0.6 supported, ≥0.4 weakly supported. 15 source types with reliability weights (orders/production 0.95 … market 0.55, competitor 0.5).
- **Conclusion:** determined if ESS ≥ 0.5 and margin over runner-up ≥ 0.15, or both ≥ 0.8 (runner-up = contributing factor).

---

## 13. Agents, decisions, actions

**Orchestrator** (`app/agents/orchestrator.py`): CREATED → CLASSIFYING → INVESTIGATING → VERIFYING → COMPLETED (any → FAILED). Result: CONCLUDED / INSUFFICIENT_EVIDENCE / FAILED, with `ImpactReference` copied from the engine.

- **Detection Agent:** `DetectionAnalysis` (event type, severity, priority P1–P4, entities, objective, evidence categories). Must match the persisted finding. Tool: `search_business_records` only.
- **Investigation Agent:** bounded tool loop through `ToolExecutor` (allow-list → limit accounting → registry → summarised result → `agent_tool_calls` row + trace). Then runs the Phase 2 engine (authoritative) and returns `InvestigationAnalysis` that may only cite existing ids and must restate ESS (±0.011). Tools: 13 read + 5 calc. Note: its exploratory tool results do not feed the engine's evidence.
- **Evidence Agent:** `EvidenceAssessment` (verified, sufficient, ids, conflicts, missing, unsupported claims). Cannot rescore; cannot be sufficient without supporting ids. Tools: 13 read only.
- **Decision Agent** (`app/agents/decision.py`): one `DecisionRecommendation` from the **16-action catalogue** (`app/decisions/catalogue.py`): INSPECT_MACHINE, SCHEDULE_MAINTENANCE, MONITOR_MACHINE, REVIEW_PRODUCTION_PLAN, REVIEW_SUPPLIER, INCREASE_REORDER, REDUCE_PURCHASE, REVIEW_STOCK, REDISTRIBUTE_INVENTORY, CONTACT_CUSTOMER, REVIEW_PRICING, INCREASE_PRODUCTION, REVIEW_DEMAND, INVESTIGATE_MARKET, INCREASE_CAPACITY, PRIORITIZE_PRODUCT. Each of the 28 hypotheses maps to a default action + alternatives.
- **Decision engine validation** (deterministic, cannot be bypassed): subject/hypothesis match, ESS restated exactly, evidence ids exist and rationale cites one, INSUFFICIENT_EVIDENCE forced when run not CONCLUDED or ESS < 0.5, catalogue action applicable to the target, human approval required.
- **Action executor** (`app/decisions/actions.py`): runs only approved catalogue actions; produces simulated records (e.g. `SIM-MAIN-…` maintenance work order) with `simulated: true, business_records_modified: false`.
- **Provider safety:** invalid output → `validation` trace → retry with error fed back → flagged deterministic fallback (`fallback_used=true`) or failure. Limits never absorbed by fallback. `agent_runtime/guard.py` swaps in `UnavailableProvider` for the AI-outage drill or when the hourly LLM budget is exhausted.
- **Trace:** `agent_trace_entries` event types include state_transition, llm_call, tool_call, validation, retry, fallback, limit, investigation_engine, result, error, decision, approval_requested, approval, rejection, action_executed. No chain-of-thought is requested or stored.

Finding status mirrors the lifecycle: incidents → AWAITING_APPROVAL → EXECUTING (or AWAITING_DECISION on insufficient/rejected); risks → MITIGATING after execution; opportunities → REVIEWED.

**Evidence packs** (Phase 6): on approve/reject, a JSON audit copy (decision, approval, action, conclusion, cited evidence with record ids, impact figures copied, visible trace, provenance: seed, generator version, data window) is written to `var/artifacts/evidence-packs/YYYY/MM/DD/<decision_id>/<artifact_id>.json` locally or S3. SHA-256 stored on the `artifacts` row; download re-verifies and refuses tampered bytes. A storage failure never blocks the approval (row marked FAILED, UI notice, metric).

---

## 14. Frontend (Phase 5)

Routes: `/` Command Center (status Critical/Needs attention/Healthy, attention items, opportunities, what is at stake, activity) · `/risks` · `/opportunities` · `/cases/:kind/:id` (what happened, likely cause, **Why?** dialog, what does not fit, impact with *View calculation*, conclusion, recommendation, **Approve/Reject**, simulated action result, evidence pack with SHA-256 + download) with tabs `?tab=evidence` (evidence chain + table) and `?tab=technical` (ESS, hypothesis ranking, readiness, validation checks, agent trace, tool calls) · `/investigations` · `/decisions` · `/activity` · `/data` Data & System (synthetic data notice, system status, provider, `/ready` readiness, **AI failure drill**, upload "Coming soon").

Top bar: **Scan business** (`POST /scan`) and **Demo scenarios** (`POST /demo/run`).

UI rules: no fake data (every number from the API); backend is authoritative for state changes (409s shown); "Simulated" badges; owner language first (ESS shown as "Strongly supported" etc. in business view); impact breakdown marks which items make up the total (`total_includes`); accessibility (text + icon for status, labelled dialogs, skip link, aria-live, reduced motion). No WebSocket/polling: reloads on navigation and after actions via a `RefreshContext`.

Structure: `src/lib` (api client, endpoints, types mirroring backend, useApi, format ₹ lakh/crore, language.ts plain-language mapping), `src/components/{common,command-center,findings,case,evidence,decisions,activity}`, `src/pages`, `src/test` (fixtures captured from the real API via `scripts/capture_ui_fixtures.py`).

---

## 15. API surface (91 routes)

- **System/ops:** `GET /health` (liveness), `GET /ready` (DB, Alembic head, seeded data, storage config, AI state; 200/503; no secrets), `GET /system/status`, `GET/POST /demo/ai-outage {"mode": "off"|"fallback"|"fail"}`
- **Entities:** `/companies`, `/plants`, `/production-lines`, `/machines`, `/machines/{id}`, `/machines/{id}/readings`, `/products`, `/customers`, `/orders`, `/inventory`, `/suppliers`, `/metrics/business`
- **Findings:** `/incidents[/{id}]`, `/risks[/{id}]`, `/opportunities[/{id}]`, plus `/{kind}/{id}/evidence|investigation|agent-run|decision`
- **Simulation/detection:** `POST /simulation/{8 scenarios}`, `POST /simulation/reset`, `POST /detection/run`, `POST /opportunity-detection/run`
- **Investigations:** `POST/GET /investigations`, `GET /investigations/{id}[/hypotheses|/evidence|/report|/graph|/trace]`, `POST /investigations/{id}/run`, `GET /investigations/registry`, `GET /investigations/tools`
- **Agents:** `GET /agents`, `POST/GET /agent-runs`, `GET /agent-runs/{id}[/trace|/tool-calls|/result]`, `POST /agent-runs/{id}/run`, `POST /incidents|opportunities|risks/{id}/react`
- **Decisions:** `POST /react/run {incident_id|opportunity_id|risk_id}` (full workflow → AWAITING_APPROVAL), `POST/GET /decisions`, `GET /decisions/{id}[/evidence|/trace|/artifacts]`, `POST /decisions/{id}/approve {reviewed_by, reason?, execute?}`, `POST /decisions/{id}/reject {reviewed_by, reason}`, `POST /decisions/{id}/execute`, `GET /approvals[/{id}]`, `GET /actions[/{id}]`, `GET /actions/catalogue`
- **Product (UI):** `GET /overview`, `GET /findings?kind=incident|risk|opportunity|risk_all&include_closed=`, `GET /findings/{kind}/{id}`, `GET /activity`, `POST /scan`, `GET /demo/scenarios`, `POST /demo/run`
- **Artifacts:** `GET /artifacts/{id}`, `GET /artifacts/{id}/download`
- All routes also work under `/api/*` (prefix stripped).

Errors: 404 unknown ids, 409 invalid transitions, 422 validation, 503 DB down or AI unavailable (fallback off), 500 with a reference id only.

---

## 16. Impact engine (`app/impact/engine.py`)

17 pure methods over a frozen `ImpactConfig` (INR; default hourly loss 45,000, max 400,000; defect multiplier 1.15; pricing capture 0.5; excess redirect 0.6; expedite premium 0.25; late-delivery penalty 0.05; churn exposure 0.10; opportunity capture 0.7): production_loss, revenue_impact, inventory_risk, downtime_impact, defect_cost, machine_degradation_impact, supplier_delay_impact, late_delivery_impact, opportunity_value, pricing_opportunity_value, excess_inventory_value, revenue_loss, delivery_impact, customer_impact, inventory_impact, opportunity_revenue, inventory_requirement. Every result stores `formula`, `inputs`, `components`, `value`, `currency`, `is_estimate=true`, `basis="SYNTHETIC"`.

---

## 17. AWS architecture (Phase 6 — defined in CDK, NOT deployed)

```
Browser → CloudFront ─┬─ /*     → S3 site bucket (private, OAC)        React SPA
                      └─ /api/* → ALB (CloudFront prefix list only) → ECS Fargate (FastAPI + agents)
                                    ├─ RDS PostgreSQL 16 (isolated subnets; creds in Secrets Manager)
                                    ├─ S3 artifacts bucket (evidence packs, versioned, SSE-S3)
                                    ├─ Amazon Bedrock (optional, Converse; default rule-based)
                                    └─ CloudWatch (logs 30 d, EMF metrics, dashboard, 7 alarms)
Images: CDK builds the Dockerfile → ECR. Optional SNS alarm e-mail + AWS Budgets when alarmEmail is given.
```

Key facts: Fargate 0.5 vCPU / 1 GiB, 1 task, autoscale ceiling 3; RDS db.t4g.micro, gp3 20→50 GiB, 7-day backups (staging); 2-AZ VPC, no NAT gateway (tasks in public subnets, SG admits only the ALB), S3 gateway endpoint; IAM task role limited to `s3:PutObject/GetObject` on `evidence-packs/*` and `bedrock:InvokeModel` on one model when enabled; no admin or `*` actions (tests fail otherwise). 17 fixed custom metrics (agent runs, fallbacks, AI unavailable, LLM calls/tokens/budget, decisions, actions, evidence packs, tool/DB/unhandled failures). Est. ~$60–70/month without Bedrock. Stacks `React-staging` / `React-production`.

Deploy (never run): `cd infra && npm ci && npx cdk bootstrap` → `scripts/deploy_aws.sh` (with `LLM_PROVIDER=bedrock BEDROCK_MODEL_ID=<id>` / `ALARM_EMAIL=` / `MONTHLY_BUDGET_USD=` options) → check `/api/ready`. Teardown `npx cdk destroy React-staging` (buckets retained).

Proposed, not built: Step Functions, Bedrock AgentCore, SQS + workers, ElastiCache, Aurora, Cognito, WAF, Textract document ingestion, NAT / interface endpoints.

Known AWS gaps: CloudFront → ALB is HTTP (no domain/ACM); no auth; investigations synchronous (capped 50 s for CloudFront's 60 s timeout); drill and LLM budget are global, not per tenant.

Full detail: `docs/aws-architecture.md` (status table in section 0) and `docs/aws-presentation.md`.

---

## 18. Measured demo results (deterministic provider, seed 42, 120 days)

| Scenario | Detection | Agent result | Impact (synthetic estimate) |
|---|---|---|---|
| Bearing failure (CNC-C1) | MACHINE_DEGRADATION CRITICAL + PRODUCTION_DROP | CONCLUDED BEARING_DEGRADATION, ESS 0.953 (sensor failure 0.017); 5 tools; 9 supporting / 2 contradicting; 20 trace steps | ~INR 3.9M; decision SCHEDULE_MAINTENANCE (alternatives INSPECT, MONITOR) → approve → simulated work order |
| Sensor failure (VMC-A2-TEMP stuck) | SENSOR_FAULT MEDIUM only, detection impact INR 0 | CONCLUDED SENSOR_FAILURE 0.90, bearing 0.0 | — (best contrast demo) |
| Demand spike (Industrial Valve X, South India) | DEMAND_OPPORTUNITY HIGH, ~+270%, z ≈ 8.6 | CONCLUDED SEGMENT_EXPANSION (0.89–1.00), market category growth contributing | potential revenue ~INR 12.7–12.8M; constraints: capacity 64/day vs +68/day, 0.4 days stock, sole-sourced actuator |
| Supplier delay (Helios, +10 d) | SUPPLIER_DELAY CRITICAL, INVENTORY_SHORTAGE CRITICAL, DELIVERY_DELAY ×3 | CONCLUDED SINGLE_SOURCE_EXPOSURE 1.00 (supplier reliability 0.95 contributing) | ~INR 2.9M exposure |
| Power instability (LINE-A) | ABNORMAL_POWER ×2 | POWER_INSTABILITY 0.86 | — |

3-minute UI demo script is in `docs/frontend.md` (Command Center → bearing failure → investigate → Why? → evidence → approve → simulated work order → Command Center no longer critical; optional demand spike and AI failure drill).

---

## 19. Tests (all passing at last run)

- Backend pytest: ~153 tests (+2 opt-in `aws_live` skipped). Markers `unit`, `integration`, `aws_live`. Covers generator determinism, all scenarios, detection, opportunities, impact, investigation, agents (retries, fallback, tampered ids, rescored ESS rejected, limits, denied tools like `drop_table`/`run_sql`), decisions (validation, state machines, data-unchanged snapshot), product API, operations (DB down, S3 down, AI outage, unhandled errors), Bedrock/S3 via botocore Stubber, cloud config.
- Frontend: 36 Vitest, 5 Playwright E2E (AI failure drill, full acceptance flow, demand spike, rejection path, mobile).
- Infra: 12 CDK assertion tests (no admin, no open ports, private DB, encrypted buckets, cost ceilings).
- Test DB: rebuilt and seeded once per session (60 days, fixed anchor 2026-09-25 07:00); each test runs in a SAVEPOINT and rolls back.

---

## 20. Known issues / tech debt (good first tasks)

1. **Put the repo under git** and push, so CI (`ci.yml`) can actually run.
2. `GET /system/status` still returns `phase: "1"` and `llm_agents_enabled: false` (hard-coded in `app/schemas/system.py`, asserted in `tests/test_api.py`).
3. `app/main.py` OpenAPI description still says "Phase 1 … No LLM reasoning is involved".
4. README Phase 1/2 sections are stale ("7 scenarios", "28 tables", "8/9/11 hypotheses", "still no LLM"); `PROJECT_STATE.md` and `REACT_HACKATHON_AUDIT.md` are older snapshots.
5. **Bug:** `PRICING_HEADROOM` in `app/investigation/registry.py` (line ~249) has `requires_any=("competitor_price_increase_pct",)` inside a trailing comment, so it applies without that signal.
6. **Scenario RNG not reproducible across runs:** `scenarios/base.py` seeds with params, and `simulation/engine.py` injects `_run_id` (a fresh UUID) into params. Demo numbers drift ~1% per run.
7. Two money figures for one opportunity: detection `opportunity_value` (~INR 37.9M for the spike) vs investigation `potential_revenue` with 0.7 capture (~INR 12.8M). Both labelled, but confusing.
8. Sensor-fault impact differs: detection INR 0 vs investigation line-level exposure (~INR 0.33M).
9. Investigation Agent calls tools that the Phase 2 engine then calls again (double data access; agent tool results do not feed evidence).
10. `scripts/demo.sh` only covers the Phase 1 flow.
11. No auth/RBAC, no approval expiry job, no tenant model, synchronous investigations.
12. Unused: `REDIS_URL`, `AgentResultStatus` enum, `InvestigationPlan` / `LLMToolSpec` schemas.

---

## 21. Likely next work (pick by phase, confirm with the owner first)

- **Deploy & verify on AWS:** AWS account + CLI + Docker, `cdk bootstrap`, `scripts/deploy_aws.sh`, confirm `/api/ready`, then one live Bedrock run (`tests/aws_live`), and update `docs/aws-architecture.md` status labels from IMPLEMENTED to DEPLOYED only after that.
- **Bedrock live mode:** pick a current model/inference-profile id enabled in the account, run the agent workflow with `LLM_PROVIDER=bedrock`, compare against deterministic results, record `llm_used=true` runs for the demo.
- **Authentication:** Cognito sign-in, real reviewer identity on approvals, `DEMO_CONTROLS_ENABLED=false` in production.
- **Data ingestion:** CSV/Excel/PDF upload → S3 `uploads/` → extraction (Textract) → normalisation into the existing schema → detection. Currently "Coming soon" in the UI.
- **Async investigations:** SQS + ECS worker, UI polling a run id.
- **Hardening:** fix issues in §20, git + CI green, refresh docs, update the deck.
- **Presentation:** 10-slide deck; every claim tagged implemented / demo-verified / planned (see `docs/aws-presentation.md` for AWS talking points).

When proposing changes: keep routers thin, keep money in the impact engine, keep ESS wording, add/adjust tests in the matching tier, add an Alembic migration for any schema change, re-run `scripts/capture_ui_fixtures.py` after API changes that affect the UI, and keep CDK assertion tests passing.
