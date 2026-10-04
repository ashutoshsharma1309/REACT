"""Thresholds for the deterministic detection engines. Everything here is tunable."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectionConfig:
    # ---- sensor analysis ----
    recent_hours: int = 24  # operating hours of readings considered "recent"
    baseline_days: int = 30  # baseline window length
    baseline_gap_days: int = 3  # most recent days excluded from the baseline
    z_threshold: float = 3.0
    min_baseline_points: int = 40
    min_pct_change_for_statistical_anomaly: float = 0.10
    stuck_min_points: int = 8
    implausible_multiple_of_critical: float = 2.2
    dropout_ratio: float = 0.5
    # ---- production analysis ----
    production_recent_days: int = 7
    production_baseline_days: int = 30
    production_drop_pct: float = 0.15
    production_vs_plan_ratio: float = 0.90  # below this, output under-performs the plan (not a planned cut)
    defect_spike_ratio: float = 2.0
    defect_min_rate: float = 0.02
    downtime_hours_threshold_recent: float = 8.0
    downtime_single_day_threshold: float = 4.0
    projected_downtime_hours_if_unaddressed: float = 24.0
    # ---- business risk ----
    compare_days: int = 14
    inventory_cover_days_critical: float = 2.0
    inventory_cover_days_high: float = 5.0
    product_required_ratio: float = 0.5  # available < required * ratio -> shortage
    supplier_lead_time_ratio: float = 1.25
    bottleneck_utilization: float = 0.95
    bottleneck_planned_gap: float = 0.10
    bottleneck_min_days: int = 3
    revenue_decline_pct: float = 0.15
    category_compare_days: int = 28
    category_revenue_decline_pct: float = 0.25
    category_min_revenue_share: float = 0.04
    margin_decline_points: float = 0.02
    product_margin_gap_points: float = 0.06
    product_min_revenue_share: float = 0.01
    delivery_delay_min_orders: int = 3
    late_delivery_ratio: float = 0.15
    demand_supply_gap: float = 0.20
    oversupply_gap: float = 0.35
