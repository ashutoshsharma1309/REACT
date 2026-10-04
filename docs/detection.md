# Detection: merchant risks and opportunities

REACT Merchant runs deterministic detection rules over the records of a fictional Bengaluru electronics retailer, Astra Retail. The rules use thresholds, exact Poisson tail tests, SQL aggregations and Impact Engine formulas. No LLM is involved. If the data and configuration are the same, the findings are the same, and every finding carries the ids of the records behind it.

The active domain belongs to the dataset (`system_state.domain`, `app/domains/__init__.py`). When it is `merchant`:

* `DetectionEngine.run()` (`app/detection/engine.py`) calls `MerchantDetector(db).risks()` and produces no machine incidents. The manufacturing operational and risk detectors run only for the `manufacturing` reference pack.
* `OpportunityEngine.run()` (`app/opportunities/engine.py`) calls `MerchantDetector(db).opportunities()`.

Source: `app/domains/merchant/detection.py` (`MerchantDetectionConfig`, `MerchantDetector`). Shared aggregations come from `queries.py`, and "normal" baselines from `baseline.py`.

## Time and windows

All windows use simulated time. `today` is the date of `system_state.data_end`. A window is made of complete days that end the day before today (`Q.window`).

| Name | Span | Used by |
|---|---|---|
| recent 14 / normal 56 | last 14 days / the 56 days before them | velocity, stockout, mismatch, product decline, demand spike |
| recent 21 / normal 56 | last 21 days / the 56 days before them | margin decline, discount leakage |
| recent 28 / previous 28 | last 28 days / the 28 days before | category decline, category and regional opportunity, excess |
| last 30 | last 30 days | slow-moving |
| weekly | last 7 days vs each of the previous 8 weeks | revenue decline |
| repeat | last 28 days vs the 56 days before | repeat-customer decline |

## Shared measures

**In-stock-adjusted velocity.** A day counts as an in-stock day when its inventory snapshot ended with stock or recorded outbound units (`Q.in_stock_days`). Velocity is:

```
v14 = units_14 / max(7,  in_stock_days_14)
v56 = units_56 / max(28, in_stock_days_56)
```

A product that was out of stock for 9 of 14 days is judged on its 5 selling days and not on 14. Without this adjustment a stockout would look like falling demand, and the system would recommend buying less of an item that customers want. The floors (7 and 28) stop one or two in-stock days from producing extreme rates. The product demand-decline rule goes further: it does not run at all unless the item was in stock on at least 10 of the last 14 days and 40 of the previous 56.

**`demand_rate` (normal-rate fallback).** This is the expected sales rate per day used for stock cover and reorder quantities. It is `v14` when the item was in stock on 7 or more of the last 14 days. Otherwise it is `v56`, the normal 8-week rate. An empty shelf sells nothing, but customers still ask for the item. The basis used is stored as `demand_basis`.

**Line-based Poisson tests.** Demand changes are tested on order lines, not units. For a product:

```
lambda = lines_56 / max(28, in_stock_days_56) * max(7, in_stock_days_14)   # expected lines at the normal in-stock rate
spike / mismatch:  p = P(X >= lines_14 | lambda)     (Q.poisson_upper)
decline:           p = P(X <= lines_14 | lambda)     (Q.poisson_lower)
```

For categories and zones, `lambda` is the previous 28-day line count. That window has the same length, and these tests are not adjusted for in-stock days. The tails are exact and computed in log space.

* *Why lines.* Quantities are lumpy. One small business buying 4 cables is one buying decision, not 4 independent events, so a test on units would overstate significance. Order lines are much closer to the independent arrivals that the Poisson model assumes.
* *Why small p.* Each scan tests about 300 SKUs (config comment), so `p <= 0.05` would raise about 15 chance alarms per scan. The thresholds are `spike_max_p = 0.001` and `decline_max_p = 0.0005` for products, `mismatch_max_p = 0.01` (which also needs a stock shortfall), and `opp_max_p = 0.0001` for the roughly 50 category x zone tests per scan.
* A two-rate z-score (`Q.z_rates`) is reported in the metrics for context. No rule uses it as a gate.

**Arrival estimate (`arrival_days`).** `supplier_info` reads the supplier's last 6 deliveries, excluding opening stock:

* `late_share` is the share of those deliveries that were late.
* `late_delay` is the mean delay of the late ones, or 1.0 if none were late.
* `lead_eff = max(quoted lead, mean lead of the last 4 deliveries)`.

For each open purchase order:

* not yet due: `eta = days_to_expected + late_share * late_delay`
* overdue: `eta = max(1, late_delay + days_to_expected, 0.5 * late_delay)`. The order is assumed to be as late as this supplier's late orders usually are.
* no open PO: `eta = days to the supplier's next order weekday + lead_eff`

The earliest ETA is used, and its basis text is stored as `next_stock_basis`.

