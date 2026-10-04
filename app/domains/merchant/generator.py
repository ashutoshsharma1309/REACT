"""Deterministic merchant simulation (SYNTHETIC data for REACT Merchant).

One pipeline produces every table, so the data is coherent:

    demand (SKU x day x channel x zone, weekday / payday / promotion / scenario effects)
      -> orders and order lines (customers by zone and segment, baskets, discounts, payment methods)
      -> stock movements (sales draw stock down; demand beyond stock is lost, never recorded as a sale)
      -> purchase orders (each supplier's order days, reorder point and order-up-to level from recent velocity)
      -> supplier deliveries (expected date = order date + quoted lead time; reliability decides lateness)
      -> pricing history, expenses, daily financial metrics, inventory snapshots

Determinism: each concern draws from its own seeded RNG stream and every id is derived from the seed, so the same
(seed, history_days, anchor, profile, scenario) always yields identical business rows (only bookkeeping timestamps such as created_at differ). "Today" is the date of the simulated
clock (system_state.data_end); the simulation covers the `history_days` complete days before it.
"""
from __future__ import annotations

import math
import random
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.database.base import utcnow
from app.domains.merchant import catalog as cat
from app.domains.merchant.profile import MerchantProfile
from app.domains.merchant.scenarios import BulkPurchase, CostChange, DemandShift, Promotion, SupplierSlowdown, build_scenario, events_as_dicts, resolve_roles
from app.models import (
    Company,
    CompetitorSignal,
    Customer,
    Discount,
    Expense,
    FinancialMetric,
    Inventory,
    InventorySnapshot,
    Order,
    OrderItem,
    PricingHistory,
    Product,
    ProductCategory,
    PurchaseOrder,
    PurchaseOrderItem,
    SalesTransaction,
    Store,
    Supplier,
    SupplierDelivery,
    SystemState,
)
from app.observability import get_logger, log_event
from app.simulation.clock import truncate_to_hour
from app.simulation.generator import SeedSummary, poisson

log = get_logger("react.merchant.generator")

GENERATOR_VERSION = "merchant-1.0"
STORE_FACTORS = (1.0, 0.8, 0.7)  # demand of the 2nd / 3rd store relative to the main store
SAFETY_DAYS = 3
COVER_DAYS = 14  # order-up-to cover beyond the lead time (phones: 6, the merchant keeps phone stock lean)
TREND_PER_DAY = 0.0004
ADHOC_DISCOUNT_SHARE = 0.18  # share of in-store order lines with a counter discount (bargaining)
BASELINE_COST_REVISION_SHARE = 0.08
PROMO_ELASTICITY = 1.5  # demand uplift = 1 + elasticity x discount
BASELINE_CAMPAIGNS = (  # (category, name, pct, start offset, end offset)
    ("POWER", "Monsoon power-bank sale", 0.10, 95, 86),
    ("COMP", "Back-to-campus week", 0.08, 70, 63),
    ("STORAGE", "Back-to-campus week (storage)", 0.08, 70, 63),
)


@dataclass
class _Sku:
    idx: int
    id: uuid.UUID
    sku: str
    name: str
    family: cat.FamilySpec
    category: cat.CategorySpec
    supplier: str
    base_v: float
    price: float
    cost: float
    mrp: float
    alloc: list[tuple[str, str, int, float]] = field(default_factory=list)  # (channel, zone direction, store index, weight)
    w1: float = 1.0
    base_weights: list[float] = field(default_factory=list)


def round_price(x: float) -> float:
    """Indian retail price points: ...49 / ...99 below 1,000; ...99 above."""
    if x < 1000:
        return float(max(49, int(round(x / 50.0)) * 50 - 1))
    return float(int(round(x / 100.0)) * 100 - 1)


