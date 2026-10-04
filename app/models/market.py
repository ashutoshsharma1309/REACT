from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKeyMixin
from app.models._types import JSONType, enum_column
from app.models.enums import CompetitorSignalType, SignalType


class MarketSignal(Base, UUIDPrimaryKeyMixin):
    """Weekly synthetic market-level signal. ALWAYS marked synthetic in Phase 1."""

    __tablename__ = "market_signals"
    __table_args__ = (Index("ix_market_signals_type_date", "signal_type", "signal_date"),)

    signal_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    signal_type: Mapped[SignalType] = mapped_column(enum_column(SignalType), nullable=False)
    product_category: Mapped[str | None] = mapped_column(String(100), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    region: Mapped[str | None] = mapped_column(String(100), index=True)
    demand_index: Mapped[float] = mapped_column(Float, nullable=False)  # 100 = baseline
    growth_rate_pct: Mapped[float] = mapped_column(Float, nullable=False)  # vs previous period
    seasonal_index: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="SYNTHETIC")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("simulation_runs.id", ondelete="SET NULL"))


class CompetitorSignal(Base, UUIDPrimaryKeyMixin):
    """Synthetic competitor event. ALWAYS marked synthetic in Phase 1."""

    __tablename__ = "competitor_signals"
    __table_args__ = (Index("ix_competitor_signals_type_date", "signal_type", "signal_date"),)

    signal_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    competitor_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    signal_type: Mapped[CompetitorSignalType] = mapped_column(enum_column(CompetitorSignalType), nullable=False)
    product_category: Mapped[str | None] = mapped_column(String(100), index=True)
    region: Mapped[str | None] = mapped_column(String(100))
    price_change_pct: Mapped[float | None] = mapped_column(Float)
    availability_score: Mapped[float | None] = mapped_column(Float)  # 0..1, 1 = fully available
    description: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONType)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="SYNTHETIC")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
