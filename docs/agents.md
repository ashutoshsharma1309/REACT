# Agents

REACT's agents wrap the deterministic engines with controlled tool calling, an orchestrator and a persistent trace. They reason about and explain **already-computed** results. They are never the source of business truth: findings come from detection, evidence and the Evidence Support Score from the investigation engine, money from the Impact Engine, and actions from a fixed catalogue.

The default provider is **deterministic** (rule-based, offline). An Amazon Bedrock provider exists behind the same interface; **it has never been invoked against a live AWS account.**

```
FINDING (stored risk / opportunity / incident)
  ↓ Detection Agent        → DetectionAnalysis (event type, entities, severity, objective, evidence categories, missing info)
  ↓ Investigation Agent    → bounded tool-calling loop over the domain's registered tools (ToolExecutor: policy, limits, validation, trace)
  ↓ Investigation engine   → signals, hypotheses, evidence items, Evidence Support Scores, conclusion, impact, report   (authoritative)
  ↓ Investigation Agent    → InvestigationAnalysis (assessment per hypothesis; may only cite IDs the engine produced)
  ↓ Evidence Agent         → EvidenceAssessment (supporting / contradicting / missing IDs, conflicts, unsupported claims, sufficient?)
  ↓ Orchestrator           → InvestigationResult (CONCLUDED | INSUFFICIENT_EVIDENCE | FAILED) + impact reference
  ↓ Decision Agent         → one catalogue action, validated by DecisionEngine ([decisions.md](decisions.md))
```

## Agents, responsibilities and tools (merchant)

The tool registry and the allow-lists are per domain (`TOOLS_BY_DOMAIN`, `POLICIES_BY_DOMAIN` in `app/agents/policies.py`). The active domain comes from the dataset (`current_domain(db)`), so a merchant run can only see merchant tools.

| Agent | Responsibility | Allowed merchant tools |
|---|---|---|
| `detection_agent` | Interpret an already-detected finding. It never creates findings. | `search_business_records` |
| `investigation_agent` | Select and call tools with structured arguments (bounded loop), then assess the scored hypotheses using only their evidence IDs | 16 read tools + 5 calculation tools (the calculation tools wrap the Impact Engine) |
| `evidence_agent` | Verify grounding and sufficiency, and restate the deterministic Evidence Support Score | 16 read tools (never calculation tools) |
| `decision_agent` | Convert a verified investigation into one recommendation from the merchant action catalogue | 16 read tools |
| `orchestrator` | State machine, limits, retries, fallback, trace persistence, final result | none |

Merchant read tools: `get_product_sales`, `get_inventory_status`, `get_sales_history`, `get_supplier_history`, `get_purchase_orders`, `get_customer_orders`, `get_customer_segments`, `get_pricing_history`, `get_discount_history`, `get_financial_metrics`, `get_expense_history`, `get_category_performance`, `get_regional_performance`, `get_business_baseline`, `get_competitor_prices`, `search_business_records`. Calculation tools: `calculate_stockout_loss`, `calculate_margin_loss`, `calculate_inventory_capital`, `calculate_demand_opportunity`, `calculate_reorder_quantity`. Every tool validates its arguments, reads through the shared query layer (`app/domains/merchant/queries.py`) and returns record IDs. No tool writes data, runs SQL, opens files or makes network calls.

`GET /agents` returns the roster, the tool policies for the active domain, the state machine and the provider configuration (no secrets).

Prompts are domain-localised (`localize()` in `app/agents/prompts.py`): merchant runs describe a retail business, and the finding context carries the product, store, category, region and supplier. Only the fields needed for the task are sent, never whole tables.

## Provider abstraction (`app/agents/provider.py`)

`LLMProvider` exposes `generate()`, `generate_structured(schema)` and `generate_with_tools(tools)`. Implementations:

