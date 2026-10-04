from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models._types import enum_column
from app.models.enums import CustomerSegment, OrderStatus, PaymentStatus


class Product(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "products"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    production_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("production_lines.id", ondelete="SET NULL"), index=True)
    sku: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    selling_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    target_margin: Mapped[float] = mapped_column(Float, nullable=False)  # 0..1
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # REACT Merchant
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product_categories.id", ondelete="SET NULL"), index=True)
    brand: Mapped[str | None] = mapped_column(String(80))
    mrp: Mapped[float | None] = mapped_column(Numeric(12, 2))  # maximum retail price printed on the pack
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"), index=True)  # primary supplier

    production_line: Mapped["ProductionLine | None"] = relationship()  # noqa: F821


class Customer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "customers"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)  # city, state
    region: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    segment: Mapped[CustomerSegment] = mapped_column(enum_column(CustomerSegment), nullable=False)
    customer_since: Mapped[date] = mapped_column(Date, nullable=False)
    average_order_value: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    credit_rating: Mapped[str] = mapped_column(String(5), nullable=False, default="A")
    # REACT Merchant
    locality: Mapped[str | None] = mapped_column(String(100))
    acquisition_channel: Mapped[str | None] = mapped_column(String(20))


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_customer_date", "customer_id", "order_date"),)

    order_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    promised_delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_delivery_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus), nullable=False, default=OrderStatus.PENDING, index=True)
    region: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # denormalized from customer
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_revenue: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("simulation_runs.id", ondelete="SET NULL"))
    # REACT Merchant
    channel: Mapped[str | None] = mapped_column(String(20), index=True)  # STORE | ONLINE | WHATSAPP
    store_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stores.id", ondelete="SET NULL"), index=True)
    discount_total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    fulfilment_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    payment_method: Mapped[str | None] = mapped_column(String(20))

    customer: Mapped[Customer] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "order_items"
    __table_args__ = (Index("ix_order_items_product_order", "product_id", "order_id"),)

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    line_revenue: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    # REACT Merchant: line_revenue = quantity * list_price - discount_amount
    list_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    discount_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    discount_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("discounts.id", ondelete="SET NULL"))

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class SalesTransaction(Base, UUIDPrimaryKeyMixin):
    """Invoice / cash event for a shipped or delivered order."""

    __tablename__ = "sales_transactions"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    payment_status: Mapped[PaymentStatus] = mapped_column(enum_column(PaymentStatus), nullable=False, default=PaymentStatus.PENDING)
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    payment_method: Mapped[str | None] = mapped_column(String(20))
