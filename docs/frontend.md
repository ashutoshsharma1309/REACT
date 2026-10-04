# Frontend: the REACT Merchant UI

A decision-support interface for the owner of a small shop. It answers four questions: **what is happening, why, how much money it means, and what to do next**. Technical detail stays one click away for analysts and judges.

## Run it

```bash
.venv/bin/uvicorn app.main:app --port 8000                 # backend (terminal 1)
cd frontend && npm install && npm run dev                  # UI (terminal 2): http://localhost:5180
```

The UI calls the API through `/api`, which Vite proxies to `REACT_API_TARGET` (default `http://127.0.0.1:8000`). No backend URL or secret is compiled into the bundle.

## Stack

Vite, React 19, TypeScript, React Router 7, and plain CSS with design tokens (`src/styles/app.css`). It uses no UI framework. Fonts are bundled through `@fontsource` (no CDN), so the UI works offline at a venue. Tests use Vitest with Testing Library, and Playwright.

## Design: the "Khata ledger"

The design draws on the Indian shopkeeper's ledger (khata). Money is a ledger entry, and "What is at stake" reads like a page from the books.

| Token | Value | Use |
|---|---|---|
| Paper | `#f2f4f1` | Page background |
| Ink | `#13243a` | Text and headings |
| Teal | `#0d6b66` | Accent, primary actions, navigation |
| Margin red | `#c0392b` | The ledger's double margin rule; money at risk |
| Gain green | `#1c7a49` | Potential value |

Type: **Bricolage Grotesque** for display, **Public Sans** for body text and **IBM Plex Mono** for money (`.money`, tabular figures). The signature element is the ledger panel: a red double margin rule, "At risk / Could gain" columns and a double-ruled total. It appears on the dashboard ("What is at stake") and on the case page ("What it could cost / be worth"). A dashed **Simulated business** stamp sits in the top bar on every page.

## Screens

| Route | Answers | Backend |
|---|---|---|
| `/` Dashboard | How is the shop doing (revenue, gross margin, inventory health, open issues, ₹ at stake)? What needs attention? What is at stake? What has REACT found for me? | `GET /overview` |
| `/findings` | Every finding, most serious first, with filters (critical and high, stock, suppliers, margin and sales, waiting for approval, handled). `/risks` redirects here | `GET /findings?kind=risk_all` |
| `/opportunities` | What the opportunity is, why REACT flagged it, its potential value, what could be done | `GET /findings?kind=opportunity` |
| `/cases/:kind/:id` | What happened; why (key signal tiles and the **Why?** dialog); what the evidence says; what does not fit; what it could cost or be worth (ledger, *View calculation*); REACT's conclusion; **What should I do?** with what REACT worked out; **Your decision**; the simulated result | `GET /findings/{kind}/{id}`, `/decisions/{id}`, `/investigations/{id}`, `POST /react/run`, `/decisions/{id}/approve` or `/reject` |
| `?tab=evidence` | Evidence chain and table: explanation selector, supports or against, plain sentence, source, observed, normal, time | `GET /investigations/{id}/evidence`, `/graph` |
| `?tab=technical` | Evidence Support Scores, hypothesis ranking, decision readiness, cited IDs, validation checks, agent trace, tool calls | `GET /agent-runs/{id}` |
| `/products` | Sales (28 days), price, margin at price, demand trend, stock and cover, linked findings; search, category filter, sort | `GET /products` |
| `/inventory` | Status (out of stock, low, overstocked, healthy), on hand, on order and overdue, sells per day, lasts, value at cost | `GET /inventory` |
| `/suppliers` | Late of the last 6 deliveries, on-time rate vs normal, lead time vs quoted, open and overdue orders, sales they supply | `GET /suppliers` |
| `/decisions`, `/activity` | Waiting for approval, approved, rejected, not enough evidence; every scenario, scan, investigation, approval and simulated action | `GET /decisions`, `GET /activity` |
| `/data` Data & System | Data source (simulated business, data window, stores, counts), reasoning provider, system readiness, AI-failure drill, upload *coming soon* | `GET /merchant`, `/system/status`, `/agents`, `/ready`, `POST /demo/ai-outage` |
| `/setup` | Set up a demo business: name, type, city, locality, state, 1–3 stores, at least 3 categories | `GET /merchant`, `/merchant/templates`, `POST /merchant/setup` |

The top bar has **Scan business** (`POST /scan`) and **Demo scenarios** (`GET /demo/scenarios`, `POST /demo/run`). After a scenario loads, **Open the main finding** goes straight to its case.

## Product rules the UI follows