| Provider | `LLM_PROVIDER` | Notes |
|---|---|---|
| `DeterministicProvider` (`app/agents/fallback.py`) | `deterministic` (default) | Rule-based, offline, derives every output from the structured context (finding, catalogue, deterministic hypotheses and evidence; for merchant the tool plan comes from `app/domains/merchant/planner.py`). Also the fallback when an LLM fails. Runs report `llm_used=false`. |
| `BedrockProvider` | `bedrock` | Amazon Bedrock `converse` API through boto3 with `toolConfig` for tool use and schema-instructed JSON for structured output. Credentials come from the standard AWS chain (env, profile, role). Configure `LLM_MODEL`, `AWS_REGION`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`. |
| `MockLLMProvider` | `mock` | Scripted responses for tests (dicts, models, `LLMResponse`, exceptions or callables). |

Structured outputs are validated with Pydantic (`extra="forbid"`); invalid outputs raise `StructuredOutputError`, are traced as `validation` events and retried with the error fed back (`LLM_MAX_RETRIES`). Provider errors are traced as `retry` / `error`. After the retries, if `AGENT_FALLBACK_TO_DETERMINISTIC=true`, the deterministic provider produces the output and the run is flagged `fallback_used=true` (trace event `fallback`); otherwise the run fails.

## Structured outputs (`app/agents/schemas.py`)

`DetectionAnalysis`, `ToolCallRequest`, `ToolCallResult`, `InvestigationPlan`, `HypothesisAssessment`, `InvestigationAnalysis`, `EvidenceAssessment`, `EvidenceConflict`, `ImpactReference`, `InvestigationResult`. None of the LLM-facing schemas contains a monetary field. Validators enforce grounding:

* detection output must copy `subject_kind`, `subject_id` and the detected `event_type`;
* investigation and evidence outputs may only reference hypothesis ids and evidence ids that exist for the investigation, and must restate the deterministic Evidence Support Score (a rescored value is rejected);
* evidence cannot be marked sufficient without supporting evidence ids.

## Tool calling (`app/agent_runtime/tool_executor.py`)

Every tool call: policy check (`app/agents/policies.py`) → limit accounting → the domain registry `resolve_tool` / `run_tool` (argument validation inside the tool) → result summary (count, keys, record ids; never the full payload) → `agent_tool_calls` row + `tool_call` trace entry. Denied or unknown tools are recorded with `status=DENIED, permitted=false` and do not consume the tool budget. Invalid arguments are recorded as `INVALID_ARGUMENTS`. There is no SQL, code execution, HTTP or filesystem tool.

## Limits (`app/agent_runtime/limits.py`)

`AGENT_MAX_ITERATIONS` (tool-selection turns), `AGENT_MAX_TOOL_CALLS`, `AGENT_MAX_LLM_CALLS`, `AGENT_MAX_EXECUTION_SECONDS`, `LLM_MAX_RETRIES`. Hitting the iteration or tool-call limit stops the tool loop (trace event `limit`) and the deterministic engine still runs. Hitting the LLM-call or time limit before a conclusion ends the run as FAILED with `result_status=INSUFFICIENT_EVIDENCE`.

## State machine (`app/agents/state.py`)

`CREATED → CLASSIFYING → INVESTIGATING → VERIFYING → COMPLETED`, and any state `→ FAILED`. Transitions are asserted and traced. Decisions, approvals and actions have their own state machines ([decisions.md](decisions.md)).

## Result semantics

`CONCLUDED` requires: the deterministic conclusion is determined (Evidence Support Score ≥ 0.5 with margin, or two strongly supported hypotheses with a contributing factor), the Evidence Agent reports `verified` and `sufficient`, and the Investigation Agent did not flag `insufficient_evidence`. Otherwise the result is `INSUFFICIENT_EVIDENCE` and still names the leading hypothesis, the missing evidence and the conflicts. `impact_reference` copies `estimated_total`, `currency`, `basis` and per-component values from the Phase 2 investigation impact (computed only by `app/impact/engine.py`).

## Agent trace (`agent_trace_entries`)

Fields: `trace_id, agent_run_id, sequence, timestamp, agent_name, event_type, state, tool_name, tool_arguments, tool_call_id, evidence_ids, hypothesis_ids, summary, details, duration_ms, status, error`. Event types: `state_transition, llm_call, tool_call, validation, retry, fallback, limit, investigation_engine, result, error`. Summaries are concise structured statements; hidden chain-of-thought is never stored (nor requested from the model).

Example: Stockout crisis, deterministic provider (from `frontend/src/test/fixtures/agent_run.json`, captured from the API):

```
 1 orchestrator         state_transition     Agent run created for risk … (provider deterministic)
 2 orchestrator         state_transition     CREATED -> CLASSIFYING
 3 detection_agent      llm_call             detection_analysis: valid DetectionAnalysis from deterministic
 4 detection_agent      result               Classified STOCKOUT_RISK (CRITICAL, P1); objective: Find out why 'Wireless Speaker X may run out within a day' is happening …
 5 orchestrator         state_transition     CLASSIFYING -> INVESTIGATING
 6 investigation_agent  llm_call             tool_selection: 4 tool call(s) requested (get_product_sales, get_inventory_status, get_supplier_history, get_purchase_orders)
 7 investigation_agent  tool_call            get_product_sales -> OK (57 records)
 8 investigation_agent  tool_call            get_inventory_status -> OK (1 records)
 9 investigation_agent  tool_call            get_supplier_history -> OK (21 records)
