"""Response schemas for business entities."""
from __future__ import annotations

from typing import Any

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from app.schemas.common import ORMModel


class CompanyOut(ORMModel):
    id: uuid.UUID
    name: str
    industry: str
    headquarters: str
    currency: str
    created_at: datetime


class PlantOut(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    location: str
    region: str
    operational_status: str


class ProductionLineOut(ORMModel):
    id: uuid.UUID
    plant_id: uuid.UUID
    name: str
    code: str
    capacity_units_per_day: int
    status: str
    description: str | None = None
    machine_count: int = 0


class MachineComponentOut(ORMModel):
    id: uuid.UUID
    name: str
    component_type: str
    part_number: str
    installed_on: date
    expected_life_hours: int
    operating_hours: int
    condition: str
    condition_score: float
    failure_probability: float


class SensorOut(ORMModel):
    id: uuid.UUID
    machine_id: uuid.UUID
    code: str
    sensor_type: str
    unit: str
    normal_min: float
    normal_max: float
    warning_threshold: float
    critical_threshold: float
    status: str


class MachineOut(ORMModel):
    id: uuid.UUID
    production_line_id: uuid.UUID
    name: str
    code: str
    machine_type: str
    manufacturer: str
    model: str
    installation_date: date
    criticality: str
    status: str
    rated_power_kw: float
    health_score: float


class MachineDetailOut(MachineOut):
    components: list[MachineComponentOut] = []
    sensors: list[SensorOut] = []
    production_line_name: str | None = None


class SensorReadingOut(ORMModel):
    id: int
    timestamp: datetime
    value: float
    quality: str


class SensorReadingsOut(ORMModel):
    sensor: SensorOut
    count: int
    readings: list[SensorReadingOut]


class MachineReadingsOut(ORMModel):
    machine_id: uuid.UUID
    machine_code: str
    window_start: datetime
    window_end: datetime
    sensors: list[SensorReadingsOut]


class ProductOut(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    production_line_id: uuid.UUID | None
    sku: str
    name: str
    category: str
    unit_cost: Decimal
    selling_price: Decimal
    target_margin: float
    is_active: bool
    # REACT Merchant (absent for the manufacturing reference pack)
    brand: str | None = None
    mrp: float | None = None
    supplier: str | None = None
    supplier_id: uuid.UUID | None = None
    margin_rate_at_price: float | None = None
    units_28d: int | None = None
    revenue_28d: float | None = None
    gross_margin_28d: float | None = None
    demand_change_pct: float | None = None
    stock_on_hand: int | None = None
    merchant_days_of_cover: float | None = None
    findings: list[dict[str, Any]] = []


class CustomerOut(ORMModel):
    id: uuid.UUID
    name: str
    industry: str
    location: str
    region: str
    segment: str
    customer_since: date
    average_order_value: Decimal
    credit_rating: str
    locality: str | None = None
    acquisition_channel: str | None = None


class OrderItemOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    unit_cost: Decimal
    line_revenue: Decimal


class OrderOut(ORMModel):
    id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID
    order_date: date
    promised_delivery_date: date
    actual_delivery_date: date | None
    status: str
    region: str
    total_quantity: int
    total_revenue: Decimal
    total_cost: Decimal
    simulation_run_id: uuid.UUID | None = None
    items: list[OrderItemOut] = []


class InventoryOut(ORMModel):
    id: uuid.UUID
    item_type: str
    product_id: uuid.UUID | None
    component_type: str | None
    item_name: str
    warehouse: str
    quantity_available: int
    quantity_reserved: int
    reorder_point: int
    safety_stock: int
    max_stock: int
    holding_cost_per_unit_month: Decimal
    unit_value: Decimal
    avg_daily_consumption: float
    days_of_cover: float = Field(default=0.0)
    last_updated: datetime
    # REACT Merchant
    store: str | None = None
    sku: str | None = None
    category: str | None = None
    on_order: int | None = None
    stock_value: float | None = None
    open_purchase_orders: int | None = None
    overdue_purchase_orders: int | None = None
    supplier: str | None = None
    status: str | None = None
    findings: list[dict[str, Any]] = []


class SupplierBatchOut(ORMModel):
    id: uuid.UUID
    batch_number: str
    component_type: str
    quantity: int
    ordered_date: date
    expected_date: date
    received_date: date | None
    status: str
    delay_days: int
    quality_pass_rate: float | None


class SupplierOut(ORMModel):
    id: uuid.UUID
    name: str
    component_type: str
    location: str
    country: str
    reliability_score: float
    lead_time_days: int
    baseline_lead_time_days: int
    unit_cost: Decimal
    status: str
    is_primary: bool
    open_batches: list[SupplierBatchOut] = []
    # REACT Merchant
    recent_lead_days: float | None = None
    normal_lead_days: float | None = None
    on_time_rate_recent: float | None = None
    on_time_rate_normal: float | None = None
    late_of_last_6: int | None = None
    deliveries_considered: int | None = None
    open_purchase_orders: int | None = None
    overdue_purchase_orders: int | None = None
    products: int | None = None
    revenue_28d: float | None = None
    findings: list[dict[str, Any]] = []
