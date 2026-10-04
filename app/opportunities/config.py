from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpportunityConfig:
    compare_days: int = 28
    demand_growth_pct: float = 0.30
    growth_significance_z: float = 2.0  # growth must exceed z * standard error of the order-line counts
    growth_high_significance_z: float = 3.0
    demand_growth_high_pct: float = 0.45
    demand_min_orders: int = 8
    regional_demand_growth_pct: float = 0.50
    regional_min_orders: int = 10
    demand_min_previous_orders: int = 6
    demand_min_orders_high: int = 12
    underserved_share_ratio: float = 0.6  # actual regional share < ratio * expected share
    underserved_signal_days: int = 60
    underserved_regional_growth_pct: float = 5.0
    high_margin_growth_pct: float = 0.10
    segment_growth_pct: float = 0.25
    segment_min_orders: int = 15
    excess_cover_days: float = 45.0
    capacity_utilization_ceiling: float = 0.72
    capacity_target_utilization: float = 0.90
    capacity_capture_ratio: float = 0.3
    pricing_competitor_min_increase_pct: float = 4.0
    pricing_signal_days: int = 90
    horizon_days: int = 30