10 investigation_agent  tool_call            get_purchase_orders -> OK (1 records)
11 investigation_agent  llm_call             tool_selection: 0 tool call(s) requested
12 investigation_agent  investigation_engine Deterministic investigation … completed: 5 hypotheses, conclusion 'SUPPLIER_DELAY_SHORTAGE' (ESS 1.0)
13 investigation_agent  llm_call             investigation_analysis: valid InvestigationAnalysis from deterministic
14 investigation_agent  result               Assessment: leading SUPPLIER_DELAY_SHORTAGE (plausible); insufficient_evidence=False
15 orchestrator         state_transition     INVESTIGATING -> VERIFYING
16 evidence_agent       llm_call             evidence_assessment: valid EvidenceAssessment from deterministic
17 evidence_agent       result               Verification: verified=True sufficient=True; 5 supporting / 0 contradicting; 0 conflict(s); 0 missing
18 orchestrator         state_transition     VERIFYING -> COMPLETED
19 orchestrator         result               CONCLUDED: The available evidence strongly supports late deliveries left the shelf short as the risk driver (Evidence Support Score 1.00) …
20 decision_agent       llm_call             decision_recommendation: valid DecisionRecommendation from deterministic
21 decision_agent       result               Recommendation: REORDER_PRODUCT (READY, P1) for product …
22 decision_agent       decision             Decision … created: READY, REORDER_PRODUCT for Wireless Speaker X
23 decision_engine      approval_requested   Human approval requested for REORDER_PRODUCT (approval …); action … PROPOSED
```

## Database

`agent_runs` (subject, investigation_id, status, provider, model, llm_used, fallback_used, counters, token usage, limits, detection_analysis, investigation_analysis, evidence_assessment, result, result_status, error), `agent_tool_calls`, `agent_trace_entries`. Alembic revision `561ae240f5d0`.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/agents` | roster, policies, state machine, provider config |
| POST | `/agent-runs` `{incident_id | opportunity_id | risk_id, run: true}` | create (and run) a workflow |
| GET | `/agent-runs`, `/agent-runs/{id}`, `/agent-runs/{id}/trace`, `/agent-runs/{id}/tool-calls`, `/agent-runs/{id}/result` | inspect |
| POST | `/agent-runs/{id}/run` | execute a CREATED run (409 otherwise) |
| POST | `/incidents/{id}/react`, `/opportunities/{id}/react`, `/risks/{id}/react` | one-call workflow |
| GET | `/incidents/{id}/agent-run`, `/opportunities/{id}/agent-run` | latest run for a subject |

## AI failure handling

* With the AI unavailable and `AGENT_FALLBACK_TO_DETERMINISTIC=false`, `POST /react/run` returns 503 "AI investigation temporarily unavailable". There is no conclusion and no recommendation, and nothing changes.
* With the AI unavailable and fallback allowed, the deterministic provider produces the same evidence-grounded result. The run records `fallback_used=true` and the UI labels it.
* The drill (`POST /demo/ai-outage {"mode": "off"|"fallback"|"fail"}`, or *Data & System* in the UI) swaps in an unavailable provider. It never calls AWS. Playwright covers both paths.

## Running with Bedrock (optional, unverified live)

```bash
# AWS credentials from the standard chain only; never in files committed to the repo
LLM_PROVIDER=bedrock LLM_MODEL=<bedrock model id> AWS_REGION=<region> .venv/bin/uvicorn app.main:app
```

The adapter is unit-tested with botocore's Stubber. `tests/aws_live` holds opt-in live tests (`REACT_AWS_LIVE=1`); they have not been run.

## Safety boundaries (implemented)

* Agents call only registered tools of the active domain through the executor. They have no SQL, shell, filesystem or open network access.
* LLM outputs are schema-validated. Unknown evidence or hypothesis IDs, rescored Evidence Support Scores, non-catalogue actions and non-candidate targets are rejected.
* Money is computed only by `app/impact/engine.py`, and agent schemas have no monetary fields.
* Hard limits apply to iterations, tool calls, LLM calls, execution time and retries, plus an hourly LLM call budget.
* An agent run never modifies business records. Actions run only after human approval, and they are simulated.
* No chain-of-thought is stored, and no secrets appear in prompts, logs or API responses.
