# REACT Merchant: hackathon demo script

A 5-minute live demo, with an optional 2-minute extension. Every step below runs in the real application on synthetic data. The Playwright suite (`frontend/e2e/`) runs the same flows on every CI build.

**Before you start**

```bash
DATABASE_URL=postgresql+psycopg://localhost:5432/react .venv/bin/uvicorn app.main:app --port 8000
cd frontend && npm run dev            # http://localhost:5180
```

* Click **Demo scenarios → Normal business** once before going on stage, so the dashboard starts calm.
* Leave the browser at 1440 px wide. Keep a phone-width window ready if you want to show mobile.
* ₹ figures depend on the business date (the day the data was generated). The examples below are from 2 Oct 2026. Say "about", and never read numbers from this page.

## The story in one line

> A shop owner in Jayanagar doesn't have time to read reports. REACT watches the shop's own records, tells the owner what matters, shows why, prices it in rupees and waits for their approval. It never acts on its own.

## Main demo: Stockout crisis (about 3 minutes)

| # | Click | Say |
|---|---|---|
| 1 | Dashboard | "This is Astra Retail, a simulated electronics shop: 300 products, 8 suppliers. The stamp says *Simulated business*: everything here is synthetic." |
| 2 | **Demo scenarios → Stockout crisis** | "This scenario rebuilds four months of trading in which a best-selling speaker suddenly sells much faster while its supplier slows down. Then REACT runs its normal scan." |
| 3 | **Back to dashboard** | The status turns **Critical**. "Wireless Speaker X may run out within a day. REACT has already priced it: about ₹38,800 of lost sales. The ledger on the right adds up what is at stake, and every amount comes from a formula, not from AI." |
| 4 | Open the critical finding → **Investigate with REACT** | "REACT tests five possible explanations against the records: demand surge, late deliveries, reorder level too low, a promotion, one big order." |
| 5 | Read **Why** | "Late deliveries left the shelf short, strongly supported. Half of SoundWave's deliveries in 45 days were late against a normal 13%, and the next order is already overdue. Demand jumping is a contributing factor." |
| 6 | **Why does REACT think this? → See the source records** | "Every line is a stored record: purchase orders, deliveries, daily stock. REACT also shows the evidence *against* each explanation, so you can judge it yourself." |
| 7 | Back to **Overview → What it could cost** | "The ledger: sales lost while out of stock. *View calculation* shows the formula and inputs. The cost of the reorder is shown for context, not added to the total." |
| 8 | **What should I do?** | "Reorder now: 94 units from SoundWave, about ₹1.7 lakh, arriving around the 11th. Here is how the quantity was set: daily sales × (lead time + 14 days cover + 3 safety days) − stock − what's on order. A calculator did that, not a language model." |
| 9 | Type a name → **Approve** | "Action completed, and it was a *simulated* action: SIM-PO-… No purchase order was sent, no money moved, and stock records are unchanged. An evidence pack with a SHA-256 hash was saved for audit." |
| 10 | **Dashboard**, then **Activity** | "The issue moved to *Handled*, and Activity shows the whole trail: scenario, scan, investigation, recommendation, approval, simulated action." |

## Second scenario, pick one (about 1 minute)

| Scenario | Point to make | Expected recommendation |
|---|---|---|
| **Demand opportunity** | "REACT also finds money to be made: wireless audio is selling about 40% faster in the store's own zone, and other zones aren't. It sizes the extra revenue and checks whether stock can keep up." | Stock up |
| **Margin leakage** | "The speaker earns less on every sale. The supplier raised the cost and the shelf price never moved. REACT prices the leak over 90 days and works out the price that restores the margin." | Review the price |
| **Excess inventory** | "A big lot bought months ago is barely selling. REACT shows the cash locked on the shelf." | Run a promotion |
| **Supplier delay** | "One supplier is late order after order. REACT traces which products depend on it." | Review the supplier |

Use **Open the main finding** in the scenario dialog to go straight to the case.

## Trust moments (about 1 minute, choose any)

* **Reject:** on any recommendation, click **Reject**. A reason is required; then "No action was taken", and it appears under *Decisions → Rejected*.
* **AI failure drill:** **Data & System → AI down, no substitute**, then investigate a finding. REACT answers "AI investigation temporarily unavailable" with no guessed answer. Switch to **AI down, use rule-based analysis** and the same evidence-based answer appears, labelled. Switch back to **Normal** afterwards.
* **Technical details** tab: the Evidence Support Scores, hypothesis ranking, validation checks, the agent trace (which tools were called, with what arguments) and the cited evidence IDs. There is no chain-of-thought, by design.
* **Products / Inventory / Suppliers:** the same records the findings came from, each with its finding linked.
* **Set up a demo business:** change the name, city or categories, and REACT generates a different synthetic shop in a few seconds.

## Questions you may get

| Question | Accurate answer |
|---|---|
| Is this running on AWS? | The AWS architecture is written in CDK and tested offline (12 infrastructure tests). It has **not been deployed**. |
| Does it use Bedrock? | The Bedrock adapter exists behind the same interface and is unit-tested with stubs. It has **not been called live**. The demo uses the deterministic provider, so every result is reproducible. |
| Is the data real? | No. Every record is synthetic and labelled synthetic. |
| Could the AI invent a number? | No. Money comes only from the deterministic Impact Engine. Agent outputs are validated against the engine's evidence and hypothesis IDs, and anything else is rejected. |
| What does "Strongly supported" mean? | It is an Evidence Support Score of at least 0.8, built from relevance, recency, source reliability, correlation and consistency of the evidence. It is not a probability. |
| Did it place an order? | No. Every action is simulated, and tests check that prices, stock levels and purchase orders are unchanged after approval. |

## If something goes wrong

* A scenario takes a few seconds; the dialog shows *Preparing scenario*.
* If the UI shows an error banner, check that `/api/ready` returns `ready`.
* `scripts/demo.sh stockout_crisis` runs the same loop against the API from a terminal.
