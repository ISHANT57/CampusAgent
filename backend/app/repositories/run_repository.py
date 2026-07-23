"""Persistence for runs and their traces.

Every step is committed BEFORE the next one begins. That is not caution about
crashes so much as a design property: the trace is the working memory the next
prompt is built from, the debugger view, and the eval dataset. If it only
existed in process memory, a crash would lose the run's entire history and
there would be nothing to resume from, inspect, or measure.

tenant_id is applied here, in one place, rather than at each call site. It is
always 1 today. The point is that when multi-tenancy arrives, this file and
resolve_tenant() change and no query does — the mistake Project 1 made by
putting its equivalent constant at the endpoint instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.run import Run, RunStatus
from app.models.step import Step


class RunRepository:
    def __init__(self, db: Session, tenant_id: int | None = None):
        self.db = db
        self.tenant_id = tenant_id if tenant_id is not None else get_settings().default_tenant_id

    # -- runs ---------------------------------------------------------------

    def create(
        self,
        goal: str,
        session_id: str | None = None,
        *,
        mode: str | None = None,
        provider_name: str | None = None,
        model: str | None = None,
        identity: str | None = None,
    ) -> Run:
        # Recording which provider and model served the run makes a trace
        # self-explanatory. Token counts and latency are otherwise
        # inexplicable — a 6-second step means something different on
        # gpt-oss-20b than on flash-lite.
        run = Run(
            tenant_id=self.tenant_id,
            goal=goal,
            session_id=session_id,
            status=RunStatus.CREATED.value,
            mode=mode,
            provider_name=provider_name,
            model=model,
            identity=identity,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get(self, run_id: int) -> Run | None:
        # Tenant-scoped even though tenant_id is always 1 — a query that
        # forgets the filter today is a cross-tenant leak the day it stops
        # being 1, and by then nobody remembers which queries were written
        # before the switch.
        return self.db.scalar(
            select(Run).where(Run.id == run_id, Run.tenant_id == self.tenant_id)
        )

    def start(self, run: Run) -> None:
        run.status = RunStatus.RUNNING.value
        run.started_at = datetime.now(timezone.utc)
        run.heartbeat_at = run.started_at
        self.db.commit()

    def heartbeat(self, run: Run) -> None:
        run.heartbeat_at = datetime.now(timezone.utc)
        self.db.commit()

    def finish(
        self,
        run: Run,
        status: RunStatus,
        answer: str | None = None,
        error: str | None = None,
    ) -> None:
        run.status = status.value
        run.final_answer = answer
        run.error = error
        run.finished_at = datetime.now(timezone.utc)
        self.db.commit()

    def recent(self, limit: int = 20) -> list[Run]:
        return list(
            self.db.scalars(
                select(Run)
                .where(Run.tenant_id == self.tenant_id)
                .order_by(Run.id.desc())
                .limit(limit)
            )
        )

    # -- steps --------------------------------------------------------------

    def add_step(
        self,
        run: Run,
        kind: str,
        *,
        tool_name: str | None = None,
        input: dict | None = None,
        output: dict | None = None,
        error: str | None = None,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> Step:
        """Append one step and commit.

        `idx` comes from the run's own counter rather than a COUNT(*) query:
        the (run_id, idx) unique constraint then turns a double-write into a
        database error instead of a duplicated line in the trace.
        """
        step = Step(
            run_id=run.id,
            tenant_id=self.tenant_id,
            idx=run.step_count,
            kind=kind,
            tool_name=tool_name,
            input=input,
            output=output,
            error=error,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
        self.db.add(step)

        run.step_count += 1
        if prompt_tokens:
            run.prompt_tokens += prompt_tokens
        if completion_tokens:
            run.completion_tokens += completion_tokens
        run.heartbeat_at = datetime.now(timezone.utc)

        self.db.commit()
        return step

    def steps(self, run_id: int) -> list[Step]:
        return list(
            self.db.scalars(
                select(Step)
                .where(Step.run_id == run_id, Step.tenant_id == self.tenant_id)
                .order_by(Step.idx)
            )
        )
