from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class HealthOut(BaseModel):
    status: str
    app: str
    version: str
    database: str
    timestamp: datetime


class SystemStatusOut(BaseModel):
    database_status: str
    seeded: bool
    simulator_status: str
    detection_engine_status: str
    opportunity_engine_status: str
    total_incidents: int
    active_incidents: int
    total_risks: int
    active_risks: int
    opportunities: int
    new_opportunities: int
    last_simulation: dict[str, Any] | None
    last_detection_run: dict[str, Any] | None
    last_opportunity_run: dict[str, Any] | None
    data_window: dict[str, Any] | None
    record_counts: dict[str, int]
    phase: str = "1"
    llm_agents_enabled: bool = False


class DailyMetricOut(BaseModel):
    date: date
    revenue: float
    cost: float
    gross_margin: float
    operating_cost: float
    production_cost: float
    profit: float
    orders_count: int
    units_sold: int


class LineUtilizationOut(BaseModel):
    production_line_id: uuid.UUID
    code: str
    name: str
    capacity_units_per_day: int
    avg_utilization: float
    avg_output_units: float
    avg_defect_rate: float
    total_downtime_hours: float
    status: str


class BusinessMetricsOut(BaseModel):
    period_days: int
    window_start: date
    window_end: date
    currency: str
    revenue: float
    cost: float
    gross_margin: float
    operating_cost: float
    production_cost: float
    profit: float
    orders: int
    units_sold: int
    previous_period: dict[str, float]
    change_pct: dict[str, float]
    revenue_by_category: list[dict[str, Any]]
    revenue_by_region: list[dict[str, Any]]
    line_utilization: list[LineUtilizationOut]
    daily: list[DailyMetricOut]
    notice: str = "SYNTHETIC data - Astra Manufacturing is a fictional company."
