"""Shared, read-only merchant aggregations.

Detection rules, investigation tools, baselines and dashboards all read business facts through these functions, so a
number shown on the dashboard, used by a detection rule and cited as evidence is always computed the same way. Every
function works in simulated time (`today` = date of system_state.data_end) and returns the record ids behind the result
where they matter for traceability.

Windows are half-open on `today`: "last 14 days" means the 14 complete days before today.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    FinancialMetric,
    Inventory,
    InventorySnapshot,
    Order,
    OrderItem,
    Product,
    ProductCategory,
    PurchaseOrder,
    PurchaseOrderItem,
    Store,
    Supplier,
    SupplierDelivery,
    SystemState,
)


def today(db: Session) -> date:
    state = db.get(SystemState, 1)
    if state is None:
        raise ValueError("Database is not seeded. Call POST /simulation/reset first.")
    return state.data_end.date()


def window(t: date, days: int, end_offset: int = 0) -> tuple[date, date]:
    """(start, end) inclusive dates of `days` complete days ending `end_offset` days before today."""
    end = t - timedelta(days=1 + end_offset)
    return end - timedelta(days=days - 1), end


def z_rates(recent: float, recent_days: int, prior: float, prior_days: int) -> float:
    """Significance of a change between two Poisson rates (counts over windows of different length)."""
    r, p = recent / recent_days, prior / prior_days
    var = recent / recent_days**2 + prior / prior_days**2
    return (r - p) / math.sqrt(var) if var > 0 else 0.0


def poisson_lower(k: int, lam: float) -> float:
    """P(X <= k) for X ~ Poisson(lam). Exact, computed in log space (stable for the counts REACT sees)."""
    if lam <= 0:
        return 1.0
    log_term = -lam
    total = math.exp(log_term)
    for i in range(1, int(k) + 1):
        log_term += math.log(lam) - math.log(i)
        total += math.exp(log_term)
    return min(1.0, total)


def poisson_upper(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    return max(0.0, 1.0 - poisson_lower(int(k) - 1, lam))


# ---- sales -----------------------------------------------------------------------------------------------------
@dataclass
class SalesStats:
    units: int = 0
    revenue: float = 0.0  # net of discounts
    list_revenue: float = 0.0
    discount: float = 0.0
    cogs: float = 0.0
    lines: int = 0
    orders: int = 0
    customers: int = 0

    @property
    def margin(self) -> float:
        return self.revenue - self.cogs

    @property
    def unit_margin(self) -> float | None:
        return self.margin / self.units if self.units else None

    @property
    def unit_price(self) -> float | None:
        return self.revenue / self.units if self.units else None

    @property
    def unit_cost(self) -> float | None:
        return self.cogs / self.units if self.units else None

    @property
    def discount_rate(self) -> float | None:
        return self.discount / self.list_revenue if self.list_revenue else None

    @property
    def margin_rate(self) -> float | None:
        return self.margin / self.revenue if self.revenue else None


def _sales_select(group_cols: list, start: date, end: date, filters: Iterable = ()):
    list_rev = func.coalesce(OrderItem.list_price, OrderItem.unit_price) * OrderItem.quantity
    return (
        select(*group_cols, func.sum(OrderItem.quantity), func.sum(OrderItem.line_revenue), func.sum(list_rev), func.sum(OrderItem.discount_amount), func.sum(OrderItem.unit_cost * OrderItem.quantity), func.count(OrderItem.id), func.count(func.distinct(Order.id)), func.count(func.distinct(Order.customer_id)))
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.order_date >= start, Order.order_date <= end, Order.status != "CANCELLED", *filters)
        .group_by(*group_cols)
    )


def _stats(row) -> SalesStats:
    return SalesStats(int(row[0] or 0), float(row[1] or 0), float(row[2] or 0), float(row[3] or 0), float(row[4] or 0), int(row[5] or 0), int(row[6] or 0), int(row[7] or 0))


def sales_by_product(db: Session, start: date, end: date, store_id: uuid.UUID | None = None, product_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, SalesStats]:
    filters = []
    if store_id:
        filters.append(Order.store_id == store_id)
    if product_ids is not None:
        filters.append(OrderItem.product_id.in_(product_ids))
    return {r[0]: _stats(r[1:]) for r in db.execute(_sales_select([OrderItem.product_id], start, end, filters)).all()}


def sales_by_category(db: Session, start: date, end: date, region: str | None = None) -> dict[str, SalesStats]:
    q = _sales_select([Product.category], start, end, [Order.region == region] if region else []).join(Product, Product.id == OrderItem.product_id)
    return {r[0]: _stats(r[1:]) for r in db.execute(q).all()}


def sales_by_category_region(db: Session, start: date, end: date) -> dict[tuple[str, str], SalesStats]:
    q = _sales_select([Product.category, Order.region], start, end).join(Product, Product.id == OrderItem.product_id)
    return {(r[0], r[1]): _stats(r[2:]) for r in db.execute(q).all()}


def sales_by_segment(db: Session, start: date, end: date, category: str | None = None, region: str | None = None) -> dict[str, SalesStats]:
    q = _sales_select([Customer.segment], start, end, ([Product.category == category] if category else []) + ([Order.region == region] if region else [])).join(Customer, Customer.id == Order.customer_id).join(Product, Product.id == OrderItem.product_id)
    return {(r[0].value if hasattr(r[0], "value") else r[0]): _stats(r[1:]) for r in db.execute(q).all()}


def sales_total(db: Session, start: date, end: date, product_id: uuid.UUID | None = None, category: str | None = None, region: str | None = None) -> SalesStats:
    filters = []
    if product_id:
        filters.append(OrderItem.product_id == product_id)
    if region:
        filters.append(Order.region == region)
    q = _sales_select([], start, end, filters)
    if category:
        q = q.join(Product, Product.id == OrderItem.product_id).where(Product.category == category)
    row = db.execute(q).first()
    return _stats(row) if row else SalesStats()


def daily_units(db: Session, product_id: uuid.UUID, start: date, end: date) -> dict[date, int]:
    rows = db.execute(select(Order.order_date, func.sum(OrderItem.quantity)).join(Order, Order.id == OrderItem.order_id).where(OrderItem.product_id == product_id, Order.order_date >= start, Order.order_date <= end).group_by(Order.order_date)).all()
    return {d: int(u) for d, u in rows}


def order_item_ids(db: Session, start: date, end: date, product_id: uuid.UUID | None = None, category: str | None = None, region: str | None = None, limit: int = 50) -> list[str]:
    q = select(OrderItem.id).join(Order, Order.id == OrderItem.order_id).where(Order.order_date >= start, Order.order_date <= end)
    if product_id:
        q = q.where(OrderItem.product_id == product_id)
    if region:
        q = q.where(Order.region == region)
    if category:
        q = q.join(Product, Product.id == OrderItem.product_id).where(Product.category == category)
    return [str(i) for i in db.scalars(q.order_by(Order.order_date.desc(), OrderItem.id).limit(limit)).all()]


def order_ids(db: Session, start: date, end: date, repeat_only: bool = False, limit: int = 50) -> list[str]:
    """Most recent order IDs in a window (optionally only orders from customers who had ordered before the window)."""
    q = select(Order.id).where(Order.order_date >= start, Order.order_date <= end, Order.status != "CANCELLED")
    if repeat_only:
        earlier = select(Order.customer_id).where(Order.order_date < start)
        q = q.where(Order.customer_id.in_(earlier))
    return [str(i) for i in db.scalars(q.order_by(Order.order_date.desc(), Order.id).limit(limit)).all()]


def financial_metric_ids(db: Session, start: date, end: date) -> list[str]:
    return [str(i) for i in db.scalars(select(FinancialMetric.id).where(FinancialMetric.metric_date >= start, FinancialMetric.metric_date <= end).order_by(FinancialMetric.metric_date)).all()]


def daily_revenue(db: Session, start: date, end: date) -> dict[date, float]:
    rows = db.execute(select(Order.order_date, func.sum(Order.total_revenue)).where(Order.order_date >= start, Order.order_date <= end, Order.status != "CANCELLED").group_by(Order.order_date)).all()
    return {d: float(v) for d, v in rows}


def repeat_share(db: Session, start: date, end: date) -> tuple[float | None, int, int]:
    """Share of orders placed by customers who had bought before (customer_since before the order date)."""
    row = db.execute(select(func.count(Order.id), func.sum(case((Customer.customer_since < Order.order_date, 1), else_=0))).join(Customer, Customer.id == Order.customer_id).where(Order.order_date >= start, Order.order_date <= end, Order.status != "CANCELLED")).first()
    total, repeat = int(row[0] or 0), int(row[1] or 0)
    return (repeat / total if total else None), repeat, total


# ---- stock -----------------------------------------------------------------------------------------------------
@dataclass
class StockPosition:
    inventory: Inventory
    product: Product
    store: Store | None
    supplier: Supplier | None
    on_hand: int
    on_order: int
    open_pos: list[dict[str, Any]] = field(default_factory=list)  # unreceived purchase orders with this product

    @property
    def value(self) -> float:
        return self.on_hand * float(self.inventory.unit_value)


def stock_positions(db: Session, product_ids: list[uuid.UUID] | None = None, store_id: uuid.UUID | None = None) -> list[StockPosition]:
    q = select(Inventory).where(Inventory.item_type == "PRODUCT", Inventory.product_id.is_not(None))
    if product_ids is not None:
        q = q.where(Inventory.product_id.in_(product_ids))
    if store_id:
        q = q.where(Inventory.store_id == store_id)
    invs = db.scalars(q).all()
    if not invs:
        return []
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_({i.product_id for i in invs}))).all()}
    stores = {s.id: s for s in db.scalars(select(Store)).all()}
    suppliers = {s.id: s for s in db.scalars(select(Supplier)).all()}
    open_rows = db.execute(
        select(PurchaseOrderItem.product_id, PurchaseOrder.store_id, PurchaseOrder.id, PurchaseOrder.po_number, PurchaseOrder.order_date, PurchaseOrder.expected_date, PurchaseOrder.supplier_id, PurchaseOrderItem.quantity - PurchaseOrderItem.quantity_received)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrder.status.in_(("OPEN", "PARTIALLY_RECEIVED")), PurchaseOrderItem.product_id.in_({i.product_id for i in invs}))
    ).all()
    open_by: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for pid, sid, po_id, num, od, ed, sup, qty in open_rows:
        if qty and qty > 0:
            open_by[(pid, sid)].append({"purchase_order_id": str(po_id), "po_number": num, "order_date": od, "expected_date": ed, "supplier_id": str(sup), "quantity": int(qty)})
    out = []
    for inv in invs:
        p = products.get(inv.product_id)
        if p is None:
            continue
        pos = sorted(open_by.get((inv.product_id, inv.store_id), []), key=lambda x: x["expected_date"])
        out.append(StockPosition(inv, p, stores.get(inv.store_id), suppliers.get(p.supplier_id), int(inv.quantity_available), sum(x["quantity"] for x in pos), pos))
    return out


def in_stock_days(db: Session, inventory_ids: list[uuid.UUID], start: date, end: date) -> dict[uuid.UUID, int]:
    """Days on which the item could be sold: it ended the day with stock, or it sold something that day."""
    rows = db.execute(
        select(InventorySnapshot.inventory_id, func.count(InventorySnapshot.id))
        .where(InventorySnapshot.inventory_id.in_(inventory_ids), InventorySnapshot.snapshot_date >= start, InventorySnapshot.snapshot_date <= end, (InventorySnapshot.quantity_available > 0) | (InventorySnapshot.outbound_units > 0))
        .group_by(InventorySnapshot.inventory_id)
    ).all()
    return {i: int(n) for i, n in rows}


def stockout_days(db: Session, inventory_id: uuid.UUID, start: date, end: date) -> list[tuple[str, date]]:
    rows = db.execute(select(InventorySnapshot.id, InventorySnapshot.snapshot_date).where(InventorySnapshot.inventory_id == inventory_id, InventorySnapshot.snapshot_date >= start, InventorySnapshot.snapshot_date <= end, InventorySnapshot.quantity_available == 0)).all()
    return [(str(i), d) for i, d in rows]


def stock_series(db: Session, inventory_id: uuid.UUID, start: date, end: date) -> list[InventorySnapshot]:
    return list(db.scalars(select(InventorySnapshot).where(InventorySnapshot.inventory_id == inventory_id, InventorySnapshot.snapshot_date >= start, InventorySnapshot.snapshot_date <= end).order_by(InventorySnapshot.snapshot_date)).all())


def receipts(db: Session, product_id: uuid.UUID, store_id: uuid.UUID | None) -> list[dict[str, Any]]:
    """Goods received for a product (newest first): delivery id, date, units, purchase order, unit cost."""
    q = (
        select(SupplierDelivery.id, SupplierDelivery.delivered_date, PurchaseOrderItem.quantity_received, PurchaseOrder.id, PurchaseOrder.po_number, PurchaseOrderItem.unit_cost, PurchaseOrder.notes, PurchaseOrder.order_date)
        .join(PurchaseOrder, PurchaseOrder.id == SupplierDelivery.purchase_order_id)
        .join(PurchaseOrderItem, and_(PurchaseOrderItem.purchase_order_id == PurchaseOrder.id, PurchaseOrderItem.product_id == product_id))
        .order_by(SupplierDelivery.delivered_date.desc())
    )
    if store_id:
        q = q.where(SupplierDelivery.store_id == store_id)
    return [{"delivery_id": str(r[0]), "delivered_date": r[1], "units": int(r[2] or 0), "purchase_order_id": str(r[3]), "po_number": r[4], "unit_cost": float(r[5]), "notes": r[6], "order_date": r[7]} for r in db.execute(q).all()]


def stock_age(db: Session, product_id: uuid.UUID, store_id: uuid.UUID | None, on_hand: int, t: date) -> tuple[int | None, list[dict[str, Any]]]:
    """FIFO age of the oldest unit on the shelf: walk receipts newest-first until they cover what is on hand."""
    if on_hand <= 0:
        return None, []
    covered, layers = 0, []
    for r in receipts(db, product_id, store_id):
        layers.append(r)
        covered += r["units"]
        if covered >= on_hand:
            return (t - r["delivered_date"]).days, layers
    return ((t - layers[-1]["delivered_date"]).days if layers else None), layers


# ---- suppliers -------------------------------------------------------------------------------------------------
def deliveries(db: Session, supplier_id: uuid.UUID, since: date | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    q = select(SupplierDelivery, PurchaseOrder.order_date, PurchaseOrder.po_number).join(PurchaseOrder, PurchaseOrder.id == SupplierDelivery.purchase_order_id).where(SupplierDelivery.supplier_id == supplier_id, PurchaseOrder.notes.is_distinct_from("Opening stock")).order_by(SupplierDelivery.delivered_date.desc(), SupplierDelivery.id)
    if since:
        q = q.where(SupplierDelivery.delivered_date >= since)
    if limit:
        q = q.limit(limit)
    return [{"delivery_id": str(d.id), "purchase_order_id": str(d.purchase_order_id), "po_number": num, "order_date": od, "expected_date": d.expected_date, "delivered_date": d.delivered_date, "delay_days": d.delay_days, "on_time": d.on_time, "lead_days": (d.delivered_date - od).days, "units": d.units_received} for d, od, num in db.execute(q).all()]


def open_purchase_orders(db: Session, supplier_id: uuid.UUID | None = None) -> list[PurchaseOrder]:
    q = select(PurchaseOrder).where(PurchaseOrder.status.in_(("OPEN", "PARTIALLY_RECEIVED")))
    if supplier_id:
        q = q.where(PurchaseOrder.supplier_id == supplier_id)
    return list(db.scalars(q.order_by(PurchaseOrder.expected_date)).all())


def observed_lead_days(db: Session, supplier_id: uuid.UUID, n: int = 4) -> float | None:
    rows = deliveries(db, supplier_id, limit=n)
    return sum(r["lead_days"] for r in rows) / len(rows) if rows else None


def supplier_products(db: Session, supplier_id: uuid.UUID) -> list[Product]:
    return list(db.scalars(select(Product).where(Product.supplier_id == supplier_id)).all())


def categories(db: Session) -> dict[str, ProductCategory]:
    return {c.name: c for c in db.scalars(select(ProductCategory)).all()}
