"""Hypothesis registry: configurable, deterministic hypothesis definitions.

A HypothesisSpec lists, for a kind of subject (incident / opportunity / risk):
  * triggers      - conditions that make the hypothesis worth considering (drive the initial ranking)
  * supporting    - conditions whose satisfaction is evidence FOR the hypothesis
  * contradicting - conditions whose satisfaction is evidence AGAINST the hypothesis

Conditions are evaluated against the SignalSet. A supporting condition that is observed but NOT met
also yields (weaker) contradicting evidence, e.g. "vibration within baseline" against bearing degradation.
Nothing here is random and nothing is hard-coded per incident: the same registry runs on any subject.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.investigation.signals import Observation, SignalSet

OPS = {"gt", "gte", "lt", "lte", "true", "false"}


@dataclass(frozen=True)
class Condition:
    signal: str
    op: str
    threshold: float | None = None
    weight: float = 1.0
    label: str = ""  # what satisfying this condition means for the hypothesis

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"unknown op {self.op}")


@dataclass
class ConditionResult:
    condition: Condition
    observation: Observation | None
    satisfied: bool | None  # None = signal not observed
    strength: float = 0.0  # 0..1 how clearly the condition is met (0.5 at threshold, 1.0 at 2x the margin)

    @property
    def observed(self) -> bool:
        return self.observation is not None and self.observation.value is not None


@dataclass(frozen=True)
class HypothesisSpec:
    type: str
    label: str
    description: str
    kinds: tuple[str, ...]  # subject kinds: incident | opportunity | risk
    subject_types: tuple[str, ...] = ("*",)  # e.g. ("DEMAND_OPPORTUNITY",) or ("*",)
    requires_any: tuple[str, ...] = ()  # at least one of these signals must be observed for the hypothesis to apply
    triggers: tuple[Condition, ...] = ()
    supporting: tuple[Condition, ...] = ()
    contradicting: tuple[Condition, ...] = ()
    next_step: str = ""

    def applies(self, kind: str, subject_type: str, signals: SignalSet) -> bool:
        if kind not in self.kinds:
            return False
        if "*" not in self.subject_types and subject_type not in self.subject_types:
            return False
        if self.requires_any and not any(signals.has(s) for s in self.requires_any):
            return False
        return True


@dataclass
class HypothesisCandidate:
    spec: HypothesisSpec
    trigger_results: list[ConditionResult]
    trigger_score: float
    initial_confidence: float = 0.0
    rank: int = 0

    @property
    def triggered_signals(self) -> list[dict[str, Any]]:
        return [{"signal": r.condition.signal, "value": r.observation.value if r.observation else None, "unit": r.observation.unit if r.observation else None, "threshold": r.condition.threshold, "op": r.condition.op, "strength": round(r.strength, 2), "label": r.condition.label} for r in self.trigger_results if r.satisfied]


# ---- evaluation --------------------------------------------------------------------------------
def evaluate(cond: Condition, signals: SignalSet) -> ConditionResult:
    obs = signals.get(cond.signal)
    if obs is None or obs.value is None:
        return ConditionResult(cond, obs, None, 0.0)
    v = obs.value
    t = cond.threshold if cond.threshold is not None else 0.0
    if cond.op in ("true", "false"):
        sat = bool(v) if cond.op == "true" else not bool(v)
        return ConditionResult(cond, obs, sat, 1.0 if sat else 0.0)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ConditionResult(cond, obs, None, 0.0)
    scale = max(abs(t), 1e-6) if t != 0 else 1.0
    if cond.op in ("gt", "gte"):
        sat = v > t if cond.op == "gt" else v >= t
        margin = (v - t) / scale
    else:
        sat = v < t if cond.op == "lt" else v <= t
        margin = (t - v) / scale
    strength = min(1.0, 0.5 + 0.5 * max(0.0, margin)) if sat else 0.0
    return ConditionResult(cond, obs, sat, strength)


def generate_hypotheses(signals: SignalSet, registry: list[HypothesisSpec] | None = None) -> list[HypothesisCandidate]:
    """Deterministic initial ranking: trigger score = Σ weight × strength of satisfied triggers.
    Untriggered but applicable hypotheses are kept with a small floor so they appear as considered alternatives."""
    specs = [s for s in (registry or registry_for(getattr(signals, "domain", "manufacturing"))) if s.applies(signals.subject_kind, signals.subject_type, signals)]
    cands: list[HypothesisCandidate] = []
    for spec in specs:
        results = [evaluate(c, signals) for c in spec.triggers]
        score = sum(r.condition.weight * r.strength for r in results if r.satisfied)
        total = sum(c.weight for c in spec.triggers) or 1.0
        raw = max(score, 0.04 * total)
        cands.append(HypothesisCandidate(spec, results, raw))
    total_raw = sum(c.trigger_score for c in cands) or 1.0
    for c in cands:
        c.initial_confidence = round(c.trigger_score / total_raw, 4)
    cands.sort(key=lambda c: (-c.trigger_score, c.spec.type))
    for i, c in enumerate(cands, start=1):
        c.rank = i
    return cands


# ---- registry ------------------------------------------------------------------------------------
C = Condition

INCIDENT_HYPOTHESES: list[HypothesisSpec] = [
    HypothesisSpec(
        "BEARING_DEGRADATION", "Bearing degradation", "A rotating-element bearing is wearing out: vibration and temperature rise together, power draw increases, output and quality fall.",
        kinds=("incident",), requires_any=("vibration_change_pct",),
        triggers=(C("vibration_change_pct", "gt", 25, 3, "vibration rising"), C("temperature_change_pct", "gt", 10, 2, "temperature rising"), C("output_change_pct", "lt", -10, 1, "output falling"), C("bearing_condition_score", "lt", 0.35, 2, "bearing near end of life")),
        supporting=(C("vibration_change_pct", "gt", 25, 3, "vibration increased"), C("vibration_above_warning", "true", None, 1.5, "vibration above warning threshold"), C("temperature_change_pct", "gt", 10, 2, "temperature increased"), C("power_change_pct", "gt", 8, 1, "power consumption increased"), C("rpm_change_pct", "lt", -2, 0.5, "spindle speed dropping under load"), C("output_change_pct", "lt", -10, 1.5, "output decreased"), C("defect_ratio", "gt", 1.5, 1, "defect rate increased"), C("bearing_condition_score", "lt", 0.35, 2, "bearing condition critical"), C("bearing_failure_probability", "gt", 0.3, 1, "elevated bearing failure probability"), C("maintenance_overdue_days", "gt", 0, 1, "preventive maintenance overdue"), C("historical_bearing_failures", "gte", 1, 1, "similar historical bearing failure")),
        contradicting=(C("other_physical_signals_normal", "true", None, 2, "other physical sensors normal"), C("readings_discontinuous", "true", None, 1.5, "readings discontinuous (sensor issue)"), C("implausible_readings", "true", None, 1.5, "readings implausible (sensor issue)"), C("simultaneous_power_anomalies", "true", None, 1, "anomalies span multiple machines")),
        next_step="Inspect the bearing assembly (vibration spectrum, lubrication, play) and plan a replacement window.",
    ),
    HypothesisSpec(
        "SENSOR_FAILURE", "Sensor malfunction", "The instrument, not the machine, is at fault: one sensor misbehaves while correlated physical signals and production stay normal.",
        kinds=("incident",), requires_any=("sensor_fault_count",),
        triggers=(C("sensor_fault_count", "gte", 1, 3, "sensor fault signature present"), C("readings_discontinuous", "true", None, 2, "readings discontinuous"), C("implausible_readings", "true", None, 2, "readings implausible")),
        supporting=(C("sensor_fault_count", "gte", 1, 3, "sensor fault signature"), C("other_physical_signals_normal", "true", None, 2, "other physical sensors normal"), C("readings_discontinuous", "true", None, 2, "readings stuck or missing"), C("implausible_readings", "true", None, 2, "readings physically implausible"), C("output_change_pct", "gt", -5, 1, "production unaffected"), C("defect_ratio", "lt", 1.3, 1, "quality unaffected"), C("historical_sensor_faults", "gte", 1, 0.5, "sensor faults seen before")),
        contradicting=(C("anomalous_sensor_types", "gte", 2, 3, "multiple physical signals anomalous"), C("output_change_pct", "lt", -10, 1.5, "production affected"), C("defect_ratio", "gt", 1.5, 1, "quality affected"), C("bearing_condition_score", "lt", 0.35, 1, "worn bearing explains the signal")),
        next_step="Verify transmitter wiring and calibration; cross-check with a portable instrument before touching the machine.",
    ),
    HypothesisSpec(
        "POWER_INSTABILITY", "Power instability", "Unstable electrical supply or drive behaviour: power consumption fluctuates and several machines on the line show simultaneous anomalies with matching output changes.",
        kinds=("incident",), requires_any=("power_change_pct",),
        triggers=(C("power_std_ratio", "gt", 1.8, 3, "power draw fluctuating"), C("simultaneous_power_anomalies", "true", None, 3, "simultaneous power anomalies on the line"), C("machines_with_power_anomaly", "gte", 2, 2, "several machines with power anomalies")),
        supporting=(C("power_std_ratio", "gt", 1.8, 3, "power draw fluctuates"), C("simultaneous_power_anomalies", "true", None, 3, "multiple machines anomalous simultaneously"), C("machines_with_power_anomaly", "gte", 2, 2, "power anomalies across machines"), C("line_power_variability_ratio", "gt", 1.5, 1, "line energy variability up"), C("output_change_pct", "lt", -5, 1, "output changed accordingly"), C("historical_power_failures", "gte", 1, 0.5, "electrical failures seen before"), C("electrical_component_condition", "lt", 0.4, 1, "worn electrical component")),
        contradicting=(C("vibration_change_pct", "gt", 25, 2, "vibration rise points to mechanical cause"), C("temperature_change_pct", "gt", 15, 1, "temperature rise points to mechanical cause"), C("other_machines_with_anomaly", "lt", 1, 2, "no other machine affected"), C("power_std_ratio", "lt", 1.2, 2, "power draw stable")),
        next_step="Log voltage and current at the line panel; check drives and incoming supply quality.",
    ),
    HypothesisSpec(
        "MAINTENANCE_ISSUE", "Operator / maintenance issue", "A recent intervention or a lapsed maintenance schedule changed machine behaviour (setup, alignment, lubrication).",
        kinds=("incident",), requires_any=("days_since_maintenance",),
        triggers=(C("recent_maintenance_event", "true", None, 3, "maintenance intervention within 7 days"), C("maintenance_overdue_days", "gt", 14, 2, "preventive maintenance overdue"), C("corrective_count_90d", "gte", 2, 1, "repeated corrective actions")),
        supporting=(C("recent_maintenance_event", "true", None, 3, "recent intervention"), C("maintenance_overdue_days", "gt", 14, 2, "maintenance overdue"), C("corrective_count_90d", "gte", 2, 1, "repeated corrective work"), C("downtime_recent_hours", "gt", 4, 1, "downtime accumulating"), C("defect_ratio", "gt", 1.5, 1, "quality drift after setup"), C("worst_component_condition", "lt", 0.35, 1, "worn component not replaced")),
        contradicting=(C("maintenance_overdue_days", "lte", 0, 1.5, "maintenance on schedule"), C("sensor_fault_count", "gte", 1, 1, "sensor fault explains the signal")),
        next_step="Review the last work order and setup parameters; verify torque, alignment and lubrication after the intervention.",
    ),
    HypothesisSpec(
        "COMPONENT_SUPPLY_SHORTAGE", "Component supply shortage", "The line is starved of components: stock below safety level or delayed supplier batches reduce output without any machine fault.",
        kinds=("incident", "risk"), subject_types=("*",), requires_any=("component_stock_below_safety",),
        triggers=(C("component_stock_below_safety", "gte", 1, 3, "component stock below safety"), C("delayed_component_batches", "gte", 1, 2, "delayed supplier batches")),
        supporting=(C("component_stock_below_safety", "gte", 1, 3, "component stock below safety"), C("delayed_component_batches", "gte", 1, 2, "delayed supplier batches"), C("output_vs_plan", "lt", 0.9, 1.5, "output under plan"), C("output_change_pct", "lt", -10, 1, "output down"), C("downtime_recent_hours", "gt", 2, 0.5, "waiting time recorded as downtime")),
        contradicting=(C("vibration_change_pct", "gt", 25, 1.5, "mechanical signature present"), C("temperature_change_pct", "gt", 15, 1, "thermal signature present"), C("anomalous_sensor_types", "gte", 2, 1.5, "machine sensors abnormal"), C("component_stock_below_safety", "lt", 1, 2, "component stock healthy")),
        next_step="Expedite delayed batches and confirm kitting shortages at the line.",
    ),
    HypothesisSpec(
        "MATERIAL_QUALITY_ISSUE", "Incoming material quality", "An off-spec supplier batch is driving defects while machines run normally.",
        kinds=("incident",), requires_any=("poor_quality_batches",),
        triggers=(C("poor_quality_batches", "gte", 1, 3, "poor-quality batch received"), C("defect_ratio", "gt", 1.8, 1, "defect spike")),
        supporting=(C("poor_quality_batches", "gte", 1, 3, "poor-quality batch received"), C("defect_ratio", "gt", 1.8, 2, "defect rate elevated"), C("output_change_pct", "gt", -8, 0.5, "output largely unaffected")),
        contradicting=(C("anomalous_sensor_types", "gte", 2, 1.5, "machine sensors abnormal"), C("poor_quality_batches", "lt", 1, 2, "no poor-quality batch")),
        next_step="Quarantine the suspect batch and run incoming inspection on the lot.",
    ),
    HypothesisSpec(
        "HYDRAULIC_SEAL_DEGRADATION", "Hydraulic seal / pump degradation", "Hydraulic pressure is being lost through worn seals or a tired pump; temperature and power creep up as the system compensates.",
        kinds=("incident",), requires_any=("pressure_change_pct",),
        triggers=(C("pressure_change_pct", "lt", -8, 3, "pressure falling"), C("hydraulic_component_condition", "lt", 0.35, 2, "seal/pump near end of life")),
        supporting=(C("pressure_change_pct", "lt", -8, 3, "pressure decreased"), C("temperature_change_pct", "gt", 5, 1, "temperature increased"), C("power_change_pct", "gt", 5, 1, "power increased"), C("hydraulic_component_condition", "lt", 0.35, 2, "seal/pump worn"), C("defect_ratio", "gt", 1.5, 1, "under-formed parts"), C("output_change_pct", "lt", -8, 1, "output down")),
        contradicting=(C("pressure_change_pct", "gt", -3, 2, "pressure stable"), C("readings_discontinuous", "true", None, 1, "sensor issue")),
        next_step="Inspect cylinder seals and pump for internal leakage; check hydraulic oil condition.",
    ),
    HypothesisSpec(
        "DEMAND_OVERLOAD", "Line overload", "The line is being run beyond sustainable capacity: utilisation pinned near 100%, quality and downtime deteriorate.",
        kinds=("incident", "risk"), subject_types=("*",), requires_any=("line_utilization_recent",),
        triggers=(C("line_utilization_recent", "gte", 0.95, 3, "line at capacity"), C("order_growth_pct", "gt", 15, 2, "demand growing"), C("downtime_recent_hours", "gt", 6, 1, "downtime accumulating")),
        supporting=(C("line_utilization_recent", "gte", 0.95, 3, "line at capacity"), C("order_growth_pct", "gt", 15, 2, "demand growth"), C("output_vs_plan", "lt", 0.9, 1.5, "plan exceeds output"), C("defect_ratio", "gt", 1.3, 1, "quality suffering"), C("downtime_recent_hours", "gt", 6, 1, "downtime accumulating"), C("line_power_change_pct", "gt", 8, 1, "energy up with load")),
        contradicting=(C("line_utilization_recent", "lt", 0.85, 3, "line has spare capacity"), C("vibration_change_pct", "gt", 25, 1, "mechanical signature present"), C("order_growth_pct", "lt", 0, 2, "demand not growing")),
        next_step="Rebalance the production plan against capacity; consider additional shifts or outsourcing.",
    ),
    HypothesisSpec(
        "CAPACITY_LOSS", "Capacity loss", "Available capacity has fallen because of downtime or degraded machines rather than excess demand.",
        kinds=("risk",), subject_types=("PRODUCTION_BOTTLENECK", "DELIVERY_DELAY", "DEMAND_SUPPLY_MISMATCH"), requires_any=("downtime_recent_hours",),
        triggers=(C("downtime_recent_hours", "gt", 6, 3, "downtime accumulating"), C("other_machines_with_anomaly", "gte", 1, 2, "machine anomalies on the line"), C("output_change_pct", "lt", -10, 1, "output down")),
        supporting=(C("downtime_recent_hours", "gt", 6, 3, "downtime accumulating"), C("other_machines_with_anomaly", "gte", 1, 2, "machine anomalies present"), C("output_change_pct", "lt", -10, 2, "output down")),
        contradicting=(C("downtime_recent_hours", "lt", 2, 2, "little downtime"), C("output_change_pct", "gt", 0, 2, "output up")),
        next_step="Review downtime log and machine health on the line.",
    ),
]

OPPORTUNITY_HYPOTHESES: list[HypothesisSpec] = [
    HypothesisSpec(
        "SUSTAINED_REGIONAL_DEMAND", "Sustained regional demand growth", "Demand is growing persistently across several weeks and customer segments in the region, corroborated by regional market signals.",
        kinds=("opportunity",), subject_types=("DEMAND_OPPORTUNITY", "UNDERSERVED_REGION", "HIGH_MARGIN_GROWTH"), requires_any=("order_growth_pct",),
        triggers=(C("order_growth_pct", "gt", 20, 2, "orders growing"), C("sustained_growth_weeks", "gte", 3, 2, "growth sustained"), C("region_growth_pct", "gt", 20, 2, "regional growth")),
        supporting=(C("order_growth_pct", "gt", 20, 2, "orders increased"), C("sustained_growth_weeks", "gte", 3, 3, "growth persisted for several weeks"), C("region_growth_pct", "gt", 20, 2, "regional demand increased"), C("regional_index_growth_pct", "gt", 3, 1.5, "regional market index rising"), C("segments_growing", "gte", 2, 2, "several segments contributing"), C("customers_growth_pct", "gt", 10, 1, "more active customers"), C("market_product_growth_pct", "gt", 5, 1, "market signal supports growth")),
        contradicting=(C("top_customer_share_of_incremental", "gt", 0.5, 2, "growth concentrated in one customer"), C("sustained_growth_weeks", "lt", 2, 2, "growth not yet sustained"), C("seasonal_uplift_pct", "gt", 4, 1, "seasonal uplift could explain it")),
        next_step="Confirm the regional pipeline with sales and reserve capacity for the region.",
    ),
    HypothesisSpec(
        "TEMPORARY_CUSTOMER_SPIKE", "Temporary customer spike", "A single customer or a one-off order explains most of the increase; the demand may not persist.",
        kinds=("opportunity",), subject_types=("DEMAND_OPPORTUNITY", "SEGMENT_GROWTH", "HIGH_MARGIN_GROWTH"), requires_any=("top_customer_share_of_incremental",),
        triggers=(C("top_customer_share_of_incremental", "gt", 0.4, 3, "one customer dominates the growth"), C("sustained_growth_weeks", "lt", 2, 1, "growth is recent")),
        supporting=(C("top_customer_share_of_incremental", "gt", 0.4, 3, "one customer dominates incremental demand"), C("sustained_growth_weeks", "lt", 2, 2, "growth not sustained"), C("segments_growing", "lte", 1, 1.5, "only one segment growing"), C("customers_growth_pct", "lt", 5, 1, "customer base not growing")),
        contradicting=(C("sustained_growth_weeks", "gte", 3, 2, "growth sustained"), C("segments_growing", "gte", 2, 2, "several segments growing"), C("top_customer_share_of_incremental", "lt", 0.25, 2, "growth is broad-based"), C("market_product_growth_pct", "gt", 5, 1, "market signal supports growth")),
        next_step="Validate order intent and forecast with the largest customer before committing capacity.",
    ),
    HypothesisSpec(
        "COMPETITOR_SUPPLY_SHORTAGE", "Competitor supply shortage", "Customers are switching because a competitor cannot deliver; new accounts appear alongside competitor availability constraints.",
        kinds=("opportunity",), subject_types=("DEMAND_OPPORTUNITY", "UNDERSERVED_REGION", "SEGMENT_GROWTH", "PRICING_OPPORTUNITY"), requires_any=("competitor_availability_constraints",),
        triggers=(C("competitor_availability_constraints", "gte", 1, 3, "competitor availability constrained"),),
        supporting=(C("competitor_availability_constraints", "gte", 1, 3, "competitor availability constrained"), C("competitor_price_increase_pct", "gt", 3, 1.5, "competitor prices rising"), C("new_customers", "gte", 2, 1.5, "new customers appearing"), C("order_growth_pct", "gt", 20, 1, "orders increased"), C("customers_growth_pct", "gt", 10, 1, "active customers increased")),
        contradicting=(C("competitor_availability_constraints", "lt", 1, 3, "no competitor constraint reported"), C("new_customers", "lt", 1, 1, "no new customers")),
        next_step="Target the competitor's accounts and secure component supply to serve switching customers.",
    ),
    HypothesisSpec(
        "SEASONAL_DEMAND", "Seasonal demand", "The increase follows the seasonal profile of the category rather than a structural shift.",
        kinds=("opportunity",), subject_types=("DEMAND_OPPORTUNITY", "SEGMENT_GROWTH", "HIGH_MARGIN_GROWTH"), requires_any=("seasonal_uplift_pct",),
        triggers=(C("seasonal_uplift_pct", "gt", 3, 3, "seasonal index elevated"),),
        supporting=(C("seasonal_uplift_pct", "gt", 3, 3, "seasonal index elevated"), C("regions_growing", "gte", 3, 1, "growth broad across regions"), C("sustained_growth_weeks", "lte", 4, 0.5, "growth recent")),
        contradicting=(C("seasonal_uplift_pct", "lt", 1, 3, "seasonal index flat"), C("region_share_of_incremental", "gt", 0.6, 1, "growth concentrated in one region"), C("sustained_growth_weeks", "gt", 6, 1, "growth longer than a seasonal wave")),
        next_step="Compare against the prior year's seasonal profile before extrapolating.",
    ),
    HypothesisSpec(
        "MARKET_CATEGORY_GROWTH", "Market category growth", "The whole category is growing in the market; the product rides a broader trend.",
        kinds=("opportunity",), subject_types=("DEMAND_OPPORTUNITY", "HIGH_MARGIN_GROWTH", "AVAILABLE_CAPACITY", "SEGMENT_GROWTH"), requires_any=("market_category_growth_pct",),
        triggers=(C("market_category_growth_pct", "gt", 3, 2, "category index rising"), C("regions_growing", "gte", 3, 1, "growth in many regions")),
        supporting=(C("market_category_growth_pct", "gt", 3, 3, "category demand index rising"), C("regions_growing", "gte", 3, 2, "growth across regions"), C("segments_growing", "gte", 2, 1, "growth across segments"), C("market_product_growth_pct", "gt", 5, 1, "product signal positive")),
        contradicting=(C("market_category_growth_pct", "lt", 0, 3, "category index falling"), C("regions_growing", "lte", 1, 1, "growth localised")),
        next_step="Check whether sibling products in the category show the same trend.",
    ),
    HypothesisSpec(
        "PRICING_HEADROOM", "Pricing headroom", "Competitors have raised prices while demand holds, leaving room to close the gap to target margin.",
        kinds=("opportunity",), subject_types=("PRICING_OPPORTUNITY", "HIGH_MARGIN_GROWTH"),  # explains a pricing opportunity, not why demand grew requires_any=("competitor_price_increase_pct",),
        triggers=(C("competitor_price_increase_pct", "gt", 3, 3, "competitor price increases"), C("margin_gap_points", "gt", 2, 1, "margin below target")),
        supporting=(C("competitor_price_increase_pct", "gt", 3, 3, "competitors raised prices"), C("order_growth_pct", "gt", 0, 1.5, "demand not falling"), C("margin_gap_points", "gt", 2, 1, "margin gap to close"), C("competitor_availability_constraints", "gte", 1, 1, "competitor supply constrained"), C("avg_discount_pct", "gt", 3, 1, "discounting can be reduced")),
        contradicting=(C("order_growth_pct", "lt", -10, 2, "demand falling"), C("competitor_discounting", "gte", 1, 2, "competitors discounting"), C("competitor_price_increase_pct", "lt", 1, 2, "no competitor price move")),
        next_step="Model a 3-5% list-price increase on the growing SKUs and monitor win rates.",
    ),
    HypothesisSpec(
        "CAPACITY_HEADROOM", "Capacity headroom", "Spare line capacity and adequate stock allow additional demand to be served without investment.",
        kinds=("opportunity",), subject_types=("AVAILABLE_CAPACITY", "DEMAND_OPPORTUNITY", "UNDERSERVED_REGION"), requires_any=("line_utilization_14d",),
        triggers=(C("line_utilization_14d", "lt", 0.8, 2, "line under-utilised"), C("spare_units_per_day", "gt", 50, 2, "spare capacity")),
        supporting=(C("line_utilization_14d", "lt", 0.8, 3, "utilisation below 80%"), C("spare_units_per_day", "gt", 50, 2, "meaningful spare capacity"), C("product_days_of_cover", "gt", 7, 1, "finished-goods cover adequate"), C("component_min_cover_days", "gt", 7, 1, "component cover adequate")),
        contradicting=(C("line_utilization_14d", "gte", 0.92, 3, "line near capacity"), C("product_days_of_cover", "lt", 5, 1, "finished goods tight"), C("component_min_cover_days", "lt", 4, 1, "component stock tight")),
        next_step="Offer shorter lead times to growing accounts; add shifts only once growth is confirmed.",
    ),
    HypothesisSpec(
        "INVENTORY_REBALANCING", "Inventory rebalancing", "Stock exceeds demand for this item; redirecting it releases working capital.",
        kinds=("opportunity",), subject_types=("EXCESS_INVENTORY",), requires_any=("product_days_of_cover",),
        triggers=(C("product_days_of_cover", "gt", 40, 3, "excess cover"), C("product_stock_change_pct", "gt", 20, 2, "stock building")),
        supporting=(C("product_days_of_cover", "gt", 40, 3, "cover far above policy"), C("product_stock_change_pct", "gt", 20, 2, "stock rising"), C("order_growth_pct", "lt", 0, 1, "demand softening"), C("regions_growing", "gte", 1, 1, "a growing region can absorb stock")),
        contradicting=(C("product_days_of_cover", "lt", 20, 3, "cover within policy"), C("order_growth_pct", "gt", 20, 1, "demand rising")),
        next_step="Redirect stock to growing regions and pause production of the SKU.",
    ),
    HypothesisSpec(
        "SEGMENT_EXPANSION", "Customer segment expansion", "New and existing customers in a segment are buying more; the customer base itself is growing.",
        kinds=("opportunity",), subject_types=("SEGMENT_GROWTH", "DEMAND_OPPORTUNITY", "UNDERSERVED_REGION"), requires_any=("segments_growing",),
        triggers=(C("segments_growing", "gte", 1, 2, "segment growing"), C("customers_growth_pct", "gt", 10, 2, "customer base growing"), C("new_customers", "gte", 2, 1, "new customers")),
        supporting=(C("segments_growing", "gte", 1, 2, "segment growing"), C("customers_growth_pct", "gt", 10, 2, "more active customers"), C("new_customers", "gte", 2, 2, "new customers acquired"), C("sustained_growth_weeks", "gte", 2, 1, "growth sustained"), C("order_growth_pct", "gt", 15, 1, "orders up")),
        contradicting=(C("top_customer_share_of_incremental", "gt", 0.5, 2, "one customer dominates"), C("customers_growth_pct", "lt", 0, 2, "customer base shrinking")),
        next_step="Profile the new accounts and replicate the win pattern across the segment.",
    ),
]

RISK_HYPOTHESES: list[HypothesisSpec] = [
    HypothesisSpec(
        "SUPPLIER_RELIABILITY_ISSUE", "Supplier reliability issue", "The supplier itself is slipping: lead time above baseline, batches late, reliability falling.",
        kinds=("risk",), subject_types=("SUPPLIER_DELAY", "INVENTORY_SHORTAGE"), requires_any=("supplier_lead_time_ratio", "supplier_delayed_batches"),
        triggers=(C("supplier_delayed_batches", "gte", 1, 3, "batches delayed"), C("supplier_lead_time_ratio", "gt", 1.2, 2, "lead time above baseline"), C("supplier_reliability", "lt", 0.85, 1, "reliability low")),
        supporting=(C("supplier_delayed_batches", "gte", 1, 3, "open batches are late"), C("supplier_lead_time_ratio", "gt", 1.2, 2, "lead time extended"), C("supplier_reliability", "lt", 0.85, 1, "reliability score low"), C("supplier_late_received_ratio", "gt", 0.2, 1, "history of late deliveries"), C("supplier_max_delay_days", "gt", 3, 1, "material delay")),
        contradicting=(C("supplier_delayed_batches", "lt", 1, 3, "no delayed batch"), C("supplier_lead_time_ratio", "lt", 1.1, 1, "lead time at baseline")),
        next_step="Escalate with the supplier for a firm delivery date; activate the secondary source if qualified.",
    ),
    HypothesisSpec(
        "DEMAND_SURGE_CONSUMPTION", "Demand surge", "Consumption has outrun the replenishment plan because downstream demand jumped.",
        kinds=("risk",), subject_types=("SUPPLIER_DELAY", "INVENTORY_SHORTAGE"), requires_any=("consumption_change_pct", "order_growth_pct"),
        triggers=(C("consumption_change_pct", "gt", 15, 3, "consumption up"), C("order_growth_pct", "gt", 15, 3, "orders up"), C("item_stock_change_pct", "lt", -30, 1, "stock draining fast")),
        supporting=(C("consumption_change_pct", "gt", 15, 3, "daily consumption increased"), C("order_growth_pct", "gt", 15, 3, "customer orders increased"), C("item_stock_change_pct", "lt", -30, 1, "stock fell sharply"), C("sustained_growth_weeks", "gte", 2, 1, "growth sustained")),
        contradicting=(C("consumption_change_pct", "lt", 5, 2, "consumption flat"), C("order_growth_pct", "lt", 5, 2, "orders flat")),
        next_step="Raise the reorder point and safety stock to the new run-rate; expedite an interim batch.",
    ),
    HypothesisSpec(
        "REORDER_POLICY_GAP", "Reorder policy gap", "The reorder point does not cover the replenishment lead time, so normal variability triggers shortages.",
        kinds=("risk",), subject_types=("SUPPLIER_DELAY", "INVENTORY_SHORTAGE"), requires_any=("reorder_policy_gap_days",),
        triggers=(C("reorder_policy_gap_days", "gt", 0, 3, "reorder point below lead-time cover"),),
        supporting=(C("reorder_policy_gap_days", "gt", 0, 3, "reorder point does not cover lead time"), C("item_below_safety", "true", None, 1, "stock below safety"), C("supplier_delayed_batches", "lt", 1, 1, "no supplier delay to blame")),
        contradicting=(C("reorder_policy_gap_days", "lte", -2, 3, "reorder point covers lead time with margin"), C("supplier_delayed_batches", "gte", 1, 1, "supplier delay explains the shortage")),
        next_step="Recalculate reorder point = lead time × consumption + safety stock.",
    ),
    HypothesisSpec(
        "SINGLE_SOURCE_EXPOSURE", "Single-source exposure", "A sole-sourced component leaves no fallback when the supplier slips.",
        kinds=("risk",), subject_types=("SUPPLIER_DELAY", "INVENTORY_SHORTAGE"), requires_any=("supplier_sole_source",),
        triggers=(C("supplier_sole_source", "true", None, 2, "sole source"), C("supplier_delayed_batches", "gte", 1, 1, "and delayed")),
        supporting=(C("supplier_sole_source", "true", None, 3, "only one qualified source"), C("supplier_delayed_batches", "gte", 1, 2, "that source is late"), C("item_days_of_cover", "lt", 5, 1, "stock cover short")),
        contradicting=(C("supplier_sole_source", "false", None, 3, "alternative source exists"),),
        next_step="Qualify a secondary supplier and hold strategic safety stock for the component.",
    ),
    HypothesisSpec(
        "PRODUCTION_SHORTFALL", "Production shortfall", "The line did not produce enough to replenish finished goods.",
        kinds=("risk",), subject_types=("INVENTORY_SHORTAGE", "DELIVERY_DELAY"), requires_any=("output_change_pct",),
        triggers=(C("output_change_pct", "lt", -10, 3, "output down"), C("downtime_recent_hours", "gt", 4, 1, "downtime"), C("output_vs_plan", "lt", 0.9, 1, "under plan")),
        supporting=(C("output_change_pct", "lt", -10, 3, "line output fell"), C("downtime_recent_hours", "gt", 4, 1, "downtime recorded"), C("output_vs_plan", "lt", 0.9, 1.5, "output under plan")),
        contradicting=(C("output_change_pct", "gt", 0, 3, "output up"),),
        next_step="Check the line's incidents and downtime log for the period.",
    ),
    HypothesisSpec(
        "DEMAND_CONTRACTION", "Market demand contraction", "Customers are buying less across the board: fewer units, fewer active customers, market index falling.",
        kinds=("risk",), subject_types=("REVENUE_DECLINE", "MARGIN_DECLINE"), requires_any=("order_growth_pct",),
        triggers=(C("order_growth_pct", "lt", -15, 3, "orders down"), C("market_category_growth_pct", "lt", -2, 2, "market index falling"), C("customers_growth_pct", "lt", -10, 1, "fewer customers")),
        supporting=(C("order_growth_pct", "lt", -15, 3, "unit demand fell"), C("revenue_growth_pct", "lt", -15, 1, "revenue fell"), C("market_category_growth_pct", "lt", -2, 2, "synthetic market index falling"), C("customers_growth_pct", "lt", -10, 1, "active customers fell")),
        contradicting=(C("order_growth_pct", "gt", 0, 3, "unit demand not falling"), C("market_category_growth_pct", "gt", 3, 1, "market growing")),
        next_step="Review the category forecast and reduce planned volume before inventory builds.",
    ),
    HypothesisSpec(
        "PRICING_PRESSURE", "Pricing pressure", "Revenue and margin are eroding through discounting rather than volume loss.",
        kinds=("risk",), subject_types=("REVENUE_DECLINE", "MARGIN_DECLINE"), requires_any=("avg_discount_pct",),
        triggers=(C("avg_discount_pct", "gt", 5, 3, "heavy discounting"), C("competitor_discounting", "gte", 1, 2, "competitor discounting"), C("margin_gap_points", "gt", 3, 1, "margin below target")),
        supporting=(C("avg_discount_pct", "gt", 5, 3, "average discount elevated"), C("competitor_discounting", "gte", 1, 2, "competitors discounting"), C("margin_gap_points", "gt", 3, 1.5, "margin gap to target"), C("company_margin_change_points", "lt", -1, 1, "company margin down")),
        contradicting=(C("avg_discount_pct", "lt", 3, 3, "discounting normal"), C("company_margin_change_points", "gt", 0, 1, "company margin stable")),
        next_step="Audit recent discount approvals and reset price floors for the category.",
    ),
    HypothesisSpec(
        "CUSTOMER_CHURN", "Customer churn", "Specific customers stopped ordering.",
        kinds=("risk",), subject_types=("REVENUE_DECLINE", "MARGIN_DECLINE"), requires_any=("customers_growth_pct",),
        triggers=(C("customers_growth_pct", "lt", -10, 3, "fewer active customers"), C("new_customers", "lt", 1, 1, "no new customers")),
        supporting=(C("customers_growth_pct", "lt", -10, 3, "active customers fell"), C("new_customers", "lt", 1, 1, "no new customers"), C("order_growth_pct", "lt", -10, 1, "orders down")),
        contradicting=(C("customers_growth_pct", "gt", 0, 3, "customer base stable"),),
        next_step="List customers whose orders stopped and contact them.",
    ),
    HypothesisSpec(
        "SUPPLY_CONSTRAINED_SALES", "Supply-constrained sales", "Revenue fell because the line could not produce, not because demand fell.",
        kinds=("risk",), subject_types=("REVENUE_DECLINE", "MARGIN_DECLINE"), requires_any=("output_change_pct",),
        triggers=(C("output_change_pct", "lt", -10, 3, "output down"), C("line_utilization_recent", "gt", 0.95, 1, "line saturated")),
        supporting=(C("output_change_pct", "lt", -10, 3, "line output fell"), C("line_utilization_recent", "gt", 0.95, 1, "line at capacity"), C("downtime_recent_hours", "gt", 6, 1, "downtime")),
        contradicting=(C("output_change_pct", "gt", -3, 3, "output stable"),),
        next_step="Reconcile lost sales against production shortfall.",
    ),
    HypothesisSpec(
        "COMPETITOR_PRESSURE", "Competitor pressure", "Competitor launches or discounting are taking share.",
        kinds=("risk",), subject_types=("REVENUE_DECLINE", "MARGIN_DECLINE"), requires_any=("competitor_launches", "competitor_discounting"),
        triggers=(C("competitor_launches", "gte", 1, 2, "competitor launch"), C("competitor_discounting", "gte", 1, 2, "competitor discounting")),
        supporting=(C("competitor_launches", "gte", 1, 2, "competitor launched a rival product"), C("competitor_discounting", "gte", 1, 2, "competitor discounting activity"), C("order_growth_pct", "lt", -10, 1, "orders down")),
        contradicting=(C("competitor_launches", "lt", 1, 1, "no launch"), C("competitor_discounting", "lt", 1, 1, "no discounting")),
        next_step="Benchmark price and lead time against the competitor offer.",
    ),
]

REGISTRY: list[HypothesisSpec] = INCIDENT_HYPOTHESES + OPPORTUNITY_HYPOTHESES + RISK_HYPOTHESES
REGISTRY_BY_TYPE: dict[str, HypothesisSpec] = {s.type: s for s in REGISTRY}


def registry_for(domain: str) -> list[HypothesisSpec]:
    if domain == "merchant":
        from app.domains.merchant.registry import MERCHANT_HYPOTHESES

        return MERCHANT_HYPOTHESES
    return REGISTRY


def describe_registry(domain: str = "manufacturing") -> list[dict[str, Any]]:
    def cond(c: Condition) -> dict[str, Any]:
        return {"signal": c.signal, "op": c.op, "threshold": c.threshold, "weight": c.weight, "label": c.label}

    return [{"type": s.type, "label": s.label, "description": s.description, "kinds": list(s.kinds), "subject_types": list(s.subject_types), "requires_any": list(s.requires_any), "triggers": [cond(c) for c in s.triggers], "supporting": [cond(c) for c in s.supporting], "contradicting": [cond(c) for c in s.contradicting], "next_step": s.next_step} for s in registry_for(domain)]


def label_for(hypothesis_type: str | None) -> str | None:
    """Plain-language name of a hypothesis from whichever domain registry defines it."""
    if not hypothesis_type:
        return None
    from app.domains.merchant.registry import MERCHANT_REGISTRY_BY_TYPE

    spec = MERCHANT_REGISTRY_BY_TYPE.get(hypothesis_type) or REGISTRY_BY_TYPE.get(hypothesis_type)
    return spec.label if spec else hypothesis_type.replace("_", " ").capitalize()
