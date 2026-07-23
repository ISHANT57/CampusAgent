"""Mark abandoned runs as failed.

Runs execute in-process via BackgroundTasks — Render's free tier has no worker
dyno. That is fine for the work, but it means a restart or crash leaves the run
row in `running` forever, and `running` is indistinguishable from "still
thinking". A user watching the trace waits for a step that will never arrive.

The loop writes `heartbeat_at` on every step, so a run whose heartbeat has gone
stale is dead by definition: the process that owned it is gone.

Reaped on startup, which is exactly when the interesting case has happened —
the process that died is the one being replaced.

ponytail: startup-only. A periodic sweep matters when a run can be abandoned
WITHOUT a restart (a hung provider call past its timeout). The per-tool and
per-run timeouts already bound that, so the restart case is the real one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.run import Run, RunStatus

logger = logging.getLogger(__name__)

# Generous: longer than the slowest legitimate step. Project 1 cold-starts on
# Render free and can take ~50s to answer, and a run may sit in one tool call
# that long. Reaping a live run is worse than reaping a dead one late.
STALE_AFTER_SECONDS = 15 * 60


def reap_stale_runs(db: Session, stale_after: int = STALE_AFTER_SECONDS) -> int:
    """Finish any run whose heartbeat has gone stale. Returns how many."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after)
    active = {RunStatus.CREATED.value, RunStatus.PLANNING.value, RunStatus.RUNNING.value}

    stale = db.scalars(
        select(Run).where(
            Run.status.in_(active),
            # A run created but never started has no heartbeat. It is still
            # abandoned — created_at serves as the clock.
            ((Run.heartbeat_at.is_(None)) & (Run.created_at < cutoff))
            | (Run.heartbeat_at < cutoff)
        )
    ).all()

    for run in stale:
        run.status = RunStatus.FAILED.value
        run.finished_at = datetime.now(timezone.utc)
        run.error = (
            "This run was abandoned — the process running it stopped, most likely "
            "a restart or a deploy. Its trace up to that point is preserved."
        )
        logger.warning("reaped abandoned run %s (last heartbeat %s)", run.id, run.heartbeat_at)

    if stale:
        db.commit()
    return len(stale)
