"""Read services for business entities (no business logic in route handlers)."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Company, Customer, Inventory, Machine, Order, Plant, Product, ProductionLine, Sensor, SensorReading, Supplier, SupplierBatch, SystemState


def _page(db: Session, stmt, limit: int, offset: int):
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = db.scalars(stmt.limit(limit).offset(offset)).all()
    return items, total


def list_companies(db: Session):
    return db.scalars(select(Company).order_by(Company.name)).all()


def list_plants(db: Session):
    return db.scalars(select(Plant).order_by(Plant.name)).all()


def list_production_lines(db: Session):
    lines = db.scalars(select(ProductionLine).options(selectinload(ProductionLine.machines)).order_by(ProductionLine.code)).all()
    for line in lines:
        line.machine_count = len(line.machines)  # type: ignore[attr-defined]
    return lines


def list_machines(db: Session, production_line_id: uuid.UUID | None = None, status: str | None = None):
    stmt = select(Machine).order_by(Machine.code)
    if production_line_id:
        stmt = stmt.where(Machine.production_line_id == production_line_id)
    if status:
        stmt = stmt.where(Machine.status == status)
    return db.scalars(stmt).all()


def get_machine(db: Session, machine_id: uuid.UUID) -> Machine | None:
    m = db.scalar(select(Machine).options(selectinload(Machine.components), selectinload(Machine.sensors), selectinload(Machine.production_line)).where(Machine.id == machine_id))
    if m:
        m.production_line_name = m.production_line.name if m.production_line else None  # type: ignore[attr-defined]
    return m


def machine_readings(db: Session, machine: Machine, hours: int = 24, sensor_type: str | None = None, limit_per_sensor: int = 2000):
    state = db.get(SystemState, 1)
    end = state.data_end if state else db.scalar(select(func.max(SensorReading.timestamp)))
    start = end - timedelta(hours=hours)
    sensors = [s for s in machine.sensors if not sensor_type or s.sensor_type.value == sensor_type.upper()]
    out = []
    for s in sorted(sensors, key=lambda s: s.code):
        rows = db.scalars(select(SensorReading).where(SensorReading.sensor_id == s.id, SensorReading.timestamp >= start, SensorReading.timestamp <= end).order_by(SensorReading.timestamp).limit(limit_per_sensor)).all()
        out.append({"sensor": s, "count": len(rows), "readings": rows})
    return {"machine_id": machine.id, "machine_code": machine.code, "window_start": start, "window_end": end, "sensors": out}


def list_products(db: Session, category: str | None = None):
    stmt = select(Product).order_by(Product.category, Product.sku)
    if category:
        stmt = stmt.where(Product.category == category)
    return db.scalars(stmt).all()


def list_customers(db: Session, region: str | None = None, segment: str | None = None, industry: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(Customer).order_by(Customer.name)
    if region:
        stmt = stmt.where(Customer.region == region)
    if segment:
        stmt = stmt.where(Customer.segment == segment.upper())
    if industry:
        stmt = stmt.where(Customer.industry == industry)
    return _page(db, stmt, limit, offset)


def list_orders(db: Session, status: str | None = None, customer_id: uuid.UUID | None = None, product_id: uuid.UUID | None = None, region: str | None = None, days: int | None = None, limit: int = 100, offset: int = 0):
    stmt = select(Order).options(selectinload(Order.items)).order_by(Order.order_date.desc(), Order.order_number.desc())
    if status:
        stmt = stmt.where(Order.status == status.upper())
    if customer_id:
        stmt = stmt.where(Order.customer_id == customer_id)
    if region:
        stmt = stmt.where(Order.region == region)
    if product_id:
        from app.models import OrderItem

        stmt = stmt.where(Order.id.in_(select(OrderItem.order_id).where(OrderItem.product_id == product_id)))
    if days:
        state = db.get(SystemState, 1)
        if state:
            stmt = stmt.where(Order.order_date > state.data_end.date() - timedelta(days=days))
    return _page(db, stmt, limit, offset)


def list_inventory(db: Session, item_type: str | None = None, below_reorder: bool | None = None, warehouse: str | None = None):
    stmt = select(Inventory).order_by(Inventory.item_type, Inventory.item_name)
    if item_type:
        stmt = stmt.where(Inventory.item_type == item_type.upper())
    if warehouse:
        stmt = stmt.where(Inventory.warehouse == warehouse)
    if below_reorder:
        stmt = stmt.where(Inventory.quantity_available < Inventory.reorder_point)
    items = db.scalars(stmt).all()
    for i in items:
        i.days_of_cover = round(i.quantity_available / i.avg_daily_consumption, 1) if i.avg_daily_consumption else 0.0  # type: ignore[attr-defined]
    return items


def list_suppliers(db: Session, component_type: str | None = None, status: str | None = None):
    stmt = select(Supplier).order_by(Supplier.component_type, Supplier.is_primary.desc(), Supplier.name)
    if component_type:
        stmt = stmt.where(Supplier.component_type == component_type.upper())
    if status:
        stmt = stmt.where(Supplier.status == status.upper())
    suppliers = db.scalars(stmt).all()
    open_batches = db.scalars(select(SupplierBatch).where(SupplierBatch.received_date.is_(None)).order_by(SupplierBatch.expected_date)).all()
    by_supplier: dict[uuid.UUID, list[SupplierBatch]] = {}
    for b in open_batches:
        by_supplier.setdefault(b.supplier_id, []).append(b)
    for s in suppliers:
        s.open_batches = by_supplier.get(s.id, [])  # type: ignore[attr-defined]
    return suppliers


def list_sensors(db: Session, machine_id: uuid.UUID | None = None):
    stmt = select(Sensor).order_by(Sensor.code)
    if machine_id:
        stmt = stmt.where(Sensor.machine_id == machine_id)
    return db.scalars(stmt).all()
