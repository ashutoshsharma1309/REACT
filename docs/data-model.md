# Data model

PostgreSQL with SQLAlchemy 2 declarative models in `app/models/`. Conventions:

- UUID primary keys, except the high-volume `sensor_readings` (BIGINT identity) and the singleton `system_state` (id = 1).
- Enums are stored as non-native VARCHAR (`app/models/_types.py: enum_column`), so new values need no DDL.
- JSON columns are JSONB on PostgreSQL and generic JSON on SQLite.
- Timestamps are naive UTC. Money is `Numeric`, currency INR.

One database holds one domain at a time. `system_state.domain` records which pack generated the data (`merchant` or `manufacturing`). Every engine reads it through `app.domains.current_domain(db)`. REACT Merchant is the default and the only domain the UI shows. The manufacturing tables stay in the schema for the backend reference pack (`app/simulation/*` and its tests).

**All business data is synthetic.** It comes from the deterministic generators described in [simulation.md](simulation.md). None of it is real merchant data.

## Entity map (merchant)

```
companies ─┬─ stores ─┬─ inventory (PRODUCT, per store) ─── inventory_snapshots (daily)
           │          ├─ orders (store_id, channel) ─┬─ order_items ─── products
           │          │                              └─ sales_transactions
           │          ├─ purchase_orders ─┬─ purchase_order_items ─── products
           │          │                   └─ supplier_deliveries
           │          └─ expenses (store_id nullable)
           ├─ product_categories ─── products (category_id, supplier_id) ─── pricing_history
           ├─ suppliers ─── purchase_orders, supplier_deliveries, products.supplier_id
           ├─ customers ─── orders
           ├─ discounts ─── order_items.discount_id
           └─ financial_metrics (daily)
competitor_signals                         (SYNTHETIC, is_synthetic = true)
business_risks, opportunities              (detection outputs, evidence JSON)
simulation_runs, detection_runs, system_state
```

## Core tables reused by merchant

