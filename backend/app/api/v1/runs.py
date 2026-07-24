"""Runs API — start an agent run, watch it think.

Three endpoints:

    POST /runs                 accept a goal, return 202 + run_id immediately
    GET  /runs/{id}            status and full trace
    GET  /runs/{id}/events     Server-Sent Events, resumable

WHY 202 AND NOT A SYNCHRONOUS RESPONSE:
a run takes 5-60 seconds and makes several provider calls. Holding an HTTP
request open for that exceeds proxy and browser tolerances, gives the client
nothing to show meanwhile, and makes a dropped connection lose the work.

WHY SSE AND NOT WEBSOCKET:
the client only listens. SSE is unidirectional, works over plain HTTP, and
reconnects automatically with a Last-Event-ID header. A WebSocket would add a
second protocol for a channel that never needs to carry anything upstream.

WHY RECONNECTION IS TRIVIAL HERE:
the trace is already durable — the loop commits every step before taking the
next one, because the prompt is rebuilt from it. So resuming a dropped stream
is `WHERE idx > n`, not a replay buffer.
"""

# NOTE: no `from __future__ import annotations` here, deliberately.
# FastAPI resolves endpoint parameter types at runtime, and slowapi's
# @limiter.limit wraps the handler — so under postponed evaluation the
# annotations are strings that FastAPI tries to resolve against slowapi's
# module namespace, where CreateRunRequest does not exist. Same failure class
# as the Typer flag-binding bug in cli.py: a framework that introspects
# annotations breaks when they are lazy.

import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import SessionLocal, get_db
from app.core.identity import COOKIE_NAME, MAX_AGE_SECONDS, Identity, resolve_or_issue
from app.core.rate_limit import RUN_CREATE_LIMIT, RUN_READ_LIMIT, limiter
from app.llm.manager import ByokConfig, Mode, NoProviderAvailable, RunContext, resolve
from app.models.run import RunStatus
from app.repositories.run_repository import UNSCOPED, RunRepository

router = APIRouter(prefix="/runs", tags=["runs"])

# How often the SSE generator looks for new steps. The loop writes on its own
# schedule, so this is a poll against the database rather than a subscription.
# 400ms is below the threshold where a trace stops feeling live, and cheap:
# the query is an indexed `WHERE run_id = ? AND idx > ?`.
#
# ponytail: polling. LISTEN/NOTIFY or an in-process queue when a second
# instance exists — at which point the SSE stream must also find the run.
POLL_SECONDS = 0.5
# How often, during a stretch with no new steps, to send an SSE comment line so
# the connection is not dropped as idle. Well under the ~30-60s idle timeout of
# a typical edge proxy — Render included — and a slow provider call routinely
# produces no steps for 10-15s, which is when the drop happened.
KEEPALIVE_SECONDS = 12.0
# Stop streaming a run that has produced nothing for this long. The reaper
# (deferred) is the real fix; this stops one abandoned browser tab from
# holding a connection open forever.
STREAM_IDLE_TIMEOUT = 180.0


# --- request / response shapes ---------------------------------------------

class ProviderConfig(BaseModel):
    """BYOK, supplied per request.

    The key is used for this run and not persisted — it is never written to
    `runs`, `steps`, or logs. A session store belongs with the UI work; until
    then a client sends it each time, which is honest about where it lives.
    """

    provider: str
    api_key: str = ""
    model: str | None = None
    base_url: str | None = None


class CreateRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    # Absent = trial mode on the hosted key.
    byok: ProviderConfig | None = None


class CreateRunResponse(BaseModel):
    run_id: int
    status: str
    mode: str
    provider: str
    model: str


class StepView(BaseModel):
    idx: int
    kind: str
    tool_name: str | None = None
    output: dict | None = None
    error: str | None = None


class RunSummary(BaseModel):
    """One row in run history. No steps — a list of 20 runs with full traces
    would be megabytes, and the list view shows none of it."""

    run_id: int
    status: str
    goal: str
    mode: str | None = None
    provider: str | None = None
    model: str | None = None
    step_count: int = 0
    total_tokens: int = 0
    elapsed_seconds: float | None = None
    created_at: str | None = None


class RunListResponse(BaseModel):
    runs: list[RunSummary]
    total: int


class RunView(BaseModel):
    run_id: int
    status: str
    goal: str
    mode: str | None = None
    provider: str | None = None
    model: str | None = None
    answer: str | None = None
    error: str | None = None
    # F2: shown in the run header. Already recorded on `runs`; previously just
    # not returned.
    step_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_seconds: float | None = None
    created_at: str | None = None
    steps: list[StepView]


