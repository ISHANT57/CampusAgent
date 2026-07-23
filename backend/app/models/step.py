"""The `steps` table — append-only, one row per thing the agent did.

THIS IS THE CORE TABLE. One table, five jobs:

  1. Working memory  — the prompt is assembled from it every iteration
  2. Event stream    — SSE replays it via Last-Event-ID (M33)
  3. Audit log       — why did the agent do that
  4. Debugger        — what the CLI and UI render
  5. Eval dataset    — trajectory metrics come from it, with NO separate
                       instrumentation anywhere in the codebase

Append-only, and never updated. A step records what happened at a point in
time; rewriting one would corrupt jobs 3 and 5 to save a row in job 1.

Why one table with a `kind` discriminator rather than separate `thoughts`,
`tool_calls`, `observations` tables: reading a run's trace in order is the hot
path, executed once per iteration. Split tables make that a three-way UNION
with an ORDER BY across all of them. One table with a (run_id, idx) unique
constraint is both the smaller schema and the faster read.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# JSONB on Postgres (stored parsed, indexable — so evaluation queries over the
# trace stay possible), plain JSON everywhere else. The variant exists so the
# test suite can run against in-memory SQLite: JSONB is Postgres-only and would
# fail to compile, which would mean either no loop tests or a required database
# for unit tests. Neither is acceptable.
JSONType = JSON().with_variant(JSONB, "postgresql")

# SQLite auto-increments only INTEGER PRIMARY KEY; BIGINT does not get rowid
# aliasing, so an insert without an explicit id fails NOT NULL. Postgres keeps
# BIGINT — this table gets a row per agent step and will be the largest one.
PkType = BigInteger().with_variant(Integer, "sqlite")


class StepKind(str, enum.Enum):
    PLAN = "plan"                # planner output, incl. every replan
    THOUGHT = "thought"          # why this action
    TOOL_CALL = "tool_call"      # what was invoked, with which args
    OBSERVATION = "observation"  # what came back — UNTRUSTED DATA
    REFLECTION = "reflection"    # critique after a trigger fired
    FINAL = "final"              # the answer
    ERROR = "error"              # unrecoverable failure


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(PkType, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        PkType, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    # Position within the run. Also the SSE event id — which is why it is a
    # dense integer per run rather than a global sequence: `Last-Event-ID: 7`
    # must mean "the 7th step of THIS run", so reconnection is a
    # `WHERE run_id = ? AND idx > 7` query and nothing more.
    idx: Mapped[int] = mapped_column(Integer, nullable=False)

    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # JSONB, not JSON: JSONB is stored parsed and is indexable, so evaluation
    # queries ("every run that called knowledge_search with an empty query")
    # stay possible without a schema change.
    input: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-step LLM accounting. On the step rather than only the run, because
    # "which step burned the tokens" is the first question when a run blows
    # its budget, and a run-level total cannot answer it.
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Guarantees trace ordering is total and gap-free per run, and makes a
        # double-write of the same step a database error rather than a
        # duplicated line in the UI. Matters on resume (M35), where a step may
        # be re-attempted after a crash.
        UniqueConstraint("run_id", "idx", name="uq_steps_run_idx"),
        Index("ix_steps_run_idx", "run_id", "idx"),
        Index("ix_steps_tenant_kind", "tenant_id", "kind"),
    )

    def __repr__(self) -> str:
        return f"<Step run={self.run_id} idx={self.idx} {self.kind} {self.tool_name or ''}>"
