"""REACT Merchant tables: stores, categories, purchasing, pricing, promotions and expenses.

Together with the reused commerce tables (products, customers, orders, order_items, sales_transactions, inventory,
inventory_snapshots, suppliers, financial_metrics) they describe a small Indian retail business. Every row is produced
by the deterministic merchant simulation and is SYNTHETIC.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models._types import enum_column
from app.models.enums import DiscountScope, ExpenseCategory, PriceChangeReason, PurchaseOrderStatus, StoreType


class Store(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stores"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    store_type: Mapped[StoreType] = mapped_column(enum_column(StoreType), nullable=False, default=StoreType.PHYSICAL)
    locality: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[str] = mapped_column(String(100), nullable=False)  # zone of the city, e.g. "South Bengaluru"
    opened_on: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProductCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "product_categories"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_margin: Mapped[float] = mapped_column(Float, nullable=False)  # 0..1 gross margin the merchant aims for
    description: Mapped[str | None] = mapped_column(Text)


class PurchaseOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "purchase_orders"
    __table_args__ = (Index("ix_purchase_orders_supplier_date", "supplier_id", "order_date"),)

    po_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    order_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    expected_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PurchaseOrderStatus] = mapped_column(enum_column(PurchaseOrderStatus), nullable=False, default=PurchaseOrderStatus.OPEN, index=True)
    total_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(String(300))
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("simulation_runs.id", ondelete="SET NULL"))

    supplier: Mapped["Supplier"] = relationship()  # noqa: F821
    items: Mapped[list["PurchaseOrderItem"]] = relationship(back_populates="purchase_order", cascade="all, delete-orphan")
    deliveries: Mapped[list["SupplierDelivery"]] = relationship(back_populates="purchase_order", cascade="all, delete-orphan")


class PurchaseOrderItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "purchase_order_items"
    __table_args__ = (Index("ix_purchase_order_items_product", "product_id", "purchase_order_id"),)

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    line_cost: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="items")


class SupplierDelivery(Base, UUIDPrimaryKeyMixin):
    """A receipt against a purchase order. Delivery history is the evidence for supplier reliability."""

    __tablename__ = "supplier_deliveries"
    __table_args__ = (Index("ix_supplier_deliveries_supplier_date", "supplier_id", "delivered_date"),)

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    expected_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivered_date: Mapped[date] = mapped_column(Date, nullable=False)
    delay_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_time: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    units_received: Mapped[int] = mapped_column(Integer, nullable=False)

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="deliveries")


class PricingHistory(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "pricing_history"
    __table_args__ = (Index("ix_pricing_history_product_date", "product_id", "effective_date"),)

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    selling_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    mrp: Mapped[float | None] = mapped_column(Numeric(12, 2))
    reason: Mapped[PriceChangeReason] = mapped_column(enum_column(PriceChangeReason), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))


class Discount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A promotion or standing discount rule. Order lines that used it reference it (order_items.discount_id)."""

    __tablename__ = "discounts"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[DiscountScope] = mapped_column(enum_column(DiscountScope), nullable=False)
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False)  # 0..1
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product_categories.id", ondelete="CASCADE"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str | None] = mapped_column(String(20))
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("simulation_runs.id", ondelete="SET NULL"))


class Expense(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "expenses"
    __table_args__ = (Index("ix_expenses_company_date", "company_id", "expense_date"),)

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    store_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stores.id", ondelete="SET NULL"))
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[ExpenseCategory] = mapped_column(enum_column(ExpenseCategory), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    is_recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