The merchant pack reuses the commerce, supply and finance tables. Merchant-only columns are nullable or have server defaults (see [Migration](#migration)).

| Table | Merchant meaning | Merchant columns / notes |
|---|---|---|
| `companies` | The merchant (default "Astra Retail") | `business_type`, `city`, `state` (null for manufacturing) |
| `products` | SKUs | `category_id` → `product_categories`, `brand`, `mrp`, `supplier_id` (primary supplier). `production_line_id` is null. `category` holds the category name; `unit_cost` / `selling_price` are the current price list |
| `customers` | Retail customers | `locality`, `acquisition_channel`. `segment` uses the retail values `STUDENT`, `YOUNG_PROFESSIONAL`, `FAMILY`, `SMALL_BUSINESS`. `region` is a city zone, e.g. "South Bengaluru" |
| `orders` | One basket | `channel` (`STORE` / `ONLINE` / `WHATSAPP`), `store_id`, `discount_total`, `fulfilment_cost`, `payment_method` |
| `order_items` | Basket lines | `list_price`, `discount_amount`, `discount_id`. Invariant: `line_revenue = quantity * list_price - discount_amount` |
| `sales_transactions` | Payment for an order | `payment_method`. The merchant generator writes them as `PAID` with 0 payment-term days |
| `inventory` | Stock position per SKU per store (`item_type = PRODUCT`) | `store_id`. `reorder_point`, `safety_stock`, `max_stock` come from the simulated reorder policy. `avg_daily_consumption` is the 28-day average of units sold |
| `inventory_snapshots` | End-of-day stock per inventory row | `quantity_available`, `inbound_units`, `outbound_units` |
| `suppliers` | Distributors | `component_type` holds what the supplier sells to the merchant. `reliability_score` and `lead_time_days` are recomputed from the delivery history; `baseline_lead_time_days` is the quoted lead time |
| `financial_metrics` | Daily P&L | `discount_total`, `fulfilment_cost`. `production_cost` is 0 for merchant. `operating_cost` is the daily share of fixed expenses plus fulfilment |
| `competitor_signals` | Weekly synthetic marketplace price checks for the top SKUs | `details` holds `sku`, `observed_price`, `our_price`. `source = "SYNTHETIC"`, `is_synthetic = true` |
| `market_signals` | Not written by the merchant generator | Used by the manufacturing pack only |
| `system_state` | Singleton: seed, data window, run pointers | `domain`, `profile` (JSON, below), `generator_version` (`merchant-1.0`), `ai_outage_mode` |

`system_state.profile` for a merchant dataset:

```json
{
  "merchant": {"business_name": "...", "business_type": "electronics", "city": "...", "state": "...",
               "locality": "...", "store_count": 1, "categories": ["PHONES", "CASES", "..."]},
  "template": "electronics",
  "roles": {"stockout_sku": "...", "stockout_supplier": "...", "margin_sku": "...", "excess_sku": "...",
            "excess_supplier": "...", "opportunity_category": "...", "opportunity_zone": "...", "delay_supplier": "..."},
  "scenario": "stockout_crisis | ... | null",
  "events": [{"kind": "demand_shift", "...": "..."}]
}
```

`profile.merchant` is what onboarding captured. A reset or scenario reuses it to rebuild the same business. `roles` and `events` record which products and suppliers a scenario targeted.

## Merchant tables (`app/models/merchant.py`)

| Table | Purpose | Notable columns |
|---|---|---|
| `stores` | Physical shops (1 to 3) | `company_id`, `code` (`S1`..`S3`, unique), `name`, `store_type` (`PHYSICAL` / `ONLINE`; the generator creates only `PHYSICAL`), `locality`, `city`, `region` (city zone), `opened_on`, `is_active` |
| `product_categories` | Categories the merchant sells | `code` (unique), `name`, `target_margin` (0..1), `description` |
| `purchase_orders` | Orders placed with a supplier for one store | `po_number` (unique), `supplier_id`, `store_id`, `order_date`, `expected_date`, `status` (`OPEN` / `PARTIALLY_RECEIVED` / `RECEIVED` / `CANCELLED`), `total_units`, `total_cost`, `notes`, `simulation_run_id`. Index (supplier_id, order_date) |
| `purchase_order_items` | PO lines | `product_id`, `quantity`, `quantity_received`, `unit_cost`, `line_cost` |
| `supplier_deliveries` | Receipts against a PO; the evidence for supplier reliability | `purchase_order_id`, `supplier_id`, `store_id`, `expected_date`, `delivered_date`, `delay_days`, `on_time`, `units_received`. Index (supplier_id, delivered_date) |
| `pricing_history` | Price-list changes per SKU | `effective_date`, `selling_price`, `unit_cost`, `mrp`, `reason` (`INITIAL` / `COST_REVISION` / `PRICE_CHANGE`), `note`. Index (product_id, effective_date) |
| `discounts` | Promotions and the standing counter discount | `code` (unique), `name`, `scope` (`STORE_WIDE` / `CATEGORY` / `PRODUCT` / `AD_HOC`), `discount_pct` (0..1), `product_id`, `category_id`, `start_date`, `end_date`, `channel`, `simulation_run_id` |
| `expenses` | Operating expenses | `store_id` (nullable), `expense_date`, `category` (`RENT` / `SALARIES` / `UTILITIES` / `MARKETING` / `DELIVERY` / `PACKAGING` / `OTHER`), `amount`, `description`, `is_recurring`. Index (company_id, expense_date) |

A PO with no `supplier_deliveries` row is still `OPEN`, so its stock has not arrived by the simulated today.

## Workflow tables (shared by both domains)

These tables are domain-agnostic. Detail is in [detection.md](detection.md), [investigation.md](investigation.md), [agents.md](agents.md) and [decisions.md](decisions.md).

| Table | Role |
|---|---|
| `incidents` | Operational incidents. Manufacturing only: merchant detection produces no incidents |
| `business_risks` | Risks. Merchant types include `STOCKOUT_RISK`, `SUPPLIER_RELIABILITY`, `SUPPLIER_DELAY`, `REVENUE_DECLINE`, `MARGIN_DECLINE`, `EXCESS_INVENTORY`, `SLOW_MOVING_INVENTORY`, `DISCOUNT_LEAKAGE`, `PRICE_ANOMALY`, `DEMAND_DECLINE`, `REPEAT_CUSTOMER_DECLINE`. `entity_type` / `entity_id` point at the product or supplier |
| `opportunities` | Opportunities. Merchant types include `CATEGORY_OPPORTUNITY`, `REGIONAL_OPPORTUNITY`, `DEMAND_OPPORTUNITY` |
| `detection_runs`, `simulation_runs` | Run records. `simulation_runs` for merchant stores roles, events, profile and seed in `parameters`, plus targeted product / supplier ids in `affected_entities` |
| `investigations`, `hypotheses`, `evidence_items`, `investigation_trace_entries` | Deterministic investigation. `hypotheses.evidence_support_score` and `evidence_items.evidence_support_score` hold the Evidence Support Score (0..1). On `investigations`, the legacy-named column `confidence` stores the leading hypothesis's Evidence Support Score; it is not a probability |
| `agent_runs`, `agent_tool_calls`, `agent_trace_entries` | Agent workflow, every tool call (permitted or denied) and the visible trace. `provider`, `llm_used` and `fallback_used` record whether a language model ran or only the deterministic provider |
| `decisions`, `approvals`, `action_executions` | Recommendation, human approval and the simulated action (`action_executions.simulated`) |
| `artifacts` | Evidence packs written on approve / reject: `storage_backend` (`local` / `s3`), `location`, `sha256`, `status` (`STORED` / `FAILED`) |

Each finding row carries an `evidence` JSON payload with the rule, the window and the ids of the records behind it.

## Synthetic labelling

The new merchant tables have no per-row synthetic flag. A whole dataset is synthetic, and the labels sit at these points:

| Where | Field |
|---|---|
| `competitor_signals`, `market_signals` | `is_synthetic` (default `true`), `source` (default `"SYNTHETIC"`) |
| `incidents`, `business_risks`, `opportunities` | `is_synthetic` (default `true`) |
| `sensors` (manufacturing) | `is_synthetic` |
| Impact estimates (`app/impact/engine.py`), product read models, agent result schema | `basis = "SYNTHETIC"` (impact values also carry `is_estimate = true`) |
| `GET /merchant` (`read_models.merchant`) | `is_synthetic: true`, `source: "SYNTHETIC"` |
| Evidence packs (`app/services/artifact_service.py`) | `"data": "SYNTHETIC"` |

## Manufacturing reference tables

`plants`, `production_lines`, `machines`, `machine_components`, `sensors`, `sensor_readings`, `production_metrics`, `maintenance_records`, `historical_incidents`, `supplier_batches` and `market_signals` stay unchanged. They are populated only when the manufacturing pack seeds the database (`python -m scripts.seed --domain manufacturing`). The same reused tables (`products`, `orders`, `inventory`, ...) then hold manufacturing data, with the merchant columns left null or at their defaults.

## Migration

`alembic/versions/20261002_c7d3e9a1f204_merchant_domain.py` (revision `c7d3e9a1f204`, revises `b6e1f0c2d9a7`, the Phase 6 migration). It is additive only:

- **Creates** `product_categories`, `stores`, `expenses`, `purchase_orders`, `supplier_deliveries`, `discounts`, `pricing_history`, `purchase_order_items`, with their indexes.
- **Adds columns** to `companies` (business_type, city, state), `customers` (locality, acquisition_channel), `products` (category_id, brand, mrp, supplier_id), `orders` (channel, store_id, discount_total, fulfilment_cost, payment_method), `order_items` (list_price, discount_amount, discount_id), `inventory` (store_id), `sales_transactions` (payment_method), `financial_metrics` (discount_total, fulfilment_cost) and `system_state` (domain, server default `'manufacturing'`; profile, server default `'{}'`). New columns are nullable or have a server default, so existing rows stay valid.
- **Adds foreign keys** from the new columns to `stores`, `product_categories`, `suppliers` and `discounts`.
- Drops and renames nothing. The earlier migrations (phase1, phase2, phase3, phase4, phase6) are untouched, so the history is a single linear chain. `downgrade()` removes only what this revision added.

Apply with `alembic upgrade head`. `AUTO_CREATE_SCHEMA=true` additionally runs `create_all` at startup for local use.