# --- identity ---------------------------------------------------------------

def current_identity(request: Request, response: Response) -> Identity:
    """Resolve the browser's identity, minting one if absent or tampered.

    Set as a dependency rather than middleware so it is visible at every
    endpoint that uses it — an implicit identity is one nobody remembers is
    being counted against.
    """
    identity, is_new = resolve_or_issue(request.cookies.get(COOKIE_NAME))
    if is_new:
        # SameSite=None is REQUIRED for a cross-site deploy.
        #
        # The frontend is on vercel.app and the API on onrender.com — different
        # registrable domains, so every request from the browser is CROSS-SITE.
        # A Lax cookie is only sent on top-level navigation, never on a
        # cross-site fetch or EventSource. With Lax, POST /runs created a run
        # under one identity and the follow-up stream arrived with NO cookie,
        # got a fresh identity, and 404'd on a run that was not "theirs".
        #
        # SameSite=None is only honoured with Secure, and Secure needs HTTPS —
        # which is also why the app must see the real scheme behind Render's
        # TLS terminator (uvicorn --proxy-headers, see the Dockerfile). Without
        # that, this computes secure=False, the browser rejects the cookie
        # outright, and the symptom is identical.
        #
        # Locally (localhost:5173 -> localhost:8000) it is same-site and plain
        # HTTP, so Lax is correct there and None would be rejected.
        cross_site = request.url.scheme == "https"
        response.set_cookie(
            COOKIE_NAME,
            identity.token,
            max_age=MAX_AGE_SECONDS,
            httponly=True,     # not readable by page scripts
            samesite="none" if cross_site else "lax",
            secure=cross_site,
        )
    return identity


# --- endpoints --------------------------------------------------------------

