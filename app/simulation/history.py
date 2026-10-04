"""Scripted historical events embedded in the seeded history.

Each event leaves a realistic signature in sensor readings, production metrics, supplier
batches and maintenance records, and is recorded in `historical_incidents` with its
leading indicators. Future agents can use these as labelled evidence of what a failure
looked like before it happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class HistoricalEvent:
    key: str
    incident_type: str
    severity: str
    days_before_end: int  # when the failure / peak occurred, counted back from data_end
    machine_code: str | None = None
    line_code: str | None = None
    component_type: str | None = None
    ramp_days: float = 0.0  # degradation ramp before the peak
    downtime_hours: float = 0.0  # hard downtime after the peak
    aftermath_days: float = 0.0  # reduced-efficiency period after the peak (line-level events)
    sensor_gain: dict[str, float] = field(default_factory=dict)  # sensor_type -> relative change at full ramp
    health_drop: float = 0.0
    output_multiplier: float = 1.0  # at full effect
    defect_multiplier: float = 1.0  # at full effect
    sensor_fault_type: str | None = None  # e.g. "TEMPERATURE" -> that sensor is stuck during the event window
    description: str = ""
    root_cause: str = ""
    resolution: str = ""
    estimated_loss: float = 0.0
    maintenance_description: str = ""
    supplier_batch_delay_days: int = 0
    supplier_quality_pass_rate: float | None = None

    # ---- resolved absolute times (computed by HistoryEffects) ----------------------------
    def peak_at(self, data_end: datetime) -> datetime:
        return (data_end - timedelta(days=self.days_before_end)).replace(hour=9, minute=0, second=0, microsecond=0)


HISTORICAL_EVENTS: list[HistoricalEvent] = [
    HistoricalEvent(
        key="grd_b1_bearing_failure",
        incident_type="MACHINE_DEGRADATION",
        severity="CRITICAL",
        days_before_end=62,
        machine_code="GRD-B1",
        line_code="LINE-B",
        component_type="BEARING",
        ramp_days=9,
        downtime_hours=22,
        sensor_gain={"TEMPERATURE": 0.34, "VIBRATION": 1.7, "POWER": 0.22, "RPM": -0.06},
        health_drop=0.4,
        output_multiplier=0.72,
        defect_multiplier=2.6,
        description="Wheel spindle bearing failure on CNC Grinding Machine B1 after nine days of rising vibration and temperature.",
        root_cause="Lubrication starvation of the wheel spindle bearing; grease line partially blocked.",
        resolution="Spindle bearing replaced, grease line flushed, vibration alarm thresholds reviewed.",
        estimated_loss=1_850_000,
        maintenance_description="Corrective: replaced wheel spindle bearing (P/N GRD-BRG-7212) after failure; flushed lubrication lines.",
    ),
    HistoricalEvent(
        key="hpr_a3_hydraulic_pressure_loss",
        incident_type="ABNORMAL_PRESSURE",
        severity="HIGH",
        days_before_end=38,
        machine_code="HPR-A3",
        line_code="LINE-A",
        component_type="SEAL",
        ramp_days=4,
        downtime_hours=9,
        sensor_gain={"PRESSURE": -0.28, "TEMPERATURE": 0.12, "POWER": 0.1},
        health_drop=0.25,
        output_multiplier=0.85,
        defect_multiplier=1.8,
        description="Gradual hydraulic pressure loss on Hydraulic Press A3 leading to under-formed valve bodies.",
        root_cause="Main cylinder seal kit degraded beyond service life; internal leakage.",
        resolution="Seal kit replaced during a 9h corrective stop; hydraulic oil replaced.",
        estimated_loss=640_000,
        maintenance_description="Corrective: replaced main cylinder seal kit and hydraulic oil after pressure loss.",
    ),
    HistoricalEvent(
        key="cnc_c1_temperature_sensor_stuck",
        incident_type="SENSOR_FAULT",
        severity="LOW",
        days_before_end=85,
        machine_code="CNC-C1",
        line_code="LINE-C",
        ramp_days=0,
        downtime_hours=0,
        aftermath_days=1.0,
        sensor_fault_type="TEMPERATURE",
        description="Temperature sensor on CNC Turning Center C1 reported a frozen value for ~24h while the machine ran normally.",
        root_cause="Loose transmitter terminal; sensor output latched at last value.",
        resolution="Terminal re-crimped; sensor recalibrated. No production impact.",
        estimated_loss=0,
        maintenance_description="Inspection: re-crimped temperature transmitter terminal and recalibrated sensor.",
    ),
    HistoricalEvent(
        key="fur_b3_heating_element_downtime",
        incident_type="MACHINE_DOWNTIME",
        severity="HIGH",
        days_before_end=95,
        machine_code="FUR-B3",
        line_code="LINE-B",
        component_type="HEATER",
        ramp_days=2,
        downtime_hours=30,
        sensor_gain={"TEMPERATURE": -0.05, "POWER": 0.18},
        health_drop=0.3,
        output_multiplier=0.6,
        defect_multiplier=1.4,
        description="Heating element bank failure on Heat Treatment Furnace B3 caused a 30h stop of the bearings line.",
        root_cause="End-of-life heating elements; power draw rose as elements degraded.",
        resolution="Heating element bank replaced; thermocouples verified.",
        estimated_loss=2_100_000,
        maintenance_description="Corrective: replaced heating element bank and verified thermocouple calibration.",
    ),
    HistoricalEvent(
        key="line_c_coating_defect_spike",
        incident_type="DEFECT_SPIKE",
        severity="MEDIUM",
        days_before_end=20,
        line_code="LINE-C",
        component_type="COATING_CHEMICALS",
        ramp_days=0,
        aftermath_days=3.0,
        output_multiplier=0.93,
        defect_multiplier=2.8,
        supplier_quality_pass_rate=0.81,
        description="Defect rate on the hydraulic fittings line tripled for three days due to an off-spec coating chemical batch.",
        root_cause="Supplier batch of coating chemicals out of viscosity specification.",
        resolution="Batch quarantined, supplier issued corrective action report, incoming inspection tightened.",
        estimated_loss=410_000,
    ),
    HistoricalEvent(
        key="line_a_casting_supply_delay",
        incident_type="PRODUCTION_DROP",
        severity="MEDIUM",
        days_before_end=50,
        line_code="LINE-A",
        component_type="STEEL_CASTING",
        ramp_days=0,
        aftermath_days=3.0,
        output_multiplier=0.78,
        defect_multiplier=1.0,
        supplier_batch_delay_days=6,
        description="Valve line output dropped ~20% for three days while a delayed steel casting batch was awaited.",
        root_cause="Primary casting supplier delivered six days late (foundry furnace outage).",
        resolution="Partial volume shifted to secondary supplier; safety stock for castings raised.",
        estimated_loss=520_000,
    ),
]


@dataclass
class MachineEffect:
    factor: float = 0.0  # 0..1 degradation ramp
    down: bool = False
    sensor_gain: dict[str, float] = field(default_factory=dict)
    stuck_sensor_type: str | None = None


@dataclass
class LineDayEffect:
    output_multiplier: float = 1.0
    defect_multiplier: float = 1.0
    downtime_hours: float = 0.0
    event_keys: list[str] = field(default_factory=list)


class HistoryEffects:
    """Resolves the scripted events into machine/timestamp and line/day effects."""

    def __init__(self, data_end: datetime, events: list[HistoricalEvent] | None = None) -> None:
        self.data_end = data_end
        self.events = events or HISTORICAL_EVENTS
        self.by_machine: dict[str, list[HistoricalEvent]] = {}
        self.by_line: dict[str, list[HistoricalEvent]] = {}
        for ev in self.events:
            if ev.machine_code:
                self.by_machine.setdefault(ev.machine_code, []).append(ev)
            if ev.line_code:
                self.by_line.setdefault(ev.line_code, []).append(ev)

    # ---- machine level ------------------------------------------------------------------
    def machine_effect(self, machine_code: str, ts: datetime) -> MachineEffect:
        eff = MachineEffect()
        for ev in self.by_machine.get(machine_code, []):
            peak = ev.peak_at(self.data_end)
            ramp_start = peak - timedelta(days=ev.ramp_days)
            down_end = peak + timedelta(hours=ev.downtime_hours)
            if ev.sensor_fault_type:
                if peak <= ts < peak + timedelta(days=ev.aftermath_days or 1):
                    eff.stuck_sensor_type = ev.sensor_fault_type
                continue
            if ramp_start <= ts < peak and ev.ramp_days > 0:
                f = (ts - ramp_start).total_seconds() / (ev.ramp_days * 86400)
                f = min(1.0, max(0.0, f)) ** 1.6  # accelerating degradation
                if f > eff.factor:
                    eff.factor = f
                    eff.sensor_gain = dict(ev.sensor_gain)
            elif peak <= ts < down_end:
                eff.down = True
        return eff

    def machine_health_drop_now(self, machine_code: str) -> float:
        return 0.0  # historical events are resolved; current health comes from component wear

    # ---- line / day level ---------------------------------------------------------------
    def line_day_effect(self, line_code: str, d: date) -> LineDayEffect:
        eff = LineDayEffect()
        for ev in self.by_line.get(line_code, []):
            peak = ev.peak_at(self.data_end)
            peak_day = peak.date()
            ramp_start_day = (peak - timedelta(days=ev.ramp_days)).date()
            down_end = peak + timedelta(hours=ev.downtime_hours)
            after_end_day = (down_end + timedelta(days=ev.aftermath_days)).date()
            if ev.sensor_fault_type:
                continue
            if ev.ramp_days > 0 and ramp_start_day <= d < peak_day:
                f = ((d - ramp_start_day).days + 1) / max(ev.ramp_days, 1)
                f = min(1.0, f) ** 1.6
                eff.output_multiplier *= 1 - (1 - ev.output_multiplier) * f
                eff.defect_multiplier *= 1 + (ev.defect_multiplier - 1) * f
                eff.event_keys.append(ev.key)
            if peak_day <= d <= down_end.date() and ev.downtime_hours > 0:
                # hours of downtime falling on this day (16h operating day)
                day_start = datetime.combine(d, datetime.min.time()) + timedelta(hours=1)
                day_end = day_start + timedelta(hours=16)
                overlap = (min(day_end, down_end) - max(day_start, peak)).total_seconds() / 3600
                if overlap > 0:
                    eff.downtime_hours += min(16.0, overlap)
                    eff.defect_multiplier *= 1.2
                    eff.event_keys.append(ev.key)
            if ev.aftermath_days > 0 and down_end.date() <= d < after_end_day and (ev.downtime_hours == 0 or d > down_end.date()):
                eff.output_multiplier *= ev.output_multiplier
                eff.defect_multiplier *= ev.defect_multiplier
                eff.event_keys.append(ev.key)
        eff.event_keys = sorted(set(eff.event_keys))
        return eff
