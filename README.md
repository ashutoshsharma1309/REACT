# REACT Merchant

**Decision intelligence for small merchants. REACT finds what is going wrong or right in a shop's own records, explains why with evidence, prices it in rupees, recommends one action and waits for the owner to approve it.**

Built for the PS21 FinTech & Smart Commerce hackathon. The demo business is **Astra Retail**, a fictional consumer-electronics and mobile-accessories shop in Jayanagar, Bengaluru: 300 products in 10 categories, 8 suppliers, up to 3 stores and 120 days of trading. **All of it is synthetic** and labelled that way everywhere (`is_synthetic=true`, `source="SYNTHETIC"`, `basis="SYNTHETIC"`).

REACT is **not a chatbot**. There is no prompt box. The core loop is:

```
DETECT → INVESTIGATE → QUANTIFY (₹) → RECOMMEND → HUMAN APPROVAL → SIMULATED ACTION → AUDIT / EVIDENCE
```

| Step | What happens | Where |
|---|---|---|
| Detect | 15 statistical rules scan sales, stock, purchase orders, deliveries, prices and discounts | `app/domains/merchant/detection.py` |
| Investigate | 28 hypotheses tested against the records; each condition becomes an evidence item with source record IDs and an **Evidence Support Score** | `app/investigation/`, `app/domains/merchant/registry.py`, `signals.py` |
| Quantify | Lost sales, margin leakage, cash locked in stock, opportunity value: formulas only, never an LLM | `app/impact/engine.py`, `app/domains/merchant/impact.py` |
| Recommend | One action from a 14-action merchant catalogue, with parameters worked out deterministically (e.g. a reorder of 94 units, and how) | `app/domains/merchant/actions.py`, `app/decisions/` |
| Approve | The owner approves or rejects with a reason; nothing happens without approval | `POST /decisions/{id}/approve` and `/reject` |
| Simulated action | A `SIM-PO-…`, `SIM-PRICE-…` or similar record. No order, price change, message or payment leaves REACT; business records are unchanged | `app/domains/merchant/actions.py` |
| Audit | A JSON evidence pack with a SHA-256 hash (decision, evidence, figures, visible trace, provenance) | `app/storage/`, `GET /decisions/{id}/artifacts` |

## The five demo scenarios

Each scenario rebuilds the simulated business with one injected event, then runs the real detection. The same scenario always gives the same numbers.

| Scenario | Injected event | REACT finds | Recommends |
|---|---|---|---|
| Stockout crisis | A best-selling speaker's demand rises 1.8× while its supplier stops delivering on time | "Wireless Speaker X may run out within a day"; the lost sales are priced | **Reorder now**, with the quantity, cost and delivery date |
| Demand opportunity | One category sells 1.55× faster in the store's own zone | "&lt;Category&gt; is selling N% faster among customers in &lt;zone&gt;", e.g. wireless audio in South Bengaluru | **Stock up** |
| Supplier delay | A regular supplier delivers 6–9 days late with 15% on time | Overdue orders and the products that depend on them | **Review the supplier** |
| Margin leakage | The supplier raises its cost 7% and the shelf price never changes | "&lt;Product&gt; earns less on every sale: ₹X down to ₹Y", priced over 90 days | **Review the price**, with the price that restores the margin |
| Excess inventory | A bulk lot bought months ago while demand fell to a third | Cash locked in slow stock | **Run a promotion** |

## Non-negotiable rules (enforced in code and tests)

* Every insight traces to stored business records; evidence items carry their record IDs.
* **Money is never calculated by an LLM.** All monetary figures come from the deterministic Impact Engine.
* LLM output never invents evidence. Agents may cite only evidence and hypothesis IDs that the deterministic engine produced, and REACT never trusts LLM-generated IDs.
* The score is the **Evidence Support Score** (Strongly supported ≥ 0.8, Supported ≥ 0.6, Weakly supported ≥ 0.4, otherwise Insufficient support). It is never a probability.
* Human approval is mandatory, and actions are simulated.
* No chain-of-thought is stored. Agents have no SQL, shell, filesystem or open network access, and tool inputs and agent outputs are validated.
* Business logic lives in services and engines, never in FastAPI routers.
* **Deployment status:** the AWS infrastructure is defined and tested in code but **not deployed**, and Amazon Bedrock has **never been called live**. The default reasoning provider is deterministic and rule-based.

