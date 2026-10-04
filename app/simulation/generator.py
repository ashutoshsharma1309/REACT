"""Deterministic synthetic data generator for Astra Manufacturing.

Design goals
------------
* Deterministic: the same (seed, history_days, anchor) always produces the same dataset.
  Each section draws from its own RNG stream (seed:section) so edits to one section do
  not shuffle the others.
* Correlated: demand drives orders -> production -> inventory -> component consumption ->
  supplier batches -> financials. Machine degradation drives sensor signatures, output and
  defects. Nothing is an independent random number.
* Traceable: every derived row references the rows it was derived from through foreign keys.
"""
from __future__ import annotations

import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import insert, text
from sqlalchemy.orm import Session

from app.database.base import Base, utcnow
from app.models import (
    Company,
    CompetitorSignal,
    Customer,
    FinancialMetric,
    HistoricalIncident,
    Inventory,
    InventorySnapshot,
    Machine,
    MachineComponent,
    MaintenanceRecord,
    MarketSignal,
    Order,
    OrderItem,
    Plant,
    Product,
    ProductionLine,
    ProductionMetric,
    SalesTransaction,
    Sensor,
    SensorReading,
    Supplier,
    SupplierBatch,
    SystemState,
)
from app.models.enums import (
    BatchStatus,
    CompetitorSignalType,
    ComponentCondition,
    Criticality,
    CustomerSegment,
    IncidentType,
    MaintenanceType,
    OrderStatus,
    PaymentStatus,
    SensorType,
    Severity,
    SignalType,
)
from app.observability import get_logger, log_event
from app.simulation import catalog as cat
from app.simulation.clock import OPERATING_HOURS_PER_DAY, is_operating, is_working_day, shift_progress, truncate_to_hour
from app.simulation.history import HISTORICAL_EVENTS, HistoryEffects

log = get_logger("react.simulation.generator")

GENERATOR_VERSION = "1.0"
TRANSIT_DAYS = {"South India": 2, "West India": 3, "North India": 4, "East India": 4, "Middle East": 6, "Southeast Asia": 7}
FIXED_OPERATING_COST_PER_DAY = 650_000.0
POWER_COST_PER_KWH = 8.5
DOWNTIME_OVERHEAD_PER_HOUR = 12_000.0
# E[segment qty multiplier] weighted by segment order share; keeps E[line-item qty] == typical_order_qty
SEGMENT_QTY_NORMALISER = sum(s["weight"] * s["orders_mult"] * s["qty_mult"] for s in cat.SEGMENTS.values()) / sum(s["weight"] * s["orders_mult"] for s in cat.SEGMENTS.values())


@dataclass
class GeneratorConfig:
    seed: int = 42
    history_days: int = 120
    sensor_interval_minutes: int = 60
    anchor: datetime | None = None  # simulated "now" (naive UTC); default: current hour


