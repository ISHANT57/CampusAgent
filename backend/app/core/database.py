"""Database engine, session factory, and declarative base.

Neon is serverless Postgres: idle connections get closed on their side without
the client noticing. `pool_pre_ping` issues a cheap liveness check before
handing a connection out, so a stale one is discarded and replaced instead of
surfacing as a random OperationalError several layers up — usually in the
middle of a run, after LLM calls have already been paid for.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    # Discards connections Neon closed while idle. Without it the first query
    # after a quiet period fails, which on Render free tier (which itself
    # spins down) would be most first queries.
    pool_pre_ping=True,
    # Recycle below Neon's own idle timeout so we close first, on our terms.
    pool_recycle=280,
    # Render free tier is one instance, but each in-flight run holds a
    # connection for its whole duration (the background executor's session),
    # and the connection string uses Neon's pooler, which multiplexes — so
    # a modestly larger pool is safe and headroom matters when several runs
    # and stream polls overlap.
    pool_size=5,
    max_overflow=15,
    # Fail fast rather than hang: if the pool is genuinely exhausted, a request
    # that waited 30s (the default) has already lost the client. 10s surfaces
    # the real problem instead of masking it as a slow response.
    pool_timeout=10,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for every model.

    SQLAlchemy 2.0 style (a DeclarativeBase subclass), not 1.x's
    `declarative_base()` factory — this one participates in static typing, so
    `Mapped[int]` annotations are checked rather than decorative.
    """


def get_db():
    """FastAPI dependency. One session per request, always closed.

    expire_on_commit=False above matters here: without it, attributes are
    expired after commit and touching them post-commit triggers a fresh SELECT
    on a session that is about to close.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
