"""Power instability (Phase 2 addition): unstable electrical supply on a production line.

Correlated effects
  power readings on ALL machines of the line fluctuate strongly and drift up   -> sensor_readings
  temperature creeps up slightly on the same machines                           -> sensor_readings
  output dips, defects rise modestly, short stops recorded as downtime          -> production_metrics
No vibration signature and no single-sensor fault, so investigation should favour
POWER_INSTABILITY over BEARING_DEGRADATION and SENSOR_FAILURE.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update

from app.models import SensorReading
from app.simulation.clock import is_operating
from app.simulation.scenarios.base import Scenario, ScenarioResult

DEFAULT_LINE = "LINE-A"


class PowerInstabilityScenario(Scenario):
    name = "power_instability"

    def apply(self) -> ScenarioResult:
        days = self.param_int("days", 3, 1, 14)
        intensity = self.param_float("intensity", 1.0, 0.3, 2.0)
        line = self.get_line(self.param_uuid("production_line_id"), DEFAULT_LINE)
        start, end = self.window(days)
        result = ScenarioResult(self.name, start, end, {"production_line_id": str(line.id), "line_code": line.code, "days": days, "intensity": intensity})
        modified = 0
        for machine in line.machines:
            for sensor in machine.sensors:
                if sensor.sensor_type.value not in ("POWER", "TEMPERATURE"):
                    continue
                rows = self.readings(sensor, start, end)
                updates = []
                for i, r in enumerate(rows):
                    if not is_operating(r.timestamp):
                        continue
                    if sensor.sensor_type.value == "POWER":
                        # sawtooth-like fluctuation (deterministic) plus an upward drift
                        swing = 0.45 * intensity * (1 if i % 2 == 0 else -0.6)
                        v = r.value * (1 + 0.14 * intensity + swing)
                    else:
                        v = r.value * (1 + 0.05 * intensity)
                    updates.append({"id": r.id, "value": round(max(0.0, v), 3)})
                if updates:
                    self.db.execute(update(SensorReading), updates)
                    modified += len(updates)
                result.touch("sensors", sensor.id)
            result.touch("machines", machine.id)
        totals = self.scale_production(line, start.date(), end.date(), output_mult=lambda f: 1 - 0.12 * intensity * (0.5 + 0.5 * f), defect_mult=lambda f: 1 + 0.5 * intensity * f, extra_downtime=lambda f: 0.8 * intensity)
        result.touch("production_metrics", *totals["metric_ids"])
        result.touch("production_lines", line.id)
        self.recompute_financials(start.date(), end.date())
        result.effects = {"sensor_readings_modified": modified, "machines_affected": len(line.machines), "lost_units": round(totals["lost_units"]), "added_downtime_hours": round(totals["added_downtime_hours"], 1)}
        result.summary = f"Unstable power on {line.name} for {days} day(s): power draw fluctuating on all {len(line.machines)} machines, output dipping and short stops recorded."
        _ = timedelta
        return result
