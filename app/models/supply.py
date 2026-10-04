from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models._types import enum_column
from app.models.enums import BatchStatus, InventoryItemType, SupplierStatus


class Supplier(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "suppliers"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    component_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)  # component supplied
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=False, default="India")
    reliability_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0..1 on-time & quality
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[SupplierStatus] = mapped_column(enum_column(SupplierStatus), nullable=False, default=SupplierStatus.ACTIVE)
    is_primary: Mapped[bool] = mapped_column(nullable=False, default=True)

    batches: Mapped[list["SupplierBatch"]] = relationship(back_populates="supplier", cascade="all, delete-orphan")


class SupplierBatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "supplier_batches"
    __table_args__ = (Index("ix_supplier_batches_supplier_expected", "supplier_id", "expected_date"),)

    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    component_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    ordered_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_date: Mapped[date] = mapped_column(Date, nullable=False)
    received_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[BatchStatus] = mapped_column(enum_column(BatchStatus), nullable=False, default=BatchStatus.ORDERED)
    quality_pass_rate: Mapped[float | None] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    delay_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    supplier: Mapped[Supplier] = relationship(back_populates="batches")


class Inventory(Base, UUIDPrimaryKeyMixin):
    """Current stock position per item and warehouse."""

    __tablename__ = "inventory"
    __table_args__ = (Index("ix_inventory_item", "item_type", "product_id", "component_type", "warehouse", unique=True),)

    item_type: Mapped[InventoryItemType] = mapped_column(enum_column(InventoryItemType), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    component_type: Mapped[str | None] = mapped_column(String(80), index=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    warehouse: Mapped[str] = mapped_column(String(120), nullable=False)
    quantity_available: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False)
    safety_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    max_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    holding_cost_per_unit_month: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit_value: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    avg_daily_consumption: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    store_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)  # REACT Merchant
    last_updated: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    product: Mapped["Product | None"] = relationship()  # noqa: F821
    snapshots: Mapped[list["InventorySnapshot"]] = relationship(back_populates="inventory", cascade="all, delete-orphan")


class InventorySnapshot(Base, UUIDPrimaryKeyMixin):
    """Daily history of stock levels (evidence for shortage / excess detection)."""

    __tablename__ = "inventory_snapshots"
    __table_args__ = (Index("ix_inventory_snapshots_item_date", "inventory_id", "snapshot_date", unique=True),)

    inventory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory.id", ondelete="CASCADE"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_available: Mapped[int] = mapped_column(Integer, nullable=False)
    inbound_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outbound_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    inventory: Mapped[Inventory] = relationship(back_populates="snapshots")