## Run it locally

Requirements: Python 3.12+, PostgreSQL 16+, Node 20+.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
createdb react
DATABASE_URL=postgresql+psycopg://localhost:5432/react .venv/bin/alembic upgrade head
DATABASE_URL=postgresql+psycopg://localhost:5432/react .venv/bin/python -m scripts.seed      # Astra Retail, ~3 s
DATABASE_URL=postgresql+psycopg://localhost:5432/react .venv/bin/uvicorn app.main:app --port 8000
cd frontend && npm install && npm run dev                                                      # http://localhost:5180
```

Then click **Demo scenarios** in the top bar, pick **Stockout crisis**, and choose **Open the main finding**. `scripts/demo.sh [scenario]` runs the same loop against the API from the shell. The full walkthrough is in [docs/hackathon-demo.md](docs/hackathon-demo.md).

`docker compose up --build` also works: it migrates, seeds when the database is empty and serves the API on :8000.

Key settings: `DATABASE_URL`, `REACT_DOMAIN` (`merchant` by default; `manufacturing` seeds the backend reference pack), `SEED`, `HISTORY_DAYS` (120), `SEED_ON_STARTUP`, `LLM_PROVIDER` (`deterministic` | `bedrock` | `mock`), `LLM_MODEL` (no default), `STORAGE_BACKEND` (`local` | `s3`), `DEMO_CONTROLS_ENABLED`. The full table is in [docs/aws-architecture.md](docs/aws-architecture.md#5-environment-variables). Credentials are never stored in the repo.

## The product

| Page | Answers |
|---|---|
| Dashboard | How is the shop doing? (revenue, gross margin, inventory health, open issues, ₹ at stake); what needs attention; the "What is at stake" ledger; what REACT has found for you |
| Findings / Opportunities | Every finding in plain language with severity, place, ₹ estimate and stage; filters for stock, suppliers, and margin and sales |
| Case page | What happened, why (key signals and a **Why?** dialog), what the evidence says, what does not fit, what it could cost or be worth (with *View calculation*), REACT's conclusion, the recommendation with what REACT worked out, and **Your decision** |
| Products / Inventory / Suppliers | Sales, margin, demand trend, stock cover, overdue orders and supplier reliability, each linked to its findings |
| Decisions / Activity | What is waiting for you, what you approved or rejected, and every scan, investigation and simulated action |
| Data & System | The data source (simulated business), the reasoning provider, readiness and the AI-failure drill |
| Set up a demo business | Generate a different synthetic shop (name, city, locality, 1–3 stores, at least 3 categories) |

## API

The API is unchanged where it already worked and extended where it was needed. There are no duplicate endpoints.

| Endpoint | Purpose |
|---|---|
| `GET /overview` | Dashboard: status, attention, opportunities, impact totals, business health, value metrics |
| `GET /findings`, `GET /findings/{kind}/{id}` | Unified risks, incidents and opportunities with their workflow stage |
| `POST /scan`, `GET /activity` | Run detection on the current data; the activity feed |
| `GET /demo/scenarios`, `POST /demo/run`, `POST /demo/ai-outage` | Deterministic scenarios and the AI-failure drill |
| `GET /merchant`, `GET /merchant/templates`, `POST /merchant/setup` | Merchant profile, business templates, onboarding |
| `GET /products`, `/inventory`, `/suppliers`, `/customers` | Existing endpoints; merchant datasets add sales, cover, value, reliability and findings |
| `GET /sales`, `GET /business-baseline`, `GET /metrics/business`, `GET /metrics/value` | Sales and margin breakdowns, what is normal for this shop, period metrics, what REACT surfaced |
| `POST /react/run {risk_id \| opportunity_id \| incident_id}` | Detection agent → investigation → evidence verification → decision (`AWAITING_APPROVAL`) |
| `POST /decisions/{id}/approve`, `/reject`; `GET /decisions/{id}/artifacts` | Human decision, simulated execution, evidence pack |
| `GET /investigations/{id}[/evidence\|/graph\|/report]`, `GET /agent-runs/{id}[/trace]` | Full traceability |
| `GET /health`, `GET /ready` | Liveness; readiness (database, migrations, data, storage, AI) without secrets |

OpenAPI: <http://localhost:8000/docs>.

## Architecture

```
React UI (Vite, TS) ──/api──► FastAPI (thin routers)
                                 │
                 services ───────┼──────── domain packs: app/domains/merchant (default), manufacturing (reference)
                                 │
   detection ─► investigation (signals, 28 hypotheses, evidence, ESS) ─► impact engine (₹ formulas)
                                 │
   agents (detection / investigation / evidence / decision; deterministic or Bedrock provider, allow-listed tools)
                                 │
   decision engine (validation, approval state machine) ─► simulated action executor ─► evidence pack (local / S3)
                                 │
                            PostgreSQL (Alembic)
