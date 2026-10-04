# Investigation engine (merchant)

Detection (`docs/detection.md`) establishes *that* a risk or opportunity exists. An investigation explains *why*, names the records that show it, and states what it is worth. The engine is deterministic. Hypotheses come from a registry, evidence is evaluated against measured signals, the Evidence Support Score is a fixed formula, and the Impact Engine calculates the money. No LLM runs anywhere in this path.

Agents (`app/agents/`) can run on top of a completed investigation, but they cannot change its results:

* Their structured output is validated against the investigation. They may only cite hypothesis and evidence ids that the engine produced, and they must restate the deterministic Evidence Support Score (checked to within 0.011).
* No chain-of-thought is stored.
* The default `llm_provider` is `deterministic`, a rule-based provider with no network access. A Bedrock provider class exists, but no real Bedrock execution or AWS deployment has taken place.

## Lifecycle (`app/investigation/engine.py`)

```
finding (BusinessRisk | Opportunity)
  -> CREATED -> COLLECTING_EVIDENCE   collect_signals: tool calls -> SignalSet
             -> ANALYZING             registry -> candidates -> evidence -> Evidence Support Score
                                      -> ranking -> conclusion -> impact -> graph -> report (+ key_signals)
             -> COMPLETED | FAILED (error recorded)
```

* Every step and tool call is written to `investigation_trace_entries` with sequence, step, message, details and duration.
* Re-running an investigation replaces its hypotheses and evidence.
* An investigation never modifies business data. For schema compatibility, the score of the leading hypothesis is persisted in `investigations.confidence` / `confidence_label`. The value in that column is the Evidence Support Score.

## Signal collection (`signals.py`, `app/domains/merchant/signals.py`)

On a merchant dataset, `collect_signals` sends risks and opportunities to `collect_merchant_signals`. Each result becomes an `Observation` with these fields: `signal`, `value`, `unit`, `baseline`, `source_type`, `source_ids`, `timestamp`, and a plain-language `description`. A failed tool call is recorded in the trace and does not stop the investigation. Observations are timestamped at the end of the day before `today`, unless a record date applies (for example, the date of a receipt or a price change).

| Scope (when) | Tools called | Example signals |
|---|---|---|
| Product (the finding has a `product_id`) | `get_product_sales` (14 vs 56 d), `get_supplier_history` (120 d), `get_inventory_status`, `get_sales_history` (120 d, if receipts exist), `get_pricing_history`, `get_discount_history` (21 d), `get_competitor_prices`, `get_category_performance` | `demand_change_pct` (in-stock adjusted), `distinct_customers_change_pct`, `top_customer_share`, `online_share_change_pts`, `unit_margin_change_pct`, `discount_rate_change_pts`, `days_of_cover`, `stock_change_pct`, `stockout_days_14`, `open_po_overdue`, `no_open_po`, `reorder_cover_ratio`, `purchase_cover_days`, `receipt_vs_normal_order_ratio`, `demand_change_since_purchase_pct`, `cost_list_change_pct`, `list_price_change_pct`, `cost_increase_passed_on`, `promotion_active`, `competitor_price_gap_pct`, `supplier_late_share_recent`, `supplier_late_deliveries_6`, `supplier_lead_change_days` |
| Supplier risks | `get_supplier_history` | `late_deliveries_6`, `late_share_recent`, `lead_change_days`, `on_time_change_pts`, `average_delay_when_late`, `overdue_pos`, `po_units_change_pct`, `products_low_cover`, `dependent_products` |
| Category / zone (category and regional opportunities, category demand decline) | `get_customer_orders` (28 d), `get_regional_performance`, `get_customer_segments`, `get_discount_history` (28 d), `get_competitor_prices` | `category_demand_change_pct`, `zone_category_growth_pct`, `other_zones_growth_pct`, `zone_total_growth_pct`, `distinct_customers_growth_pct`, `new_customer_share_change_pts`, `segment_share_change_pts` |
| Whole business (revenue / repeat decline) | `get_business_baseline`, `get_financial_metrics` (28 and 56 d) | `weekly_revenue_change_pct`, `repeat_share_change_pts`, `top_products_short_of_stock`, `top_products_declining`, `orders_change_pct`, `average_order_value_change_pct` |

## Hypothesis registry (`registry.py`, `app/domains/merchant/registry.py`)

`registry_for("merchant")` returns `MERCHANT_HYPOTHESES` (28 specs). Each `HypothesisSpec` has `kinds`, `subject_types`, `requires_any`, `triggers`, `supporting` and `contradicting` conditions, and a `next_step`. A `Condition` is `(signal, op, threshold, weight, label)`, where `op` is one of `gt`, `gte`, `lt`, `lte`, `true`, `false`.