@router.post("", response_model=CreateRunResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(RUN_CREATE_LIMIT)
def create_run(
    # `request` is required by slowapi's decorator, which reads it positionally
    # — removing it breaks the limiter at import time, not at request time.
    request: Request,
    payload: CreateRunRequest,
    background: BackgroundTasks,
    response: Response,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    """Accept a goal and start working on it.

    Provider resolution happens HERE, synchronously, before 202 is returned.
    A refusal — out of trial runs, invalid key, unsafe base URL — is a 4xx the
    client can act on, not a run that fails silently in the background thirty
    seconds later. It is also not recorded as a failed run, because the agent
    never attempted anything.
    """
    context = RunContext(
        mode=Mode.BYOK if payload.byok else Mode.TRIAL,
        identity=identity.key,
        byok=ByokConfig(**payload.byok.model_dump()) if payload.byok else None,
    )

    try:
        resolved = resolve(context)
    except NoProviderAvailable as e:
        # `reason` lets the UI tell "you are out of trial runs" (a conversion
        # moment) from "your key is invalid" (a fix-it moment). They deserve
        # very different screens.
        code = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if e.reason == "trial_exhausted"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail={"message": str(e), "reason": e.reason})

    run = RunRepository(db, identity=UNSCOPED).create(
        payload.goal,
        session_id=payload.session_id,
        mode=resolved.mode.value,
        provider_name=resolved.provider_name,
        model=resolved.model,
        identity=identity.key,
    )
    run_id = run.id

    # Runs after the response is sent, in this process — no queue, no worker.
    # Render's free tier has no worker dyno, and the run's state is durable
    # regardless, so a restart loses the process but not the trace.
    background.add_task(_execute_in_background, run_id, resolved)

    return CreateRunResponse(
        run_id=run_id,
        status=RunStatus.CREATED.value,
        mode=resolved.mode.value,
        provider=resolved.provider_name,
        model=resolved.model,
    )


def _execute_in_background(run_id: int, resolved) -> None:
    """Own a fresh session: the request's session is closed by the time this
    runs, and sharing one across threads is a SQLAlchemy footgun."""
    from app.agent.loop import execute_run
    from app.tools import registry

    db = SessionLocal()
    try:
        run = RunRepository(db, identity=UNSCOPED).get(run_id)
        if run is None:
            return
        execute_run(db, run, resolved.provider, registry, resolved.budget, label=resolved.label)
    except Exception as e:  # noqa: BLE001
        # execute_run does not raise for agent-level problems, so reaching here
        # means something structural. The run must not be left `running`
        # forever — a status nobody can distinguish from "still thinking".
        db.rollback()
        run = RunRepository(db, identity=UNSCOPED).get(run_id)
        if run and run.status not in {s.value for s in RunStatus.terminal()}:
            RunRepository(db, identity=UNSCOPED).finish(
                run, RunStatus.FAILED, error=f"Run crashed: {type(e).__name__}: {e}"
            )
    finally:
        db.close()


def _owned_or_404(run):
    """Turn "not yours or not there" into 404.

    Ownership itself is enforced by the REPOSITORY: RunRepository(identity=...)
    filters every read, so get() already returns None for another caller's run.
    This is now only a null check.

    That is the point of F0. Previously ownership was a guard the endpoint had
    to remember, and the next endpoint could forget it — which is exactly how
    the original IDOR happened. Now forgetting is a TypeError at import,
    because `identity` is a required argument.

    404, not 403: a 403 confirms the run exists and hands an enumerator a map
    of valid ids.
    """
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.get("/{run_id}", response_model=RunView)
@limiter.limit(RUN_READ_LIMIT)
def get_run(
    request: Request,
    run_id: int,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    repo = RunRepository(db, identity=identity.key)
    run = _owned_or_404(repo.get(run_id))

    return RunView(
        run_id=run.id, status=run.status, goal=run.goal, mode=run.mode,
        provider=run.provider_name, model=run.model,
        answer=run.final_answer, error=run.error,
        step_count=run.step_count,
        prompt_tokens=run.prompt_tokens, completion_tokens=run.completion_tokens,
        elapsed_seconds=_elapsed(run),
        created_at=run.created_at.isoformat() if run.created_at else None,
        steps=[
            StepView(idx=s.idx, kind=s.kind, tool_name=s.tool_name, output=s.output, error=s.error)
            for s in repo.steps(run_id)
        ],
    )


@router.get("", response_model=RunListResponse)
@limiter.limit(RUN_READ_LIMIT)
def list_runs(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    """This caller's runs, newest first.

    Scoped by the repository, not by a filter written here — a list endpoint
    that forgets ownership leaks EVERY run, which is a worse version of the
    IDOR already found on the read endpoints. Passing `identity` is required,
    so forgetting is impossible rather than merely unlikely.

    Cookie-keyed: clearing browser data loses this history, and there is no
    account to recover it from. The UI must say so rather than let someone
    discover it.
    """
    limit = max(1, min(limit, 100))
    repo = RunRepository(db, identity=identity.key)
    return RunListResponse(
        runs=[
            RunSummary(
                run_id=r.id, status=r.status, goal=r.goal, mode=r.mode,
                provider=r.provider_name, model=r.model,
                step_count=r.step_count,
                total_tokens=r.prompt_tokens + r.completion_tokens,
                elapsed_seconds=_elapsed(r),
                created_at=r.created_at.isoformat() if r.created_at else None,
            )
            for r in repo.recent(limit=limit, offset=max(0, offset))
        ],
        total=repo.count(),
    )


@router.post("/{run_id}/cancel", response_model=RunView)
@limiter.limit(RUN_READ_LIMIT)
def cancel_run(
    request: Request,
    run_id: int,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    """Stop a running agent.

    Cooperative, not forceful: the loop checks the run's status at the top of
    each iteration and stops there. Killing a thread mid-provider-call would
    leave the trace inconsistent with what was actually spent, and Python
    cannot kill a thread anyway.

    So an in-flight tool call still completes — cancellation takes effect
    within one step, not instantly. Without this a run can burn its whole
    budget with no way to stop it.
    """
    repo = RunRepository(db, identity=identity.key)
    run = _owned_or_404(repo.get(run_id))

    if run.status in {s.value for s in RunStatus.terminal()}:
        # Already finished. Not an error — the client may simply have raced the
        # final step.
        return get_run(request=request, run_id=run_id, identity=identity, db=db)

    repo.finish(run, RunStatus.CANCELLED, error="Cancelled by the user.")
    return get_run(request=request, run_id=run_id, identity=identity, db=db)


def _elapsed(run) -> float | None:
    if not run.started_at:
        return None
    end = run.finished_at or datetime.now(timezone.utc)
    return round((end - run.started_at).total_seconds(), 1)


@router.get("/{run_id}/events")
async def stream_run(
    run_id: int,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    """Stream the trace as it is written.

    `Last-Event-ID` is the browser's automatic reconnect header, and it is
    handled by starting the query above that index. No replay buffer, no
    server-side stream state — the durable trace IS the buffer.
    """
    try:
        after = int(last_event_id) if last_event_id else -1
    except ValueError:
        after = -1

    # Authorise BEFORE the generator, and raise rather than yield: once a
    # StreamingResponse has started, the status code is already sent and a
    # refusal can only be an in-band message with HTTP 200. A denied stream
    # must be a 404 on the wire.
    #
    # The generator opens its own session (the request's is closed by the time
    # it runs), so only the identity KEY is closed over — a string, not a
    # request-scoped object.
    _owned_or_404(RunRepository(db, identity=identity.key).get(run_id))
    owner_key = identity.key
    terminal = {s.value for s in RunStatus.terminal()}

    def poll(after_idx: int) -> dict:
        """One poll's database work — deliberately synchronous and run OFF the
        event loop (via run_in_threadpool below).

        Two problems this shape fixes, both of which produced "connection lost"
        across devices on the single free-tier worker:

        1. Doing blocking SQLAlchemy directly in the async generator ran a
           ~100-700ms Neon query on the ONE event loop every poll, for every
           open stream — starving every other connection, including new
           EventSource handshakes.
        2. The old generator held one pooled connection for the stream's whole
           life (up to 3 minutes). A handful of open tabs exhausted the pool.
           A fresh session per poll, closed immediately, holds a connection for
           milliseconds instead.

        Summaries are built here, inside the session, so no attribute is read
        lazily after it closes.
        """
        session = SessionLocal()
        try:
            repo = RunRepository(session, identity=owner_key)
            run = repo.get(run_id)
            if run is None:
                return {"gone": True}
            return {
                "meta": {"run_id": run.id, "goal": run.goal, "status": run.status,
                         "provider": run.provider_name, "model": run.model},
                "steps": [_summarise_step(s) for s in repo.steps(run_id) if s.idx > after_idx],
                "done": run.status if run.status in terminal else None,
                "answer": run.final_answer,
                "error": run.error,
            }
        finally:
            session.close()

    async def events():
        cursor = after
        sent_run = False
        last_send = time.monotonic()
        idle = 0.0

        while True:
            state = await run_in_threadpool(poll, cursor)
            if state.get("gone"):
                yield _sse("error", {"message": "Run not found"})
                return

            if not sent_run:
                sent_run = True
                yield _sse("run", state["meta"])

            for step in state["steps"]:
                cursor = step["idx"]
                idle = 0.0
                last_send = time.monotonic()
                yield _sse("step", step, event_id=step["idx"])

            if state["done"]:
                yield _sse("done", {"status": state["done"], "answer": state["answer"],
                                    "error": state["error"]})
                return

            if not state["steps"]:
                idle += POLL_SECONDS
                if idle >= STREAM_IDLE_TIMEOUT:
                    yield _sse("error", {"message": "Stream timed out waiting for progress."})
                    return
                # THE cross-device fix. A slow provider call produces no steps
                # for 10s+ (the screenshot's "thinking… 9.8s"), and Render's
                # edge proxy drops a connection that has sent nothing. An SSE
                # comment line is invisible to the app but keeps the connection
                # demonstrably alive — the standard cure for exactly this.
                if time.monotonic() - last_send >= KEEPALIVE_SECONDS:
                    last_send = time.monotonic()
                    yield ": keepalive\n\n"

            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nginx and several proxies buffer responses by default, which
            # would hold every event until the stream closed — the exact
            # opposite of what SSE is for.
            "X-Accel-Buffering": "no",
        },
    )


# Cap on the observation text carried in a stream event.
MAX_STREAM_OBSERVATION_CHARS = 600


def _summarise_step(step) -> dict:
    """What the stream carries for one step.

    NOT the raw row. A single knowledge_search observation is several kilobytes
    — every retrieved passage, plus a `rendered` copy of the same text for the
    prompt. Streaming that pushes kilobytes per step to a browser that only
    needs to draw one line, and duplicates what GET /runs/{id} already serves
    in full.

    So the stream is a live SUMMARY and the run endpoint is the record. The
    trace stays complete in the database either way.
    """
    view = {"idx": step.idx, "kind": step.kind, "tool": step.tool_name, "error": step.error}
    output = step.output or {}

    if step.kind == "observation":
        view["ok"] = output.get("ok")
        view["unavailable"] = output.get("unavailable")
        meta = output.get("meta") or {}
        view["count"] = meta.get("count")
        view["latency_ms"] = meta.get("latency_ms")
        preview = meta.get("rendered") or output.get("data")
        if preview is not None:
            text = str(preview)
            view["preview"] = text[:MAX_STREAM_OBSERVATION_CHARS]
            view["truncated"] = len(text) > MAX_STREAM_OBSERVATION_CHARS
    elif step.kind == "tool_call":
        # Arguments are small and are the interesting part of this step.
        view["arguments"] = step.input
    else:
        # thought / final / plan / error — short by nature.
        view["output"] = output

    return view


def _sse(event: str, data: dict, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, default=str)}")
    return "\n".join(lines) + "\n\n"
