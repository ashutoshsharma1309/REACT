from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import Page
from app.schemas.entities import CompanyOut, CustomerOut, InventoryOut, MachineDetailOut, MachineOut, MachineReadingsOut, OrderOut, PlantOut, ProductionLineOut, ProductOut, SupplierOut
from app.services import catalog_service as svc

router = APIRouter(tags=["entities"])


@router.get("/companies", response_model=list[CompanyOut])
def companies(db: Session = Depends(get_db)):
    return svc.list_companies(db)


@router.get("/plants", response_model=list[PlantOut])
def plants(db: Session = Depends(get_db)):
    return svc.list_plants(db)


@router.get("/production-lines", response_model=list[ProductionLineOut])
def production_lines(db: Session = Depends(get_db)):
    return svc.list_production_lines(db)


@router.get("/machines", response_model=list[MachineOut])
def machines(production_line_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_db)):
    return svc.list_machines(db, production_line_id, status)


@router.get("/machines/{machine_id}", response_model=MachineDetailOut)
def machine(machine_id: uuid.UUID, db: Session = Depends(get_db)):
    m = svc.get_machine(db, machine_id)
    if not m:
        raise HTTPException(404, "machine not found")
    return m


@router.get("/machines/{machine_id}/readings", response_model=MachineReadingsOut)
def machine_readings(machine_id: uuid.UUID, hours: int = Query(24, ge=1, le=24 * 120), sensor_type: str | None = None, db: Session = Depends(get_db)):
    m = svc.get_machine(db, machine_id)
    if not m:
        raise HTTPException(404, "machine not found")
    return svc.machine_readings(db, m, hours=hours, sensor_type=sensor_type)


def _merchant(db: Session) -> bool:
    from app.domains import is_merchant

    return is_merchant(db)


@router.get("/products", response_model=list[ProductOut], summary="Products (REACT Merchant adds sales, margin, demand trend, stock and open findings)")
def products(category: str | None = None, db: Session = Depends(get_db)):
    if _merchant(db):
        from app.domains.merchant import read_models

        return read_models.products(db, category)
    return svc.list_products(db, category)


@router.get("/customers", response_model=Page[CustomerOut])
def customers(region: str | None = None, segment: str | None = None, industry: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_customers(db, region, segment, industry, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/orders", response_model=Page[OrderOut])
def orders(status: str | None = None, customer_id: uuid.UUID | None = None, product_id: uuid.UUID | None = None, region: str | None = None, days: int | None = Query(None, ge=1, le=365), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items, total = svc.list_orders(db, status, customer_id, product_id, region, days, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/inventory", response_model=list[InventoryOut], summary="Stock positions (REACT Merchant adds store, cover, value, open orders, status and findings)")
def inventory(item_type: str | None = None, below_reorder: bool | None = None, warehouse: str | None = None, db: Session = Depends(get_db)):
    if _merchant(db):
        from app.domains.merchant import read_models

        return read_models.inventory(db)
    return svc.list_inventory(db, item_type, below_reorder, warehouse)


@router.get("/suppliers", response_model=list[SupplierOut], summary="Suppliers (REACT Merchant adds delivery performance, open orders and dependent revenue)")
def suppliers(component_type: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    if _merchant(db):
        from app.domains.merchant import read_models

        return read_models.suppliers(db)
    return svc.list_suppliers(db, component_type, status)
