from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models._types import enum_column
from app.models.enums import LineStatus, OperationalStatus


class Company(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    industry: Mapped[str] = mapped_column(String(120), nullable=False)
    headquarters: Mapped[str] = mapped_column(String(200), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    # REACT Merchant profile (null for the manufacturing reference pack)
    business_type: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    plants: Mapped[list["Plant"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Plant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "plants"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    region: Mapped[str] = mapped_column(String(100), nullable=False, default="South India")
    operational_status: Mapped[OperationalStatus] = mapped_column(enum_column(OperationalStatus), nullable=False, default=OperationalStatus.OPERATIONAL)

    company: Mapped[Company] = relationship(back_populates="plants")
    production_lines: Mapped[list["ProductionLine"]] = relationship(back_populates="plant", cascade="all, delete-orphan")


class ProductionLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "production_lines"
    __table_args__ = (Index("ix_production_lines_plant_name", "plant_id", "name", unique=True),)

    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    capacity_units_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[LineStatus] = mapped_column(enum_column(LineStatus), nullable=False, default=LineStatus.RUNNING)
    description: Mapped[str | None] = mapped_column(String(500))

    plant: Mapped[Plant] = relationship(back_populates="production_lines")
    machines: Mapped[list["Machine"]] = relationship(back_populates="production_line", cascade="all, delete-orphan")  # noqa: F821
