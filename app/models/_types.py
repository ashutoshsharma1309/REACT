"""Portable column types."""
from __future__ import annotations

import enum as _enum

from sqlalchemy import JSON, Enum
from sqlalchemy.dialects.postgresql import JSONB

# JSONB on PostgreSQL, generic JSON elsewhere (SQLite for lightweight tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


def enum_column(enum_cls: type[_enum.Enum], length: int = 40) -> Enum:
    """Store enums as plain VARCHAR so new statuses can be added without DDL."""
    return Enum(enum_cls, native_enum=False, length=length, create_constraint=False, validate_strings=True)
