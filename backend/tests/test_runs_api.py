"""P2 tests — identity, the runs API, and SSE replay.

No network: the provider is stubbed at resolution, so these exercise the HTTP
contract rather than the agent.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.core import identity as ident
from app.core.identity import COOKIE_NAME, InvalidIdentity, issue, resolve_or_issue, verify
from app.main import app


# --- identity ---------------------------------------------------------------

def test_issued_token_verifies():
    i = issue()
    assert verify(i.token).subject == i.subject


def test_quota_key_is_a_hash_not_the_token():
    # The key lands in the database and in logs. A value replayable as a
    # credential does not belong in either.
    i = issue()
    assert i.subject not in i.key
    assert i.token not in i.key
    assert len(i.key) == 32


@pytest.mark.parametrize("bad", [
    None, "", "garbage", "a.b", "a.b.c.d",
    "subject.notanumber.sig",
])
def test_malformed_tokens_are_rejected(bad):
    with pytest.raises(InvalidIdentity):
        verify(bad)


def test_a_forged_signature_is_rejected():
    i = issue()
    subject, issued, _ = i.token.split(".")
    with pytest.raises(InvalidIdentity, match="signature"):
        verify(f"{subject}.{issued}.{'0' * 32}")


def test_tampering_with_the_issue_date_is_rejected():
    # Otherwise an old token could be back-dated to dodge expiry, or
    # forward-dated to look fresh.
    i = issue()
    subject, _, sig = i.token.split(".")
    with pytest.raises(InvalidIdentity, match="signature"):
        verify(f"{subject}.{int(time.time()) + 999}.{sig}")


def test_expired_token_is_rejected(monkeypatch):
    i = issue()
    monkeypatch.setattr(ident, "MAX_AGE_SECONDS", -1)
    with pytest.raises(InvalidIdentity, match="expired"):
        verify(i.token)


def test_a_bad_token_yields_a_new_identity_rather_than_an_error():
    # This is not authentication. Refusing service because a cookie was mangled
    # punishes an ordinary user for a browser quirk, and the attacker it would
    # inconvenience can simply clear the cookie.
    got, is_new = resolve_or_issue("tampered.999.badsig")
    assert is_new and verify(got.token)


def test_ip_hash_is_salted_and_not_reversible():
    h = ident.hash_ip("203.0.113.5")
    assert h and "203.0.113" not in h
    assert ident.hash_ip(None) is None


# --- API --------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Every test shares one client, so counters would leak between them and
    later tests would fail for a reason unrelated to what they assert.
    Rate limiting itself is covered explicitly below."""
    from app.core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


def test_creating_runs_is_rate_limited(client, fake_resolution):
    """POST /runs is unauthenticated and starts LLM work.

    Even in BYOK mode, where the caller pays for inference, a script can fill
    the database, exhaust the connection pool, and monopolise the single
    instance.
    """
    from app.core.rate_limit import RUN_CREATE_LIMIT

    allowed = int(RUN_CREATE_LIMIT.split("/")[0])
    codes = [
        client.post("/api/v1/runs", json={"goal": f"run {i}"}).status_code
        for i in range(allowed + 2)
    ]
    assert codes[:allowed] == [202] * allowed
    assert 429 in codes[allowed:]


def test_rate_limit_is_keyed_per_browser_not_per_ip(client, fake_resolution):
    """Campus NAT: an IP-keyed limit would treat the whole university as one
    caller — the exact failure Project 1 documented."""
    from app.core.rate_limit import rate_limit_key
    from app.core.identity import COOKIE_NAME, issue

    class FakeRequest:
        def __init__(self, cookies):
            self.cookies = cookies

    a, b = issue(), issue()
    assert rate_limit_key(FakeRequest({COOKIE_NAME: a.token})).startswith("id:")
    assert rate_limit_key(FakeRequest({COOKIE_NAME: a.token})) != rate_limit_key(
        FakeRequest({COOKIE_NAME: b.token})
    )


