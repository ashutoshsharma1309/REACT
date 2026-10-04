"""Merchant simulation control: baseline reset, demo scenarios and onboarding all regenerate the business.

Regeneration keeps the seed, the simulated clock (anchor), the history window, the merchant profile and the operator's
AI-drill setting, so it is deterministic and repeatable. It replaces every table, including earlier findings, decisions
and activity: a scenario always starts from a clean business. A SimulationRun records what was injected.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database.base import utcnow
from app.domains.merchant.profile import MerchantProfile
from app.domains.merchant.scenarios import SCENARIO_KEYS, build_scenario
from app.models import Product, SimulationRun, Supplier, SystemState
from app.models.enums import RunStatus, ScenarioType
from app.observability import get_logger, log_event

log = get_logger("react.merchant.simulation")
DEFAULT_HISTORY_DAYS = 120


class MerchantScenarioError(ValueError):
    pass


def regenerate(db: Session, scenario: str | None = None, profile: dict[str, Any] | None = None, seed: int | None = None, history_days: int | None = None) -> SimulationRun:
    from app.simulation.generator import GeneratorConfig, seed_database

    if scenario is not None and scenario not in SCENARIO_KEYS:
        raise MerchantScenarioError(f"unknown merchant scenario '{scenario}'. Available: {', '.join(SCENARIO_KEYS)}")
    state = db.get(SystemState, 1)
    prev = dict(state.profile or {}) if state else {}
    prof = MerchantProfile.from_dict(profile if profile is not None else prev.get("merchant")).as_dict()
    cfg = GeneratorConfig(
        seed=seed if seed is not None else (state.seed if state else settings.seed),
        history_days=history_days or ((state.data_end - state.data_start).days if state else max(DEFAULT_HISTORY_DAYS, settings.history_days)),
        anchor=state.data_end if state else None,
    )
    outage = state.ai_outage_mode if state else "off"
    summary = seed_database(db, cfg, clear=True, domain="merchant", profile=prof, scenario=scenario)
    state = db.get(SystemState, 1)
    state.ai_outage_mode = outage
    roles = (state.profile or {}).get("roles", {})
    spec = build_scenario(scenario, roles) if scenario else None
    kind = ScenarioType(scenario) if scenario else (ScenarioType.MERCHANT_SETUP if profile is not None else ScenarioType.RESET)
    affected: dict[str, list[str]] = {}
    for key in ("stockout_sku", "margin_sku", "excess_sku"):
        if spec and roles.get(key) and any(getattr(e, "sku", None) == roles[key] or roles[key] in getattr(e, "skus", ()) for e in spec.events):
            p = db.query(Product).filter(Product.sku == roles[key]).one_or_none()
            if p:
                affected.setdefault("products", []).append(str(p.id))
    if spec:
        for e in spec.events:
            sup_code = getattr(e, "supplier", None)
            if sup_code:
                from app.domains.merchant.catalog import get_template

                name = next((s.name for s in get_template(prof["business_type"]).suppliers if s.code == sup_code), None)
                sup = db.query(Supplier).filter(Supplier.name == name).one_or_none() if name else None
                if sup:
                    affected.setdefault("suppliers", []).append(str(sup.id))
    window_days = max([getattr(e, "start", getattr(e, "offset", 0)) for e in (spec.events if spec else [])] or [0])
    from datetime import timedelta

    summary_text = spec.story if spec else ("Business set up from the onboarding profile and simulated from scratch." if profile is not None else "Business reset to its normal baseline.")
    run = SimulationRun(scenario=kind, status=RunStatus.COMPLETED, parameters={"roles": roles, "events": (state.profile or {}).get("events", []), "profile": prof, "seed": cfg.seed}, affected_entities=affected, effects={"summary": summary_text, "counts": summary.counts}, finished_at=utcnow(), window_start=state.data_end - timedelta(days=window_days), window_end=state.data_end)
    db.add(run)
    db.flush()
    state.last_simulation_id = run.id
    state.last_simulation_at = run.finished_at
    db.commit()
    log_event(log, "merchant.simulation.completed", scenario=scenario or kind.value, run_id=run.id, business=prof["business_name"])
    return run
