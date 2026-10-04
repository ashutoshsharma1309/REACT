"""Cross-process locks for work that must run once even when several API tasks start together.

On ECS, two or more containers can boot at the same time. Alembic migrations and the first-boot synthetic seed
must not run concurrently, so both take a PostgreSQL session-level advisory lock. On other databases this is a
no-op (local SQLite-style experiments only).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection

MIGRATION_LOCK_KEY = 72_310_601  # arbitrary constants, unique within REACT
SEED_LOCK_KEY = 72_310_602


@contextmanager
def advisory_lock(conn: Connection, key: int) -> Iterator[None]:
    if conn.dialect.name != "postgresql":
        yield
        return
    conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
    try:
        yield
    finally:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
