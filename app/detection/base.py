"""Shared finding structure + helpers used by all deterministic engines."""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, ProductionLine, ProductionMetric

SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


@dataclass
class Finding:
    kind: str  # incident | risk | opportunity
    type: str
    severity: str
    title: str
    description: str
    dedupe_key: str
    machine_id: uuid.UUID | None = None
    production_line_id: uuid.UUID | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    entity_name: str | None = None
    product_id: uuid.UUID | None = None
    customer_segment: str | None = None
    region: str | None = None
    signal: str | None = None
    confidence: str | None = None
    affected_metrics: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    impact: dict[str, Any] = field(default_factory=dict)


def max_severity(*sevs: str) -> str:
    return max(sevs, key=lambda s: SEVERITY_ORDER.get(s, 0))


def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def std(xs: Iterable[float]) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def pct_change(new: float, old: float) -> float:
    return (new - old) / old if old else 0.0


def ids(objs: Iterable[Any], cap: int = 100) -> list[str]:
    out = []
    for o in objs:
        out.append(str(getattr(o, "id", o)))
        if len(out) >= cap:
            break
    return out


class LineEconomics:
    """Average unit margin / cost per production line, computed once per run."""

    def __init__(self, db: Session) -> None:
        self.lines: dict[uuid.UUID, ProductionLine] = {line.id: line for line in db.scalars(select(ProductionLine)).all()}
        self.products: dict[uuid.UUID, Product] = {p.id: p for p in db.scalars(select(Product)).all()}
        self.by_line: dict[uuid.UUID, list[Product]] = {}
        for p in self.products.values():
            if p.production_line_id:
                self.by_line.setdefault(p.production_line_id, []).append(p)

    def unit_margin(self, line_id: uuid.UUID | None) -> float:
        prods = self.by_line.get(line_id, []) if line_id else list(self.products.values())
        return mean(float(p.selling_price) - float(p.unit_cost) for p in prods) if prods else 800.0

    def unit_cost(self, line_id: uuid.UUID | None) -> float:
        prods = self.by_line.get(line_id, []) if line_id else list(self.products.values())
        return mean(float(p.unit_cost) for p in prods) if prods else 1500.0

    def product_margin(self, product: Product) -> float:
        return float(product.selling_price) - float(product.unit_cost)


def production_windows(db: Session, line_id: uuid.UUID, today: date, recent_days: int, baseline_days: int) -> tuple[list[ProductionMetric], list[ProductionMetric]]:
    """Recent vs baseline production metrics (working days only)."""
    start = today - timedelta(days=recent_days + baseline_days)
    rows = db.scalars(select(ProductionMetric).where(ProductionMetric.production_line_id == line_id, ProductionMetric.metric_date > start, ProductionMetric.metric_date <= today).order_by(ProductionMetric.metric_date)).all()
    split = today - timedelta(days=recent_days)
    recent = [m for m in rows if m.metric_date > split and m.operating_hours > 0]
    baseline = [m for m in rows if m.metric_date <= split and m.operating_hours > 0]
    return recent, baseline


def window_dict(start: datetime | date, end: datetime | date, **extra: Any) -> dict[str, Any]:
    return {"start": start.isoformat(), "end": end.isoformat(), **extra}
