"""Test fixtures.

* The test database (TEST_DATABASE_URL, default DATABASE_URL + "_test") is rebuilt and seeded ONCE per
  session with a smaller deterministic dataset (60 days, fixed anchor).
* Each test runs inside an outer transaction with SAVEPOINT-based commits, so scenario / detection
  commits are rolled back at the end of the test and tests stay independent.
"""
from __future__ import annotations

import logging
import tempfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.database.base import Base
import app.models  # noqa: F401
from app.simulation.generator import GeneratorConfig, seed_database

ANCHOR = datetime(2026, 9, 25, 7, 0, 0)
HISTORY_DAYS = 60

logging.disable(logging.WARNING)
settings.history_days = HISTORY_DAYS  # keeps POST /simulation/reset fast in tests
settings.seed_on_startup = False
settings.auto_create_schema = False
settings.storage_backend = "local"
settings.storage_local_dir = tempfile.mkdtemp(prefix="react-test-artifacts-")  # evidence packs never land in the repo
settings.metrics_enabled = False
settings.llm_provider = "deterministic"  # normal tests never need AWS credentials or network


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(settings.effective_test_database_url, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with Session(eng) as db:
        seed_database(db, GeneratorConfig(seed=42, history_days=HISTORY_DAYS, sensor_interval_minutes=60, anchor=ANCHOR), clear=False, domain="manufacturing")
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    conn = engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False, autoflush=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.fixture
def client(db):
    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)  # no lifespan: schema/seed handled by fixtures
    finally:
        app.dependency_overrides.clear()


# ---- REACT Merchant: a second, merchant-seeded test database --------------------------------------------------
MERCHANT_ANCHOR = datetime(2026, 10, 2, 7, 0, 0)
MERCHANT_HISTORY_DAYS = 120  # merchant baselines need 8 weeks + 8 weeks of comparison


def _merchant_url() -> str:
    from sqlalchemy.engine import make_url

    url = make_url(settings.effective_test_database_url)
    return url.set(database=f"{url.database}_merchant").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def merchant_engine():
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    url = make_url(_merchant_url())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as c:
        if not c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).scalar():
            c.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()
    eng = create_engine(url, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with Session(eng) as db:
        seed_database(db, GeneratorConfig(seed=42, history_days=MERCHANT_HISTORY_DAYS, anchor=MERCHANT_ANCHOR), clear=False, domain="merchant")
    yield eng
    eng.dispose()


@pytest.fixture
def mdb(merchant_engine):
    conn = merchant_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False, autoflush=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.fixture
def mclient(mdb):
    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: mdb
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def pytest_collection_modifyitems(config, items):
    """Test tiers: UNIT (no DB), INTEGRATION (PostgreSQL test DB), AWS_LIVE (real AWS, opt-in). `pytest -m unit` etc."""
    for item in items:
        if item.get_closest_marker("aws_live"):
            continue
        uses_db = {"db", "client", "engine", "mdb", "mclient", "merchant_engine"} & set(getattr(item, "fixturenames", ()))
        item.add_marker(pytest.mark.integration if uses_db else pytest.mark.unit)
