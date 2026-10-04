# REACT — Repository Audit, Master Summary and Presentation Content

> **Historical (2026-10-03).** This file describes REACT before the REACT Merchant transformation, when it was a manufacturing demo (Astra Manufacturing). The current product, its rules and its status are in `README.md`, `PROJECT_STATE.md` and `docs/hackathon-presentation.md`. The manufacturing pack described here still exists as a backend reference.

> **Update 2026-09-29 (Phase 6).** Several items this audit lists as planned are now **implemented in code but not deployed**:
> * S3 (evidence packs);
> * the AWS deployment definition (`infra/`, AWS CDK: CloudFront, S3, ECS Fargate, ALB, RDS, Secrets Manager, CloudWatch);
> * CI (`.github/workflows/ci.yml`);
> * human approval and the frontend (Phases 4 and 5).
>
> The following remain **planned**:
> * Step Functions, AgentCore, SQS, ElastiCache, Cognito and authentication;
> * document upload;
> * live Bedrock reasoning;
> * any actual AWS deployment.
>
> The authoritative status table is section 0 of `docs/aws-architecture.md`.

**[IMPLEMENTED]** exists in code · **[DEMO-VERIFIED]** executed on 2026-09-28 against the local database and covered by the passing test suite · **[PARTIAL]** code exists but a key part has never run for real · **[PLANNED]** not in the repository.

---

## PART 1 — FULL REPOSITORY AUDIT

### 1.1 Inventory

| Area | Location | Status |
|---|---|---|
| FastAPI app | `app/main.py`, `app/api/` (6 routers, 61 routes) | [IMPLEMENTED] |
| ORM models | `app/models/` (35 tables) | [IMPLEMENTED] |
| Migrations | `alembic/versions/` 3 revisions: Phase 1 schema `387ed9242674`, Phase 2 investigations `4fd6fbe3c4b1`, Phase 3 agent runs `561ae240f5d0` | [IMPLEMENTED] |
| Pydantic schemas | `app/schemas/` (entities, insights, investigations, agents, system, common) | [IMPLEMENTED] |
| Services | `app/services/` (catalog, insight, metrics, system, investigation, agent) | [IMPLEMENTED] |
| Simulation | `app/simulation/` (catalog, generator, history, clock, engine, 8 scenarios) | [IMPLEMENTED] |
| Detection | `app/detection/` (operational, risk, engine, config, base) | [IMPLEMENTED] |
| Opportunities | `app/opportunities/` | [IMPLEMENTED] |
| Impact | `app/impact/engine.py` | [IMPLEMENTED] |
| Investigation | `app/investigation/` (tools, signals, registry, evidence, analysis, graph, report, engine) | [IMPLEMENTED] |
| Agents | `app/agents/` (provider, fallback, schemas, prompts, policies, state, base, detection, investigation, evidence, orchestrator) | [IMPLEMENTED] with the deterministic provider |
| Agent runtime | `app/agent_runtime/` (limits, tool_executor, trace) | [IMPLEMENTED] |
| LLM provider: Amazon Bedrock | `BedrockProvider` in `app/agents/provider.py` | [PARTIAL]: unit-tested against a fake boto3 client only; never called real Bedrock |
| Tests | `tests/` 10 files, **89 tests, all passing** (2026-09-28) | [IMPLEMENTED] |
| Docker | `Dockerfile`, `docker-compose.yml` (postgres + backend, redis commented out) | [IMPLEMENTED], never run on the dev Mac (Docker not installed) |
| Docs | `README.md`, `architecture.md`, `docs/{data-model,simulation,detection,investigation,agents}.md`, `PROJECT_STATE.md` | [IMPLEMENTED], partly stale (see 1.3) |
| Scripts | `scripts/seed.py`, `scripts/demo.sh` (Phase 1 flow only) | [IMPLEMENTED] |
| Frontend | none | [PLANNED] |
| Authentication | none | [PLANNED] |
| Decision / Response agents, human approval, action execution | none | [PLANNED] |
| Any AWS service other than the Bedrock client class | none | [PLANNED] |
| Version control / CI | repository is **not** a git repository; no CI | gap |

About 12,300 lines of Python across `app/`, `tests/`, `scripts/`. Dependencies: fastapi, uvicorn, sqlalchemy 2, psycopg 3, pydantic 2, pydantic-settings, alembic, python-dotenv, boto3; pytest and httpx for tests. No agent framework (no LangChain, CrewAI or similar); the agent loop is plain Python.

### 1.2 The single most important nuance for judges
The agent workflow runs end to end, but **by default no LLM is involved.** `LLM_PROVIDER=deterministic` is the default. In that mode the three agents are driven by `DeterministicProvider` (`app/agents/fallback.py`), a rule-based stand-in that builds every agent output from the structured context and plans tools from a fixed table per subject kind. Every measured run reports `llm_used=false`. The Bedrock path (`LLM_PROVIDER=bedrock`) is written and unit-tested with a fake client, but it has never been executed against Amazon Bedrock.

A second nuance: the Investigation Agent's tool calls are exploratory and traced, but they do **not** feed the evidence. After the tool loop, the agent runs the Phase 2 `InvestigationEngine`, which collects its own signals through the same tools. The authoritative evidence, scores and conclusion come from that engine. In practice the tools are called twice per run.

### 1.3 Inconsistencies found
1. `GET /system/status` still returns `phase: "1"` and `llm_agents_enabled: false` (hard-coded defaults in `app/schemas/system.py`, asserted by `tests/test_api.py`), even though agents are enabled by default.
2. `app/main.py` still titles the API "REACT Phase 1 API" and its description says "No LLM reasoning is involved in Phase 1".
3. README is internally mixed: the Phase 3 section is current, but the Phase 1 table says "7 correlated scenarios", the layout tree says "28 tables", and the Phase 2 bullet says "Future agents call these; there is still no LLM".
4. Hypothesis counts in README/docs say "8 incident, 9 opportunity, 11 risk". Code: 28 specs, 264 conditions; applicable to incidents 8, opportunities 9, risks 13 (three specs serve more than one kind).
5. **Bug:** `PRICING_HEADROOM` in `app/investigation/registry.py` has its `requires_any=(...)` argument inside a trailing comment, so it applies without the competitor-price signal being present.
6. **Scenario runs are not exactly reproducible.** Each scenario seeds its RNG with the request parameters, and the engine injects the new `simulation_run` UUID (`_run_id`) into those parameters. Demand-spike output therefore varies slightly per run (measured +268% / 3,765 units in one run and +270% / 3,783 units in another). The baseline generator itself is deterministic (tested).
7. **Different money figures for the same opportunity.** Detection stores `opportunity_value` (growth extrapolated over 30 days, about INR 37.9M for the demand spike). The investigation stores `potential_revenue` with a 0.7 capture ratio (about INR 12.7–12.8M). Both are formula-based and labelled; the difference will draw questions.
8. **Sensor fault impact differs by layer.** Detection records INR 0 impact for a SENSOR_FAULT. The investigation's incident impact analysis still computes line-level production and delivery exposure, measured at INR 334,618. Say "detection impact INR 0" if you use that number.
9. Default `LLM_MODEL` is `anthropic.claude-3-5-sonnet-20240620-v1:0`, an older Bedrock model id.
10. `scripts/demo.sh` does not exercise investigations or agents.
11. `app/observability/logging.py` docstring lists only Phase 1 event families; `investigation.*`, `tool.*`, `agent.*`, `agent_run.*` events exist.

### 1.4 Dead or unused infrastructure
- Redis: `REDIS_URL` setting and a commented-out service. Nothing reads it.
- `IncidentStatus.AWAITING_DECISION`, `AWAITING_APPROVAL`, `EXECUTING`: reserved, never set.
- `AgentResultStatus` enum: defined, the code uses string literals instead.
- `ToolCallResult`, `InvestigationPlan`, `LLMToolSpec` schemas: `InvestigationPlan` and `LLMToolSpec` are unused.
- `app/agents/provider.py` `generate()` (free text): no agent calls it.
- Linter-silencing no-ops (`_ = timedelta`, `_ = lines, is_working_day`) in scenarios.
- `Finding.severity` is always "MEDIUM" for opportunities and never used.