* **When a spec applies.** The subject kind must match, the finding type must be in `subject_types`, and at least one `requires_any` signal must have been observed.
* **Condition strength.** A satisfied condition has strength `min(1, 0.5 + 0.5 x margin)`, where `margin = (value - threshold)/|threshold|`, with the sign flipped for `lt`/`lte`, and divided by 1 when the threshold is 0. A satisfied boolean condition has strength 1.
* **Initial ranking.** `trigger_score = Σ weight x strength` over the satisfied triggers, with a floor of `0.04 x total trigger weight`. `initial_confidence` is each candidate's share of the summed trigger scores. It only orders the candidates; it is not the Evidence Support Score.
* **API.** `GET /investigations/registry` returns the registry of the active domain. You can override it with `InvestigationEngine(db, registry=[...])`.

| Finding types | Hypotheses (`TYPE`: label) |
|---|---|
| `STOCKOUT_RISK`, `DEMAND_SUPPLY_MISMATCH` | `DEMAND_SURGE`: Demand jumped faster than stock was replenished; `SUPPLIER_DELAY_SHORTAGE`: Late deliveries left the shelf short; `REORDER_POLICY_GAP`: The reorder level is too low; `PROMOTION_DRIVEN_DEMAND`: A promotion pulled demand forward; `BULK_ORDER_DEPLETION`: One large order drained the stock |
| `MARGIN_DECLINE`, `PRICE_ANOMALY`, `DISCOUNT_LEAKAGE` | `PURCHASE_COST_INCREASE`: The supplier raised the cost and the price did not follow; `DISCOUNTING`: Deeper discounts are eating the margin; `SELLING_PRICE_CUT`: The shelf price was cut; `FULFILMENT_COST_INCREASE`: More delivery orders added cost; `COMPETITOR_PRICE_PRESSURE`* |
| `EXCESS_INVENTORY`, `SLOW_MOVING_INVENTORY` | `OVER_PURCHASE`: A purchase was far larger than demand; `DEMAND_FADED`: Demand dropped after the stock was bought; `CHRONIC_SLOW_MOVER`: The product has always sold slowly; `COMPETITOR_PRICE_PRESSURE`* |
| `SUPPLIER_DELAY`, `SUPPLIER_RELIABILITY` | `SUPPLIER_RELIABILITY_DETERIORATED`: The supplier has become unreliable; `ONE_OFF_DELAY`: A single delayed order, not a pattern; `SUPPLIER_CAPACITY_STRAIN`: The supplier is struggling with larger orders |
| `DEMAND_DECLINE` | `MARKET_DEMAND_DECREASE`: Demand for the whole category is falling; `PRODUCT_LOSING_APPEAL`: This product is losing customers to alternatives; `PRICE_INCREASE_REDUCED_DEMAND`: A price increase reduced demand; `STOCKOUTS_LOST_SALES`†; `COMPETITOR_PRICE_PRESSURE`* |
| `REVENUE_DECLINE`, `REPEAT_CUSTOMER_DECLINE` | `STOCKOUTS_LOST_SALES`†; `CUSTOMER_RETENTION_DECLINE`: Fewer customers are coming back; `FEWER_SHOPPERS`: Fewer customers are shopping |
| `DEMAND_OPPORTUNITY` | `BROAD_PRODUCT_DEMAND`: Many more customers want this product; `CATEGORY_DEMAND_GROWTH`‡; `PROMOTION_DRIVEN_SPIKE`§; `BULK_BUYER_SPIKE`¶ |
| `CATEGORY_OPPORTUNITY` | `CATEGORY_DEMAND_GROWTH`‡; `SEGMENT_EXPANSION`: A customer segment is driving the growth; `PROMOTION_DRIVEN_SPIKE`§; `BULK_BUYER_SPIKE`¶ |
| `REGIONAL_OPPORTUNITY` | `REGIONAL_DEMAND_GROWTH`: Customers in one area are buying more; `CATEGORY_DEMAND_GROWTH`‡; `SEGMENT_EXPANSION`; `PROMOTION_DRIVEN_SPIKE`§; `BULK_BUYER_SPIKE`¶ |

Labels of hypotheses that appear in more than one row: \* Cheaper prices online are forcing discounts; † Empty shelves are costing sales; ‡ The whole category is growing; § A promotion created a temporary spike; ¶ One bulk buyer explains the jump.

## Evidence items (`evidence.py`)

Each candidate's conditions are evaluated against the `SignalSet`:

* A **supporting condition that is met** produces evidence that **supports** the hypothesis, with weight `w`.
* A **supporting condition that is observed but not met** produces evidence that **contradicts** the hypothesis, with weight `0.6 x w`. Its claim starts with "Condition not met: ".
* A **supporting condition that is not observed** adds `w` to the unknown weight `U`.
* A **contradicting condition that is met** produces evidence that contradicts the hypothesis, with weight `w`. It is skipped if a supporting condition already used the same `(signal, op)`.

