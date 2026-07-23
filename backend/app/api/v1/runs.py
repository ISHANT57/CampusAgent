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

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.identity import COOKIE_NAME, MAX_AGE_SECONDS, Identity, hash_ip, resolve_or_issue
from app.llm.manager import ByokConfig, Mode, NoProviderAvailable, RunContext, resolve
from app.models.run import RunStatus
from app.repositories.run_repository import RunRepository

router = APIRouter(prefix="/runs", tags=["runs"])

# How often the SSE generator looks for new steps. The loop writes on its own
# schedule, so this is a poll against the database rather than a subscription.
# 400ms is below the threshold where a trace stops feeling live, and cheap:
# the query is an indexed `WHERE run_id = ? AND idx > ?`.
#
# ponytail: polling. LISTEN/NOTIFY or an in-process queue when a second
# instance exists — at which point the SSE stream must also find the run.
POLL_SECONDS = 0.4
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


class RunView(BaseModel):
    run_id: int
    status: str
    goal: str
    mode: str | None = None
    provider: str | None = None
    model: str | None = None
    answer: str | None = None
    error: str | None = None
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
        response.set_cookie(
            COOKIE_NAME,
            identity.token,
            max_age=MAX_AGE_SECONDS,
            httponly=True,     # not readable by page scripts
            samesite="lax",
            secure=request.url.scheme == "https",
        )
    return identity


# --- endpoints --------------------------------------------------------------

@router.post("", response_model=CreateRunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: CreateRunRequest,
    background: BackgroundTasks,
    request: Request,
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

    run = RunRepository(db).create(
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
        run = RunRepository(db).get(run_id)
        if run is None:
            return
        execute_run(db, run, resolved.provider, registry, resolved.budget, label=resolved.label)
    except Exception as e:  # noqa: BLE001
        # execute_run does not raise for agent-level problems, so reaching here
        # means something structural. The run must not be left `running`
        # forever — a status nobody can distinguish from "still thinking".
        db.rollback()
        run = RunRepository(db).get(run_id)
        if run and run.status not in {s.value for s in RunStatus.terminal()}:
            RunRepository(db).finish(
                run, RunStatus.FAILED, error=f"Run crashed: {type(e).__name__}: {e}"
            )
    finally:
        db.close()


def _owned_or_404(run, identity_key: str):
    """Ownership check for the read endpoints.

    run_id is a sequential integer, so without this anyone can walk 1..N and
    read every other user's goals, answers, and retrieved document text. Goals
    are personal ("My CGPA is 6.2, do I qualify?"), so this is a real
    disclosure, not a theoretical one.

    404, not 403: a 403 confirms the run exists, which hands an enumerator a
    map of valid ids. An unauthorised read should be indistinguishable from a
    missing one.

    Runs with NO recorded identity are also refused — fail closed. They predate
    the API (created via the CLI, which reads the database directly and is
    unaffected), and "no owner recorded" cannot be proven to mean "belongs to
    this caller".
    """
    if run is None or not run.identity or run.identity != identity_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.get("/{run_id}", response_model=RunView)
def get_run(
    run_id: int,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    repo = RunRepository(db)
    run = _owned_or_404(repo.get(run_id), identity.key)

    return RunView(
        run_id=run.id, status=run.status, goal=run.goal, mode=run.mode,
        provider=run.provider_name, model=run.model,
        answer=run.final_answer, error=run.error,
        steps=[
            StepView(idx=s.idx, kind=s.kind, tool_name=s.tool_name, output=s.output, error=s.error)
            for s in repo.steps(run_id)
        ],
    )


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
    _owned_or_404(RunRepository(db).get(run_id), identity.key)
    owner_key = identity.key

    async def events():
        db = SessionLocal()
        try:
            repo = RunRepository(db)
            run = repo.get(run_id)
            # Re-checked inside: between authorisation and the first read the
            # run could in principle have been reassigned. Cheap, and it means
            # the invariant holds at the point of disclosure, not just at the
            # point of entry.
            if run is None or run.identity != owner_key:
                yield _sse("error", {"message": "Run not found"})
                return

            yield _sse("run", {"run_id": run.id, "goal": run.goal, "status": run.status,
                               "provider": run.provider_name, "model": run.model})

            cursor = after
            idle = 0.0
            while True:
                db.expire_all()   # the background thread committed; re-read
                steps = [s for s in repo.steps(run_id) if s.idx > cursor]

                for step in steps:
                    cursor = step.idx
                    idle = 0.0
                    yield _sse("step", _summarise_step(step), event_id=step.idx)

                run = repo.get(run_id)
                if run and run.status in {s.value for s in RunStatus.terminal()}:
                    yield _sse("done", {"status": run.status, "answer": run.final_answer,
                                        "error": run.error})
                    return

                if not steps:
                    idle += POLL_SECONDS
                    if idle >= STREAM_IDLE_TIMEOUT:
                        yield _sse("error", {"message": "Stream timed out waiting for progress."})
                        return
                await asyncio.sleep(POLL_SECONDS)
        finally:
            db.close()

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
