from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKeyMixin


class FinancialMetric(Base, UUIDPrimaryKeyMixin):
    """Daily company-level P&L snapshot derived from orders + production (synthetic)."""

    __tablename__ = "financial_metrics"
    __table_args__ = (Index("ix_financial_metrics_company_date", "company_id", "metric_date", unique=True),)

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_type: Mapped[str] = mapped_column(String(10), nullable=False, default="DAILY")
    revenue: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)  # cost of goods sold
    gross_margin: Mapped[float] = mapped_column(Float, nullable=False)  # (revenue - cost) / revenue
    operating_cost: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    production_cost: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    profit: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    units_sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    # REACT Merchant
    discount_total: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False, default=0, server_default="0")
    fulfilment_cost: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False, default=0, server_default="0")