---

## PART 2 — ACTUAL PROJECT SUMMARY

### For a non-technical business owner
1. **What is REACT?** A backend system that watches a manufacturer's data (machines, production, orders, stock, suppliers, money) and reports what is going wrong, where an opportunity is, why, and how much it matters in rupees.
2. **Problem solved.** The data exists, but nobody has time to connect a vibrating machine, a late supplier, a demand surge and a margin drop into one clear answer. REACT does that connecting and shows its working.
3. **Intended user.** Plant managers, operations heads and owners of mid-sized manufacturers. The demo company, Astra Manufacturing in Bangalore, is fictional.
4. **What the user gives REACT.** Today, nothing manually: REACT generates realistic demo data itself. In the target product it would connect to ERP, production and sensor systems. Uploading documents is not built.
5. **What REACT does.** It runs rules and statistics to find incidents, risks and opportunities. It then investigates: lists possible causes, gathers evidence for and against each, scores that evidence, picks the best-supported explanation, estimates the rupee impact, and has a second step check that every claim is backed by a record.
6. **What REACT returns.** A finding, the records behind it, ranked explanations with an Evidence Support Score, an impact estimate with its formula, a recommended next step, and a step-by-step trace. Today this comes back through an API, not a screen.
7. **How it helps.** It turns "vibration is up on machine C1" into "bearing degradation is the best-supported cause (score 0.95); estimated impact INR 3.9M; inspect the bearing assembly."
8. **Better than a dashboard.** A dashboard shows numbers; you still have to notice, connect and interpret them. REACT does the noticing, connecting and interpreting, and links each statement to its source records.
9. **Different from a chatbot.** It does not answer free-form questions or make up facts. Statements come from database records and fixed formulas. The AI layer is fenced in: it may only cite evidence the system already produced and can never produce a money figure.

