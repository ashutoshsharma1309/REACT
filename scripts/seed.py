"""Seed (or reseed) the database with the deterministic synthetic dataset.

Usage:
    python -m scripts.seed                # uses SEED / HISTORY_DAYS from the environment
    python -m scripts.seed --days 90 --seed 7
    python -m scripts.seed --domain manufacturing   # the backend reference pack instead of REACT Merchant
"""
from __future__ import annotations

import argparse
import json

from app.config import settings
from app.database import SessionLocal, init_schema
from app.domains import DOMAINS
from app.observability import configure_logging
from app.simulation.generator import GeneratorConfig, seed_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the REACT synthetic dataset")
    parser.add_argument("--seed", type=int, default=settings.seed)
    parser.add_argument("--days", type=int, default=settings.history_days)
    parser.add_argument("--domain", choices=sorted(DOMAINS), default=settings.react_domain, help="business domain to generate (default: REACT_DOMAIN)")
    parser.add_argument("--interval", type=int, default=settings.sensor_interval_minutes, help="sensor interval in minutes")
    args = parser.parse_args()
    configure_logging(settings.log_level)
    init_schema()
    with SessionLocal() as db:
        summary = seed_database(db, GeneratorConfig(seed=args.seed, history_days=args.days, sensor_interval_minutes=args.interval), domain=args.domain)
    print(json.dumps(summary.as_dict(), default=str, indent=2))


if __name__ == "__main__":
    main()
