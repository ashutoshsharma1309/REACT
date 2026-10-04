"""Simulation engine: runs scenarios inside a recorded SimulationRun and updates system state."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database.base import utcnow
from app.models import SimulationRun, SystemState
from app.models.enums import RunStatus, ScenarioType
from app.observability import get_logger, log_event
from app.simulation.generator import GeneratorConfig, SeedSummary, seed_database
from app.simulation.scenarios import SCENARIOS
from app.simulation.scenarios.base import ScenarioError

log = get_logger("react.simulation")


class SimulationEngine:
    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, scenario: str, params: dict[str, Any] | None = None) -> SimulationRun:
        from app.domains import MERCHANT, current_domain

        if current_domain(self.db) == MERCHANT:  # merchant scenarios are events injected into a deterministic regeneration
            from app.domains.merchant.simulation import MerchantScenarioError, regenerate

            try:
                return regenerate(self.db, scenario=scenario)
            except MerchantScenarioError as exc:
                raise ScenarioError(str(exc)) from None
        if scenario not in SCENARIOS:
            raise ScenarioError(f"unknown scenario '{scenario}'")
        params = json.loads(json.dumps({k: v for k, v in (params or {}).items() if v is not None}, default=str))  # JSON-safe (UUIDs -> str)
        run = SimulationRun(scenario=ScenarioType(scenario), parameters=params, status=RunStatus.RUNNING)
        self.db.add(run)
        self.db.flush()
        state = self.db.get(SystemState, 1)
        if state is None:
            run.status = RunStatus.FAILED
            run.error = "database not seeded"
            self.db.commit()
            raise ScenarioError("Database is not seeded. Call POST /simulation/reset first.")
        state.simulator_status = "RUNNING"
        log_event(log, "simulation.started", run_id=run.id, scenario=scenario, parameters=params)
        try:
            impl = SCENARIOS[scenario](self.db, {**params, "_run_id": run.id})
            result = impl.apply()
            run.window_start, run.window_end = result.window_start, result.window_end
            run.parameters = json.loads(json.dumps({**params, **result.parameters}, default=str))  # request + resolved targets
            run.affected_entities = dict(result.affected_entities)
            run.effects = {**result.effects, "summary": result.summary}
            run.status = RunStatus.COMPLETED
            run.finished_at = utcnow()
            state.simulator_status = "IDLE"
            state.last_simulation_id = run.id
            state.last_simulation_at = run.finished_at
            self.db.commit()
            log_event(log, "simulation.completed", run_id=run.id, scenario=scenario, summary=result.summary, affected={k: len(v) for k, v in result.affected_entities.items()})
            return run
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            run = self.db.merge(SimulationRun(id=run.id, scenario=ScenarioType(scenario), parameters=params, status=RunStatus.FAILED, error=str(exc), finished_at=utcnow()))
            state = self.db.get(SystemState, 1)
            if state:
                state.simulator_status = "ERROR"
            self.db.commit()
            log_event(log, "simulation.failed", level=40, run_id=run.id, scenario=scenario, error=str(exc))
            raise

    def reset(self, seed: int | None = None, history_days: int | None = None) -> tuple[SimulationRun, SeedSummary]:
        """Wipe everything and regenerate the deterministic baseline dataset."""
        from app.domains import MERCHANT, current_domain

        state = self.db.get(SystemState, 1)
        outage_mode = state.ai_outage_mode if state else "off"  # an operator setting, not business data: survives a data reset
        domain = current_domain(self.db)  # a reset regenerates the same domain pack (and merchant profile)
        keep_anchor = state is not None and history_days is None  # same simulated "now" unless a new window is requested
        if domain == MERCHANT:
            from app.domains.merchant.simulation import regenerate

            run = regenerate(self.db, seed=seed, history_days=history_days)
            state = self.db.get(SystemState, 1)
            from app.simulation.generator import SeedSummary

            return run, SeedSummary(state.seed, state.data_start, state.data_end, dict((run.effects or {}).get("counts", {})))
        cfg = GeneratorConfig(
            seed=seed if seed is not None else (state.seed if state else settings.seed),
            history_days=history_days or settings.history_days,
            sensor_interval_minutes=settings.sensor_interval_minutes,
            anchor=state.data_end if keep_anchor else None,
        )
        log_event(log, "simulation.reset.started", seed=cfg.seed, history_days=cfg.history_days)
        summary = seed_database(self.db, cfg, clear=True, domain=domain)
        run = SimulationRun(scenario=ScenarioType.RESET, status=RunStatus.COMPLETED, parameters={"seed": cfg.seed, "history_days": cfg.history_days}, effects={"counts": summary.counts, "summary": "Dataset reset to the deterministic baseline."}, finished_at=utcnow(), window_start=summary.data_start, window_end=summary.data_end)
        self.db.add(run)
        state = self.db.get(SystemState, 1)
        state.last_simulation_id = run.id
        state.last_simulation_at = run.finished_at
        state.ai_outage_mode = outage_mode or "off"
        self.db.commit()
        log_event(log, "simulation.reset.completed", run_id=run.id, counts=summary.counts)
        return run, summary