### For a software engineer or judge
REACT is a Python FastAPI service on PostgreSQL. A deterministic generator seeds 120 days of correlated synthetic data for a fictional plant; 8 scenarios perturb it with cross-table effects and record provenance. Rule-based detectors create 9 incident types, 7 risk types and 7 opportunity types, each persisted with a dedupe key, record ids and an impact estimate from a single formula engine. A deterministic investigation engine turns any finding into ranked hypotheses (28-spec registry, 264 conditions), evaluates them against about 74 named signals gathered through 18 validated tools, scores each evidence item with a five-factor Evidence Support Score, selects a conclusion with margin rules, computes impact, and builds an evidence graph, report and trace. A Phase 3 agent layer wraps this: an orchestrator state machine runs a Detection Agent, an Investigation Agent (bounded tool-calling loop through a policy-checked executor, then assessment of the engine's hypotheses) and an Evidence Agent (grounding verification), with hard limits, schema validation, retries, a flagged deterministic fallback and a persisted trace. The LLM behind those agents is pluggable; the default is a rule-based provider, and an Amazon Bedrock Converse provider exists but has not been run against Bedrock.

---

## PART 3 — USER EXPERIENCE

| Stage | Status | What exists |
|---|---|---|
| Business data | IMPLEMENTED (synthetic only) | Generator + 8 scenarios |
| Documents / real data connectors | FUTURE | Nothing |
| Data understanding | IMPLEMENTED (structured schema, catalogue, BOM, regions, segments) | 35 tables |
| Detection | IMPLEMENTED | 3 detectors, 23 finding types |
| Investigation | IMPLEMENTED | Deterministic engine + agent workflow |
| Evidence | IMPLEMENTED | Evidence items, ESS, graph, Evidence Agent verification |
| Impact | IMPLEMENTED | Impact engine; agents only reference it |
| Recommended next step | IMPLEMENTED (from the hypothesis registry) | `recommended_next_step` |
| LLM-written reasoning and explanation | IN DEVELOPMENT | Bedrock provider written, not run live |
| Decision | FUTURE | Reserved status only |
| Human approval | FUTURE | Reserved status only |
| Action | FUTURE | Nothing |
| Frontend | FUTURE | Nothing |

Document-upload journey ("upload business documents, REACT understands, analyses, explains, recommends"): **FUTURE PRODUCT DIRECTION.** There is no upload endpoint, parser or unstructured-data pipeline.

What a user can do today through Swagger (`/docs`) or curl: reset data, trigger scenarios, run detection, list findings with evidence and impact, run a deterministic investigation, run the full agent workflow with one call (`POST /incidents/{id}/react`, `/opportunities/{id}/react`, `/risks/{id}/react`), and read hypotheses, evidence, report, graph, investigation trace, agent trace, tool calls and final result.

---

## PART 4 — CURRENT TECHNICAL ARCHITECTURE

```
Generator + 8 scenarios ──► PostgreSQL (35 tables) ──► Detectors ──► incidents / risks / opportunities
                                                           (evidence ids + ImpactEngine)
                                                                  │
                     ┌──────────────── Orchestrator (state machine, limits, trace) ─────────────────┐
                     │ CLASSIFYING   Detection Agent ──► DetectionAnalysis                          │
                     │ INVESTIGATING Investigation Agent ──► ToolExecutor ──► 18-tool registry      │
                     │               └─► InvestigationEngine (authoritative: signals → hypotheses → │
                     │                   evidence → ESS → conclusion → impact → graph → report)     │
                     │               └─► InvestigationAnalysis (ids and scores must match)          │
                     │ VERIFYING     Evidence Agent ──► EvidenceAssessment                          │
                     │ COMPLETED     InvestigationResult + ImpactReference                          │
                     └──────────────────────────────────────────────────────────────────────────────┘
                     LLMProvider: DeterministicProvider (default) | BedrockProvider | MockLLMProvider
                                                                  │
                                                        FastAPI (61 routes)
```

| Component | What it does | Why it exists | Connects to |
|---|---|---|---|
| FastAPI | 61 routes, OpenAPI docs, request logging, error mapping | Thin transport | Services (reads), engines and orchestrator (writes) |
| PostgreSQL + SQLAlchemy 2 + Alembic | 35 tables, 3 migrations, JSONB, UUID keys, naive UTC | Single source of truth | Everything |
| Pydantic 2 | API contracts and strict agent output schemas (`extra="forbid"`) | Validation and grounding | API, agents |
| Simulation engine | Seed + scenarios, recorded in `simulation_runs` | Realistic reproducible demo data | Tables |
| Detection / opportunity engines | Rules, statistics, dedupe, resolve | "What is happening" | Findings tables |
| Impact engine | 17 pure formulas, frozen config | Only place money is computed | Detectors, investigation, agents (by reference) |
| Investigation engine | Deterministic "why" pipeline | Authoritative evidence and scores | Tools, investigation tables |
| Tool registry | 18 validated, logged tools | Contract between engines, agents and data | Investigation engine, ToolExecutor |
| Hypothesis registry | 28 specs as data | Configurable candidate causes | Investigation engine |
| Evidence graph | Subject → Signal → Evidence → Hypothesis → Conclusion + record nodes | Walk every claim back to rows | `investigations.graph` |
| Orchestrator | State machine CREATED → CLASSIFYING → INVESTIGATING → VERIFYING → COMPLETED (any → FAILED) | Controls the agent workflow | Agents, runtime, `agent_runs` |
| Agents (3) | Frame, explore/assess, verify | AI layer around the engines | Provider, ToolExecutor |
| Agent runtime | Limits, tool executor with allow-lists, tracer | Safety and audit | `agent_tool_calls`, `agent_trace_entries` |
| LLM provider abstraction | `generate`, `generate_structured`, `generate_with_tools` | Swap rule-based, mock or Bedrock | Agents |
| Frontend, auth, AWS services beyond the Bedrock client | absent | — | — |

---

## PART 5 — DATA MODEL

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
machines / production_lines ─── historical_incidents (6 seeded)
market_signals, competitor_signals (is_synthetic=true, source="SYNTHETIC")
incidents, business_risks, opportunities
simulation_runs, detection_runs, system_state (data_end = simulated "now")
investigations, hypotheses, evidence_items, investigation_trace_entries      (Phase 2)
agent_runs, agent_tool_calls, agent_trace_entries                            (Phase 3)
```

Key enums: `IncidentType` (9), `IncidentStatus` (DETECTED, INVESTIGATING, AWAITING_DECISION, AWAITING_APPROVAL, EXECUTING, RESOLVED), `RiskType` (7), `RiskStatus`, `OpportunityType` (7), `OpportunityStatus`, `Confidence` (opportunity LOW/MEDIUM/HIGH), `Severity`, `ScenarioType` (8 + reset), `InvestigationStatus`, `HypothesisStatus`, `AgentRunStatus` (CREATED, CLASSIFYING, INVESTIGATING, VERIFYING, COMPLETED, FAILED), `AgentName` (detection_agent, investigation_agent, evidence_agent, orchestrator), `ToolCallStatus` (OK, DENIED, INVALID_ARGUMENTS, ERROR, LIMIT_EXCEEDED).

Representations:
- **Incident / risk / opportunity:** type, severity or confidence, status, linked entities, title, description, metrics, `estimated_impact` (formula, inputs, components, value, currency, is_estimate, basis), `evidence` (window, rule, record ids, trace), run ids, `occurrence_count`, `dedupe_key`, `is_synthetic`.
- **Investigation:** subject, status, `confidence` (= Evidence Support Score of the leading hypothesis), label, conclusion, impact, report, graph, signals, error.
- **Hypothesis:** type, rank, triggered signals, conditions, initial_confidence (share of trigger weight), ESS, counts, status, score breakdown.
- **Evidence item:** signal, source_type, source_id(s), timestamp, claim, value, unit, baseline, relevance, supports/contradicts, ESS, 5-factor breakdown.
- **Agent run:** subject, investigation_id, status, provider, model, `llm_used`, `fallback_used`, iterations, tool/LLM call counts, tokens, limits, detection_analysis, investigation_analysis, evidence_assessment, result, result_status, error.
- **Agent tool call:** sequence, agent, tool, arguments, purpose, status, permitted, duration, result summary (count, keys, record ids; never the full payload).
- **Agent trace entry:** sequence, agent, event_type (state_transition, llm_call, tool_call, validation, retry, fallback, limit, investigation_engine, result, error), state, tool, evidence ids, hypothesis ids, summary, duration, status, error. No chain-of-thought.

Data flow: Company → Plant → 3 lines → 12 machines → 43 sensors → hourly readings; daily production; orders from 120 customers; finished-goods and component inventory; supplier batches from 12 suppliers; daily financials → incidents / risks / opportunities → investigation → evidence → impact → agent run → result.

---

## PART 6 — DETECTION SYSTEM (verified against code)

### 6.1 Operational anomalies → incidents (`app/detection/operational.py`)
Inputs: sensor readings (recent = last 24 operating hours; baseline = 30 days ending 3 days before now, at least 40 points), production metrics (last 7 working days vs prior 30), components, historical incidents.

Per sensor: dropout (under 50% of expected readings in 24 h), stuck (8+ identical trailing values), implausible (max above 2.2 × critical, or 20%+ zeros on temperature/vibration/power) → `SENSOR_FAULT`. Level: mean ≥ critical → CRITICAL, ≥ warning → HIGH, z ≥ 3 with change ≥ 10% → MEDIUM. Pressure low: ≤ 0.9 × normal minimum → HIGH, z ≤ −3 with change ≤ −8% → MEDIUM. RPM: |z| ≥ 3 and |change| ≥ 4% → LOW, corroboration only.

Per machine: two or more anomalous sensor types, or one HIGH/CRITICAL anomaly with a line output drop ≥ 15% or defect ratio ≥ 2 → one `MACHINE_DEGRADATION` (vibration plus temperature at HIGH upgrades to CRITICAL); the most-worn component and similar historical incidents are attached. Otherwise single anomalies become `ABNORMAL_<TYPE>` with projected downtime of 2/4/8/16 h by severity. Sensor faults stay sensor faults, with text noting when other sensors and production are normal.

Per line: `PRODUCTION_DROP` when output is 15%+ below baseline and below 90% of plan (a planned cut is not an incident; CRITICAL at 35%, HIGH at 25%). `DEFECT_SPIKE` when defect rate ≥ 2× baseline and ≥ 2% (HIGH at 3×). `MACHINE_DOWNTIME` at 8 h in 7 days, 4 h in one day, or a machine DOWN. Supply context (components below safety stock, delayed batches) is attached.

Example. PROBLEM: production drops. REACT checks 7-day output vs 30-day baseline, output vs plan, defects, downtime, component stock, delayed batches and correlated machine findings. REACT identifies (measured): "Production drop on Hydraulic Fittings & Pump Components Line: 23% below baseline", linked to the CRITICAL degradation on CNC-C1.

### 6.2 Business risks (`app/detection/risk.py`)
| Risk | Rule | Impact formula |
|---|---|---|
| INVENTORY_SHORTAGE (product) | available < safety stock, or < 50% of (orders due in 7 d + 7 d demand); CRITICAL at ≤ 0 or < 2 days cover | inventory_risk |
| INVENTORY_SHORTAGE (component) | available < safety stock, or < 5 days cover with no on-time inbound batch | inventory_risk |
| SUPPLIER_DELAY | late open batch, lead time ≥ 1.25× baseline, or AT_RISK; CRITICAL if sole source and stock below safety | supplier_delay_impact |
| PRODUCTION_BOTTLENECK | 7-day utilisation ≥ 95%, or plan missed 10%+ on 3+ days at 85%+, or line BOTTLENECKED | production_loss + late_delivery_impact |
| REVENUE_DECLINE | company 14 d ≥ 15% below normalised prior; category 28 d ≥ 25% down with 4%+ share | revenue_impact |
| MARGIN_DECLINE | company margin down ≥ 2 pts; product ≥ 6 pts under target with 1%+ share | revenue_impact on gross profit |
| DELIVERY_DELAY | per line ≥ 3 overdue orders or ≥ 15% late deliveries in 14 d | late_delivery_impact |
| DEMAND_SUPPLY_MISMATCH | 14-day demand vs good output: 20%+ under or 35%+ over | production_loss / excess_inventory_value |

### 6.3 Opportunities (`app/opportunities/engine.py`), 28 days vs prior 28
Significance: z = growth / (1.3·√(1/n₁ + 1/n₂)) on order-line counts, z ≥ 2 required.
| Opportunity | Rule | HIGH confidence when |
|---|---|---|
| DEMAND_OPPORTUNITY | product +30% (product × region +50%), z ≥ 2, 8+ recent / 6+ prior orders | growth ≥ 45%, z ≥ 3, 12+ orders and a positive synthetic market signal |
| UNDERSERVED_REGION | regional share < 60% of expected and (competitor availability constraint or regional index +5%) | both signals |
| HIGH_MARGIN_GROWTH | realised margin ≥ target and units +10%, z ≥ 2 | growth ≥ 20% |
| SEGMENT_GROWTH | segment/industry revenue +25%, z ≥ 2, 15+ orders | z ≥ 3 and 10+ orders |
| EXCESS_INVENTORY | above max stock or 45+ days cover | 60+ days |
| AVAILABLE_CAPACITY | 14-day utilisation < 72% | growing products and positive category signal |
| PRICING_OPPORTUNITY | competitor increases ≥ 4% in 90 d, demand not down > 15% | 2+ signals |

The quiet baseline yields zero incidents and zero risks (tested). It does yield structural opportunities (pricing, underserved region) from scripted competitor signals.

### 6.4 Persistence and provenance
Findings are keyed by `dedupe_key`. An open match is updated (max severity, fresh evidence and impact, `occurrence_count += 1`). Open incidents and risks no longer detected are resolved. Every run is a `detection_runs` row. Incidents store the `simulation_run_id` that altered the data.

---

## PART 7 — INVESTIGATION SYSTEM

### 7.1 Deterministic engine (Phase 2, authoritative)
Finding → signal collection through the tool layer (fixed tool selection per subject kind) → hypothesis generation and initial ranking (registry triggers) → evidence items from supporting and contradicting conditions → Evidence Support Score per item and per hypothesis → conclusion → impact → evidence graph → report. Lifecycle CREATED → COLLECTING_EVIDENCE → ANALYZING → COMPLETED | FAILED. Raw data is never modified; the only outside change is incident DETECTED → INVESTIGATING (tested by checksum).

Exact counts:
- Tools: **18** — 13 read (`get_machine_readings`, `get_production_metrics`, `get_maintenance_history`, `get_previous_incidents`, `get_supplier_history`, `get_inventory_status`, `get_customer_orders`, `get_market_signals`, `get_competitor_signals`, `get_financial_metrics`, `get_line_context`, `get_capacity_status`, `search_business_records`) and 5 calculation tools that wrap the impact engine. Error codes: MISSING_PARAM, INVALID_PARAM, NOT_FOUND, NOT_SEEDED, MISSING_DB, UNKNOWN_TOOL, TOOL_ERROR.
- Hypothesis specs: **28**, with **264** conditions.
- Signals: **74** static names plus per-sensor families (`<type>_change_pct`, `_level`, `_z`, `_above_warning`, `_above_critical`, `_std_ratio`).
- Evidence source types: **15** with reliability weights (production_metric, order, order_item 0.95; sensor_reading, maintenance_record, customer, inventory, inventory_snapshot, supplier_batch, financial_metric 0.9; machine_component, supplier 0.85; historical_incident 0.7; market_signal 0.55; competitor_signal 0.5).
- Investigation trace step kinds: **14** (created, restarted, started, tool, tool_failed, signals, hypotheses, evidence, scoring, conclusion, impact, report, completed, failed).
- Measured bearing-failure investigation: 56 signals from 5 tool calls, 7 hypotheses, 60 evidence items, 51 record nodes, 15 trace entries.

Conclusion rule: the top hypothesis is determined when ESS ≥ 0.5 and it leads the runner-up by ≥ 0.15, or when both are ≥ 0.8 (runner-up reported as contributing factor). Otherwise "evidence is split" or "not adequately supported".

### 7.2 Agent workflow (Phase 3)
`POST /incidents/{id}/react` (or opportunity/risk) runs:
1. **CLASSIFYING — Detection Agent.** Produces `DetectionAnalysis` (event type, severity, priority P1–P4, affected entities, investigation objective, evidence categories, missing information). Validated: subject and event type must match the persisted finding exactly. Allowed tool: `search_business_records` only.
2. **INVESTIGATING — Investigation Agent.** Bounded tool loop: at most 5 calls per turn, 4 iterations, 12 tool calls. Each call goes through `ToolExecutor`: allow-list check → limit accounting → registry `run_tool` (argument validation) → summary (count, keys, record ids) → `agent_tool_calls` row + trace entry. Denied and unknown tools are recorded as DENIED and do not consume budget. Then it runs the Phase 2 engine and produces `InvestigationAnalysis`, which may only cite existing hypothesis and evidence ids and must restate the deterministic ESS (±0.011). Allowed tools: 13 read + 5 calculation.
3. **VERIFYING — Evidence Agent.** Produces `EvidenceAssessment` (verified, sufficient, supporting/contradicting/missing evidence ids, conflicts, unsupported claims, restated ESS). Validated: ids must exist; cannot be sufficient without supporting ids; cannot rescore. Allowed tools: 13 read only, never calculation.
4. **COMPLETED — Orchestrator.** Builds `InvestigationResult` with status CONCLUDED (deterministic conclusion determined + evidence verified and sufficient + no insufficiency flag) or INSUFFICIENT_EVIDENCE; attaches `ImpactReference` copied from the engine's impact. Any failure → FAILED with the error in the run and trace; LLM-call or time limits end as FAILED with `result_status=INSUFFICIENT_EVIDENCE`.

Provider behaviour: invalid structured output is traced as `validation` and retried with the error fed back (`LLM_MAX_RETRIES`, default 1). Provider errors are traced as `retry`/`error`. After retries, if `AGENT_FALLBACK_TO_DETERMINISTIC=true`, the deterministic provider produces the output and the run is flagged `fallback_used=true`. Limits are never absorbed by fallback.

Default limits: 4 iterations, 12 tool calls, 10 LLM calls, 120 seconds, 1 retry.

---

## PART 8 — EVIDENCE / EXPLAINABILITY

How REACT avoids "the AI thinks so":
- **Record-level evidence.** Every evidence item has source type, primary id, the list of record ids behind it, timestamp, claim, value and baseline.
- **For and against.** Satisfied supporting conditions support; satisfied contradicting conditions contradict; observed-but-unmet supporting conditions contradict at 60% weight ("Condition not met: …"); unobserved supporting conditions count half against.
- **Evidence Support Score.** Per item = 0.35·relevance + 0.15·recency + 0.20·source reliability + 0.15·correlation + 0.15·consistency. Per hypothesis ESS = S / (S + C + 0.5·U), capped at 0.15 without any satisfied supporting condition. Labels ≥ 0.8 strongly supported, ≥ 0.6 supported, ≥ 0.4 weakly supported. API, models, reports and agent schemas all state it is not a probability.
- **Evidence graph** from subject to records.
- **Agent grounding checks** reject fabricated evidence ids and rescored ESS (tested by tampering: both rejected, then retried successfully).
- **Traces**: investigation trace and agent trace record every tool call and step; no chain-of-thought stored.
- **Determinism**: same data → same signals, scores and conclusion.

Measured example (bearing failure): "Bearing degradation is the best-supported root cause (ESS 0.95) because vibration rose 139% and is above the warning threshold, temperature rose 32%, power rose 20%, spindle speed fell 3.5%, LINE-C output fell 23%, the defect rate is 1.99× baseline, and the spindle bearing condition is 0.18 (CRITICAL) with 75% failure probability." Contradicting: "preventive maintenance is not overdue; no similar historical bearing failure." Alternatives: material quality 0.77, maintenance 0.62, component supply 0.21, demand overload 0.19, power instability 0.10, sensor failure 0.02. The Evidence Agent verified 9 supporting and 2 contradicting items and raised 1 conflict.

---

## PART 9 — IMPACT ENGINE (`app/impact/engine.py`)

Formulas (17 methods): production_loss, revenue_impact, inventory_risk, downtime_impact, defect_cost, machine_degradation_impact, supplier_delay_impact, late_delivery_impact, opportunity_value, pricing_opportunity_value, excess_inventory_value, revenue_loss, delivery_impact, customer_impact, inventory_impact, opportunity_revenue, inventory_requirement.

Configuration (frozen `ImpactConfig`): INR; default hourly loss 45,000; max hourly loss 400,000; defect cost multiplier 1.15; pricing capture 0.5; excess redirect 0.6; expedite premium 0.25; late-delivery penalty 0.05; churn exposure 0.10; opportunity capture 0.7. Line economics come from product prices and costs.

Why deterministic: reproducible, auditable, configurable, testable. Why the LLM must not compute money: an unreproducible figure cannot be defended; so the LLM-facing schemas have no money fields (tested), and the final result only carries an `ImpactReference` copied from the engine.

Every result stores `formula`, `inputs`, `components`, `value`, `currency`, `is_estimate=true`, `basis="SYNTHETIC"`. Reports say "Estimated impact / Potential opportunity – synthetic, formula-based estimates; not guaranteed financial outcomes."

---

## PART 10 — SYNTHETIC DATA / DEMO ENVIRONMENT (`app/simulation/catalog.py`)

- Company: Astra Manufacturing, Industrial Components Manufacturing, Bangalore, INR.
- Plant: Bangalore Manufacturing Plant, Peenya Industrial Area, South India; two shifts 06:30–22:30 IST, Sundays off.
- Lines: LINE-A Valve Assembly (1,200/day), LINE-B Precision Bearings & Gears (2,000/day), LINE-C Hydraulic Fittings & Pump Components (1,600/day).
- Machines: 12 (CNC-A1, VMC-A2, HPR-A3, ROB-A4, GRD-B1, CNC-B2, FUR-B3, HOB-B4, CNC-C1, FRG-C2, SFU-C3, PKG-C4) with 43 components.
- Sensors: 43 (temperature, vibration, power, pressure, RPM) with normal, warning and critical thresholds.
- Products: 25 in 5 categories; embedded trends (Industrial Valve X +22%, gearboxes +8–12%, pump components −7 to −10%).
- Customers: 120 B2B across 6 regions, 10 industries, 3 segments.
- Suppliers: 12 across 10 component types; Helios Controls GmbH is the sole source of electronic actuators.
- History: 120 days (60 in tests), about 4,000 orders, about 124k hourly sensor readings, daily production and financials, inventory snapshots, supplier batches, maintenance records, about 350 synthetic market signals, 12 scripted competitor signals, 6 historical incidents with leading indicators.
- Scenarios (8 + reset): bearing-failure, sensor-failure (stuck/spike/dropout), supplier-delay, demand-spike, demand-drop, inventory-shortage, production-bottleneck, power-instability.

Why synthetic: no shareable real plant data; known correlations are needed to validate detection; scenarios must be triggerable on stage. Everything is labelled synthetic in data and responses. Caveat: the baseline is byte-deterministic, but scenario outputs vary slightly per run (see 1.3 item 6).

---

## PART 11 — DEMO SCENARIOS (measured 2026-09-28, seed 42, 120 days)

Numbers may shift by about 1% between runs because of the scenario RNG caveat.

**A. Bearing failure [DEMO-VERIFIED]**
TRIGGER `POST /simulation/bearing-failure` (CNC-C1, 7 days) → DETECTION: MACHINE_DEGRADATION **CRITICAL**, PRODUCTION_DROP MEDIUM, DEMAND_SUPPLY_MISMATCH risk; detection impact INR 2.49M → AGENT RUN `POST /incidents/{id}/react`: Detection Agent P1; Investigation Agent calls 5 tools (all OK); engine scores 7 hypotheses → EVIDENCE: 60 items; Evidence Agent verified 9 supporting / 2 contradicting, 1 conflict → CONCLUSION: CONCLUDED, BEARING_DEGRADATION ESS **0.953**; SENSOR_FAILURE 0.017 → IMPACT: INR 3,903,958 total (production loss 1.33M, projected 24 h downtime 2.05M, defect cost 0.24M, 20 orders / 18 customers at risk) → next step: inspect the bearing assembly. 20 trace entries, `llm_used=false`.

**B. Demand spike [DEMO-VERIFIED]**
TRIGGER (Industrial Valve X, South India, ×3, 14 days) → DETECTION: DEMAND_OPPORTUNITY **HIGH**, about +270%, z ≈ 8.6, 11 supporting synthetic signals → AGENT RUN: 5 tools (orders, market, competitor, inventory, capacity) → CONCLUSION: CONCLUDED, SEGMENT_EXPANSION (ESS 0.89–1.00 across runs), MARKET_CATEGORY_GROWTH contributing, TEMPORARY_CUSTOMER_SPIKE 0.0 → IMPACT: +68 units/day, spare capacity 64/day (insufficient), potential revenue about INR 12.7–12.8M, margin about INR 5.0M; constraints: capacity, 0.4 days finished-goods cover, sole-sourced actuator (21-day lead time) → follow-up detection raises INVENTORY_SHORTAGE and PRODUCTION_BOTTLENECK risks.

**C. Supplier delay [DEMO-VERIFIED]**
TRIGGER (Helios Controls, +10 days) → DETECTION: SUPPLIER_DELAY CRITICAL (sole source), INVENTORY_SHORTAGE CRITICAL, DELIVERY_DELAY ×3 → AGENT RUN: 2 tools (supplier history, inventory) → CONCLUDED: SINGLE_SOURCE_EXPOSURE 1.00 with SUPPLIER_RELIABILITY_ISSUE 0.95 contributing → IMPACT: INR 2,918,208 exposure.

**D. Sensor failure [DEMO-VERIFIED]** → only SENSOR_FAULT MEDIUM, no machine incident; detection impact INR 0 → CONCLUDED: SENSOR_FAILURE 0.90, BEARING_DEGRADATION 0.0. Best used as the contrast to A: same kind of alarm, different cause.

**E. Power instability [DEMO-VERIFIED]** → 2 × ABNORMAL_POWER MEDIUM → POWER_INSTABILITY 0.86 (maintenance 0.53, sensor failure 0.49).

**F. Production bottleneck [DEMO-VERIFIED, detection]** → PRODUCTION_BOTTLENECK HIGH, DELIVERY_DELAY ×2.

**G. Demand drop [DEMO-VERIFIED]** → REVENUE_DECLINE MEDIUM, MARGIN_DECLINE HIGH ×3; AVAILABLE_CAPACITY opportunity; investigation leads with DEMAND_CONTRACTION or PRICING_PRESSURE (tested).

**H. Inventory shortage [DEMO-VERIFIED]** → INVENTORY_SHORTAGE CRITICAL for Industrial Valve X; investigation tested.

Recommended stage order: A, then D as the contrast, then B. Show the agent trace for A (20 steps) and the evidence graph.

---

## PART 12 — CURRENT IMPLEMENTATION STATUS

| Capability | Status | Evidence in code |
|---|---|---|
| Synthetic data | DONE | `app/simulation/`; `tests/test_generator.py`, `test_scenarios.py` |
| Database | DONE | 35 tables, 3 Alembic revisions |
| Incident detection | DONE | `app/detection/operational.py`; `tests/test_detection.py` |
| Risk detection | DONE | `app/detection/risk.py` |
| Opportunity detection | DONE | `app/opportunities/engine.py`; `tests/test_opportunities.py` |
| Impact engine | DONE | `app/impact/engine.py`; `tests/test_impact.py` |
| Investigation | DONE | `app/investigation/`; `tests/test_investigation*.py` |
| Evidence + ESS | DONE | `app/investigation/evidence.py` |
| Evidence graph | DONE | `app/investigation/graph.py` |
| Agentic AI (agents + orchestrator) | DONE with the deterministic provider | `app/agents/`, `app/agent_runtime/`; `tests/test_agents*.py` (23 tests) |
| LLM | PARTIAL | `BedrockProvider` tested only with a fake client; no live Bedrock run; default provider is rule-based |
| Tool calling | DONE (policy-checked, limited, persisted) | `app/agent_runtime/tool_executor.py`, `app/agents/policies.py` |
| Orchestration | DONE | `app/agents/orchestrator.py`, `state.py` |
| Decision Agent | NOT STARTED | reserved `AWAITING_DECISION` |
| Response Agent | NOT STARTED | — |
| Human approval | NOT STARTED | reserved `AWAITING_APPROVAL` |
| Action execution | NOT STARTED | reserved `EXECUTING` |
| Frontend | NOT STARTED | — |
| Authentication | NOT STARTED | — |
| AWS integration | PARTIAL | Bedrock client code and settings only |
| Deployment | PARTIAL | Dockerfile + compose, never run locally; no git, no CI |
| Tests | DONE | 89 tests, all passing 2026-09-28 |

---

## PART 13 — ROADMAP (adjusted to the actual state)

- **Phase 1** — Synthetic environment, detection, impact, API. Done 2026-09-25.
- **Phase 2** — Deterministic investigation and evidence. Done 2026-09-25.
- **Phase 3** — Agents, tool calling, orchestration. Done 2026-09-27/28 with the deterministic provider. Remaining: run and validate against real Bedrock (current model id, IAM, latency, cost), fix `system/status` and API metadata, update README counts, fix the `PRICING_HEADROOM` bug, make scenario RNG reproducible.
- **Phase 4** — Decision Agent, human approval workflow, response/action layer with an action allow-list. Not started.
- **Phase 5** — Frontend: findings list, agent trace view, evidence graph, approval queue. Not started.
- **Phase 6** — AWS deployment (Part 16). Not started beyond the Bedrock client.
- **Phase 7** — Git, CI, demo script covering agents, hardening, presentation. Not started.

---

## PART 14 — HACKATHON PROBLEM STATEMENT

**What is the problem?** Manufacturers collect sensor, production, order, stock, supplier and financial data, but plant leaders lack the time and analysts to connect it. Problems surface late; opportunities go unserved.

**Why it matters.** An unnoticed bearing stops a line for a day. A sole-source supplier slipping ten days strands orders. A regional demand surge is lost because nobody linked orders to capacity and stock. Each has a rupee cost.

**Who experiences it.** Plant managers, operations heads and owners of mid-sized manufacturers, and the finance teams who must justify decisions.

**Why dashboards and BI tools fall short.** They display; they do not detect, correlate, explain or quantify. Single-signal alerts cannot tell a sensor fault from a machine fault. BI needs an analyst to ask the right question.

**What REACT does differently.** It answers four questions from the records: what is happening (detection with evidence ids), why (ranked hypotheses with Evidence Support Scores, verified by an Evidence Agent), how much it matters (formula-based impact), and what to look at next (recommended next step). All four are implemented. Deciding and acting, with human approval, is the planned extension.

---

## PART 15 — WHY AI?

Concrete roles the agent layer is built for:
- **Framing:** turn a finding into an objective, priority and evidence plan (Detection Agent).
- **Tool selection:** choose which of the 18 tools to call and with what arguments, within limits (Investigation Agent).
- **Assessment:** band each deterministically scored hypothesis (plausible / uncertain / unlikely / insufficient) with a rationale citing evidence ids.
- **Verification:** check grounding, surface conflicts and missing evidence, and refuse sufficiency without support (Evidence Agent).
- **Natural-language explanation:** the intended contribution of a real LLM.

Honest status: with the default provider these roles are performed by deterministic rules, so today the AI layer demonstrates the **architecture and the guardrails**, not LLM reasoning. Switching `LLM_PROVIDER=bedrock` routes the same calls to Bedrock; that path is unit-tested but not yet exercised live.

What stays deterministic by design: data truth, evidence collection and ids, Evidence Support Score computation, all money, validation (schemas, id checks, ESS tolerance), limits, and the final CONCLUDED/INSUFFICIENT_EVIDENCE decision rule.

---

## PART 16 — WHY AWS?

**Current state:** no AWS service is called at runtime. The repository contains `BedrockProvider` (Bedrock Runtime Converse API via boto3, standard credential chain, `toolConfig` tool use, `toolUse` parsing, timeouts, errors that never leak provider details), `AWS_REGION` and `LLM_MODEL` settings, and boto3. It is tested with a fake client. Say: "Bedrock provider implemented and unit-tested; live integration pending."

**PROPOSED AWS ARCHITECTURE** (all [PLANNED]):
| Service | Why | Would host | Connects via |
|---|---|---|---|
| Amazon Bedrock (Converse API, Claude) | Managed LLM with tool use; matches the existing provider | Agent reasoning | `LLM_PROVIDER=bedrock` |
| Amazon RDS for PostgreSQL / Aurora PostgreSQL | Drop-in for the current schema and Alembic | All tables | `DATABASE_URL` |
| AWS Fargate (ECS) or App Runner | Runs the existing Docker image | API, engines, orchestrator | ALB |
| AWS Step Functions | Durable workflow for detect → investigate → verify → await approval (Phase 4) | Long-running and approval steps | Calls API/Lambda per state |
| Amazon EventBridge Scheduler + Lambda | Periodic detection instead of manual POST | Schedulers | API |
| Amazon S3 | Reports, evidence graphs, future document uploads | Artifacts | API |
| Amazon CloudWatch | Ingests existing JSON logs; alarms on `agent_run.failed`, `investigation.failed` | Observability | stdout |
| AWS Secrets Manager + IAM task roles | No credentials in `.env`; provider already uses the boto3 chain | Secrets | Task role |
| Amazon Bedrock AgentCore | Optional later: managed agent runtime / tool gateway for the 18-tool registry | Agent hosting | Only if it replaces the custom loop cleanly |

---

## PART 17 — DIFFERENTIATORS (supported by the code)

1. **Evidence-grounded investigation.** Every conclusion links to evidence items with record ids, for and against, and a graph down to rows.
2. **Evidence Support Score, not "confidence".** Published five-factor formula, penalties for missing observations, explicit "not a probability".
3. **Deterministic money.** One formula engine, formula and inputs stored with each number, LLM schemas structurally unable to emit money.
4. **Guardrailed agent architecture.** Per-agent tool allow-lists, hard limits, schema and id validation that rejects fabricated evidence and rescored scores, flagged fallback, persisted trace without chain-of-thought — all tested.
5. **Risks and opportunities from one dataset.** The demand-spike demo yields an opportunity and follow-on stock and bottleneck risks.
6. **Cause vs symptom.** Sensor fault, bearing wear and power instability are separated (true cause 0.90 / 0.95 / 0.86; wrong causes near zero).
7. **Reproducible demo environment with provenance** and 89 passing tests.

Not claimed: live LLM reasoning, human-in-the-loop, business-first UI, AWS-native deployment.

---

## PART 18 — JUDGE QUESTIONS

**Business**
- *Problem?* Turning fragmented plant data into what / why / how much / what next, with evidence, without an analyst.
- *User?* Plant and operations managers and owners of mid-sized manufacturers.
- *Why use it?* Earlier detection of correlated failures and demand shifts, explained in rupees with checkable references.
- *Why not Excel?* It cannot continuously correlate hourly sensors with orders, stock and suppliers, or score competing explanations.
- *Why not BI tools?* BI visualises; REACT detects, investigates, scores evidence and quantifies. It could sit beside a BI stack.
- *What if the data is wrong?* Sensor plausibility checks classify instrument faults separately; source reliability weights lower weak sources; the score drops and the result becomes "split" or INSUFFICIENT_EVIDENCE. Real-feed data quality is future work.

**Technical**
- *Why agents?* To plan tool use, assess and verify within guardrails, and later explain in language. Today they run on a rule-based provider; the architecture is what is proven.
- *Why not a normal ML model?* Synthetic data with six labelled historical incidents; rules and statistics are auditable. ML baselines can be added later.
- *Why an LLM?* Only for planning and explanation over structured evidence; never for facts or numbers.
- *How do you prevent hallucinations?* Strict schemas with `extra="forbid"`; every cited evidence or hypothesis id must exist; ESS must match the engine within 0.011; no money fields; Evidence Agent verification; tests inject fabricated ids and rescored ESS and both are rejected.
- *How do you verify evidence?* Evidence items carry record ids; tests load those readings and metrics and check they belong to the right machine and line.
- *How is money calculated?* Only in `app/impact/engine.py`, with formula and inputs stored.
- *How do agents select tools?* Through `generate_with_tools` over a per-agent allow-list; every call is policy-checked, limited, validated and persisted. With the default provider the plan is a fixed table per subject kind.
- *Failures?* Validation errors retry with the error fed back; provider errors retry; then flagged deterministic fallback or FAILED; limits end runs as INSUFFICIENT_EVIDENCE; every failure is traced.
- *Scale?* Stateless API over PostgreSQL with bounded windows and indexed readings; production scale needs scheduling, partitioning, async execution (agent runs are synchronous today) and a queue.
- *Why AWS?* Bedrock matches the provider already written; RDS, Fargate, Step Functions and CloudWatch map onto the Docker/PostgreSQL/JSON-log design. Proposed, not deployed.

**Data**
- *Source?* A deterministic generator from a catalogue.
- *Real?* No; fictional company, all signals flagged synthetic.
- *Validation?* Tests check volumes, normal bands, demand→production→revenue correlation, determinism, and a quiet baseline.
- *Incomplete data?* Unobserved conditions reduce the ESS; dropout detection flags missing readings; tools validate inputs.
- *Real data?* The schema is a standard manufacturing model; connectors, cleansing and threshold calibration are not built.

**Security**
- *Documents?* No document handling exists; planned S3 + IAM + Secrets Manager.
- *Sensitive data?* All synthetic and local today; prompts carry no secrets; tool results are summarised before persistence; no chain-of-thought stored.
- *Arbitrary actions?* No. Agents can only call the 18 read/calculate tools via allow-lists; there is no SQL, code, HTTP, filesystem or write tool; unknown tools are recorded as DENIED (tested with `drop_table` and `run_sql`). Actions are Phase 4 behind human approval.

---

## PART 19 — PPT CONTENT (10 slides)

**SLIDE 1 — REACT**
- Main message: Evidence-grounded business intelligence for manufacturers.
- Text: "What is happening. Why. How much it matters. What to check next — with the records to prove it."
- Visual: Detect → Investigate → Verify → Impact.
- Demo: none.
- Notes: Not a chatbot. Demo data is synthetic for a fictional plant.

**SLIDE 2 — The problem**
- Main message: Data exists; understanding does not.
- Text: "Sensors, production, orders, stock, suppliers, finance — six streams nobody connects. Failures found late. Opportunities missed. Dashboards show; they don't explain."
- Visual: six disconnected data icons around a plant manager.
- Demo: none.
- Notes: Bearing example: vibration climbs for a week before anyone links it to output and defects.

**SLIDE 3 — The solution**
- Main message: Four answers from the records.
- Text: "WHAT — detection with evidence ids [IMPLEMENTED] · WHY — ranked causes with Evidence Support Scores, verified by an Evidence Agent [IMPLEMENTED] · HOW MUCH — formula-based impact [IMPLEMENTED] · WHAT NEXT — recommended check [IMPLEMENTED]; decide & act with approval [PLANNED]"
- Visual: four tagged boxes.
- Demo: none.
- Notes: Keep the tags visible.

**SLIDE 4 — How it works**
- Main message: Deterministic core, guarded agents around it.
- Text: "Synthetic plant: 3 lines · 12 machines · 43 sensors · 25 products · 120 customers · 12 suppliers · 120 days · 8 scenarios → 23 finding types → 18 tools · 28 hypotheses · 264 conditions · 5-factor Evidence Support Score → impact engine → 3 agents + orchestrator"
- Visual: pipeline with numbers.
- Demo: `POST /simulation/bearing-failure`, `POST /detection/run`.
- Notes: All implemented; 89 tests pass.

**SLIDE 5 — System architecture**
- Main message: One source of truth, thin API.
- Text: "FastAPI (61 routes) · PostgreSQL (35 tables, Alembic) · Simulation · Detection · Opportunity · Impact · Investigation · Agent orchestrator · Tool registry · JSON logs · Docker"
- Visual: Part 4 diagram.
- Demo: Swagger `/docs`.
- Notes: No frontend yet; the API is the product surface.

**SLIDE 6 — Agent architecture**
- Main message: Agents plan and verify; engines own facts and money.
- Text: "Detection Agent → Investigation Agent (policy-checked tool calls) → Evidence Agent · Orchestrator: state machine, limits (4 iterations, 12 tools, 10 LLM calls, 120 s), retries, flagged fallback · Rejects fabricated evidence ids and rescored scores · No money fields [IMPLEMENTED] · Provider: rule-based today; Amazon Bedrock Converse provider built, live run pending [PARTIAL]"
- Visual: three agents around the deterministic core, arrows only into the tool registry.
- Demo: `GET /agents` (roster and allow-lists).
- Notes: Say plainly that the demo runs with `llm_used=false`.

**SLIDE 7 — Evidence you can open**
- Main message: Every conclusion walks back to rows.
- Text: "Bearing degradation — ESS 0.95. For: vibration +139% (above warning), temperature +32%, power +20%, output −23%, defects 1.99×, bearing condition 0.18. Against: maintenance on schedule; no similar history. Alternatives: material quality 0.77 · maintenance 0.62 · sensor failure 0.02."
- Visual: evidence graph.
- Demo: `/investigations/{id}/graph`, `/report`.
- Notes: ESS is support in the records, not probability.

**SLIDE 8 — Live demo: crisis, contrast, opportunity**
- Main message: Same data, different stories, correctly separated.
- Text: "Bearing failure → CRITICAL, CONCLUDED 0.95, est. impact INR 3.9M, 20-step agent trace · Sensor fault → sensor, not machine (0.90 vs 0.00), detection impact INR 0 · Demand spike → HIGH opportunity +270%, potential revenue ≈ INR 12.8M, blocked by capacity, 0.4 days stock and a sole-sourced part"
- Visual: three cards labelled "synthetic estimate".
- Demo: `POST /incidents/{id}/react`, then `/agent-runs/{id}/trace`.
- Notes: Numbers can move about 1% between runs.

**SLIDE 9 — AWS architecture (proposed)**
- Main message: Designed for AWS; Bedrock provider already written.
- Text: "[PLANNED] Bedrock (agents) · RDS/Aurora PostgreSQL · Fargate · Step Functions (workflow + approval) · EventBridge + Lambda (scheduled detection) · S3 · CloudWatch · Secrets Manager/IAM"
- Visual: dashed-border AWS diagram.
- Demo: none.
- Notes: Never say "runs on AWS".

**SLIDE 10 — Roadmap and ask**
- Main message: From evidence to approved action.
- Text: "Done: detect, investigate, verify, quantify — 89 tests. Next: live Bedrock reasoning, Decision Agent, human approval, actions, UI, real connectors, document upload. Ask: pilot plants with real data."
- Visual: Phases 1–7 bar.
- Demo: none.
- Notes: Close on the principle: REACT never asks to be trusted; it shows the records.

---

## PART 20 — PPT CLAIM SAFETY

Safe as [IMPLEMENTED] / [DEMO-VERIFIED]: synthetic environment and 8 scenarios; detection of 23 finding types with evidence ids and dedupe; impact engine with stored formulas; deterministic investigation (18 tools, 28 hypotheses, ESS, conclusion, graph, report, trace); three agents plus orchestrator with policy-checked tool calling, limits, validation, fallback and trace; REST API; 89 passing tests; measured numbers in Part 11.

Only as [PARTIAL]: "Amazon Bedrock provider implemented; live integration pending."

Only as [PLANNED]: live LLM reasoning in the demo, Step Functions, AgentCore, S3, any AWS deployment, Decision/Response agents, human approval, autonomous actions, frontend, authentication, document upload, real data connectors.

Never write: "powered by Bedrock", "runs on AWS", "LLM-driven investigation" (for the current demo), "autonomous actions", "human-in-the-loop", "analyses uploaded documents", or "probability/confidence" for the Evidence Support Score.

---

## PART 21 — REACT — MASTER PROJECT SUMMARY

1. **Name:** REACT — Real-time Event Analysis & Crisis Triage.
2. **One line:** Evidence-grounded business intelligence for manufacturers: deterministic detection, investigation and impact, wrapped by a guardrailed three-agent workflow whose LLM backend is pluggable (rule-based by default; Amazon Bedrock provider built, not yet run live).
3. **Problem:** Plant data is fragmented; owners lack analysts to connect sensors, production, orders, stock, suppliers and finance into what / why / how much / what next.
4. **Users:** Plant and operations managers and owners of mid-sized manufacturers.
5. **Solution:** FastAPI + PostgreSQL service: synthetic data → detection → deterministic investigation with Evidence Support Scores → impact engine → agent workflow that frames, explores, assesses and verifies within strict guardrails → traceable result.
6. **Journey:** Data (synthetic) → detection → agent run (classify → investigate → verify) → conclusion with evidence ids → impact reference → recommended next step. Decision, approval, action, UI, documents, real connectors: future.
7. **Features:** 61 REST routes; 8 scenarios + reset; 9 incident, 7 risk, 7 opportunity types; dedupe/resolve; 17 impact formulas; investigation with 18 tools, 28 hypotheses (264 conditions), about 74 signals, 15 source types, graph, report, trace; agent workflow with per-agent tool allow-lists, limits, retries, fallback, 3 persisted agent tables; JSON logs; Docker files; 89 tests.
8. **Architecture:** Python 3.12+ (venv 3.13), FastAPI, SQLAlchemy 2, Pydantic 2, Alembic, PostgreSQL 16/17, psycopg 3, boto3; no agent framework. Packages: `app/{api,models,schemas,services,simulation,detection,opportunities,impact,investigation,agents,agent_runtime,database,config,observability}`. Local: Homebrew PostgreSQL on localhost:5432 without password, DBs `react` and `react_test`; `.venv/bin/uvicorn app.main:app --reload`; `.venv/bin/pytest` (a few minutes). Docker not installed locally; not a git repo.
9. **Data model:** 35 tables in 3 migrations — organisation, machines/sensors/readings, production/maintenance, products/customers/orders/sales, inventory/suppliers/batches, financials, synthetic market/competitor signals, historical incidents, findings, runs/state, investigation tables, agent tables. Simulated clock in `system_state.data_end`.
10. **Detection:** sensor faults, level and z-score anomalies (24 operating hours vs 30-day baseline), correlated machine degradation, line drops/defects/downtime; seven risk rules; seven opportunity rules with a significance filter; thresholds in `DetectionConfig` / `OpportunityConfig`.
11. **Investigation:** deterministic pipeline signals → hypotheses → evidence → ESS → conclusion (ESS ≥ 0.5 and margin ≥ 0.15, or co-factor ≥ 0.8) → impact → graph → report. Agent pipeline CREATED → CLASSIFYING → INVESTIGATING → VERIFYING → COMPLETED/FAILED; result CONCLUDED / INSUFFICIENT_EVIDENCE / FAILED.
12. **Evidence:** items with record ids; per-item score 0.35/0.15/0.20/0.15/0.15; hypothesis ESS = S/(S+C+0.5·U), cap 0.15; labels 0.8/0.6/0.4; agent outputs must cite existing ids and restate ESS.
13. **Impact:** `app/impact/engine.py` only; formula, inputs, components, `is_estimate=true`, `basis="SYNTHETIC"`; agents carry only an `ImpactReference`.
14. **Agentic AI status:** Implemented and tested end to end with `DeterministicProvider` (default; `llm_used=false`). `BedrockProvider` written and unit-tested with a fake client; never run against Bedrock. `MockLLMProvider` used in tests for retries, fallback, tampering and limits.
15. **AWS status:** No AWS service used at runtime. Proposed: Bedrock, RDS/Aurora, Fargate, Step Functions, EventBridge + Lambda, S3, CloudWatch, Secrets Manager/IAM, optionally AgentCore.
16. **Demos (measured 2026-09-28):** bearing failure → CRITICAL, CONCLUDED BEARING_DEGRADATION 0.953, INR 3.90M, 20-step trace; sensor failure → SENSOR_FAILURE 0.90, bearing 0.0; demand spike → HIGH opportunity about +270%, CONCLUDED SEGMENT_EXPANSION, potential revenue about INR 12.8M with capacity/stock/sole-source constraints; supplier delay → CONCLUDED SINGLE_SOURCE_EXPOSURE 1.00, INR 2.92M; power instability → 0.86.
17. **Status:** DONE — data, DB, detection, risks, opportunities, impact, investigation, evidence, graph, agents, tool calling, orchestration, tests. PARTIAL — live LLM, AWS, deployment. NOT STARTED — decision/response agents, approval, actions, frontend, auth.
18. **Roadmap:** finish Phase 3 hardening and live Bedrock; Phase 4 decision + approval + actions; Phase 5 UI; Phase 6 AWS; Phase 7 git/CI/demo/presentation.
19. **Positioning:** "Evidence-grounded AI business intelligence for manufacturing — agents that can only say what the records support, and money only from auditable formulas."
20. **Differentiators:** record-level evidence; published Evidence Support Score; deterministic money; guardrailed agents (allow-lists, limits, id and score validation, flagged fallback, trace); risks and opportunities from one dataset; cause-vs-symptom separation; reproducible environment with 89 tests.
21. **Judge questions:** Part 18.
22. **PPT structure:** Title · Problem · Solution · How it works · Architecture · Agent architecture · Evidence · Live demo · Proposed AWS · Roadmap/ask.

Owner rules: build only the named phase; money only from `app/impact/engine.py`; say "Evidence Support Score", never probability or confidence; every insight carries record ids; determinism where possible; synthetic stays labelled synthetic; routers stay thin.

Known issues to fix before the demo: `system/status` says phase 1 and agents disabled; API title says Phase 1; README counts (7 scenarios, 28 tables, 11 risk hypotheses); `PRICING_HEADROOM` missing `requires_any`; scenario RNG includes the run UUID; no git.

---

## FACTS I VERIFIED DIRECTLY FROM THE REPOSITORY

| Fact | Location |
|---|---|
| 89 tests, all passing (2026-09-28) | `tests/` (10 files), pytest output |
| 61 routes: system 2, entities 11, insights 6, simulation 8 + reset + 2 detection, investigations 18, agents 13 | `app/api/*.py`, `app/simulation/scenarios/__init__.py` |
| 35 tables, 3 migrations (`387ed9242674` → `4fd6fbe3c4b1` → `561ae240f5d0`) | `app/models/`, `alembic/versions/` |
| Catalogue counts (3 lines, 12 machines, 43 components, 43 sensors, 25 products, 12 suppliers, 10 component types, 6 regions, 10 industries, 3 segments, 120 customers) | `app/simulation/catalog.py`, `generator.py` |
| 6 historical incidents | `app/simulation/history.py` |
| Scenario RNG seeded with params including `_run_id` | `app/simulation/scenarios/base.py` line 86, `app/simulation/engine.py` |
| Detection thresholds | `app/detection/config.py`, `operational.py`, `risk.py` |
| Opportunity thresholds and z formula | `app/opportunities/config.py`, `engine.py` `_growth_z` |
| 17 impact methods and constants | `app/impact/engine.py` |
| 18 tools and error codes | `app/investigation/tools.py` |
| 28 hypotheses, 264 conditions; `PRICING_HEADROOM` `requires_any` commented out | `app/investigation/registry.py` |
| ESS weights, penalties, cap, labels, 15 source reliabilities | `app/investigation/evidence.py` |
| Conclusion thresholds 0.5 / 0.15 / 0.8 | `app/investigation/analysis.py` |
| Agent state machine | `app/agents/state.py` |
| Tool allow-lists (detection 1, investigation 18, evidence 13, orchestrator 0) | `app/agents/policies.py` |
| Limits 4 / 12 / 10 / 120 s / 1 retry | `app/agent_runtime/limits.py`, `app/config/settings.py` |
| Id and ESS validation (±0.011), sufficiency rule | `app/agents/investigation.py`, `app/agents/evidence.py` |
| CONCLUDED rule and `ImpactReference` | `app/agents/orchestrator.py` `_final_result` |
| Default provider deterministic; Bedrock via `bedrock-runtime` `converse` | `app/agents/provider.py`, `app/agents/fallback.py`, `settings.py` |
| Bedrock only tested with a fake client | `tests/test_agents.py::test_bedrock_provider_parsing_and_errors` |
| Fabricated ids and rescored ESS rejected | `tests/test_agents.py::test_unknown_evidence_ids_and_rescored_ess_are_rejected` |
| Unknown tools (`drop_table`, `run_sql`) recorded as DENIED | `tests/test_agents.py` tool tests |
| No money fields in LLM-facing schemas | `app/agents/schemas.py`, end-to-end test |
| `phase: "1"`, `llm_agents_enabled: False` | `app/schemas/system.py`, `tests/test_api.py` |
| Measured runs A–D with `llm_used=false` and results above | executed on local `react` DB 2026-09-28, then reset |