def test_a_forged_cookie_falls_back_to_ip_rather_than_escaping_limits(monkeypatch):
    # An attacker sending garbage must not be able to opt OUT of rate limiting.
    from app.core.rate_limit import rate_limit_key
    from app.core.identity import COOKIE_NAME

    monkeypatch.setattr("app.core.rate_limit.get_remote_address", lambda r: "1.2.3.4")

    class FakeRequest:
        cookies = {COOKIE_NAME: "forged.123.badsignature"}

    assert rate_limit_key(FakeRequest()) == "ip:1.2.3.4"


@pytest.fixture
def fake_resolution(monkeypatch):
    """Stub provider resolution so the API can be tested without a model."""
    from app.core.budget import RunBudget
    from app.llm.manager import Mode, ResolvedProvider

    class FakeProvider:
        name, model = "fake", "fake-1"

        def complete(self, messages, tools=None, temperature=0.0):
            raise AssertionError("the background task should not run in these tests")

    resolved = ResolvedProvider(
        provider=FakeProvider(), mode=Mode.TRIAL,
        budget=RunBudget(max_steps=4, max_wall_clock_seconds=10),
        label="Trial · Fake", provider_name="fake", model="fake-1",
    )
    monkeypatch.setattr("app.api.v1.runs.resolve", lambda ctx, **kw: resolved)
    # Neutralise the background task: these tests are about the HTTP contract.
    monkeypatch.setattr("app.api.v1.runs._execute_in_background", lambda *a, **k: None)
    return resolved


def test_post_run_returns_202_with_an_id(client, fake_resolution):
    r = client.post("/api/v1/runs", json={"goal": "What is 2 plus 2?"})
    assert r.status_code == 202
    body = r.json()
    assert body["run_id"] > 0
    assert body["mode"] == "trial"
    # The client learns which provider served it without a second request.
    assert body["provider"] == "fake" and body["model"] == "fake-1"


def test_identity_cookie_is_set_on_first_contact_and_reused(client, fake_resolution):
    first = client.post("/api/v1/runs", json={"goal": "one"})
    assert COOKIE_NAME in first.cookies

    token = first.cookies[COOKIE_NAME]
    second = client.post("/api/v1/runs", json={"goal": "two"})
    # Same browser, same identity — otherwise trial quota counts nothing.
    assert COOKIE_NAME not in second.cookies or second.cookies[COOKIE_NAME] == token


def test_identity_cookie_is_httponly(client, fake_resolution):
    r = client.post("/api/v1/runs", json={"goal": "x"})
    assert "httponly" in r.headers["set-cookie"].lower()


def test_trial_exhaustion_is_429_with_a_machine_readable_reason(client, monkeypatch):
    from app.llm.manager import NoProviderAvailable

    def refuse(ctx, **kw):
        raise NoProviderAvailable("You've used both trial runs for today.",
                                  reason="trial_exhausted")

    monkeypatch.setattr("app.api.v1.runs.resolve", refuse)
    r = client.post("/api/v1/runs", json={"goal": "x"})
    assert r.status_code == 429
    # The reason lets the UI show a conversion screen rather than an error.
    assert r.json()["detail"]["reason"] == "trial_exhausted"


def test_a_bad_byok_key_is_400_not_500(client, monkeypatch):
    from app.llm.manager import NoProviderAvailable

    def refuse(ctx, **kw):
        raise NoProviderAvailable("Groq requires an API key.", reason="missing_key")

    monkeypatch.setattr("app.api.v1.runs.resolve", refuse)
    r = client.post("/api/v1/runs", json={"goal": "x", "byok": {"provider": "groq"}})
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "missing_key"


def test_refusals_do_not_create_a_run(client, monkeypatch):
    """A refusal is not a failed run.

    Recording one would corrupt the success-rate metric with attempts the agent
    never made.
    """
    from app.core.database import SessionLocal
    from app.llm.manager import NoProviderAvailable
    from app.repositories.run_repository import UNSCOPED, RunRepository

    db = SessionLocal()
    try:
        before = len(RunRepository(db, identity=UNSCOPED).recent(200))
    finally:
        db.close()

    monkeypatch.setattr(
        "app.api.v1.runs.resolve",
        lambda ctx, **kw: (_ for _ in ()).throw(
            NoProviderAvailable("nope", reason="trial_exhausted")
        ),
    )
    client.post("/api/v1/runs", json={"goal": "should not persist"})

    db = SessionLocal()
    try:
        assert len(RunRepository(db, identity=UNSCOPED).recent(200)) == before
    finally:
        db.close()