Each item stores `signal`, `source_type`, `source_id(s)`, `timestamp`, `claim`, `value`, `unit`, `baseline`, `relevance`, `supports` / `contradicts`, its score and `score_breakdown`, plus the condition, threshold and weight in `details`.

**Per-item score.**

```
score = 0.35·relevance + 0.15·recency + 0.20·source_reliability + 0.15·correlation + 0.15·consistency
```

* `relevance = (w / max condition weight) x strength` for a met condition, or `x 0.6` for an unmet one, capped at 1.
* `recency` depends on the age of the observation: ≤ 1 day 1.0, ≤ 7 days 0.85, ≤ 30 days 0.7, ≤ 90 days 0.5, older 0.3, no timestamp 0.6.
* `source_reliability` comes from `SOURCE_RELIABILITY`, with 0.7 for an unknown source:
  * 0.95: `order`, `order_item`, `purchase_order`, `supplier_delivery`, `pricing_history`, `product`
  * 0.9: `inventory`, `inventory_snapshot`, `customer`, `discount`, `expense`, `financial_metric`
  * 0.85: `supplier`
  * 0.55: `market_signal`
  * 0.5: `competitor_signal` (the synthetic online price checks)
* `correlation = 0.5 + 0.5 x (share of the hypothesis' other items that point the same way)`.
* `consistency` is the supporting share of the hypothesis' evidence weight for a supporting item, and one minus that share for a contradicting item.

## Evidence Support Score

```
ESS = S / (S + C + 0.5·U)
S = Σ w·score (supporting items),  C = Σ w·score (contradicting items),  U = Σ w (unobserved supporting conditions)
```

* ESS is 0 when there is no evidence and no unobserved weight.
* A hypothesis with no supporting item is capped at 0.15.
* `support_label` (backend): ≥ 0.8 `strongly supported`, ≥ 0.6 `supported`, ≥ 0.4 `weakly supported`, otherwise `insufficient support`.
* The UI (`frontend/src/lib/language.ts`) shows the same bands, capitalised: Strongly supported / Supported / Weakly supported / Insufficient support.
* Hypothesis status is SUPPORTED at ≥ 0.6, WEAKLY_SUPPORTED at ≥ 0.4, and NOT_SUPPORTED otherwise.

The Evidence Support Score measures how strongly the available records support a hypothesis. It is not a probability, a model confidence or a certainty, and every report states this.

## Leading and contributing hypotheses (`analysis.py`)

Hypotheses are ranked by ESS, with ties broken by type. Let `margin` be the leader's ESS minus the runner-up's.

* **Contributing factor.** If both the leader and the runner-up are ≥ 0.8 and `margin < 0.15`, the runner-up is returned as `contributing_factor`.
* **Determined.** The result is *determined* when the leader is ≥ 0.5 and either there is no runner-up, `margin ≥ 0.15`, or there is a contributing factor.
* **Split evidence.** If the leader is ≥ 0.5 but `margin < 0.15` without a contributing factor, the statement says the evidence is split.
* **Otherwise**, the statement says that no hypothesis is adequately supported.

For merchant findings the conclusion kind is `risk_driver` or `opportunity_explanation`. The conclusion also includes:

* the supporting and contradicting evidence of the leader
* the alternatives, with their scores
* the registry's `next_step`

## "Why" panel: key signals (`app/domains/merchant/language.py`)

`key_signals()` builds `report.key_signals` from the leader's supporting items:

1. If at least 3 items have weight ≥ 1.5, it uses only those. Otherwise it uses all supporting items. The items are sorted by weight and then by score.
2. It takes the first 3 of these, then up to 2 supporting items from the contributing factor (weight ≥ 1.5), then the rest.
3. It removes duplicate signals and keeps at most 5.

Each entry has a plain label (`LABELS`), a formatted `display` and `direction`, `baseline_display`, the `claim`, `source_type`, `record_count` and the condition. The UI does not re-derive any business logic.

## Impact analysis (`app/domains/merchant/impact.py`)

`analyse_merchant_impact` recomputes the figures from current data. It uses the same `MerchantDetector` measures as detection and calls only `ImpactEngine` methods. `total_includes` lists the components that make up `estimated_total`. Every other figure is context and is never added to the total.

