# Simulation

REACT Merchant runs on a **synthetic** business: Astra Retail, a fictional Bengaluru consumer-electronics and mobile-accessories retailer. Brands, products, suppliers and customers are all invented (`app/domains/merchant/catalog.py`). No real merchant data is used.

| File | Role |
|---|---|
| `app/domains/merchant/catalog.py` | Business template: categories, product families, suppliers, segments, channel / zone / weekday mixes, monthly expenses |
| `app/domains/merchant/profile.py` | `MerchantProfile`: what onboarding captures, plus validation |
| `app/domains/merchant/generator.py` | `MerchantDataGenerator`: the deterministic pipeline that writes every table |
| `app/domains/merchant/scenarios.py` | Scenario events and role resolution |
| `app/domains/merchant/simulation.py` | `regenerate()`: reset, scenario and onboarding all rebuild the business |
| `app/simulation/generator.py` | `seed_database()`: clears all tables, then dispatches on domain |
| `app/simulation/engine.py` | `SimulationEngine.run/reset`: hands merchant datasets to `regenerate()` |
| `app/domains/__init__.py` | `default_domain()` (`REACT_DOMAIN`, default `merchant`) and `current_domain(db)` (from `system_state.domain`) |

## Entry points

| Entry | What happens |
|---|---|
| `python -m scripts.seed [--seed N] [--days N] [--domain merchant\|manufacturing]` | `seed_database()` for the chosen domain (default `REACT_DOMAIN`) |
| App startup with `SEED_ON_STARTUP=true` and an empty database | `seed_database()` with the default domain |
| `POST /merchant/setup` | `regenerate(profile=...)`, then risk and opportunity detection |
| `POST /demo/run {"scenario": ...}` | `regenerate(scenario=...)` (`baseline` = no events), then detection |
| `POST /simulation/reset` | `SimulationEngine.reset()` → `regenerate()` for a merchant dataset |

`seed_database(db, config, clear=True, domain, profile, scenario)` truncates every REACT table, runs `MerchantDataGenerator` (merchant) or `SyntheticDataGenerator` (manufacturing), then runs `ANALYZE` on PostgreSQL.

## Determinism

- Each concern draws from its own RNG stream, `random.Random(f"{seed}:merchant:{section}")` (`catalogue`, `pricing`, `customers`, `demand`, `orders`, `supply`, `competitors`). UUIDs come from a seeded `ids` stream.
- The inputs are `(seed, history_days, anchor, profile, scenario)`. The anchor is the simulated "now" (`system_state.data_end`). The history covers the `history_days` complete days before it (default 120, minimum 30).
- Two runs with the same inputs give identical business rows. Only the bookkeeping timestamps (`created_at`, `updated_at`, `seeded_at`) take the wall clock. We checked this by hashing every table, minus those columns, across two in-memory SQLite runs.

## Pipeline

`MerchantDataGenerator.generate()` builds every row in memory and bulk-inserts the rows in foreign-key order. Steps:

1. **Organisation.** Writes the company, 1 to 3 `stores`, the selected `product_categories` and the suppliers those categories need. The main store sits in the zone of the onboarding locality. Extra stores go in other zones, with demand factors 1.0 / 0.8 / 0.7 relative to the main store.
2. **Catalogue.** Expands each product family into SKUs, one per variant. Velocity follows a long tail (`1/(i+1)^0.9`). Prices are rounded to Indian price points (…49 / …99), cost is `price × (1 − margin)`, and MRP is never below the price. Each SKU's demand is split across channel (STORE 0.74, ONLINE 0.14, WHATSAPP 0.12, with online share reduced for phones), store and city zone.
3. **Roles and events.** `resolve_roles()` picks the scenario targets from the merchant's categories. `build_scenario()` turns the scenario key into events (see [Scenarios](#scenarios)).
4. **Pricing and discounts.** Writes an `INITIAL` price row per SKU. About 8% of SKUs (never the scenario SKUs) get a 3 to 6% supplier cost revision that is passed on to the shelf price. Discount rows: a standing `COUNTER` discount (AD_HOC, in store), three baseline category campaigns (power banks, computer accessories, storage), and any scenario `Promotion`s.
5. **Customers.** Seeds a pool of 1,200 existing customers, weighted towards the main store's zone and its neighbours. New customers are created as orders arrive. `RETURNING_SHARE` (STORE 0.34, ONLINE 0.45, WHATSAPP 0.78) decides how often an order comes from an existing customer.
6. **Day loop.** Each simulated day runs:
   - **a. Deliveries.** Purchase orders due today add stock. A `supplier_deliveries` row records `delay_days` / `on_time`, and the PO becomes `RECEIVED`.
   - **b. Demand.** Order lines per SKU are `Poisson(base velocity × weekday factor × payday factor (1.08, days 1–5) × slow trend × promotion uplift (1 + 1.5 × discount) × scenario demand factors)`. Each line is assigned a channel, zone and store.
   - **c. Orders.** Lines are grouped into baskets of 1 / 2 / 3 items (weights 0.60 / 0.28 / 0.12). Each basket becomes an order with a customer, payment method, order items and a `PAID` sales transaction. Small businesses sometimes buy multiples. **Quantity is capped at stock on hand: demand beyond stock is a lost sale and is never recorded.** 18% of in-store lines get a counter discount (1–2.5% on phones, 2–6% otherwise) unless a promotion applies. Online and WhatsApp orders carry a fulfilment cost of ₹55 plus 1.2% of order value.
   - **d. Snapshots.** End-of-day `inventory_snapshots` (on hand, inbound, outbound) and the 28-day sales history per store and SKU.
   - **e. Purchasing.** Runs only on each supplier's order weekdays (see below).
   - **f. Bulk purchases.** Any scenario `BulkPurchase` is placed and delivered after the quoted lead time.