## Rule catalogue

All thresholds are `MerchantDetectionConfig` defaults (`app/domains/merchant/detection.py`). `p` is a line-based exact Poisson tail. The Impact Engine computes every impact.

| Finding type (dedupe key) | Trigger | Impact (Impact Engine) | Minimum impact | Severity |
|---|---|---|---|---|
| `STOCKOUT_RISK` (`:inventory_id`) | `demand_rate >= 0.4`/day and (`gap = eta - on_hand/demand_rate >= 0.5` d or cover `< 1` d) | `stockout_loss`: rate x gap x unit price (lost margin as a component) | ₹1,000 | CRITICAL `>= ₹25k` or (cover `<= 1` d and `>= ₹5k`); HIGH `>= ₹8k`; MEDIUM `>= ₹3k`; else LOW |
| `DEMAND_SUPPLY_MISMATCH` (`:inventory_id`) | not a stockout product; `units_14 >= 10`; `v14` up `>= 25%` on `v56`; `p <= 0.01`; `(on_hand + on_order)/v14 < lead_eff + 7` | `inventory_risk`: `(v14 x (lead_eff+7) - available) x price` | ₹1,000 (reuses `stockout_min_impact`) | HIGH `>= ₹15k`, else MEDIUM |
| `DEMAND_DECLINE` product (`:product:id`) | `units_56 >= 25`; in stock `>= 10/14` and `>= 40/56` days; `v14` down `>= 40%`; `p <= 0.0005` | `revenue_impact(v56 x 30 x price, v14 x 30 x price)` | none | HIGH `>= ₹30k`, else MEDIUM |
| `DEMAND_DECLINE` category (`:category:name`) | previous 28 d `>= 60` units; units down `>= 25%`; `p <= 0.0005` on lines | `revenue_impact` of 28-day revenue scaled to 30 days | none | HIGH `>= ₹50k`, else MEDIUM |
| `SUPPLIER_RELIABILITY` (`:supplier_id`) | of the last 6 deliveries within 75 days, at least 5 considered and `>= 4` late | `supplier_delay_exposure`: sum over dependent products (`v14 >= 0.15`) of rate x days out of stock before arrival x price | none | HIGH if `>= ₹25k`, or overdue POs exist, or `>= 5` late; else MEDIUM |
| `SUPPLIER_DELAY` (`:supplier_id`) | not unreliable, and at least one PO is overdue by `>= 2` days | same | dropped if `< ₹1,000` and fewer than 2 overdue POs | HIGH `>= ₹25k`; MEDIUM `>= ₹5k`; else LOW |
| `MARGIN_DECLINE` (`:product:id`) | `>= 15` units in each window; recent revenue `>= ₹10,000`; unit margin down `>= 20%` and `>= ₹15` | `margin_loss`: drop per unit x `units_21/21` x 90 d | none | HIGH `>= ₹10k`; MEDIUM `>= ₹3k`; else LOW |
| `PRICE_ANOMALY` (`:product_id`) | not margin-flagged; sold below average cost (`>= 3` units in 14 d) or list margin `< 5%` where category target `>= 15%` | `margin_loss(max(units_14/14, 0.1), price x target, current unit margin)` | none | HIGH if below cost, else MEDIUM |
| `DISCOUNT_LEAKAGE` (`:product:id`) | not margin-flagged; `>= 10` units and `>= ₹10,000` list revenue; rate `>= 8%` and `>= max(2 x normal, normal + 6 pts)` | `discount_impact`: extra rate x list revenue/day x 90 d | none | HIGH `>= ₹10k`, else MEDIUM |
| `EXCESS_INVENTORY` (`:inventory_id`) | cover `= on_hand / max(units_28/28, 1/60) >= 90` d; stock value `>= ₹20,000`; `>= 10` units | `inventory_capital_locked`: excess x unit cost, where excess = on_hand - ceil(v28 x 45); holding cost over 3 months as a component | none | HIGH `>= ₹50k`; MEDIUM `>= ₹20k`; else LOW |
| `SLOW_MOVING_INVENTORY` (`:inventory_id`) | not excess; `<= 2` units sold in 30 d; value `>= ₹8,000`; FIFO age of the oldest unit `>= 75` d | `inventory_capital_locked` on all units on hand | none | MEDIUM if value `>= ₹25k`, else LOW |
| `REVENUE_DECLINE` (`:business`) | last 7 days `>= 20%` below the 8-week weekly mean and `z = (last - mean)/std <= -2.5` | `revenue_impact(normal week, last 7 days)` | none | HIGH if change `<= -30%`, else MEDIUM |
| `REPEAT_CUSTOMER_DECLINE` (`:business`) | `>= 150` recent orders; repeat-order share down `>= 25%` relative and `>= 8` pts | `customer_revenue_exposure`: (share drop x recent orders x 30/28) x AOV x 1 month | none | MEDIUM |
| `DEMAND_OPPORTUNITY` (`:product:id`) | not a stockout product; `units_14 >= 12`; `v14` up `>= 40%`; `p <= 0.001` | `demand_opportunity_value`: (v14 - v56) x 30 d x 0.7 capture x price | none | signal strength from p |
| `CATEGORY_OPPORTUNITY` (`:name`) | recent 28 d `>= 60` units; up `>= 25%`; `p <= 0.0001` | same, with incremental units/day = (recent - previous)/28 | none | signal strength from p |
| `REGIONAL_OPPORTUNITY` (`:category:zone`) | category x city zone: recent `>= 50` units; up `>= 30%`; `p <= 0.0001` | same | none | signal strength from p |