def test_goal_is_bounded(client, fake_resolution):
    assert client.post("/api/v1/runs", json={"goal": ""}).status_code == 422
    assert client.post("/api/v1/runs", json={"goal": "x" * 5000}).status_code == 422


def test_get_run_404s_for_an_unknown_id(client):
    assert client.get("/api/v1/runs/99999999").status_code == 404


# --- authorisation (IDOR) ---------------------------------------------------

def test_another_browser_cannot_read_your_run(client, fake_resolution):
    """run_id is a sequential integer.

    Without an ownership check, walking 1..N exposes every user's goals,
    answers, and retrieved document text — and goals are personal ("My CGPA is
    6.2, do I qualify?"). A real disclosure, not a theoretical one.
    """
    run_id = client.post("/api/v1/runs", json={"goal": "my private question"}).json()["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}").status_code == 200

    other = TestClient(app)          # a different browser, a different cookie
    r = other.get(f"/api/v1/runs/{run_id}")
    # 404, not 403: a 403 confirms the run exists and hands an enumerator a map
    # of valid ids.
    assert r.status_code == 404
    assert "private question" not in r.text


def test_another_browser_cannot_stream_your_trace(client, fake_resolution):
    run_id = client.post("/api/v1/runs", json={"goal": "streamed secret"}).json()["run_id"]

    other = TestClient(app)
    r = other.get(f"/api/v1/runs/{run_id}/events")
    # Refused on the wire, not as an in-band SSE message: once a
    # StreamingResponse starts, the status is already 200.
    assert r.status_code == 404
    assert "streamed secret" not in r.text


def test_a_run_with_no_recorded_owner_is_refused(client, fake_resolution):
    """Fail closed.

    Runs predating the API (created via the CLI) have no identity. "No owner
    recorded" cannot be proven to mean "belongs to this caller". The CLI reads
    the database directly and is unaffected.
    """
    from app.core.database import SessionLocal
    from app.repositories.run_repository import UNSCOPED, RunRepository

    db = SessionLocal()
    try:
        legacy = RunRepository(db, identity=UNSCOPED).create("a legacy run")   # identity=None
        legacy_id = legacy.id
    finally:
        db.close()

    assert client.get(f"/api/v1/runs/{legacy_id}").status_code == 404
    assert client.get(f"/api/v1/runs/{legacy_id}/events").status_code == 404


def test_get_run_returns_the_trace(client, fake_resolution):
    run_id = client.post("/api/v1/runs", json={"goal": "trace me"}).json()["run_id"]
    body = client.get(f"/api/v1/runs/{run_id}").json()
    assert body["run_id"] == run_id
    assert body["goal"] == "trace me"
    assert isinstance(body["steps"], list)


# --- SSE --------------------------------------------------------------------

def test_sse_streams_and_terminates_on_a_finished_run(client, fake_resolution):
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.models.step import StepKind
    from app.repositories.run_repository import UNSCOPED, RunRepository

    run_id = client.post("/api/v1/runs", json={"goal": "streamed"}).json()["run_id"]

    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        run = repo.get(run_id)
        repo.add_step(run, StepKind.THOUGHT.value, output={"text": "thinking"})
        repo.add_step(run, StepKind.FINAL.value, output={"answer": "done"})
        repo.finish(run, RunStatus.COMPLETED, answer="done")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = "".join(r.iter_text())

    assert "event: run" in body
    assert "event: step" in body
    assert "event: done" in body
    assert "id: 0" in body            # event ids are step indexes


