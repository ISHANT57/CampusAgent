"""Health endpoints.

Two of them, deliberately, because they answer different questions:

  /health       Am I alive?    -> the platform's restart decision
  /health/deps  Am I useful?   -> a human's debugging question

Conflating them is the classic outage amplifier: if the liveness probe checked
Postgres, a 30-second Neon blip would make the platform kill and restart a
perfectly healthy process — turning a brief dependency wobble into a cold start
on top of it. Project 1 hit this reasoning too and kept its own /health
dependency-free (CollegeRag backend/app/main.py:40).
"""

from __future__ import annotations

import time

from fastapi import APIRouter
from sqlalchemy import text

from app.core.database import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Liveness. Deliberately checks NOTHING.

    Answers exactly one question: is this process running and able to serve
    HTTP? Any dependency check here hands an external service the power to
    restart us.
    """
    return {"status": "ok"}


@router.get("/health/deps")
def health_deps() -> dict:
    """Readiness. Checks each dependency and reports per-dependency status.

    Always returns HTTP 200, even when a dependency is down — the report IS the
    payload. A non-200 here would tempt a platform into treating it as a
    liveness signal, which is the exact coupling /health exists to avoid.
    """
    checks: dict[str, dict] = {}

    started = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {
            "status": "ok",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as e:
        # Report the exception TYPE, not the message. Connection errors
        # routinely echo back the DSN, and this endpoint is unauthenticated —
        # a leaked Neon password in a health response would be a real incident.
        checks["database"] = {
            "status": "error",
            "error": type(e).__name__,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}
