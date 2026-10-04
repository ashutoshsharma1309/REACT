"""Seed data volume, realism and determinism."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.models import Customer, FinancialMetric, Machine, MarketSignal, Order, Product, ProductionLine, ProductionMetric, Sensor, SensorReading, Supplier, SystemState
from app.simulation.clock import is_operating
from app.simulation.generator import GeneratorConfig, SyntheticDataGenerator, clear_all_data, seed_database
from tests.conftest import ANCHOR


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_minimum_volumes(db):
    assert count(db, ProductionLine) == 3
    assert count(db, Machine) >= 10
    assert count(db, Sensor) >= 30
    assert count(db, Product) >= 20
    assert count(db, Customer) >= 100
    assert count(db, Order) >= 500
    assert count(db, Supplier) >= 10
    assert count(db, SensorReading) > 30 * 24 * 50
    state = db.get(SystemState, 1)
    assert state.data_end == ANCHOR and state.seed == 42


def test_market_signals_are_marked_synthetic(db):
    total = count(db, MarketSignal)
    synthetic = db.scalar(select(func.count()).select_from(MarketSignal).where(MarketSignal.is_synthetic.is_(True), MarketSignal.source == "SYNTHETIC"))
    assert total > 0 and synthetic == total


def test_normal_machine_behaviour(db):
    """A healthy machine's recent operating readings sit inside the sensor's normal band."""
    machine = db.scalar(select(Machine).where(Machine.code == "ROB-A4"))
    for sensor in machine.sensors:
        rows = db.scalars(select(SensorReading).where(SensorReading.sensor_id == sensor.id, SensorReading.timestamp > ANCHOR - timedelta(days=3))).all()
        operating = [r.value for r in rows if is_operating(r.timestamp)]
        assert operating, sensor.code
        mean = sum(operating) / len(operating)
        assert sensor.normal_min * 0.8 <= mean <= sensor.normal_max * 1.05, (sensor.code, mean)
        assert mean < sensor.warning_threshold


def test_demand_drives_production_and_financials(db):
    """Correlation: line output tracks order demand; revenue tracks orders."""
    lines = db.scalars(select(ProductionLine)).all()
    for line in lines:
        out = db.scalar(select(func.sum(ProductionMetric.output_units)).where(ProductionMetric.production_line_id == line.id))
        pids = [p.id for p in db.scalars(select(Product).where(Product.production_line_id == line.id)).all()]
        from app.models import OrderItem

        ordered = db.scalar(select(func.sum(OrderItem.quantity)).where(OrderItem.product_id.in_(pids)))
        assert 0.7 <= out / ordered <= 1.4, (line.code, out, ordered)
    fm = db.scalars(select(FinancialMetric)).all()
    rev_days = [m for m in fm if m.orders_count > 0]
    assert all(float(m.revenue) > 0 for m in rev_days)
    assert 0.2 < sum(m.gross_margin for m in rev_days) / len(rev_days) < 0.45


def test_generator_is_deterministic(db):
    cfg = GeneratorConfig(seed=7, history_days=35, sensor_interval_minutes=240, anchor=datetime(2026, 1, 15, 6))
    checksums = []
    for _ in range(2):
        clear_all_data(db)
        SyntheticDataGenerator(db, cfg).generate()
        orders = db.execute(select(Order.order_number, Order.total_revenue, Order.status).order_by(Order.order_number)).all()
        readings = db.execute(select(Sensor.code, func.sum(SensorReading.value)).join(SensorReading).group_by(Sensor.code).order_by(Sensor.code)).all()
        checksums.append((hash(tuple(orders)), tuple((c, round(float(v), 2)) for c, v in readings)))
    assert checksums[0] == checksums[1]


def test_seed_database_clears_and_reseeds(db):
    before = count(db, Order)
    summary = seed_database(db, GeneratorConfig(seed=42, history_days=40, sensor_interval_minutes=240, anchor=ANCHOR), domain="manufacturing")
    assert summary.counts["orders"] > 0 and count(db, Order) == summary.counts["orders"]
    assert count(db, Order) != before or summary.counts["orders"] == before