@dataclass
class SeedSummary:
    seed: int
    data_start: datetime
    data_end: datetime
    counts: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"seed": self.seed, "data_start": self.data_start, "data_end": self.data_end, "counts": self.counts, "duration_seconds": round(self.duration_seconds, 2)}


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's Poisson sampler (deterministic given rng)."""
    if lam <= 0:
        return 0
    if lam > 60:  # normal approximation for large means
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class SyntheticDataGenerator:
    def __init__(self, db: Session, config: GeneratorConfig | None = None) -> None:
        self.db = db
        self.cfg = config or GeneratorConfig()
        self.data_end = truncate_to_hour(self.cfg.anchor or utcnow())
        self.data_start = self.data_end - timedelta(days=self.cfg.history_days)
        self.days: list[date] = [(self.data_start + timedelta(days=i)).date() for i in range(self.cfg.history_days + 1)]
        self.history = HistoryEffects(self.data_end, HISTORICAL_EVENTS)
        self.counts: dict[str, int] = defaultdict(int)
        # registries filled during generation
        self.company: Company
        self.plant: Plant
        self.lines: dict[str, ProductionLine] = {}
        self.line_specs: dict[str, cat.LineSpec] = {ls.code: ls for ls in cat.LINES}
        self.machines: dict[str, Machine] = {}
        self.machine_specs: dict[str, cat.MachineSpec] = {m.code: m for m in cat.MACHINES}
        self.components: dict[tuple[str, str], MachineComponent] = {}
        self.sensors: dict[tuple[str, str], Sensor] = {}
        self.products: dict[str, Product] = {}
        self.product_specs: dict[str, cat.ProductSpec] = {p.sku: p for p in cat.PRODUCTS}
        self.customers: list[Customer] = []
        self.suppliers: dict[str, list[Supplier]] = defaultdict(list)  # component_type -> [primary, secondary]
        self.supplier_specs: dict[str, cat.SupplierSpec] = {s.name: s for s in cat.SUPPLIERS}
        # simulation traces (also used by market signals & financials)
        self.daily_line_metrics: dict[str, dict[date, dict[str, float]]] = defaultdict(dict)
        self.daily_product_orders: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))
        self.daily_product_produced: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
        self.orders: list[Order] = []

    # ------------------------------------------------------------------------------------
    def rng(self, section: str) -> random.Random:
        return random.Random(f"{self.cfg.seed}:{section}")

    def generate(self) -> SeedSummary:
        t0 = time.perf_counter()
        log_event(log, "simulation.seed.started", seed=self.cfg.seed, history_days=self.cfg.history_days, data_end=self.data_end)
        self._create_organisation()
        self.db.flush()  # assign ids used as plain FKs below
        self._create_machines()
        self.db.flush()
        self._create_products()
        self._create_customers()
        self._create_suppliers()
        self.db.flush()
        self._simulate_business_days()
        self._create_sensor_readings()
        self._create_maintenance_and_history()
        self._create_market_signals()
        self._create_competitor_signals()
        self._create_sales_transactions()
        self._finalise_customers()
        self._create_system_state()
        self.db.commit()
        summary = SeedSummary(self.cfg.seed, self.data_start, self.data_end, dict(self.counts), time.perf_counter() - t0)
        log_event(log, "simulation.seed.completed", **summary.as_dict())
        return summary

    # ---- 1. organisation -----------------------------------------------------------------
    def _create_organisation(self) -> None:
        self.company = Company(**cat.COMPANY)
        self.db.add(self.company)
        self.plant = Plant(company=self.company, **cat.PLANT)
        self.db.add(self.plant)
        for ls in cat.LINES:
            line = ProductionLine(plant=self.plant, name=ls.name, code=ls.code, capacity_units_per_day=ls.capacity, description=ls.description)
            self.db.add(line)
            self.lines[ls.code] = line
        self.counts["companies"] = 1
        self.counts["plants"] = 1
        self.counts["production_lines"] = len(cat.LINES)

    # ---- 2. machines, components, sensors ------------------------------------------------
    def _create_machines(self) -> None:
        rng = self.rng("machines")
        for idx, ms in enumerate(cat.MACHINES):
            install = date(ms.install_year, 1 + (idx * 5) % 12, 1 + (idx * 7) % 27)
            machine = Machine(
                production_line=self.lines[ms.line], name=ms.name, code=ms.code, machine_type=ms.machine_type,
                manufacturer=ms.manufacturer, model=ms.model, installation_date=install, criticality=Criticality(ms.criticality),
                rated_power_kw=ms.rated_power_kw,
            )
            self.db.add(machine)
            self.machines[ms.code] = machine
            # components with realistic wear based on age since (re)installation
            for cidx, (cname, ctype, life_hours) in enumerate(ms.components):
                age_days = (self.data_end.date() - install).days
                # some components were replaced during history (see events); others aged since install
                replaced_days_ago = None
                for ev in HISTORICAL_EVENTS:
                    if ev.machine_code == ms.code and ev.component_type == ctype and ev.downtime_hours > 0:
                        replaced_days_ago = ev.days_before_end
                if replaced_days_ago is not None:
                    installed_on = self.data_end.date() - timedelta(days=replaced_days_ago)
                else:
                    # spread ages so that not everything is new: 15%..85% of expected life consumed
                    consumed = 0.15 + 0.7 * rng.random()
                    installed_on = self.data_end.date() - timedelta(days=int(consumed * life_hours / (OPERATING_HOURS_PER_DAY * 6 / 7)))
                    installed_on = max(installed_on, install)
                op_hours = int((self.data_end.date() - installed_on).days * OPERATING_HOURS_PER_DAY * 6 / 7)
                score = clamp(1 - op_hours / life_hours, 0.12, 1.0)
                cond = ComponentCondition.GOOD if score > 0.5 else ComponentCondition.WORN if score > 0.25 else ComponentCondition.CRITICAL
                comp = MachineComponent(
                    machine=machine, name=cname, component_type=ctype, part_number=f"{ms.code}-{ctype[:3]}-{cidx + 1:02d}",
                    installed_on=installed_on, expected_life_hours=life_hours, operating_hours=op_hours, condition=cond,
                    condition_score=round(score, 3), failure_probability=round(0.02 + 0.3 * (1 - score) ** 2, 4),
                )
                self.db.add(comp)
                self.components[(ms.code, ctype)] = comp
                self.counts["machine_components"] += 1
            machine.health_score = round(min(c.condition_score for c in machine.components) * 0.4 + 0.6, 3)
            for stype in ms.sensors:
                if stype == "TEMPERATURE":
                    lo, hi, warn, crit = ms.temp_profile
                elif stype == "VIBRATION":
                    lo, hi, warn, crit = ms.vib_profile
                elif stype == "PRESSURE":
                    lo, hi, warn, crit = ms.pressure_profile
                elif stype == "RPM":
                    lo, hi, warn, crit = ms.rpm_profile
                else:  # POWER
                    lo, hi, warn, crit = ms.rated_power_kw * 0.4, ms.rated_power_kw * 0.92, ms.rated_power_kw * 1.08, ms.rated_power_kw * 1.25
                sensor = Sensor(
                    machine=machine, code=f"{ms.code}-{stype[:4]}", sensor_type=SensorType(stype), unit=cat.SENSOR_UNITS[stype],
                    normal_min=round(lo, 2), normal_max=round(hi, 2), warning_threshold=round(warn, 2), critical_threshold=round(crit, 2),
                )
                self.db.add(sensor)
                self.sensors[(ms.code, stype)] = sensor
                self.counts["sensors"] += 1
        self.counts["machines"] = len(cat.MACHINES)

    # ---- 3. products ---------------------------------------------------------------------
    def _create_products(self) -> None:
        for ps in cat.PRODUCTS:
            product = Product(
                company_id=self.company.id, production_line=self.lines[ps.line], sku=ps.sku, name=ps.name,
                category=ps.category, unit_cost=ps.unit_cost, selling_price=ps.selling_price, target_margin=ps.target_margin,
            )
            self.db.add(product)
            self.products[ps.sku] = product
        self.counts["products"] = len(cat.PRODUCTS)

    # ---- 4. customers --------------------------------------------------------------------
    def _create_customers(self, n: int = 120) -> None:
        rng = self.rng("customers")
        regions = list(cat.REGIONS)
        region_w = [cat.REGIONS[r]["weight"] for r in regions]
        industries = list(cat.INDUSTRIES)
        segments = list(cat.SEGMENTS)
        seg_w = [cat.SEGMENTS[s]["weight"] for s in segments]
        used: set[str] = set()
        for i in range(n):
            region = rng.choices(regions, region_w)[0]
            industry = rng.choice(industries)
            segment = rng.choices(segments, seg_w)[0]
            while True:
                name = f"{rng.choice(cat.CUSTOMER_NAME_PARTS['prefix'])} {rng.choice(cat.CUSTOMER_NAME_PARTS['industry_word'][industry])} {rng.choice(cat.CUSTOMER_NAME_PARTS['suffix'])}"
                if name not in used:
                    used.add(name)
                    break
            years = {"ENTERPRISE": (3, 12), "MID_MARKET": (1, 8), "SMB": (0, 5)}[segment]
            since = self.data_start.date() - timedelta(days=int(rng.uniform(*years) * 365) + rng.randint(30, 300))
            cust = Customer(
                company_id=self.company.id, name=name, industry=industry, location=rng.choice(cat.REGIONS[region]["cities"]), region=region,
                segment=CustomerSegment(segment), customer_since=since, credit_rating=rng.choices(["A", "B", "C"], [0.6, 0.3, 0.1])[0],
            )
            self.db.add(cust)
            self.customers.append(cust)
        self.counts["customers"] = n

    # ---- 5. suppliers --------------------------------------------------------------------
    def _create_suppliers(self) -> None:
        for ss in cat.SUPPLIERS:
            sup = Supplier(
                company_id=self.company.id, name=ss.name, component_type=ss.component_type, location=ss.location, country=ss.country,
                reliability_score=ss.reliability, lead_time_days=ss.lead_time_days, baseline_lead_time_days=ss.lead_time_days,
                unit_cost=ss.unit_cost, is_primary=ss.is_primary,
            )
            self.db.add(sup)
            self.suppliers[ss.component_type].append(sup)
        for lst in self.suppliers.values():
            lst.sort(key=lambda s: not s.is_primary)
        self.counts["suppliers"] = len(cat.SUPPLIERS)

    # ---- demand model ---------------------------------------------------------------------
    def _demand_multiplier(self, ps: cat.ProductSpec, d: date, day_index: int) -> float:
        """Seasonality x trend x weekday for a product on a day (no noise)."""
        doy = d.timetuple().tm_yday
        phase = {"Industrial Valves": 0.4, "Precision Bearings": 1.9, "Gear Assemblies": 2.4, "Hydraulic Fittings": 3.1, "Pump Components": 0.9}[ps.category]
        seasonal = 1 + 0.06 * math.sin(2 * math.pi * doy / 365 + phase)
        if d.month in (3, 6, 9, 12) and d.day >= 21:
            seasonal *= 1.06  # quarter-end buying
        trend = 1 + (ps.trend_pct / 100) * (day_index / max(1, self.cfg.history_days))
        weekday = {5: 0.55, 6: 0.15}.get(d.weekday(), 1.0)
        return seasonal * trend * weekday

    def _customer_weights(self, ps: cat.ProductSpec) -> list[float]:
        weights = []
        for c in self.customers:
            w = cat.INDUSTRIES[c.industry][ps.category] * cat.SEGMENTS[c.segment.value]["orders_mult"] * ps.region_bias.get(c.region, 1.0)
            weights.append(w)
        return weights

    # ---- 6. day-by-day business simulation ----------------------------------------------
    def _simulate_business_days(self) -> None:
        rng_orders = self.rng("orders")
        rng_prod = self.rng("production")
        rng_inv = self.rng("inventory")
        rng_fin = self.rng("finance")
        cust_weights = {sku: self._customer_weights(ps) for sku, ps in self.product_specs.items()}
        products_by_line: dict[str, list[cat.ProductSpec]] = defaultdict(list)
        for ps in cat.PRODUCTS:
            products_by_line[ps.line].append(ps)

        # --- 6a. orders & items (demand) ---
        order_seq = 0
        day_orders: dict[date, list[Order]] = defaultdict(list)
        for di, d in enumerate(self.days):
            orders_today: dict[Any, Order] = {}
            for ps in cat.PRODUCTS:
                lam = ps.base_daily_demand * self._demand_multiplier(ps, d, di) / ps.typical_order_qty
                n_items = poisson(rng_orders, lam)
                for _ in range(n_items):
                    # 35% of items join an existing order of a customer with affinity, creating multi-line orders
                    existing = [o for o in orders_today.values() if cat.INDUSTRIES[o.customer.industry][ps.category] >= 1.0]
                    if existing and rng_orders.random() < 0.35:
                        order = rng_orders.choice(existing)
                    else:
                        cust = rng_orders.choices(self.customers, cust_weights[ps.sku])[0]
                        order = orders_today.get(cust.id)
                        if order is None:
                            order_seq += 1
                            order = Order(
                                order_number=f"SO-{d.strftime('%y%m')}-{order_seq:05d}", customer=cust, order_date=d, region=cust.region,
                                promised_delivery_date=d + timedelta(days=self.line_specs[ps.line].promised_lead_days + TRANSIT_DAYS[cust.region] - 2),
                            )
                            orders_today[cust.id] = order
                    seg = cat.SEGMENTS[order.customer.segment.value]
                    qty = max(1, int(round(ps.typical_order_qty * seg["qty_mult"] / SEGMENT_QTY_NORMALISER * math.exp(rng_orders.gauss(0, 0.2) - 0.2 ** 2 / 2))))
                    price = round(ps.selling_price * (1 - seg["discount"]), 2)
                    # NB: passing order= appends the item to order.items through the relationship
                    OrderItem(order=order, product=self.products[ps.sku], quantity=qty, unit_price=price, unit_cost=ps.unit_cost, line_revenue=round(qty * price, 2))
                    self.daily_product_orders[ps.sku][d] += qty
                    self.counts["order_items"] += 1
            for order in orders_today.values():
                order.total_quantity = sum(i.quantity for i in order.items)
                order.total_revenue = round(sum(float(i.line_revenue) for i in order.items), 2)
                order.total_cost = round(sum(i.quantity * float(i.unit_cost) for i in order.items), 2)
                # promised date = longest lead among lines in the order
                lead = max(self.line_specs[self.product_specs[i.product.sku].line].promised_lead_days for i in order.items)
                order.promised_delivery_date = d + timedelta(days=lead + max(0, TRANSIT_DAYS[order.region] - 2))
                self.db.add(order)
                self.orders.append(order)
                day_orders[d].append(order)
        self.counts["orders"] = len(self.orders)

        # --- 6b. production per line per day (produce-to-demand with inventory correction) ---
        fg_inventory: dict[str, float] = {ps.sku: ps.base_daily_demand * 20 for ps in cat.PRODUCTS}
        fg_history: dict[str, dict[date, tuple[int, int, int]]] = defaultdict(dict)  # sku -> date -> (qty, inbound, outbound)
        ship_dates: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))  # shipments planned later (sku -> date -> units)
        maintenance_downtime = self._maintenance_downtime_by_line_day()
        for di, d in enumerate(self.days):
            for lc, line in self.lines.items():
                ls = self.line_specs[lc]
                specs = products_by_line[lc]
                window = [self.days[j] for j in range(max(0, di - 6), di + 1)]
                demand_by_sku = {ps.sku: sum(self.daily_product_orders[ps.sku][w] for w in window) / len(window) for ps in specs}
                demand_rate = sum(demand_by_sku.values())
                if di < 3:  # warm-up: use nominal demand
                    demand_rate = sum(ps.base_daily_demand for ps in specs) * 0.8
                    demand_by_sku = {ps.sku: ps.base_daily_demand * 0.8 for ps in specs}
                inv_units = sum(fg_inventory[ps.sku] for ps in specs)
                inv_days = inv_units / max(demand_rate, 1)
                target_inv_days = 18.0
                correction = clamp(1 + 0.4 * (target_inv_days - inv_days) / target_inv_days, 0.7, 1.2)
                target = clamp(demand_rate * (7 / 6) * correction, 0.35 * ls.capacity, ls.capacity)  # 6 working days cover 7 days of demand
                eff = self.history.line_day_effect(lc, d)
                working = is_working_day(d)
                downtime = 0.0 if not working else min(OPERATING_HOURS_PER_DAY, eff.downtime_hours + maintenance_downtime[lc].get(d, 0.0) + rng_prod.uniform(0, 0.6))
                operating_hours = 0.0 if not working else OPERATING_HOURS_PER_DAY - downtime
                planned = int(round(target)) if working else 0
                output = int(round(target * eff.output_multiplier * (operating_hours / OPERATING_HOURS_PER_DAY) * rng_prod.uniform(0.965, 1.035))) if working else 0
                defect_rate = clamp(ls.base_defect_rate * eff.defect_multiplier * rng_prod.uniform(0.85, 1.15), 0.0, 0.5) if working else 0.0
                defect_units = int(round(output * defect_rate))
                utilization = output / ls.capacity
                rated = sum(self.machine_specs[m.code].rated_power_kw for m in line.machines)
                power = rated * operating_hours * (0.42 + 0.5 * utilization) * rng_prod.uniform(0.96, 1.04) + rated * 0.05 * (24 - operating_hours)
                pm = ProductionMetric(
                    production_line=line, metric_date=d, planned_units=planned, output_units=output, output_rate=round(output / operating_hours, 2) if operating_hours else 0.0,
                    defect_units=defect_units, defect_rate=round(defect_rate, 4), downtime_hours=round(downtime, 2), operating_hours=round(operating_hours, 2),
                    utilization=round(utilization, 4), power_consumption_kwh=round(power, 1),
                )
                self.db.add(pm)
                self.counts["production_metrics"] += 1
                self.daily_line_metrics[lc][d] = {"output": output, "utilization": utilization, "defect_rate": defect_rate, "downtime": downtime, "planned": planned, "power": power, "events": eff.event_keys}
                good_output = output - defect_units
                total_demand = max(sum(demand_by_sku.values()), 1e-9)
                for ps in specs:
                    share = demand_by_sku[ps.sku] / total_demand
                    produced = good_output * share
                    self.daily_product_produced[ps.sku][d] = produced
                    fg_inventory[ps.sku] += produced

            # shipments for orders placed earlier (ship date = order date + production lead + load-dependent queue)
            for order in day_orders[d]:
                lead = max(self.line_specs[self.product_specs[i.product.sku].line].production_lead_days for i in order.items)
                loads = []
                for j in range(di, min(len(self.days), di + lead)):
                    loads.append(max(self.daily_line_metrics[self.product_specs[i.product.sku].line].get(self.days[j], {}).get("utilization", 0.0) for i in order.items))
                avg_load = sum(loads) / len(loads) if loads else 0.7
                queue_days = 0
                if avg_load > 0.92:
                    queue_days += 1 + int((avg_load - 0.92) * 25)
                events = {e for j in range(di, min(len(self.days), di + lead)) for i in order.items for e in self.daily_line_metrics[self.product_specs[i.product.sku].line].get(self.days[j], {}).get("events", [])}
                if events:
                    queue_days += 2
                ship = d + timedelta(days=lead + queue_days + rng_prod.choice([-1, 0, 0, 0, 1, 2]))
                while not is_working_day(ship):
                    ship += timedelta(days=1)
                order._ship_date = ship  # type: ignore[attr-defined]
                for i in order.items:
                    ship_dates[i.product.sku][ship] += i.quantity
            # apply today's shipments to finished-goods inventory and snapshot
            for ps in cat.PRODUCTS:
                out = ship_dates[ps.sku].get(d, 0)
                inbound = int(round(self.daily_product_produced[ps.sku][d]))
                fg_inventory[ps.sku] = max(0.0, fg_inventory[ps.sku] - out)
                fg_history[ps.sku][d] = (int(round(fg_inventory[ps.sku])), inbound, out)

        # --- 6c. order statuses, delivery dates ---
        end_date = self.data_end.date()
        rng_status = self.rng("order_status")
        for order in self.orders:
            ship: date = getattr(order, "_ship_date")
            transit = TRANSIT_DAYS[order.region] + rng_status.choice([-1, 0, 0, 1])
            delivered = ship + timedelta(days=max(1, transit))
            cancelled = rng_status.random() < 0.025 and order.order_date <= end_date - timedelta(days=3)
            if cancelled:
                order.status = OrderStatus.CANCELLED
                order.actual_delivery_date = None
            elif delivered <= end_date:
                order.status = OrderStatus.DELIVERED
                order.actual_delivery_date = delivered
            elif ship <= end_date:
                order.status = OrderStatus.SHIPPED
            elif order.promised_delivery_date < end_date:
                order.status = OrderStatus.DELAYED
            elif order.order_date + timedelta(days=2) <= end_date:
                order.status = OrderStatus.IN_PRODUCTION
            elif order.order_date + timedelta(days=1) <= end_date:
                order.status = OrderStatus.CONFIRMED
            else:
                order.status = OrderStatus.PENDING
            order._expected_delivery = delivered  # type: ignore[attr-defined]

        # --- 6d. finished goods inventory rows + snapshots ---
        snapshot_rows: list[dict] = []
        for ps in cat.PRODUCTS:
            recent_demand = sum(self.daily_product_orders[ps.sku][d] for d in self.days[-28:]) / 28
            avg_daily = max(recent_demand, ps.base_daily_demand * 0.5)
            inv = Inventory(
                item_type="PRODUCT", product=self.products[ps.sku], item_name=ps.name, warehouse=cat.WAREHOUSES["finished"],
                quantity_available=fg_history[ps.sku][self.days[-1]][0], quantity_reserved=int(sum(ship_dates[ps.sku].get(end_date + timedelta(days=k), 0) for k in range(1, 8))),
                reorder_point=int(avg_daily * 10), safety_stock=int(avg_daily * 5), max_stock=int(avg_daily * 40),
                holding_cost_per_unit_month=round(ps.unit_cost * 0.02, 2), unit_value=ps.unit_cost, avg_daily_consumption=round(avg_daily, 2),
            )
            self.db.add(inv)
            self.db.flush()
            for d, (qty, inbound, out) in fg_history[ps.sku].items():
                snapshot_rows.append({"inventory_id": inv.id, "snapshot_date": d, "quantity_available": qty, "inbound_units": inbound, "outbound_units": out})
            self.counts["inventory"] += 1

        # --- 6e. component inventory, consumption and supplier batches ---
        consumption_by_component: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
        for ps in cat.PRODUCTS:
            bom = {**cat.BOM_BY_CATEGORY[ps.category], **cat.BOM_BY_SKU.get(ps.sku, {})}
            for d, produced in self.daily_product_produced[ps.sku].items():
                for ctype, per_unit in bom.items():
                    consumption_by_component[ctype][d] += produced * per_unit
        batch_seq = 0
        forced_delays = {ev.component_type: ev for ev in HISTORICAL_EVENTS if ev.supplier_batch_delay_days or ev.supplier_quality_pass_rate}
        for ctype in cat.COMPONENT_TYPES:
            sups = self.suppliers[ctype]
            primary_spec = self.supplier_specs[sups[0].name]
            avg_cons = sum(consumption_by_component[ctype].values()) / len(self.days)
            reorder_point = int(avg_cons * (primary_spec.lead_time_days + 4))
            safety = int(avg_cons * 4)
            level = reorder_point * 2.2
            batch_size = max(primary_spec.batch_size, int(avg_cons * (primary_spec.lead_time_days + 12)))
            open_batches: list[SupplierBatch] = []
            history_rows: list[tuple[date, int, int, int]] = []
            forced = forced_delays.get(ctype)
            forced_day = (self.data_end - timedelta(days=forced.days_before_end)).date() if forced else None
            for d in self.days:
                inbound = 0
                for b in list(open_batches):
                    if b._arrival <= d:  # type: ignore[attr-defined]
                        inbound += int(b.quantity * (b.quality_pass_rate or 1.0))
                        open_batches.remove(b)
                level += inbound
                used = consumption_by_component[ctype].get(d, 0.0)
                level = max(0.0, level - used)
                if level <= reorder_point and not open_batches:
                    batch_seq += 1
                    use_secondary = len(sups) > 1 and rng_inv.random() < 0.25
                    sup = sups[1] if use_secondary else sups[0]
                    sspec = self.supplier_specs[sup.name]
                    delay = 0 if rng_inv.random() < sspec.reliability else rng_inv.randint(1, 6)
                    pass_rate = round(clamp(rng_inv.gauss(0.985, 0.012), 0.9, 1.0), 3)
                    expected = d + timedelta(days=sspec.lead_time_days)
                    if forced and forced_day and abs((expected - forced_day).days) <= 8 and forced.supplier_batch_delay_days and delay < forced.supplier_batch_delay_days:
                        delay = forced.supplier_batch_delay_days
                        expected = forced_day - timedelta(days=delay)
                    if forced and forced_day and forced.supplier_quality_pass_rate and abs((expected - forced_day).days) <= 8:
                        pass_rate = forced.supplier_quality_pass_rate
                    arrival = expected + timedelta(days=delay)
                    b = SupplierBatch(
                        supplier=sup, batch_number=f"PO-{d.strftime('%y%m')}-{batch_seq:04d}", component_type=ctype, quantity=batch_size, ordered_date=d,
                        expected_date=expected, unit_cost=sspec.unit_cost, delay_days=delay, quality_pass_rate=pass_rate,
                    )
                    b._arrival = arrival  # type: ignore[attr-defined]
                    if arrival <= end_date:
                        b.received_date = arrival
                        b.status = BatchStatus.REJECTED if pass_rate < 0.85 else BatchStatus.RECEIVED
                    elif expected < end_date:
                        b.status = BatchStatus.DELAYED
                    elif d + timedelta(days=2) <= end_date:
                        b.status = BatchStatus.IN_TRANSIT
                    else:
                        b.status = BatchStatus.ORDERED
                    self.db.add(b)
                    open_batches.append(b)
                    self.counts["supplier_batches"] += 1
                history_rows.append((d, int(level), inbound, int(used)))
            inv = Inventory(
                item_type="COMPONENT", component_type=ctype, item_name=ctype.replace("_", " ").title(), warehouse=cat.WAREHOUSES["components"],
                quantity_available=int(level), reorder_point=reorder_point, safety_stock=safety, max_stock=int(reorder_point * 3),
                holding_cost_per_unit_month=round(primary_spec.unit_cost * 0.015, 2), unit_value=primary_spec.unit_cost, avg_daily_consumption=round(avg_cons, 2),
            )
            self.db.add(inv)
            self.db.flush()
            for d, qty, inbound, used in history_rows:
                snapshot_rows.append({"inventory_id": inv.id, "snapshot_date": d, "quantity_available": qty, "inbound_units": inbound, "outbound_units": used})
            self.counts["inventory"] += 1
        self.db.execute(insert(InventorySnapshot), snapshot_rows)
        self.counts["inventory_snapshots"] = len(snapshot_rows)

        # --- 6f. daily financial metrics ---
        for d in self.days:
            todays = [o for o in day_orders[d] if o.status != OrderStatus.CANCELLED]
            revenue = sum(float(o.total_revenue) for o in todays)
            cogs = sum(float(o.total_cost) for o in todays)
            production_cost = 0.0
            for lc, line in self.lines.items():
                m = self.daily_line_metrics[lc][d]
                avg_unit_cost = sum(ps.unit_cost for ps in products_by_line[lc]) / len(products_by_line[lc])
                production_cost += m["output"] * avg_unit_cost * 0.55 + m["power"] * POWER_COST_PER_KWH + m["downtime"] * DOWNTIME_OVERHEAD_PER_HOUR
            operating = (FIXED_OPERATING_COST_PER_DAY if is_working_day(d) else FIXED_OPERATING_COST_PER_DAY * 0.4) * rng_fin.uniform(0.97, 1.03)
            fm = FinancialMetric(
                company_id=self.company.id, metric_date=d, revenue=round(revenue, 2), cost=round(cogs, 2), gross_margin=round((revenue - cogs) / revenue, 4) if revenue else 0.0,
                operating_cost=round(operating, 2), production_cost=round(production_cost, 2), profit=round(revenue - cogs - operating, 2),
                orders_count=len(todays), units_sold=sum(o.total_quantity for o in todays),
            )
            self.db.add(fm)
            self.counts["financial_metrics"] += 1
        self.db.flush()

    def _maintenance_downtime_by_line_day(self) -> dict[str, dict[date, float]]:
        """Preventive maintenance is scheduled on Sundays (no production impact); inspections cost ~1h on weekdays."""
        result: dict[str, dict[date, float]] = defaultdict(dict)
        self._maintenance_plan: list[tuple[str, date, str, float]] = []  # machine_code, date, type, downtime
        for idx, ms in enumerate(cat.MACHINES):
            for d in self.days:
                offset = (d - self.data_start.date()).days
                if d.weekday() == 6 and (offset // 7 + idx) % 4 == 0:  # every 4th Sunday, staggered
                    self._maintenance_plan.append((ms.code, d, "PREVENTIVE", 4.0))
                elif d.weekday() == 2 and (offset // 7 + idx) % 6 == 3:  # inspection every 6 weeks on a Wednesday
                    self._maintenance_plan.append((ms.code, d, "INSPECTION", 1.0))
                    result[ms.line][d] = result[ms.line].get(d, 0.0) + 0.5  # inspections cost half the line time
        return result

    # ---- 7. sensor readings --------------------------------------------------------------
    def _create_sensor_readings(self) -> None:
        rng = self.rng("sensors")
        step = timedelta(minutes=self.cfg.sensor_interval_minutes)
        timestamps: list[datetime] = []
        ts = self.data_start
        while ts <= self.data_end:
            timestamps.append(ts)
            ts += step
        rows: list[dict] = []
        for ms in cat.MACHINES:
            line_metrics = self.daily_line_metrics[ms.line]
            last_value: dict[str, float] = {}
            for ts in timestamps:
                eff = self.history.machine_effect(ms.code, ts)
                day_m = line_metrics.get(ts.date(), {})
                load = clamp(day_m.get("utilization", 0.7), 0.3, 1.1)
                operating = is_operating(ts) and not eff.down
                warm = 1.0
                if operating:
                    prog = shift_progress(ts)
                    warm = 0.72 + 0.28 * min(1.0, prog * OPERATING_HOURS_PER_DAY / 1.5)  # 90 min warm-up
                f = eff.factor
                for stype in ms.sensors:
                    sensor = self.sensors[(ms.code, stype)]
                    gain = eff.sensor_gain.get(stype, 0.0) * f
                    if stype == "TEMPERATURE":
                        lo, hi = sensor.normal_min, sensor.normal_max
                        if operating:
                            v = (lo + (hi - lo) * (0.3 + 0.5 * load)) * warm * (1 + gain) + rng.gauss(0, (hi - lo) * 0.045)
                        else:
                            v = (lo * 0.55 if ms.machine_type == "Heat Treatment Furnace" else 29.0) + rng.gauss(0, 0.8)
                    elif stype == "VIBRATION":
                        lo, hi = sensor.normal_min, sensor.normal_max
                        v = (lo + (hi - lo) * (0.25 + 0.55 * load)) * (1 + gain) + rng.gauss(0, (hi - lo) * 0.09) if operating else abs(rng.gauss(0.15, 0.05))
                    elif stype == "POWER":
                        v = ms.rated_power_kw * (0.42 + 0.5 * load) * warm * (1 + gain) + rng.gauss(0, ms.rated_power_kw * 0.03) if operating else ms.rated_power_kw * 0.04 + abs(rng.gauss(0, 0.2))
                    elif stype == "PRESSURE":
                        lo, hi = sensor.normal_min, sensor.normal_max
                        v = (lo + (hi - lo) * (0.35 + 0.45 * load)) * (1 + gain) + rng.gauss(0, (hi - lo) * 0.03) if operating else rng.uniform(0, 3)
                    else:  # RPM
                        lo, hi = sensor.normal_min, sensor.normal_max
                        v = (lo + (hi - lo) * (0.3 + 0.5 * load)) * (1 + gain) + rng.gauss(0, (hi - lo) * 0.02) if operating else 0.0
                    if eff.stuck_sensor_type == stype and stype in last_value:
                        v = last_value[stype]  # frozen transmitter
                    v = round(max(0.0, v), 3)
                    last_value[stype] = v
                    rows.append({"sensor_id": sensor.id, "timestamp": ts, "value": v, "quality": "GOOD"})
        for i in range(0, len(rows), 20000):
            self.db.execute(insert(SensorReading), rows[i : i + 20000])
        self.counts["sensor_readings"] = len(rows)

    # ---- 8. maintenance + historical incidents -----------------------------------------
    def _create_maintenance_and_history(self) -> None:
        rng = self.rng("maintenance")
        for code, d, mtype, downtime in self._maintenance_plan:
            machine = self.machines[code]
            comp = rng.choice(machine.components)
            desc = {
                "PREVENTIVE": f"Scheduled preventive maintenance: lubrication, alignment check and filter change on {machine.name}.",
                "INSPECTION": f"Routine condition inspection of {comp.name} on {machine.name}; vibration and thermal readings logged.",
            }[mtype]
            self.db.add(MaintenanceRecord(
                machine=machine, component=comp if mtype == "INSPECTION" else None, maintenance_date=d, maintenance_type=MaintenanceType(mtype),
                description=desc, downtime_hours=downtime, technician=rng.choice(cat.TECHNICIANS),
                cost=round(cat.MAINTENANCE_COST_BY_TYPE[mtype] * rng.uniform(0.85, 1.2), 2), outcome="OK",
            ))
            self.counts["maintenance_records"] += 1
        for ev in HISTORICAL_EVENTS:
            peak = ev.peak_at(self.data_end)
            machine = self.machines.get(ev.machine_code) if ev.machine_code else None
            comp = self.components.get((ev.machine_code, ev.component_type)) if ev.machine_code and ev.component_type else None
            resolved = peak + timedelta(hours=ev.downtime_hours) if ev.downtime_hours else peak + timedelta(days=ev.aftermath_days or 1)
            leading: dict[str, Any] = {"ramp_days": ev.ramp_days, "sensor_gain_at_failure": ev.sensor_gain, "output_multiplier": ev.output_multiplier, "defect_multiplier": ev.defect_multiplier}
            if ev.supplier_batch_delay_days:
                leading["supplier_batch_delay_days"] = ev.supplier_batch_delay_days
            if ev.supplier_quality_pass_rate:
                leading["supplier_quality_pass_rate"] = ev.supplier_quality_pass_rate
            self.db.add(HistoricalIncident(
                incident_type=IncidentType(ev.incident_type), machine_id=machine.id if machine else None,
                production_line_id=self.lines[ev.line_code].id if ev.line_code else None, component_id=comp.id if comp else None,
                severity=Severity(ev.severity), occurred_at=peak, resolved_at=resolved, description=ev.description, root_cause=ev.root_cause,
                resolution=ev.resolution, downtime_hours=ev.downtime_hours, estimated_loss=ev.estimated_loss, leading_indicators=leading,
            ))
            self.counts["historical_incidents"] += 1
            if machine and ev.maintenance_description:
                self.db.add(MaintenanceRecord(
                    machine=machine, component=comp, maintenance_date=peak.date(), maintenance_type=MaintenanceType.CORRECTIVE if ev.downtime_hours else MaintenanceType.INSPECTION,
                    description=ev.maintenance_description, downtime_hours=ev.downtime_hours, technician=rng.choice(cat.TECHNICIANS),
                    cost=round((cat.MAINTENANCE_COST_BY_TYPE["CORRECTIVE"] if ev.downtime_hours else cat.MAINTENANCE_COST_BY_TYPE["INSPECTION"]) * rng.uniform(0.9, 1.6), 2),
                    outcome="Component replaced" if ev.downtime_hours else "Recalibrated",
                ))
                self.counts["maintenance_records"] += 1

    # ---- 9. market signals (SYNTHETIC) ---------------------------------------------------
    def _create_market_signals(self) -> None:
        rng = self.rng("market")
        weeks = [d for d in self.days if d.weekday() == 0]
        prev_index: dict[tuple, float] = {}
        products_by_cat: dict[str, list[cat.ProductSpec]] = defaultdict(list)
        for ps in cat.PRODUCTS:
            products_by_cat[ps.category].append(ps)
        for wi, wd in enumerate(weeks):
            di = (wd - self.data_start.date()).days
            for category, specs in products_by_cat.items():
                mult = sum(self._demand_multiplier(ps, wd, di) / 1.0 for ps in specs) / len(specs)
                index = 100 * mult * (1 + rng.gauss(0, 0.02))
                key = ("cat", category)
                growth = (index / prev_index[key] - 1) * 100 if key in prev_index else 0.0
                prev_index[key] = index
                seasonal = 1 + 0.09 * math.sin(2 * math.pi * wd.timetuple().tm_yday / 365)
                self.db.add(MarketSignal(signal_date=wd, signal_type=SignalType.MARKET_DEMAND, product_category=category, demand_index=round(index, 2), growth_rate_pct=round(growth, 2), seasonal_index=round(seasonal, 3), description=f"[SYNTHETIC] Weekly market demand index for {category} in India."))
                self.db.add(MarketSignal(signal_date=wd, signal_type=SignalType.SEASONAL_DEMAND, product_category=category, demand_index=round(100 * seasonal, 2), growth_rate_pct=0.0, seasonal_index=round(seasonal, 3), description=f"[SYNTHETIC] Seasonal demand factor for {category}."))
                if wi % 4 == 3:
                    trend = sum(ps.trend_pct for ps in specs) / len(specs)
                    self.db.add(MarketSignal(signal_date=wd, signal_type=SignalType.CATEGORY_GROWTH, product_category=category, demand_index=round(index, 2), growth_rate_pct=round(trend * (wi + 1) / len(weeks) + rng.gauss(0, 1.0), 2), seasonal_index=round(seasonal, 3), description=f"[SYNTHETIC] Trailing growth of the {category} category."))
                self.counts["market_signals"] += 2 + (1 if wi % 4 == 3 else 0)
            for region, rinfo in cat.REGIONS.items():
                bias = sum(ps.region_bias.get(region, 1.0) - 1 for ps in cat.PRODUCTS) / len(cat.PRODUCTS)
                index = 100 * (1 + bias * (wi / max(1, len(weeks) - 1))) * (1 + rng.gauss(0, 0.025))
                key = ("region", region)
                growth = (index / prev_index[key] - 1) * 100 if key in prev_index else 0.0
                prev_index[key] = index
                self.db.add(MarketSignal(signal_date=wd, signal_type=SignalType.REGIONAL_DEMAND, region=region, demand_index=round(index, 2), growth_rate_pct=round(growth, 2), description=f"[SYNTHETIC] Regional industrial-components demand index for {region}."))
                self.counts["market_signals"] += 1
            for ps in cat.PRODUCTS:
                if ps.trend_pct and wi % 2 == 0:
                    g = ps.trend_pct * (wi + 1) / len(weeks) + rng.gauss(0, 1.5)
                    region = max(ps.region_bias, key=ps.region_bias.get) if ps.region_bias else None
                    self.db.add(MarketSignal(signal_date=wd, signal_type=SignalType.PRODUCT_DEMAND_GROWTH, product_category=ps.category, product_id=self.products[ps.sku].id, region=region, demand_index=round(100 + g, 2), growth_rate_pct=round(g, 2), description=f"[SYNTHETIC] Demand growth signal for {ps.name}" + (f" in {region}." if region else ".")))
                    self.counts["market_signals"] += 1

    # ---- 10. competitor signals (SYNTHETIC) ----------------------------------------------
    def _create_competitor_signals(self) -> None:
        script = [
            (110, "Vulcan Precision Works", "PRICE_CHANGE", "Precision Bearings", "West India", 3.5, None, "raised list prices on deep-groove bearings by 3.5%"),
            (98, "Meridian Industrial Components", "PRODUCT_LAUNCH", "Gear Assemblies", None, None, None, "launched a compact planetary gearbox series targeting construction OEMs"),
            (84, "Orion Flow Systems", "AVAILABILITY", "Industrial Valves", "Middle East", None, 0.72, "reported extended lead times on control valves for Gulf customers"),
            (70, "Vulcan Precision Works", "MARKET_ACTIVITY", "Hydraulic Fittings", "North India", None, None, "opened a distribution centre near Delhi"),
            (56, "Orion Flow Systems", "PRICE_CHANGE", "Industrial Valves", "South India", 6.0, None, "raised control valve prices by 6% citing actuator costs"),
            (45, "Meridian Industrial Components", "AVAILABILITY", "Precision Bearings", "South India", None, 0.85, "allocation constraints on tapered roller bearings"),
            (33, "Vulcan Precision Works", "PRICE_CHANGE", "Hydraulic Fittings", None, 5.5, None, "increased hydraulic coupling prices by 5.5% after steel cost pass-through"),
            (27, "Orion Flow Systems", "AVAILABILITY", "Industrial Valves", "Middle East", None, 0.55, "suspended new control valve orders for Middle East delivery until next quarter"),
            (18, "Meridian Industrial Components", "PRODUCT_LAUNCH", "Precision Bearings", None, None, None, "announced a ceramic hybrid bearing line for high-speed spindles"),
            (12, "Vulcan Precision Works", "MARKET_ACTIVITY", "Pump Components", "West India", None, None, "aggressive discounting on impellers and casings to clear stock"),
            (9, "Orion Flow Systems", "PRICE_CHANGE", "Industrial Valves", None, 4.0, None, "another 4% list price increase on industrial valves"),
            (4, "Meridian Industrial Components", "AVAILABILITY", "Gear Assemblies", "South India", None, 0.8, "gearbox lead times extended to 10 weeks"),
        ]
        for days_ago, name, stype, category, region, price_change, availability, text_ in script:
            d = self.data_end.date() - timedelta(days=days_ago)
            self.db.add(CompetitorSignal(
                signal_date=d, competitor_name=name, signal_type=CompetitorSignalType(stype), product_category=category, region=region,
                price_change_pct=price_change, availability_score=availability, description=f"[SYNTHETIC] {name} {text_}.",
                details={"days_before_data_end": days_ago},
            ))
            self.counts["competitor_signals"] += 1

    # ---- 11. sales transactions ---------------------------------------------------------
    def _create_sales_transactions(self) -> None:
        rng = self.rng("sales")
        end = self.data_end.date()
        seq = 0
        for order in self.orders:
            if order.status not in (OrderStatus.SHIPPED, OrderStatus.DELIVERED):
                continue
            ship: date = getattr(order, "_ship_date")
            terms = {"ENTERPRISE": 60, "MID_MARKET": 45, "SMB": 30}[order.customer.segment.value]
            due = ship + timedelta(days=terms)
            if due <= end:
                status = PaymentStatus.PAID if rng.random() < 0.9 else PaymentStatus.OVERDUE
            else:
                status = PaymentStatus.PAID if rng.random() < 0.25 else PaymentStatus.PENDING
            seq += 1
            self.db.add(SalesTransaction(order_id=order.id, customer_id=order.customer_id, invoice_number=f"INV-{ship.strftime('%y%m')}-{seq:05d}", transaction_date=ship, amount=order.total_revenue, payment_status=status, payment_terms_days=terms))
            self.counts["sales_transactions"] += 1

    def _finalise_customers(self) -> None:
        totals: dict[Any, list[float]] = defaultdict(list)
        for o in self.orders:
            if o.status != OrderStatus.CANCELLED:
                totals[o.customer_id].append(float(o.total_revenue))
        for c in self.customers:
            vals = totals.get(c.id, [])
            c.average_order_value = round(sum(vals) / len(vals), 2) if vals else 0.0

    def _create_system_state(self) -> None:
        self.db.add(SystemState(id=1, seed=self.cfg.seed, data_start=self.data_start, data_end=self.data_end, generator_version=GENERATOR_VERSION, domain="manufacturing", profile={}))


# ---- public helpers ------------------------------------------------------------------------
def clear_all_data(db: Session) -> None:
    """Remove every row from every REACT table (used by reset / reseed)."""
    import app.models  # noqa: F401

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        db.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    else:
        for t in reversed(Base.metadata.sorted_tables):
            db.execute(t.delete())
    db.commit()
    db.expunge_all()  # drop stale identity-map entries (e.g. the old SystemState row)


def seed_database(db: Session, config: GeneratorConfig | None = None, clear: bool = True, domain: str | None = None, profile: dict | None = None, scenario: str | None = None) -> SeedSummary:
    """Generate a deterministic dataset for a domain pack (default: REACT_DOMAIN, i.e. merchant)."""
    from app.domains import MERCHANT, default_domain

    domain = domain or default_domain()
    if clear:
        clear_all_data(db)
    if domain == MERCHANT:
        from app.domains.merchant.generator import MerchantDataGenerator

        cfg = config or GeneratorConfig()
        summary = MerchantDataGenerator(db, seed=cfg.seed, history_days=cfg.history_days, anchor=cfg.anchor, profile=profile, scenario=scenario).generate()
    else:
        summary = SyntheticDataGenerator(db, config).generate()
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("ANALYZE"))  # planner statistics are stale after TRUNCATE + bulk insert
        db.commit()
    return summary