def test_sse_resumes_from_last_event_id(client, fake_resolution):
    """Reconnection is a `WHERE idx > n` query.

    No replay buffer and no server-side stream state — the durable trace IS the
    buffer, which is only true because the loop commits every step.
    """
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.models.step import StepKind
    from app.repositories.run_repository import UNSCOPED, RunRepository

    run_id = client.post("/api/v1/runs", json={"goal": "resumed"}).json()["run_id"]

    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        run = repo.get(run_id)
        for i in range(4):
            repo.add_step(run, StepKind.THOUGHT.value, output={"text": f"step {i}"})
        repo.finish(run, RunStatus.COMPLETED, answer="ok")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events",
                       headers={"Last-Event-ID": "1"}) as r:
        body = "".join(r.iter_text())

    assert "step 0" not in body and "step 1" not in body     # already delivered
    assert "step 2" in body and "step 3" in body             # the remainder


def test_sse_summarises_observations_instead_of_streaming_them_whole(client, fake_resolution):
    """A live run exposed this: one knowledge_search observation was ~5KB.

    Every retrieved passage, plus a `rendered` duplicate of the same text for
    the prompt. Streaming that pushes kilobytes per step to a browser drawing
    one line, and duplicates what GET /runs/{id} already serves in full.
    """
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.models.step import StepKind
    from app.repositories.run_repository import UNSCOPED, RunRepository

    big = "PASSAGE " * 2000
    run_id = client.post("/api/v1/runs", json={"goal": "big"}).json()["run_id"]

    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        run = repo.get(run_id)
        repo.add_step(
            run, StepKind.OBSERVATION.value, tool_name="knowledge_search",
            output={"ok": True, "unavailable": False, "data": [{"text": big}],
                    "meta": {"count": 5, "rendered": big, "latency_ms": 42}},
        )
        repo.finish(run, RunStatus.COMPLETED, answer="ok")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as r:
        body = "".join(r.iter_text())

    assert len(body) < 4000, "the stream is carrying the whole observation"
    assert '"truncated": true' in body
    # The useful summary survives: outcome, count, latency, a preview.
    assert '"count": 5' in body and '"ok": true' in body and '"latency_ms": 42' in body

    # ...and the FULL payload is still available from the run endpoint.
    full = client.get(f"/api/v1/runs/{run_id}").json()
    assert len(str(full["steps"])) > 10_000


def test_sse_tool_call_events_carry_their_arguments(client, fake_resolution):
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.models.step import StepKind
    from app.repositories.run_repository import UNSCOPED, RunRepository

    run_id = client.post("/api/v1/runs", json={"goal": "args"}).json()["run_id"]
    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        run = repo.get(run_id)
        repo.add_step(run, StepKind.TOOL_CALL.value, tool_name="calculator",
                      input={"expression": "6.5 - 6.2"})
        repo.finish(run, RunStatus.COMPLETED, answer="0.3")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as r:
        body = "".join(r.iter_text())
    assert "6.5 - 6.2" in body


def test_sse_on_an_unknown_run_is_a_404_not_a_200_with_an_error_event(client):
    """Changed by the authorisation fix, and improved by it.

    Previously an unknown run opened a stream and reported the problem in-band,
    which meant HTTP 200 for a request that could not succeed. Now the check
    happens before the StreamingResponse starts, so the refusal is a real 404 —
    and a missing run is indistinguishable from someone else's, which is what
    stops id enumeration.
    """
    assert client.get("/api/v1/runs/99999999/events").status_code == 404


def test_sse_sets_headers_that_stop_proxies_buffering(client, fake_resolution):
    from app.core.database import SessionLocal
    from app.models.run import RunStatus
    from app.repositories.run_repository import UNSCOPED, RunRepository

    run_id = client.post("/api/v1/runs", json={"goal": "headers"}).json()["run_id"]
    db = SessionLocal()
    try:
        repo = RunRepository(db, identity=UNSCOPED)
        repo.finish(repo.get(run_id), RunStatus.COMPLETED, answer="x")
    finally:
        db.close()

    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as r:
        # Without these a proxy holds every event until the stream closes —
        # the exact opposite of what SSE is for.
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["x-accel-buffering"] == "no"
        "".join(r.iter_text())
