"""FastAPI application.

Thin by design. The agent is a library (`app.agent.loop`); this exposes it over
HTTP and does nothing else clever.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1.health import router as health_router
from app.api.v1.identity_dep import router as identity_router
from app.api.v1.providers import router as providers_router
from app.api.v1.runs import router as runs_router
from app.core.config import get_settings
from app.core.rate_limit import limiter

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reap on startup: a run left `running` by the process this one is
    # replacing would otherwise stay that way forever, indistinguishable from
    # one still thinking. Startup is exactly when that has just happened.
    from app.core.database import SessionLocal
    from app.core.reaper import reap_stale_runs

    db = SessionLocal()
    try:
        if reaped := reap_stale_runs(db):
            logger.warning("startup: reaped %s abandoned run(s)", reaped)
    except Exception as e:  # noqa: BLE001
        # Never block startup on housekeeping. A database blip at boot must not
        # take the service down — /health is deliberately dependency-free for
        # the same reason.
        logger.error("startup reaper failed: %s", e)
    finally:
        db.close()
    yield


app = FastAPI(
    title="CampusBrain Agent",
    description=(
        "Autonomous agent runtime. Consumes CampusBrain RAG (Project 1) as an "
        "external service. Bring your own AI provider."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Health is mounted at the root, NOT under /api/v1: platform probes expect a
# stable, unversioned path. Versioning a liveness check means a future /api/v2
# silently breaks the deploy platform's polling.
app.include_router(health_router)
app.include_router(runs_router, prefix="/api/v1")
app.include_router(identity_router, prefix="/api/v1")
app.include_router(providers_router, prefix="/api/v1")

# CORS. The identity cookie must survive a cross-origin browser client (a
# Vercel frontend calling a Render backend), which requires allow_credentials.
#
# Credentialed CORS and allow_origins=["*"] are mutually exclusive — browsers
# reject the combination outright, and the cookie would silently never be sent,
# making every request look like a brand-new visitor. So a wildcard is treated
# as "no browser credentials", and a real deploy MUST set
# CORS_ALLOWED_ORIGINS to the actual frontend origin.
_wildcard = "*" in settings.cors_origins
if _wildcard and settings.cors_origins != ["*"]:
    logger.warning("CORS_ALLOWED_ORIGINS mixes '*' with explicit origins; '*' wins and cookies are disabled")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Vercel preview deployments get a unique host each time, so an exact
    # allow-list breaks every preview — and it fails as a 404 on the run
    # rather than a CORS error, because the cookie is simply never sent.
    allow_origin_regex=settings.cors_allowed_origin_regex or None,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Last-Event-ID", "X-Identity"],
    allow_credentials=not _wildcard,
)
