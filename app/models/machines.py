from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models._types import enum_column
from app.models.enums import ComponentCondition, Criticality, MachineStatus, SensorStatus, SensorType


class Machine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "machines"

    production_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_lines.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    machine_type: Mapped[str] = mapped_column(String(100), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    installation_date: Mapped[date] = mapped_column(Date, nullable=False)
    criticality: Mapped[Criticality] = mapped_column(enum_column(Criticality), nullable=False, default=Criticality.MEDIUM)
    status: Mapped[MachineStatus] = mapped_column(enum_column(MachineStatus), nullable=False, default=MachineStatus.RUNNING)
    rated_power_kw: Mapped[float] = mapped_column(Float, nullable=False, default=25.0)
    health_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)  # 0..1 deterministic health estimate

    production_line: Mapped["ProductionLine"] = relationship(back_populates="machines")  # noqa: F821
    components: Mapped[list["MachineComponent"]] = relationship(back_populates="machine", cascade="all, delete-orphan")
    sensors: Mapped[list["Sensor"]] = relationship(back_populates="machine", cascade="all, delete-orphan")


class MachineComponent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "machine_components"

    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    component_type: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. BEARING, MOTOR, PUMP, BELT
    part_number: Mapped[str] = mapped_column(String(60), nullable=False)
    installed_on: Mapped[date] = mapped_column(Date, nullable=False)
    expected_life_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    operating_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    condition: Mapped[ComponentCondition] = mapped_column(enum_column(ComponentCondition), nullable=False, default=ComponentCondition.GOOD)
    condition_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)  # 1.0 new .. 0.0 failed
    failure_probability: Mapped[float] = mapped_column(Float, nullable=False, default=0.02)  # 0..1 per 30 days

    machine: Mapped[Machine] = relationship(back_populates="components")


class Sensor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sensors"
    __table_args__ = (Index("ix_sensors_machine_type", "machine_id", "sensor_type"),)

    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    sensor_type: Mapped[SensorType] = mapped_column(enum_column(SensorType), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    normal_min: Mapped[float] = mapped_column(Float, nullable=False)
    normal_max: Mapped[float] = mapped_column(Float, nullable=False)
    warning_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    critical_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[SensorStatus] = mapped_column(enum_column(SensorStatus), nullable=False, default=SensorStatus.ACTIVE)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    machine: Mapped[Machine] = relationship(back_populates="sensors")
    readings: Mapped[list["SensorReading"]] = relationship(back_populates="sensor", cascade="all, delete-orphan", passive_deletes=True)


class SensorReading(Base):
    """High-volume time series. Integer PK + composite index on (sensor_id, timestamp)."""

    __tablename__ = "sensor_readings"
    __table_args__ = (Index("ix_sensor_readings_sensor_ts", "sensor_id", "timestamp"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    sensor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    quality: Mapped[str] = mapped_column(String(10), nullable=False, default="GOOD")  # GOOD | SUSPECT | BAD

    sensor: Mapped[Sensor] = relationship(back_populates="readings")
