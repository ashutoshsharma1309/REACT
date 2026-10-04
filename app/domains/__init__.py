"""Domain packs.

The REACT platform core (investigation engine, Evidence Support Score, agents, decision lifecycle, artifacts) is
domain-agnostic. Domain knowledge (data generator, scenarios, detection rules, signals, hypotheses, impact selection,
tools, action catalogue, read models) lives in a domain pack:

    merchant       REACT Merchant: small Indian retail businesses (default; the only domain the UI presents)
    manufacturing  the original reference pack (backend only; keeps the shared core under regression test)

The active domain is a property of the dataset: `system_state.domain` records which pack generated it. Every engine
asks `current_domain(db)`, so one database always speaks one domain. `REACT_DOMAIN` only decides what a fresh seed
produces.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

MERCHANT = "merchant"
MANUFACTURING = "manufacturing"
DOMAINS = (MERCHANT, MANUFACTURING)


def default_domain() -> str:
    from app.config import settings

    d = (settings.react_domain or MERCHANT).lower()
    return d if d in DOMAINS else MERCHANT


def current_domain(db: Session | None) -> str:
    """Domain of the seeded dataset; falls back to the configured default when the database is empty."""
    if db is None:
        return default_domain()
    from app.models import SystemState

    try:
        state = db.get(SystemState, 1)
    except Exception:  # noqa: BLE001  (e.g. a session in a failed transaction)
        return default_domain()
    if state is not None and getattr(state, "domain", None) in DOMAINS:
        return state.domain
    return default_domain()


def is_merchant(db: Session | None) -> bool:
    return current_domain(db) == MERCHANT