class MerchantDataGenerator:
    def __init__(self, db: Session, seed: int = 42, history_days: int = 120, anchor: datetime | None = None, profile: dict[str, Any] | None = None, scenario: str | None = None) -> None:
        self.db = db
        self.seed = seed
        self.history_days = max(30, int(history_days))
        self.data_end = truncate_to_hour(anchor or utcnow())
        self.today: date = self.data_end.date()
        self.data_start = datetime.combine(self.today - timedelta(days=self.history_days), datetime.min.time())
        self.days: list[date] = [self.today - timedelta(days=self.history_days - i) for i in range(self.history_days)]
        self.profile = MerchantProfile.from_dict(profile)
        self.tpl = cat.get_template(self.profile.business_type)
        self.scenario_key = scenario
        self.counts: dict[str, int] = defaultdict(int)
        self._rngs: dict[str, random.Random] = {}
        self.rows: dict[type, list[dict[str, Any]]] = defaultdict(list)

    # ---- helpers ----------------------------------------------------------------------------------------------
    def rng(self, section: str) -> random.Random:
        if section not in self._rngs:
            self._rngs[section] = random.Random(f"{self.seed}:merchant:{section}")
        return self._rngs[section]

    def _id(self) -> uuid.UUID:
        return uuid.UUID(int=self.rng("ids").getrandbits(128), version=4)

    def offset(self, d: date) -> int:
        return (self.today - d).days

    def add(self, model: type, **row: Any) -> dict[str, Any]:
        row.setdefault("id", self._id())
        self.rows[model].append(row)
        return row

    # ---- public -----------------------------------------------------------------------------------------------
    def generate(self) -> SeedSummary:
        started = time.perf_counter()
        log_event(log, "merchant.seed.started", seed=self.seed, history_days=self.history_days, business=self.profile.business_name, scenario=self.scenario_key)
        self._organisation()
        self._catalogue()
        self.roles = resolve_roles(self.tpl, self.profile.categories, self.skus_by_family, self.profile.store_zone())
        self.scenario = build_scenario(self.scenario_key, self.roles)
        self.events = list(self.scenario.events) if self.scenario else []
        self._pricing_and_discounts()
        self._customers_pool()
        self._simulate()
        self._finalise()
        self._insert_all()
        summary = SeedSummary(self.seed, self.data_start, self.data_end, dict(self.counts), time.perf_counter() - started)
        log_event(log, "merchant.seed.completed", seed=self.seed, counts=dict(self.counts), duration_s=round(summary.duration_seconds, 2), scenario=self.scenario_key)
        return summary

    # ---- 1. merchant, stores, categories, suppliers --------------------------------------------------------------
    def _organisation(self) -> None:
        p = self.profile
        self.company = self.add(Company, name=p.business_name, industry="Retail", headquarters=f"{p.locality}, {p.city}", currency="INR", business_type=self.tpl.label, city=p.city, state=p.state, created_at=self.data_start)
        main_dir = p.store_zone()
        others = [d for d in ("East", "West", "North", "Central", "South") if d != main_dir]
        self.stores: list[dict[str, Any]] = []
        for i in range(p.store_count):
            direction = main_dir if i == 0 else others[i - 1]
            locality = p.locality if i == 0 else cat.localities(p.city, direction)[0]
            self.stores.append(self.add(Store, company_id=self.company["id"], code=f"S{i + 1}", name=f"{p.business_name}, {locality}", store_type="PHYSICAL", locality=locality, city=p.city, region=f"{direction} {p.city}", opened_on=self.days[0] - timedelta(days=900 - 300 * i), is_active=True, _dir=direction))
        self.counts["stores"] = len(self.stores)
        self.categories = {c.code: c for c in self.tpl.categories if c.code in self.profile.categories}
        self.category_rows = {code: self.add(ProductCategory, company_id=self.company["id"], code=code, name=c.name, target_margin=c.target_margin, description=c.description) for code, c in self.categories.items()}
        self.counts["product_categories"] = len(self.category_rows)
        used = {f.supplier for f in self.tpl.families if f.category in self.categories}
        self.suppliers = {s.code: s for s in self.tpl.suppliers if s.code in used}
        self.supplier_rows = {code: self.add(Supplier, company_id=self.company["id"], name=s.name, component_type=s.supplies[:80], location=s.city, country="India", reliability_score=s.on_time_rate, lead_time_days=s.lead_time_days, baseline_lead_time_days=s.lead_time_days, unit_cost=0.0, status="ACTIVE", is_primary=True) for code, s in self.suppliers.items()}
        self.counts["suppliers"] = len(self.supplier_rows)

    # ---- 2. products (SKUs) and demand allocation --------------------------------------------------------------
    def _catalogue(self) -> None:
        rng = self.rng("catalogue")
        self.skus: list[_Sku] = []
        self.skus_by_family: dict[str, list[str]] = {}
        for fam in self.tpl.families:
            if fam.category not in self.categories:
                continue
            raw = [1.0 / (i + 1) ** 0.9 for i in range(len(fam.variants))]
            norm = sum(raw)
            codes = []
            for i, (label, mult) in enumerate(fam.variants):
                sku_code = f"{fam.code}-{i + 1:02d}" if len(fam.variants) > 1 else fam.code
                price = round_price(fam.price * mult)
                cost = float(round(price * (1 - fam.margin)))
                mrp = round_price(price * (1.05 if fam.category == "PHONES" else rng.uniform(1.15, 1.4)))
                s = _Sku(len(self.skus), self._id(), sku_code, fam.name.replace("{v}", label).strip(), fam, self.categories[fam.category], fam.supplier, fam.daily_units * raw[i] / norm, price, cost, max(mrp, price))
                self.skus.append(s)
                codes.append(sku_code)
            self.skus_by_family[fam.code] = codes
        self.sku_by_code = {s.sku: s for s in self.skus}
        main_dir = self.stores[0]["_dir"]
        dirs = cat.ZONE_DIRECTIONS
        for s in self.skus:
            os_ = s.category.online_share
            alloc: list[tuple[str, str, int, float]] = []
            for k, store in enumerate(self.stores):
                sd = store["_dir"]
                nb = cat.NEIGHBOURS[sd]
                far = [d for d in dirs if d != sd and d not in nb]
                for d in dirs:
                    share = cat.STORE_ZONE_MIX["same"] if d == sd else (cat.STORE_ZONE_MIX["neighbour"] / len(nb) if d in nb else cat.STORE_ZONE_MIX["far"] / max(1, len(far)))
                    alloc.append(("STORE", d, k, cat.CHANNEL_MIX["STORE"] * STORE_FACTORS[k] * share))
            for d in dirs:
                alloc.append(("ONLINE", d, 0, cat.CHANNEL_MIX["ONLINE"] * os_ * cat.ONLINE_ZONE_MIX[d]))
                nb = cat.NEIGHBOURS[main_dir]
                far = [x for x in dirs if x != main_dir and x not in nb]
                share = 0.55 if d == main_dir else (0.35 / len(nb) if d in nb else 0.10 / max(1, len(far)))
                alloc.append(("WHATSAPP", d, 0, cat.CHANNEL_MIX["WHATSAPP"] * os_ * share))
            s.alloc = alloc
            s.w1 = sum(w for ch, _, k, w in alloc if not (ch == "STORE" and k > 0))  # single-store total: base_v keeps its meaning
        self.counts["products"] = len(self.skus)

    # ---- 3. pricing history, promotions ------------------------------------------------------------------------
    def _pricing_and_discounts(self) -> None:
        rng = self.rng("pricing")
        self.price_events: dict[int, list[tuple[int, float, bool, str]]] = defaultdict(list)  # sku idx -> (offset, cost mult, pass_through, reason note)
        protected = {self.roles["stockout_sku"], self.roles["margin_sku"], self.roles["excess_sku"]}
        for s in self.skus:
            self.add(PricingHistory, product_id=s.id, effective_date=self.days[0] - timedelta(days=60), selling_price=s.price, unit_cost=s.cost, mrp=s.mrp, reason="INITIAL", note="Price list at the start of the history")
            if s.sku not in protected and rng.random() < BASELINE_COST_REVISION_SHARE:
                self.price_events[s.idx].append((rng.randint(25, self.history_days - 10), rng.uniform(1.03, 1.06), True, "Supplier price revision passed on to the shelf price"))
        for e in self.events:
            if isinstance(e, CostChange):
                note = "Supplier price increase; shelf price unchanged" if not e.pass_through else "Supplier price increase passed on"
                self.price_events[self.sku_by_code[e.sku].idx].append((e.start, e.cost_multiplier, e.pass_through, note))
        for evs in self.price_events.values():
            evs.sort(key=lambda x: -x[0])
        # standing counter discount + campaigns
        cid = self.company["id"]
        self.adhoc = self.add(Discount, company_id=cid, code="COUNTER", name="Counter discount (staff discretion)", scope="AD_HOC", discount_pct=0.04, start_date=self.days[0] - timedelta(days=365), end_date=self.today + timedelta(days=365), channel="STORE")
        self.promos: list[tuple[dict[str, Any], set[int], int, int, float]] = []  # (row, sku idxs, start offset, end offset, pct)
        for code, name, pct, start, end in BASELINE_CAMPAIGNS:
            if code not in self.categories or start >= self.history_days:
                continue
            row = self.add(Discount, company_id=cid, code=f"CAMP-{code}-{start}", name=name, scope="CATEGORY", discount_pct=pct, category_id=self.category_rows[code]["id"], start_date=self.today - timedelta(days=start), end_date=self.today - timedelta(days=end), channel=None)
            self.promos.append((row, {s.idx for s in self.skus if s.category.code == code}, start, end, pct))
        for e in self.events:
            if isinstance(e, Promotion):
                idxs = {self.sku_by_code[x].idx for x in e.skus} | ({s.idx for s in self.skus if s.category.code == e.category} if e.category else set())
                scope = "PRODUCT" if e.skus else "CATEGORY"
                row = self.add(Discount, company_id=cid, code=f"PROMO-{len(self.promos) + 1}", name=e.name, scope=scope, discount_pct=e.pct, product_id=self.sku_by_code[e.skus[0]].id if e.skus else None, category_id=self.category_rows[e.category]["id"] if e.category else None, start_date=self.today - timedelta(days=e.start), end_date=self.today - timedelta(days=max(e.end, 0)) if e.end else self.today + timedelta(days=14), channel=None)
                self.promos.append((row, idxs, e.start, e.end, e.pct))
        self.counts["discounts"] = len(self.rows[Discount])

    def _price_at(self, s: _Sku, off: int) -> tuple[float, float]:
        price, cost = s.price, s.cost
        for start, mult, pass_through, _ in self.price_events.get(s.idx, []):
            if off <= start:
                cost = float(round(cost * mult))
                if pass_through:
                    price = round_price(price * mult)
        return price, cost

    def _promo_for(self, idx: int, off: int) -> tuple[dict[str, Any], float] | None:
        best = None
        for row, idxs, start, end, pct in self.promos:
            if idx in idxs and end < off <= start and (best is None or pct > best[1]):
                best = (row, pct)
        return best

    # ---- 4. existing customers --------------------------------------------------------------------------------
    def _customers_pool(self) -> None:
        rng = self.rng("customers")
        self.pool: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)  # (zone dir, segment) -> customers
        self.customers: list[dict[str, Any]] = []
        main_dir = self.stores[0]["_dir"]
        nb = cat.NEIGHBOURS[main_dir]
        for _ in range(1200):
            r = rng.random()
            d = main_dir if r < 0.45 else (rng.choice(nb) if r < 0.80 else rng.choice([x for x in cat.ZONE_DIRECTIONS if x != main_dir]))
            seg = self._pick_segment(rng, None)
            since = self.days[0] - timedelta(days=rng.randint(10, 720))
            self._new_customer(rng, d, seg, since, rng.choice(("STORE", "STORE", "STORE", "WHATSAPP", "ONLINE")))

    def _pick_segment(self, rng: random.Random, category: str | None) -> cat.SegmentSpec:
        weights = [s.share * (s.affinity.get(category, 1.0) if category else 1.0) for s in self.tpl.segments]
        return rng.choices(self.tpl.segments, weights)[0]

    def _new_customer(self, rng: random.Random, zone_dir: str, seg: cat.SegmentSpec, since: date, channel: str) -> dict[str, Any]:
        city = self.profile.city
        locality = rng.choice(cat.localities(city, zone_dir))
        if seg.code == "SMALL_BUSINESS":
            name = f"{rng.choice(cat.LAST_NAMES)} {rng.choice(cat.SMALL_BUSINESS_NAMES)}"
        else:
            name = f"{rng.choice(cat.FIRST_NAMES)} {rng.choice(cat.LAST_NAMES)}"
        c = self.add(Customer, company_id=self.company["id"], name=name, industry="Small business" if seg.code == "SMALL_BUSINESS" else "Consumer", location=f"{locality}, {city}", region=f"{zone_dir} {city}", segment=seg.code, customer_since=since, average_order_value=0.0, credit_rating="A", locality=locality, acquisition_channel=channel, _seg=seg, _dir=zone_dir, _orders=0)
        self.pool[(zone_dir, seg.code)].append(c)
        self.customers.append(c)
        return c

    # ---- 5. day-by-day simulation -----------------------------------------------------------------------------
    def _demand_factor(self, s: _Sku, entry: tuple[str, str, int, float], off: int) -> float:
        f = 1.0
        for e in self.events:
            if isinstance(e, DemandShift):
                if e.skus and s.sku not in e.skus:
                    continue
                if e.families and s.family.code not in e.families:
                    continue
                if e.category and s.category.code != e.category:
                    continue
                if not (e.skus or e.families or e.category):
                    continue
                if e.zone and entry[1] != e.zone:
                    continue
                f *= e.factor(off)
        return f

    def _simulate(self) -> None:
        rd, ro, rc, rs = self.rng("demand"), self.rng("orders"), self.rng("customers"), self.rng("supply")
        n_stores = len(self.stores)
        wk_norm = sum(cat.WEEKDAY_FACTOR) / 7
        on_hand = [[0] * len(self.skus) for _ in range(n_stores)]
        on_order = [[0] * len(self.skus) for _ in range(n_stores)]
        sales_hist = [[deque(maxlen=28) for _ in self.skus] for _ in range(n_stores)]
        exp_v = [[s.base_v * sum(w for ch, _, k, w in s.alloc if k == st) / s.w1 for s in self.skus] for st in range(n_stores)]
        self.exp_v = exp_v
        self.inv_ids = [[self._id() for _ in self.skus] for _ in range(n_stores)]
        deliveries_due: dict[date, list[dict[str, Any]]] = defaultdict(list)
        sup_skus: dict[str, list[_Sku]] = defaultdict(list)
        for s in self.skus:
            sup_skus[s.supplier].append(s)

        def policy(st: int, s: _Sku) -> tuple[int, int, float]:
            hist = sales_hist[st][s.idx]
            n = len(hist)
            v = (sum(hist) + exp_v[st][s.idx] * (28 - n)) / 28
            sup = self.suppliers[s.supplier]
            review = 7 / len(sup.order_weekdays)
            lead = sup.lead_time_days
            cover = 6 if s.category.code == "PHONES" else COVER_DAYS
            r = math.ceil(v * (lead + review + SAFETY_DAYS)) if v >= 0.04 else 0
            r = max(r, 1 if v >= 0.04 else 0)
            target = max(r + 1, math.ceil(v * (lead + review + cover)))
            return r, target, v

        # opening stock, delivered a week before the history starts (traceable as an opening purchase order)
        open_date = self.days[0] - timedelta(days=7)
        for st, store in enumerate(self.stores):
            for code, items in sup_skus.items():
                po_items = []
                for s in items:
                    _, target, _ = policy(st, s)
                    qty = max(1 if exp_v[st][s.idx] >= 0.02 else 0, math.ceil(target * rs.uniform(0.6, 0.95)))
                    if s.sku == self.roles.get("excess_sku"):
                        qty = max(qty, 3)
                    if qty:
                        on_hand[st][s.idx] = qty
                        po_items.append((s, qty, s.cost))
                if po_items:
                    self._make_po(code, st, open_date, open_date, po_items, delivered=open_date, note="Opening stock")
        # bulk purchases scheduled by scenario events
        bulk = {self.days[-e.offset] if e.offset <= self.history_days else None: e for e in self.events if isinstance(e, BulkPurchase)}

        self.promo_skus = set().union(*[p[1] for p in self.promos]) if self.promos else set()
        self.shifted = {s.idx for s in self.skus for e in self.events if isinstance(e, DemandShift) and ((e.skus and s.sku in e.skus) or (e.families and s.family.code in e.families) or (e.category and s.category.code == e.category))}
        for s in self.skus:
            s.base_weights = [entry[3] for entry in s.alloc]
        order_seq = 0
        self.day_totals: dict[date, dict[str, float]] = {}
        snapshot_rows = self.rows[InventorySnapshot]
        for di, d in enumerate(self.days):
            off = self.offset(d)
            inbound = [[0] * len(self.skus) for _ in range(n_stores)]
            # a. deliveries arriving today
            for po in deliveries_due.pop(d, []):
                st = po["_store"]
                for item in po["_items"]:
                    on_hand[st][item["_idx"]] += item["quantity"]
                    on_order[st][item["_idx"]] -= item["quantity"]
                    inbound[st][item["_idx"]] += item["quantity"]
                    item["quantity_received"] = item["quantity"]
                delay = (d - po["expected_date"]).days
                self.add(SupplierDelivery, purchase_order_id=po["id"], supplier_id=po["supplier_id"], store_id=po["store_id"], expected_date=po["expected_date"], delivered_date=d, delay_days=max(0, delay), on_time=delay <= 0, units_received=po["total_units"])
                po["status"] = "RECEIVED"
            # b. demand
            dayf = cat.WEEKDAY_FACTOR[d.weekday()] / wk_norm * (cat.PAYDAY_FACTOR if d.day <= 5 else 1.0) * (1 + TREND_PER_DAY * (di - self.history_days / 2))
            groups: dict[tuple[str, str, int], list[int]] = defaultdict(list)
            for s in self.skus:
                promo = self._promo_for(s.idx, off) if s.idx in self.promo_skus else None
                pf = 1 + PROMO_ELASTICITY * promo[1] if promo else 1.0
                weights = [entry[3] * self._demand_factor(s, entry, off) for entry in s.alloc] if s.idx in self.shifted else s.base_weights
                lam = s.base_v * dayf * pf * sum(weights) / s.w1
                n = poisson(rd, lam)
                if not n:
                    continue
                for _ in range(n):
                    ch, zd, st, _w = rd.choices(s.alloc, weights)[0]
                    groups[(ch, zd, st)].append(s.idx)
            # c. orders
            totals = {"revenue": 0.0, "cost": 0.0, "discount": 0.0, "fulfilment": 0.0, "orders": 0, "units": 0}
            sold_today = [[0] * len(self.skus) for _ in range(n_stores)]
            for key in sorted(groups):
                ch, zd, st = key
                lines = groups[key]
                ro.shuffle(lines)
                i = 0
                while i < len(lines):
                    size = ro.choices([b for b, _ in cat.BASKET_SIZES], [w for _, w in cat.BASKET_SIZES])[0]
                    basket = lines[i:i + size]
                    i += size
                    order_seq += 1
                    order = self._order(ro, rc, d, off, ch, zd, st, basket, on_hand, sold_today, order_seq)
                    if order is None:
                        continue
                    totals["revenue"] += order["total_revenue"]
                    totals["cost"] += order["total_cost"]
                    totals["discount"] += order["discount_total"]
                    totals["fulfilment"] += order["fulfilment_cost"]
                    totals["orders"] += 1
                    totals["units"] += order["total_quantity"]
            self.day_totals[d] = totals
            # d. end-of-day snapshots and velocity history
            for st in range(n_stores):
                for s in self.skus:
                    sold = sold_today[st][s.idx]
                    sales_hist[st][s.idx].append(sold)
                    if on_hand[st][s.idx] or sold or inbound[st][s.idx] or di == 0 or exp_v[st][s.idx] >= 0.02:
                        snapshot_rows.append({"id": self._id(), "inventory_id": self.inv_ids[st][s.idx], "snapshot_date": d, "quantity_available": on_hand[st][s.idx], "inbound_units": inbound[st][s.idx], "outbound_units": sold})
            # e. purchasing (supplier order days)
            for code, items in sup_skus.items():
                sup = self.suppliers[code]
                if d.weekday() not in sup.order_weekdays:
                    continue
                for st in range(n_stores):
                    po_items = []
                    for s in items:
                        r, target, _ = policy(st, s)
                        pos = on_hand[st][s.idx] + on_order[st][s.idx]
                        if target > 0 and pos <= r:
                            po_items.append((s, target - pos, self._price_at(s, off)[1]))
                    if po_items:
                        po = self._po_with_delivery(code, st, d, off, po_items, rs)
                        for item in po["_items"]:
                            on_order[st][item["_idx"]] += item["quantity"]
                        deliveries_due[po["_delivered"]].append(po)
            # f. scenario bulk purchase (placed today, delivered on time)
            e = bulk.get(d)
            if e is not None:
                s = self.sku_by_code[e.sku]
                lead = self.suppliers[s.supplier].lead_time_days
                po = self._make_po(s.supplier, 0, d, d + timedelta(days=lead), [(s, e.units, self._price_at(s, off)[1])], delivered=d + timedelta(days=lead), note=e.note)
                on_order[0][s.idx] += e.units
                deliveries_due[po["_delivered"]].append(po)
        self.on_hand, self.on_order, self.sales_hist = on_hand, on_order, sales_hist
        self.policy = policy
        self.counts["orders"] = len(self.rows[Order])
        self.counts["order_items"] = len(self.rows[OrderItem])
        self.counts["inventory_snapshots"] = len(snapshot_rows)

    def _order(self, ro, rc, d: date, off: int, ch: str, zd: str, st: int, basket: list[int], on_hand, sold_today, seq: int) -> dict[str, Any] | None:
        first = self.skus[basket[0]]
        cust = self._pick_customer(rc, ch, zd, first.category.code, d)
        qty_by_sku: dict[int, int] = defaultdict(int)
        for idx in basket:
            qty_by_sku[idx] += 1
        seg: cat.SegmentSpec = cust["_seg"]
        lines = []
        for idx, q in sorted(qty_by_sku.items()):
            s = self.skus[idx]
            if seg.qty_boost and s.price < 800 and ro.random() < seg.qty_boost:
                q *= ro.randint(2, 4)
            q = min(q, on_hand[st][idx])
            if q <= 0:
                continue  # out of stock: the sale is lost and never recorded
            on_hand[st][idx] -= q
            sold_today[st][idx] += q
            price, cost = self._price_at(s, off)
            promo = self._promo_for(idx, off)
            disc_row, pct = (promo[0], promo[1]) if promo else (None, 0.0)
            if disc_row is None and ch == "STORE" and ro.random() < ADHOC_DISCOUNT_SHARE:
                disc_row, pct = self.adhoc, ro.uniform(0.01, 0.025) if s.category.code == "PHONES" else ro.uniform(0.02, 0.06)
            unit_disc = float(round(price * pct))
            line_rev = price * q - unit_disc * q
            lines.append({"product_id": s.id, "quantity": q, "unit_price": round(line_rev / q, 2), "unit_cost": cost, "line_revenue": round(line_rev, 2), "list_price": price, "discount_amount": unit_disc * q, "discount_id": disc_row["id"] if disc_row else None})
        if not lines:
            return None
        oid = self._id()
        revenue = round(sum(x["line_revenue"] for x in lines), 2)
        cogs = round(sum(x["unit_cost"] * x["quantity"] for x in lines), 2)
        online = ch != "STORE"
        fulfil = round(cat.DELIVERY_FEE_COST + cat.PACKAGING_RATE * revenue, 2) if online else 0.0
        if online:
            actual = d + timedelta(days=1 if ro.random() < 0.9 else 2)
            status, actual_date = ("DELIVERED", actual) if actual < self.today else ("SHIPPED", None)
            promised = d + timedelta(days=1)
        else:
            status, actual_date, promised = "DELIVERED", d, d
        pm_mix = cat.PAYMENT_MIX[ch]
        pm = ro.choices(list(pm_mix), list(pm_mix.values()))[0]
        store = self.stores[st]
        order = self.add(Order, id=oid, order_number=f"ORD-{d:%y%m%d}-{seq:05d}", customer_id=cust["id"], order_date=d, promised_delivery_date=promised, actual_delivery_date=actual_date, status=status, region=cust["region"], total_quantity=sum(x["quantity"] for x in lines), total_revenue=revenue, total_cost=cogs, channel=ch, store_id=store["id"], discount_total=round(sum(x["discount_amount"] for x in lines), 2), fulfilment_cost=fulfil, payment_method=pm)
        for x in lines:
            self.add(OrderItem, order_id=oid, **x)
        self.add(SalesTransaction, order_id=oid, customer_id=cust["id"], invoice_number=f"INV-{d:%y%m%d}-{seq:05d}", transaction_date=d, amount=revenue, payment_status="PAID", payment_terms_days=0, payment_method=pm)
        cust["_orders"] += 1
        cust.setdefault("_rev", []).append(revenue)
        return order

    def _pick_customer(self, rc: random.Random, ch: str, zd: str, category: str, d: date) -> dict[str, Any]:
        if rc.random() < cat.RETURNING_SHARE[ch]:
            segs = [s for s in self.tpl.segments if self.pool.get((zd, s.code))]
            if segs:
                seg = rc.choices(segs, [s.share * s.affinity.get(category, 1.0) for s in segs])[0]
                candidates = self.pool[(zd, seg.code)]
                return candidates[rc.randrange(len(candidates))]
        seg = self._pick_segment(rc, category)
        return self._new_customer(rc, zd, seg, d, ch)

    # ---- purchase orders ------------------------------------------------------------------------------------------
    def _make_po(self, code: str, st: int, order_date: date, expected: date, items: list[tuple[_Sku, int, float]], delivered: date | None = None, note: str | None = None) -> dict[str, Any]:
        store = self.stores[st]
        sup = self.supplier_rows[code]
        pid = self._id()
        n = len(self.rows[PurchaseOrder]) + 1
        po_items = []
        for s, qty, unit_cost in items:
            po_items.append(self.add(PurchaseOrderItem, purchase_order_id=pid, product_id=s.id, quantity=qty, quantity_received=0, unit_cost=unit_cost, line_cost=round(unit_cost * qty, 2), _idx=s.idx))
        po = self.add(PurchaseOrder, id=pid, po_number=f"PO-{order_date:%y%m%d}-{code[:4]}-{store['code']}-{n:04d}", supplier_id=sup["id"], store_id=store["id"], order_date=order_date, expected_date=expected, status="OPEN", total_units=sum(i["quantity"] for i in po_items), total_cost=round(sum(i["line_cost"] for i in po_items), 2), notes=note, _store=st, _items=po_items, _delivered=delivered)
        if delivered is not None and delivered < self.days[0]:  # opening stock: already received
            for i in po_items:
                i["quantity_received"] = i["quantity"]
            po["status"] = "RECEIVED"
            self.add(SupplierDelivery, purchase_order_id=pid, supplier_id=sup["id"], store_id=store["id"], expected_date=expected, delivered_date=delivered, delay_days=0, on_time=True, units_received=po["total_units"])
        return po

    def _po_with_delivery(self, code: str, st: int, d: date, off: int, items, rs: random.Random) -> dict[str, Any]:
        sup = self.suppliers[code]
        on_time, delay_range, hold = sup.on_time_rate, (1, 3), 0
        for e in self.events:
            if isinstance(e, SupplierSlowdown) and e.supplier == code and off <= e.start:
                on_time, delay_range, hold = e.on_time_rate, e.delay_range, e.hold_last_days
        expected = d + timedelta(days=sup.lead_time_days)
        delay = 0 if rs.random() < on_time else rs.randint(*delay_range)
        delivered = expected + timedelta(days=delay)
        if hold and self.offset(delivered) <= hold:  # stuck at the supplier: still not here today
            delivered = max(delivered, self.today + timedelta(days=rs.randint(1, 3)))
        return self._make_po(code, st, d, expected, items, delivered=delivered)

    # ---- 6. finalise: inventory, suppliers, expenses, financials, signals, state ------------------------------------
    def _finalise(self) -> None:
        cid = self.company["id"]
        # inventory positions (today, before trading)
        for st, store in enumerate(self.stores):
            for s in self.skus:
                r, target, v = self.policy(st, s)
                if self.on_hand[st][s.idx] == 0 and self.exp_v[st][s.idx] < 0.02 and not any(self.sales_hist[st][s.idx]):
                    continue
                hist = self.sales_hist[st][s.idx]
                avg28 = round(sum(hist) / max(1, len(hist)), 3)
                self.add(Inventory, id=self.inv_ids[st][s.idx], item_type="PRODUCT", product_id=s.id, component_type=None, item_name=s.name, warehouse=store["name"][:120], quantity_available=self.on_hand[st][s.idx], quantity_reserved=0, reorder_point=r, safety_stock=math.ceil(v * SAFETY_DAYS), max_stock=target, holding_cost_per_unit_month=round(s.cost * 0.015, 2), unit_value=self._price_at(s, 0)[1], avg_daily_consumption=avg28, last_updated=self.data_end, store_id=store["id"])
        inv_ids = {r["id"] for r in self.rows[Inventory]}
        self.rows[InventorySnapshot] = [r for r in self.rows[InventorySnapshot] if r["inventory_id"] in inv_ids]
        self.counts["inventory"] = len(self.rows[Inventory])
        self.counts["inventory_snapshots"] = len(self.rows[InventorySnapshot])
        # products carry the current price list
        for s in self.skus:
            price, cost = self._price_at(s, 0)
            self.add(Product, id=s.id, company_id=cid, production_line_id=None, sku=s.sku, name=s.name, category=s.category.name, unit_cost=cost, selling_price=price, target_margin=s.category.target_margin, is_active=True, category_id=self.category_rows[s.category.code]["id"], brand=s.family.brand, mrp=max(s.mrp, price), supplier_id=self.supplier_rows[s.supplier]["id"])
            for start, mult, pass_through, note in sorted(self.price_events.get(s.idx, []), key=lambda x: -x[0]):
                if start < self.history_days:
                    p, c = self._price_at(s, start)
                    self.add(PricingHistory, product_id=s.id, effective_date=self.today - timedelta(days=start), selling_price=p, unit_cost=c, mrp=max(s.mrp, p), reason="COST_REVISION", note=note)
        self.counts["pricing_history"] = len(self.rows[PricingHistory])
        # suppliers: observed reliability and current lead time from the delivery history
        deliveries = self.rows[SupplierDelivery]
        po_by_id = {p["id"]: p for p in self.rows[PurchaseOrder]}
        for code, row in self.supplier_rows.items():
            mine = sorted([x for x in deliveries if x["supplier_id"] == row["id"] and x["delivered_date"] >= self.days[0]], key=lambda x: x["delivered_date"])
            if mine:
                row["reliability_score"] = round(sum(1 for x in mine if x["on_time"]) / len(mine), 3)
                recent = mine[-6:]
                row["lead_time_days"] = max(1, round(sum((x["delivered_date"] - po_by_id[x["purchase_order_id"]]["order_date"]).days for x in recent) / len(recent)))
            costs = [self._price_at(s, 0)[1] for s in self.skus if s.supplier == code]
            row["unit_cost"] = round(sum(costs) / len(costs), 2) if costs else 0.0
        # customers
        for c in self.customers:
            revs = c.get("_rev", [])
            c["average_order_value"] = round(sum(revs) / len(revs), 2) if revs else 0.0
        self.counts["customers"] = len(self.customers)
        self.counts["purchase_orders"] = len(self.rows[PurchaseOrder])
        self.counts["supplier_deliveries"] = len(deliveries)
        self._expenses_and_financials()
        self._competitor_signals()
        state_profile = {"merchant": self.profile.as_dict(), "template": self.tpl.key, "roles": self.roles, "scenario": self.scenario_key if self.scenario else None, "events": events_as_dicts(self.scenario)}
        self.add(SystemState, id=1, seed=self.seed, data_start=self.data_start, data_end=self.data_end, generator_version=GENERATOR_VERSION, simulator_status="IDLE", detection_status="IDLE", domain="merchant", profile=state_profile, ai_outage_mode="off")
        self.counts["sales_transactions"] = len(self.rows[SalesTransaction])

    def _expenses_and_financials(self) -> None:
        cid = self.company["id"]
        n = len(self.stores)
        monthly = dict(self.tpl.expenses_monthly)
        monthly["RENT"] *= 1 + 0.7 * (n - 1)
        monthly["SALARIES"] *= 1 + 0.6 * (n - 1)
        monthly["UTILITIES"] *= 1 + 0.6 * (n - 1)
        post_day = {"RENT": 1, "SALARIES": 1, "UTILITIES": 5, "OTHER": 10}
        labels = {"RENT": "Shop rent", "SALARIES": "Staff salaries", "UTILITIES": "Electricity and internet", "OTHER": "Maintenance, stationery and bank charges", "MARKETING": "Local ads and social media boosts", "DELIVERY": "Delivery partner charges and packaging"}
        weekly_marketing = monthly.get("MARKETING", 0.0) * 12 / 52
        for d in self.days:
            for k, day in post_day.items():
                if d.day == day and k in monthly:
                    self.add(Expense, company_id=cid, store_id=None, expense_date=d, category=k, amount=round(monthly[k], 2), description=labels[k], is_recurring=True)
            if d.weekday() == 0 and weekly_marketing:
                self.add(Expense, company_id=cid, store_id=None, expense_date=d, category="MARKETING", amount=round(weekly_marketing, 2), description=labels["MARKETING"], is_recurring=True)
            t = self.day_totals[d]
            if t["fulfilment"]:
                self.add(Expense, company_id=cid, store_id=self.stores[0]["id"], expense_date=d, category="DELIVERY", amount=round(t["fulfilment"], 2), description=labels["DELIVERY"], is_recurring=False)
            dim = (date(d.year + (d.month == 12), d.month % 12 + 1, 1) - date(d.year, d.month, 1)).days
            fixed = sum(monthly[k] for k in post_day if k in monthly) / dim + monthly.get("MARKETING", 0.0) * 12 / 365
            opex = fixed + t["fulfilment"]
            rev, cost = t["revenue"], t["cost"]
            self.add(FinancialMetric, company_id=cid, metric_date=d, period_type="DAILY", revenue=round(rev, 2), cost=round(cost, 2), gross_margin=round((rev - cost) / rev, 4) if rev else 0.0, operating_cost=round(opex, 2), production_cost=0.0, profit=round(rev - cost - opex, 2), orders_count=int(t["orders"]), units_sold=int(t["units"]), currency="INR", discount_total=round(t["discount"], 2), fulfilment_cost=round(t["fulfilment"], 2))
        self.counts["expenses"] = len(self.rows[Expense])
        self.counts["financial_metrics"] = len(self.rows[FinancialMetric])

    def _competitor_signals(self) -> None:
        rng = self.rng("competitors")
        heroes = sorted(self.skus, key=lambda s: -s.base_v * s.price)[:8]
        for s in heroes:
            for d in self.days[::7]:
                price, _ = self._price_at(s, self.offset(d))
                pct = round(rng.gauss(0, 2.0), 1)
                observed = round(price * (1 + pct / 100))
                self.add(CompetitorSignal, signal_date=d, competitor_name="Online marketplaces (weekly price check)", signal_type="PRICE_CHANGE", product_category=s.category.name, region=self.profile.city, price_change_pct=pct, availability_score=round(rng.uniform(0.85, 1.0), 2), description=f"{s.name} listed online at about ₹{observed:,.0f} (our price ₹{price:,.0f})", details={"sku": s.sku, "product_id": str(s.id), "observed_price": observed, "our_price": price}, source="SYNTHETIC", is_synthetic=True)
        self.counts["competitor_signals"] = len(self.rows[CompetitorSignal])

    # ---- 7. bulk insert ------------------------------------------------------------------------------------------
    def _insert_all(self) -> None:
        order = [Company, Store, ProductCategory, Supplier, Product, Discount, PricingHistory, Customer, PurchaseOrder, PurchaseOrderItem, SupplierDelivery, Order, OrderItem, SalesTransaction, Inventory, InventorySnapshot, Expense, FinancialMetric, CompetitorSignal, SystemState]
        now = utcnow()
        for model in order:
            rows = self.rows.get(model, [])
            if not rows:
                continue
            cols = set(model.__table__.columns.keys())
            clean = []
            for r in rows:
                row = {k: v for k, v in r.items() if not k.startswith("_") and k in cols}
                if "created_at" in cols and "created_at" not in row:
                    row["created_at"] = now
                if "updated_at" in cols and "updated_at" not in row:
                    row["updated_at"] = now
                clean.append(row)
            for i in range(0, len(clean), 5000):
                self.db.execute(insert(model), clean[i:i + 5000])
        self.db.commit()