```

The active domain is a property of the dataset (`system_state.domain`), so the same engines serve both packs. Details are in [architecture.md](architecture.md).

| Doc | Contents |
|---|---|
| [docs/data-model.md](docs/data-model.md) | Tables, merchant additions, synthetic labelling, migrations |
| [docs/simulation.md](docs/simulation.md) | The deterministic merchant generator and the scenario events |
| [docs/detection.md](docs/detection.md) | The 15 rules, thresholds and statistics |
| [docs/investigation.md](docs/investigation.md) | Signals, hypotheses, evidence, the Evidence Support Score, impact, tools |
| [docs/agents.md](docs/agents.md) | Agents, providers, tool policies, limits, fallback |
| [docs/decisions.md](docs/decisions.md) | The action catalogue, validation, approval and simulated actions |
| [docs/frontend.md](docs/frontend.md) | The UI, its design and its tests |
| [docs/aws-architecture.md](docs/aws-architecture.md) | AWS design in CDK (not deployed) |
| [docs/hackathon-demo.md](docs/hackathon-demo.md), [docs/hackathon-presentation.md](docs/hackathon-presentation.md) | Demo script and presentation narrative |
| [docs/merchant-migration-plan.md](docs/merchant-migration-plan.md) | How the manufacturing build became REACT Merchant |

## Tests

```bash
.venv/bin/pytest                         # backend: unit + integration on two session databases (<db>_test, <db>_test_merchant)
.venv/bin/pytest tests/merchant          # merchant data, detection, workflow, API and tool tests
cd frontend && npm test                  # Vitest: components and pages against captured API fixtures
cd frontend && npm run e2e               # Playwright: 8 flows on an isolated react_e2e database
cd infra && npm test                     # CDK assertion tests
REACT_AWS_LIVE=1 LLM_MODEL=<id> ARTIFACTS_BUCKET=<bucket> .venv/bin/pytest tests/aws_live -m aws_live   # opt-in, real AWS
```

| Suite | Result on 2026-10-03 |
|---|---|
| Backend (pytest) | 195 passed, 2 skipped (AWS live, opt-in) |
| Frontend unit and component (Vitest) | 42 passed |
| End to end (Playwright) | 8 passed: the five scenarios, rejection, AI failure and fallback, mobile |
| Infrastructure (CDK) | 12 passed |

Frontend fixtures are real API responses captured by `scripts/capture_ui_fixtures.py`. The UI contains no hard-coded business numbers.

## Not built

These are out of scope on purpose: payments, UPI, GST, POS integration, CRM, authentication (the approver is a typed name), WhatsApp or email automation, uploads of the merchant's own files (shown as *coming soon*), microservices, Redis, Kafka, WebSockets, SQS, Step Functions and agent frameworks.
