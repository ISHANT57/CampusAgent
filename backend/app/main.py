"""FastAPI application.

Thin by design. The agent is a library (`app.agent.loop`), not a web service —
the CLI is the product through the whole Core Path, and this app exists so
there is somewhere to expose a run endpoint later without restructuring.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="CampusBrain Agent",
    description="Autonomous agent runtime. Consumes CampusBrain RAG (Project 1) as an external service.",
    version="0.1.0",
)

# Health is mounted at the root, NOT under /api/v1: platform probes and load
# balancers expect a stable, unversioned path. Versioning a liveness check
# means a future /api/v2 silently breaks the deploy platform's health polling.
app.include_router(health_router)

# "*" is fine while the CLI is the only client. A browser client on a different
# origin (deferred to M45) requires the real origin here — Project 1's
# DEPLOYMENT_JOURNAL records what a wildcard in production actually costs.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
