"""Deterministic impact engine.

All monetary estimates in REACT come from these formulas and ONLY from these formulas.
Later phases must call this engine; an LLM is never allowed to compute impact figures.

Every result records the formula used and the inputs, so it is auditable and traceable.
All values are SYNTHETIC estimates (is_estimate=True, basis="SYNTHETIC").
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ImpactConfig:
    """Tunable constants. Override via ImpactEngine(ImpactConfig(...))."""

    currency: str = "INR"
    # Fallback when a production line has no computed hourly margin
    default_hourly_loss: float = 45_000.0
    # Fraction of a line's daily margin lost per hour of downtime is derived from capacity; this caps it
    max_hourly_loss: float = 400_000.0
    # Cost multiplier applied to each defective unit (scrap + rework) relative to unit cost
    defect_cost_multiplier: float = 1.15
    # Expected margin uplift for a pricing opportunity, as fraction of current margin gap closed
    pricing_capture_ratio: float = 0.5
    # Fraction of excess inventory that could realistically be redirected
    excess_inventory_redirect_ratio: float = 0.6
    # Expedite premium applied to component unit cost when mitigating supplier delay
    expedite_premium_ratio: float = 0.25
    # Fraction of at-risk revenue lost when orders are delivered late (churn / penalties)
    late_delivery_penalty_ratio: float = 0.05
    # Phase 2: share of affected customers' annualised revenue considered at risk after a service failure
    customer_churn_exposure_ratio: float = 0.10
    # Phase 2: fraction of projected incremental demand a business can realistically capture
    opportunity_capture_ratio: float = 0.7
    # REACT Merchant
    default_horizon_days: int = 30  # projection window for demand opportunities
    recurring_horizon_days: int = 90  # margin and discount leakage persist until the price list is reviewed: one quarter
    holding_cost_months: int = 3  # carrying-cost horizon for locked inventory
    repeat_order_exposure_months: int = 1


@dataclass
class ImpactResult:
    formula: str
    inputs: dict[str, Any]
    value: float
    unit: str
    description: str
    currency: str = "INR"
    is_estimate: bool = True
    basis: str = "SYNTHETIC"
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["value"] = round(self.value, 2)
        d["components"] = {k: round(v, 2) for k, v in self.components.items()}
        return d


class ImpactEngine:
    def __init__(self, config: ImpactConfig | None = None) -> None:
        self.config = config or ImpactConfig()

    # ---- Core formulas (spec) ------------------------------------------------------------
    def production_loss(self, lost_units: float, unit_margin: float) -> ImpactResult:
        """Estimated production loss = lost_units × unit_margin."""
        lost_units = max(0.0, float(lost_units))
        value = lost_units * float(unit_margin)
        return ImpactResult(
            formula="production_loss = lost_units * unit_margin",
            inputs={"lost_units": round(lost_units, 2), "unit_margin": round(float(unit_margin), 2)},
            value=value,
            unit=self.config.currency,
            currency=self.config.currency,
            description=f"{lost_units:.0f} units of lost output at {unit_margin:.2f} margin per unit",
        )

    def revenue_impact(self, expected_revenue: float, actual_revenue: float) -> ImpactResult:
        """Revenue impact = expected_revenue - actual_revenue (positive = shortfall)."""
        value = float(expected_revenue) - float(actual_revenue)
        return ImpactResult(
            formula="revenue_impact = expected_revenue - actual_revenue",
            inputs={"expected_revenue": round(float(expected_revenue), 2), "actual_revenue": round(float(actual_revenue), 2)},
            value=value,
            unit=self.config.currency,
            currency=self.config.currency,
            description="Revenue shortfall versus expected" if value >= 0 else "Revenue above expected",
        )

    def inventory_risk(self, required_quantity: float, available_quantity: float, unit_value: float = 0.0) -> ImpactResult:
        """Inventory risk (units) = required_quantity - available_quantity. Value = shortfall × unit_value."""
        shortfall = max(0.0, float(required_quantity) - float(available_quantity))
        return ImpactResult(
            formula="inventory_shortfall_units = required_quantity - available_quantity; value = shortfall * unit_value",
            inputs={
                "required_quantity": round(float(required_quantity), 2),
                "available_quantity": round(float(available_quantity), 2),
                "unit_value": round(float(unit_value), 2),
            },
            value=shortfall * float(unit_value),
            unit=self.config.currency,
            currency=self.config.currency,
            description=f"Shortfall of {shortfall:.0f} units",
            components={"shortfall_units": shortfall},
        )

    def downtime_impact(self, downtime_hours: float, estimated_hourly_loss: float | None = None) -> ImpactResult:
        """Downtime impact = downtime_hours × estimated_hourly_loss."""
        hourly = float(estimated_hourly_loss) if estimated_hourly_loss is not None else self.config.default_hourly_loss
        hourly = min(hourly, self.config.max_hourly_loss)
        value = max(0.0, float(downtime_hours)) * hourly
        return ImpactResult(
            formula="downtime_impact = downtime_hours * estimated_hourly_loss",
            inputs={"downtime_hours": round(float(downtime_hours), 2), "estimated_hourly_loss": round(hourly, 2)},
            value=value,
            unit=self.config.currency,
            currency=self.config.currency,
            description=f"{downtime_hours:.1f}h of downtime at {hourly:,.0f}/h",
        )

    # ---- Derived helpers -----------------------------------------------------------------
    def hourly_loss_for_line(self, capacity_units_per_day: int, avg_unit_margin: float, operating_hours_per_day: float = 16.0) -> float:
        if operating_hours_per_day <= 0:
            return self.config.default_hourly_loss
        return min(self.config.max_hourly_loss, capacity_units_per_day * avg_unit_margin / operating_hours_per_day)

    def defect_cost(self, extra_defective_units: float, unit_cost: float) -> ImpactResult:
        value = max(0.0, float(extra_defective_units)) * float(unit_cost) * self.config.defect_cost_multiplier
        return ImpactResult(
            formula="defect_cost = extra_defective_units * unit_cost * defect_cost_multiplier",
            inputs={
                "extra_defective_units": round(float(extra_defective_units), 2),
                "unit_cost": round(float(unit_cost), 2),
                "defect_cost_multiplier": self.config.defect_cost_multiplier,
            },
            value=value,
            unit=self.config.currency,
            currency=self.config.currency,
            description=f"{extra_defective_units:.0f} additional defective units (scrap + rework)",
        )

    def machine_degradation_impact(
        self, lost_units: float, unit_margin: float, extra_defects: float, unit_cost: float, projected_downtime_hours: float, hourly_loss: float
    ) -> ImpactResult:
        loss = self.production_loss(lost_units, unit_margin)
        defects = self.defect_cost(extra_defects, unit_cost)
        downtime = self.downtime_impact(projected_downtime_hours, hourly_loss)
        total = loss.value + defects.value + downtime.value
        return ImpactResult(
            formula="degradation_impact = production_loss + defect_cost + projected_downtime_impact",
            inputs={**loss.inputs, **defects.inputs, "projected_downtime_hours": projected_downtime_hours, "hourly_loss": round(hourly_loss, 2)},
            value=total,
            unit=self.config.currency,
            currency=self.config.currency,
            description="Realised output/defect losses plus projected downtime if unaddressed",
            components={"production_loss": loss.value, "defect_cost": defects.value, "projected_downtime_impact": downtime.value},
        )

    def supplier_delay_impact(self, shortfall_units: float, unit_cost: float, at_risk_revenue: float) -> ImpactResult:
        expedite = max(0.0, shortfall_units) * unit_cost * self.config.expedite_premium_ratio
        penalty = max(0.0, at_risk_revenue) * self.config.late_delivery_penalty_ratio
        return ImpactResult(
            formula="supplier_delay_impact = shortfall_units * unit_cost * expedite_premium_ratio + at_risk_revenue * late_delivery_penalty_ratio",
            inputs={
                "shortfall_units": round(shortfall_units, 2),
                "unit_cost": round(unit_cost, 2),
                "at_risk_revenue": round(at_risk_revenue, 2),
                "expedite_premium_ratio": self.config.expedite_premium_ratio,
                "late_delivery_penalty_ratio": self.config.late_delivery_penalty_ratio,
            },
            value=expedite + penalty,
            unit=self.config.currency,
            currency=self.config.currency,
            description="Expedite premium on shortfall plus penalty exposure on at-risk revenue",
            components={"expedite_cost": expedite, "late_delivery_exposure": penalty, "at_risk_revenue": at_risk_revenue},
        )

    def late_delivery_impact(self, at_risk_revenue: float) -> ImpactResult:
        value = max(0.0, at_risk_revenue) * self.config.late_delivery_penalty_ratio
        return ImpactResult(
            formula="late_delivery_impact = at_risk_revenue * late_delivery_penalty_ratio",
            inputs={"at_risk_revenue": round(at_risk_revenue, 2), "late_delivery_penalty_ratio": self.config.late_delivery_penalty_ratio},
            value=value,
            unit=self.config.currency,
            currency=self.config.currency,
            description="Penalty / churn exposure on late orders",
            components={"at_risk_revenue": at_risk_revenue},
        )

    def opportunity_value(self, incremental_units: float, unit_margin: float, horizon_days: int = 30) -> ImpactResult:
        value = max(0.0, incremental_units) * unit_margin
        return ImpactResult(
            formula="opportunity_value = incremental_units * unit_margin (over horizon_days)",
            inputs={"incremental_units": round(incremental_units, 2), "unit_margin": round(unit_margin, 2), "horizon_days": horizon_days},
            value=value,
            unit=self.config.currency,
            currency=self.config.currency,
            description=f"Incremental margin over the next {horizon_days} days if demand is captured",
        )

    def pricing_opportunity_value(self, monthly_units: float, current_price: float, unit_cost: float, target_margin: float) -> ImpactResult:
        current_margin = (current_price - unit_cost) / current_price if current_price else 0.0
        gap = max(0.0, target_margin - current_margin)
        # price needed to hit target margin: p = cost / (1 - target)
        target_price = unit_cost / (1 - target_margin) if target_margin < 1 else current_price
        uplift_per_unit = (target_price - current_price) * self.config.pricing_capture_ratio
        value = max(0.0, uplift_per_unit) * monthly_units
        return ImpactResult(
            formula="pricing_value = monthly_units * (target_price - current_price) * pricing_capture_ratio",
            inputs={
                "monthly_units": round(monthly_units, 2),
                "current_price": round(current_price, 2),
                "unit_cost": round(unit_cost, 2),
                "target_margin": target_margin,
                "current_margin": round(current_margin, 4),
                "target_price": round(target_price, 2),
                "pricing_capture_ratio": self.config.pricing_capture_ratio,
            },
            value=value,
            unit=self.config.currency,
            currency=self.config.currency,
            description=f"Margin gap of {gap * 100:.1f} pts could be partially closed by repricing",
            components={"margin_gap": gap},
        )

    def excess_inventory_value(self, excess_units: float, unit_value: float, holding_cost_per_unit_month: float) -> ImpactResult:
        redirectable = excess_units * self.config.excess_inventory_redirect_ratio
        holding_saved = excess_units * holding_cost_per_unit_month
        value = redirectable * unit_value + holding_saved
        return ImpactResult(
            formula="excess_value = excess_units * redirect_ratio * unit_value + excess_units * holding_cost_per_unit_month",
            inputs={
                "excess_units": round(excess_units, 2),
                "unit_value": round(unit_value, 2),
                "holding_cost_per_unit_month": round(holding_cost_per_unit_month, 2),
                "redirect_ratio": self.config.excess_inventory_redirect_ratio,
            },
            value=value,
            unit=self.config.currency,
            currency=self.config.currency,
            description="Working capital that could be released plus monthly holding cost avoided",
            components={"redirectable_value": redirectable * unit_value, "monthly_holding_cost_saved": holding_saved},
        )

    # ---- Phase 2 additions: investigation impact analysis ---------------------------------
    def revenue_loss(self, lost_units: float, unit_price: float) -> ImpactResult:
        """Estimated revenue loss = lost_units × unit_price (sales that cannot be fulfilled from lost output)."""
        value = max(0.0, float(lost_units)) * float(unit_price)
        return ImpactResult(
            formula="revenue_loss = lost_units * unit_price",
            inputs={"lost_units": round(float(lost_units), 2), "unit_price": round(float(unit_price), 2)},
            value=value, unit=self.config.currency, currency=self.config.currency,
            description=f"Estimated revenue not realised on {lost_units:.0f} units of lost output",
        )

    def delivery_impact(self, orders_at_risk: int, at_risk_revenue: float, avg_days_late: float) -> ImpactResult:
        penalty = self.late_delivery_impact(at_risk_revenue)
        return ImpactResult(
            formula="delivery_impact = at_risk_revenue * late_delivery_penalty_ratio (orders_at_risk, avg_days_late reported)",
            inputs={"orders_at_risk": orders_at_risk, "at_risk_revenue": round(at_risk_revenue, 2), "avg_days_late": round(avg_days_late, 1), "late_delivery_penalty_ratio": self.config.late_delivery_penalty_ratio},
            value=penalty.value, unit=self.config.currency, currency=self.config.currency,
            description=f"{orders_at_risk} order(s) at risk of late delivery",
            components={"at_risk_revenue": at_risk_revenue},
        )

    def customer_impact(self, customers_affected: int, affected_customer_revenue: float) -> ImpactResult:
        value = max(0.0, affected_customer_revenue) * self.config.customer_churn_exposure_ratio
        return ImpactResult(
            formula="customer_impact = affected_customer_revenue * customer_churn_exposure_ratio",
            inputs={"customers_affected": customers_affected, "affected_customer_revenue": round(affected_customer_revenue, 2), "customer_churn_exposure_ratio": self.config.customer_churn_exposure_ratio},
            value=value, unit=self.config.currency, currency=self.config.currency,
            description=f"Churn exposure across {customers_affected} affected customer(s)",
        )

    def inventory_impact(self, days_of_cover_before: float, days_of_cover_after: float, avg_daily_demand: float, unit_value: float) -> ImpactResult:
        lost_cover = max(0.0, days_of_cover_before - days_of_cover_after)
        units = lost_cover * avg_daily_demand
        return ImpactResult(
            formula="inventory_impact = (days_of_cover_before - days_of_cover_after) * avg_daily_demand * unit_value",
            inputs={"days_of_cover_before": round(days_of_cover_before, 1), "days_of_cover_after": round(days_of_cover_after, 1), "avg_daily_demand": round(avg_daily_demand, 2), "unit_value": round(unit_value, 2)},
            value=units * unit_value, unit=self.config.currency, currency=self.config.currency,
            description=f"Stock cover reduced by {lost_cover:.1f} days (~{units:.0f} units)",
            components={"lost_cover_days": lost_cover, "lost_cover_units": units},
        )

    def opportunity_revenue(self, incremental_units: float, unit_price: float, unit_margin: float, horizon_days: int = 30) -> ImpactResult:
        captured = max(0.0, incremental_units) * self.config.opportunity_capture_ratio
        revenue = captured * unit_price
        margin = captured * unit_margin
        return ImpactResult(
            formula="potential_revenue = incremental_units * opportunity_capture_ratio * unit_price; potential_margin = ... * unit_margin",
            inputs={"incremental_units": round(incremental_units, 2), "opportunity_capture_ratio": self.config.opportunity_capture_ratio, "unit_price": round(unit_price, 2), "unit_margin": round(unit_margin, 2), "horizon_days": horizon_days},
            value=revenue, unit=self.config.currency, currency=self.config.currency,
            description=f"Potential additional revenue over {horizon_days} days if {self.config.opportunity_capture_ratio:.0%} of incremental demand is captured",
            components={"potential_revenue": revenue, "potential_margin": margin, "captured_units": captured},
        )

    def inventory_requirement(self, demand_units: float, on_hand_units: float, planned_production_units: float, unit_cost: float) -> ImpactResult:
        gap = max(0.0, demand_units - on_hand_units - planned_production_units)
        return ImpactResult(
            formula="inventory_requirement = max(0, demand_units - on_hand_units - planned_production_units); value = gap * unit_cost",
            inputs={"demand_units": round(demand_units, 2), "on_hand_units": round(on_hand_units, 2), "planned_production_units": round(planned_production_units, 2), "unit_cost": round(unit_cost, 2)},
            value=gap * unit_cost, unit=self.config.currency, currency=self.config.currency,
            description=f"{gap:.0f} additional units must be produced or sourced to serve the demand",
            components={"gap_units": gap},
        )


    # ---- REACT Merchant formulas ----------------------------------------------------------------------------------
    def stockout_loss(self, units_per_day: float, gap_days: float, unit_price: float, unit_margin: float) -> ImpactResult:
        """Sales lost while a product is out of stock before the next delivery can arrive."""
        lost = max(0.0, float(units_per_day)) * max(0.0, float(gap_days))
        return ImpactResult(
            formula="lost_sales = units_per_day * days_without_stock * unit_price; lost_margin = lost_units * unit_margin",
            inputs={"units_per_day": round(float(units_per_day), 2), "days_without_stock": round(float(gap_days), 1), "unit_price": round(float(unit_price), 2), "unit_margin": round(float(unit_margin), 2)},
            value=lost * float(unit_price), unit=self.config.currency, currency=self.config.currency,
            description=f"About {lost:.0f} units of sales lost over {gap_days:.1f} days without stock",
            components={"lost_units": lost, "lost_margin": lost * float(unit_margin)},
        )

    def supplier_delay_exposure(self, items: list[dict[str, float]]) -> ImpactResult:
        """Revenue exposed by late deliveries: per product, units/day x days it would be out of stock x unit price."""
        lost_units = sum(max(0.0, i["units_per_day"]) * max(0.0, i["gap_days"]) for i in items)
        value = sum(max(0.0, i["units_per_day"]) * max(0.0, i["gap_days"]) * i["unit_price"] for i in items)
        margin = sum(max(0.0, i["units_per_day"]) * max(0.0, i["gap_days"]) * i["unit_margin"] for i in items)
        return ImpactResult(
            formula="exposure = sum over dependent products (units_per_day * days_out_of_stock_during_delay * unit_price)",
            inputs={"products": len(items), "items": [{k: round(v, 2) if isinstance(v, float) else v for k, v in i.items()} for i in items[:20]]},
            value=value, unit=self.config.currency, currency=self.config.currency,
            description=f"{lost_units:.0f} units of sales at risk across {len(items)} dependent products",
            components={"lost_units": lost_units, "lost_margin": margin},
        )

    def margin_loss(self, units_per_day: float, baseline_unit_margin: float, current_unit_margin: float, horizon_days: int | None = None) -> ImpactResult:
        """Margin given up if the lower unit margin continues for a quarter: drop per unit x units per day x horizon."""
        h = horizon_days or self.config.recurring_horizon_days
        drop = max(0.0, float(baseline_unit_margin) - float(current_unit_margin))
        return ImpactResult(
            formula="margin_loss = (normal_unit_margin - current_unit_margin) * units_per_day * horizon_days",
            inputs={"normal_unit_margin": round(float(baseline_unit_margin), 2), "current_unit_margin": round(float(current_unit_margin), 2), "units_per_day": round(float(units_per_day), 2), "horizon_days": h},
            value=drop * float(units_per_day) * h, unit=self.config.currency, currency=self.config.currency,
            description=f"₹{drop:,.0f} less margin per unit on about {units_per_day * h:.0f} units over {h} days",
            components={"margin_drop_per_unit": drop, "units_over_horizon": float(units_per_day) * h},
        )

    def discount_impact(self, list_revenue_per_day: float, baseline_rate: float, current_rate: float, horizon_days: int | None = None) -> ImpactResult:
        """Extra discount given away versus the merchant's normal discount rate, over a quarter at the current run-rate."""
        h = horizon_days or self.config.recurring_horizon_days
        extra = max(0.0, float(current_rate) - float(baseline_rate))
        return ImpactResult(
            formula="discount_impact = (current_discount_rate - normal_discount_rate) * list_revenue_per_day * horizon_days",
            inputs={"current_discount_rate": round(float(current_rate), 4), "normal_discount_rate": round(float(baseline_rate), 4), "list_revenue_per_day": round(float(list_revenue_per_day), 2), "horizon_days": h},
            value=extra * float(list_revenue_per_day) * h, unit=self.config.currency, currency=self.config.currency,
            description=f"{extra * 100:.1f} percentage points more discount than normal over {h} days",
            components={"extra_discount_rate": extra},
        )

    def inventory_capital_locked(self, excess_units: float, unit_cost: float, holding_cost_per_unit_month: float) -> ImpactResult:
        """Cash tied up in stock beyond what the next weeks of demand need, plus its carrying cost."""
        units = max(0.0, float(excess_units))
        months = self.config.holding_cost_months
        holding = units * float(holding_cost_per_unit_month) * months
        return ImpactResult(
            formula="capital_locked = excess_units * unit_cost; holding_cost = excess_units * holding_cost_per_unit_month * months",
            inputs={"excess_units": round(units, 1), "unit_cost": round(float(unit_cost), 2), "holding_cost_per_unit_month": round(float(holding_cost_per_unit_month), 2), "months": months},
            value=units * float(unit_cost), unit=self.config.currency, currency=self.config.currency,
            description=f"{units:.0f} surplus units at ₹{unit_cost:,.0f} cost each",
            components={"holding_cost": holding},
        )

    def customer_revenue_exposure(self, lost_repeat_orders_per_month: float, average_order_value: float) -> ImpactResult:
        months = self.config.repeat_order_exposure_months
        value = max(0.0, float(lost_repeat_orders_per_month)) * float(average_order_value) * months
        return ImpactResult(
            formula="exposure = fewer_repeat_orders_per_month * average_order_value * months",
            inputs={"fewer_repeat_orders_per_month": round(float(lost_repeat_orders_per_month), 1), "average_order_value": round(float(average_order_value), 2), "months": months},
            value=value, unit=self.config.currency, currency=self.config.currency,
            description=f"About {lost_repeat_orders_per_month:.0f} fewer repeat orders a month",
        )

    def demand_opportunity_value(self, incremental_units_per_day: float, unit_price: float, unit_margin: float, horizon_days: int | None = None) -> ImpactResult:
        """Extra revenue if the merchant keeps serving the higher demand (capture ratio applied)."""
        h = horizon_days or self.config.default_horizon_days
        units = max(0.0, float(incremental_units_per_day)) * h * self.config.opportunity_capture_ratio
        return ImpactResult(
            formula="potential_revenue = incremental_units_per_day * horizon_days * capture_ratio * unit_price; potential_margin = same units * unit_margin",
            inputs={"incremental_units_per_day": round(float(incremental_units_per_day), 2), "horizon_days": h, "capture_ratio": self.config.opportunity_capture_ratio, "unit_price": round(float(unit_price), 2), "unit_margin": round(float(unit_margin), 2)},
            value=units * float(unit_price), unit=self.config.currency, currency=self.config.currency,
            description=f"About {units:.0f} extra units over {h} days if stock keeps up",
            components={"potential_units": units, "potential_margin": units * float(unit_margin)},
        )

    def dependent_revenue(self, revenue_per_day: float, days: int | None = None) -> ImpactResult:
        """Revenue that depends on one supplier over the default horizon (exposure, not a loss)."""
        h = days or self.config.default_horizon_days
        return ImpactResult(
            formula="dependent_revenue = revenue_per_day_of_dependent_products * horizon_days",
            inputs={"revenue_per_day": round(float(revenue_per_day), 2), "horizon_days": h},
            value=max(0.0, float(revenue_per_day)) * h, unit=self.config.currency, currency=self.config.currency,
            description=f"Sales of products sourced from this supplier over {h} days",
        )

    def purchase_order_value(self, quantity: float, unit_cost: float) -> ImpactResult:
        return ImpactResult(
            formula="purchase_value = quantity * unit_cost",
            inputs={"quantity": round(float(quantity), 1), "unit_cost": round(float(unit_cost), 2)},
            value=max(0.0, float(quantity)) * float(unit_cost), unit=self.config.currency, currency=self.config.currency,
            description=f"{quantity:.0f} units at ₹{unit_cost:,.0f}",
        )

    def price_to_restore_margin(self, unit_cost: float, target_unit_margin: float, current_price: float) -> ImpactResult:
        """Shelf price that restores the merchant's normal margin per unit at the current cost (a suggestion, not a change)."""
        import math as _math

        price = float(_math.ceil(float(unit_cost) + float(target_unit_margin)))
        return ImpactResult(
            formula="suggested_price = ceil(current_unit_cost + normal_unit_margin)",
            inputs={"current_unit_cost": round(float(unit_cost), 2), "normal_unit_margin": round(float(target_unit_margin), 2), "current_price": round(float(current_price), 2)},
            value=price, unit=self.config.currency, currency=self.config.currency,
            description=f"Price of ₹{price:,.0f} restores the normal margin (now ₹{current_price:,.0f})",
            components={"price_change": price - float(current_price)},
        )


impact_engine = ImpactEngine()
