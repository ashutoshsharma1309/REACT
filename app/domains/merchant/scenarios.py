"""Merchant demo scenarios as deterministic business events.

A scenario is not a patch applied to finished data. It is a set of events injected into the merchant simulation
(a demand surge, a supplier slowing down, a cost increase that was not passed on, an over-sized purchase). The generator
then produces sales, stock movements, purchase orders, deliveries and financials from those events, so every table
stays consistent: extra sales reduce stock, late deliveries leave purchase orders open, a higher cost lowers margin.

Offsets are days before the simulated "today". The same seed, anchor, profile and scenario always give the same data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.domains.merchant.catalog import BusinessTemplate, FamilySpec


@dataclass(frozen=True)
class DemandShift:
    multiplier: float
    start: int  # days before today when the shift begins
    ramp_days: int = 0
    families: tuple[str, ...] = ()  # family codes (all variants) ...
    skus: tuple[str, ...] = ()  # ... or individual SKUs
    category: str | None = None  # ... or a whole category
    zone: str | None = None  # restrict to customers from one zone direction ("South")
    kind: str = "demand_shift"

    def factor(self, offset: int) -> float:
        if offset > self.start:
            return 1.0
        if self.ramp_days > 0:
            progress = min(1.0, (self.start - offset + 1) / self.ramp_days)
            return 1.0 + (self.multiplier - 1.0) * progress
        return self.multiplier


@dataclass(frozen=True)
class SupplierSlowdown:
    supplier: str
    start: int
    on_time_rate: float
    delay_range: tuple[int, int]
    hold_last_days: int = 0  # deliveries that would land in the last N days slip past today (the supplier is stuck)
    kind: str = "supplier_slowdown"


@dataclass(frozen=True)
class CostChange:
    sku: str
    start: int
    cost_multiplier: float
    pass_through: bool = False  # True = the merchant raised the selling price by the same ratio
    kind: str = "cost_change"


@dataclass(frozen=True)
class Promotion:
    name: str
    pct: float
    start: int
    end: int = 0  # 0 = still running today
    skus: tuple[str, ...] = ()
    category: str | None = None
    kind: str = "promotion"


@dataclass(frozen=True)
class BulkPurchase:
    sku: str
    offset: int
    units: int
    note: str = ""
    kind: str = "bulk_purchase"


Event = DemandShift | SupplierSlowdown | CostChange | Promotion | BulkPurchase


@dataclass
class ScenarioSpec:
    key: str
    label: str
    story: str
    finding_type: str  # the detection type the scenario is designed to surface
    finding_kind: str  # risk | opportunity
    events: list[Any] = field(default_factory=list)


# ---- roles ----------------------------------------------------------------------------------------------------
def resolve_roles(tpl: BusinessTemplate, categories: list[str], skus_by_family: dict[str, list[str]], store_zone: str) -> dict[str, Any]:
    """Pick the products, category and supplier each scenario uses, honouring the merchant's chosen categories."""
    fam = {f.code: f for f in tpl.families}
    selected = set(categories)

    def family_pick(role: str, fallback_key) -> FamilySpec:
        for code in tpl.roles[role]:
            f = fam.get(code)
            if f and f.category in selected:
                return f
        eligible = [f for f in tpl.families if f.category in selected and f.category != "PHONES"] or [f for f in tpl.families if f.category in selected]
        return sorted(eligible, key=fallback_key)[0]

    stock = family_pick("stockout_product", lambda f: (-f.daily_units / len(f.variants), f.code))
    margin = family_pick("margin_product", lambda f: (-(f.daily_units / len(f.variants)) * f.price * f.margin, f.code))
    if margin.code == stock.code:  # keep scenarios independent
        others = [f for f in tpl.families if f.category in selected and f.code != stock.code and f.category != "PHONES"]
        margin = sorted(others, key=lambda f: (-(f.daily_units / len(f.variants)) * f.price * f.margin, f.code))[0]
    excess = family_pick("excess_product", lambda f: (f.daily_units / len(f.variants), -f.price, f.code))
    opp_cat = next((c for c in tpl.roles["opportunity_category"] if c in selected), None) or next(c.code for c in tpl.categories if c.code in selected and c.code != "PHONES")
    used = {stock.supplier}
    delay_sup = next((s for s in tpl.roles["delay_supplier"] if s not in used and any(f.supplier == s and f.category in selected for f in tpl.families)), None)
    if delay_sup is None:
        delay_sup = next(s.code for s in tpl.suppliers if s.code not in used and any(f.supplier == s.code and f.category in selected for f in tpl.families))
    return {
        "stockout_sku": skus_by_family[stock.code][0], "stockout_supplier": stock.supplier,
        "margin_sku": skus_by_family[margin.code][0],
        "excess_sku": skus_by_family[excess.code][0], "excess_supplier": excess.supplier,
        "opportunity_category": opp_cat, "opportunity_zone": store_zone,
        "delay_supplier": delay_sup,
    }


