"""REACT Merchant synthetic data: deterministic, coherent, labelled synthetic; baselines computed from data."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select

from app.domains import current_domain
from app.domains.merchant import baseline as B
from app.domains.merchant import queries as Q
from app.domains.merchant.catalog import ELECTRONICS
from app.domains.merchant.generator import MerchantDataGenerator
from app.domains.merchant.profile import MerchantProfile, ProfileError
from app.models import CompetitorSignal, FinancialMetric, Inventory, InventorySnapshot, Order, OrderItem, Product, PurchaseOrder, PurchaseOrderItem, SalesTransaction, Store, SupplierDelivery, SystemState
from app.simulation.generator import clear_all_data

import pytest


def test_dataset_is_a_merchant_and_synthetic(mdb):
    st = mdb.get(SystemState, 1)
    assert current_domain(mdb) == "merchant" and st.domain == "merchant" and st.generator_version.startswith("merchant")
    assert (st.data_end - st.data_start).days == 120
    assert mdb.scalar(select(func.count(Product.id))) >= 300
    assert mdb.scalar(select(func.count(Store.id))) == 1
    assert all(s.is_synthetic and s.source == "SYNTHETIC" for s in mdb.scalars(select(CompetitorSignal)).all())
    assert st.profile["merchant"]["business_name"] == "Astra Retail" and set(st.profile["roles"]) >= {"stockout_sku", "margin_sku", "excess_sku", "opportunity_category", "delay_supplier"}


def test_stock_ledger_balances_for_every_item(mdb):
    """opening stock + received - sold == stock on hand, for every product: sales really draw stock down."""
    for inv in mdb.scalars(select(Inventory)).all():
        snaps = mdb.scalars(select(InventorySnapshot).where(InventorySnapshot.inventory_id == inv.id).order_by(InventorySnapshot.snapshot_date)).all()
        if not snaps:
            continue
        first = snaps[0]
        opening = first.quantity_available - first.inbound_units + first.outbound_units
        assert opening + sum(s.inbound_units for s in snaps) - sum(s.outbound_units for s in snaps) == inv.quantity_available, inv.item_name


def test_sales_inventory_orders_and_financials_agree(mdb):
    t = Q.today(mdb)
    # snapshot outbound == order item quantities, per product over the whole window
    sold = dict(mdb.execute(select(OrderItem.product_id, func.sum(OrderItem.quantity)).group_by(OrderItem.product_id)).all())
    out = dict(mdb.execute(select(Inventory.product_id, func.sum(InventorySnapshot.outbound_units)).join(InventorySnapshot, InventorySnapshot.inventory_id == Inventory.id).group_by(Inventory.product_id)).all())
    assert all(int(sold.get(pid, 0)) == int(out.get(pid, 0)) for pid in set(sold) | set(out))
    # order totals == sum of lines; one paid invoice per order
    o = mdb.scalars(select(Order).limit(200)).all()
    for order in o:
        assert round(sum(float(i.line_revenue) for i in order.items), 2) == float(order.total_revenue)
        assert order.order_date < t and order.channel in ("STORE", "ONLINE", "WHATSAPP")
    assert mdb.scalar(select(func.count(SalesTransaction.id))) == mdb.scalar(select(func.count(Order.id)))
    # daily revenue in financial metrics == orders of that day
    by_day = dict(mdb.execute(select(Order.order_date, func.sum(Order.total_revenue)).group_by(Order.order_date)).all())
    for fm in mdb.scalars(select(FinancialMetric)).all():
        assert abs(float(fm.revenue) - float(by_day.get(fm.metric_date, 0))) < 1.0
    # every received purchase order has a delivery for the same units
    for po in mdb.scalars(select(PurchaseOrder).where(PurchaseOrder.status == "RECEIVED").limit(100)).all():
        assert sum(d.units_received for d in po.deliveries) == sum(i.quantity_received for i in po.items) == po.total_units


def test_line_revenue_is_list_price_minus_discount(mdb):
    for i in mdb.scalars(select(OrderItem).where(OrderItem.discount_amount > 0).limit(300)).all():
        assert abs(float(i.list_price) * i.quantity - float(i.discount_amount) - float(i.line_revenue)) < 0.01
        assert i.discount_id is not None


def test_generation_is_deterministic(mdb):
    from tests.conftest import MERCHANT_ANCHOR

    def fingerprint():
        orders = mdb.execute(select(Order.order_number, Order.total_revenue).order_by(Order.order_number)).all()
        pos = mdb.execute(select(PurchaseOrder.po_number, PurchaseOrder.total_units).order_by(PurchaseOrder.po_number)).all()
        return hash(tuple(orders)), hash(tuple(pos)), len(orders)

    first = fingerprint()
    clear_all_data(mdb)
    MerchantDataGenerator(mdb, seed=42, history_days=120, anchor=MERCHANT_ANCHOR).generate()
    assert fingerprint() == first


def test_profile_validation_and_multi_store(mdb):
    with pytest.raises(ProfileError):
        MerchantProfile(business_name="X").normalised()
    with pytest.raises(ProfileError):
        MerchantProfile(categories=["AUDIO", "CASES"]).normalised()
    with pytest.raises(ProfileError):
        MerchantProfile(store_count=4).normalised()
    with pytest.raises(ProfileError):
        MerchantProfile(categories=["AUDIO", "CASES", "GROCERY"]).normalised()
    from tests.conftest import MERCHANT_ANCHOR

    clear_all_data(mdb)
    MerchantDataGenerator(mdb, seed=7, history_days=60, anchor=MERCHANT_ANCHOR, profile={"business_name": "Mehta Mobiles", "store_count": 2, "categories": ["AUDIO", "CASES", "CHARGE", "POWER"], "city": "Pune", "locality": "Kothrud"}).generate()
    stores = mdb.scalars(select(Store).order_by(Store.code)).all()
    assert [s.code for s in stores] == ["S1", "S2"] and stores[0].region == "South Pune"
    assert set(mdb.scalars(select(Product.category).distinct()).all()) == {"Wireless audio", "Cases & screen guards", "Chargers & cables", "Power banks"}
    assert mdb.scalar(select(func.count(Inventory.id)).where(Inventory.store_id == stores[1].id)) > 0


def test_baselines_come_from_the_data(mdb):
    t = Q.today(mdb)
    days = B.weekday_sales(mdb, t)
    assert len(days) == 7 and all(len(d["baseline_dates"]) == 8 for d in days)
    for d in days:  # the baseline is the same weekday in each of the previous 8 weeks
        from datetime import date

        wd = date.fromisoformat(d["date"]).weekday()
        assert all(date.fromisoformat(x).weekday() == wd for x in d["baseline_dates"])
    weekend = [d["normal_mean"] for d in days if d["weekday"] in ("Saturday", "Sunday")]
    weekday = [d["normal_mean"] for d in days if d["weekday"] in ("Monday", "Tuesday")]
    assert min(weekend) > max(weekday)  # merchants sell more at the weekend
    data = B.compute(mdb)
    assert data["repeat_purchase"]["normal_share"] and 0.2 < data["repeat_purchase"]["normal_share"] < 0.7
    assert all(s["recent_on_time_rate"] is None or 0 <= s["recent_on_time_rate"] <= 1 for s in data["supplier_delivery"])
    assert data["basis"] == "SYNTHETIC"


def test_poisson_helpers():
    assert abs(Q.poisson_lower(0, 2.0) - 0.135335) < 1e-5
    assert abs(Q.poisson_upper(1, 2.0) - (1 - 0.135335)) < 1e-5
    assert Q.poisson_upper(30, 10) < 1e-6 and Q.poisson_lower(0, 0) == 1.0