* **No fake data.** Every number comes from an API response, and the UI has no hard-coded business figures. Titles, descriptions, evidence claims and key-signal values are written by the backend from the records. `src/lib/language.ts` only maps type codes to labels (`STOCKOUT_RISK` → "Stockout risk", `REORDER_PRODUCT` → "Reorder now") and phrases unmet conditions as "Not seen: …".
* **The backend is authoritative.** Approve and reject only send requests; the UI shows what the server returns, including 409 refusals.
* **Simulated means simulated.** The action result says "This was a simulated action" and that no purchase order, price change, message or payment was sent to any real system.
* **Owner language first.** The Evidence Support Score appears as "Strongly supported", "Supported", "Weakly supported" or "Insufficient support". The number, IDs and trace live in *Technical details*. It is never called a probability.
* **Impact honesty.** Totals come from the Impact Engine. The ledger marks which lines make up the total and which are context only (such as the cost of the suggested reorder), and notes that estimates can overlap across findings.
* **Not built means labelled.** Upload is *Coming soon* with a disabled control.
* **AI failures are visible.** A 503 from `/react/run` shows "AI investigation temporarily unavailable" with no conclusion. A decision with `fallback_used` carries a notice that rule-based analysis was used.
* **Audit record.** After approval or rejection, the evidence pack appears with its SHA-256 and a download link, or a plain statement that the audit copy could not be saved.
* **Accessible and responsive.** Severity is shown as text plus an icon, never colour alone. The UI has semantic headings (the likely cause is an `h3`), labelled dialogs that close on Escape, a skip link, visible focus and live status regions. Tables scroll inside their card, and the 390 px layout has no sideways scrolling (checked in Playwright).

## Structure

```
frontend/src/
├── lib/            api/ (client, endpoints), types.ts (mirror backend schemas), useApi.ts, format.ts (₹ lakh/crore), language.ts
├── components/
│   ├── common/         Layout, Badges (incl. the Simulated business stamp), States, Dialog, DemoDialog, FindingChips, Icon
│   ├── command-center/ StatusBanner, BusinessHealth, StakeLedger, ValueStrip
│   ├── findings/       FindingCard
│   ├── case/           Stepper, CaseOverview, WhySignals, WhyDialog, ImpactPanel, TechnicalDetails
│   ├── evidence/       EvidenceChain, EvidenceTable
│   ├── decisions/      RecommendationPanel, ActionParameters, ActionResult, EvidencePack
│   └── activity/       ActivityFeed
├── pages/          CommandCenterPage, FindingsPages, CasePage, MerchantPages (Products, Inventory, Suppliers, Setup), DecisionsPage, OtherPages
└── test/           fixtures/ (captured from the real API), utils.tsx, app.test.tsx, cloud.test.tsx
```

## Refresh model

The backend has no WebSocket, SSE or event queue, so the UI loads data on navigation and after each action. A global refresh signal (`RefreshContext`) reloads mounted views after a scan, a demo, an investigation or an approval. There is no background polling.

## Tests

```bash
cd frontend
npm test            # Vitest: 42 tests (plain-language helpers, every page, case flow, approval, AI failure, evidence pack, readiness)
npm run e2e         # Playwright: isolated backend on react_e2e (reseeded with Astra Retail each run) + UI; 8 flows
```

Component tests render the real routes against responses captured from a merchant backend by `python scripts/capture_ui_fixtures.py [api-url]`. It runs baseline, the Stockout crisis (investigate, approve) and the Demand opportunity, plus the AI-failure drill. Re-run it after API changes. Expectations are derived from the fixtures, not typed in.

| Playwright flow | Checks |
|---|---|
| Stockout crisis | Critical status → case → investigate → Why → evidence → cost ledger → "Reorder now" with what REACT worked out → approve → `SIM-PO` → handled on the dashboard and in Activity |
| Demand opportunity | At least 3 supporting reasons, potential value, "Stock up", evidence tab, approve |
| Supplier delay | "Review the supplier", approve, overdue orders on Suppliers |
| Margin leakage | "Review the price", approve, every shelf price unchanged (`/products` before and after) |
| Excess inventory | "Run a promotion", approve, every stock level unchanged (`/inventory` before and after) |
| Rejection | Reason required, "No action was taken", listed under Rejected |
| AI failure | No substitute → 503 message and no recommendation; fallback → labelled result; approve → evidence pack with SHA-256 and `provenance.data = "SYNTHETIC"` |
| Mobile (Pixel 7) | Dashboard, Findings, Products, Inventory, Suppliers and a case without sideways scrolling |

The demo script is in [hackathon-demo.md](hackathon-demo.md).
