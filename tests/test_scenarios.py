"""Each scenario must create correlated effects across several tables (not a single field)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.models import Inventory, MarketSignal, Order, ProductionMetric, Sensor, SensorReading, SimulationRun, Supplier, SupplierBatch, SystemState
from app.models.enums import OrderStatus
from app.simulation.clock import is_operating
from app.simulation.engine import SimulationEngine
from app.simulation.scenarios.base import ScenarioError


def op_mean(db, sensor, start, end):
    rows = db.scalars(select(SensorReading).where(SensorReading.sensor_id == sensor.id, SensorReading.timestamp >= start, SensorReading.timestamp <= end)).all()
    vals = [r.value for r in rows if is_operating(r.timestamp)]
    return sum(vals) / len(vals) if vals else 0.0


def line_avg(db, line_id, start, end, field="output_units"):
    rows = db.scalars(select(ProductionMetric).where(ProductionMetric.production_line_id == line_id, ProductionMetric.metric_date >= start, ProductionMetric.metric_date <= end, ProductionMetric.operating_hours > 0)).all()
    return sum(getattr(m, field) for m in rows) / len(rows)


def test_bearing_failure_is_correlated(db):
    now = db.get(SystemState, 1).data_end
    run = SimulationEngine(db).run("bearing_failure", {"days": 7})
    assert run.status.value == "COMPLETED"
    machine_id = run.parameters["machine_id"]
    from app.models import Machine

    machine = db.get(Machine, __import__("uuid").UUID(machine_id))
    line = machine.production_line
    vib = next(s for s in machine.sensors if s.sensor_type.value == "VIBRATION")
    temp = next(s for s in machine.sensors if s.sensor_type.value == "TEMPERATURE")
    recent = (now - timedelta(days=1), now)
    baseline = (now - timedelta(days=30), now - timedelta(days=8))
    assert op_mean(db, vib, *recent) > op_mean(db, vib, *baseline) * 1.6
    assert op_mean(db, temp, *recent) > op_mean(db, temp, *baseline) * 1.15
    # output down, defects up on the line
    assert line_avg(db, line.id, now.date() - timedelta(days=2), now.date()) < line_avg(db, line.id, now.date() - timedelta(days=40), now.date() - timedelta(days=8)) * 0.85
    assert line_avg(db, line.id, now.date() - timedelta(days=2), now.date(), "defect_rate") > line_avg(db, line.id, now.date() - timedelta(days=40), now.date() - timedelta(days=8), "defect_rate") * 1.5
    bearing = next(c for c in machine.components if c.component_type == "BEARING")
    assert bearing.condition.value == "CRITICAL" and bearing.failure_probability > 0.5
    assert machine.status.value == "DEGRADED"
    assert set(run.affected_entities) >= {"sensors", "production_metrics", "inventory", "machine_components"}
    assert db.get(SystemState, 1).last_simulation_id == run.id


def test_sensor_failure_is_isolated(db):
    now = db.get(SystemState, 1).data_end
    sensor = db.scalar(select(Sensor).where(Sensor.code == "VMC-A2-TEMP"))
    other = db.scalar(select(Sensor).where(Sensor.code == "VMC-A2-VIBR"))
    before_other = op_mean(db, other, now - timedelta(hours=36), now)
    before_output = line_avg(db, sensor.machine.production_line_id, now.date() - timedelta(days=3), now.date())
    run = SimulationEngine(db).run("sensor_failure", {"mode": "stuck", "hours": 36})
    rows = db.scalars(select(SensorReading).where(SensorReading.sensor_id == sensor.id, SensorReading.timestamp >= now - timedelta(hours=36))).all()
    assert len({r.value for r in rows}) == 1  # frozen
    assert op_mean(db, other, now - timedelta(hours=36), now) == before_other
    assert line_avg(db, sensor.machine.production_line_id, now.date() - timedelta(days=3), now.date()) == before_output
    assert run.effects["production_impact"].startswith("none")


def test_sensor_failure_modes(db):
    for mode in ("spike", "dropout"):
        run = SimulationEngine(db).run("sensor_failure", {"mode": mode, "hours": 12})
        assert run.status.value == "COMPLETED"
    try:
        SimulationEngine(db).run("sensor_failure", {"mode": "nonsense"})
        assert False, "expected ScenarioError"
    except ScenarioError:
        pass


def test_supplier_delay_propagates(db):
    supplier = db.scalar(select(Supplier).where(Supplier.name == "Helios Controls GmbH"))
    inv = db.scalar(select(Inventory).where(Inventory.component_type == supplier.component_type))
    before_qty = inv.quantity_available
    run = SimulationEngine(db).run("supplier_delay", {"delay_days": 10})
    db.refresh(supplier)
    db.refresh(inv)
    assert supplier.lead_time_days == supplier.baseline_lead_time_days + 10 and supplier.status.value == "AT_RISK"
    delayed = db.scalars(select(SupplierBatch).where(SupplierBatch.supplier_id == supplier.id, SupplierBatch.status == "DELAYED")).all()
    assert delayed
    assert inv.quantity_available < before_qty and inv.quantity_available < inv.safety_stock
    assert run.effects["orders_delayed"] > 0
    assert db.scalar(select(func.count()).select_from(Order).where(Order.status == OrderStatus.DELAYED)) >= run.effects["orders_delayed"]
    assert run.effects["production_units_lost"] > 0


def test_demand_spike_propagates(db):
    from app.models import Product

    product = db.scalar(select(Product).where(Product.sku == "AV-CX80"))
    inv = db.scalar(select(Inventory).where(Inventory.product_id == product.id))
    now = db.get(SystemState, 1).data_end
    before_orders = db.scalar(select(func.count()).select_from(Order))
    before_inv = inv.quantity_available
    before_util = line_avg(db, product.production_line_id, now.date() - timedelta(days=6), now.date(), "utilization")
    run = SimulationEngine(db).run("demand_spike", {"days": 14, "multiplier": 3.0})
    db.refresh(inv)
    assert db.scalar(select(func.count()).select_from(Order)) - before_orders == run.effects["orders_created"] > 5
    assert db.scalar(select(func.count()).select_from(Order).where(Order.simulation_run_id == run.id)) == run.effects["orders_created"]
    assert inv.quantity_available < before_inv
    assert line_avg(db, product.production_line_id, now.date() - timedelta(days=6), now.date(), "utilization") > before_util
    sig = db.scalar(select(MarketSignal).where(MarketSignal.simulation_run_id == run.id))
    assert sig is not None and sig.is_synthetic and sig.region == "South India"


def test_demand_drop_propagates(db):
    from app.models import FinancialMetric, Product

    now = db.get(SystemState, 1).data_end
    products = db.scalars(select(Product).where(Product.category == "Pump Components")).all()
    invs = {p.id: db.scalar(select(Inventory).where(Inventory.product_id == p.id)).quantity_available for p in products}
    before_rev = db.scalar(select(func.sum(FinancialMetric.revenue)).where(FinancialMetric.metric_date > now.date() - timedelta(days=21)))
    run = SimulationEngine(db).run("demand_drop", {"days": 21, "factor": 0.4})
    assert run.effects["units_removed"] > 0 and run.effects["orders_deleted"] >= 0
    after_rev = db.scalar(select(func.sum(FinancialMetric.revenue)).where(FinancialMetric.metric_date > now.date() - timedelta(days=21)))
    assert float(after_rev) < float(before_rev)
    for p in products:
        assert db.scalar(select(Inventory).where(Inventory.product_id == p.id)).quantity_available >= invs[p.id]


def test_inventory_shortage(db):
    run = SimulationEngine(db).run("inventory_shortage", {"level": 0.25})
    inv = db.get(Inventory, __import__("uuid").UUID(run.parameters["inventory_id"]))
    assert inv.quantity_available <= inv.safety_stock * 0.25 + 1
    assert run.effects["open_orders_at_risk"] >= 0
    run2 = SimulationEngine(db).run("inventory_shortage", {"component_type": "STEEL_CASTING", "level": 0.1})
    assert "production_metrics" in run2.affected_entities


def test_production_bottleneck(db):
    now = db.get(SystemState, 1).data_end
    run = SimulationEngine(db).run("production_bottleneck", {"days": 10})
    from app.models import ProductionLine

    line = db.get(ProductionLine, __import__("uuid").UUID(run.parameters["production_line_id"]))
    assert line.status.value == "BOTTLENECKED"
    assert line_avg(db, line.id, now.date() - timedelta(days=9), now.date(), "utilization") >= 0.94
    assert run.effects["unfilled_planned_units"] > 0 and run.effects["orders_delayed"] > 0


def test_unknown_scenario_and_run_bookkeeping(db):
    try:
        SimulationEngine(db).run("volcano")
        assert False
    except ScenarioError:
        pass
    run = SimulationEngine(db).run("bearing_failure", {"days": 3, "intensity": 0.5})
    stored = db.get(SimulationRun, run.id)
    assert stored.status.value == "COMPLETED" and stored.window_end == db.get(SystemState, 1).data_end and stored.parameters["days"] == 3


def test_reset_restores_baseline(db):
    SimulationEngine(db).run("bearing_failure")
    run, summary = SimulationEngine(db).reset()
    assert run.scenario.value == "reset" and summary.counts["orders"] > 0
    assert db.scalar(select(func.count()).select_from(SimulationRun)) == 1  # only the reset run remains
