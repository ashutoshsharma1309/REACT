"""Engine / session factory. The FastAPI dependency `get_db` yields a session per request."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database.base import Base

# pool_pre_ping survives RDS failover / idle disconnects; pool_recycle stays under typical idle timeouts.
# Connections per container = WEB_CONCURRENCY x (DB_POOL_SIZE + DB_MAX_OVERFLOW); keep the total under the RDS limit.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow, pool_recycle=1800, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_schema() -> None:
    """Create all tables (dev convenience). Alembic migrations are the source of truth."""
    import app.models  # noqa: F401  (ensure models are registered)

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