# ---- scenarios ------------------------------------------------------------------------------------------------
def build_scenario(key: str | None, roles: dict[str, Any]) -> ScenarioSpec | None:
    if not key or key in ("baseline", "reset"):
        return None
    if key == "stockout_crisis":
        return ScenarioSpec(key, "Stockout crisis", "A best-seller is selling faster than usual while its supplier has slowed down. REACT spots the stock running out before the next delivery, prices the lost sales and recommends a reorder.", "STOCKOUT_RISK", "risk", [
            DemandShift(1.8, start=18, ramp_days=3, skus=(roles["stockout_sku"],)),
            SupplierSlowdown(roles["stockout_supplier"], start=30, on_time_rate=0.0, delay_range=(4, 6), hold_last_days=4),
        ])
    if key == "demand_opportunity":
        return ScenarioSpec(key, "Demand opportunity", "One category is selling much faster among customers in the store's own neighbourhood. REACT explains why and sizes the extra revenue.", "REGIONAL_OPPORTUNITY", "opportunity", [
            DemandShift(1.55, start=28, ramp_days=5, category=roles["opportunity_category"], zone=roles["opportunity_zone"]),
        ])
    if key == "supplier_delay":
        return ScenarioSpec(key, "Supplier delay", "A regular supplier has started delivering late, order after order. REACT traces which products depend on it and what the delays put at risk.", "SUPPLIER_RELIABILITY", "risk", [
            SupplierSlowdown(roles["delay_supplier"], start=45, on_time_rate=0.15, delay_range=(6, 9)),
        ])
    if key == "margin_leakage":
        return ScenarioSpec(key, "Margin leakage", "A product is selling more than ever, but each sale earns less because the supplier raised its price and the shelf price never changed. REACT finds the leak and prices it.", "MARGIN_DECLINE", "risk", [
            CostChange(roles["margin_sku"], start=24, cost_multiplier=1.07, pass_through=False),
            DemandShift(1.2, start=21, ramp_days=2, skus=(roles["margin_sku"],)),
        ])
    if key == "excess_inventory":
        return ScenarioSpec(key, "Excess inventory", "A large lot bought months ago is barely selling. REACT shows how much cash is locked in the shelf and what to do about it.", "EXCESS_INVENTORY", "risk", [
            BulkPurchase(roles["excess_sku"], offset=104, units=60, note="Bulk lot bought on a distributor deal"),
            DemandShift(0.32, start=100, skus=(roles["excess_sku"],)),
        ])
    raise KeyError(key)


SCENARIO_KEYS = ("stockout_crisis", "demand_opportunity", "supplier_delay", "margin_leakage", "excess_inventory")
SCENARIO_LABELS = {
    "stockout_crisis": "Stockout crisis",
    "demand_opportunity": "Demand opportunity",
    "supplier_delay": "Supplier delay",
    "margin_leakage": "Margin leakage",
    "excess_inventory": "Excess inventory",
    "baseline": "Normal business",
}


def events_as_dicts(spec: ScenarioSpec | None) -> list[dict[str, Any]]:
    return [asdict(e) for e in (spec.events if spec else [])]