7. **Finalise.**
   - **Inventory.** One row per store and SKU (rows that never had stock or velocity are skipped): reorder point, `safety_stock = ceil(v × 3)`, `max_stock` = order-up-to level, `avg_daily_consumption` = 28-day average.
   - **Products.** Each product takes its current price and cost. `COST_REVISION` rows are added to `pricing_history`.
   - **Suppliers.** `reliability_score` = observed on-time share. `lead_time_days` = mean order-to-delivery days of the last 6 deliveries.
   - **Expenses.** Monthly rent, salaries, utilities and other costs are posted on fixed days (rent, salaries and utilities scale with store count). Marketing is posted weekly on Mondays. Delivery costs are posted daily from fulfilment.
   - **Financial metrics** (daily). Revenue, COGS, gross margin, operating cost (daily share of fixed expenses plus fulfilment), profit, discount and fulfilment totals.
   - **Competitor signals.** A weekly synthetic marketplace price check (Gaussian, σ = 2%) on the 8 SKUs with the highest velocity × price. Each row has `source="SYNTHETIC"` and `is_synthetic=true`.
   - **SystemState.** `domain="merchant"`, `generator_version="merchant-1.0"`, and `profile` = {merchant, template, roles, scenario, events}.

### Reorder policy (`policy()` in `generator.py`)

For each store and SKU, on the supplier's order days:

| Term | Definition |
|---|---|
| `v` | Units/day over the last 28 days, with missing days filled by the expected velocity |
| review period | `7 / number of supplier order weekdays` |
| reorder point `r` | `ceil(v × (lead + review + 3 safety days))` when `v ≥ 0.04`, else 0 |
| order-up-to | `max(r + 1, ceil(v × (lead + review + cover)))`. Cover is 14 days (6 for phones) |
| trigger | Place a PO when on hand + on order ≤ `r`, for order-up-to − position, at the current cost |
| delivery | Expected = order date + quoted lead time. On time with the supplier's on-time rate, otherwise 1 to 3 days late |

Opening stock comes from an "Opening stock" PO received 7 days before the history starts, sized at 60–95% of the order-up-to level.

### Suppliers (electronics template)

| Code | Lead time (days) | On-time rate | Order weekdays |
|---|---|---|---|
| METRO | 2 | 0.94 | Mon, Thu |
| SOUNDWAVE | 6 | 0.92 | Tue, Fri |
| DECCAN | 3 | 0.93 | Mon, Thu |
| POWERHUB | 5 | 0.92 | Tue, Fri |
| TECHLINE | 3 | 0.95 | Wed |
| WEARIX | 6 | 0.91 | Tue |
| KAVERI | 4 | 0.94 | Wed |
| PHONIX | 2 | 0.96 | Mon, Wed, Fri |

## Merchant profile and onboarding

`POST /merchant/setup` takes `business_name` (2–80 chars), `business_type` (only `electronics` exists), `city`, `state`, `locality`, `store_count` and `categories`. `MerchantProfile.normalised()` enforces:

- `store_count` between 1 and 3 (`MAX_STORES`).
- Category codes from the template. Unknown codes are rejected. An empty list means all 10. **At least 3 categories are required.**
- Errors raise `ProfileError`, which the endpoint returns as HTTP 422.

The profile is saved in `system_state.profile["merchant"]`. There is no account system. Setup replaces all current data, then runs `DetectionEngine` and `OpportunityEngine`. Default profile: "Astra Retail", Jayanagar, Bengaluru, Karnataka, 1 store, all categories.

## Scenarios

A scenario is **not a patch on finished data**. It is a list of events injected into the same deterministic run. Event types (offsets are days before today):