| Finding | In total | Context |
|---|---|---|
| `STOCKOUT_RISK` | `lost_sales` = `stockout_loss(demand_rate, gap, price, margin)` | `reorder_cost` (`purchase_order_value`), `reorder_suggestion`; a lead-time constraint if the observed lead time exceeds quoted + 1 day |
| `DEMAND_SUPPLY_MISMATCH` | `sales_at_risk` = `inventory_risk(v14 x (lead_eff + 7), on_hand + on_order, price)` | `reorder_cost`, `reorder_suggestion` |
| `SUPPLIER_DELAY` / `_RELIABILITY` | `delay_exposure` = `supplier_delay_exposure(...)` | `dependent_revenue` (28-day revenue per day x 30 days) |
| `MARGIN_DECLINE`, `PRICE_ANOMALY` | `margin_loss` (90 days) | `suggested_price` = `price_to_restore_margin`: ceil(cost + normal unit margin); an MRP constraint if that price is above the MRP |
| `DISCOUNT_LEAKAGE` | `discount_leakage` = `discount_impact` (90 days) | |
| `EXCESS_` / `SLOW_MOVING_INVENTORY` | `capital_locked` = `inventory_capital_locked` (excess over 45 days of demand; for slow-moving, all units) | holding cost (3 months) |
| `DEMAND_DECLINE` / `REVENUE_DECLINE` | `revenue_shortfall` = `revenue_impact` (30-day, or weekly for revenue decline) | |
| `REPEAT_CUSTOMER_DECLINE` | `customer_exposure` = `customer_revenue_exposure` (1 month) | |
| Opportunities | `potential_revenue` = `demand_opportunity_value` (30 days, 0.7 capture) | `stock_needed` (`purchase_order_value`), stock constraints |

When the leading hypothesis is `PROMOTION_DRIVEN_SPIKE`, `BULK_BUYER_SPIKE` or `PROMOTION_DRIVEN_DEMAND`, a `demand_durability` constraint is added. It marks the projection as an upper bound.

`reorder_suggestion` computes `quantity = ceil(units_per_day x (lead_days + 14 cover days + 3 safety days) - on_hand - on_order)`. `units_per_day` comes from `demand_rate`, which falls back to the normal rate when the item was mostly out of stock, and `lead_days` from the supplier's `lead_eff`. Both inputs are rounded before use so that anyone can reproduce the number.

**ImpactEngine methods (merchant).**

* Default horizon of 30 days (`default_horizon_days`): `demand_opportunity_value`, `dependent_revenue`.
* Recurring horizon of 90 days (`recurring_horizon_days`), because these losses persist until the price list is reviewed: `margin_loss`, `discount_impact`.
* No horizon: `stockout_loss` and `supplier_delay_exposure` count only the days a product would be out of stock before the next delivery.
* Other methods: `inventory_capital_locked` (3-month holding cost), `customer_revenue_exposure` (1 month), `purchase_order_value`, `price_to_restore_margin`, and the shared `revenue_impact` / `inventory_risk`.

Every result is labelled `is_estimate=True` and `basis="SYNTHETIC"`. Money is calculated only by this engine, never by an LLM.

## Tools (`app/investigation/tools.py`, `app/domains/merchant/tools.py`)

The `@tool(name, description, params, domain=...)` decorator registers a function in `TOOLS_BY_DOMAIN[domain]`. `resolve_tool` looks a tool up in the dataset's domain, so a merchant database cannot call manufacturing tools. `GET /investigations/tools` lists the active domain's catalogue.

Every tool:

* validates its input and raises `ToolError` with a stable `code` on failure
* returns JSON
* logs `tool.executed` / `tool.failed`
* never runs caller SQL, writes data, or touches the network or filesystem

Agent allow-lists (`app/agents/policies.py`) give the Investigation agent read and calculation tools, the Evidence and Decision agents read tools only, and the Detection agent `search_business_records` only.

* **Read tools (16):** `get_product_sales`, `get_inventory_status`, `get_sales_history`, `get_supplier_history`, `get_purchase_orders`, `get_customer_orders`, `get_customer_segments`, `get_pricing_history`, `get_discount_history`, `get_financial_metrics`, `get_expense_history`, `get_category_performance`, `get_regional_performance`, `get_business_baseline`, `get_competitor_prices` (synthetic, lower reliability), `search_business_records`.
* **Calculation tools (5):**
  * `calculate_stockout_loss`, `calculate_margin_loss`, `calculate_inventory_capital` and `calculate_demand_opportunity` are thin wrappers over the Impact Engine.
  * `calculate_reorder_quantity` returns units, not money, using the same formula as `reorder_suggestion`.

## Graph and report

`graph.py` builds `Subject -> Signal -> Evidence -> Hypothesis -> Conclusion`, with `record:<source_type>:<id>` nodes. The merchant report (`report.py`) contains:

* the summary statement
* the top hypothesis and its ESS with the label
* the ranked hypotheses, and the supporting and contradicting claims
* impact lines, with context components marked "(context, not in total)"
* constraints, the recommended next step, `key_signals`, and the Evidence Support Score terminology note
