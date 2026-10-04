from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models._types import enum_column
from app.models.enums import MaintenanceType


class ProductionMetric(Base, UUIDPrimaryKeyMixin):
    """Daily production metrics per production line."""

    __tablename__ = "production_metrics"
    __table_args__ = (Index("ix_production_metrics_line_date", "production_line_id", "metric_date", unique=True),)

    production_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_lines.id", ondelete="CASCADE"), nullable=False)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    planned_units: Mapped[int] = mapped_column(Integer, nullable=False)
    output_units: Mapped[int] = mapped_column(Integer, nullable=False)
    output_rate: Mapped[float] = mapped_column(Float, nullable=False)  # units per operating hour
    defect_units: Mapped[int] = mapped_column(Integer, nullable=False)
    defect_rate: Mapped[float] = mapped_column(Float, nullable=False)  # 0..1
    downtime_hours: Mapped[float] = mapped_column(Float, nullable=False)
    operating_hours: Mapped[float] = mapped_column(Float, nullable=False)
    utilization: Mapped[float] = mapped_column(Float, nullable=False)  # output / capacity, 0..1+
    power_consumption_kwh: Mapped[float] = mapped_column(Float, nullable=False)

    production_line: Mapped["ProductionLine"] = relationship()  # noqa: F821


class MaintenanceRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "maintenance_records"
    __table_args__ = (Index("ix_maintenance_machine_date", "machine_id", "maintenance_date"),)

    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    component_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machine_components.id", ondelete="SET NULL"))
    maintenance_date: Mapped[date] = mapped_column(Date, nullable=False)
    maintenance_type: Mapped[MaintenanceType] = mapped_column(enum_column(MaintenanceType), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    downtime_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    technician: Mapped[str] = mapped_column(String(120), nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    outcome: Mapped[str | None] = mapped_column(String(200))

    machine: Mapped["Machine"] = relationship()  # noqa: F821
    component: Mapped["MachineComponent | None"] = relationship()  # noqa: F821