Notes:

* **Supplier reliability vs supplier delay.** A supplier with repeated lateness is reported as `SUPPLIER_RELIABILITY`, which is a pattern, even when nothing is overdue. `SUPPLIER_DELAY` is only for overdue orders from a supplier whose record is otherwise acceptable. One supplier produces at most one of the two.
* **Rule interactions.** `risks()` runs the rules in table order. Products flagged by `MARGIN_DECLINE` are skipped by `PRICE_ANOMALY` and `DISCOUNT_LEAKAGE`. Stockout products are excluded from `DEMAND_SUPPLY_MISMATCH` and `DEMAND_OPPORTUNITY`, because `opportunities()` runs the stockout rule first to know which products to skip. A product with excess stock is not also reported as slow-moving.
* **Opportunity signal strength.** Opportunities have no severity. Their `confidence` field holds detection signal strength taken from the Poisson p-value: HIGH if `p < 1e-6`, MEDIUM if `p < 1e-4`, else LOW. It describes how unusual the change is. It is unrelated to the Evidence Support Score.
* **Regional opportunity.** Customer segments are ranked by their increase in units. The leading segment is stored as `customer_segment`.

## Evidence attached to each finding

`_base()` always adds `domain: "merchant"`. Where they apply, it also adds `product_id`, `category`, `inventory_id`, `store_id`, `store_name` and `supplier_id`. Each rule then adds its `window`, a `rule` string that restates the trigger, and `records`/`trace`:

| Finding | Records |
|---|---|
| Stockout | inventory, 14 daily inventory snapshots, up to 40 order lines (14 d), open purchase orders, the last 4 supplier deliveries; `next_stock_basis` |
| Mismatch | inventory, order lines (14 d), open purchase orders |
| Demand decline | product: order lines (56 d) and inventory; category: order lines of the previous 28 d |
| Supplier | deliveries considered, overdue purchase orders, dependent products (with cover and rate) |
| Margin / discount / price | order lines of the recent window and the product; the trace also names `pricing_history` / `discount` |
| Excess / slow-moving | inventory, FIFO receipt layers (delivery, PO, date, units, notes), order lines |
| Revenue decline | the weekday table (each day vs the mean ± 1 std of the same weekday over 8 weeks), `financial_metric_ids` for the 9 weeks compared and the last 7 days' `order_ids` |
| Repeat decline | the repeat-share metrics, the last 28 days' `order_ids` and `repeat_order_ids` (orders from customers who had bought before) |
| Opportunities | order lines of the recent window (filtered by category / zone), and segment movements for regional |

`affected_metrics` holds the measured values (rates, growth, `p_value`, `z`, cover, lead times) that the UI and the investigation display.

## Persistence

* **Risks.** A finding whose `dedupe_key` matches an open risk updates that risk: severity becomes the higher of the two, and evidence, impact and metrics are refreshed. It also sets `occurrence_count += 1` and the new `detection_run_id`. Open risks that are not re-detected are set to `RESOLVED` with `resolved_at`.
* **Opportunities.** These are matched against non-dismissed opportunities with the same `dedupe_key` and updated in the same way. They are not auto-resolved, and their review status stays as it was.
* Each run writes a `DetectionRun` with a summary of created, updated and resolved findings. The most recent non-reset simulation is linked as `simulation_run_id`.

## Impact Engine

`app/impact/engine.py` is the only place where money is calculated. Neither an LLM nor an agent ever produces a monetary figure. Each `ImpactResult` carries `formula`, `inputs`, `components`, `value`, `currency="INR"`, `is_estimate=True` and `basis="SYNTHETIC"`.

The merchant constants in `ImpactConfig` are:

* `default_horizon_days = 30` (demand opportunities, dependent revenue)
* `recurring_horizon_days = 90` (margin and discount leakage, which persist until the price list is reviewed)
* `holding_cost_months = 3`
* `repeat_order_exposure_months = 1`
* `opportunity_capture_ratio = 0.7`

For how investigations reuse these formulas, see `docs/investigation.md`.