| Event | Effect inside the pipeline |
|---|---|
| `DemandShift(multiplier, start, ramp_days, skus / families / category, zone)` | Multiplies the demand rate from `start` onwards, with an optional linear ramp, optionally restricted to one customer zone |
| `SupplierSlowdown(supplier, start, on_time_rate, delay_range, hold_last_days)` | POs placed since `start` use the new on-time rate and delay range. `hold_last_days` pushes deliveries due in the last N days past today, so those POs stay `OPEN` |
| `CostChange(sku, start, cost_multiplier, pass_through)` | Supplier cost rises from `start`. If `pass_through` is false the shelf price stays the same |
| `Promotion(name, pct, start, end, skus / category)` | Adds a discount row. Demand rises by `1 + 1.5 × pct` |
| `BulkPurchase(sku, offset, units, note)` | Places a one-off PO |

Scenario parameters (`build_scenario`). Targets come from `system_state.profile.roles`. The values in brackets are what the default profile resolves to:

| Scenario | Events | Designed to surface |
|---|---|---|
| `stockout_crisis` | `DemandShift(1.8, start=18, ramp=3)` on `stockout_sku` [SPKX]; `SupplierSlowdown(stockout_supplier [SOUNDWAVE], start=30, on_time=0.0, delay 4–6, hold_last_days=4)` | risk `STOCKOUT_RISK` |
| `demand_opportunity` | `DemandShift(1.55, start=28, ramp=5)` on `opportunity_category` [AUDIO], zone `opportunity_zone` [South] | opportunity `REGIONAL_OPPORTUNITY` |
| `supplier_delay` | `SupplierSlowdown(delay_supplier [POWERHUB], start=45, on_time=0.15, delay 6–9)` | risk `SUPPLIER_RELIABILITY` |
| `margin_leakage` | `CostChange(margin_sku [BUDL-01], start=24, ×1.07, pass_through=False)`; `DemandShift(1.2, start=21, ramp=2)` on the same SKU | risk `MARGIN_DECLINE` |
| `excess_inventory` | `BulkPurchase(excess_sku [CAMW], offset=104, units=60)`; `DemandShift(0.32, start=100)` on the same SKU | risk `EXCESS_INVENTORY` |
| `baseline` | none (`regenerate(scenario=None)`) | normal trading |

`resolve_roles()` takes the first template-preferred family, category or supplier that the merchant actually sells. It keeps the stockout and margin SKUs distinct, and it never picks the stockout SKU's supplier as the delay supplier.

### Why regenerate instead of mutate

`regenerate()` keeps the seed, the anchor, the history window, the profile and `ai_outage_mode`, then rebuilds every table with the scenario's events applied. Extra demand draws stock down and triggers earlier POs. Late deliveries leave POs open and shelves empty. A cost rise flows into COGS, margin and `pricing_history`. Patching finished rows would leave orders, stock, POs, deliveries and financials out of step with each other.

The trade-off: a scenario also deletes earlier findings, decisions and activity, so each scenario starts from a clean business. A `simulation_runs` row records the roles, events, profile and seed, plus the targeted product and supplier ids in `affected_entities`.

## Scale

These counts come from one run of `MerchantDataGenerator` into in-memory SQLite (seed 42, 120 days, anchor 2026-09-25 07:00, electronics template, all 10 categories):

| Rows | 1 store | 3 stores |
|---|---|---|
| products / suppliers / categories | 300 / 8 / 10 | 300 / 8 / 10 |
| orders (= sales_transactions) | 4,578 | 9,642 |
| order_items | 6,332 | 13,426 |
| customers | 3,908 | 7,259 |
| inventory / inventory_snapshots | 300 / 35,816 | 900 / 107,381 |
| purchase_orders / supplier_deliveries | 239 / 231 | 700 / 676 |
| pricing_history / discounts / expenses | 334 / 4 / 153 | 334 / 4 / 153 |
| financial_metrics / competitor_signals | 120 / 144 | 120 / 144 |

Counts change with the seed, window, profile and scenario.

## Manufacturing reference pack

`SyntheticDataGenerator` (`app/simulation/generator.py`) still generates the original Astra Manufacturing dataset: machines, sensors, production, component supply. Its scenarios (`app/simulation/scenarios/`: bearing_failure, sensor_failure, supplier_delay, demand_spike, demand_drop, inventory_shortage, production_bottleneck, power_instability) **mutate** the seeded data inside a `SimulationRun`. They are exposed as `POST /simulation/<name>` and apply only when `system_state.domain = manufacturing`, for example after `python -m scripts.seed --domain manufacturing`. The test suite seeds and exercises this pack. On a merchant dataset `SimulationEngine.run()` routes to `regenerate()` instead, so only `/simulation/supplier-delay` (a scenario name both packs share) has an effect there; the other names are rejected as unknown merchant scenarios.
