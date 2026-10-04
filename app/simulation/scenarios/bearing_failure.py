"""Bearing failure: progressive degradation of a machine's bearing over the window.

Correlated effects
  temperature ↑, vibration ↑↑, power ↑, rpm ↓ (slightly)      -> sensor readings
  output ↓, defects ↑, downtime ↑                              -> production metrics of the line
  finished-goods inventory of the line's products ↓            -> inventory + snapshots
  bearing component condition ↓, failure probability ↑          -> machine_components
  machine status DEGRADED, health ↓                             -> machines
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import Machine, MachineComponent
from app.models.enums import ComponentCondition, MachineStatus
from app.simulation.scenarios.base import Scenario, ScenarioResult

SENSOR_GAINS = {"TEMPERATURE": 0.38, "VIBRATION": 1.7, "POWER": 0.24, "RPM": -0.05, "PRESSURE": 0.0}


class BearingFailureScenario(Scenario):
    name = "bearing_failure"

    def default_machine(self) -> Machine:
        """The machine whose most-worn BEARING component has the lowest condition score."""
        comp = self.db.scalar(select(MachineComponent).where(MachineComponent.component_type == "BEARING").order_by(MachineComponent.condition_score, MachineComponent.part_number))
        if comp is None:
            raise ValueError("no bearing components in dataset")
        return comp.machine

    def apply(self) -> ScenarioResult:
        days = self.param_int("days", 7, 2, 30)
        intensity = self.param_float("intensity", 1.0, 0.3, 2.0)
        machine_id = self.param_uuid("machine_id")
        machine = self.get_machine(machine_id) if machine_id else self.default_machine()
        line = machine.production_line
        start, end = self.window(days)
        result = ScenarioResult(self.name, start, end, {"machine_id": str(machine.id), "machine_code": machine.code, "days": days, "intensity": intensity})

        # 1. sensor signatures
        touched = {}
        for sensor in machine.sensors:
            gain = SENSOR_GAINS.get(sensor.sensor_type.value, 0.0) * intensity
            if gain:
                touched[sensor.code] = self.ramp_readings(sensor, start, end, gain)
                result.touch("sensors", sensor.id)
        # 2. production of the line: output falls, defects rise, downtime creeps in near the end
        totals = self.scale_production(
            line, start.date(), end.date(),
            output_mult=lambda f: 1 - 0.28 * intensity * f**1.5,
            defect_mult=lambda f: 1 + 2.2 * intensity * f**1.5,
            extra_downtime=lambda f: 1.8 * intensity * max(0.0, f - 0.6),
        )
        result.touch("production_metrics", *totals["metric_ids"])
        # 3. finished goods inventory of the line's products drains by the lost units (pro-rata by demand)
        products = self.line_products(line)
        lost = totals["lost_units"]
        if products and lost > 0:
            weights = {p.id: max(1.0, (self.product_inventory(p).avg_daily_consumption if self.product_inventory(p) else 1.0)) for p in products}
            wsum = sum(weights.values())
            for p in products:
                inv = self.product_inventory(p)
                if inv:
                    self.drain_inventory(inv, lost * weights[p.id] / wsum, start.date(), end.date())
                    result.touch("inventory", inv.id)
        # 4. component + machine state
        bearing = next((c for c in machine.components if c.component_type == "BEARING"), None)
        if bearing:
            bearing.condition_score = round(min(bearing.condition_score, 0.18 / intensity), 3)
            bearing.condition = ComponentCondition.CRITICAL
            bearing.failure_probability = round(min(0.95, 0.55 * intensity + 0.2), 3)
            result.touch("machine_components", bearing.id)
        machine.status = MachineStatus.DEGRADED
        machine.health_score = round(max(0.1, machine.health_score - 0.45 * intensity), 3)
        result.touch("machines", machine.id)
        result.touch("production_lines", line.id)
        self.recompute_financials(start.date(), end.date())
        result.effects = {
            "sensor_readings_modified": touched,
            "sensor_gains_at_peak": {k: round(v * intensity, 3) for k, v in SENSOR_GAINS.items() if v},
            "lost_units": round(totals["lost_units"]),
            "extra_defective_units": round(totals["extra_defects"]),
            "added_downtime_hours": round(totals["added_downtime_hours"], 1),
            "bearing_condition_score": bearing.condition_score if bearing else None,
            "bearing_failure_probability": bearing.failure_probability if bearing else None,
        }
        result.summary = f"Progressive bearing degradation on {machine.name} ({machine.code}) over {days} days: vibration and temperature rising, output on {line.name} falling."
        _ = timedelta
        return result
