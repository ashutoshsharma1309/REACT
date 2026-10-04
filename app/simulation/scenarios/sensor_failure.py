"""Sensor failure: ONE sensor misbehaves while the machine and the line stay healthy.

Modes: stuck (frozen value), spike (implausible values), dropout (missing readings).
The detection engine must classify this as SENSOR_FAULT rather than a machine problem,
because no correlated sensor or production anomaly exists.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select, update

from app.models import Sensor, SensorReading
from app.simulation.clock import is_operating
from app.simulation.scenarios.base import Scenario, ScenarioError, ScenarioResult

DEFAULT_SENSOR_CODE = "VMC-A2-TEMP"


class SensorFailureScenario(Scenario):
    name = "sensor_failure"

    def apply(self) -> ScenarioResult:
        hours = self.param_int("hours", 36, 6, 240)
        mode = str(self.params.get("mode") or "stuck").lower()
        if mode not in ("stuck", "spike", "dropout"):
            raise ScenarioError("mode must be one of stuck | spike | dropout")
        sensor_id = self.param_uuid("sensor_id")
        sensor = self.db.get(Sensor, sensor_id) if sensor_id else self.db.scalar(select(Sensor).where(Sensor.code == DEFAULT_SENSOR_CODE))
        if not sensor:
            raise ScenarioError("sensor not found")
        start = self.now - timedelta(hours=hours)
        end = self.now
        result = ScenarioResult(self.name, start, end, {"sensor_id": str(sensor.id), "sensor_code": sensor.code, "mode": mode, "hours": hours})
        rows = self.readings(sensor, start, end)
        modified = 0
        if mode == "stuck":
            frozen = rows[0].value if rows else sensor.normal_max
            self.db.execute(update(SensorReading), [{"id": r.id, "value": frozen} for r in rows])
            modified = len(rows)
            result.effects["frozen_value"] = frozen
        elif mode == "spike":
            updates = []
            for i, r in enumerate(rows):
                if not is_operating(r.timestamp):
                    continue
                # alternate between physically implausible highs and zeros
                v = sensor.critical_threshold * self.rng.uniform(2.6, 4.0) if i % 3 else 0.0
                updates.append({"id": r.id, "value": round(v, 3)})
            if updates:
                self.db.execute(update(SensorReading), updates)
            modified = len(updates)
        else:  # dropout: delete most readings in the window
            ids = [r.id for i, r in enumerate(rows) if i % 6 != 0]
            if ids:
                self.db.execute(delete(SensorReading).where(SensorReading.id.in_(ids)))
            modified = len(ids)
            result.effects["readings_deleted"] = len(ids)
        result.touch("sensors", sensor.id)
        result.touch("machines", sensor.machine_id)
        result.effects.update({"readings_modified": modified, "machine_code": sensor.machine.code, "production_impact": "none (isolated sensor fault)"})
        result.summary = f"{sensor.code} ({sensor.sensor_type.value}) on {sensor.machine.name} is {mode} for the last {hours}h; all other sensors and production remain normal."
        return result
