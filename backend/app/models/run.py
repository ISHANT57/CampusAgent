"""The `runs` table — one row per goal the agent is asked to accomplish.

A run is a durable state machine, not a function call (ARCHITECTURE.md A4).
Three forcing functions, any one sufficient on its own:

  1. Human-in-the-loop. A run must pause for approval, and there is no such
     thing as a paused HTTP request.
  2. Duration. 30s-5min exceeds proxy and browser tolerances.
  3. Crash recovery. Render free tier restarts; a run living only in process
     memory is lost with no record of how far it got.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# See app/models/step.py — JSONB on Postgres, JSON elsewhere so tests can run
# against in-memory SQLite.
JSONType = JSON().with_variant(JSONB, "postgresql")

# SQLite auto-increments only INTEGER PRIMARY KEY (it aliases the rowid);
# BIGINT does not get that treatment, so an insert without an explicit id hits
# a NOT NULL violation. Postgres keeps BIGINT, which is what we want for a
# table that accumulates a row per agent step.
PkType = BigInteger().with_variant(Integer, "sqlite")


class RunStatus(str, enum.Enum):
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @classmethod
    def terminal(cls) -> set["RunStatus"]:
        return {cls.COMPLETED, cls.FAILED, cls.REJECTED, cls.CANCELLED, cls.TIMED_OUT}


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(PkType, primary_key=True)

    # Multi-tenancy seam. Nothing reads this yet and DEFAULT_TENANT_ID is
    # always 1 — that is the point. Every table carries it and every repository
    # filters on it from day one, so adding real tenancy later changes
    # resolve_tenant() and nothing else.
    #
    # Project 1 put its equivalent constant at the endpoint instead
    # (api/v1/chat.py: PUBLIC_ORG_ID = 1), which is exactly why it cannot serve
    # a second institution today without touching that endpoint. ~30 lines now
    # versus a migration touching every query later.
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    # Groups related runs so the planner can see prior goals (M37). Nullable:
    # a one-off run has no session.
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    goal: Mapped[str] = mapped_column(Text, nullable=False)

    # Stored as a plain string rather than a Postgres ENUM. Adding a status to
    # a native enum needs ALTER TYPE, which does not run inside a transaction
    # on older Postgres and complicates rollback. A CHECK-free varchar plus the
    # Python enum gives the same safety at the only boundary that matters —
    # application code — with a free migration path.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RunStatus.CREATED.value)

    # The current plan. Versioned rather than overwritten so a replan preserves
    # what it replaced — you cannot debug a bad replan without both.
    plan: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Budget counters (M19). Denormalised onto the run rather than summed from
    # steps on every check: the budget is consulted once per iteration, and an
    # aggregate over a growing table on the hot path is the wrong trade.
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=0)

    # The reaper watches this (M34). A run whose heartbeat has gone stale is
    # dead: the process that owned it is gone. Without this, a crashed run
    # stays `running` forever and nothing can tell it apart from a slow one.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_runs_tenant_status", "tenant_id", "status"),
        Index("ix_runs_tenant_session", "tenant_id", "session_id"),
        # The reaper's query: find running runs with stale heartbeats.
        Index("ix_runs_status_heartbeat", "status", "heartbeat_at"),
    )

    def __repr__(self) -> str:
        return f"<Run {self.id} {self.status} {self.goal[:40]!r}>"
